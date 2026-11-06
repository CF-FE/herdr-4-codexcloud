"""Actual adapter logic with synthetic App tools, never live approval messages."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.coordinator_approval_channel import CodexAppDecisionEntry, READ_TOOL, SEND_TOOL
from scripts.project_coordinator import Coordinator, CoordinatorError
from tests.test_coordinator_approvals import fixture_spec


class DecisionEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.now = datetime(2026, 11, 6, 2, tzinfo=timezone.utc)
        self.c = Coordinator(Path(temporary.name) / "state", clock=lambda: self.now)
        self.c.manage("register_project", {"project_id": "alpha", "repository": "CF-FE/alpha",
                      "project_ref": "project:alpha", "cloud_dot_principal": "dot", "report_route": "local"})
        self.c.manage("activate_owner", {"project_id": "alpha", "expected_generation": 0,
                      "owner_principal": "owner", "endpoint": "native:original", "appointment_ref": "a",
                      "verification_ref": "v", "handoff_ref": "h", "release_ref": "r"})
        prepared = self.c.manage("approval_prepare", {"spec": fixture_spec()})
        self.key = {k: prepared["spec"][k] for k in ("project_id", "approval_id", "scope_version")}
        self.sent = []
        self.messages = []
        self.fail_after_send = False
        self.accept_without_visible_item = False
        self.entry = CodexAppDecisionEntry(call_tool=self.call, capability_check=lambda route: True)

    def item(self):
        return self.c.manage("approval_list", self.key)["items"][0]

    def call(self, name, args):
        self.assertEqual((args["threadId"], args["hostId"]), ("bound-fixture-thread", "bound-host"))
        if name == SEND_TOOL:
            self.assertEqual(self.item()["progress"]["delivery"]["status"], "INTENT")
            self.sent.append(args["prompt"])
            if not self.accept_without_visible_item:
                self.messages.append({"id": "native-item", "type": "userMessage",
                                      "content": [{"type": "text", "text": args["prompt"]}]})
            if self.fail_after_send:
                raise TimeoutError("synthetic lost response")
            return {"content": [{"type": "text", "text": '{"accepted":true}'}]}
        self.assertEqual(name, READ_TOOL)
        data = {"thread": {"id": args["threadId"], "hostId": args["hostId"]},
                "turns": [{"id": "native-turn", "items": self.messages}], "page": {"hasMore": False}}
        return {"content": [{"type": "text", "text": json.dumps(data)}]}

    def test_no_actual_host_channel_or_answer_source_fails_before_claim(self):
        missing = CodexAppDecisionEntry()
        with self.assertRaisesRegex(CoordinatorError, "MISSING_COORDINATOR_DECISION_CHANNEL"):
            missing.send(self.c, self.key)
        self.assertEqual(self.item()["progress"]["delivery"]["status"], "PENDING")
        with self.assertRaisesRegex(CoordinatorError, "MISSING_TRUSTED_DECISION_INTAKE"):
            self.entry.receive(self.c, self.key, {"role": "user", "text": "批准"})

    def test_send_readback_only_adopts_native_prompt_at_bound_entry(self):
        delivered = self.entry.send(self.c, self.key)
        self.assertEqual(delivered["progress"]["delivery"]["status"], "DELIVERED")
        self.assertEqual(delivered["progress"]["decision"]["status"], "PENDING")
        self.assertIn("native-turn:native-item", delivered["progress"]["delivery"]["evidence_ref"])
        self.assertNotIn("/fixture/workflow.py", self.sent[0])
        self.entry.send(self.c, self.key)
        self.assertEqual(len(self.sent), 1)

    def test_accepted_is_not_delivery_and_arbitrary_user_or_tool_echo_does_not_approve(self):
        self.accept_without_visible_item = True
        item = self.entry.send(self.c, self.key)
        self.assertEqual(item["progress"]["delivery"]["status"], "ACCEPTED")
        self.messages = [{"type": "userMessage", "id": "answer", "content": [{"type": "text", "text": "批准"}]},
                         {"type": "toolCall", "id": "echo", "arguments": {"prompt": self.sent[0]}},
                         {"type": "agentMessage", "id": "assistant-echo", "text": self.sent[0]}]
        self.entry.query(self.c, self.key)
        self.assertEqual(self.item()["progress"]["delivery"]["status"], "ACCEPTED")
        self.assertEqual(self.item()["progress"]["decision"]["status"], "PENDING")
        self.entry.send(self.c, self.key)
        self.assertEqual(len(self.sent), 1)

    def test_lost_send_response_reconciles_positive_history_without_resending(self):
        self.fail_after_send = True
        with self.assertRaises(TimeoutError):
            self.entry.send(self.c, self.key)
        self.assertEqual(self.item()["progress"]["delivery"]["status"], "UNKNOWN")
        found = self.entry.send(self.c, self.key)
        self.assertEqual(found["progress"]["delivery"]["status"], "DELIVERED")
        self.assertEqual(len(self.sent), 1)

    def test_wrong_entry_is_not_a_receipt_and_escalation_has_separate_actual_result(self):
        self.accept_without_visible_item = True
        self.entry.send(self.c, self.key)
        original = self.entry.call_tool
        def wrong(name, args):
            reply = original(name, args)
            if name == READ_TOOL:
                value = json.loads(reply["content"][0]["text"])
                value["thread"]["id"] = "unbound-thread"
                reply["content"][0]["text"] = json.dumps(value)
            return reply
        self.entry.call_tool = wrong
        with self.assertRaisesRegex(CoordinatorError, "APPROVAL_ENTRY_MISMATCH"):
            self.entry.query(self.c, self.key)
        self.entry.call_tool = original
        self.accept_without_visible_item = False
        # Use a separate sender assertion: escalation has its own intent, not a new approval send.
        def escalation(name, args):
            if name == SEND_TOOL:
                self.assertEqual(self.item()["progress"]["escalations"]["decision"]["status"], "INTENT")
                self.sent.append(args["prompt"])
                self.messages.append({"id": "escalation-item", "type": "userMessage",
                                      "content": [{"type": "text", "text": args["prompt"]}]})
                return {"content": [{"type": "text", "text": '{"accepted":true}'}]}
            return original(name, args)
        self.entry.call_tool = escalation
        self.c.manage("tick", {})
        raised = self.entry.send(self.c, self.key, stage="escalation", phase="decision")
        self.assertEqual(raised["progress"]["escalations"]["decision"]["status"], "DELIVERED")
        self.entry.send(self.c, self.key, stage="escalation", phase="decision")
        self.assertEqual(len(self.sent), 2)


if __name__ == "__main__":
    unittest.main()
