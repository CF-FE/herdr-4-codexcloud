"""Sanitized 0.9.5 native shape; no real Herdr command or model turn.

The private pilot observed omitted launch_pending for a ready agent. The pinned
official AgentInfo serde contract defaults that boolean to false and omits false:
herdrdev/herdr@ee5bfb5041bb3e4a49d591c72308aae3c81ecdde,
src/api/schema/agents.rs:238-241. These targets are synthetic fixture values.
"""

import unittest

import test_herdr_executor_contract as contracts


class NativeSchemaFixture(contracts.NativeFixture):
    def agent(self):
        return {
            "agent": "codex", "agent_status": "idle", "cwd": self.cwd,
            "focused": False, "foreground_cwd": self.cwd, "interactive_ready": True,
            "name": self.agent_name, "pane_id": self.pane_id, "revision": 7,
            "state_change_seq": 2, "tab_id": "fixture-tab",
            "terminal_id": "fixture-terminal", "terminal_title": "Codex fixture",
            "terminal_title_stripped": "Codex fixture", "workspace_id": "fixture-workspace",
        }


class NativeSchemaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = contracts.ExecutorContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def native(self, *, pending="omitted"):
        native = NativeSchemaFixture(self.fixture.workspace)
        original = native.agent
        if pending != "omitted":
            def agent():
                return {**original(), "launch_pending": pending}
            native.agent = agent
        return native

    def dispatch(self, native):
        fixture = self.fixture
        envelope = fixture.envelope()
        return fixture.dispatch(fixture.executor(native=native), envelope)

    def test_ready_native_shape_omits_false_launch_pending(self):
        native = self.native()
        result = self.dispatch(native)
        self.assertEqual(result["state"], "SUBMITTED")
        self.assertIs(result["native"]["agent"]["launch_pending"], False)
        self.assertEqual(len(native.prompts), 1)

    def test_explicit_false_launch_pending_remains_ready(self):
        native = self.native(pending=False)
        self.assertEqual(self.dispatch(native)["state"], "SUBMITTED")
        self.assertEqual(len(native.prompts), 1)

    def test_true_launch_pending_is_not_ready(self):
        native = self.native(pending=True)
        result = self.dispatch(native)
        self.assertEqual(result["state"], "FAILED_NOT_DISPATCHED")
        self.assertEqual(result["detail"], "NATIVE_AGENT_NOT_READY")
        self.assertEqual(native.prompts, [])

    def test_present_nonboolean_launch_pending_is_a_protocol_error(self):
        for index, pending in enumerate((None, 0, 1, "false", [], {})):
            with self.subTest(pending=pending):
                self.fixture.state_dir = self.fixture.directory / ("invalid-schema-" + str(index))
                native = self.native(pending=pending)
                result = self.dispatch(native)
                self.assertEqual(result["state"], "FAILED_NOT_DISPATCHED")
                self.assertEqual(result["detail"], "NATIVE_PROTOCOL_ERROR")
                self.assertEqual(native.prompts, [])


if __name__ == "__main__":
    unittest.main()
