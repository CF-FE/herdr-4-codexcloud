"""Run the installed-style CLI against a temporary authenticated Unix endpoint."""

import hashlib
import hmac
import json
from pathlib import Path
import secrets
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from project_coordinator import Coordinator, canonical_hash


def wire(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


class TestBridge(socketserver.StreamRequestHandler):
    def handle(self):
        port = self.server
        challenge = secrets.token_hex(32)
        self.wfile.write(wire({'v': 1, 'challenge': challenge}) + b'\n')
        request = json.loads(self.rfile.readline())
        auth = request.pop('auth')
        signature = hmac.new(port.key, b'MBRIDGE/1\0' + challenge.encode() + b'\0' + wire(request),
                             hashlib.sha256).hexdigest()
        if auth != {'key_id': 'port-1', 'mac': signature}:
            self.wfile.write(wire({'v': 1, 'ok': False, 'code': 'ACCESS_DENIED'}) + b'\n')
            return
        op = request['op']
        port.calls.append(op)
        if op == 'probe':
            value = {'service': 'original-bridge', 'protocol': 1,
                     'operations': ['verified_job', 'lookup_report', 'enqueue_result']}
        elif op == 'verified_job':
            value = port.verified
        elif op == 'lookup_report':
            value = port.reports.get(request['event_id'])
        else:
            assert op == 'enqueue_result'
            value = {'job_id': request['job_id'], 'event_id': request['event_id'], 'status': request['status'],
                     'result_hash': canonical_hash(request['result']), 'state': 'PENDING', 'receipt': None}
            assert request['event_id'] not in port.reports
            port.reports[request['event_id']] = value
            if port.lose_ack:
                port.lose_ack = False
                return
        answer = {'v': 1, 'ok': True, 'value': value}
        if not port.forge_reply:
            proof = b'MBRIDGE/1/REPLY\0' + challenge.encode() + b'\0' + signature.encode() + b'\0' + wire(answer)
            answer['server_mac'] = hmac.new(port.key, proof, hashlib.sha256).hexdigest()
        self.wfile.write(wire(answer) + b'\n')


class BridgeCommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        address = self.root / 'b.sock'
        self.port = socketserver.UnixStreamServer(str(address), TestBridge)
        address.chmod(0o600)
        self.port.key = secrets.token_bytes(32)
        self.port.calls, self.port.reports, self.port.lose_ack, self.port.forge_reply = [], {}, False, False
        thread = threading.Thread(target=self.port.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(self.port.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.port.shutdown)
        self.config = {'v': 1, 'socket_path': str(address), 'key_id': 'port-1', 'key_hex': self.port.key.hex(),
                       'recipient': 'coordinator-test', 'grants': [], 'pilot': None}
        self.config_path = self.root / 'bridge-port.json'

    def run_cli(self, *args, error=None):
        self.config_path.write_bytes(wire(self.config))
        self.config_path.chmod(0o600)
        process = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/coordinator_bridge.py'),
                                  '--config', str(self.config_path), *args], capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 2 if error else 0, process.stderr)
        if error:
            self.assertEqual(json.loads(process.stderr)['error']['code'], error)
        else:
            return json.loads(process.stdout)

    def project(self):
        self.core = Coordinator(self.root / 'state')
        project_ref = 'https://github.com/orgs/CF-FE/projects/1'
        issue = 'https://github.com/CF-FE/example/issues/1'
        self.core.manage('register_project', {'project_id': 'example', 'repository': 'CF-FE/example',
                         'project_ref': project_ref, 'cloud_dot_principal': 'source:native', 'report_route': 'original-job'})
        self.config['grants'] = [{'project_id': 'example', 'channel': 'native-source', 'subject': 'reader-a',
                                  'principal': 'source:native', 'sender_alias': 'dot-alias'}]
        payload = {'schema_version': 1, 'origin_task_id': issue, 'sender': 'dot-alias', 'recipient': 'coordinator-test',
                   'report_route': 'original-job', 'target': {'service': 'project-coordinator', 'project_id': 'example'},
                   'content': '核原任务的阻塞证据', 'tracking': {'repository': 'CF-FE/example', 'project_url': project_ref,
                        'item_id': 1, 'issue_url': issue, 'iteration': {'state': 'UNSCHEDULED', 'reason': 'Fixture'}},
                   'coordinator_request': {'schema_version': 1, 'kind': 'QUERY_BLOCKER', 'request_id': 'query-1',
                        'idempotency_key': 'key-1', 'expected_generation': 0, 'deadline': '2099-01-01T00:00:00Z',
                        'authorization_ref': 'test:read-only', 'evidence_refs': [],
                        'budget': {'contact_attempts': 1, 'production_writes': 0, 'audio_generations': 0}}}
        digest = canonical_hash(payload)
        self.port.verified = {'origin': {'channel': 'native-source', 'subject': 'reader-a'},
            'origin_ref': 'verified-native-job-and-sha', 'job_id': 'job-1', 'payload_hash': digest,
            'task': {'repository': 'CF-FE/example', 'project_ref': project_ref, 'item_id': 1,
                     'task_ref': issue, 'evidence_ref': 'verified-board-item'},
            'record': {'job_id': 'job-1', 'payload_hash': digest, 'payload': payload, 'cancel_requested': False}}
        self.config['pilot'] = {'project_id': 'example', 'request_id': 'query-1', 'job_id': 'job-1', 'payload_hash': digest}

    def test_probe_requires_key_but_no_business_binding_or_db(self):
        self.assertEqual(self.run_cli('probe')['state'], 'PORT_VERIFIED')
        self.assertEqual(self.run_cli('pump')['state'], 'NO_PILOT')
        self.assertFalse((self.root / 'state').exists())
        self.config['key_hex'] = '00' * 32
        self.run_cli('probe', error='ACCESS_DENIED')
        self.assertEqual(self.port.calls, ['probe'])
        self.config['key_hex'] = self.port.key.hex()
        self.port.forge_reply = True
        self.run_cli('probe', error='UNVERIFIED_BRIDGE_RESPONSE')

    def test_pump_checks_fixed_original_and_native_peer_before_intake(self):
        self.project()
        self.config['pilot']['request_id'] = 'wrong'
        self.run_cli('pump', error='PILOT_BINDING_MISMATCH')
        self.config['pilot']['request_id'] = 'query-1'
        self.config['pilot']['payload_hash'] = '00' * 32
        self.run_cli('pump', error='PILOT_BINDING_MISMATCH')
        self.port.verified['origin']['subject'] = 'unverified'
        self.run_cli('deliver', '--job-id', 'job-1', error='ACCESS_DENIED')
        self.assertEqual(self.core.local_health('example')['open_followups'], 0)

    def test_source_drift_same_principal_alias_refs_and_payload_blocks_original_outbox(self):
        self.project()
        self.run_cli('deliver', '--job-id', 'job-1')
        self.core.manage('report_wait', {'project_id': 'example', 'request_id': 'query-1', 'evidence_ref': 'wait'})
        alternate = dict(self.config['grants'][0], subject='another-native-subject')
        self.config['grants'].append(alternate)
        self.port.verified['origin']['subject'] = alternate['subject']
        self.run_cli('deliver', '--job-id', 'job-1', error='ORIGIN_BINDING_CONFLICT')
        self.run_cli('pump', error='ORIGIN_BINDING_CONFLICT')
        self.port.verified['origin']['subject'] = self.config['grants'][0]['subject']
        self.port.verified['task']['evidence_ref'] = 'other-project-proof'
        self.run_cli('pump', error='ORIGIN_BINDING_CONFLICT')
        self.assertEqual(self.port.calls.count('enqueue_result'), 0)

    def test_preexisting_admission_without_native_proof_remains_locally_readable(self):
        self.project()
        raw = self.port.verified['record']['payload']
        source = {k: v for k, v in raw['coordinator_request'].items() if k != 'schema_version'}
        source.update(project_id='example', job_id='job-1', payload_hash=self.port.verified['payload_hash'],
                      task_ref=raw['origin_task_id'], question=raw['content'],
                      evidence_refs=[self.port.verified['origin_ref'], self.port.verified['task']['evidence_ref']])
        self.core.submit('source:native', source)
        report = self.core.manage('report_wait', {'project_id': 'example', 'request_id': 'query-1', 'evidence_ref': 'wait'})
        self.run_cli('deliver', '--job-id', 'job-1', error='ORIGIN_BINDING_UNATTESTED')
        self.run_cli('pump', error='ORIGIN_BINDING_UNATTESTED')
        self.assertEqual(self.core.delivery('example', 'query-1'), report)
        self.assertEqual(self.core.local_health('example')['open_followups'], 1)
        self.assertEqual(self.port.calls.count('enqueue_result'), 0)

    def test_late_results_drain_contiguously_through_original_outbox_after_ack_loss(self):
        self.project()
        self.assertEqual(self.run_cli('pump')['request_state'], 'WAITING_OWNER')
        self.assertEqual(self.run_cli('deliver', '--job-id', 'job-1')['state'], 'WAITING_OWNER')
        self.assertEqual(self.core.local_health('example')['open_followups'], 1)
        self.assertEqual(self.run_cli('pump')['next_version'], 1)
        for evidence in ('first-wait', 'second-wait'):
            self.core.manage('report_wait', {'project_id': 'example', 'request_id': 'query-1', 'evidence_ref': evidence})
        self.port.lose_ack = True
        process = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/coordinator_bridge.py'),
                                 '--config', str(self.config_path), 'pump'], capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)['state'], 'UNKNOWN')
        self.assertEqual(self.core.pilot_outbox_version('example', 'query-1'), 1)
        for version in (1, 2):
            sent = self.run_cli('pump')
            self.assertEqual((sent['state'], sent['core_version']), ('PENDING', version))
        self.assertEqual(self.run_cli('pump')['next_version'], 3)
        explicit = self.run_cli('enqueue', '--project-id', 'example', '--request-id', 'query-1', '--version', '1')
        self.assertEqual(explicit['state'], 'PENDING')
        self.assertEqual(self.port.calls.count('enqueue_result'), 2)
        self.assertFalse(self.core.inspect('example', 'query-1')['cloud_dot_read'])
        self.assertIsNone(self.core.inspect('example', 'query-1')['action_id'])
