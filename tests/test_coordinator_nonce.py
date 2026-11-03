"""One transport-only result and recovery in a private temporary case; no live client, OAuth or agent."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from coordinator_bridge import SocketBridge
from coordinator_nonce import handle, load_policy, read_private
from coordinator_transport import Peer, TransportError, VerifiedJob, VerifiedTask
from project_coordinator import canonical, canonical_hash


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


class FixtureConnection(SocketBridge):
    """Exercise the installed client protocol, substituting only the authenticated wire exchange."""

    def __init__(self, remote):
        self.remote = remote

    def call(self, op, **args):
        if op == 'verified_job':
            return asdict(self.remote.verified_job(**args))
        if op == 'lookup_report':
            return self.remote.lookup_report(**args)
        if op == 'enqueue_result':
            return self.remote.enqueue_result(**args)
        raise AssertionError('unexpected probe operation')


class NonceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.case = Path(tmp.name).resolve()
        tracking = {'repository': 'CF-FE/task-mailbox-mcp', 'project_url': 'https://github.com/orgs/CF-FE/projects/6',
                    'item_id': 17, 'issue_url': 'https://github.com/CF-FE/task-mailbox-mcp/issues/1',
                    'iteration': {'state': 'UNSCHEDULED', 'reason': 'Isolated probe fixture'}}
        payload = {'schema_version': 1, 'origin_task_id': tracking['issue_url'], 'sender': 'cloud-dot',
                   'recipient': 'mac-coordinator', 'report_route': 'task-mailbox:original-job',
                   'target': {'service': 'transport-probe', 'operation': 'sha256'}, 'tracking': tracking,
                   'content': 'Fixture-π-$()-only-data'}
        self.job = 'single-fixture'
        digest = canonical_hash(payload)
        self.original = VerifiedJob(Peer('fixture', 'origin'), 'test:bound', self.job, digest,
            {'job_id': self.job, 'payload_hash': digest, 'payload': payload, 'created_at': NOW.isoformat(),
             'status': 'PERSISTED', 'cancel_requested': False, 'latest_result_version': 0},
            VerifiedTask(tracking['repository'], tracking['project_url'], 17, tracking['issue_url'], 'test:item'))
        self.result = {'schema_version': 1, 'type': 'TRANSPORT_PROBE_RESULT', 'operation': 'sha256', 'job_id': self.job,
            'payload_hash': digest, 'execution_enabled': False, 'business_accepted': False, 'task_tracking': tracking,
            'nonce_sha256': hashlib.sha256(payload['content'].encode('utf-8')).hexdigest()}
        self.event = 'transport-probe-' + canonical_hash([self.job, digest])
        policy = dict(job_id=self.job, payload_hash=digest, expires_at='2030-01-02T00:00:00Z',
                      report_event_id=self.event, expected_result_hash=canonical_hash(self.result), expected_version=1)
        p = self.case / 'policy.json'
        p.write_bytes(canonical(policy)); p.chmod(0o600)
        self.policy = load_policy(p)
        self.queued = None
        self.calls = []
        self.lose_ack = False
        self.lookup_offline = False

    def verified_job(self, job_id):
        self.assertEqual(job_id, self.job)
        self.calls.append('verified_job')
        return deepcopy(self.original)

    def lookup_report(self, job_id, event_id):
        self.assertEqual((job_id, event_id), (self.job, self.event))
        self.calls.append('lookup_report')
        if self.lookup_offline:
            raise ConnectionError()
        return deepcopy(self.queued)

    def enqueue_result(self, **data):
        self.assertEqual(read_private(self.case / 'processed.json')['result'], self.result)
        self.assertEqual(data, {'project_id': 'task-mailbox-mcp', 'job_id': self.job, 'event_id': self.event,
            'payload_hash': self.policy['payload_hash'], 'status': 'REPORTED_COMPLETED', 'result': self.result})
        self.calls.append('enqueue_result')
        self.assertIsNone(self.queued)
        self.queued = {'job_id': self.job, 'event_id': self.event, 'status': 'REPORTED_COMPLETED',
                       'result_hash': canonical_hash(data['result']), 'state': 'PENDING', 'receipt': None}
        if self.lose_ack:
            raise ConnectionError()
        return self.queued

    def step(self, when=NOW):
        return handle(self.case, self.policy, FixtureConnection(self), lambda: when)

    def test_durable_before_enqueue_reopen_after_lost_ack_and_verify_exact_remote_version(self):
        self.lose_ack = True
        with self.assertRaises(ConnectionError):
            self.step()
        receipt = (self.case / 'processed.json').read_bytes()
        self.assertNotIn(self.original.record['payload']['content'].encode(), receipt)
        self.assertEqual(self.step(), 'PENDING')
        self.queued.update(state='SENT', receipt={k:self.queued[k] for k in ('job_id','event_id','status','result_hash')})
        self.queued['receipt']['version'] = 2
        with self.assertRaisesRegex(TransportError, 'REMOTE_RECEIPT_MISMATCH'):
            self.step()
        self.assertFalse((self.case/'sent.json').exists())
        self.queued['receipt']['version'] = 1
        self.assertEqual(self.step(), 'SENT')
        self.assertEqual(self.step(), 'SENT')
        self.assertEqual(read_private(self.case/'sent.json')['result_hash'], self.policy['expected_result_hash'])
        self.assertEqual((self.case/'processed.json').read_bytes(), receipt)
        self.assertEqual(self.calls.count('enqueue_result'), 1)
        self.assertEqual(self.calls.count('verified_job'), 1)

    def test_cancellation_expiry_wrong_payload_and_source_drift_never_create_a_second_result(self):
        row = self.original.record
        row['cancel_requested'] = True
        with self.assertRaisesRegex(TransportError, 'CANCELLED_OR_UNVERIFIED'):
            self.step()
        row['cancel_requested'] = False
        with self.assertRaisesRegex(TransportError, 'CASE_EXPIRED'):
            self.step(datetime(2030,1,2,tzinfo=timezone.utc))
        nonce = row['payload']['content']
        row['payload']['content'] = 'different'
        with self.assertRaisesRegex(TransportError, 'ORIGINAL_DIGEST_MISMATCH'):
            self.step()
        self.assertFalse((self.case/'processed.json').exists())
        row['payload']['content'] = nonce
        self.lookup_offline = True
        with self.assertRaises(ConnectionError):
            self.step()
        self.lookup_offline = False
        self.original = replace(self.original, origin_ref='different-proof')
        with self.assertRaisesRegex(TransportError, 'RECEIPT_CONFLICT'):
            self.step()
        self.assertNotIn('enqueue_result', self.calls)

    def test_reject_synthesized_result_or_nonce_from_policy(self):
        path = self.case / 'policy.json'
        path.write_bytes(canonical({**self.policy, 'nonce': self.original.record['payload']['content']}))
        with self.assertRaisesRegex(TransportError, 'INVALID_POLICY'):
            load_policy(path)
        self.policy['expected_result_hash'] = '0' * 64
        with self.assertRaisesRegex(TransportError, 'COMPUTED_RESULT_MISMATCH'):
            self.step()
        self.assertFalse((self.case/'processed.json').exists())
