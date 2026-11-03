"""Synthetic native sessions, original bridge and server with a real temporary core."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from coordinator_transport import (ACTIONS, CONTACT_KEYS, MAILBOX_LIMIT, CoordinatorTransport, Grant,
                                   OwnerObservation, Peer, TransportError, VerifiedJob, VerifiedRead, VerifiedTask)
from project_coordinator import Coordinator, CoordinatorError, canonical_hash


class FixturePorts:
    """Only the test fixture can register native sessions or confirm its reader."""

    def __init__(self):
        self.jobs, self.sessions, self.reports, self.reads, self.receipts = {}, {}, {}, {}, {}
        self.fetches = []
        self.enqueue_calls = 0
        self.lose_enqueue_ack = False
        self.lookup_offline = False
        self.owner_messages, self.contact_calls, self.native_effects = {}, [], []
        self.owner_generations = {'alpha': 1, 'beta': 1}
        self.lose_contact_ack = False
        self.not_sent_reason = None
        self.before_dispatch = None

    def verified_job(self, job_id):
        return deepcopy(self.jobs[job_id])

    def authenticated_peer(self, session):
        return self.sessions.get(session)

    def lookup_report(self, job_id, event_id):
        if self.lookup_offline:
            raise ConnectionError('synthetic unavailable outbox')
        return deepcopy(self.reports.get((job_id, event_id)))

    def enqueue_result(self, *, project_id, job_id, payload_hash, event_id, status, result):
        self.enqueue_calls += 1
        original = self.jobs[job_id]
        assert payload_hash == original.payload_hash
        assert original.record['payload']['target']['project_id'] == project_id
        assert result['task_tracking'] == original.record['payload']['tracking']
        record = {'job_id': job_id, 'event_id': event_id, 'status': status, 'result': deepcopy(result),
                  'result_hash': canonical_hash(result), 'state': 'PENDING', 'receipt': None}
        existing = self.reports.setdefault((job_id, event_id), record)
        if existing['result_hash'] != record['result_hash']:
            raise ValueError('conflicting synthetic producer event')
        if self.lose_enqueue_ack:
            self.lose_enqueue_ack = False
            raise TimeoutError('synthetic ACK lost after persistence')
        return deepcopy(existing)

    def upload(self, job_id, event_id):
        record = self.reports[(job_id, event_id)]
        receipt = {k: record[k] for k in ('job_id', 'event_id', 'result_hash', 'status')}
        # Server versions are in the original job's namespace, not the core's.
        record.update(state='SENT', receipt={**receipt, 'version': 7})

    def read_result(self, session, job_id, version):
        self.fetches.append((session, job_id, version))
        record = next((r for (job, _), r in self.reports.items() if job == job_id and r['receipt']
                       and r['receipt']['version'] == version), None)
        if record is None:
            raise KeyError('synthetic result version does not exist')
        self.reads[session] = deepcopy(record)
        return {**record['receipt'], 'result': deepcopy(record['result'])}

    def confirm_native_read(self, session):
        record = self.reads[session]
        handle = object()
        self.receipts[handle] = VerifiedRead(self.sessions[session], 'test:confirmed-read',
            record['job_id'], record['event_id'], record['receipt']['version'], record['result_hash'])
        return handle

    def verified_read(self, session, receipt_handle):
        return self.receipts.get(receipt_handle)

    def submit_contact(self, intent, *, verify_current):
        self.contact_calls.append(deepcopy(intent))
        assert intent['kind'] == 'QUERY_BLOCKER'
        assert intent['budget'] == {'contact_attempts': 1, 'production_writes': 0, 'audio_generations': 0}
        assert intent['job_id'] == 'job-' + intent['project_id']
        assert intent['payload_hash'] == self.jobs[intent['job_id']].payload_hash
        assert intent['task_ref'] == self.jobs[intent['job_id']].record['payload']['origin_task_id']
        assert intent['authorization_ref'] == 'test:scope'
        receipt = {k: intent[k] for k in CONTACT_KEYS}
        if self.before_dispatch:
            self.before_dispatch()
        try:
            permit = verify_current()
        except CoordinatorError:
            return {**receipt, 'outcome': 'NOT_SENT', 'reason': 'POLICY_DENIED', 'evidence_ref': 'test:local-fence-rejected'}
        assert all(permit[k] == intent[k] for k in ('action_id', 'generation', 'owner_principal', 'endpoint', 'deadline'))
        if self.owner_generations[intent['project_id']] != intent['generation']:
            return {**receipt, 'outcome': 'NOT_SENT', 'reason': 'STALE_GENERATION', 'evidence_ref': 'test:stale-generation-denied'}
        if self.not_sent_reason:
            return {**receipt, 'outcome': 'NOT_SENT', 'reason': self.not_sent_reason, 'evidence_ref': 'test:diagnostic-proof'}
        assert intent['action_id'] not in self.native_effects
        self.native_effects.append(intent['action_id'])
        if self.lose_contact_ack:
            raise TimeoutError('synthetic ACK loss after native submission')
        return {**receipt, 'outcome': 'SUBMITTED', 'reason': 'NATIVE_SUBMISSION', 'evidence_ref': 'test:native-submission'}

    def verified_observation(self, native_session, handle):
        return self.owner_messages.get(handle)


class CoordinatorTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / 'state'
        self.clock = lambda: datetime(2026, 11, 3, 8, tzinfo=timezone.utc)
        self.core = Coordinator(self.state, clock=self.clock)
        self.port = FixturePorts()
        self.alpha, self.beta, self.second = object(), object(), object()
        self.a_peer, self.b_peer = Peer('fixture-native', 'alpha-cloud-dot'), Peer('fixture-native', 'beta-cloud-dot')
        self.second_peer = Peer('fixture-native', 'another-enrolled-session')
        self.port.sessions = {self.alpha: self.a_peer, self.beta: self.b_peer, self.second: self.second_peer}
        owner_actions = frozenset({'owner_read', 'owner_reply'})
        cloud_dot_actions = ACTIONS - owner_actions
        self.owner, self.owner_peer = object(), Peer('fixture-owner-channel', 'alpha-owner')
        self.port.sessions[self.owner] = self.owner_peer
        self.grants = [Grant(self.a_peer, 'alpha', 'native:alpha', 'send-alpha', cloud_dot_actions),
                       Grant(self.b_peer, 'beta', 'native:beta', 'send-beta', cloud_dot_actions),
                       Grant(self.second_peer, 'alpha', 'native:alpha', 'other-alpha', cloud_dot_actions),
                       Grant(self.owner_peer, 'alpha', 'owner:alpha', 'owner-alpha', owner_actions)]
        self.adapter = self.new_adapter()
        for project, peer in [('alpha', self.a_peer), ('beta', self.b_peer)]:
            project_ref = 'https://github.com/orgs/CF-FE/projects/' + ('1' if project == 'alpha' else '2')
            self.core.manage('register_project', {'project_id': project, 'repository': 'CF-FE/' + project,
                'project_ref': project_ref, 'cloud_dot_principal': 'native:' + project, 'report_route': 'read:' + project})
            self.core.manage('activate_owner', {'project_id': project, 'expected_generation': 0,
                'owner_principal': 'owner:' + project, 'endpoint': 'owner-route:' + project,
                'appointment_ref': 'test:appointment', 'verification_ref': 'test:native-contact',
                'handoff_ref': 'test:accepted', 'release_ref': 'test:no-prior-writer'})
            payload = {'schema_version': 1, 'origin_task_id': 'github:CF-FE/' + project + '#1',
                'sender': 'send-' + project, 'recipient': 'test-coordinator', 'report_route': 'read:' + project,
                'target': {'service': 'project-coordinator', 'project_id': project},
                'content': 'Check existing evidence only.',
                'tracking': {'repository': 'CF-FE/' + project, 'project_url': project_ref,
                    'item_id': 1, 'issue_url': 'https://github.com/CF-FE/' + project + '/issues/1',
                    'iteration': {'state': 'UNSCHEDULED', 'reason': 'Synthetic fixture'}},
                'coordinator_request': {'schema_version': 1, 'kind': 'QUERY_BLOCKER',
                    'request_id': 'req-' + project, 'idempotency_key': 'key-' + project, 'expected_generation': 1,
                    'deadline': '2026-11-03T09:00:00+00:00', 'authorization_ref': 'test:scope',
                    'evidence_refs': ['test:existing-evidence'], 'budget': {'contact_attempts': 1,
                        'production_writes': 0, 'audio_generations': 0}}}
            self.bind_job(project, peer, payload)

    def bind_job(self, project, peer, payload):
        job_id = 'job-' + project
        digest = canonical_hash(payload, max_bytes=MAILBOX_LIMIT)
        task = VerifiedTask('CF-FE/' + project, self.core.route(project)['project_ref'], 1,
            'https://github.com/CF-FE/' + project + '/issues/1', 'test:verified-project-item:' + project)
        self.port.jobs[job_id] = VerifiedJob(peer, 'test:origin:' + project, job_id, digest,
            {'job_id': job_id, 'payload_hash': digest, 'payload': deepcopy(payload), 'cancel_requested': False}, task)

    def new_adapter(self):
        return CoordinatorTransport(self.core, grants=self.grants, recipient='test-coordinator',
                                    bridge=self.port, callers=self.port, owners=self.port)

    def complete_query(self, project='alpha'):
        req = 'req-' + project
        self.adapter.deliver('job-' + project)
        contact = self.core.claim_contact(project, req, 1)
        report = {'blocker': 'Existing artifact index awaits review.', 'impact': 'Acceptance waiting.',
                  'evidence_refs': ['test:existing-index'], 'verified': ['Read the index.'],
                  'unverified': ['Audio quality.'], 'next_owner': 'fixture-reviewer',
                  'next_condition': 'Reviewer confirms receipt.', 'observed_at': '2026-11-03T08:00:00Z'}
        return self.core.reply('owner:' + project, project, req, contact['action_id'], 1,
            'owner-route:' + project, 'test:owner-native-reply', report)['delivery']

    def assert_transport_error(self, code, operation):
        with self.assertRaises(TransportError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)

    def test_verified_hash_and_native_origin_are_both_required(self):
        original = self.port.jobs['job-alpha']
        self.port.jobs['job-alpha'] = replace(original, origin=self.b_peer)
        self.assert_transport_error('ACCESS_DENIED', lambda: self.adapter.deliver('job-alpha'))
        self.port.jobs['job-alpha'] = replace(original, job_id='job-beta')
        self.assert_transport_error('ORIGINAL_JOB_MISMATCH', lambda: self.adapter.deliver('job-alpha'))
        changed = deepcopy(original.record)
        changed['payload']['content'] = 'changed since the attestation'
        self.port.jobs['job-alpha'] = replace(original, record=changed)
        self.assert_transport_error('ORIGINAL_JOB_MISMATCH', lambda: self.adapter.deliver('job-alpha'))

    def test_body_cannot_choose_peer_endpoint_project_or_write_budget(self):
        original = self.port.jobs['job-alpha']
        for key, value, code in [('sender', 'send-beta', 'SENDER_BINDING_MISMATCH'),
                ('recipient', 'other-consumer', 'WRONG_RECIPIENT'), ('report_route', 'callback:other', 'WRONG_RECIPIENT'),
                ('tracking', {'repository': 'CF-FE/beta'}, 'PROJECT_BINDING_MISMATCH')]:
            with self.subTest(key=key):
                self.bind_job('alpha', self.a_peer, {**original.record['payload'], key: value})
                self.assert_transport_error(code, lambda: self.adapter.deliver('job-alpha'))
        payload = deepcopy(original.record['payload'])
        payload['coordinator_request']['principal'] = 'native:alpha'
        self.bind_job('alpha', self.a_peer, payload)
        self.assert_transport_error('INVALID_COORDINATOR_REQUEST', lambda: self.adapter.deliver('job-alpha'))
        del payload['coordinator_request']['principal']
        payload['coordinator_request']['budget']['audio_generations'] = 22
        self.bind_job('alpha', self.a_peer, payload)
        with self.assertRaises(CoordinatorError) as raised:
            self.adapter.deliver('job-alpha')
        self.assertEqual(raised.exception.code, 'INVALID_BUDGET')

    def test_cancelled_job_and_unbound_bridge_do_not_admit_requests(self):
        row = self.port.jobs['job-alpha']
        self.port.jobs['job-alpha'] = replace(row, record={**row.record, 'cancel_requested': True})
        self.assert_transport_error('JOB_CANCELLED_OR_UNVERIFIED', lambda: self.adapter.deliver('job-alpha'))
        unbound = CoordinatorTransport(self.core, grants=self.grants, recipient='test-coordinator',
                                       bridge=None, callers=None)
        self.assert_transport_error('BRIDGE_UNBOUND', lambda: unbound.deliver('job-alpha'))
        self.assert_transport_error('CALLER_CHANNEL_UNBOUND', lambda: unbound.health(self.alpha, 'alpha'))

    def test_well_known_foreign_ids_cannot_read_write_or_ack(self):
        self.complete_query('beta')
        operations = [lambda: self.adapter.status(self.alpha, 'beta', 'req-beta'),
                      lambda: self.adapter.cancel(self.alpha, 'beta', 'req-beta', 'test:forged'),
                      lambda: self.adapter.result(self.alpha, 'beta', 'req-beta', mailbox_version=7),
                      lambda: self.adapter.ack(self.alpha, 'beta', 'req-beta', mailbox_version=7, receipt_handle=object()),
                      lambda: self.adapter.health(self.alpha, 'beta'),
                      lambda: self.adapter.status('send-beta', 'beta', 'req-beta')]
        for operation in operations:
            self.assert_transport_error('ACCESS_DENIED', operation)
        with self.assertRaises(CoordinatorError) as raised:
            self.adapter.status(self.alpha, 'alpha', 'req-beta')
        self.assertEqual(raised.exception.code, 'REQUEST_NOT_FOUND')
        self.assertFalse(self.core.status('native:beta', 'beta', 'req-beta')['cancel_requested'])
        self.assertEqual(self.port.fetches, [])

    def test_granted_project_does_not_imply_all_verbs_or_all_native_readers(self):
        self.adapter.deliver('job-alpha')
        self.assert_transport_error('ACCESS_DENIED', lambda: self.adapter.status(self.second, 'alpha', 'req-alpha'))
        self.assert_transport_error('ACCESS_DENIED', lambda: self.adapter.cancel(self.second, 'alpha', 'req-alpha', 'x'))
        narrowed = CoordinatorTransport(self.core, grants=[replace(self.grants[0], actions=frozenset({'status'}))],
                                        recipient='test-coordinator', bridge=self.port, callers=self.port)
        self.assertEqual(narrowed.status(self.alpha, 'alpha', 'req-alpha')['state'], 'READY')
        self.assert_transport_error('ACCESS_DENIED', lambda: narrowed.cancel(self.alpha, 'alpha', 'req-alpha', 'x'))

    def test_lost_intake_ack_reopens_same_durable_request_without_contact(self):
        first = self.adapter.deliver('job-alpha')
        self.core = Coordinator(self.state, clock=self.clock)
        repeat = self.new_adapter().deliver('job-alpha')
        self.assertEqual(first, repeat)
        self.assertIsNone(repeat['action_id'])
        payload = deepcopy(self.port.jobs['job-alpha'].record['payload'])
        payload['content'] = 'Different request under same key.'
        self.bind_job('alpha', self.a_peer, payload)
        with self.assertRaises(CoordinatorError) as raised:
            self.adapter.deliver('job-alpha')
        self.assertEqual(raised.exception.code, 'IDEMPOTENCY_CONFLICT')

    def test_lost_outbox_ack_recovers_exact_event_without_duplicate_upload(self):
        outcome = self.complete_query()
        self.port.lookup_offline = True
        unknown = self.adapter.enqueue('alpha', 'req-alpha')
        self.assertEqual((unknown['state'], self.port.enqueue_calls), ('UNKNOWN', 0))
        self.port.lookup_offline, self.port.lose_enqueue_ack = False, True
        lost = self.adapter.enqueue('alpha', 'req-alpha')
        self.assertEqual((lost['state'], self.port.enqueue_calls), ('UNKNOWN', 1))
        self.core = Coordinator(self.state, clock=self.clock)
        self.adapter = self.new_adapter()
        pending = self.adapter.enqueue('alpha', 'req-alpha')
        self.assertEqual((pending['state'], self.port.enqueue_calls), ('PENDING', 1))
        self.assertEqual(lost['event_id'], outcome['event_id'])
        self.assertEqual(pending['result_hash'], lost['result_hash'])
        self.port.upload(outcome['job_id'], outcome['event_id'])
        sent = self.adapter.enqueue('alpha', 'req-alpha')
        self.assertEqual((sent['state'], sent['version'], self.port.enqueue_calls), ('SENT', 7, 1))

    def test_read_and_ack_use_original_cloud_dot_job_actual_wire_version_and_digest(self):
        outcome = self.complete_query()
        self.adapter.enqueue('alpha', 'req-alpha')
        self.port.upload(outcome['job_id'], outcome['event_id'])
        with self.assertRaises(KeyError):
            self.adapter.result(self.alpha, 'alpha', 'req-alpha', mailbox_version=1)
        result = self.adapter.result(self.alpha, 'alpha', 'req-alpha', mailbox_version=7)
        self.assertEqual(self.port.fetches[-1], (self.alpha, 'job-alpha', 7))
        self.assertEqual(result['result_hash'], canonical_hash(result['result']))
        self.assertNotEqual(result['result_hash'], outcome['result_hash'])
        self.assertEqual(result['result']['core_result_hash'], outcome['result_hash'])
        self.assertFalse(self.adapter.status(self.alpha, 'alpha', 'req-alpha')['cloud_dot_read'])
        receipt = self.port.confirm_native_read(self.alpha)
        self.assert_transport_error('READ_RECEIPT_MISMATCH', lambda: self.adapter.ack(self.alpha, 'alpha', 'req-alpha',
            mailbox_version=1, receipt_handle=receipt))
        self.assert_transport_error('READ_RECEIPT_MISMATCH', lambda: self.adapter.ack(self.second, 'alpha', 'req-alpha',
            mailbox_version=7, receipt_handle=receipt))
        ack = self.adapter.ack(self.alpha, 'alpha', 'req-alpha', mailbox_version=7, receipt_handle=receipt)
        self.assertTrue(ack['cloud_dot_read'])

    def test_mismatched_durable_receipt_and_replaced_wire_payload_are_rejected(self):
        outcome = self.complete_query()
        self.adapter.enqueue('alpha', 'req-alpha')
        key = outcome['job_id'], outcome['event_id']
        self.port.upload(*key)
        record = self.port.reports[key]
        good_receipt = deepcopy(record['receipt'])
        record['receipt']['result_hash'] = '0' * 64
        self.assert_transport_error('OUTBOX_RECEIPT_MISMATCH', lambda: self.adapter.enqueue('alpha', 'req-alpha'))
        record['receipt'] = good_receipt
        record['result']['task_tracking']['repository'] = 'CF-FE/beta'
        record['receipt']['result_hash'] = canonical_hash(record['result'])
        self.assert_transport_error('SERVER_RESULT_MISMATCH', lambda: self.adapter.result(
            self.alpha, 'alpha', 'req-alpha', mailbox_version=7))

    def test_same_repository_wrong_project_item_or_original_issue_is_rejected(self):
        original = self.port.jobs['job-alpha']
        for field, value, code in [('project_url', self.core.route('beta')['project_ref'], 'PROJECT_BINDING_MISMATCH'),
                ('item_id', 2, 'PROJECT_BINDING_MISMATCH'), ('item_id', True, 'PROJECT_BINDING_MISMATCH'),
                ('issue_url', 'https://github.com/CF-FE/alpha/issues/2', 'TASK_BINDING_MISMATCH')]:
            with self.subTest(field=field, value=value):
                payload = deepcopy(original.record['payload'])
                payload['tracking'][field] = value
                self.bind_job('alpha', self.a_peer, payload)
                self.assert_transport_error(code, lambda: self.adapter.deliver('job-alpha'))
        payload = deepcopy(original.record['payload'])
        payload['origin_task_id'] = 'github:CF-FE/alpha#2'
        self.bind_job('alpha', self.a_peer, payload)
        self.assert_transport_error('TASK_BINDING_MISMATCH', lambda: self.adapter.deliver('job-alpha'))

    def test_replaced_original_object_blocks_read_cancel_and_queue(self):
        self.complete_query()
        original = self.port.jobs['job-alpha']
        self.port.jobs['job-alpha'] = replace(original, task=replace(original.task, item_id=2))
        self.assert_transport_error('PROJECT_BINDING_MISMATCH', lambda: self.adapter.enqueue('alpha', 'req-alpha'))
        self.assert_transport_error('PROJECT_BINDING_MISMATCH', lambda: self.adapter.status(self.alpha, 'alpha', 'req-alpha'))
        changed = deepcopy(original.record['payload'])
        changed['content'] = 'Another original in a replaced source.'
        self.bind_job('alpha', self.a_peer, changed)
        self.assert_transport_error('ORIGINAL_JOB_MISMATCH', lambda: self.adapter.cancel(self.alpha, 'alpha', 'req-alpha', 'test:cancel'))
        self.assertFalse(self.core.status('native:alpha', 'alpha', 'req-alpha')['cancel_requested'])
        self.assertEqual(self.port.enqueue_calls, 0)

    def test_preverified_project_draft_retains_its_exact_native_origin(self):
        raw = deepcopy(self.port.jobs['job-alpha'].record['payload'])
        del raw['tracking']['issue_url']
        raw['origin_task_id'] = raw['tracking']['draft_id'] = 'DI_synthetic123'
        self.bind_job('alpha', self.a_peer, raw)
        original = self.port.jobs['job-alpha']
        self.assert_transport_error('TASK_BINDING_MISMATCH', lambda: self.adapter.deliver('job-alpha'))
        self.port.jobs['job-alpha'] = replace(original, task=replace(original.task, task_ref='DI_synthetic123'))
        self.assertEqual(self.adapter.deliver('job-alpha')['state'], 'READY')

    def test_native_owner_receipt_and_reply_require_the_bound_channel_and_target(self):
        self.adapter.deliver('job-alpha')
        sent = self.adapter.contact('alpha', 'req-alpha', 1)
        self.assertEqual(sent['outcome'], 'SUBMITTED')
        self.assertEqual(self.adapter.status(self.alpha, 'alpha', 'req-alpha')['owner_read'], False)
        read = OwnerObservation(self.owner_peer, 'owner-route:alpha', 'alpha', 'req-alpha', sent['action_id'],
                                1, 'owner_read', 'test:native-owner-read')
        handle = object()
        self.port.owner_messages[handle] = read
        self.assert_transport_error('ACCESS_DENIED', lambda: self.adapter.owner_read(self.alpha, 'alpha', 'req-alpha', handle))
        self.port.owner_messages[handle] = replace(read, peer=self.a_peer)
        self.assert_transport_error('OWNER_RECEIPT_MISMATCH', lambda: self.adapter.owner_read(self.owner, 'alpha', 'req-alpha', handle))
        self.port.owner_messages[handle] = replace(read, endpoint='wrong-native-endpoint')
        with self.assertRaises(CoordinatorError) as raised:
            self.adapter.owner_read(self.owner, 'alpha', 'req-alpha', handle)
        self.assertEqual(raised.exception.code, 'ACCESS_DENIED')
        self.port.owner_messages[handle] = read
        self.assertTrue(self.adapter.owner_read(self.owner, 'alpha', 'req-alpha', handle)['accepted'])
        report = {'blocker': 'Awaiting QA', 'impact': 'Release pending', 'evidence_refs': ['test:existing'],
                  'verified': [], 'unverified': ['QA'], 'next_owner': 'project QA', 'next_condition': 'QA accepts',
                  'observed_at': '2026-11-03T08:00:00Z'}
        self.port.owner_messages[handle] = replace(read, kind='owner_reply', report=report, report_hash='0' * 64)
        self.assert_transport_error('OWNER_REPLY_HASH_MISMATCH', lambda: self.adapter.owner_reply(
            self.owner, 'alpha', 'req-alpha', handle))
        self.port.owner_messages[handle] = replace(read, kind='owner_reply', report=report,
            report_hash=canonical_hash(report), evidence_ref='test:native-owner-response')
        reply = self.adapter.owner_reply(self.owner, 'alpha', 'req-alpha', handle)
        self.assertTrue(reply['accepted'])
        self.assertEqual(reply['delivery']['result']['source_principal'], 'owner:alpha')
        self.assertEqual(len(self.port.native_effects), 1)

    def test_stale_final_native_generation_is_definitely_not_sent(self):
        self.adapter.deliver('job-alpha')
        self.port.owner_generations['alpha'] = 2
        denied = self.adapter.contact('alpha', 'req-alpha', 1)
        self.assertEqual(denied['outcome'], 'NOT_SENT')
        self.assertEqual(self.port.native_effects, [])
        self.assertEqual(self.adapter.status(self.alpha, 'alpha', 'req-alpha')['contact_state'], 'NOT_SENT')

    def test_contact_ack_loss_restarts_with_original_intent_and_zero_resends(self):
        self.adapter.deliver('job-alpha')
        self.port.lose_contact_ack = True
        first = self.adapter.contact('alpha', 'req-alpha', 1)
        self.assertEqual(first['outcome'], 'UNKNOWN')
        self.assertEqual(first['reason'], 'ACK_UNCONFIRMED')
        self.core = Coordinator(self.state, clock=self.clock)
        self.adapter = self.new_adapter()
        second = self.adapter.contact('alpha', 'req-alpha', 1)
        self.assertEqual(second['outcome'], 'RECONCILE_ORIGINAL')
        self.assertFalse(second['native_attempted'])
        self.assertEqual(first['action_id'], second['action_id'])
        self.assertEqual((len(self.port.native_effects), len(self.port.contact_calls)), (1, 1))

    def test_original_mailbox_hash_uses_the_mailbox_budget(self):
        payload = deepcopy(self.port.jobs['job-alpha'].record['payload'])
        payload['unused_original_metadata'] = 'x' * (300 * 1024)
        self.bind_job('alpha', self.a_peer, payload)
        self.assertEqual(self.adapter.deliver('job-alpha')['payload_hash'], canonical_hash(payload, max_bytes=MAILBOX_LIMIT))

    def test_native_offline_and_unavailable_channel_have_distinct_durable_evidence(self):
        for project, reason in [('alpha', 'OWNER_OFFLINE'), ('beta', 'CHANNEL_UNAVAILABLE')]:
            with self.subTest(project=project):
                self.adapter.deliver('job-' + project)
                self.port.not_sent_reason = reason
                result = self.adapter.contact(project, 'req-' + project, 1)
                self.assertEqual((result['outcome'], result['reason']), ('NOT_SENT', reason))
                status = self.adapter.status(self.alpha if project == 'alpha' else self.beta, project, 'req-' + project)
                self.assertEqual(status['contact_reason'], reason)
                self.assertFalse(status['owner_read'])
        self.assertEqual(self.port.native_effects, [])

    def test_cancellation_between_claim_and_final_admission_blocks_native_effect(self):
        self.adapter.deliver('job-alpha')
        self.port.before_dispatch = lambda: self.adapter.cancel(self.alpha, 'alpha', 'req-alpha', 'test:cancelled')
        result = self.adapter.contact('alpha', 'req-alpha', 1)
        self.assertEqual((result['outcome'], result['reason']), ('NOT_SENT', 'POLICY_DENIED'))
        self.assertTrue(self.adapter.status(self.alpha, 'alpha', 'req-alpha')['cancel_requested'])
        self.assertEqual(self.port.native_effects, [])


if __name__ == '__main__':
    unittest.main()
