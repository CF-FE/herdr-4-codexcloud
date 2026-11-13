"""The three role entry points share one native start and preserve the original intent."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_herdr_model_selection import NativeDouble
from allowlists import DEFAULT_FILE
from herdr_agent_creation import mac_agent_process, mac_launch_argv, mac_shell_process, non_model_args
from herdr_role_creation import operate
import herdr_win_creation as win
from project_coordinator import Coordinator


class RoleCreationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.role_directory = self.root / "handoffs/herdr-project-coordinator"
        self.role_directory.mkdir(mode=0o700, parents=True)
        self.binding_path = self.root / "binding.json"
        self.target = {"session": "herdr-test", "agent": "new-agent", "cwd": str(self.root),
                       "workspace_id": "workspace", "tab_id": "tab", "pane_id": "pane", "terminal_id": "terminal"}
        self.native = NativeDouble(self.target)
        self.binding = {
            "schema_version": 1, "role": "executor", "project_id": "alpha", "task_ref": "issue:42",
            "unit_id": "unit-1", "creation_id": "attempt-1", "authorization_ref": "authorization:42",
            "dispatch_ref": "dispatch:42", "input_sha256": "a" * 64,
            "target": self.target,
            "route": {"environment_id": "mac-work", "worker_id": "mac-codex",
                      "backend_id": "current-default", "provider": "openai"},
            "agent_args": ["--no-daemon", "-a", "on-request", "-s", "workspace-write"],
            "selection": {"requirements": {}, "capabilities": [
                {"worker_id": "mac-codex", "backend_id": "current-default", "provider": "openai",
                 "model_type": "reasoning", "model": "gpt-6.1-sol", "supported_efforts": ["low", "high"],
                 "tools": [], "modalities": ["text"], "context_window": 2048, "evidence_ref": "fixture:reviewed"},
            ]},
        }

    def write_binding(self):
        self.binding_path.write_text(json.dumps(self.binding), encoding="utf-8")
        self.binding_path.chmod(0o600)

    def fake_adapter(self, binding):
        def render(selected):
            return mac_launch_argv(non_model_args(binding["agent_args"], self.target["cwd"], "openai"), selected)

        return ("codex", lambda *command: self.native(self.target["session"], *command),
                mac_shell_process, mac_agent_process, None, render)

    def call(self, action, **options):
        return operate(action, self.binding_path, root=self.root, get_adapter=self.fake_adapter, **options)

    def start(self, **options):
        prepared = self.call("prepare", **options)
        self.assertEqual(prepared["launch_outcome"], "not_started")
        return self.call("start", prepared_sha=prepared["prepared_sha256"], **options)

    def owner(self):
        self.binding["role"] = "project_owner"
        del self.binding["dispatch_ref"], self.binding["input_sha256"]
        self.binding["owner_creation"] = {
            "expected_generation": 0, "vacancy_ref": "vacancy:alpha", "release_ref": "release:alpha",
        }
        coordinator = Coordinator(self.root / "state")
        coordinator.manage("register_project", {"project_id": "alpha", "repository": "test/alpha",
                                                "project_ref": "project:alpha", "cloud_dot_principal": "dot",
                                                "report_route": "route:alpha"})
        return coordinator

    def bind_win(self):
        self.target.update(machine="a" * 32, session="collab", cwd=r"D:\work\project",
                           session_dir=r"C:\user\.omp\agent\sessions\project")
        self.binding["route"] = win.ROUTE
        self.binding["agent_args"] = []
        self.binding["selection"]["capabilities"][0].update(win.ROUTE, supported_efforts=["high"])
        self.write_binding()

    def test_win_worker_uses_process_cwd_when_native_foreground_cwd_is_omitted_or_null(self):
        self.bind_win()
        wrong_cwd = {"shell": True, "agent": True}
        session_id = "019a0c0e-0000-7000-8000-000000000001"

        def win_native(target, *command):
            self.assertEqual(target, self.target)
            result = self.native(target["session"], *command)
            if command[:2] == ("pane", "list"):
                del result["panes"][0]["foreground_cwd"]
            elif command[:2] == ("pane", "process-info"):
                foreground = result["process_info"]["foreground_processes"][0]
                foreground["argv"][0] = r"C:\bin\omp.exe" if self.native.ready else r"C:\Windows\System32\cmd.exe"
                if wrong_cwd["agent" if self.native.ready else "shell"]:
                    foreground["cwd"] = r"D:\different"
            elif command[:2] == ("agent", "get"):
                result["agent"].update(agent="omp", foreground_cwd=None, agent_session={
                    "agent": "omp", "source": "herdr:omp", "kind": "id", "value": session_id,
                })
            return result

        with patch.object(win, "native", side_effect=win_native):
            plan = operate("prepare", self.binding_path, root=self.root)
            with self.assertRaisesRegex(ValueError, "interactive shell is unconfirmed"):
                operate("start", self.binding_path, root=self.root, prepared_sha=plan["prepared_sha256"])
            self.assertFalse(any(command[:2] == ("agent", "start") for command in self.native.calls))

            wrong_cwd["shell"] = False
            result = operate("start", self.binding_path, root=self.root, prepared_sha=plan["prepared_sha256"])
            self.assertEqual(result["launch_outcome"], "ready_acknowledged")
            self.assertEqual(result["candidate_state"], "unconfirmed")
            self.assertIsNone(result["thread_id"])

            wrong_cwd["agent"] = False
            recovered = operate("reconcile", self.binding_path, root=self.root)
            self.assertEqual(recovered["candidate_state"], "created")
            self.assertEqual(recovered["thread_id"], session_id)
            self.assertEqual(sum(command[:2] == ("agent", "start") for command in self.native.calls), 1)

    def test_win_owner_route_cannot_prepare_or_start(self):
        coordinator = self.owner()
        self.bind_win()
        with patch.object(win, "native") as native:
            for operation in ("prepare", "start"):
                with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "lifecycles are Mac-only"):
                    operate(operation, self.binding_path, root=self.root, prepared_sha="0" * 64)
            native.assert_not_called()
        self.assertIsNone(coordinator.manage("creation_status", {
            "project_id": "alpha", "creation_id": "attempt-1",
        })["creation"])

    def test_mac_creation_still_requires_native_foreground_cwd(self):
        self.write_binding()
        original = self.native

        def incomplete_mac_target(session, *command):
            result = original(session, *command)
            if command[:2] == ("pane", "list"):
                result["panes"][0]["foreground_cwd"] = None
            return result

        self.native = incomplete_mac_target
        with self.assertRaisesRegex(ValueError, "authorized pane is unconfirmed"):
            self.start()
        self.assertFalse(any(command[:2] == ("agent", "start") for command in original.calls))

    def test_worker_starts_once_with_explicit_option_and_frozen_policy(self):
        self.binding["selection"]["requirements"]["efforts"] = ["low"]
        self.write_binding()
        result = self.start()
        self.assertEqual(result["candidate_state"], "created")
        self.assertEqual(result["thread_id"], self.native.thread_id)
        self.assertIsNone(result["effective_model"])
        self.assertEqual(result["launch_model"]["reasoning_effort"], "low")
        self.assertIn('model_reasoning_effort="low"', self.native.argv)
        self.assertEqual(self.call("start", prepared_sha=result["prepared_sha256"]), result)
        self.assertEqual(sum(command[:2] == ("agent", "start") for command in self.native.calls), 1)
        self.binding["creation_id"] = "attempt-2"
        self.write_binding()
        with self.assertRaisesRegex(ValueError, "original task unit has a launch"):
            self.call("prepare")

    def test_astra_request_or_codex_override_cannot_reach_native_start(self):
        cases = (
            (["gpt-6.1-sol", "openai/GPT-6-ASTRA"], []),
            (None, ["-m", "openai/gpt-6.1-astra:latest"]),
            (None, ["-mgpt-6.1-astra"]),
            (None, ["--model=local-gpt:astra"]),
            (None, ["--model=gpt-4-proxy:astra"]),
            (None, ["-c", 'model="openai/gpt-6-astra"']),
            (None, ["--config=profiles.example.model='astra-high'"]),
        )
        for models, args in cases:
            with self.subTest(models=models, args=args):
                self.binding["selection"]["requirements"] = {} if models is None else {"models": models}
                self.binding["agent_args"] = ["--no-daemon", *args]
                self.write_binding()
                with self.assertRaisesRegex(ValueError, "Astra"):
                    self.call("prepare")
                self.assertEqual(self.native.calls, [])

    def test_win_rejects_explicit_astra_during_shared_selection(self):
        self.bind_win()
        self.binding["selection"]["requirements"] = {"models": ["cli-proxy-responses/Astra"]}
        self.write_binding()
        with patch.object(win, "native") as native:
            with self.assertRaisesRegex(ValueError, "Astra"):
                operate("prepare", self.binding_path, root=self.root)
            native.assert_not_called()

    def test_launch_defaults_to_sol_and_keeps_explicit_other_allowed_model(self):
        first = self.binding["selection"]["capabilities"][0]
        self.binding["selection"]["capabilities"].append({**first, "model": "gpt-5.6-sol"})
        self.write_binding()
        plan = self.call("prepare")
        self.assertEqual(plan["launch_model"]["model"], "gpt-6.1-sol")
        self.binding["selection"]["requirements"]["models"] = ["gpt-5.6-sol"]
        self.write_binding()
        self.assertEqual(self.start()["launch_model"]["model"], "gpt-5.6-sol")
        self.assertEqual(self.native.argv[self.native.argv.index("-m") + 1], "gpt-5.6-sol")

    def test_coordinator_first_start_blocks_existing_binding_even_if_invalid(self):
        self.binding["role"] = "project_coordinator"
        del self.binding["dispatch_ref"], self.binding["input_sha256"]
        self.binding["initialization_ref"] = "host-admin:initialization"
        self.write_binding()
        registration = self.role_directory / "binding.json"
        registration.write_text("unresolved", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "coordinator binding present"):
            self.call("prepare")
        self.assertEqual(self.native.calls, [])
        registration.unlink()
        result = self.start()
        self.assertEqual(result["launch_model"]["reasoning_effort"], "high")
        self.assertEqual(result["candidate_state"], "created")
        self.assertFalse(registration.exists())

    def test_owner_uses_existing_ledger_and_reconciles_lost_start_as_candidate(self):
        coordinator = self.owner()
        self.write_binding()
        key = {"project_id": "alpha", "creation_id": "attempt-1"}
        self.assertIsNone(coordinator.manage("creation_status", key)["creation"])
        self.native.start_lost = True
        result = self.start()
        self.assertEqual(result["launch_outcome"], "unknown")
        self.assertIsNone(result["thread_id"])
        row = coordinator.manage("creation_status", key)["creation"]
        self.assertEqual(row["state"], "UNKNOWN")
        events = coordinator.manage("events", {"project_id": "alpha"})["events"]
        original = next(event for event in events if event["kind"] == "CREATION_INTENT")
        self.assertEqual(original["body"]["binding_sha256"], hashlib.sha256(self.binding_path.read_bytes()).hexdigest())

        self.native.ready = True
        recovered = self.call("reconcile", policy_path=self.root / "no-policy")
        self.assertEqual(recovered["candidate_state"], "created")
        self.assertEqual(recovered["launch_outcome"], "unknown")
        view = coordinator.manage("creation_status", key)
        self.assertEqual(view["creation"]["state"], "CREATED")
        self.assertEqual(view["creation"]["candidate_ref"], recovered["candidate_ref"])
        self.assertEqual(view["current_generation"], 0)
        self.assertIsNone(coordinator.route("alpha")["owner_principal"])
        self.assertEqual(sum(command[:2] == ("agent", "start") for command in self.native.calls), 1)

    def test_policy_change_at_native_preflight_records_owner_not_created(self):
        coordinator = self.owner()
        self.write_binding()
        path = self.root / "policy.json"
        content = DEFAULT_FILE.read_bytes()
        path.write_bytes(content)
        original = self.native

        def changing_host(session, *command):
            if command[:2] == ("pane", "process-info"):
                path.write_bytes(content + b"\n")
            return original(session, *command)

        self.native = changing_host
        result = self.start(policy_path=path)
        self.assertEqual(result["reason"], "agent_model_policy_changed")
        self.assertEqual(result["launch_outcome"], "not_started")
        self.assertFalse(any(command[:2] == ("agent", "start") for command in original.calls))
        row = coordinator.manage("creation_status", {"project_id": "alpha", "creation_id": "attempt-1"})["creation"]
        self.assertEqual(row["state"], "NOT_CREATED")
        self.native = original
        path.write_bytes(content)
        self.binding["creation_id"] = "attempt-2"
        self.write_binding()
        recovered = self.start(policy_path=path)
        self.assertEqual(recovered["candidate_state"], "created")
        self.assertTrue(Path(result["receipt_path"]).exists())
        self.assertNotEqual(result["receipt_path"], recovered["receipt_path"])
        next_row = coordinator.manage("creation_status", {"project_id": "alpha", "creation_id": "attempt-2"})
        self.assertEqual(next_row["creation"]["state"], "CREATED")
        self.assertEqual(sum(command[:2] == ("agent", "start") for command in original.calls), 1)

    def test_owner_intent_recovered_elsewhere_before_start_blocks_native_effect(self):
        coordinator = self.owner()
        self.write_binding()

        def recover_owner_intent(*args):
            coordinator.manage("recover", {"evidence_ref": "fixture:unexpected recovery"})
            return True

        with patch("herdr_role_creation.check_policy_at_start", side_effect=recover_owner_intent):
            result = self.start()
        self.assertEqual(result["reason"], "owner_creation_changed")
        self.assertEqual(result["launch_outcome"], "not_started")
        self.assertFalse(any(command[:2] == ("agent", "start") for command in self.native.calls))

    def test_owner_interruption_before_ledger_intent_can_reconcile_and_start_new_attempt(self):
        coordinator = self.owner()
        self.write_binding()
        manage = Coordinator.manage

        def interrupt_before_ledger(instance, operation, data):
            if operation == "begin_creation":
                raise RuntimeError("interrupted before owner intent")
            return manage(instance, operation, data)

        with patch.object(Coordinator, "manage", interrupt_before_ledger):
            with self.assertRaisesRegex(RuntimeError, "interrupted before owner intent"):
                self.start()
        receipt_path, = self.role_directory.glob("native-creation-*.json")
        self.assertEqual(json.loads(receipt_path.read_bytes())["launch_outcome"], "unknown")
        self.assertIsNone(coordinator.manage("creation_status", {
            "project_id": "alpha", "creation_id": "attempt-1",
        })["creation"])
        self.assertFalse(any(command[:2] == ("agent", "start") for command in self.native.calls))

        reconciled = self.call("reconcile")
        self.assertEqual(reconciled["reason"], "owner_creation_not_registered")
        self.assertEqual(reconciled["launch_outcome"], "not_started")
        self.assertEqual(json.loads(receipt_path.read_bytes()), reconciled)
        self.assertEqual(self.call("prepare"), reconciled)

        self.binding["creation_id"] = "attempt-2"
        self.write_binding()
        next_attempt = self.start()
        self.assertEqual(next_attempt["candidate_state"], "created")
        self.assertEqual(json.loads(receipt_path.read_bytes()), reconciled)
        self.assertEqual(sum(command[:2] == ("agent", "start") for command in self.native.calls), 1)

    def test_worker_zero_effect_retries_new_attempt_without_losing_original(self):
        self.write_binding()
        original_binding = self.binding_path.read_bytes()
        path = self.root / "policy.json"
        content = DEFAULT_FILE.read_bytes()
        path.write_bytes(content)
        original = self.native

        def changing_host(session, *command):
            if command[:2] == ("pane", "process-info"):
                path.write_bytes(content + b"\n")
            return original(session, *command)

        self.native = changing_host
        first = self.start(policy_path=path)
        self.assertEqual(first["launch_outcome"], "not_started")
        self.native = original
        path.write_bytes(content)
        self.binding["creation_id"] = "attempt-2"
        self.write_binding()
        second = self.start(policy_path=path)
        self.assertEqual(second["candidate_state"], "created")
        self.assertNotEqual(first["receipt_path"], second["receipt_path"])
        self.binding_path.write_bytes(original_binding)
        self.assertEqual(self.call("reconcile", policy_path=path), first)
        self.assertEqual(sum(command[:2] == ("agent", "start") for command in original.calls), 1)

    def test_unresolved_native_target_cannot_be_reused_under_another_unit(self):
        self.write_binding()
        self.native.start_lost = True
        self.assertEqual(self.start()["launch_outcome"], "unknown")
        self.binding["unit_id"] = "unit-2"
        self.binding["creation_id"] = "attempt-2"
        self.write_binding()
        prepared = self.call("prepare")
        with self.assertRaisesRegex(ValueError, "target has an unresolved launch"):
            self.call("start", prepared_sha=prepared["prepared_sha256"])
        self.assertEqual(sum(command[:2] == ("agent", "start") for command in self.native.calls), 1)


if __name__ == "__main__":
    unittest.main()
