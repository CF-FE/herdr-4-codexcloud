"""Use private temporary task files, a fixture ledger and a simulated native endpoint."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from scripts import herdr_directed_contact as contact
from scripts import herdr_role_contact as role_contact
from scripts.project_coordinator import Coordinator, CoordinatorError


def put(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)


class DirectedContactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.task = self.root / "private-task"
        self.task.mkdir(mode=0o700)
        self.path = self.task / "intent.json"
        self.body = b"--session=another-session"
        self.intent = {"version": 1, "project_id": "fixture", "task_ref": "https://github.com/CF-FE/fixture/issues/9",
                       "handoff_ref": "original-handoff", "action_id": "approved-contact", "generation": 1,
                       "driver_pane": "w1:p1", "driver_terminal_id": "term_driver", "target_terminal_id": "term_target",
                       "prompt_file": "prompt.txt", "prompt_sha256": hashlib.sha256(self.body).hexdigest()}
        put(self.task / self.intent["prompt_file"], self.body)
        self.save_intent()
        handoffs = self.root / "handoffs/herdr-project-coordinator"
        handoffs.mkdir(mode=0o700, parents=True)
        accepted = b"synthetic acceptance receipt"
        put(handoffs / "accepted.txt", accepted)
        self.binding_path = handoffs / "binding.json"
        self.binding = {"role": "project_coordinator", "harness": "codex", "session": "fixture-session",
                        "agent": "coordinator", "pane": "w1:p2", "workspace": "w1", "tab": "w1:t2",
                        "receipt_sha256": hashlib.sha256(accepted).hexdigest(),
                        "agent_session": {"kind": "id", "source": "herdr:codex", "agent": "codex", "value": "thread-1"}}
        put(self.binding_path, json.dumps(self.binding).encode())
        self.coordinator = Coordinator(self.root / "state")
        self.coordinator.manage("register_project", {"project_id": "fixture", "repository": "CF-FE/fixture",
                                "project_ref": "fixture-project", "cloud_dot_principal": "dot", "report_route": "dot"})
        self.coordinator.manage("activate_owner", {"project_id": "fixture", "expected_generation": 0,
                                "owner_principal": "owner", "endpoint": "original-owner", "appointment_ref": "a",
                                "verification_ref": "v", "handoff_ref": "h", "release_ref": "r"})
        self.agent = {"agent": "codex", "name": "coordinator", "agent_session": self.binding["agent_session"],
                      "pane_id": "w1:p2", "workspace_id": "w1", "tab_id": "w1:t2", "terminal_id": "term_target",
                      "agent_status": "idle", "interactive_ready": True}
        self.driver = {"pane_id": "w1:p1", "terminal_id": "term_driver"}
        self.driver_pid = os.getpid()
        self.prompts = []
        self.reply = None
        for override in (patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_SESSION": "fixture-session",
                                                  "HERDR_PANE_ID": "w1:p1"}),
                         patch.object(os, "isatty", return_value=True),
                         patch.object(os, "tcgetpgrp", return_value=os.getpgrp())):
            override.start()
            self.addCleanup(override.stop)

    def save_intent(self):
        put(self.path, json.dumps(self.intent).encode())

    def plan(self):
        intent, _, _ = contact.read_intent(self.path)
        return contact.contact_plan(self.root, intent)[0]

    def status(self):
        return contact.status(self.intent["task_ref"], self.intent["action_id"], root=self.root)

    def send(self, digest=None, *, path=None, query=None):
        return contact.send(path or self.path, digest or self.plan()["plan_sha256"], root=self.root, query=query or self.query)

    def query(self, session, *command):
        self.assertEqual(session, self.binding["session"])
        if command == ("pane", "process-info", "--pane", "w1:p1"):
            return {"process_info": {"pane_id": "w1:p1", "foreground_processes": [{"pid": self.driver_pid}]}}
        if command == ("pane", "get", "w1:p1"):
            return {"pane": self.driver}
        if command == ("pane", "process-info", "--pane", "w1:p2"):
            return {"process_info": {"pane_id": "w1:p2", "foreground_processes": [{"argv": ["codex", "--no-daemon"]}]}}
        if command == ("agent", "get", "w1:p2"):
            return {"agent": self.agent}
        self.assertEqual(command[:3], ("agent", "prompt", "w1:p2"))
        self.assertEqual(self.status()["state"], "UNKNOWN")  # persisted before the external effect
        self.prompts.append(command[3])
        if self.reply is not None:
            return self.reply(session, *command)
        return {"type": "agent_prompted", "agent": {**self.agent, "agent_status": "working"}}

    def test_read_only_plan_and_status_then_one_send_even_from_a_copied_intent(self):
        with patch.object(role_contact.subprocess, "run", side_effect=AssertionError("native call")):
            plan = self.plan()
            self.assertEqual(plan["attach_argv"], [str(contact.HERDR), "--session", "fixture-session",
                                                 "terminal", "attach", "term_driver"])
            self.assertEqual(self.status()["state"], "NO_RECORD")
        first = self.send(plan["plan_sha256"])
        self.assertEqual(first["state"], "SUBMITTED")
        self.assertEqual(first, self.status())
        self.assertIn("native_receipt_sha256", first)
        other = self.root / "copy"
        shutil.copytree(self.task, other)
        duplicate = self.send(plan["plan_sha256"], path=other / self.path.name,
                              query=lambda *args: self.fail("duplicate contacted Herdr"))
        self.assertEqual(duplicate, {**first, "duplicate": True})
        self.assertEqual(self.prompts, ["HANDOFF_REF=original-handoff\nCONTACT_ACTION_ID=approved-contact\n"
                                        "TASK_REF=https://github.com/CF-FE/fixture/issues/9\n\n--session=another-session"])
        new_body = b"Different instruction for an existing action"
        put(self.task / self.intent["prompt_file"], new_body)
        self.intent["prompt_sha256"] = hashlib.sha256(new_body).hexdigest()
        self.save_intent()
        with self.assertRaisesRegex(CoordinatorError, "CONTACT_INTENT_CONFLICT"):
            self.send()
        self.assertEqual(len(self.prompts), 1)

    def test_project_scope_must_match_original_issue_and_generation(self):
        plan = self.plan()
        self.coordinator.manage("confirm_vacancy", {"project_id": "fixture", "expected_generation": 1,
                                "vacancy_ref": "owner-left", "release_ref": "released"})
        with self.assertRaisesRegex(CoordinatorError, "STALE_GENERATION"):
            self.send(plan["plan_sha256"])
        self.intent["task_ref"] = "https://github.com/CF-FE/other/issues/9"
        self.save_intent()
        with self.assertRaisesRegex(CoordinatorError, "TASK_PROJECT_MISMATCH"):
            self.plan()
        self.assertEqual(self.prompts, [])

    def test_appointment_change_during_preflight_stops(self):
        def changed(session, *command):
            response = self.query(session, *command)
            if command == ("agent", "get", "w1:p2"):
                self.binding["agent_session"] = {**self.binding["agent_session"], "value": "thread-2"}
                put(self.binding_path, json.dumps(self.binding).encode())
            return response

        with self.assertRaisesRegex(CoordinatorError, "CONTACT_PLAN_CHANGED"):
            self.send(query=changed)
        self.assertEqual(self.status()["state"], "NO_RECORD")
        self.assertEqual(self.prompts, [])

    def test_agent_replaced_in_same_terminal_during_reservation_is_never_prompted(self):
        write = contact.write_atomically

        def replace_after_reserving(folder, name, raw):
            write(folder, name, raw)
            if not name.endswith(".receipt.json"):
                self.agent["agent_session"] = {**self.agent["agent_session"], "value": "replacement-thread"}

        with patch.object(contact, "write_atomically", side_effect=replace_after_reserving):
            result = self.send()
        self.assertEqual((result["state"], result["reason"]), ("NOT_SENT", "TARGET_IDENTITY_MISMATCH"))
        self.assertEqual(self.status(), result)
        self.assertTrue(self.send()["duplicate"])
        self.assertEqual(self.prompts, [])

    def test_only_exact_native_driver_target_and_ready_state_may_send(self):
        plan = self.plan()["plan_sha256"]
        with patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p8"}):
            with self.assertRaisesRegex(CoordinatorError, "NATIVE_DRIVER_REQUIRED"):
                self.send(plan)
        self.driver_pid = -1
        with self.assertRaisesRegex(CoordinatorError, "NATIVE_DRIVER_REQUIRED"):
            self.send(plan)
        self.driver_pid = os.getpid()
        with patch.dict(self.driver, {"terminal_id": "term_replaced_driver"}):
            with self.assertRaisesRegex(CoordinatorError, "DRIVER_TERMINAL_MISMATCH"):
                self.send(plan)
        for change, reason in (({"agent_status": "working"}, "TARGET_BUSY_OR_UNCONFIRMED"),
                               ({"agent_status": "blocked"}, "TARGET_BUSY_OR_UNCONFIRMED"),
                               ({"agent_session": {}}, "TARGET_IDENTITY_MISMATCH"),
                               ({"terminal_id": "term_replaced_target"}, "TARGET_TERMINAL_MISMATCH")):
            with self.subTest(change=change), patch.dict(self.agent, change):
                with self.assertRaisesRegex(CoordinatorError, reason):
                    self.send(plan)
        self.assertEqual(self.status()["state"], "NO_RECORD")
        self.assertEqual(self.prompts, [])

    def test_two_simultaneous_sends_share_one_reservation(self):
        in_prompt, resume = Event(), Event()
        digest = self.plan()["plan_sha256"]

        def response(*args):
            in_prompt.set()
            self.assertTrue(resume.wait(5))
            return {"type": "agent_prompted", "agent": self.agent}

        self.reply = response
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.send, digest)
            try:
                self.assertTrue(in_prompt.wait(5))
                second = pool.submit(self.send, digest)
                with self.assertRaisesRegex(CoordinatorError, "DIRECTED_CONTACT_BUSY"):
                    second.result(timeout=5)
                self.assertEqual(self.status()["state"], "UNKNOWN")
            finally:
                resume.set()
            self.assertEqual(first.result(timeout=5)["state"], "SUBMITTED")
        self.assertTrue(self.send(digest)["duplicate"])
        self.assertEqual(len(self.prompts), 1)

    def test_only_correlated_structured_blocked_means_not_sent(self):
        blocked = {"error": {"code": "agent_blocked", "message": "requires interactive input"}}
        for response_id, expected in (("cli:agent:prompt", "NOT_SENT"), ("cli:agent:get", "UNKNOWN")):
            with self.subTest(response_id=response_id):
                self.intent["action_id"] = expected
                self.save_intent()
                self.reply = role_contact.native
                stderr = json.dumps({"id": response_id, **blocked}).encode()
                failure = subprocess.CompletedProcess([], 1, stdout=b"", stderr=stderr)
                with patch.object(role_contact.subprocess, "run", return_value=failure) as invoke:
                    self.assertEqual(self.send()["state"], expected)
                    self.assertTrue(self.send()["duplicate"])
                invoke.assert_called_once()

    def test_timeout_and_wrong_success_receipts_are_unknown_and_never_repeat(self):
        replies = (
            ("timeout", None),
            ("wrong-type", {"type": "agent_got", "agent": self.agent}),
            ("wrong-agent", {"type": "agent_prompted", "agent": {**self.agent, "agent_session": {}}}),
            ("wrong-terminal", {"type": "agent_prompted", "agent": {**self.agent, "terminal_id": "term_other"}}),
        )
        for action, reply in replies:
            with self.subTest(action=action):
                self.intent["action_id"] = action
                self.save_intent()
                self.reply = role_contact.native
                stdout = json.dumps({"id": "cli:agent:prompt", "result": reply}).encode()
                with patch.object(role_contact.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout, b""),
                                  side_effect=subprocess.TimeoutExpired("herdr", 8) if reply is None else None) as invoke:
                    self.assertEqual(self.send()["state"], "UNKNOWN")
                    self.assertEqual(self.status()["state"], "UNKNOWN")
                    self.assertTrue(self.send()["duplicate"])
                invoke.assert_called_once()

    def test_receipt_write_failure_preserves_unknown_reservation(self):
        write = contact.write_atomically
        for published in (False, True):
            with self.subTest(published=published):
                self.intent["action_id"] = f"failed-write-{int(published)}"
                self.save_intent()

                def lost_write(folder, name, raw):
                    if not name.endswith(".receipt.json") or published:
                        write(folder, name, raw)
                    if name.endswith(".receipt.json"):
                        raise OSError("synthetic storage failure after prompt")

                with patch.object(contact, "write_atomically", side_effect=lost_write):
                    self.assertEqual(self.send()["reason"], "RECEIPT_PERSIST_FAILED")
                self.assertEqual(self.status()["state"], "UNKNOWN")
                self.assertTrue(self.send()["duplicate"])
        self.assertEqual(len(self.prompts), 2)
