"""Prepare proposals use the original coordinator transcript, never its authority to approve."""
import json
import sqlite3
import unittest
from unittest.mock import patch

from scripts import coordinator_followup_intake as intake
from scripts.project_coordinator import Coordinator
from tests import test_coordinator_followup_intake as fixtures


PREPARE_MARKER = "COORD_APPROVAL_PREPARE "


class ApprovalPrepareIntakeTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.FollowupIntakeTests()
        self.addCleanup(self.h.doCleanups)
        self.h.setUp()
        self.policy_path = self.h.root / "native-approval-policy.json"
        self.policy = {"schema_version": 1, "binary": "/not-executed/codex", "binary_sha256": "a" * 64,
                       "codex_home": "/not-executed/codex-home", "thread_id": "bound-main-thread",
                       "host_id": "bound-mac", "cwd": "/not-executed/project",
                       "transcript_path": "/not-executed/main.jsonl", "source": "vscode",
                       "originator": "Codex Desktop", "approver_principal": "user:bound",
                       "relay_principal": "recorder:main", "project_ids": ["demo"],
                       "authorization_ref": "operator:original-route", "resume_checkpoints": False}
        self.write_policy()

    def write_policy(self):
        self.policy_path.write_text(json.dumps(self.policy))
        self.policy_path.chmod(0o600)

    def spec(self, approval_id="repair-window"):
        return {"project_id": "demo", "approval_id": approval_id, "scope_version": 1, "generation": 1,
                "task_ref": "https://github.com/CF-FE/demo/issues/2", "scope": {"action": "original-maintenance"},
                "authorization_gap": "Open the original maintenance window.", "notice_summary": "请决定原范围维护窗口。",
                "route": {"kind": "codex_app", **{key: self.policy[key] for key in
                          ("thread_id", "host_id", "authorization_ref", "approver_principal", "relay_principal")}},
                "dispatcher": "original-dispatcher", "executor_ref": "native:original-writer",
                "checkpoint": {"runner": "/project/workflows/repair/workflow.py", "state": "/private/run.sqlite3",
                               "project": "demo", "workflow": "repair", "task": "original-task", "step": "approve",
                               "source_sha256": "b" * 64, "input_sha256": "c" * 64, "choice": "approved"},
                "acceptor_principal": "owner", "delivery_due_at": "2030-11-04T12:00:00Z",
                "escalate_at": "2030-11-04T13:00:00Z", "escalation_owner": "project-coordinator"}

    def proposal(self, payload=None):
        payload = {"spec": self.spec()} if payload is None else payload
        return self.h.line(self.h.message(PREPARE_MARKER + json.dumps(payload, ensure_ascii=False)))

    def approvals(self):
        return self.h.core.manage("approval_list", {"project_id": "demo"})["items"]

    def events(self):
        with sqlite3.connect(self.h.core.path) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM events WHERE kind='APPROVAL_PREPARE_PROPOSAL' ORDER BY seq")]

    def cursor(self):
        return json.loads((self.h.state / intake.CURSOR).read_bytes())

    def test_prepares_pending_without_a_job_and_replays_after_cursor_save_interruption(self):
        self.assertEqual(intake.PREPARE_MARKER, PREPARE_MARKER)
        self.h.bind()
        self.h.append(self.proposal())
        with patch.object(intake, "save_cursor", side_effect=OSError("cursor save interrupted")):
            with self.assertRaisesRegex(OSError, "cursor save interrupted"):
                self.h.collect()
        item, = self.approvals()
        self.assertEqual((item["progress"]["decision"]["status"], item["progress"]["execution"]["status"]),
                         ("PENDING", "NOT_DISPATCHED"))
        before = self.events()
        self.assertEqual(len(before), 1)
        self.assertTrue(before[0]["source_ref"])
        self.h.core = Coordinator(self.h.state)
        result = self.h.collect()
        self.assertEqual((result["accepted"], result["rejected"]), (1, 0))
        self.assertEqual(len(self.approvals()), 1)
        self.assertEqual(self.events(), before)
        recorded = self.cursor()["last_approval_prepare"]
        self.assertEqual({k: recorded[k] for k in ("project_id", "approval_id", "scope_version")},
                         {"project_id": "demo", "approval_id": "repair-window", "scope_version": 1})
        self.assertEqual((recorded["scope_hash"], recorded["source_ref"]), (item["scope_hash"], before[0]["source_ref"]))
        with sqlite3.connect(self.h.core.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM requests").fetchone()[0], 0)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone())

    def test_user_tool_and_quoted_proposals_cannot_prepare_an_approval(self):
        self.h.bind()
        marker = PREPARE_MARKER + json.dumps({"spec": self.spec()})
        rows = [self.h.message(marker, role="user", part="input_text"), self.h.message(marker, role="developer"),
                self.h.message(marker, phase="analysis"), self.h.message(marker, part="input_text"),
                self.h.message("> " + marker), self.h.message("```text\n" + marker + "\n```"),
                self.h.message("引用：\n" + marker),
                {"type": "response_item", "payload": {"type": "function_call_output", "output": marker}}]
        multipart = self.h.message(marker)
        multipart["payload"]["content"].append({"type": "output_text", "text": "another part"})
        for row in [*rows, multipart]:
            self.h.append(self.h.line(row))
        self.h.append(self.h.decision(self.h.put("ordinary")))
        result = self.h.collect()
        self.assertEqual((result["accepted"], result["rejected"]), (1, 0))
        self.assertEqual(self.approvals(), [])
        self.assertEqual(self.events(), [])
        self.assertEqual(set(self.h.rows()), {"ordinary"})

    def test_strict_payload_project_route_and_summary_rejections_do_not_block_followups(self):
        self.h.bind()
        bad = [{"op": "approval_prepare", "spec": self.spec()},
               {"covered_authorization_ref": "self:approved", "spec": self.spec()},
               {"decision": "APPROVED", "spec": self.spec()},
               {"op": "claim", "spec": self.spec()}, {"op": "close", "spec": self.spec()}]
        outside = self.spec()
        outside.update(project_id="another-project")
        outside["checkpoint"]["project"] = "another-project"
        bad.append({"spec": outside})
        for key in ("thread_id", "host_id", "authorization_ref", "approver_principal", "relay_principal"):
            changed = self.spec()
            changed["route"][key] = "another-binding"
            bad.append({"spec": changed})
        extra = self.spec()
        extra["route"]["fallback"] = "unbound"
        bad.append({"spec": extra})
        missing = self.spec()
        missing.pop("notice_summary")
        bad.append({"spec": missing})
        bad.extend({"spec": {**self.spec(), "notice_summary": summary}} for summary in ("", "   ", "x" * 1001))
        self.h.append(self.h.line(self.h.message(PREPARE_MARKER + "{bad json")))
        for payload in bad:
            last_bad = self.h.source.stat().st_size
            self.h.append(self.proposal(payload))
        self.h.append(self.h.decision(self.h.put("after-rejections")))
        result = self.h.collect()
        self.assertEqual((result["accepted"], result["rejected"]), (1, len(bad) + 1))
        self.assertEqual(self.cursor()["last_rejection"]["offset"], last_bad)
        self.assertEqual(result["offset"], self.h.source.stat().st_size)
        self.assertEqual(self.approvals(), [])
        self.assertEqual(set(self.h.rows()), {"after-rejections"})

    def test_unavailable_policy_preserves_original_proposal_until_policy_recovers(self):
        bound = self.h.bind()
        self.h.append(self.proposal())
        self.h.append(self.h.decision(self.h.put("after-policy-recovery")))
        for mode in ("missing", "nonprivate", "malformed"):
            with self.subTest(policy=mode):
                self.write_policy()
                if mode == "missing":
                    self.policy_path.unlink()
                elif mode == "nonprivate":
                    self.policy_path.chmod(0o644)
                else:
                    self.policy_path.write_text('{"schema_version":1}')
                with self.assertRaisesRegex(ValueError, "APPROVAL_PREPARE_POLICY_(UNAVAILABLE|INVALID)"):
                    self.h.collect()
                cursor = self.cursor()
                self.assertEqual((cursor["offset"], cursor["accepted"], cursor["rejected"]),
                                 (bound["initial_offset"], 0, 0))
                self.assertEqual(cursor["last_failure"]["offset"], bound["initial_offset"])
                self.assertIn(cursor["last_failure"]["code"],
                              {"APPROVAL_PREPARE_POLICY_UNAVAILABLE", "APPROVAL_PREPARE_POLICY_INVALID"})
                self.assertEqual(self.approvals(), [])
                self.assertEqual(self.h.rows(), {})
        self.write_policy()
        result = self.h.collect()
        self.assertEqual((result["accepted"], result["rejected"]), (2, 0))
        self.assertEqual(len(self.approvals()), 1)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(set(self.h.rows()), {"after-policy-recovery"})

    def test_sqlite_prepare_failure_keeps_original_offset_for_recovery_not_rejection(self):
        bound = self.h.bind()
        self.h.append(self.proposal())
        self.h.append(self.h.decision(self.h.put("after-recovery")))
        original_manage = self.h.core.manage
        def unavailable(operation, data):
            if operation == "approval_prepare":
                raise sqlite3.OperationalError("database is locked")
            return original_manage(operation, data)
        with patch.object(self.h.core, "manage", side_effect=unavailable):
            with self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"):
                self.h.collect()
        cursor = self.cursor()
        self.assertEqual((cursor["offset"], cursor["rejected"]), (bound["initial_offset"], 0))
        self.assertEqual(cursor["last_failure"]["offset"], bound["initial_offset"])
        self.assertEqual(self.h.rows(), {})
        self.assertEqual(self.approvals(), [])
        result = self.h.collect()
        self.assertEqual((result["accepted"], result["rejected"]), (2, 0))
        self.assertEqual(len(self.approvals()), 1)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(set(self.h.rows()), {"after-recovery"})


if __name__ == "__main__":
    unittest.main()
