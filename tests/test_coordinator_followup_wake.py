"""A simulated Herdr endpoint and the actual temporary coordinator ledger. No native prompts."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import coordinator_followup_wake as wake
from project_coordinator import Coordinator, CoordinatorError


class WakeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        directory = self.root / "handoffs/herdr-project-coordinator"
        directory.mkdir(mode=0o700, parents=True)
        self.binding_file = directory / "binding.json"
        receipt = directory / "accepted.txt"
        receipt.write_bytes(b"synthetic local appointment")
        self.route = {"role": "project_coordinator", "session": "fixture", "workspace": "w1",
                      "tab": "w1:t2", "pane": "w1:p2", "agent": "coordinator", "harness": "codex",
                      "agent_session": {"source": "herdr:codex", "kind": "id", "value": "fixture-thread",
                                        "agent": "codex"}, "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}
        self.binding_file.write_text(json.dumps(self.route))
        self.binding_file.chmod(0o600)
        receipt.chmod(0o600)
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.c = Coordinator(self.root / "state", clock=lambda: self.now)
        self.c.manage("register_project", {"project_id": "alpha", "repository": "CF-FE/alpha",
                      "project_ref": "project:alpha", "cloud_dot_principal": "cloud:alpha", "report_route": "route:alpha"})
        self.c.manage("activate_owner", {"project_id": "alpha", "expected_generation": 0, "owner_principal": "owner",
                      "endpoint": "native:owner", "appointment_ref": "a", "verification_ref": "v",
                      "handoff_ref": "h", "release_ref": "r"})
        self.item = {"project_id": "alpha", "followup_key": "pending-1", "generation": 1,
                     "task_ref": "https://example.invalid/private", "receipt_ref": "private:original",
                     "next_action": "UNTRUSTED_PRIVATE_INSTRUCTION", "action_owner": "owner",
                     "next_check_at": self.now.isoformat()}
        self.agent = {"name": "coordinator", "agent": "codex", "agent_session": self.route["agent_session"],
                      "pane_id": "w1:p2", "tab_id": "w1:t2", "workspace_id": "w1",
                      "focused": False, "agent_status": "idle", "interactive_ready": True}
        self.prompts = []

    def row(self):
        return self.c.manage("role_followup_list", {"project_id": "alpha", "followup_key": "pending-1"})["items"][0]

    def query(self, session, *command):
        self.assertEqual(session, self.route["session"])
        if command == ("pane", "process-info", "--pane", self.route["pane"]):
            return {"process_info": {"pane_id": self.route["pane"],
                    "foreground_processes": [{"pid": 111, "argv": ["/bin/codex", "--no-daemon"]}]}}
        if command == ("agent", "get", self.route["pane"]):
            return {"agent": self.agent.copy()}
        self.assertEqual(command[:3], ("agent", "prompt", self.route["pane"]))
        self.assertEqual(self.row()["state"], "INTENT")
        self.prompts.append(command[3])
        return {"type": "agent_prompted", "agent": self.agent.copy()}

    def once(self, query=None):
        return wake.wake_one(self.c, self.root, self.route, query or self.query)

    def test_only_exact_coordinator_is_prompted_once_and_item_stays_open(self):
        self.c.manage("role_followup_put", self.item)
        self.agent["focused"] = True  # observed even when the driver and coordinator occupy separate panes
        self.assertEqual(self.once()["state"], "SUBMITTED")
        self.assertEqual(len(self.prompts), 1)
        self.assertIn(self.item["followup_key"], self.prompts[0])
        self.assertIn("docs/project-coordinator.md#native-followups", self.prompts[0])
        for field in ("receipt_ref", "next_action", "task_ref"):
            self.assertNotIn(self.item[field], self.prompts[0])
        self.c = Coordinator(self.root / "state", clock=lambda: self.now)
        self.assertEqual(self.once()["state"], "NO_DUE_ITEM")
        self.assertEqual(self.row()["state"], "SUBMITTED")
        self.assertEqual(len(self.prompts), 1)

    def test_busy_wrong_identity_and_foreground_never_claim_or_send(self):
        self.c.manage("role_followup_put", self.item)
        original = self.agent.copy()
        for change in ({"agent_status": "working"}, {"agent_status": "blocked"},
                       {"agent_status": "unknown"}, {"interactive_ready": False}, {"agent_session": {}},
                       {"name": "project-owner"}, {"pane_id": "w1:p1"}):
            with self.subTest(change=change):
                self.agent = {**original, **change}
                self.assertEqual(self.once()["state"], "DEFERRED")
                self.assertIsNone(self.row()["action_id"])
                self.now += timedelta(seconds=wake.INTERVAL + 1)
        self.agent = original
        def at_shell(*args):
            if args[1] == "pane":
                return {"process_info": {"pane_id": self.route["pane"], "foreground_processes": [{"argv": ["zsh"]}]}}
            return self.query(*args)
        self.assertEqual(self.once(at_shell)["reason"], "TARGET_NOT_FOREGROUND")
        self.assertEqual(self.prompts, [])

    def test_native_becomes_busy_after_claim_returns_to_pending_without_delivery(self):
        self.c.manage("role_followup_put", self.item)
        def became_busy(*args):
            if self.row()["state"] == "INTENT":
                self.agent["agent_status"] = "working"
            return self.query(*args)
        self.assertEqual(self.once(became_busy)["state"], "NOT_SENT")
        self.assertEqual(self.row()["state"], "PENDING")
        self.assertEqual(self.prompts, [])

    def test_changed_appointment_or_closed_item_is_not_sent(self):
        self.c.manage("role_followup_put", self.item)
        manage = self.c.manage
        def close_on_claim(op, args):
            result = manage(op, args)
            if op == "role_followup_claim":
                manage("role_followup_record", {"project_id": "alpha", "followup_key": "pending-1", "generation": 1,
                       "expected_revision": result["revision"], "outcome": "CLOSED",
                       "evidence_ref": "verified:completed-before-wake"})
            return result
        with patch.object(self.c, "manage", side_effect=close_on_claim):
            self.assertEqual(self.once()["state"], "FENCED_NOT_SENT")
        self.assertFalse(self.prompts)
        self.item["followup_key"] = "pending-2"
        self.c.manage("role_followup_put", self.item)
        self.binding_file.write_text(json.dumps({**self.route, "pane": "w1:p7"}))
        self.assertEqual(self.once()["reason"], "COORDINATOR_BINDING_CHANGED")
        self.assertFalse(self.prompts)

    def test_lost_native_receipt_and_crash_never_replay(self):
        self.c.manage("role_followup_put", self.item)
        def timeout(*args):
            reply = self.query(*args)
            if args[1:3] == ("agent", "prompt"):
                raise subprocess.TimeoutExpired("fixture", 1)
            return reply
        self.assertEqual(self.once(timeout)["state"], "UNKNOWN")
        self.assertEqual(self.once()["state"], "NO_DUE_ITEM")
        self.item["followup_key"] = "pending-2"
        self.c.manage("role_followup_put", self.item)
        def crash(*args):
            if args[1:3] == ("agent", "prompt"):
                raise RuntimeError("simulated process death before receipt")
            return self.query(*args)
        with self.assertRaisesRegex(RuntimeError, "process death"):
            self.once(crash)
        self.c = Coordinator(self.root / "state", clock=lambda: self.now)
        self.assertEqual(self.once()["state"], "NO_DUE_ITEM")
        self.assertEqual(len(self.prompts), 1)

    def test_external_watch_rejected_before_writes_and_native_round_collects_without_due(self):
        command = [sys.executable, "-I", "-B", str(SCRIPTS / "coordinator_followup_wake.py"), "watch",
                   "--root", str(self.root)]
        result = subprocess.run(command, env={}, stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        self.assertEqual(json.loads(result.stderr)["error"], "NATIVE_DRIVER_REQUIRED")
        self.assertFalse((self.root / "state/role-followup-wake.lock").exists())
        collect = Mock(return_value={"backlog": False})
        with patch.object(wake, "check_driver") as check:
            self.assertEqual(wake.watch_round(self.c, self.root, collect, self.query)["state"], "NO_DUE_ITEM")
            check.assert_called_once()
            collect.assert_called_once_with(self.c, self.root)
        with wake.single_driver(self.root / "state"):
            with self.assertRaisesRegex(CoordinatorError, "DRIVER_ALREADY_RUNNING"), wake.single_driver(self.root / "state"):
                self.fail("a second driver acquired the lock")

    def test_env_alone_cannot_substitute_for_driver_pid(self):
        query = Mock(return_value={"process_info": {"pane_id": "w1:p1", "foreground_processes": [{"pid": -1}]}})
        with patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_SESSION": "fixture", "HERDR_PANE_ID": "w1:p1"}), \
             patch.object(os, "isatty", return_value=True), patch.object(os, "tcgetpgrp", return_value=os.getpgrp()):
            with self.assertRaisesRegex(CoordinatorError, "NATIVE_DRIVER_REQUIRED"):
                wake.check_driver(self.route, query)
        query.assert_called_once_with("fixture", "pane", "process-info", "--pane", "w1:p1")
