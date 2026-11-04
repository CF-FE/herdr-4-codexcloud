"""Full goal transport path with a real temporary ledger and synthetic native ports."""

from copy import deepcopy
from contextlib import nullcontext
from dataclasses import replace
import unittest

import test_coordinator_transport as fixtures
from coordinator_transport import CONTACT_KEYS, CoordinatorTransport, OwnerObservation, TransportError
from project_coordinator import Coordinator, CoordinatorError, GOAL_BUDGET, canonical_hash


class GoalPorts(fixtures.FixturePorts):
    def prepare_goal(self, binding):
        return nullcontext(binding['request_id'])

    def submit_goal(self, intent, *, verify_current, preparation):
        assert preparation == intent['request_id']
        self.contact_calls.append(deepcopy(intent))
        assert intent['kind'] == 'DELEGATE_GOAL'
        assert intent['budget'] == GOAL_BUDGET
        assert intent['task_ref'] == self.jobs[intent['job_id']].task.task_ref
        receipt = {key: intent[key] for key in CONTACT_KEYS}
        receipt['kind'] = 'DELEGATE_GOAL'
        if self.before_dispatch:
            self.before_dispatch()
        try:
            permit = verify_current()
        except (CoordinatorError, TransportError):
            return {**receipt, 'outcome': 'NOT_SENT', 'reason': 'POLICY_DENIED',
                    'evidence_ref': 'test:final-source-or-authority-gate'}
        assert all(permit[key] == intent[key] for key in ('action_id', 'generation', 'owner_principal', 'endpoint'))
        if self.owner_generations[intent['project_id']] != intent['generation']:
            return {**receipt, 'outcome': 'NOT_SENT', 'reason': 'STALE_GENERATION', 'evidence_ref': 'test:native-generation'}
        assert intent['action_id'] not in self.native_effects
        self.native_effects.append(intent['action_id'])
        if self.lose_contact_ack:
            raise TimeoutError('Synthetic receipt lost after native persistence')
        return {**receipt, 'outcome': 'SUBMITTED', 'reason': 'NATIVE_SUBMISSION', 'evidence_ref': 'test:goal-native-submit'}

    def upload(self, job_id, event_id):
        record = self.reports[(job_id, event_id)]
        if record['receipt']:
            return
        version = 7 + sum(bool(row['receipt']) for (job, _), row in self.reports.items() if job == job_id)
        receipt = {key: record[key] for key in ('job_id', 'event_id', 'result_hash', 'status')}
        record.update(state='SENT', receipt={**receipt, 'version': version})


