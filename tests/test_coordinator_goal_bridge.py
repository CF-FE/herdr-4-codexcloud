"""The installed CLI preserves query grants and drains goal evidence without native availability."""

from datetime import datetime, timezone
import json
import subprocess
import sys
import unittest

import test_coordinator_bridge as fixtures
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


if __name__ == '__main__':
    unittest.main()
