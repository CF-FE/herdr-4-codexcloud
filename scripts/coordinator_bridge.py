#!/usr/bin/env python3
"""云端Dot项目协调员的本机桥端口。仅交付已核原件和已持久结果。"""

import argparse
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import struct
import sys
import time

# The fixed release runs with -I. Import only its installed sibling modules.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from coordinator_transport import CoordinatorTransport, Grant, Peer, TransportError, VerifiedJob, VerifiedTask, require
    from project_coordinator import Coordinator, CoordinatorError, canonical, identifier
else:
    from .coordinator_transport import CoordinatorTransport, Grant, Peer, TransportError, VerifiedJob, VerifiedTask, require
    from .project_coordinator import Coordinator, CoordinatorError, canonical, identifier


FRAME_LIMIT = 2 * 1024 * 1024
TIMEOUT = 5
PROBE = {'service': 'original-bridge', 'protocol': 1,
         'operations': ['verified_job', 'lookup_report', 'enqueue_result']}
HEX64 = re.compile('[a-f0-9]{64}')
CONFIG_NAME = 'bridge-port.json'


def json_object(raw):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            require(key not in obj, 'DUPLICATE_JSON_KEY')
            obj[key] = value
        return obj
    def reject(_):
        raise TransportError('INVALID_JSON')
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=reject)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise TransportError('INVALID_JSON') from error
    require(isinstance(obj, dict), 'INVALID_JSON')
    return obj


def private_path(raw, mode_check):
    path = Path(raw)
    require(path.is_absolute() and path.resolve() == path, 'PRIVATE_PATH_REQUIRED')
    parent = path.parent.stat()
    require(parent.st_uid == os.getuid() and not parent.st_mode & 0o077, 'PRIVATE_DIRECTORY_REQUIRED')
    info = path.lstat()
    require(mode_check(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            'PRIVATE_PATH_REQUIRED')
    return path


def load_config(raw):
    path = private_path(raw, stat.S_ISREG)
    require(path.name == CONFIG_NAME, 'INVALID_CONFIG_LOCATION')
    with path.open('rb') as source:
        data = source.read(65537)
    require(len(data) <= 65536, 'CONFIG_TOO_LARGE')
    config = json_object(data)
    required = {'v', 'socket_path', 'key_id', 'key_hex', 'recipient', 'grants', 'pilot'}
    require(required <= set(config) <= required | {'goal_channel_config'}
            and type(config['v']) is int and config['v'] == 1, 'INVALID_CONFIG')
    identifier(config['key_id'])
    require(isinstance(config['key_hex'], str) and HEX64.fullmatch(config['key_hex'])
            and isinstance(config['socket_path'], str) and Path(config['socket_path']).is_absolute()
            and isinstance(config['recipient'], str) and config['recipient'].strip()
            and isinstance(config['grants'], list) and len(config['grants']) <= 64, 'INVALID_CONFIG')
    grants = []
    for item in config['grants']:
        names = {'channel', 'subject', 'project_id', 'principal', 'sender_alias'}
        require(isinstance(item, dict) and names <= set(item) <= names | {'actions'}
                and all(isinstance(item[k], str) and item[k].strip() for k in names), 'INVALID_GRANT')
        # Legacy enrollment remains query-only. Installing code never upgrades a grant.
        actions = item.get('actions', ['submit'])
        require(isinstance(actions, list) and actions and len(actions) == len(set(actions))
                and all(a in ('submit', 'submit_goal') for a in actions), 'INVALID_GRANT')
        grants.append(Grant(Peer(item['channel'], item['subject']), identifier(item['project_id']),
                            item['principal'], item['sender_alias'], frozenset(actions)))
    pilot = config['pilot']
    if pilot is not None:
        require(isinstance(pilot, dict) and set(pilot) == {'project_id', 'job_id', 'request_id', 'payload_hash'}
                and isinstance(pilot['payload_hash'], str) and HEX64.fullmatch(pilot['payload_hash']), 'INVALID_PILOT')
        for key in ('project_id', 'job_id', 'request_id'):
            identifier(pilot[key])
        require(any(g.project_id == pilot['project_id'] for g in grants), 'PILOT_PROJECT_NOT_GRANTED')
    if config.get('goal_channel_config') is not None:
        require(pilot is not None, 'GOAL_CHANNEL_REQUIRES_EXACT_PILOT')
        require(isinstance(config['goal_channel_config'], str)
                and Path(config['goal_channel_config']).is_absolute(), 'PRIVATE_PATH_REQUIRED')
        # Existence, symlinks and permissions belong to load_goal_channel. Saved
        # results must drain even when that separate native binding is unavailable.
    return path.parent, config, grants


def peer_uid(sock):
    if sys.platform == 'darwin':
        uid, gid = ctypes.c_uint32(), ctypes.c_uint32()
        if ctypes.CDLL(None, use_errno=True).getpeereid(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)):
            raise ConnectionError('PEER_CREDENTIALS_UNAVAILABLE')
        return uid.value
    if hasattr(socket, 'SO_PEERCRED'):
        data = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
        return struct.unpack('3i', data)[1]
    raise TransportError('UNSUPPORTED_PEER_CREDENTIALS')


