#!/usr/bin/env python3
"""Run one approved transport nonce in the Mac coordinator, via the existing signed local bridge."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

if not __package__:
    # The installed entry point runs with -I and imports only its fixed release siblings.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from coordinator_bridge import HEX64, SocketBridge, TransportError, json_object, private_path, require
    from project_coordinator import CoordinatorError, canonical, canonical_hash, identifier, timestamp
else:
    from .coordinator_bridge import HEX64, SocketBridge, TransportError, json_object, private_path, require
    from .project_coordinator import CoordinatorError, canonical, canonical_hash, identifier, timestamp


PROJECT = 'task-mailbox-mcp'
TARGET = {'service': 'transport-probe', 'operation': 'sha256'}
POLL_SECONDS = 10


def read_private(path):
    private_path(path, stat.S_ISREG)
    require(stat.S_IMODE(path.stat().st_mode) == 0o600, 'PRIVATE_FILE_REQUIRED')
    with path.open('rb') as stream:
        data = stream.read(65537)
    require(len(data) <= 65536, 'FILE_TOO_LARGE')
    return json_object(data)


def load_policy(path):
    policy = read_private(path)
    keys = {'job_id', 'payload_hash', 'report_event_id', 'expires_at', 'expected_version', 'expected_result_hash'}
    require(keys <= policy.keys() <= keys | {'scope', 'status', 'ack_event_id'}, 'INVALID_POLICY')
    identifier(policy['job_id'])
    for key in ('payload_hash', 'expected_result_hash'):
        require(isinstance(policy[key], str) and HEX64.fullmatch(policy[key]), 'INVALID_POLICY_DIGEST')
    require(policy['report_event_id'] == 'transport-probe-' + canonical_hash([policy['job_id'], policy['payload_hash']])
            and type(policy['expected_version']) is int and policy['expected_version'] == 1, 'INVALID_EVENT_BINDING')
    timestamp(policy['expires_at'])
    return policy


def retain(path, value):
    """Publish a complete 0600 receipt; a different existing receipt is never overwritten."""
    if path.exists() or path.is_symlink():
        require(read_private(path) == value, 'RECEIPT_CONFLICT')
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.receipt-') as staged:
        staged.write(canonical(value) + b'\n')
        staged.flush()
        os.fsync(staged.fileno())
        try:
            os.link(staged.name, path)
        except FileExistsError:
            require(read_private(path) == value, 'RECEIPT_CONFLICT')
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def handle(case_dir, policy, bridge, clock):
    """One round under the case's exclusive process lock. No direct network, command or model calls."""
    deadline = timestamp(policy['expires_at'])
    require(clock() < deadline, 'CASE_EXPIRED')
    job, event, payload_hash = policy['job_id'], policy['report_event_id'], policy['payload_hash']
    expected = {'job_id': job, 'event_id': event, 'status': 'REPORTED_COMPLETED',
                'result_hash': policy['expected_result_hash']}
    path = case_dir / 'processed.json'
    if path.exists() or path.is_symlink():
        saved = read_private(path)
        require(saved.get('policy_sha256') == canonical_hash(policy) and saved.get('report_event_id') == event
                and canonical_hash(saved.get('result')) == expected['result_hash'], 'RECEIPT_CONFLICT')
        # A previous enqueue may have persisted even if its reply was lost.
        record = bridge.lookup_report(job, event)
    else:
        saved, record = None, None

    if record is None:
        original = bridge.verified_job(job)
        row = original.record
        require(isinstance(row, dict) and original.job_id == row.get('job_id') == job
                and original.payload_hash == row.get('payload_hash') == payload_hash, 'WRONG_ORIGINAL')
        payload = row.get('payload')
        require(isinstance(payload, dict) and canonical_hash(payload) == payload_hash, 'ORIGINAL_DIGEST_MISMATCH')
        require(row.get('cancel_requested') is False, 'CANCELLED_OR_UNVERIFIED')
        require(type(payload.get('schema_version')) is int and payload['schema_version'] == 1
                and payload.get('sender') == 'cloud-dot' and payload.get('recipient') == 'mac-coordinator'
                and payload.get('target') == TARGET and payload.get('report_route') == 'task-mailbox:original-job',
                'WRONG_ROUTE')
        tracking, nonce = payload.get('tracking'), payload.get('content')
        require(isinstance(tracking, dict) and tracking.get('repository') == 'CF-FE/' + PROJECT
                and isinstance(nonce, str) and 1 <= len(nonce.encode()) <= 256, 'INVALID_PAYLOAD')
        require(isinstance(original.origin_ref, str) and original.origin_ref.strip(), 'ORIGIN_UNVERIFIED')
        result = {'schema_version': 1, 'type': 'TRANSPORT_PROBE_RESULT', 'job_id': job, 'payload_hash': payload_hash,
                  'operation': 'sha256', 'nonce_sha256': hashlib.sha256(nonce.encode('utf-8')).hexdigest(),
                  'execution_enabled': False, 'business_accepted': False, 'task_tracking': tracking}
        require(canonical_hash(result) == expected['result_hash'], 'COMPUTED_RESULT_MISMATCH')
        if saved is None:
            require(type(row.get('latest_result_version')) is int and row['latest_result_version'] == 0
                    and row.get('status') == 'PERSISTED', 'EXISTING_SERVER_RESULT')
        proof = {'component': 'mac-project-coordinator', 'operation': 'transport-probe', 'policy_sha256': canonical_hash(policy),
                 'source_proof_sha256': canonical_hash([original.origin.channel, original.origin.subject,
                                                        original.origin_ref, vars(original.task)]),
                 'original_job_created_at': row.get('created_at'), 'report_event_id': event, 'result': result}
        require(isinstance(proof['original_job_created_at'], str) and proof['original_job_created_at'], 'ORIGINAL_TIME_MISSING')
        processed = clock()
        require(processed < deadline, 'CASE_EXPIRED')
        proof['processed_at'] = saved['processed_at'] if saved else processed.isoformat()
        retain(path, proof)
        # Even on first use, check the original outbox before admitting a write.
        if saved is None:
            record = bridge.lookup_report(job, event)
        if record is None:
            require(type(row.get('latest_result_version')) is int and row['latest_result_version'] == 0
                    and row.get('status') == 'PERSISTED', 'EXISTING_SERVER_RESULT')
            require(clock() < deadline, 'CASE_EXPIRED')
            record = bridge.enqueue_result(project_id=PROJECT, job_id=job, payload_hash=payload_hash,
                                           event_id=event, status=expected['status'], result=result)

    require(isinstance(record, dict) and all(record.get(k) == v for k, v in expected.items()), 'OUTBOX_MISMATCH')
    if record.get('state') == 'PENDING':
        require(record.get('receipt') is None, 'OUTBOX_MISMATCH')
        return 'PENDING'
    receipt = record.get('receipt')
    require(record.get('state') == 'SENT' and isinstance(receipt, dict)
            and all(receipt.get(k) == v for k, v in expected.items())
            and type(receipt.get('version')) is int and receipt['version'] == policy['expected_version'],
            'REMOTE_RECEIPT_MISMATCH')
    retain(case_dir / 'sent.json', {**expected, 'version': receipt['version']})
    return 'SENT'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-dir', required=True, type=Path, help='Private directory with policy.json and socket-client.json')
    args = parser.parse_args()
    case_dir = args.case_dir
    os.umask(0o077)
    require(stat.S_IMODE(case_dir.stat().st_mode) == 0o700, 'PRIVATE_DIRECTORY_REQUIRED')
    policy = load_policy(case_dir / 'policy.json')
    config_path = case_dir / 'socket-client.json'
    # Lock the directory rather than creating another shared service or lock database.
    fd = os.open(case_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TransportError('ALREADY_RUNNING') from error
        config = read_private(config_path)
        require(set(config) == {'v', 'socket_path', 'key_id', 'key_hex'} and type(config['v']) is int and config['v'] == 1
                and isinstance(config['key_hex'], str) and HEX64.fullmatch(config['key_hex'])
                and not identifier(config['key_id']).startswith('probe-')
                and isinstance(config['socket_path'], str) and Path(config['socket_path']).is_absolute(), 'INVALID_CLIENT')
        bridge = SocketBridge(config)
        def clock():
            return datetime.now(timezone.utc)
        previous = None
        while True:
            # A custodian may revoke/replace the grant while the process waits. Never silently follow a new case.
            require(load_policy(case_dir / 'policy.json') == policy, 'POLICY_CHANGED')
            try:
                state = handle(case_dir, policy, bridge, clock)
            except (ConnectionError, TimeoutError):
                state = 'AWAITING_ORIGINAL_OR_RECEIPT'
            if state != previous:
                print(json.dumps({'state': state, 'job_id': policy['job_id']}), flush=True)
                previous = state
            if state == 'SENT':
                return
            left = (timestamp(policy['expires_at']) - clock()).total_seconds()
            require(left > 0, 'CASE_EXPIRED')
            time.sleep(min(POLL_SECONDS, left))
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except (TransportError, CoordinatorError) as error:
        print(json.dumps({'error': error.code}), file=sys.stderr)
        sys.exit(2)
    except (ValueError, OSError):
        print('{"error":"LOCAL_IO_OR_INVALID_DATA"}', file=sys.stderr)
        sys.exit(2)
