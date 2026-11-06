from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.coordinator_approval_workflow import WorkflowCheckpoint, resume_approval
from scripts.coordinator_approvals import VerifiedDecision, receive_decision
from scripts.project_coordinator import Coordinator
from scripts.project_workflow import initialize


class ApprovalWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repo = self.root / "demo"
        self.repo.mkdir()
        (self.repo / "MEMORY.md").write_text("Original project memory\n")
        definition = {
            "version": 1, "project": "demo", "workflow": "repair", "memory": "MEMORY.md",
            "start": "approval", "steps": {
                "approval": {"kind": "decision", "question": "Approve this repair?",
                             "choices": {"approved": "repair", "rejected": "end"}},
                "repair": {"kind": "handoff", "instruction": "Resume the original writer.", "next": "end"},
                "end": {"kind": "end"}}}
        spec = self.root / "definition.json"
        spec.write_text(json.dumps(definition))
        initialize(self.repo, spec)
        self.runner = self.repo / "workflows/repair/workflow.py"
        self.state = self.root / "run.sqlite3"
        data = self.root / "input.json"
        data.write_text('{"repair":"original"}')
        run = self.cli("start", "--task", "original-task", "--input", data)
        self.spec = {"checkpoint": {
            "runner": str(self.runner), "state": str(self.state), "project": "demo", "workflow": "repair",
            "task": "original-task", "step": "approval", "choice": "approved",
            "source_sha256": run["source_sha256"], "input_sha256": run["input_sha256"]}}

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-I", "-B", str(self.runner), "--state", str(self.state),
                                 *map(str, args)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_decision_resumes_original_checkpoint_and_replays_after_restart(self):
        checkpoint = WorkflowCheckpoint(self.spec)
        waiting = checkpoint.status()
        self.assertEqual((waiting["status"], waiting["step"]), ("WAITING_DECISION", "approval"))
        resumed = checkpoint.resume("decision:trusted-original")
        self.assertEqual(resumed["status"], "WAITING_RECEIPT")
        self.assertEqual(resumed["decisions"]["approval"],
                         {"choice": "approved", "evidence": "decision:trusted-original"})
        original_action = resumed["action"]["id"]
        original_bytes = self.state.read_bytes()
        replay = WorkflowCheckpoint(deepcopy(self.spec)).resume("decision:trusted-original")
        self.assertEqual(replay["action"]["id"], original_action)
        self.assertEqual(self.state.read_bytes(), original_bytes)
        with self.assertRaisesRegex(ValueError, "conflict"):
            checkpoint.resume("decision:another")

    def test_status_is_read_only_and_does_not_execute_project_definitions(self):
        # Defining the custom operation must only happen in modifying commands.
        definition = self.runner.with_name("definition.py")
        marker = self.root / "module-loaded"
        definition.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
                              + definition.read_text())
        self.spec["checkpoint"]["source_sha256"] = self.source_digest()
        # Keep the original source-version expectation inside this synthetic run consistent.
        import sqlite3
        with sqlite3.connect(self.state) as db:
            run = json.loads(db.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()[0])
            run["source_sha256"] = self.spec["checkpoint"]["source_sha256"]
            db.execute("UPDATE checkpoint SET data=? WHERE id=1", (json.dumps(run),))
        before = self.state.read_bytes()
        WorkflowCheckpoint(self.spec).status()
        self.assertFalse(marker.exists())
        self.assertEqual(self.state.read_bytes(), before)

    def source_digest(self):
        source = {name: hashlib.sha256(self.runner.with_name(name).read_bytes()).hexdigest()
                  for name in ("workflow.py", "definition.py")}
        return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()

    def test_source_mismatch_is_rejected_before_the_changed_runner_can_execute(self):
        marker = self.root / "changed-runner-executed"
        self.runner.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"
                               + self.runner.read_text())
        with self.assertRaisesRegex(ValueError, "source"):
            WorkflowCheckpoint(self.spec).status()
        self.assertFalse(marker.exists())

    def test_input_project_workflow_task_and_pending_node_mismatches_are_rejected(self):
        for field, wrong in (("project", "another-project"), ("workflow", "another-workflow"),
                             ("task", "another-task"), ("input_sha256", "0" * 64),
                             ("step", "another-step"), ("choice", "not-allowed")):
            with self.subTest(field=field):
                spec = deepcopy(self.spec)
                spec["checkpoint"][field] = wrong
                with self.assertRaises(ValueError):
                    WorkflowCheckpoint(spec).resume("decision:original")
        self.assertEqual(self.cli("status")["status"], "WAITING_DECISION")

    def test_unknown_handoff_is_preserved_without_resubmission(self):
        result = WorkflowCheckpoint(self.spec).resume("decision:original")
        action_id = result["action"]["id"]
        unknown = self.cli("receive", "--action", action_id, "--status", "UNKNOWN", "--evidence", "native:lost")
        before = self.state.read_bytes()
        after = WorkflowCheckpoint(self.spec).resume("decision:original")
        self.assertEqual(after, unknown)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual((after["status"], after["action"]["id"]), ("UNKNOWN", action_id))

    def test_rejected_decision_cannot_be_reinterpreted_as_approved(self):
        self.cli("decide", "--step", "approval", "--choice", "rejected", "--evidence", "decision:denied")
        with self.assertRaisesRegex(ValueError, "conflict"):
            WorkflowCheckpoint(self.spec).resume("decision:original")
        self.assertEqual(self.cli("status")["status"], "FINISHED")

    def approval(self, *, expires=None):
        clock = lambda: datetime(2026, 11, 6, 6, tzinfo=timezone.utc)
        coordinator = Coordinator(self.root / "coordinator", clock=clock)
        coordinator.manage("register_project", {"project_id": "demo", "repository": "CF-FE/demo",
                           "project_ref": "projects/1", "cloud_dot_principal": "dot", "report_route": "local"})
        coordinator.manage("activate_owner", {"project_id": "demo", "expected_generation": 0,
                           "owner_principal": "owner:1", "endpoint": "native:1", "appointment_ref": "appoint",
                           "verification_ref": "verify", "handoff_ref": "handoff", "release_ref": "release"})
        key = {"project_id": "demo", "approval_id": "repair-window", "scope_version": 1}
        spec = {**key, "generation": 1, "task_ref": "issue:original-task", "scope": {"repair": "original"},
                "authorization_gap": "Open the original maintenance window.", "dispatcher": "original-dispatcher",
                "executor_ref": "native:original-writer", "checkpoint": self.spec["checkpoint"],
                "acceptor_principal": "owner:1", "delivery_due_at": "2026-11-06T05:00:00Z",
                "escalate_at": "2026-11-06T07:00:00Z", "escalation_owner": "coordinator",
                "route": {"kind": "codex_app", "thread_id": "bound-thread", "host_id": "bound-host",
                          "authorization_ref": "bound-route", "approver_principal": "user:bound",
                          "relay_principal": "local:trusted-inlet"}}
        if expires:
            spec["authorization_expires_at"] = expires
        item = coordinator.manage("approval_prepare", {"spec": spec})
        proof = VerifiedDecision(**key, scope_hash=item["scope_hash"], decision="APPROVED",
                                 approver_principal="user:bound", relay_principal="local:trusted-inlet",
                                 source_ref="native:original-user-answer", decided_at="2026-11-06T05:50:00Z")
        # A verifier double represents the host boundary; no CLI can adopt a decision.
        receive_decision(coordinator, key, "answer-handle", lambda *_: proof)
        return coordinator, key

    def test_coordinator_claim_applies_once_and_recovers_after_checkpoint_commit(self):
        coordinator, key = self.approval()
        original_manage = coordinator.manage

        def fail_first_record(operation, data):
            if operation == "approval_record":
                raise RuntimeError("lost after checkpoint committed")
            return original_manage(operation, data)

        with mock.patch.object(coordinator, "manage", side_effect=fail_first_record):
            with self.assertRaisesRegex(RuntimeError, "lost after checkpoint"):
                resume_approval(coordinator, key)
        adopted = self.cli("status")
        self.assertEqual(adopted["status"], "WAITING_RECEIPT")
        pending = coordinator.manage("approval_list", key)["items"][0]
        self.assertEqual(pending["progress"]["execution"]["status"], "INTENT")
        action = pending["progress"]["execution"]["action_id"]
        result = resume_approval(coordinator, key)
        self.assertEqual(result["run"], adopted)
        self.assertEqual(result["ledger"]["progress"]["execution"]["status"], "CHECKPOINT_APPLIED")
        self.assertEqual(result["ledger"]["progress"]["execution"]["action_id"], action)
        self.assertIn(adopted["action"]["id"], result["ledger"]["progress"]["execution"]["evidence_ref"])
        # Once adopted, later resumes only query; they neither decide nor modify the ledger.
        with mock.patch.object(WorkflowCheckpoint, "resume", side_effect=AssertionError("duplicate adoption")):
            replay = resume_approval(coordinator, key)
        self.assertEqual(replay, result)

    def test_coordinator_refuses_expired_or_withdrawn_approval_without_changing_checkpoint(self):
        coordinator, key = self.approval(expires="2026-11-06T06:30:00Z")
        coordinator.clock = lambda: datetime(2026, 11, 6, 7, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "EXPIRED"):
            resume_approval(coordinator, key)
        item = coordinator.manage("approval_list", key)["items"][0]
        proof = VerifiedDecision(**key, scope_hash=item["scope_hash"], decision="WITHDRAWN",
                                 approver_principal="user:bound", relay_principal="local:trusted-inlet",
                                 source_ref="native:user-withdrawal", decided_at="2026-11-06T06:55:00Z",
                                 supersedes_ref="native:original-user-answer")
        receive_decision(coordinator, key, "withdrawal", lambda *_: proof)
        with self.assertRaisesRegex(ValueError, "APPROVED"):
            resume_approval(coordinator, key)
        self.assertEqual(self.cli("status")["status"], "WAITING_DECISION")
        self.assertEqual(coordinator.manage("approval_list", key)["items"][0]["progress"]["execution"]["status"],
                         "STOPPED")

    def test_withdrawal_during_resume_internal_status_stops_before_decide(self):
        coordinator, key = self.approval()
        item = coordinator.manage("approval_list", key)["items"][0]
        coordinator.manage("approval_claim", {**key, "stage": "execution",
                                               "expected_revision": item["progress"]["revision"]})
        original_status = WorkflowCheckpoint.status
        calls = 0

        def status_then_withdraw(checkpoint):
            nonlocal calls
            run = original_status(checkpoint)
            calls += 1
            if calls == 2:  # resume() queries again after the outer preflight.
                proof = VerifiedDecision(**key, scope_hash=item["scope_hash"], decision="WITHDRAWN",
                                         approver_principal="user:bound", relay_principal="local:trusted-inlet",
                                         source_ref="native:withdraw-during-status", decided_at="2026-11-06T06:00:00Z",
                                         supersedes_ref="native:original-user-answer")
                receive_decision(coordinator, key, "withdrawal", lambda *_: proof)
            return run

        with mock.patch.object(WorkflowCheckpoint, "status", new=status_then_withdraw):
            with self.assertRaisesRegex(ValueError, "APPROVED"):
                resume_approval(coordinator, key)
        self.assertEqual(self.cli("status")["status"], "WAITING_DECISION")
        current = coordinator.manage("approval_list", key)["items"][0]["progress"]
        self.assertEqual(current["decision"]["status"], "WITHDRAWN")
        self.assertTrue(current["stop_requested"])
        self.assertEqual(current["execution"]["status"], "INTENT")

    def test_withdrawal_after_adoption_preserves_original_checkpoint_and_stop_request(self):
        coordinator, key = self.approval()
        adopted = resume_approval(coordinator, key)
        item = adopted["ledger"]
        proof = VerifiedDecision(**key, scope_hash=item["scope_hash"], decision="WITHDRAWN",
                                 approver_principal="user:bound", relay_principal="local:trusted-inlet",
                                 source_ref="native:withdraw-after-adoption", decided_at="2026-11-06T06:00:00Z",
                                 supersedes_ref="native:original-user-answer")
        receive_decision(coordinator, key, "withdrawal", lambda *_: proof)
        with mock.patch.object(WorkflowCheckpoint, "resume", side_effect=AssertionError("must remain read-only")):
            reconciled = resume_approval(coordinator, key)
        self.assertEqual(reconciled["run"], adopted["run"])
        self.assertTrue(reconciled["ledger"]["progress"]["stop_requested"])
        self.assertEqual(reconciled["ledger"]["progress"]["decision"]["status"], "WITHDRAWN")


if __name__ == "__main__":
    unittest.main()