class SocketBridge:
    """One authenticated bounded exchange with the original collector per connection."""

    readback_job = None
    _readback_snapshot = None

    def __init__(self, config):
        self.socket_path = config['socket_path']
        self.key_id = config['key_id']
        self.key = bytes.fromhex(config['key_hex'])

    def call(self, op, **args):
        deadline = time.monotonic() + TIMEOUT
        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError('BRIDGE_TIMEOUT')
            return seconds
        def receive(sock):
            data = bytearray()
            while len(data) < FRAME_LIMIT:
                sock.settimeout(remaining())
                part = sock.recv(min(65536, FRAME_LIMIT - len(data)))
                if not part:
                    raise ConnectionError('INCOMPLETE_FRAME')
                data.extend(part)
                if b'\n' in part:
                    require(data.endswith(b'\n') and data.count(b'\n') == 1, 'INVALID_FRAME')
                    return json_object(data)
            raise TransportError('FRAME_TOO_LARGE')

        try:
            path = private_path(self.socket_path, stat.S_ISSOCK)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(remaining())
                sock.connect(str(path))
                require(peer_uid(sock) == os.getuid(), 'PEER_UID_MISMATCH')
                challenge = receive(sock)
                require(set(challenge) == {'v', 'challenge'} and type(challenge['v']) is int and challenge['v'] == 1
                        and isinstance(challenge['challenge'], str) and HEX64.fullmatch(challenge['challenge']),
                        'INVALID_CHALLENGE')
                request = {'v': 1, 'op': op, **args}
                body = canonical(request, max_bytes=FRAME_LIMIT)
                message = b'MBRIDGE/1\x00' + challenge['challenge'].encode('ascii') + b'\x00' + body
                client_mac = hmac.new(self.key, message, hashlib.sha256).hexdigest()
                request['auth'] = {'key_id': self.key_id, 'mac': client_mac}
                sock.settimeout(remaining())
                sock.sendall(canonical(request, max_bytes=FRAME_LIMIT - 1) + b'\n')
                try:
                    response = receive(sock)
                    signature = response.pop('server_mac', None)
                    if signature is None and op != 'enqueue_result' and response == {'v': 1, 'ok': False, 'code': 'ACCESS_DENIED'}:
                        raise TransportError('ACCESS_DENIED')
                    authenticated = (b'MBRIDGE/1/REPLY\0' + challenge['challenge'].encode('ascii') + b'\0'
                                     + client_mac.encode('ascii') + b'\0' + canonical(response, max_bytes=FRAME_LIMIT))
                    expected = hmac.new(self.key, authenticated, hashlib.sha256).hexdigest()
                    require(isinstance(signature, str) and HEX64.fullmatch(signature)
                            and hmac.compare_digest(expected, signature), 'UNVERIFIED_BRIDGE_RESPONSE')
                except (TransportError, CoordinatorError) as error:
                    if op == 'enqueue_result':
                        raise ConnectionError('UNCONFIRMED_ENQUEUE') from error
                    raise
        except PermissionError as error:
            raise TransportError('ACCESS_DENIED') from error
        except OSError as error:
            raise ConnectionError('BRIDGE_UNAVAILABLE') from error
        require(type(response.get('v')) is int and response['v'] == 1 and type(response.get('ok')) is bool,
                'INVALID_BRIDGE_RESPONSE')
        if response['ok']:
            require(set(response) == {'v', 'ok', 'value'}, 'INVALID_BRIDGE_RESPONSE')
            return response['value']
        code = response.get('code')
        require(set(response) == {'v', 'ok', 'code'} and isinstance(code, str)
                and re.fullmatch('[A-Z][A-Z0-9_]{0,63}', code), 'INVALID_BRIDGE_RESPONSE')
        if code == 'UNAVAILABLE':
            raise ConnectionError('BRIDGE_UNAVAILABLE')
        raise TransportError(code)

    def verified_job(self, job_id):
        # This socket adapter lives for exactly one CLI invocation. Only the
        # already-issued action's readback and outbox phases share a source
        # snapshot. Admission and the last gate before a new send stay live.
        if job_id != self.readback_job:
            return self._verified_job(job_id)
        if self._readback_snapshot is None:
            try:
                self._readback_snapshot = self._verified_job(job_id)
            except (ConnectionError, TimeoutError, TransportError, CoordinatorError) as error:
                self._readback_snapshot = error
        if isinstance(self._readback_snapshot, Exception):
            raise self._readback_snapshot
        return self._readback_snapshot

    def _verified_job(self, job_id):
        value = self.call('verified_job', job_id=job_id)
        require(isinstance(value, dict) and set(value) == {'origin', 'origin_ref', 'job_id', 'payload_hash', 'record', 'task'}
                and isinstance(value['origin'], dict) and set(value['origin']) == {'channel', 'subject'}
                and isinstance(value['task'], dict)
                and set(value['task']) == {'repository', 'project_ref', 'item_id', 'task_ref', 'evidence_ref'},
                'ORIGIN_UNVERIFIED')
        return VerifiedJob(Peer(**value['origin']), value['origin_ref'], value['job_id'], value['payload_hash'],
                           value['record'], VerifiedTask(**value['task']))

    def lookup_report(self, job_id, event_id):
        return self.call('lookup_report', job_id=job_id, event_id=event_id)

    def enqueue_result(self, **result):
        return self.call('enqueue_result', **result)


