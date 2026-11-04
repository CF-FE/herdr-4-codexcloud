"""The installed CLI preserves query grants and drains goal evidence without native availability."""

from datetime import datetime, timezone
from argparse import Namespace
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_coordinator_bridge as fixtures
import coordinator_bridge as bridge_runtime
import coordinator_goal_channel as native_runtime
from coordinator_transport import OwnerObservation, Peer, TransportError
from project_coordinator import GOAL_BUDGET, canonical_hash


class GoalBridgeTests(unittest.TestCase):
    setUp = fixtures.BridgeCommandTests.setUp
    project = fixtures.BridgeCommandTests.project
    run_cli = fixtures.BridgeCommandTests.run_cli

    def goal(self):
        self.project()
        self.core.manage('activate_owner', {'project_id': 'example', 'expected_generation': 0,
            'owner_principal': 'owner:existing', 'endpoint': 'native:existing',
            'appointment_ref': 'test:appointment', 'verification_ref': 'test:verified-owner',
            'handoff_ref': 'test:accepted', 'release_ref': 'test:no-old-writer'})
        payload = self.port.verified['record']['payload']
        scopes = ['test:fixed-batch-sha256']
        goal = {'objective': 'Coordinate the original review, without generating audio.',
                'acceptance_criteria': ['Named accountable reviewer and evidence'],
                'scope_refs': scopes, 'evidence_refs': ['test:manifest'], 'related_request_ids': [],
                'allowed_actions': ['coordinate_existing_owner', 'report_progress'],
                'review_requirement': 'none', 'next_check_at': '2098-01-01T00:00:00Z',
                'work_key': canonical_hash({'task_ref': payload['origin_task_id'], 'scope_refs': scopes})}
        payload['coordinator_request'].update(kind='DELEGATE_GOAL', goal=goal,
                                             expected_generation=1, budget=GOAL_BUDGET)
        digest = canonical_hash(payload)
        self.port.verified['payload_hash'] = digest
        self.port.verified['record']['payload_hash'] = digest
        self.config['pilot']['payload_hash'] = digest
        self.config['grants'][0]['actions'] = ['submit_goal']

    def test_goal_without_native_configuration_is_admitted_but_not_dispatched(self):
        self.goal()
        status = self.run_cli('pump')
        self.assertEqual(status['state'], 'PENDING')
        current = self.core.inspect('example', 'query-1')
        self.assertEqual(current['goal_state'], 'ADMITTED')
        self.assertIsNone(current['action_id'])
        saved = self.core.delivery('example', 'query-1')['result']
        self.assertEqual(saved['kind'], 'COORDINATOR_WAIT_REPORT')
        self.assertEqual(saved['waiting']['reason'], 'WAITING_NATIVE_GOAL_CHANNEL')
        self.assertEqual(self.run_cli('pump')['state'], 'WAITING_NATIVE_GOAL_CHANNEL')
        self.assertIsNone(self.core.inspect('example', 'query-1')['action_id'])
        self.assertEqual(self.core.inspect('example', 'query-1')['result_version'], 1)

    def test_legacy_enrollment_does_not_inherit_goal_permissions(self):
        self.goal()
        del self.config['grants'][0]['actions']
        self.run_cli('pump', error='ACCESS_DENIED')
        self.assertEqual(self.core.local_health('example')['open_followups'], 0)

    def accepted_goal_result(self):
        self.goal()
        self.run_cli('deliver', '--job-id', 'job-1')
        intent = self.core.claim_contact('example', 'query-1', 1)
        report = {'seq': 1, 'phase': 'ACCEPTED', 'summary': 'Explicitly accepted the original goal.',
                  'observed_at': datetime.now(timezone.utc).isoformat(), 'evidence_refs': ['test:native-acceptance'],
                  'next_owner': 'owner:existing', 'next_check_at': '2098-01-01T00:00:00Z'}
        return self.core.goal_update('owner:existing', 'example', 'query-1', intent['action_id'], 1,
                                     'native:existing', 'test:verified-native-item', report)['delivery']

    def test_pending_goal_result_drains_before_loading_an_unavailable_native_channel(self):
        result = self.accepted_goal_result()
        broken = self.root / 'unavailable-native-config.json'
        broken.write_text('not a native configuration')
        broken.chmod(0o600)
        self.config['goal_channel_config'] = str(broken)
        self.port.lose_ack = True
        self.config_path.write_bytes(fixtures.wire(self.config))
        self.config_path.chmod(0o600)
        run = subprocess.run([sys.executable, '-I', '-B', str(fixtures.ROOT / 'scripts/coordinator_bridge.py'),
                              '--config', str(self.config_path), 'pump'], capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(json.loads(run.stdout)['state'], 'UNKNOWN')
        self.assertEqual(self.run_cli('pump')['state'], 'PENDING')
        self.assertEqual(list(self.port.reports), [result['event_id']])
        self.assertEqual(self.port.calls.count('enqueue_result'), 1)
        self.assertEqual(self.core.pilot_outbox_version('example', 'query-1'), 2)

    def assert_saved_result_drains(self, native_path):
        result = self.accepted_goal_result()
        self.config['goal_channel_config'] = str(native_path)
        receipt = self.run_cli('pump')
        self.assertEqual(receipt['state'], 'PENDING')
        self.assertEqual(receipt['event_id'], result['event_id'])
        self.assertEqual(receipt['core_version'], result['version'])
        self.assertEqual(list(self.port.reports), [result['event_id']])
        self.assertEqual(self.port.calls.count('enqueue_result'), 1)
        current = self.core.inspect('example', 'query-1')
        self.assertEqual(current['goal_state'], 'ACCEPTED')
        self.assertEqual(current['result_version'], 1)  # No replacement wait result or native attempt.
        self.assertEqual(self.core.pilot_outbox_version('example', 'query-1'), 2)

    def test_saved_result_drains_after_native_configuration_is_deleted(self):
        missing = self.root / 'deleted-native-config.json'
        missing.write_text('{}')
        missing.chmod(0o600)
        missing.unlink()
        self.assert_saved_result_drains(missing)

    def test_saved_result_drains_without_early_native_permission_check(self):
        unsafe = self.root / 'unsafe-native-config.json'
        unsafe.write_text('{}')
        unsafe.chmod(0o644)
        self.assert_saved_result_drains(unsafe)

    def test_saved_result_drains_without_resolving_native_symlink(self):
        link = self.root / 'unavailable-native-link.json'
        link.symlink_to(self.root / 'absent-native-config.json')
        self.assert_saved_result_drains(link)

    def test_native_preflight_failure_is_reported_on_original_job_without_dispatch(self):
        self.goal()
        broken = self.root / 'invalid-native-config.json'
        broken.write_text('{}')
        broken.chmod(0o600)
        self.config['goal_channel_config'] = str(broken)
        self.assertEqual(self.run_cli('pump')['state'], 'PENDING')
        current = self.core.inspect('example', 'query-1')
        self.assertIsNone(current['action_id'])
        self.assertFalse(current['accepted'])
        self.assertEqual(self.core.delivery('example', 'query-1')['result']['kind'], 'COORDINATOR_WAIT_REPORT')
        self.assertEqual(self.run_cli('pump')['state'], 'GOAL_CHANNEL_BLOCKED')
        self.assertEqual(self.core.inspect('example', 'query-1')['result_version'], 1)

    def test_verified_acceptance_survives_later_rejected_native_completion(self):
        self.goal()
        self.run_cli('deliver', '--job-id', 'job-1')
        intent = self.core.claim_contact('example', 'query-1', 1)
        report = {'seq': 1, 'phase': 'ACCEPTED', 'summary': 'Accepted the existing goal.',
                  'observed_at': datetime.now(timezone.utc).isoformat(), 'evidence_refs': ['test:accepted'],
                  'next_owner': 'owner:existing', 'next_check_at': '2098-01-01T00:00:00Z'}
        peer, session, handle = Peer('fixture-native', 'existing-owner'), object(), object()
        observation = OwnerObservation(peer, 'native:existing', 'example', 'query-1', intent['action_id'],
            1, 'goal_update', 'test:original-native-acceptance', report, canonical_hash(report))
        def reconcile(context):
            self.assertEqual(context['action_id'], intent['action_id'])
            error = TransportError('NATIVE_LISTENING_CAPABILITY_UNAVAILABLE')
            error.validated_handles = [(session, handle)]
            error.evidence_ref = 'native-goal-report-sha256:' + 'd' * 64
            raise error
        channel = SimpleNamespace(peer=peer, reconcile=reconcile,
            bind_organization_parent=lambda path: self.assertEqual(path, self.core.path.parent),
            authenticated_peer=lambda value: peer if value is session else None,
            verified_observation=lambda value, ref: observation if value is session and ref is handle else None)
        candidate = self.root / 'fixed-channel.json'
        candidate.write_text('{}'); candidate.chmod(0o600)
        self.config['goal_channel_config'] = str(candidate)
        self.config_path.write_bytes(fixtures.wire(self.config)); self.config_path.chmod(0o600)
        with patch.object(native_runtime, 'load_goal_channel', return_value=channel):
            result = bridge_runtime.dispatch(Namespace(config=self.config_path, operation='pump'))
        self.assertEqual(result['state'], 'PENDING')
        self.assertEqual(result['core_version'], 1)
        status = self.core.inspect('example', 'query-1')
        self.assertTrue(status['accepted'])
        self.assertEqual(status['goal_state'], 'ACCEPTED')
        self.assertEqual(status['action_id'], intent['action_id'])
        self.assertEqual(status['result_version'], 2)
        self.assertEqual(self.core.delivery('example', 'query-1', version=1)['result']['kind'], 'GOAL_UPDATE')
        wait = self.core.delivery('example', 'query-1', version=2)['result']
        self.assertEqual(wait['kind'], 'COORDINATOR_WAIT_REPORT')
        self.assertEqual(wait['waiting']['reason'], 'NATIVE_LISTENING_CAPABILITY_UNAVAILABLE')
        self.assertIn('native-goal-report-sha256:' + 'd' * 64, json.dumps(wait))
        self.assertEqual(self.run_cli('pump')['core_version'], 2)
        self.assertEqual(self.port.calls.count('enqueue_result'), 2)


if __name__ == '__main__':
    unittest.main()
