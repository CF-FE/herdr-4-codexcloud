"""Local hook transport behavior; no native user messages are sent."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.coordinator_approval_hook import handle
from scripts.coordinator_approvals import VerifiedDecision, receive_decision
from scripts.project_coordinator import Coordinator, CoordinatorError
from tests.test_coordinator_approvals import fixture_spec


class ApprovalHookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = datetime(2026, 11, 6, 1, tzinfo=timezone.utc)
        self.c = Coordinator(self.root / "state", clock=lambda: self.now)
        self.c.manage("register_project", {"project_id": "alpha", "repository": "CF-FE/alpha", "project_ref": "project:alpha",
                      "cloud_dot_principal": "dot", "report_route": "local"})
        self.c.manage("activate_owner", {"project_id": "alpha", "expected_generation": 0, "owner_principal": "owner",
                      "endpoint": "native:original", "appointment_ref": "a", "verification_ref": "v", "handoff_ref": "h", "release_ref": "r"})
        self.item = self.c.manage("approval_prepare", {"spec": fixture_spec(notice_summary="一次有界维护，范围见原单。")})
        self.key = {k: self.item["spec"][k] for k in ("project_id", "approval_id", "scope_version")}
        self.policy = {"thread_id": "bound-fixture-thread", "host_id": "bound-host", "project_ids": ["alpha"],
                       "cwd": str(self.root), "transcript_path": str(self.root / "bound.jsonl")}
        self.event = {"session_id": self.policy["thread_id"], "cwd": str(self.root), "transcript_path": self.policy["transcript_path"],
                      "turn_id": "hook-turn", "hook_event_name": "UserPromptSubmit"}

    def current(self):
        return self.c.manage("approval_list", self.key)["items"][0]

    def decide(self, decision="APPROVED", **changes):
        proof = VerifiedDecision(**self.key, scope_hash=self.item["scope_hash"], decision=decision,
                                 approver_principal="user:fixture", relay_principal="relay:fixture",
                                 source_ref="fixture:" + decision, decided_at=self.now.isoformat(), **changes)
        return receive_decision(self.c, self.key, "fixture:trusted-recorder", lambda *_: proof)

    def test_bound_activity_prepares_one_notice_without_marking_delivery(self):
        with patch("scripts.coordinator_approval_hook.collect", return_value={"accepted": 0}) as intake:
            result = handle(self.c, self.policy, self.event)
            self.assertIn("一次有界维护", result["system_message"])
            self.assertIn("APPROVAL_DECISION", result["additional_context"])
            self.assertEqual(self.current()["progress"]["delivery"]["status"], "ACCEPTED")
            self.assertEqual(self.current()["progress"]["decision"]["status"], "PENDING")
            self.assertIsNone(handle(self.c, self.policy, self.event)["system_message"])
            self.assertEqual(intake.call_args.kwargs["timeout"], 0.9)

    def test_reader_timeout_preserves_local_notice_and_stop_returns_safely(self):
        with patch("scripts.coordinator_approval_hook.collect", side_effect=CoordinatorError("NATIVE_READ_TIMEOUT")):
            result = handle(self.c, self.policy, self.event)
            self.assertTrue(result["system_message"])
            self.assertEqual(result["intake"], {"unavailable": 1})
            self.assertIn("承接责任保留", result["additional_context"])
            stop = handle(self.c, self.policy, {**self.event, "hook_event_name": "Stop"})
            self.assertIsNone(stop["system_message"])
            self.assertEqual(stop["intake"], {"unavailable": 1})
            self.assertEqual(self.current()["progress"]["decision"]["status"], "PENDING")

    def test_wrong_session_or_transcript_never_reads_or_claims(self):
        with patch("scripts.coordinator_approval_hook.collect") as intake:
            for change in ({"session_id": "other"}, {"cwd": "/elsewhere"}, {"transcript_path": "/other.jsonl"}):
                self.assertIsNone(handle(self.c, self.policy, {**self.event, **change})["system_message"])
            intake.assert_not_called()
        self.assertEqual(self.current()["progress"]["delivery"]["status"], "PENDING")

    def test_withdrawal_unknown_is_a_stop_and_reconciliation_notice(self):
        approved = self.decide()
        claim = self.c.manage("approval_claim", {**self.key, "expected_revision": approved["progress"]["revision"], "stage": "execution"})
        self.c.manage("approval_record", {**self.key, "expected_revision": claim["progress"]["revision"], "stage": "execution",
                      "action_id": claim["action_id"], "outcome": "UNKNOWN", "evidence_ref": "fixture:unknown"})
        self.decide("WITHDRAWN", supersedes_ref="fixture:APPROVED")
        self.now += timedelta(hours=2)
        with patch("scripts.coordinator_approval_hook.collect", return_value={"accepted": 0}):
            result = handle(self.c, self.policy, self.event)
        self.assertIn("待停止与对账", result["system_message"])
        self.assertIn("不请求再次批准", result["system_message"])
        self.assertEqual(self.current()["progress"]["execution"]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