def goal_wait(core, project, request_id, reason, evidence_ref=None):
    """Durably expose a local admission/recovery blocker on the original job."""
    status = core.inspect(project, request_id)
    wait = dict(status['waiting'])
    wait.pop('follow_up_owner', None)
    wait.pop('contact_reason', None)
    condition = ('Wait for the current owner turn to finish, then check the original request again'
                 if reason == 'NATIVE_OWNER_BUSY' and not status['action_id']
                 else 'Resolve the original native goal channel and reconcile any existing action')
    wait.update(project_id=project, request_id=request_id, action_owner=core.coordinator_id,
                reason=reason, next_condition=condition,
                evidence_ref=evidence_ref or 'local:goal-channel:' + reason)
    core.manage('set_wait', wait)
    core.manage('report_wait', {'project_id': project, 'request_id': request_id,
                               'evidence_ref': wait['evidence_ref']})


def dispatch(args):
    app, config, grants = load_config(args.config)
    bridge = SocketBridge(config)
    if args.operation == 'probe':
        reply = bridge.call('probe')
        require(isinstance(reply, dict) and type(reply.get('protocol')) is int and reply == PROBE,
                'INVALID_PROBE_RESPONSE')
        return {'state': 'PORT_VERIFIED', 'scope': 'LOCAL_BRIDGE_PORT', **reply}
    pilot = config['pilot']
    if args.operation == 'pump' and pilot is None:
        return {'state': 'NO_PILOT'}
    state_dir = app / 'state'
    require((state_dir / 'project-coordinator.sqlite3').is_file(), 'STATE_NOT_INITIALIZED')
    core = Coordinator(state_dir, read_only=args.operation == 'enqueue')
    if args.operation == 'enqueue' and core.inspect(identifier(args.project_id), identifier(args.request_id)).get('kind') == 'DELEGATE_GOAL':
        # Goal publication also records authenticated source-control changes.
        core = Coordinator(state_dir)
    transport = CoordinatorTransport(core, grants=grants, recipient=config['recipient'], bridge=bridge, callers=None)
    if args.operation == 'deliver':
        status = transport.deliver(identifier(args.job_id))
        return {k: status[k] for k in ('project_id', 'request_id', 'job_id', 'payload_hash', 'state', 'target_generation')}
    if args.operation == 'enqueue':
        return transport.enqueue(identifier(args.project_id), identifier(args.request_id), args.version)

    # A cancelled original can still have an already-durable result to return.
    project, request_id = pilot['project_id'], pilot['request_id']
    try:
        status = core.inspect(project, request_id)
    except CoordinatorError as error:
        if error.code != 'REQUEST_NOT_FOUND':
            raise
        status = transport.deliver(pilot['job_id'], expected_project_id=project,
                                   expected_payload_hash=pilot['payload_hash'], expected_request_id=request_id)
    require(status['job_id'] == pilot['job_id'] and status['payload_hash'] == pilot['payload_hash'],
            'PILOT_BINDING_MISMATCH')
    version = core.pilot_outbox_version(project, request_id)
    goal = status.get('kind') == 'DELEGATE_GOAL'
    pending_at_start = bool(status['result_version'] and version <= status['result_version'])
    if not goal and pending_at_start:
        receipt = transport.enqueue(project, request_id, version)
        if receipt['state'] in ('PENDING', 'SENT'):
            core.pilot_outbox_version(project, request_id, version=version, receipt=receipt)
        return {'state': receipt['state'], 'core_version': version, 'event_id': receipt['event_id']}
    channel_state, channel_error, control = None, None, None
    if goal and not status['unblocked']:
        if not config.get('goal_channel_config'):
            # Receipt of a goal is not native acceptance. Keep the original
            # request available without consuming the one dispatch attempt.
            channel_state = 'WAITING_NATIVE_GOAL_CHANNEL'
            if not pending_at_start:
                goal_wait(core, project, request_id, channel_state)
        else:
            if __package__:
                from .coordinator_goal_channel import load_goal_channel
            else:
                from coordinator_goal_channel import load_goal_channel
            try:
                channel = load_goal_channel(config['goal_channel_config'])
                channel.bind_organization_parent(core.path.parent)
                context = core.goal_context(project, request_id)
                owner_grant = Grant(channel.peer, project, context['owner_principal'], 'native-goal-owner',
                                    frozenset({'goal_update'}))
                transport = CoordinatorTransport(core, grants=[*grants, owner_grant], recipient=config['recipient'],
                                                 bridge=bridge, callers=channel, owners=channel)
                if not status['action_id'] and not status['cancel_requested']:
                    transport.dispatch_goal(project, request_id, status['target_generation'])
                context = core.goal_context(project, request_id)
                if context['action_id']:
                    # This reads the original native action. It never resumes a
                    # second turn, even after UNKNOWN or a missing result receipt.
                    bridge.readback_job = context['job_id']
                    try:
                        observations = channel.reconcile(context)
                    except TransportError as error:
                        # A later rejected report must not erase an earlier
                        # verified acceptance in the same native readback. Each
                        # retained handle still passes the ordinary owner/source
                        # and report gates; the rejected item is never adopted.
                        if error.code == 'NATIVE_LISTENING_CAPABILITY_UNAVAILABLE':
                            control, _ = transport.goal_updates(project, request_id, getattr(error, 'validated_handles', ()))
                        raise
                    control, _ = transport.goal_updates(project, request_id, observations)
                channel_state = core.inspect(project, request_id)['goal_state']
            except (TransportError, CoordinatorError, OSError, ValueError) as error:
                code = getattr(error, 'code', 'NATIVE_CHANNEL_UNAVAILABLE')
                code = code if isinstance(code, str) and re.fullmatch('[A-Z][A-Z0-9_]{0,63}', code) else 'NATIVE_CHANNEL_UNAVAILABLE'
                channel_state = 'GOAL_CHANNEL_BLOCKED'
                channel_error = code
                control = getattr(error, 'source_control', control)
                evidence = getattr(error, 'evidence_ref', None)
                evidence = evidence if (code == 'NATIVE_LISTENING_CAPABILITY_UNAVAILABLE'
                    and isinstance(evidence, str) and re.fullmatch('native-goal-report-sha256:[a-f0-9]{64}', evidence)) else None
                if not pending_at_start or evidence:
                    goal_wait(core, project, request_id, code, evidence)
        status = core.inspect(project, request_id)
    version = core.pilot_outbox_version(project, request_id)
    local = ({'local_goal_state': status['goal_state'], 'local_result_version': status['result_version'],
              'native_state': channel_state, 'native_error': channel_error, 'source_control': control} if goal else {})
    if not status['result_version'] or version > status['result_version']:
        return {'state': channel_state or 'FOLLOWING', 'scope': 'LOCAL_LEDGER',
                'request_state': status['state'], 'next_version': version, **local}
    try:
        receipt = transport.enqueue(project, request_id, version)
    except (TimeoutError, ConnectionError):
        if not goal:
            raise
        return {**local, 'state': 'UNKNOWN', 'outbox_error': 'BRIDGE_UNAVAILABLE', 'core_version': version}
    except (TransportError, CoordinatorError) as error:
        if not goal:
            raise
        return {**local, 'state': 'OUTBOX_BLOCKED', 'outbox_error': error.code, 'core_version': version}
    if receipt['state'] in ('PENDING', 'SENT'):
        core.pilot_outbox_version(project, request_id, version=version, receipt=receipt)
    return {'state': receipt['state'], 'core_version': version, 'event_id': receipt['event_id'], **local}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path, help='Private <installation>/bridge-port.json')
    ops = parser.add_subparsers(dest='operation', required=True)
    ops.add_parser('probe')
    ops.add_parser('pump')
    deliver = ops.add_parser('deliver')
    deliver.add_argument('--job-id', required=True)
    enqueue = ops.add_parser('enqueue')
    enqueue.add_argument('--project-id', required=True)
    enqueue.add_argument('--request-id', required=True)
    enqueue.add_argument('--version', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 2 if result.get('state') in ('UNKNOWN', 'OUTBOX_BLOCKED') else 0
    except (TransportError, CoordinatorError) as error:
        detail = {'code': error.code}
        if getattr(error, 'conflicts', None):
            detail['conflicts'] = error.conflicts
    except ConnectionError:
        detail = {'code': 'BRIDGE_UNAVAILABLE'}
    except (OSError, ValueError, sqlite3.Error):
        detail = {'code': 'LOCAL_BRIDGE_IO_ERROR'}
    print(json.dumps({'error': detail}), file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
