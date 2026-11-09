import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from allowlists import DEFAULT_FILE, DEFAULT_ROOT, read_active_agent_policy, resolve_agent_default
import herdr_win_creation as win


MACHINE = "a" * 32
TARGET = {"machine": MACHINE, "session": "collab", "cwd": r"D:\work\project",
          "session_dir": r"C:\user\.omp\agent\sessions\project"}
PROFILE = {"id": MACHINE, "label": "win-work", "session": "collab", "enabled": True}
DRIVER = {"HERDR_ENV": "1", "HERDR_SESSION": "project-coordinator", "HERDR_PANE_ID": "w2:p1",
          "DECISION_SERVICE_TOKEN": "fixture-only"}
SELECTED = {"provider": "cli-proxy-responses", "model_type": "reasoning",
            "model": "gpt-6.1-sol", "reasoning_effort": "high"}


def response(value):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(value).encode())


class WinCreationTests(unittest.TestCase):
    def test_policy_limits_win_to_two_exact_high_combinations(self):
        policy, _, _ = read_active_agent_policy(DEFAULT_FILE, DEFAULT_ROOT)
        entries = [entry for entry in policy["entries"] if entry["environment_id"] == "win-work"]
        self.assertEqual({(entry["model"], entry["reasoning_effort"]) for entry in entries},
                         {("gpt-6.1-sol", "high"), ("gpt-5.6-sol", "high")})
        self.assertTrue(all(all(entry[key] == value for key, value in win.ROUTE.items()) for entry in entries))
        default = resolve_agent_default(policy, "win-work")
        self.assertEqual((default["model"], default["reasoning_effort"]), ("gpt-6.1-sol", "high"))

    def test_launch_renders_exact_provider_model_and_effort(self):
        binding = {"route": win.ROUTE, "agent_args": [], "target": TARGET}
        self.assertEqual(win.launch_argv(binding, SELECTED),
                         ["--model", "cli-proxy-responses/gpt-6.1-sol", "--thinking", "high",
                          "--session-dir", TARGET["session_dir"]])
        for changes in ({"provider": "cli-proxy-chat"}, {"reasoning_effort": "ultra"},
                        {"model": "another/provider"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                win.launch_argv(binding, {**SELECTED, **changes})
        with self.assertRaises(ValueError):
            win.launch_argv({**binding, "agent_args": ["--config", "override.yml"]}, SELECTED)
        with self.assertRaises(ValueError):
            win.launch_argv({**binding, "target": {**TARGET, "session_dir": "relative"}}, SELECTED)

    def test_native_checks_saved_route_and_never_uses_remote_session_as_local(self):
        with patch.dict(win.os.environ, DRIVER, clear=True), patch.object(win.platform, "system", return_value="Darwin"), \
                patch.object(win.subprocess, "run", side_effect=[response([PROFILE]), response({"result": {"agents": []}})]) as run:
            self.assertEqual(win.native(TARGET, "agent", "list"), {"agents": []})
        calls = run.call_args_list
        self.assertEqual([call.args[0][1:] for call in calls],
                         [["machine", "list", "--json"], ["--machine", MACHINE, "agent", "list"]])
        self.assertNotIn("DECISION_SERVICE_TOKEN", calls[-1].kwargs["env"])

    def test_unbound_or_replaced_route_sends_no_remote_command(self):
        with patch.dict(win.os.environ, {}, clear=True), patch.object(win.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "driver pane"):
                win.native(TARGET, "agent", "list")
            run.assert_not_called()
        for changes in ({"session": "different"}, {"label": "different"}, {"enabled": False}):
            with self.subTest(changes=changes), patch.dict(win.os.environ, DRIVER, clear=True), \
                    patch.object(win.platform, "system", return_value="Darwin"), \
                    patch.object(win.subprocess, "run", return_value=response([{**PROFILE, **changes}])) as run:
                with self.assertRaisesRegex(ValueError, "binding changed"):
                    win.native(TARGET, "agent", "list")
                self.assertEqual(run.call_count, 1)

    def test_native_session_path_resolves_only_to_the_omp_session_id(self):
        session_id = "019a0c0e-0000-7000-8000-000000000001"
        source = {"agent": "omp", "source": "herdr:omp"}
        reference = {**source, "kind": "path",
                     "value": rf"C:\user\sessions\2026-11-09T10-23-32-000Z_{session_id}.jsonl"}
        self.assertEqual(win.session_identity(reference), session_id)
        self.assertEqual(win.session_identity({**source, "kind": "id", "value": session_id}), session_id)
        self.assertIsNone(win.session_identity({**reference, "source": "manual"}))
        self.assertIsNone(win.session_identity({**reference, "value": f"2026-11-09T10-23-32-000Z_{session_id}.jsonl"}))
        self.assertTrue(win.shell_process(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"))
        self.assertTrue(win.agent_process(r"C:\bin\omp.exe"))
        self.assertFalse(win.agent_process("node.exe"))


if __name__ == "__main__":
    unittest.main()
