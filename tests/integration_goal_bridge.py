#!/usr/bin/env python3
"""Two-process signed GOAL contract integration; no production, network, or models.

Run from the coordinator checkout:
  python3 tests/integration_goal_bridge.py --mailbox-root /path/to/task-mailbox-mcp

Each child imports only its own checkout. A private temporary socket, two SQLite
ledgers, and a random test key are removed on exit. The native owner is simulated;
real results stop in the local bridge outbox (PENDING), without external upload.
This script is intentionally outside unittest's test_*.py discovery.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

DEFAULT_COORDINATOR_ROOT = Path(__file__).resolve().parents[1]

SERVER = r'''
import asyncio, collections, json, os, signal, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from mac.bridge_store import BridgeStore
from mac.coordinator_port import CoordinatorPort

root = Path(sys.argv[1])
store = BridgeStore(root / 'bridge.sqlite3', 'cross-goal-fixture')
port = CoordinatorPort(root / 'server.json', store)
job = json.loads((root / 'job.json').read_text())
proof = port.capture_proof(job)
assert proof is not None, 'fixture source grant did not match'
store.record_job(job, proof_hash=proof)
calls = collections.Counter()
fault = {'armed': False, 'used': False, 'lost_receipts': 0}
dispatch = port.dispatch
def traced_dispatch(frame, challenge):
    op = frame.get('op')
    calls[op] += 1
    answer = dispatch(frame, challenge)
    if op == 'enqueue_result' and answer.get('ok') and not fault['used']:
        # Real signed RPC and SQLite commit have completed. Drop its socket reply.
        fault['armed'], fault['used'] = True, True
    return answer
port.dispatch = traced_dispatch
exchange = port.exchange
class DropOneReply:
    def __init__(self, writer):
        self.writer = writer
    def write(self, data):
        if fault['armed']:
            fault['armed'] = False
            fault['lost_receipts'] += 1
            self.writer.close()
            return
        self.writer.write(data)
    def __getattr__(self, name):
        return getattr(self.writer, name)
async def faulty_exchange(reader, writer):
    await exchange(reader, DropOneReply(writer))
port.exchange = faulty_exchange
async def main():
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    async with port.listening():
        print(json.dumps({'ready': True, 'pid': os.getpid()}), flush=True)
        await asyncio.wait_for(stop.wait(), 45)
    with sqlite3.connect(store.path.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT job_id,event_id,result_hash,state,result FROM reports ORDER BY created_at,event_id').fetchall()
    reports = [{'job_id': r[0], 'event_id': r[1], 'wire_hash': r[2], 'state': r[3],
                'core_version': json.loads(r[4])['core_version'],
                'core_hash': json.loads(r[4])['core_result_hash']} for r in rows]
    print(json.dumps({'pid': os.getpid(), 'rpc_calls': dict(calls),
                      'lost_socket_receipts': fault['lost_receipts'], 'reports': reports}), flush=True)
asyncio.run(main())
'''

CLIENT = r'''
import json, os, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'scripts'))
from coordinator_bridge import SocketBridge
from coordinator_transport import CoordinatorTransport, Grant, OwnerObservation, Peer, CONTACT_KEYS
from project_coordinator import Coordinator, canonical_hash

root = Path(sys.argv[1])
config = json.loads((root / 'client.json').read_text())
job = json.loads((root / 'job.json').read_text())
payload, goal = job['payload'], job['payload']['coordinator_request']['goal']
core = Coordinator(root / 'core')
core.manage('register_project', {'project_id': 'example', 'repository': 'ExampleOrg/demo',
    'project_ref': payload['tracking']['project_url'], 'cloud_dot_principal': 'cloud:fixture',
    'report_route': payload['report_route']})
core.manage('activate_owner', {'project_id': 'example', 'expected_generation': 0,
    'owner_principal': 'owner:fixture', 'endpoint': 'fixture-native:owner',
    'appointment_ref': 'fixture:appointment', 'verification_ref': 'fixture:native-verification',
    'handoff_ref': 'fixture:handoff', 'release_ref': 'fixture:release'})
source, owner = Peer('fixture-source', 'original-native-subject'), Peer('fixture-native', 'owner-subject')
grants = [Grant(source, 'example', 'cloud:fixture', payload['sender'], frozenset({'submit_goal'})),
          Grant(owner, 'example', 'owner:fixture', 'owner-alias', frozenset({'goal_update'}))]
class NativeFixture:
    def __init__(self):
        self.calls, self.effects, self.observations = 0, [], {}
        self.session = object()
    def authenticated_peer(self, session):
        return owner if session is self.session else None
    def submit_goal(self, intent, *, verify_current):
        verify_current()
        self.calls += 1
        assert intent['action_id'] not in self.effects
        self.effects.append(intent['action_id'])
        # The one simulated native effect persisted, but its submit receipt is lost.
        raise TimeoutError('fixture native receipt lost after acceptance of transport')
    def verified_observation(self, session, handle):
        assert session is self.session
        return self.observations[handle]
native = NativeFixture()
bridge = SocketBridge(config)
def adapter(c):
    return CoordinatorTransport(c, grants=grants, recipient=payload['recipient'],
                                bridge=bridge, callers=native, owners=native)
transport = adapter(core)
received = transport.deliver(job['job_id'])
assert received['goal_state'] == 'ADMITTED' and not received['accepted']
assert transport.deliver(job['job_id'])['request_id'] == 'cross-goal-request'
first = transport.dispatch_goal('example', 'cross-goal-request', 1)
assert first['outcome'] == 'UNKNOWN'
core = Coordinator(root / 'core')
transport = adapter(core)
again = transport.dispatch_goal('example', 'cross-goal-request', 1)
assert again['outcome'] == 'RECONCILE_ORIGINAL' and not again['native_attempted']
assert again['action_id'] == first['action_id']
results = []
for seq, phase in ((1, 'ACCEPTED'), (2, 'COMPLETED')):
    report = {'seq': seq, 'phase': phase, 'summary': 'Synthetic trusted native goal observation.',
              'observed_at': datetime.now(timezone.utc).isoformat(),
              'evidence_refs': ['fixture:native:' + str(seq)], 'next_owner': 'owner:fixture'}
    if phase == 'ACCEPTED':
        report['next_check_at'] = goal['next_check_at']
    else:
        report.update(terminal_condition='Synthetic original evidence covers the frozen criteria.',
            criteria_evidence=[{'criterion': c, 'evidence_refs': ['fixture:criterion:' + str(i)]}
                               for i, c in enumerate(goal['acceptance_criteria'])],
            independent_review={'state': 'VERIFIED', 'reviewer': 'fixture:reviewer', 'producer': 'fixture:producer',
                'scope_refs': goal['scope_refs'], 'evidence_refs': ['fixture:listening-observation'], 'method': 'human_listening'})
    handle = object()
    native.observations[handle] = OwnerObservation(owner, 'fixture-native:owner', 'example', 'cross-goal-request',
        first['action_id'], 1, 'goal_update', 'fixture:receipt:' + str(seq), report, canonical_hash(report))
    delivery = transport.goal_update(native.session, 'example', 'cross-goal-request', handle)['delivery']
    assert transport.goal_update(native.session, 'example', 'cross-goal-request', handle)['delivery'] == delivery
    queued = transport.enqueue('example', 'cross-goal-request', delivery['version'])
    if seq == 1:
        assert queued['state'] == 'UNKNOWN', 'socket reply loss must not be reported as confirmed'
    pending = transport.enqueue('example', 'cross-goal-request', delivery['version'])
    assert pending['state'] == 'PENDING'
    assert pending == transport.enqueue('example', 'cross-goal-request', delivery['version'])
    assert pending['job_id'] == job['job_id'] and pending['event_id'] == delivery['event_id']
    results.append({'job_id': delivery['job_id'], 'event_id': delivery['event_id'], 'core_version': delivery['version'],
                    'core_hash': delivery['result_hash'], 'wire_hash': pending['result_hash'], 'state': pending['state']})
with sqlite3.connect(core.path.as_uri() + '?mode=ro', uri=True) as db:
    counts = {name: db.execute('SELECT count(*) FROM ' + name).fetchone()[0]
              for name in ('requests', 'contacts', 'results', 'goal_updates')}
assert counts == {'requests': 1, 'contacts': 1, 'results': 2, 'goal_updates': 2}
assert native.calls == len(native.effects) == 1
assert core.inspect('example', 'cross-goal-request')['goal_state'] == 'COMPLETED'
print(json.dumps({'pid': os.getpid(), 'native_fixture_attempts': native.calls, 'action_id': first['action_id'],
    'sqlite_counts': counts, 'results': results, 'goal_state': 'COMPLETED'}), flush=True)
'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def private_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def source_hashes(mailbox_root, coordinator_root):
    files = [('mailbox', mailbox_root, 'mac/coordinator_port.py'),
             ('mailbox', mailbox_root, 'mac/bridge_store.py'),
             ('coordinator', coordinator_root, 'scripts/coordinator_bridge.py'),
             ('coordinator', coordinator_root, 'scripts/coordinator_transport.py'),
             ('coordinator', coordinator_root, 'scripts/project_coordinator.py')]
    return {label + '/' + name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for label, root, name in files}


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mailbox-root', type=Path, required=True, help='Task Mailbox checkout with mac/coordinator_port.py')
    parser.add_argument('--coordinator-root', type=Path, default=DEFAULT_COORDINATOR_ROOT,
                        help="Coordinator checkout (defaults to this script's checkout)")
    parser.add_argument('--report', type=Path, help='Optional JSON evidence file; contains no test key or private state')
    args = parser.parse_args(argv)
    mailbox_root, coordinator_root = args.mailbox_root.resolve(strict=True), args.coordinator_root.resolve(strict=True)
    os.umask(0o077)
    before = source_hashes(mailbox_root, coordinator_root)
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix='goal-x-', dir='/tmp') as directory:
        root = Path(directory).resolve()
        key = secrets.token_hex(32)
        scopes = ['fixture:batch:segment-01', 'fixture:batch:segment-02']
        issue = 'https://github.com/ExampleOrg/demo/issues/2'
        project_url = 'https://github.com/orgs/ExampleOrg/projects/5'
        goal = {'objective': 'Synthetic coordination of an existing independent review.', 'scope_refs': scopes,
                'work_key': digest({'task_ref': issue, 'scope_refs': sorted(scopes)}),
                'acceptance_criteria': ['review accepted', 'original review evidence returned'],
                'evidence_refs': ['fixture:original-input'], 'related_request_ids': [],
                'allowed_actions': ['coordinate_existing_owner', 'verify_existing_evidence', 'plan_independent_review', 'report_progress'],
                'next_check_at': (now + timedelta(minutes=10)).isoformat(), 'review_requirement': 'independent_listening'}
        payload = {'schema_version': 1, 'origin_task_id': 'github:ExampleOrg/demo#2', 'sender': 'fixture-cloud-dot',
                   'recipient': 'cross-coordinator', 'report_route': 'original-job',
                   'target': {'service': 'project-coordinator', 'project_id': 'example'}, 'content': 'Synthetic test only.',
                   'tracking': {'repository': 'ExampleOrg/demo', 'project_url': project_url, 'issue_url': issue, 'item_id': 42,
                                'iteration': {'state': 'UNSCHEDULED', 'reason': 'Synthetic temporary contract fixture'}},
                   'coordinator_request': {'schema_version': 1, 'request_id': 'cross-goal-request', 'idempotency_key': 'cross-goal-key',
                       'kind': 'DELEGATE_GOAL', 'expected_generation': 1, 'deadline': (now + timedelta(hours=1)).isoformat(),
                       'authorization_ref': 'fixture:bounded-goal', 'evidence_refs': ['fixture:source'],
                       'budget': {'dispatch_attempts': 1, 'production_writes': 0, 'audio_generations': 0}, 'goal': goal}}
        job = {'job_id': 'cross-goal-job', 'payload': payload, 'payload_hash': digest(payload), 'status': 'PERSISTED',
               'cancel_requested': False, 'latest_result_version': 0, 'created_at': now.isoformat()}
        grant = {'key_id': 'cross-test-key', 'actions': ['verified_job', 'lookup_report', 'enqueue_result'],
                 'project_id': 'example', 'coordinator_id': 'cloud-dot-project-coordinator', 'recipient': payload['recipient'],
                 'report_route': payload['report_route'], 'job_id': job['job_id'], 'payload_hash': job['payload_hash'],
                 'origin': {'channel': 'fixture-source', 'subject': 'original-native-subject'}, 'origin_ref': 'fixture:authenticated-original',
                 'task': {'repository': 'ExampleOrg/demo', 'project_ref': project_url, 'item_id': 42,
                          'task_ref': issue, 'evidence_ref': 'fixture:authoritative-project-readback'}, 'request_kind': 'DELEGATE_GOAL'}
        private_json(root / 'job.json', job)
        private_json(root / 'server.json', {'v': 1, 'socket_path': str(root / 'b.sock'), 'keys': {'cross-test-key': key}, 'grants': [grant]})
        private_json(root / 'client.json', {'socket_path': str(root / 'b.sock'), 'key_id': 'cross-test-key', 'key_hex': key})
        server = subprocess.Popen([sys.executable, '-I', '-B', '-c', SERVER, str(root)], cwd=mailbox_root,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                assert selector.select(10), 'temporary socket server readiness timeout'
            ready = server.stdout.readline()
            if not ready:
                raise RuntimeError('temporary server failed: ' + server.stderr.read()[-3000:])
            assert json.loads(ready)['ready']
            client = subprocess.run([sys.executable, '-I', '-B', '-c', CLIENT, str(root)], cwd=coordinator_root,
                                    capture_output=True, text=True, timeout=25)
            assert client.returncode == 0, 'coordinator fixture failed: ' + client.stderr[-3000:]
            client_result = json.loads(client.stdout)
        finally:
            server.send_signal(signal.SIGTERM)
            try:
                output, errors = server.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                output, errors = server.communicate()
        assert server.returncode == 0, 'temporary server failed: ' + errors[-3000:]
        server_result = json.loads(output)
        assert client_result['pid'] != server_result['pid']
        assert server_result['lost_socket_receipts'] == 1
        assert server_result['rpc_calls']['enqueue_result'] == 2
        assert sorted(server_result['reports'], key=lambda x: x['core_version']) == client_result['results']
        assert source_hashes(mailbox_root, coordinator_root) == before, 'source changed during contract test'
        result = {'observed_at': datetime.now(timezone.utc).isoformat(), 'passed': True,
                  'real': ['two independent processes', 'Unix socket HMAC request and response', 'Coordinator SQLite', 'BridgeStore SQLite'],
                  'simulated': ['trusted native owner submission and business observations', 'native receipt loss', 'one socket reply loss'],
                  'not_exercised': ['external mailbox server upload', 'actual listening', 'production service', 'models'],
                  'source_hashes': before, 'client': client_result, 'server': server_result}
    result['temporary_state_and_keys_removed'] = not root.exists()
    if args.report:
        private_json(args.report, result)
    summary = {'passed': True, 'reports': len(result['server']['reports']),
               'core_versions': [r['core_version'] for r in result['client']['results']],
               'outbox_state': 'PENDING', 'native_attempts': result['client']['native_fixture_attempts'],
               'lost_socket_receipts': result['server']['lost_socket_receipts'],
               'rpc_calls': result['server']['rpc_calls'],
               'temporary_state_and_keys_removed': result['temporary_state_and_keys_removed'],
               'real': result['real'], 'simulated': result['simulated'], 'not_exercised': result['not_exercised']}
    if args.report:
        summary['report'] = str(args.report)
    print(json.dumps(summary))


if __name__ == '__main__':
    run()
