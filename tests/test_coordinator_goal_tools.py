"""Organizational MCP tests: real stdio and native subprocesses, temporary state only."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'mcp/project-coordinator-tools/server.py'
spec = importlib.util.spec_from_file_location('goal_tools_server', SERVER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from project_coordinator import Coordinator, canonical, canonical_hash, GOAL_BUDGET
from coordinator_transport import OwnerObservation, Peer, CONTACT_KEYS
import test_coordinator_goal_channel as native_fixtures


class FakeChannel:
    def __init__(self, policy):
        self.policy = policy
        self.calls = 0
        self.before_send = None
        self.lookup = 'ABSENT'
        self.fail_after_send = False
        self.intent = None
        self.report = None
        self.peer = Peer('fake-native', 'reviewer')

    def lookup_goal(self, intent):
        return {'state': self.lookup, 'thread_id': 'fixture-thread', 'turn_id': 'fixture-original'}

    @contextmanager
    def prepare_goal(self, intent):
        yield object()

    def submit_goal(self, intent, *, verify_current, preparation=None):
        if self.before_send:
            self.before_send()
        verify_current()
        self.calls += 1
        self.intent = intent
        self.observed = datetime.now(timezone.utc).isoformat()
        if self.fail_after_send:
            raise TimeoutError('Synthetic lost receipt')
        return {**{k: intent[k] for k in CONTACT_KEYS}, 'kind': 'DELEGATE_GOAL', 'outcome': 'SUBMITTED'}

    def reconcile(self, intent):
        if self.intent is None:
            raise module.ToolError('NATIVE_REPORT_NOT_READY')
        self.session, self.handle = object(), object()
        return [(self.session, self.handle)]

    def authenticated_peer(self, session):
        return self.peer if session is self.session else None

    def verified_observation(self, session, handle):
        assert session is self.session and handle is self.handle
        report = self.report or {'seq': 1, 'phase': 'ACCEPTED', 'summary': 'Existing reviewer accepted this fixed scope.',
                  'observed_at': self.observed, 'evidence_refs': ['native:acceptance'],
                  'next_owner': self.intent['owner_principal'], 'next_check_at': '2099-01-01T00:00:00Z'}
        return OwnerObservation(self.peer, self.intent['endpoint'], self.intent['project_id'], self.intent['request_id'],
                                self.intent['action_id'], self.intent['generation'], 'goal_update',
                                'native:original-receipt', report, canonical_hash(report))


class GoalToolsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.root.chmod(0o700)
        self.state = self.root / 'tools'; self.state.mkdir(mode=0o700)
        self.core = Coordinator(self.root / 'core')
        self.core.manage('register_project', {'project_id': 'fixture', 'repository': 'ExampleOrg/demo',
            'project_ref': 'https://github.com/orgs/ExampleOrg/projects/1', 'cloud_dot_principal': 'cloud:fixture', 'report_route': 'original-job'})
        self.core.manage('activate_owner', {'project_id': 'fixture', 'expected_generation': 0,
            'owner_principal': 'owner:parent', 'endpoint': 'codex-cli:mac:00000000-0000-4000-8000-000000000002',
            'appointment_ref': 'fixture:parent', 'verification_ref': 'fixture:parent-route',
            'handoff_ref': 'fixture:parent-handoff', 'release_ref': 'fixture:release'})
        scopes = ['sha256:' + 'a' * 64, 'sha256:' + 'b' * 64]
        goal = {'objective': 'Coordinate existing independent listening.', 'scope_refs': scopes,
                'work_key': canonical_hash({'task_ref': 'https://github.com/ExampleOrg/demo/issues/1', 'scope_refs': scopes}),
                'acceptance_criteria': ['complete fixed review'], 'evidence_refs': ['fixture:input'], 'related_request_ids': [],
                'allowed_actions': ['coordinate_existing_owner', 'verify_existing_evidence', 'plan_independent_review', 'report_progress'],
                'next_check_at': '2099-01-01T00:00:00Z', 'review_requirement': 'independent_listening'}
        request = {'project_id': 'fixture', 'request_id': 'parent-request', 'job_id': 'parent-job', 'idempotency_key': 'parent-idem',
                   'payload_hash': 'c' * 64, 'task_ref': 'https://github.com/ExampleOrg/demo/issues/1', 'kind': 'DELEGATE_GOAL',
                   'expected_generation': 1, 'authorization_ref': 'fixture:parent-authority', 'deadline': '2099-01-02T00:00:00Z',
                   'evidence_refs': ['fixture:source'], 'budget': dict(GOAL_BUDGET), 'goal': goal}
        self.core.submit('cloud:fixture', request)
        self.core.claim_contact('fixture', 'parent-request', 1)
        context = self.core.goal_context('fixture', 'parent-request')
        binding = {k: context[k] for k in module.BINDING_KEYS - {'work_key', 'scope_refs', 'allowed_actions'}}
        binding.update({k: goal[k] for k in ('work_key', 'scope_refs', 'allowed_actions')})
        self.policy = {'v': 1, 'role': 'coordinator', 'core_state_dir': str(self.core.path.parent), 'state_dir': str(self.state),
                       'binding': binding, 'expires_at': request['deadline'], 'source_bridge': None,
                       'evidence': [], 'reviewer': None, 'review_contact_attempts': 0}
        self.policy_path = self.root / 'policy.json'
        self.add_evidence('fixture:index', {'rows': [{'scope_ref': x} for x in scopes]}, 'asset_index')
        self.save()

    def save(self):
        self.policy_path.write_bytes(canonical(self.policy)); self.policy_path.chmod(0o600)

    def add_evidence(self, ref_id, value, kind):
        path = self.root / (ref_id.replace(':', '-') + '.json')
        raw = canonical(value); path.write_bytes(raw)
        ref = {'ref_id': ref_id, 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
               'format': 'json', 'kind': kind, 'scope_refs': self.policy['binding']['scope_refs']}
        self.policy['evidence'].append(ref)
        return ref

    def tools(self, **kwargs):
        return module.GoalTools(self.policy_path, **kwargs)

    def reviewer_tool_policy(self):
        child = deepcopy(self.policy)
        state = self.root / 'reviewer-tools'; state.mkdir(mode=0o700, exist_ok=True)
        child.update(role='reviewer', reviewer=None, review_contact_attempts=0, state_dir=str(state))
        child['binding']['allowed_actions'] = module.CHILD_ACTIONS
        return child

    def bind_reviewer(self):
        b = self.policy['binding']
        reviewer = {'principal': 'reviewer:independent', 'endpoint': 'codex-cli:mac:00000000-0000-4000-8000-000000000001',
                    'producer_principal': 'producer:original', 'appointment_ref': 'fixture:appointment',
                    'independence_ref': 'fixture:independence', 'authorization_ref': 'fixture:one-review-contact',
                    'scope_refs': b['scope_refs']}
        self.add_evidence(reviewer['appointment_ref'], {'project_id': 'fixture', 'reviewer_principal': reviewer['principal'],
            'endpoint': reviewer['endpoint'], 'generation': 1, 'authorization_ref': reviewer['authorization_ref'], 'state': 'ACTIVE',
            'appointed_by': b['owner_principal'], 'scope_refs': b['scope_refs'], 'expires_at': b['deadline']}, 'appointment')
        self.add_evidence(reviewer['independence_ref'], {'project_id': 'fixture', 'reviewer_principal': reviewer['principal'],
            'producer_principal': reviewer['producer_principal'], 'independent': True, 'scope_refs': b['scope_refs']}, 'independence')
        self.child_policy_path = self.root / 'child-tools.json'
        self.child_policy_path.write_bytes(canonical(self.reviewer_tool_policy())); self.child_policy_path.chmod(0o600)
        self.process_path = self.root / 'process.json'
        self.process = {'mcp_servers': {'bounded': {'policy': str(self.child_policy_path), 'enabled_tools': ['goal_context']}}}
        self.process_path.write_bytes(canonical(self.process)); self.process_path.chmod(0o600)
        channel_policy = {'v': 2, 'process_config': str(self.process_path),
                          'process_config_sha256': hashlib.sha256(self.process_path.read_bytes()).hexdigest()}
        channel_file = self.root / 'channel.json'; channel_file.write_bytes(canonical(channel_policy)); channel_file.chmod(0o600)
        reviewer['channel_policy'] = {'path': str(channel_file), 'sha256': hashlib.sha256(channel_file.read_bytes()).hexdigest()}
        self.policy.update(reviewer=reviewer, review_contact_attempts=1,
                           source_bridge={'path': str(self.root / 'fixture-source.json'), 'sha256': 'd' * 64})
        self.save()
        self.channel = FakeChannel(channel_policy)
        return self.tools(channel_loader=lambda _: self.channel, source_gate=lambda *_: None)

    def test_real_stdio_initialize_list_read_and_default_blocked(self):
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'fixture', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'read_review_evidence', 'arguments': {'ref_id': 'fixture:index'}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'coordinate_independent_review', 'arguments': {}}},
        ]
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(SERVER), '--policy', str(self.policy_path)],
                                input=b''.join(canonical(x) + b'\n' for x in requests), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(x) for x in result.stdout.splitlines()]
        self.assertEqual([x['id'] for x in responses], [1, 2, 3, 4])
        self.assertEqual(len(responses[1]['result']['tools']), 4)
        self.assertEqual(responses[2]['result']['structuredContent']['trust'], 'HOST_ATTESTED')
        self.assertFalse(responses[2]['result']['structuredContent']['native_acceptance_verified'])
        self.assertEqual(responses[3]['result']['structuredContent']['state'], 'BLOCKED')
        self.assertFalse((self.state / 'review-contact.sqlite3').exists())

    def test_no_receiver_and_imported_accepted_json_never_mean_native_acceptance(self):
        tools = self.bind_reviewer()
        self.policy['reviewer'] = None; self.save()
        self.assertEqual(self.tools().coordinate_independent_review()['reason'], 'NO_AUTHORIZED_REVIEWER')
        self.assertEqual(self.channel.calls, 0)
        self.policy['reviewer'] = tools.policy['reviewer']
        self.add_evidence('fixture:old-arrangement', {'accepted': True, 'reviewer': 'invented'}, 'arrangement')
        self.save()
        result = self.tools(channel_loader=lambda _: self.channel, source_gate=lambda *_: None).coordinate_independent_review()
        self.assertEqual(result['reason'], 'EXISTING_ARRANGEMENT_REQUIRES_RECONCILIATION')
        self.assertFalse(result['existing']['native_acceptance_verified'])
        self.assertEqual(self.channel.calls, 0)

    def test_scope_and_evidence_arguments_cannot_expand_read_access(self):
        tools = self.tools()
        with self.assertRaises(module.ToolError):
            tools.call('read_review_evidence', {'ref_id': 'fixture:index', 'path': '/etc/passwd'})
        ref = self.policy['evidence'][0]
        Path(ref['path']).write_text('{}')
        with self.assertRaises(module.ToolError) as raised:
            tools.read_review_evidence('fixture:index')
        self.assertEqual(raised.exception.code, 'EVIDENCE_CHANGED')

    def test_known_source_credential_file_and_hardlink_cannot_be_exported_as_evidence(self):
        source = self.add_evidence('fixture:control', {'key_hex': 'fixture-control-value'}, 'receipt')
        Path(source['path']).chmod(0o600)
        self.policy['source_bridge'] = {'path': source['path'], 'sha256': source['sha256']}
        self.save()
        with self.assertRaises(module.ToolError) as raised:
            self.tools().read_review_evidence(source['ref_id'])
        self.assertEqual(raised.exception.code, 'CONTROL_FILE_NOT_BUSINESS_EVIDENCE')
        alias = self.root / 'business-looking-alias.json'
        alias.hardlink_to(source['path'])
        source['path'] = str(alias); self.save()
        with self.assertRaises(module.ToolError) as raised:
            self.tools().read_review_evidence(source['ref_id'])
        self.assertEqual(raised.exception.code, 'CONTROL_FILE_NOT_BUSINESS_EVIDENCE')

    def test_child_tools_have_no_recursive_coordination(self):
        self.policy['role'] = 'reviewer'
        self.policy['binding']['allowed_actions'] = module.CHILD_ACTIONS
        self.save()
        tools = self.tools()
        self.assertNotIn('coordinate_independent_review', [x['name'] for x in tools.tools()])
        with self.assertRaises(module.ToolError):
            tools.call('coordinate_independent_review', {})

    def test_one_contact_unknown_restart_reconciles_original_and_parent_owner_is_unchanged(self):
        tools = self.bind_reviewer()
        self.channel.fail_after_send = True
        self.assertEqual(tools.coordinate_independent_review()['state'], 'UNKNOWN')
        recovered = self.tools(channel_loader=lambda _: self.channel, source_gate=lambda *_: None)
        result = recovered.coordinate_independent_review()
        self.assertEqual(result['state'], 'REVIEWER_ACCEPTED')
        self.assertTrue(result['native_acceptance_verified'])
        self.assertEqual(self.channel.calls, 1)
        self.assertEqual(self.core.route('fixture')['owner_principal'], 'owner:parent')
        self.assertEqual(self.core.inspect('fixture', 'parent-request')['goal_state'], 'ADMITTED')
        self.assertEqual(recovered.coordinate_independent_review()['reports'], result['reports'])

    def test_final_send_rechecks_original_source_and_local_cancel(self):
        tools = self.bind_reviewer()
        self.channel.before_send = lambda: self.core.cancel('cloud:fixture', 'fixture', 'parent-request', 'fixture:cancel')
        result = tools.coordinate_independent_review()
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(self.channel.calls, 0)
        self.assertEqual(self.tools(channel_loader=lambda _: self.channel, source_gate=lambda *_: None).coordinate_independent_review()['state'], 'UNKNOWN')

    def test_cancellation_during_final_source_read_prevents_native_send(self):
        tools = self.bind_reviewer()
        calls = []
        def source(*_):
            calls.append(True)
            if len(calls) == 2:
                self.core.cancel('cloud:fixture', 'fixture', 'parent-request', 'fixture:cancel-during-source')
        tools.source_gate = source
        self.assertEqual(tools.coordinate_independent_review()['state'], 'UNKNOWN')
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.channel.calls, 0)

    def test_unknown_recovers_original_after_cancel_expiry_and_source_revocation(self):
        tools = self.bind_reviewer()
        self.channel.fail_after_send = True
        self.assertEqual(tools.coordinate_independent_review()['state'], 'UNKNOWN')
        self.core.cancel('cloud:fixture', 'fixture', 'parent-request', 'fixture:cancel-after-send')
        def forbidden_source(*_):
            self.fail('Historical readback must not require a fresh contact grant or remote source access')
        recovered = self.tools(channel_loader=lambda _: self.channel, source_gate=forbidden_source,
                               clock=lambda: datetime(2100, 1, 1, tzinfo=timezone.utc))
        result = recovered.coordinate_independent_review()
        self.assertEqual(result['state'], 'REVIEWER_ACCEPTED', result)
        self.assertTrue(result['historical_readback'])
        self.assertTrue(result['current_parent_status']['cancel_requested'])
        self.assertTrue(result['current_parent_status']['authority_expired'])
        self.assertEqual(self.channel.calls, 1)

    def test_reviewer_channel_cannot_bind_coordinator_tools_or_recursive_tool(self):
        self.bind_reviewer()
        child = json.loads(self.child_policy_path.read_text())
        child['role'] = 'coordinator'
        self.child_policy_path.write_bytes(canonical(child))
        result = self.tools(channel_loader=lambda _: self.channel, source_gate=lambda *_: None).coordinate_independent_review()
        self.assertEqual(result['reason'], 'REVIEW_TOOL_BOUNDARY_INVALID', result)
        self.assertEqual(self.channel.calls, 0)
        self.child_policy_path.write_bytes(canonical(self.reviewer_tool_policy()))
        self.process['mcp_servers']['bounded']['enabled_tools'].append('coordinate_independent_review')
        self.process_path.write_bytes(canonical(self.process))
        self.channel.policy['process_config_sha256'] = hashlib.sha256(self.process_path.read_bytes()).hexdigest()
        ref = self.policy['reviewer']['channel_policy']
        Path(ref['path']).write_bytes(canonical(self.channel.policy))
        ref['sha256'] = hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest(); self.save()
        result = self.tools(channel_loader=lambda _: self.channel, source_gate=lambda *_: None).coordinate_independent_review()
        self.assertEqual(result['reason'], 'RECURSIVE_DELEGATION_FORBIDDEN', result)
        self.assertEqual(self.channel.calls, 0)

    def test_listening_selfreport_cannot_complete_and_original_claim_remains_readable(self):
        tools = self.bind_reviewer()
        self.assertEqual(tools.coordinate_independent_review()['state'], 'REVIEWER_ACCEPTED')
        intent = self.channel.intent
        self.channel.report = {'seq': 2, 'phase': 'COMPLETED', 'summary': 'Claimed listening complete.',
            'observed_at': datetime.now(timezone.utc).isoformat(), 'evidence_refs': ['native:claimed-review'],
            'next_owner': intent['owner_principal'], 'terminal_condition': 'claimed fixed review complete',
            'criteria_evidence': [{'criterion': x, 'evidence_refs': ['native:claimed-review']}
                                  for x in intent['goal']['acceptance_criteria']],
            'independent_review': {'state': 'VERIFIED', 'reviewer': self.policy['reviewer']['principal'],
                'producer': self.policy['reviewer']['producer_principal'], 'scope_refs': intent['goal']['scope_refs'],
                'evidence_refs': ['native:claimed-review'], 'method': 'human_listening'}}
        result = tools.coordinate_independent_review()
        self.assertEqual(result['state'], 'BLOCKED', result)
        self.assertEqual(result['reason'], 'INDEPENDENT_LISTENING_EXECUTION_UNAVAILABLE')
        original = tools.read_coordination_report()
        self.assertEqual(original['state'], 'REVIEWER_ACCEPTED')
        self.assertEqual(original['unadopted_reports'][0]['report'], self.channel.report)
        self.assertEqual(len(original['reports']), 1)
        self.assertEqual(self.channel.calls, 1)
        self.assertEqual(self.core.inspect('fixture', 'parent-request')['goal_state'], 'ADMITTED')

    def test_source_unknown_before_reservation_and_present_native_history_never_send(self):
        tools = self.bind_reviewer()
        def unavailable(*_):
            raise module.ToolError('SOURCE_UNKNOWN')
        tools.source_gate = unavailable
        self.assertEqual(tools.coordinate_independent_review()['reason'], 'SOURCE_UNKNOWN')
        self.assertEqual(self.channel.calls, 0)
        tools.source_gate = lambda *_: None
        self.channel.lookup = 'PRESENT'
        self.assertEqual(tools.coordinate_independent_review()['reason'], 'EXISTING_NATIVE_ACTION_REQUIRES_RECONCILIATION')
        self.channel.lookup = 'ABSENT'
        self.assertEqual(tools.coordinate_independent_review()['reason'], 'EXISTING_NATIVE_ACTION_REQUIRES_RECONCILIATION')
        self.assertEqual(self.channel.calls, 0)

    def test_v1_native_channel_cannot_bypass_reviewer_admission(self):
        self.bind_reviewer()
        ref = self.policy['reviewer']['channel_policy']
        Path(ref['path']).write_bytes(canonical({'v': 1}))
        ref['sha256'] = hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()
        self.save()
        result = self.tools(source_gate=lambda *_: None).coordinate_independent_review()
        self.assertEqual(result['reason'], 'REVIEW_CHANNEL_V2_REQUIRED')

    def actual_native_fixture(self, *, completion=False):
        tools = self.bind_reviewer()
        fixture = native_fixtures.NativeGoalChannelTests('test_real_subprocess_resume_and_independent_native_proof')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        # Use the real native protocol fixture with a current observation, not an old summary.
        code = fixture.binary.read_text().replace("'observed_at':'2026-11-04T01:00:00Z'",
            "'observed_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()")
        if completion:
            code = code.replace("  turn={'id':'native-turn-1'",
                "  answer['updates'].append({'seq':2,'phase':'COMPLETED'})\n  turn={'id':'native-turn-1'")
        fixture.binary.write_text(code); fixture.binary.chmod(0o700)
        child = tools._child(self.core.goal_context('fixture', 'parent-request'), self.policy['reviewer'])
        fixture.intent = child
        for value in (fixture.policy, fixture.cap):
            value.update({k: child[k] for k in ('project_id', 'request_id', 'job_id', 'payload_hash', 'owner_principal', 'generation')})
            value.update(binary_sha256=hashlib.sha256(fixture.binary.read_bytes()).hexdigest(),
                         work_key=child['goal']['work_key'], scope_refs=child['goal']['scope_refs'], allowed_actions=module.CHILD_ACTIONS)
        fixture.policy.update(endpoint=child['endpoint'], authorization_ref=child['authorization_ref'])
        fixture.use_process_policy()
        fixture_process = json.loads(Path(fixture.policy['process_config']).read_text())
        tool_policy_path = Path(fixture_process['mcp_servers']['bounded']['policy'])
        tool_policy_path.write_bytes(canonical(self.reviewer_tool_policy()))
        for artifact in fixture.cap['config_files']:
            if artifact['path'] == str(tool_policy_path):
                artifact['sha256'] = hashlib.sha256(tool_policy_path.read_bytes()).hexdigest()
        fixture.cap['action_tools'] = {a: ['bounded/goal_context'] for a in module.CHILD_ACTIONS}
        fixture.refresh_capability()
        ref = self.policy['reviewer']['channel_policy']
        Path(ref['path']).write_bytes(canonical(fixture.policy))
        ref['sha256'] = hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()
        self.save()
        loader = lambda path: fixture.process_channel(json.loads(Path(path).read_text()))
        tools = self.tools(channel_loader=loader, source_gate=lambda *_: None)
        return tools, fixture, loader

    def test_actual_codex_goal_channel_with_fake_native_process_accepts_and_reads_same_action(self):
        tools, fixture, loader = self.actual_native_fixture()
        result = tools.coordinate_independent_review()
        self.assertEqual(result['state'], 'REVIEWER_ACCEPTED', result)
        self.assertTrue(result['native_acceptance_verified'])
        self.assertEqual(len(fixture.calls()), 1)
        again = self.tools(channel_loader=loader, source_gate=lambda *_: None).coordinate_independent_review()
        self.assertEqual(again['reports'], result['reports'])
        self.assertEqual(len(fixture.calls()), 1)

    def test_actual_native_completion_block_preserves_verified_acceptance_and_proof(self):
        tools, fixture, loader = self.actual_native_fixture(completion=True)
        result = tools.coordinate_independent_review()
        self.assertEqual(result['state'], 'BLOCKED', result)
        self.assertEqual(result['reason'], 'INDEPENDENT_LISTENING_EXECUTION_UNAVAILABLE')
        original = tools.read_coordination_report()
        self.assertTrue(original['native_acceptance_verified'])
        self.assertEqual(original['state'], 'REVIEWER_ACCEPTED')
        self.assertEqual([x['report']['phase'] for x in original['reports']], ['ACCEPTED'])
        block = original['readback_blocks'][0]
        self.assertEqual(block['reason'], 'NATIVE_LISTENING_CAPABILITY_UNAVAILABLE')
        self.assertTrue(block['evidence_ref'].startswith('native-goal-report-sha256:'))
        self.assertTrue(list((fixture.state / original['child_action_id']).glob('native-item-*.json')))
        self.assertEqual(len(fixture.calls()), 1)
        again = self.tools(channel_loader=loader, source_gate=lambda *_: None).coordinate_independent_review()
        self.assertEqual(again['original']['readback_blocks'], original['readback_blocks'])
        self.assertEqual(len(fixture.calls()), 1)


if __name__ == '__main__':
    unittest.main()
