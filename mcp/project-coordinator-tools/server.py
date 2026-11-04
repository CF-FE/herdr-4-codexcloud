#!/usr/bin/env python3
"""Single-goal organizational tools over MCP stdio. No daemon, shell, or new owner.

Run with python -I -S -B server.py --policy /private/reviewed-policy.json.
Imported documents are HOST_ATTESTED. Only the existing native channel can prove
that the separately authorized, independent reviewer actually accepted a task.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from project_coordinator import Coordinator, CoordinatorError, canonical, canonical_hash, GOAL_ACTIONS, GOAL_BUDGET, timestamp
from coordinator_goal_channel import load_goal_channel
from coordinator_bridge import SocketBridge, load_config
from coordinator_transport import CoordinatorTransport, CONTACT_KEYS

MAX_FILE = 256 * 1024
MAX_FRAME = 1024 * 1024
BINDING_KEYS = {'project_id', 'request_id', 'job_id', 'payload_hash', 'owner_principal', 'endpoint',
                'generation', 'authorization_ref', 'work_key', 'scope_refs', 'allowed_actions', 'deadline'}
POLICY_KEYS = {'v', 'role', 'core_state_dir', 'state_dir', 'binding', 'expires_at', 'source_bridge',
               'evidence', 'reviewer', 'review_contact_attempts'}
CHILD_ACTIONS = ['verify_existing_evidence', 'report_progress']


class ToolError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(ok, code):
    if not ok:
        raise ToolError(code)


def parse(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, 'DUPLICATE_JSON_KEY')
            out[key] = value
        return out
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, ToolError):
            raise
        raise ToolError('INVALID_JSON') from None


def plain(value):
    return isinstance(value, str) and value and value.strip() == value and value.isprintable()


def private_dir(value):
    path = Path(value)
    require(path.is_absolute() and path.resolve() == path, 'UNSAFE_PATH')
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            'PRIVATE_DIRECTORY_REQUIRED')
    return path


def read_file(value, *, private=False, maximum=MAX_FILE):
    path = Path(value)
    require(path.is_absolute() and path.resolve() == path, 'UNSAFE_PATH')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= maximum, 'UNSAFE_FILE')
        if private:
            require(info.st_uid == os.getuid() and not info.st_mode & 0o077, 'PRIVATE_FILE_REQUIRED')
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, 'FILE_TOO_LARGE')
    return raw


def pinned(ref, *, private=False):
    require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}, 'INVALID_FILE_REFERENCE')
    raw = read_file(ref['path'], private=private)
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'PIN_CHANGED')
    return raw


class GoalTools:
    def __init__(self, policy_path, *, clock=None, channel_loader=load_goal_channel, source_gate=None):
        self.policy_path = Path(policy_path)
        raw = read_file(self.policy_path, private=True)
        self.policy_hash = hashlib.sha256(raw).hexdigest()
        self.policy = p = parse(raw)
        require(isinstance(p, dict) and set(p) == POLICY_KEYS and type(p['v']) is int and p['v'] == 1,
                'INVALID_TOOL_POLICY')
        require(p['role'] in ('project_owner', 'reviewer') and type(p['review_contact_attempts']) is int
                and p['review_contact_attempts'] in (0, 1), 'INVALID_TOOL_POLICY')
        b = p['binding']
        require(isinstance(b, dict) and set(b) == BINDING_KEYS and type(b['generation']) is int
                and b['generation'] > 0, 'INVALID_BINDING')
        require(all(plain(b[k]) for k in BINDING_KEYS - {'generation', 'scope_refs', 'allowed_actions'}), 'INVALID_BINDING')
        for key in ('scope_refs', 'allowed_actions'):
            require(isinstance(b[key], list) and 1 <= len(b[key]) <= 64 and all(plain(v) for v in b[key])
                    and len(set(b[key])) == len(b[key]), 'INVALID_BINDING')
        require(set(b['allowed_actions']) <= GOAL_ACTIONS, 'INVALID_BINDING')
        timestamp(p['expires_at']); timestamp(b['deadline'])
        require(timestamp(p['expires_at']) <= timestamp(b['deadline']), 'AUTHORITY_EXPANDED')
        self.core_dir = private_dir(p['core_state_dir'])
        self.state = private_dir(p['state_dir'])
        self.journal = self.state / 'review-contact.sqlite3'
        require(not self.journal.is_symlink(), 'UNSAFE_PATH')
        require(isinstance(p['evidence'], list) and len(p['evidence']) <= 64, 'INVALID_EVIDENCE')
        self.evidence = {}
        for item in p['evidence']:
            require(isinstance(item, dict), 'INVALID_EVIDENCE')
            keys = {'ref_id', 'path', 'sha256', 'bytes', 'format', 'kind', 'scope_refs'}
            if item.get('kind') == 'prior_round':
                keys.add('prior_round_ref')
                require(plain(item.get('prior_round_ref')) and item['prior_round_ref'] not in
                        (b['request_id'], b['job_id'], b['work_key']), 'INVALID_PRIOR_ROUND')
            require(set(item) == keys
                    and plain(item['ref_id']) and item['ref_id'] not in self.evidence
                    and type(item['bytes']) is int and 0 <= item['bytes'] <= MAX_FILE
                    and item['format'] in ('json', 'text') and item['kind'] in ('appointment', 'independence', 'arrangement', 'receipt', 'asset_index',
                        'prior_round'),
                    'INVALID_EVIDENCE')
            require(isinstance(item['scope_refs'], list) and set(item['scope_refs']) <= set(b['scope_refs']), 'EVIDENCE_SCOPE_MISMATCH')
            require(not set(Path(item['path']).parts) & {'.codex', '.ssh', '.aws', '.gnupg'}, 'RESTRICTED_EVIDENCE_PATH')
            self.evidence[item['ref_id']] = item
        reviewer = p['reviewer']
        if reviewer is not None:
            require(isinstance(reviewer, dict) and set(reviewer) == {'principal', 'endpoint', 'producer_principal',
                    'appointment_ref', 'independence_ref', 'channel_policy', 'authorization_ref', 'scope_refs'}, 'INVALID_REVIEWER')
            require(all(plain(reviewer[k]) for k in reviewer.keys() - {'channel_policy', 'scope_refs'})
                    and reviewer['principal'] != reviewer['producer_principal']
                    and reviewer['endpoint'] != b['endpoint']
                    and set(reviewer['scope_refs']) == set(b['scope_refs'])
                    and reviewer['appointment_ref'] in self.evidence and reviewer['independence_ref'] in self.evidence,
                    'INVALID_REVIEWER')
        if p['role'] == 'reviewer':
            require(set(b['allowed_actions']) <= set(CHILD_ACTIONS) and reviewer is None
                    and p['review_contact_attempts'] == 0, 'RECURSIVE_DELEGATION_FORBIDDEN')
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.channel_loader, self.source_gate = channel_loader, source_gate

    def _policy_current(self):
        require(hashlib.sha256(read_file(self.policy_path, private=True)).hexdigest() == self.policy_hash, 'POLICY_CHANGED')

    def _context(self, *, active=False, historical=False):
        self._policy_current()
        b = self.policy['binding']
        core = Coordinator(self.core_dir, read_only=True, clock=self.clock)
        context, route = core.goal_context(b['project_id'], b['request_id']), core.route(b['project_id'])
        for key in BINDING_KEYS - {'work_key', 'scope_refs', 'allowed_actions'}:
            require(context.get(key) == b[key], 'PARENT_BINDING_CHANGED')
        require(context['goal']['work_key'] == b['work_key'] and set(context['goal']['scope_refs']) == set(b['scope_refs'])
                and set(b['allowed_actions']) <= set(context['goal']['allowed_actions']), 'PARENT_SCOPE_CHANGED')
        current_owner = (route['owner_principal'], route['endpoint'], route['generation']) == (
            b['owner_principal'], b['endpoint'], b['generation'])
        require(historical or current_owner, 'CURRENT_OWNER_CHANGED')
        status = core.inspect(b['project_id'], b['request_id'])
        status = {**status, 'original_owner_current': current_owner,
                  'authority_expired': self.clock() >= timestamp(b['deadline']) or
                  self.clock() >= timestamp(self.policy['expires_at'])}
        if active:
            require(not status['cancel_requested'] and not status['stopped'], 'PARENT_CANCELLED_OR_STOPPED')
            require(self.clock() < timestamp(b['deadline']) and self.clock() < timestamp(self.policy['expires_at']), 'AUTHORITY_EXPIRED')
            require(status['goal_state'] != 'COMPLETED', 'PARENT_COMPLETED')
        return core, context, status

    def goal_context(self):
        _, context, status = self._context()
        return {'parent': context, 'status': status, 'role': self.policy['role'],
                'review_contact_attempts': self.policy['review_contact_attempts'],
                'evidence_refs': list(self.evidence), 'reviewer_bound': self.policy['reviewer'] is not None}

    def _evidence(self, ref_id):
        require(ref_id in self.evidence, 'EVIDENCE_NOT_ALLOWED')
        ref = self.evidence[ref_id]
        self._forbid_control_evidence(ref['path'])
        raw = read_file(ref['path'])
        require(len(raw) == ref['bytes'] and hashlib.sha256(raw).hexdigest() == ref['sha256'], 'EVIDENCE_CHANGED')
        value = parse(raw) if ref['format'] == 'json' else raw.decode('utf-8')
        return {'ref_id': ref_id, 'sha256': ref['sha256'], 'bytes': len(raw), 'kind': ref['kind'],
                **({'prior_round_ref': ref['prior_round_ref']} if ref['kind'] == 'prior_round' else {}),
                'trust': 'HOST_ATTESTED', 'native_acceptance_verified': False, 'content': value}

    def _forbid_control_evidence(self, evidence_path):
        """Known control/credential files never become business evidence exports."""
        controls = [self.policy_path, self.journal]
        def check():
            for control in controls:
                path = Path(control)
                require(Path(evidence_path) != path and not (
                    path.exists() and Path(evidence_path).exists() and os.path.samefile(path, evidence_path)),
                    'CONTROL_FILE_NOT_BUSINESS_EVIDENCE')
        if self.policy['source_bridge'] is not None:
            controls.append(self.policy['source_bridge']['path'])
        reviewer = self.policy['reviewer']
        if reviewer is None:
            check()
            return
        controls.append(reviewer['channel_policy']['path'])
        check()
        channel = parse(pinned(reviewer['channel_policy'], private=True))
        for field in ('process_config', 'capability_manifest'):
            if field in channel:
                controls.append(channel[field])
        check()
        if channel.get('v') == 2:
            process = parse(pinned({'path': channel['process_config'], 'sha256': channel['process_config_sha256']}, private=True))
            for server in process.get('mcp_servers', {}).values():
                controls.append(server['policy'])
                controls.extend(server.get('artifacts', []))
            check()
        if 'capability_manifest' in channel:
            cap = parse(pinned({'path': channel['capability_manifest'], 'sha256': channel['capability_manifest_sha256']}, private=True))
            controls.extend(x['path'] for x in cap.get('config_files', []))
            if 'owner_exclusivity' in cap:
                controls.append(cap['owner_exclusivity'])
        check()

    def read_review_evidence(self, ref_id):
        core, context, _ = self._context(active=True)
        require('verify_existing_evidence' in self.policy['binding']['allowed_actions'], 'ACTION_NOT_ALLOWED')
        self._source(core, context)
        value = self._evidence(ref_id)
        self._context(active=True)
        return value

    def _reviewer(self):
        b, r = self.policy['binding'], self.policy['reviewer']
        require(r is not None, 'NO_AUTHORIZED_REVIEWER')
        appointment = self._evidence(r['appointment_ref'])
        independence = self._evidence(r['independence_ref'])
        require(appointment['kind'] == 'appointment' and independence['kind'] == 'independence', 'REVIEWER_EVIDENCE_MISMATCH')
        a, i = appointment['content'], independence['content']
        required = {'project_id': b['project_id'], 'reviewer_principal': r['principal'], 'endpoint': r['endpoint'],
                    'generation': b['generation'], 'authorization_ref': r['authorization_ref']}
        require(isinstance(a, dict) and type(a.get('generation')) is int and all(a.get(k) == v for k, v in required.items()) and a.get('state') == 'ACTIVE'
                and a.get('appointed_by') == b['owner_principal'] and set(a.get('scope_refs', [])) == set(b['scope_refs'])
                and self.clock() < timestamp(a.get('expires_at')), 'REVIEWER_APPOINTMENT_INVALID')
        require(isinstance(i, dict) and i.get('project_id') == b['project_id'] and i.get('reviewer_principal') == r['principal']
                and i.get('producer_principal') == r['producer_principal'] and i.get('independent') is True
                and set(i.get('scope_refs', [])) == set(b['scope_refs']), 'REVIEWER_INDEPENDENCE_INVALID')
        return r

    def _reviewer_tool_boundary(self, channel_policy):
        """Inspect the actual pinned tool policy, not a claimed child action list."""
        require(type(channel_policy.get('v')) is int and channel_policy['v'] == 2, 'REVIEW_CHANNEL_V2_REQUIRED')
        process = parse(pinned({'path': channel_policy['process_config'],
                                'sha256': channel_policy['process_config_sha256']}, private=True))
        servers = process.get('mcp_servers')
        require(isinstance(servers, dict) and len(servers) == 1, 'REVIEW_TOOL_BOUNDARY_INVALID')
        server = next(iter(servers.values()))
        require(isinstance(server, dict) and isinstance(server.get('enabled_tools'), list)
                and server['enabled_tools'] and set(server['enabled_tools']) <= {
                    'goal_context', 'read_review_evidence', 'read_coordination_report'}, 'RECURSIVE_DELEGATION_FORBIDDEN')
        child = GoalTools(server['policy'], clock=self.clock)
        require(child.policy['role'] == 'reviewer' and child.policy['reviewer'] is None
                and child.policy['review_contact_attempts'] == 0 and child.core_dir == self.core_dir
                and child.state != self.state
                and ('read_review_evidence' not in server['enabled_tools'] or
                     child.policy['source_bridge'] == self.policy['source_bridge'])
                and set(child.policy['binding']['allowed_actions']) <= set(CHILD_ACTIONS)
                and all(child.policy['binding'][k] == self.policy['binding'][k]
                        for k in BINDING_KEYS - {'allowed_actions'}), 'REVIEW_TOOL_BOUNDARY_INVALID')
        return child.policy_hash

    def _source(self, core, context):
        require(self.policy['source_bridge'] is not None, 'SOURCE_BRIDGE_UNBOUND')
        if self.source_gate is not None:  # Offline dependency injection; never selected by MCP or policy.
            self.source_gate(core, context)
            return
        raw = pinned(self.policy['source_bridge'], private=True)
        _, config, grants = load_config(self.policy['source_bridge']['path'])
        require(canonical(parse(raw)) == canonical(config), 'SOURCE_CONFIG_CHANGED')
        transport = CoordinatorTransport(core, grants=grants, recipient=config['recipient'], bridge=SocketBridge(config), callers=None)
        original, payload = transport._original(context['job_id'])
        require(original.payload_hash == context['payload_hash'], 'SOURCE_JOB_CHANGED')
        transport._verify_route(original, payload, context['project_id'], admitted_request=context['request_id'])
        transport._grant(original.origin, context['project_id'], 'submit_goal')
        require(original.record.get('cancel_requested') is False, 'SOURCE_CANCELLED_OR_UNKNOWN')

    def _child(self, context, reviewer):
        b = self.policy['binding']
        key = canonical_hash({k: b[k] for k in ('project_id', 'request_id', 'job_id', 'payload_hash', 'work_key')})[:48]
        goal = dict(context['goal'])
        goal.update(objective='Verify existing independent-review evidence for the frozen assets and report gaps. '
                              'These tools cannot listen to audio: do not execute a new listening review or claim it completed. '
                              'Do not delegate, generate audio, change assets, or appoint anyone. Parent goal: ' + goal['objective'],
                    allowed_actions=CHILD_ACTIONS, related_request_ids=[b['request_id']],
                    evidence_refs=list(dict.fromkeys([*goal['evidence_refs'], reviewer['appointment_ref'], reviewer['independence_ref']])))
        return {**{k: context[k] for k in ('project_id', 'job_id', 'payload_hash', 'task_ref', 'deadline')},
                'request_id': 'review-' + key, 'action_id': 'review-contact-' + key, 'kind': 'DELEGATE_GOAL',
                'generation': b['generation'], 'owner_principal': reviewer['principal'], 'endpoint': reviewer['endpoint'],
                'authorization_ref': reviewer['authorization_ref'], 'budget': dict(GOAL_BUDGET), 'goal': goal}

    @contextmanager
    def _lock(self):
        fd = os.open(self.state / 'review-contact.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ToolError('REVIEW_COORDINATION_BUSY') from None
            yield

    @contextmanager
    def _db(self, write=False, *, path=None):
        path = self.journal if path is None else path
        read_file(path, private=True, maximum=8 * MAX_FRAME)
        db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if write:
                db.execute('CREATE TABLE IF NOT EXISTS contact(id INTEGER PRIMARY KEY CHECK(id=1), binding_hash TEXT NOT NULL, intent TEXT NOT NULL, admission_hash TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, receipt TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS reports(seq INTEGER PRIMARY KEY, report_hash TEXT NOT NULL, body TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS unadopted_reports(seq INTEGER PRIMARY KEY, report_hash TEXT NOT NULL, body TEXT NOT NULL, reason TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS readback_blocks(evidence_ref TEXT PRIMARY KEY, reason TEXT NOT NULL)')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def read_coordination_report(self):
        self._policy_current()
        if not self.journal.exists():
            return {'state': 'NO_REVIEW_CONTACT', 'native_acceptance_verified': False, 'reports': []}
        with self._db() as db:
            row = db.execute('SELECT * FROM contact WHERE id=1').fetchone()
            require(row and row['binding_hash'] == canonical_hash(self.policy['binding']), 'JOURNAL_BINDING_CONFLICT')
            reports = [parse(x['body']) for x in db.execute('SELECT body FROM reports ORDER BY seq')]
            unadopted = [{**parse(x['body']), 'reason': x['reason']} for x in db.execute('SELECT body,reason FROM unadopted_reports ORDER BY seq')]
            blocks = [dict(x) for x in db.execute('SELECT evidence_ref,reason FROM readback_blocks ORDER BY evidence_ref')]
            return {'parent_binding': self.policy['binding'], 'child_action_id': parse(row['intent'])['action_id'],
                    'state': row['state'], 'native_acceptance_verified': any(x['report']['phase'] == 'ACCEPTED' for x in reports),
                    'receipt': parse(row['receipt']) if row['receipt'] else None, 'reports': reports,
                    'unadopted_reports': unadopted, 'readback_blocks': blocks}

    def _record(self, intent, state, receipt=None):
        if not self.journal.exists():
            # The published database's existence means a committed original
            # reservation. A crash before this link cannot leave an empty journal
            # that suppresses the first authorized send forever.
            with tempfile.TemporaryDirectory(dir=self.state, prefix='.prepare-review-') as folder:
                pending = Path(folder) / self.journal.name
                pending.touch(mode=0o600)
                with self._db(True, path=pending) as db:
                    db.execute('INSERT INTO contact VALUES(1,?,?,?,?,?,?)', (canonical_hash(self.policy['binding']),
                               canonical(intent).decode(), self.policy_hash, state, self.clock().isoformat(),
                               canonical(receipt).decode() if receipt else None))
                os.link(pending, self.journal)
                directory = os.open(self.state, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            return
        with self._db(True) as db:
            row = db.execute('SELECT * FROM contact WHERE id=1').fetchone()
            require(row and row['binding_hash'] == canonical_hash(self.policy['binding']) and row['intent'] == canonical(intent).decode()
                    and row['admission_hash'] == self.policy_hash, 'REVIEW_RESERVATION_CONFLICT')
            db.execute('UPDATE contact SET state=?,receipt=COALESCE(?,receipt) WHERE id=1',
                       (state, canonical(receipt).decode() if receipt else None))

    def _reconcile(self, channel, intent):
        # Original-action readback is evidence recovery, never renewed authority.
        # Expiry, cancellation or a successor must not hide an already sent action.
        self._context(historical=True)
        native_block = None
        try:
            handles = channel.reconcile(intent)
        except Exception as error:
            if getattr(error, 'code', None) != 'NATIVE_LISTENING_CAPABILITY_UNAVAILABLE':
                raise
            ref, handles = getattr(error, 'evidence_ref', None), getattr(error, 'validated_handles', None)
            require(isinstance(ref, str) and ref.startswith('native-goal-report-sha256:')
                    and len(ref.rsplit(':', 1)[1]) == 64 and all(c in '0123456789abcdef' for c in ref.rsplit(':', 1)[1])
                    and isinstance(handles, tuple), 'INVALID_NATIVE_READBACK_BLOCK')
            native_block = {'evidence_ref': ref, 'reason': error.code}
        _, _, status = self._context(historical=True)
        blocked = 'INDEPENDENT_LISTENING_EXECUTION_UNAVAILABLE' if native_block else None
        for session, handle in handles:
            item = channel.verified_observation(session, handle)
            require(channel.authenticated_peer(session) == item.peer and item.kind == 'goal_update'
                    and all(getattr(item, k) == intent[k] for k in ('project_id', 'request_id', 'action_id', 'generation', 'endpoint'))
                    and item.report_hash == canonical_hash(item.report), 'NATIVE_REVIEW_RECEIPT_MISMATCH')
            report = item.report
            require(isinstance(report, dict) and type(report.get('seq')) is int and report['seq'] > 0
                    and report.get('phase') in ('ACCEPTED', 'IN_PROGRESS', 'BLOCKED', 'COMPLETED')
                    and isinstance(report.get('evidence_refs'), list) and report['evidence_refs'], 'INVALID_NATIVE_REVIEW_REPORT')
            self._validate_report(report, intent)
            observation = {'source': 'VERIFIED_NATIVE_READBACK', 'evidence_ref': item.evidence_ref,
                           'report_hash': item.report_hash, 'report': report}
            with self._db(True) as db:
                prior = db.execute('SELECT * FROM reports WHERE seq=?', (report['seq'],)).fetchone()
                if prior:
                    require(prior['report_hash'] == item.report_hash, 'NATIVE_REVIEW_REPORT_CONFLICT')
                    continue
                claim = db.execute('SELECT report_hash FROM unadopted_reports WHERE seq=?', (report['seq'],)).fetchone()
                require(not claim or claim['report_hash'] == item.report_hash, 'NATIVE_REVIEW_REPORT_CONFLICT')
                rows = db.execute('SELECT body FROM reports ORDER BY seq').fetchall()
                row = db.execute('SELECT * FROM contact WHERE id=1').fetchone()
                require(report['seq'] == len(rows) + 1, 'NATIVE_REVIEW_REPORT_GAP')
                require(timestamp(row['created_at']) <= timestamp(report['observed_at']) <= self.clock(), 'INVALID_NATIVE_REVIEW_TIME')
                history = [parse(x['body'])['report'] for x in rows]
                require(not history or (history[-1]['phase'] != 'COMPLETED' and
                        timestamp(report['observed_at']) >= timestamp(history[-1]['observed_at'])), 'NATIVE_REVIEW_REPORT_REGRESSION')
                accepted = any(x['phase'] == 'ACCEPTED' for x in history)
                require((report['phase'] not in ('IN_PROGRESS', 'COMPLETED') or accepted)
                        and (report['phase'] != 'ACCEPTED' or not accepted), 'NATIVE_REVIEW_ACCEPTANCE_REQUIRED')
                if report['phase'] == 'COMPLETED' and intent['goal']['review_requirement'] == 'independent_listening':
                    blocked = 'INDEPENDENT_LISTENING_EXECUTION_UNAVAILABLE'
                    prior_claim = db.execute('SELECT report_hash FROM unadopted_reports WHERE seq=?', (report['seq'],)).fetchone()
                    require(not prior_claim or prior_claim['report_hash'] == item.report_hash, 'NATIVE_REVIEW_REPORT_CONFLICT')
                    db.execute('INSERT OR IGNORE INTO unadopted_reports VALUES(?,?,?,?)',
                               (report['seq'], item.report_hash, canonical(observation).decode(), blocked))
                    continue
                db.execute('INSERT INTO reports VALUES(?,?,?)', (report['seq'], item.report_hash, canonical(observation).decode()))
                db.execute('UPDATE contact SET state=? WHERE id=1', ('REVIEWER_' + report['phase'],))
        if native_block:
            with self._db(True) as db:
                db.execute('INSERT OR IGNORE INTO readback_blocks VALUES(?,?)',
                           (native_block['evidence_ref'], native_block['reason']))
        result = self.read_coordination_report()
        result.update(historical_readback=True, current_parent_status=status)
        if blocked:
            return {'state': 'BLOCKED', 'reason': blocked, 'original': result,
                    'native_acceptance_verified': result['native_acceptance_verified']}
        return result

    def _validate_report(self, report, intent):
        base = {'seq', 'phase', 'summary', 'observed_at', 'evidence_refs', 'next_owner'}
        require(all(plain(report.get(k)) for k in ('summary', 'observed_at', 'next_owner'))
                and len(report['evidence_refs']) <= 64 and all(plain(x) for x in report['evidence_refs']), 'INVALID_NATIVE_REVIEW_REPORT')
        observed = timestamp(report['observed_at'])
        require(observed <= timestamp(intent['deadline']), 'INVALID_NATIVE_REVIEW_TIME')
        if report['phase'] != 'COMPLETED':
            require(set(report) == base | {'next_check_at'} and
                    observed < timestamp(report['next_check_at']) <= timestamp(intent['deadline']), 'INVALID_NATIVE_REVIEW_REPORT')
            return
        required = base | {'terminal_condition', 'criteria_evidence'}
        if intent['goal']['review_requirement'] == 'independent_listening':
            required.add('independent_review')
        require(set(report) == required and plain(report['terminal_condition']), 'INVALID_NATIVE_REVIEW_REPORT')
        checks = report['criteria_evidence']
        require(isinstance(checks, list) and len(checks) == len(intent['goal']['acceptance_criteria']), 'REVIEW_CRITERIA_INCOMPLETE')
        covered = []
        for check in checks:
            require(isinstance(check, dict) and set(check) == {'criterion', 'evidence_refs'} and plain(check['criterion'])
                    and isinstance(check['evidence_refs'], list) and check['evidence_refs']
                    and all(plain(x) for x in check['evidence_refs']), 'REVIEW_CRITERIA_INCOMPLETE')
            covered.append(check['criterion'])
        require(len(set(covered)) == len(covered) and set(covered) == set(intent['goal']['acceptance_criteria']), 'REVIEW_CRITERIA_INCOMPLETE')
        if 'independent_review' in required:
            review, reviewer = report['independent_review'], self.policy['reviewer']
            require(isinstance(review, dict) and set(review) == {'state', 'reviewer', 'producer', 'scope_refs', 'evidence_refs', 'method'}
                    and review['state'] == 'VERIFIED' and review['reviewer'] == reviewer['principal']
                    and review['producer'] == reviewer['producer_principal'] and review['reviewer'] != review['producer']
                    and review['method'] in ('human_listening', 'audio_perception')
                    and isinstance(review['scope_refs'], list) and len(review['scope_refs']) == len(intent['goal']['scope_refs'])
                    and set(review['scope_refs']) == set(intent['goal']['scope_refs'])
                    and isinstance(review['evidence_refs'], list) and review['evidence_refs']
                    and all(plain(x) for x in review['evidence_refs']), 'INDEPENDENT_LISTENING_REQUIRED')

    def coordinate_independent_review(self):
        require(self.policy['role'] == 'project_owner' and 'plan_independent_review' in self.policy['binding']['allowed_actions'],
                'ACTION_NOT_ALLOWED')
        try:
            with self._lock():
                existing = self.journal.exists()
                core, context, _ = self._context(active=not existing, historical=existing)
                reviewer = self.policy['reviewer']
                require(reviewer is not None, 'NO_AUTHORIZED_REVIEWER')
                if not existing:
                    require(self.policy['review_contact_attempts'] == 1, 'REVIEW_CONTACT_NOT_AUTHORIZED')
                    require(set(CHILD_ACTIONS) <= set(self.policy['binding']['allowed_actions']), 'REVIEW_ACTIONS_NOT_AUTHORIZED')
                    self._reviewer()
                    self._source(core, context)
                channel_policy = parse(pinned(reviewer['channel_policy'], private=True))
                require(type(channel_policy.get('v')) is int and channel_policy['v'] == 2, 'REVIEW_CHANNEL_V2_REQUIRED')
                if not existing:
                    review_tool_hash = self._reviewer_tool_boundary(channel_policy)
                channel = self.channel_loader(reviewer['channel_policy']['path'])
                require(canonical(channel.policy) == canonical(channel_policy), 'REVIEW_CHANNEL_POLICY_CHANGED')
                intent = self._child(context, reviewer)
                if existing:
                    with self._db() as db:
                        row = db.execute('SELECT * FROM contact WHERE id=1').fetchone()
                        require(row and row['intent'] == canonical(intent).decode() and row['admission_hash'] == self.policy_hash,
                                'REVIEW_RESERVATION_CONFLICT')
                        state = row['state']
                    if state == 'EXTERNAL_SEEN':
                        return {'state': 'BLOCKED', 'reason': 'EXISTING_NATIVE_ACTION_REQUIRES_RECONCILIATION',
                                'original': self.read_coordination_report()}
                    if state == 'NOT_SENT':
                        return self.read_coordination_report()
                    return self._reconcile(channel, intent)  # INTENT/UNKNOWN never call submit again.
                channel.bind_organization_parent(self.core_dir, reviewer_policy_sha256=review_tool_hash)
                for ref in self.evidence.values():
                    if ref['kind'] == 'prior_round':
                        require(ref['prior_round_ref'] not in (context['action_id'], intent['request_id'], intent['action_id']),
                                'INVALID_PRIOR_ROUND')
                    if ref['kind'] in ('arrangement', 'receipt'):
                        imported = self._evidence(ref['ref_id'])
                        self._context(active=True)
                        return {'state': 'BLOCKED', 'reason': 'EXISTING_ARRANGEMENT_REQUIRES_RECONCILIATION', 'existing': imported}
                found = channel.lookup_goal(intent)
                require(found.get('state') in ('ABSENT', 'PRESENT'), 'NATIVE_LOOKUP_UNKNOWN')
                if found['state'] == 'PRESENT':
                    self._record(intent, 'EXTERNAL_SEEN', found)  # Observation barrier, never a claimed submission.
                    return {'state': 'BLOCKED', 'reason': 'EXISTING_NATIVE_ACTION_REQUIRES_RECONCILIATION', 'native_ref': found}
                with channel.prepare_goal(intent) as preparation:
                    self._context(active=True)
                    self._record(intent, 'INTENT')
                    def verify_current():
                        current, original, _ = self._context(active=True)
                        self._reviewer()
                        pinned(reviewer['channel_policy'], private=True)
                        self._source(current, original)
                        # Network/native reads can outlive cancellation or handoff.
                        # End at the local parent fence after those slower checks.
                        self._reviewer()
                        require(self._reviewer_tool_boundary(channel_policy) == review_tool_hash,
                                'REVIEW_TOOL_POLICY_CHANGED')
                        self._context(active=True)
                        return {k: intent[k] for k in (*CONTACT_KEYS, 'deadline')}
                    try:
                        receipt = channel.submit_goal(intent, verify_current=verify_current, preparation=preparation)
                        require(isinstance(receipt, dict) and all(receipt.get(k) == intent[k] for k in CONTACT_KEYS)
                                and receipt.get('kind') == 'DELEGATE_GOAL' and receipt.get('outcome') in ('SUBMITTED', 'NOT_SENT', 'UNKNOWN'),
                                'NATIVE_REVIEW_RECEIPT_MISMATCH')
                        self._record(intent, receipt['outcome'], receipt)
                    except Exception:
                        self._record(intent, 'UNKNOWN')
                        return {'state': 'UNKNOWN', 'reason': 'RECONCILE_ORIGINAL_REVIEW_ACTION', 'action_id': intent['action_id']}
                if receipt['outcome'] == 'NOT_SENT':
                    return self.read_coordination_report()
                return self._reconcile(channel, intent)
        except Exception as error:
            code = getattr(error, 'code', 'REVIEW_OBSERVATION_UNKNOWN')
            original = self.read_coordination_report() if self.journal.exists() else None
            return {'state': 'BLOCKED' if original is None else 'UNKNOWN', 'reason': code,
                    'original': original, 'native_acceptance_verified': bool(original and original['native_acceptance_verified'])}

    def tools(self):
        actions = self.policy['binding']['allowed_actions']
        names = [('goal_context', {}, [], True)]
        if 'report_progress' in actions:
            names.append(('read_coordination_report', {}, [], True))
        if 'verify_existing_evidence' in actions:
            names.append(('read_review_evidence', {'ref_id': {'type': 'string', 'enum': list(self.evidence)}}, ['ref_id'], True))
        if self.policy['role'] == 'project_owner' and 'plan_independent_review' in actions:
            names.append(('coordinate_independent_review', {}, [], False))
        return [{'name': name, 'description': name.replace('_', ' ') + '; exact reviewed single-job scope only.',
                 'inputSchema': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False},
                 'annotations': {'readOnlyHint': readonly, 'destructiveHint': False, 'openWorldHint': not readonly}}
                for name, properties, required, readonly in names]

    def call(self, name, arguments):
        allowed = {x['name']: x for x in self.tools()}
        require(name in allowed and isinstance(arguments, dict) and set(arguments) == set(allowed[name]['inputSchema']['required']),
                'TOOL_ARGUMENTS_NOT_ALLOWED')
        return getattr(self, name)(**arguments)


def serve(tools, source, sink):
    initialized, ready = False, False
    while True:
        raw = source.readline(MAX_FRAME + 1)
        if not raw:
            return
        response_id = None
        try:
            require(len(raw) <= MAX_FRAME and raw.endswith(b'\n'), 'INVALID_FRAME')
            request = parse(raw)
            require(isinstance(request, dict) and request.get('jsonrpc') == '2.0' and plain(request.get('method')), 'INVALID_REQUEST')
            response_id = request.get('id')
            require(response_id is None or type(response_id) in (str, int), 'INVALID_REQUEST')
            method, params = request['method'], request.get('params', {})
            if response_id is None:
                if method == 'notifications/initialized' and initialized:
                    ready = True
                continue
            if method == 'initialize':
                require(not initialized and isinstance(params, dict) and plain(params.get('protocolVersion'))
                        and isinstance(params.get('clientInfo'), dict) and isinstance(params.get('capabilities'), dict), 'INVALID_INITIALIZE')
                initialized = True
                result = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': 'project-coordinator-tools', 'version': '1.0.0'}}
            elif method == 'ping':
                result = {}
            else:
                require(ready, 'NOT_INITIALIZED')
                if method == 'tools/list':
                    require(params == {}, 'INVALID_PARAMS')
                    result = {'tools': tools.tools()}
                elif method == 'tools/call':
                    require(isinstance(params, dict) and set(params) == {'name', 'arguments'}, 'INVALID_PARAMS')
                    try:
                        value = tools.call(params['name'], params['arguments'])
                        result = {'content': [{'type': 'text', 'text': canonical(value, max_bytes=MAX_FRAME - 1).decode()}],
                                  'structuredContent': value, 'isError': False}
                    except Exception as error:
                        result = {'content': [{'type': 'text', 'text': getattr(error, 'code', 'TOOL_FAILED')}], 'isError': True}
                else:
                    raise ToolError('METHOD_NOT_FOUND')
            answer = {'jsonrpc': '2.0', 'id': response_id, 'result': result}
        except Exception as error:
            answer = {'jsonrpc': '2.0', 'id': response_id, 'error': {'code': -32601 if getattr(error, 'code', '') == 'METHOD_NOT_FOUND' else -32600,
                      'message': getattr(error, 'code', 'INVALID_REQUEST')}}
        try:
            frame = canonical(answer, max_bytes=MAX_FRAME - 1)
        except CoordinatorError as error:
            if error.code != 'MESSAGE_TOO_LARGE':
                raise
            # An oversized request ID can itself prevent a bounded error reply.
            small_id = response_id if len(str(response_id)) <= 256 else None
            frame = canonical({'jsonrpc': '2.0', 'id': small_id,
                               'error': {'code': -32000, 'message': 'MESSAGE_TOO_LARGE'}})
        sink.write(frame + b'\n')
        sink.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        serve(GoalTools(args.policy), sys.stdin.buffer, sys.stdout.buffer)
        return 0
    except Exception as error:
        print(getattr(error, 'code', 'TOOL_SERVER_FAILED'), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