class GoalTransportTests(unittest.TestCase):
    bind_job = fixtures.CoordinatorTransportTests.bind_job
    new_adapter = fixtures.CoordinatorTransportTests.new_adapter
    assert_transport_error = fixtures.CoordinatorTransportTests.assert_transport_error

    def setUp(self):
        fixtures.CoordinatorTransportTests.setUp(self)
        port = GoalPorts()
        port.__dict__.update(self.port.__dict__)
        self.port = port
        self.grants = [replace(grant, actions=frozenset({'goal_update'})) if grant.peer == self.owner_peer
                       else replace(grant, actions=grant.actions - {'goal_update'}) for grant in self.grants]
        self.adapter = self.new_adapter()
        payload = deepcopy(self.port.jobs['job-alpha'].record['payload'])
        scopes = ['batch:v1:segment-01', 'batch:v1:segment-02']
        self.goal = {'objective': 'Coordinate the existing review and return its evidence.',
            'work_key': canonical_hash({'task_ref': self.port.jobs['job-alpha'].task.task_ref, 'scope_refs': sorted(scopes)}),
            'scope_refs': scopes, 'acceptance_criteria': ['reviewer identified', 'listening evidence returned'],
            'evidence_refs': ['test:original-manifest'], 'related_request_ids': [],
            'allowed_actions': ['coordinate_existing_owner', 'verify_existing_evidence', 'plan_independent_review', 'report_progress'],
            'next_check_at': '2026-11-03T08:30:00Z', 'review_requirement': 'independent_listening'}
        payload['coordinator_request'].update(kind='DELEGATE_GOAL', goal=self.goal, budget=deepcopy(GOAL_BUDGET))
        self.bind_job('alpha', self.a_peer, payload)

    def start(self):
        self.adapter.deliver('job-alpha')
        self.dispatch = self.adapter.dispatch_goal('alpha', 'req-alpha', 1)
        return self.dispatch

    def observation(self, seq=1, phase='ACCEPTED'):
        report = {'seq': seq, 'phase': phase, 'summary': 'Checked the original review task.',
                  'observed_at': self.clock().isoformat(), 'evidence_refs': ['test:native-report:' + str(seq)],
                  'next_owner': 'owner:alpha'}
        if phase == 'COMPLETED':
            report.update(terminal_condition='Original evidence covers every criterion.',
                criteria_evidence=[{'criterion': criterion, 'evidence_refs': ['test:criterion:' + str(index)]}
                                   for index, criterion in enumerate(self.goal['acceptance_criteria'])],
                independent_review={'state': 'VERIFIED', 'reviewer': 'reviewer:bob', 'producer': 'producer:carol',
                    'scope_refs': self.goal['scope_refs'], 'evidence_refs': ['test:native-listening'], 'method': 'human_listening'})
        else:
            report['next_check_at'] = '2026-11-03T08:45:00Z'
        handle = object()
        self.port.owner_messages[handle] = OwnerObservation(self.owner_peer, 'owner-route:alpha', 'alpha', 'req-alpha',
            self.core.inspect('alpha', 'req-alpha')['action_id'], 1, 'goal_update', 'test:native-receipt:' + str(seq),
            report, canonical_hash(report))
        return handle

    def update(self, handle):
        return self.adapter.goal_update(self.owner, 'alpha', 'req-alpha', handle)

    def test_original_goal_reaches_native_owner_and_versions_return_to_same_job_with_ack(self):
        dispatch = self.start()
        self.assertEqual(dispatch['outcome'], 'SUBMITTED')
        self.assertEqual(len(self.port.native_effects), 1)
        self.assertFalse(self.core.inspect('alpha', 'req-alpha')['accepted'])
        versions = []
        for seq, phase in enumerate(('ACCEPTED', 'IN_PROGRESS', 'COMPLETED'), 1):
            handle = self.observation(seq, phase)
            delivery = self.update(handle)['delivery']
            self.assertEqual(self.update(handle)['delivery'], delivery)
            pending = self.adapter.enqueue('alpha', 'req-alpha', delivery['version'])
            self.assertEqual(pending['state'], 'PENDING')
            self.port.upload(pending['job_id'], pending['event_id'])
            sent = self.adapter.enqueue('alpha', 'req-alpha', delivery['version'])
            versions.append(sent['version'])
            wire = self.adapter.result(self.alpha, 'alpha', 'req-alpha', mailbox_version=sent['version'],
                                       core_version=delivery['version'])
            self.assertEqual(wire['result']['job_id'], 'job-alpha')
            self.assertEqual(wire['result']['core_result_hash'], delivery['result_hash'])
            self.assertEqual(wire['result_hash'], canonical_hash(wire['result']))
            handle = self.port.confirm_native_read(self.alpha)
            acknowledged = self.adapter.ack(self.alpha, 'alpha', 'req-alpha', mailbox_version=sent['version'],
                                             receipt_handle=handle, core_version=delivery['version'])
            self.assertTrue(acknowledged['cloud_dot_read'])
        self.assertEqual(versions, [7, 8, 9])
        self.assertEqual(self.core.inspect('alpha', 'req-alpha')['goal_state'], 'COMPLETED')
        self.assertEqual(self.port.enqueue_calls, 3)
        self.assertEqual(len(self.port.contact_calls), 1)

    def test_old_query_grant_and_query_channel_cannot_receive_goal(self):
        self.adapter.grants[(self.a_peer, 'alpha')] = replace(self.grants[0], actions=frozenset({'submit'}))
        self.assert_transport_error('ACCESS_DENIED', lambda: self.adapter.deliver('job-alpha'))
        self.assertEqual(self.port.contact_calls, [])
        self.adapter = self.new_adapter()
        self.adapter.deliver('job-alpha')
        self.assert_transport_error('USE_GOAL_DISPATCH', lambda: self.adapter.contact('alpha', 'req-alpha', 1))
        self.adapter.owners = None
        self.assert_transport_error('GOAL_CHANNEL_UNBOUND', lambda: self.adapter.dispatch_goal('alpha', 'req-alpha', 1))
        self.assertIsNone(self.core.inspect('alpha', 'req-alpha')['action_id'])

    def test_original_source_and_mailbox_cancellation_rechecked_at_final_native_boundary(self):
        for change in ('cancel', 'origin', 'grant', 'generation'):
            with self.subTest(change=change):
                # Every case uses an independent temporary fixture and original action.
                if change != 'cancel':
                    self.setUp()
                self.adapter.deliver('job-alpha')
                def mutate():
                    original = self.port.jobs['job-alpha']
                    if change == 'cancel':
                        self.port.jobs['job-alpha'] = replace(original, record={**original.record, 'cancel_requested': True})
                    elif change == 'origin':
                        self.port.jobs['job-alpha'] = replace(original, origin_ref='test:unrelated-new-origin')
                    elif change == 'grant':
                        self.adapter.grants[(self.a_peer, 'alpha')] = replace(self.grants[0], actions=frozenset({'status'}))
                    else:
                        self.port.owner_generations['alpha'] = 2
                self.port.before_dispatch = mutate
                result = self.adapter.dispatch_goal('alpha', 'req-alpha', 1)
                self.assertEqual(result['outcome'], 'NOT_SENT')
                self.assertEqual(self.port.native_effects, [])

    def test_busy_before_dispatch_preserves_the_one_attempt(self):
        self.adapter.deliver('job-alpha')
        available = self.port.prepare_goal
        def busy(_):
            raise TransportError('NATIVE_OWNER_BUSY')
        self.port.prepare_goal = busy
        self.assert_transport_error('NATIVE_OWNER_BUSY', lambda: self.adapter.dispatch_goal('alpha', 'req-alpha', 1))
        self.assertIsNone(self.core.inspect('alpha', 'req-alpha')['action_id'])
        self.port.prepare_goal = available
        self.assertEqual(self.adapter.dispatch_goal('alpha', 'req-alpha', 1)['outcome'], 'SUBMITTED')
        self.assertEqual(len(self.port.native_effects), 1)

    def test_unknown_native_outcome_recovers_same_action_without_a_second_dispatch(self):
        self.port.lose_contact_ack = True
        first = self.start()
        self.assertEqual(first['outcome'], 'UNKNOWN')
        self.core = Coordinator(self.state, clock=self.clock)
        self.adapter = self.new_adapter()
        replay = self.adapter.dispatch_goal('alpha', 'req-alpha', 1)
        self.assertEqual(replay['outcome'], 'RECONCILE_ORIGINAL')
        self.assertFalse(replay['native_attempted'])
        self.assertEqual(replay['action_id'], first['action_id'])
        self.assertEqual(len(self.port.native_effects), 1)
        self.assertEqual(len(self.port.contact_calls), 1)
        self.assertTrue(self.update(self.observation())['accepted'])

    def test_lost_outbox_ack_reuses_same_event_and_wire_hash(self):
        self.start()
        delivery = self.update(self.observation())['delivery']
        self.port.lose_enqueue_ack = True
        first = self.adapter.enqueue('alpha', 'req-alpha', 1)
        self.assertEqual(first['state'], 'UNKNOWN')
        replay = self.new_adapter().enqueue('alpha', 'req-alpha', 1)
        self.assertEqual(replay['state'], 'PENDING')
        self.assertEqual(replay['event_id'], delivery['event_id'])
        self.assertEqual(replay['result_hash'], first['result_hash'])
        self.assertEqual(self.port.enqueue_calls, 1)

    def test_later_mailbox_cancellation_preserves_native_observation_but_cannot_accept(self):
        self.start()
        handle = self.observation()
        original = self.port.jobs['job-alpha']
        self.port.jobs['job-alpha'] = replace(original, record={**original.record, 'cancel_requested': True})
        self.assertEqual(self.update(handle), {'accepted': False, 'late': True})
        status = self.core.inspect('alpha', 'req-alpha')
        self.assertTrue(status['cancel_requested'])
        self.assertFalse(status['accepted'])
        self.assertEqual(status['last_seq'], 0)
        self.assertIn(handle, self.port.owner_messages)

    def test_observation_source_revocation_or_unknown_does_not_advance_or_discard_report(self):
        self.start()
        handle = self.observation()
        original = self.port.jobs['job-alpha']
        self.port.jobs['job-alpha'] = replace(original, origin_ref='test:changed-source')
        with self.assertRaises(CoordinatorError) as raised:
            self.update(handle)
        self.assertEqual(raised.exception.code, 'ORIGIN_BINDING_CONFLICT')
        self.port.jobs['job-alpha'] = original
        self.adapter.grants[(self.a_peer, 'alpha')] = replace(self.grants[0], actions=frozenset({'status'}))
        self.assert_transport_error('ACCESS_DENIED', lambda: self.update(handle))
        self.adapter = self.new_adapter()
        fetch = self.port.verified_job
        def unavailable(job):
            raise TimeoutError('Synthetic source observation unknown')
        self.port.verified_job = unavailable
        with self.assertRaises(TimeoutError):
            self.update(handle)
        self.assertEqual(self.core.inspect('alpha', 'req-alpha')['last_seq'], 0)
        self.assertIn(handle, self.port.owner_messages)
        self.port.verified_job = fetch
        self.assertTrue(self.update(handle)['accepted'])

    def test_native_report_hash_sequence_and_listening_method_are_verified(self):
        self.start()
        handle = self.observation()
        original = self.port.owner_messages[handle]
        self.port.owner_messages[handle] = replace(original, report_hash='0' * 64)
        self.assert_transport_error('OWNER_REPLY_HASH_MISMATCH', lambda: self.update(handle))
        self.port.owner_messages[handle] = original
        first = self.update(handle)['delivery']
        self.assertEqual(self.update(handle)['delivery'], first)
        completed = self.observation(2, 'COMPLETED')
        observation = self.port.owner_messages[completed]
        invalid = deepcopy(observation.report)
        invalid['independent_review']['method'] = 'ASR'
        self.port.owner_messages[completed] = replace(observation, report=invalid, report_hash=canonical_hash(invalid))
        with self.assertRaises(CoordinatorError) as raised:
            self.update(completed)
        self.assertEqual(raised.exception.code, 'INDEPENDENT_REVIEW_REQUIRED')
        self.assertEqual(self.core.inspect('alpha', 'req-alpha')['goal_state'], 'ACCEPTED')


if __name__ == '__main__':
    unittest.main()
