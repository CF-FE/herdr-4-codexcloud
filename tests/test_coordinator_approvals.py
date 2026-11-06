"""Approval behavior against real temporary SQLite; verifiers are synthetic host fakes.

These tests establish engineering state contracts, never a real user's identity,
delivery to a live decision entrance, or production/business acceptance.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from coordinator_approvals import VerifiedDecision, receive_decision
from project_coordinator import Coordinator, CoordinatorError


def fixture_spec(project="alpha", **changes):
    return {"project_id": project, "approval_id": "maintenance", "scope_version": 1, "generation": 1,
            "task_ref": "https://github.com/CF-FE/" + project + "/issues/2",
            "scope": {"action": "maintenance", "machine": "fixture-host", "writes": ["fixture:cache"]},
            "authorization_gap": "Fixture maintenance exceeds the existing read-only scope.",
            "route": {"kind": "codex_app", "thread_id": "bound-fixture-thread", "host_id": "bound-host",
                      "authorization_ref": "fixture:route-authority", "approver_principal": "user:fixture",
                      "relay_principal": "relay:fixture"},
            "dispatcher": "coordinator:original", "executor_ref": "native:original-executor",
            "checkpoint": {"runner": "/fixture/workflow.py", "state": "/fixture/state.json",
                           "project": project, "workflow": "maintenance", "task": "original-task",
                           "step": "authorize", "source_sha256": "a" * 64, "input_sha256": "b" * 64,
                           "choice": "approved"},
            "acceptor_principal": "owner:" + project,
            "delivery_due_at": "2026-11-06T01:10:00Z", "escalate_at": "2026-11-06T01:20:00Z",
            "escalation_owner": "coordinator:original", **changes}


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.now = datetime(2026, 11, 6, 1, tzinfo=timezone.utc)
        self.c = Coordinator(self.state, clock=lambda: self.now)
        for project in ("alpha", "beta"):
            self.c.manage("register_project", {"project_id": project, "repository": "CF-FE/" + project,
                          "project_ref": "project:" + project, "cloud_dot_principal": "dot:" + project,
                          "report_route": "route:" + project})
            self.activate(project, 0, "owner:" + project)

    def activate(self, project, generation, principal):
        return self.c.manage("activate_owner", {"project_id": project, "expected_generation": generation,
                             "owner_principal": principal, "endpoint": "native:" + principal,
                             "appointment_ref": "ref:appointment", "verification_ref": "ref:verification",
                             "handoff_ref": "ref:handoff", "release_ref": "ref:release"})

    spec = staticmethod(fixture_spec)

    def key(self, item):
        return {k: item["spec"][k] for k in ("project_id", "approval_id", "scope_version")}

    def prepare(self, project="alpha", **changes):
        return self.c.manage("approval_prepare", {"spec": self.spec(project, **changes)})

    def current(self, item):
        return self.c.manage("approval_list", self.key(item))["items"][0]

    def claim(self, item, stage="delivery", **extra):
        return self.c.manage("approval_claim", {**self.key(item), "expected_revision": item["progress"]["revision"],
                             "stage": stage, **extra})

    def record(self, item, stage, outcome, **extra):
        slot = item["progress"]["escalations"][extra["phase"]] if stage == "escalation" else item["progress"][stage]
        return self.c.manage("approval_record", {**self.key(item), "expected_revision": item["progress"]["revision"],
                             "stage": stage, "action_id": slot["action_id"], "outcome": outcome,
                             "evidence_ref": "fixture:" + outcome, **extra})

    def proof(self, item, decision="APPROVED", **changes):
        return VerifiedDecision(**{**self.key(item), "scope_hash": item["scope_hash"], "decision": decision,
                                "approver_principal": "user:fixture", "relay_principal": "relay:fixture",
                                "source_ref": "fixture:answer:" + decision, "decided_at": self.now.isoformat(), **changes})

    def decide(self, item, decision="APPROVED", **changes):
        proof = self.proof(item, decision, **changes)
        # The fake represents privileged host verification; its existence does not attest a live inlet.
        return receive_decision(self.c, self.key(item), "fixture:source-handle", lambda snapshot, handle: proof)

    def test_existing_authority_bypasses_without_approval_or_business_job(self):
        result = self.c.manage("approval_prepare", {"spec": self.spec(),
                               "covered_authorization_ref": "fixture:existing-authority"})
        self.assertEqual(result["state"], "BYPASSED")
        self.assertEqual(self.c.manage("approval_list", {})["items"], [])
        with sqlite3.connect(self.c.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM requests").fetchone()[0], 0)

    def test_acceptance_is_not_delivery_and_unknown_survives_restart_without_resend(self):
        item = self.claim(self.prepare())
        accepted = self.record(item, "delivery", "ACCEPTED")
        self.assertEqual(accepted["progress"]["delivery"]["status"], "ACCEPTED")
        self.assertEqual(accepted["progress"]["decision"]["status"], "PENDING")
        self.assertFalse(self.claim(accepted, "execution")["send"])
        unknown = self.record(accepted, "delivery", "UNKNOWN")
        self.c = Coordinator(self.state, clock=lambda: self.now)
        self.assertFalse(self.claim(self.current(unknown))["send"])
        self.assertEqual(self.current(unknown)["progress"]["delivery"]["action_id"], item["action_id"])
        delivered = self.record(self.current(unknown), "delivery", "DELIVERED")
        self.assertEqual(delivered["progress"]["delivery"]["status"], "DELIVERED")
        self.assertEqual(delivered["progress"]["decision"]["status"], "PENDING")

    def test_valid_answer_completes_missing_delivery_and_duplicate_never_redispatches(self):
        item = self.prepare()
        proof = self.proof(item)
        approved = self.decide(item)
        self.assertEqual(approved["progress"]["delivery"]["status"], "DELIVERED")
        self.assertEqual(approved["progress"]["decision"]["status"], "APPROVED")
        intent = self.claim(approved, "execution")
        self.assertTrue(intent["send"])
        unknown = self.record(intent, "execution", "UNKNOWN")
        self.c = Coordinator(self.state, clock=lambda: self.now)
        duplicate = receive_decision(self.c, self.key(item), "same-source", lambda *_: proof)
        self.assertEqual(duplicate["progress"]["revision"], unknown["progress"]["revision"])
        self.assertEqual(duplicate["progress"]["execution"]["status"], "UNKNOWN")
        self.assertFalse(self.claim(duplicate, "execution")["send"])

    def test_decisions_require_host_verifier_and_are_not_administrative_outcomes(self):
        item = self.claim(self.prepare())
        with self.assertRaisesRegex(CoordinatorError, "MISSING_TRUSTED_DECISION_INTAKE"):
            receive_decision(self.c, self.key(item), {"role": "user", "text": "approved"}, None)
        with self.assertRaisesRegex(CoordinatorError, "UNVERIFIED_DECISION"):
            receive_decision(self.c, self.key(item), "untrusted", lambda *_: {"decision": "APPROVED"})
        with self.assertRaisesRegex(CoordinatorError, "APPROVAL_STATE_CONFLICT"):
            self.record(item, "delivery", "APPROVED")
        with self.assertRaisesRegex(CoordinatorError, "ADMIN_OPERATION_NOT_ALLOWED"):
            self.c.manage("approval_decide", {**self.key(item), "decision": "APPROVED"})
        self.assertEqual(self.current(item)["progress"]["decision"]["status"], "PENDING")

    def test_scope_project_identity_and_generation_fences(self):
        item = self.prepare()
        proof = self.proof(item)
        for bad, code in (({"project_id": "beta"}, "DECISION_SCOPE_MISMATCH"),
                          ({"scope_version": 2}, "DECISION_SCOPE_MISMATCH"),
                          ({"scope_hash": "f" * 64}, "DECISION_SCOPE_MISMATCH"),
                          ({"approver_principal": "other-user"}, "DECISION_IDENTITY_MISMATCH"),
                          ({"relay_principal": "other-relay"}, "DECISION_IDENTITY_MISMATCH")):
            with self.subTest(bad=bad), self.assertRaisesRegex(CoordinatorError, code):
                receive_decision(self.c, self.key(item), "fixture", lambda *_: replace(proof, **bad))
        self.activate("alpha", 1, "owner:new")
        with self.assertRaisesRegex(CoordinatorError, "STALE_GENERATION"):
            self.decide(item)
        with self.assertRaisesRegex(CoordinatorError, "STALE_GENERATION"):
            self.claim(item)
        self.assertFalse(self.current(item)["generation_current"])
        self.assertEqual(self.current(item)["progress"]["decision"]["status"], "PENDING")

    def test_rejection_is_local_and_cancellation_requires_actual_disposition(self):
        rejected = self.decide(self.prepare(), "REJECTED")
        independent = self.decide(self.prepare("beta"))
        self.assertEqual(rejected["progress"]["execution"]["status"], "STOPPED")
        self.assertTrue(rejected["progress"]["stop_requested"])
        self.assertFalse(self.claim(rejected, "execution")["send"])
        self.assertTrue(self.claim(independent, "execution")["send"])
        with self.assertRaisesRegex(CoordinatorError, "RESULT_REVIEW_REQUIRED"):
            self.c.manage("approval_close", {**self.key(rejected), "expected_revision": rejected["progress"]["revision"],
                          "acceptor_principal": "owner:alpha", "result_ref": "fixture:no-disposition",
                          "disposition": "cancelled", "evidence_ref": "fixture:review"})
        cancelled = self.c.manage("approval_result", {**self.key(rejected),
                                "expected_revision": rejected["progress"]["revision"],
                                "result_kind": "CANCELLED", "result_ref": "fixture:original-never-dispatched"})
        self.assertEqual(cancelled["progress"]["execution"]["status"], "RESULT_PENDING")

    def test_withdrawal_retains_unknown_effect_and_late_native_receipts(self):
        approved = self.decide(self.prepare())
        unknown = self.record(self.claim(approved, "execution"), "execution", "UNKNOWN")
        withdrawn = self.decide(unknown, "WITHDRAWN", supersedes_ref="fixture:answer:APPROVED")
        self.assertTrue(withdrawn["progress"]["stop_requested"])
        self.assertEqual(withdrawn["progress"]["execution"]["status"], "UNKNOWN")
        self.assertFalse(self.claim(withdrawn, "execution")["send"])
        late = self.record(withdrawn, "execution", "ACCEPTED")
        self.assertEqual(late["progress"]["execution"]["status"], "ACCEPTED")
        self.assertTrue(late["progress"]["stop_requested"])

    def test_expired_authority_does_not_release_execution(self):
        expired = self.prepare(authorization_expires_at="2026-11-06T00:59:00Z")
        with self.assertRaisesRegex(CoordinatorError, "AUTHORIZATION_EXPIRED"):
            self.decide(expired)
        self.assertFalse(self.claim(expired, "execution")["send"])
        self.assertEqual(self.current(expired)["progress"]["decision"]["status"], "PENDING")

    def test_trusted_conflict_blocks_old_approval_until_explicit_trusted_clarification(self):
        approved = self.decide(self.prepare())
        conflict_source = "fixture:answer:contradicting-rejection"
        disputed = self.decide(approved, "REJECTED", source_ref=conflict_source)
        self.assertEqual(disputed["progress"]["decision"]["status"], "APPROVED")
        self.assertEqual(disputed["progress"]["decision_conflict"]["source_ref"], conflict_source)
        self.assertFalse(self.claim(disputed, "execution")["send"])
        duplicate = self.decide(disputed, "REJECTED", source_ref=conflict_source)
        self.assertEqual(duplicate["progress"]["revision"], disputed["progress"]["revision"])
        self.c = Coordinator(self.state, clock=lambda: self.now)
        self.assertFalse(self.claim(self.current(disputed), "execution")["send"])
        replay = self.decide(disputed)  # replaying the old approval cannot silently choose it over the conflict
        self.assertTrue(replay["progress"].get("decision_conflict"))
        self.assertFalse(self.claim(replay, "execution")["send"])
        untrusted = self.proof(disputed, source_ref="fixture:false-clarification", supersedes_ref=conflict_source,
                               approver_principal="other-user")
        with self.assertRaisesRegex(CoordinatorError, "DECISION_IDENTITY_MISMATCH"):
            receive_decision(self.c, self.key(disputed), "untrusted", lambda *_: untrusted)
        self.assertTrue(self.current(disputed)["progress"].get("decision_conflict"))
        clarified = self.decide(disputed, source_ref="fixture:answer:explicit-clarification", supersedes_ref=conflict_source)
        self.assertFalse(clarified["progress"].get("decision_conflict"))
        self.assertEqual(clarified["progress"]["decision"]["source_ref"], "fixture:answer:explicit-clarification")
        self.assertTrue(self.claim(clarified, "execution")["send"])

    def test_scope_revision_keeps_history_and_does_not_inherit_old_approval(self):
        old = self.decide(self.prepare())
        new = self.prepare(scope_version=2, scope={"action": "maintenance", "machine": "different-host"})
        self.assertFalse(self.current(old)["progress"]["current"])
        self.assertEqual(new["progress"]["decision"]["status"], "PENDING")
        self.assertFalse(self.claim(new, "execution")["send"])
        with self.assertRaisesRegex(CoordinatorError, "APPROVAL_NOT_CURRENT"):
            self.claim(self.current(old), "execution")
        active = self.record(self.claim(self.decide(new), "execution"), "execution", "UNKNOWN")
        with self.assertRaisesRegex(CoordinatorError, "RECONCILE_ORIGINAL_EXECUTION"):
            self.prepare(scope_version=3)
        self.assertEqual(self.current(active)["progress"]["execution"]["status"], "UNKNOWN")

    def test_recheck_preserves_absolute_deadlines_and_escalations_have_one_intent_per_phase(self):
        item = self.prepare()
        self.now += timedelta(minutes=21)
        rechecked = self.c.manage("approval_recheck", {**self.key(item),
                                 "expected_revision": item["progress"]["revision"],
                                 "next_check_at": "2026-11-07T01:00:00Z", "evidence_ref": "fixture:new-observation"})
        self.assertEqual(rechecked["spec"]["escalate_at"], item["spec"]["escalate_at"])
        self.assertEqual(rechecked["due_phases"], ["delivery", "decision"])
        self.assertLessEqual(rechecked["progress"]["next_check_at"], rechecked["spec"]["escalate_at"])
        for phase in ("delivery", "decision"):
            intent = self.claim(self.current(item), "escalation", phase=phase)
            self.assertTrue(intent["send"])
            self.assertEqual(intent["spec"]["escalation_owner"], "coordinator:original")
            unknown = self.record(intent, "escalation", "UNKNOWN", phase=phase)
            self.assertFalse(self.claim(unknown, "escalation", phase=phase)["send"])
            delivered = self.record(unknown, "escalation", "DELIVERED", phase=phase)
            self.assertEqual(delivered["progress"]["escalations"][phase]["status"], "DELIVERED")
            self.assertFalse(self.claim(delivered, "escalation", phase=phase)["send"])
        approved = self.decide(self.current(item))
        execution = self.claim(approved, "escalation", phase="execution")
        self.assertTrue(execution["send"])
        self.assertFalse(self.claim(execution, "escalation", phase="execution")["send"])

    def test_existing_tick_raises_owned_escalations_once_without_claiming_delivery(self):
        item = self.prepare()
        self.now += timedelta(minutes=21)
        first = self.c.manage("tick", {})
        self.assertEqual({row["phase"] for row in first["approval_escalations"]}, {"delivery", "decision"})
        raised = self.current(item)
        for phase in ("delivery", "decision"):
            escalation = raised["progress"]["escalations"][phase]
            self.assertEqual(escalation["owner"], "coordinator:original")
            self.assertEqual(escalation["status"], "PENDING")
            self.assertNotIn("action_id", escalation)
        with sqlite3.connect(self.c.path) as db:
            event_count = db.execute("SELECT count(*) FROM events").fetchone()[0]
        self.assertNotIn("approval_escalations", self.c.manage("tick", {}))
        with sqlite3.connect(self.c.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], event_count)
        checked = self.c.manage("approval_recheck", {**self.key(item),
                               "expected_revision": raised["progress"]["revision"],
                               "next_check_at": "2026-11-07T01:00:00Z", "evidence_ref": "fixture:still-waiting"})
        self.assertEqual(checked["spec"]["delivery_due_at"], item["spec"]["delivery_due_at"])
        self.assertEqual(checked["spec"]["escalate_at"], item["spec"]["escalate_at"])
        self.assertNotIn("approval_escalations", self.c.manage("tick", {}))
        for phase in ("delivery", "decision"):
            intent = self.claim(self.current(item), "escalation", phase=phase)
            self.assertTrue(intent["send"])
            delivered = self.record(intent, "escalation", "DELIVERED", phase=phase)
            self.assertEqual(delivered["progress"]["escalations"][phase]["evidence_ref"], "fixture:DELIVERED")
        self.decide(self.current(item))  # synthetic trusted-host verifier, no live user-answer claim
        execution_tick = self.c.manage("tick", {})
        self.assertEqual([row["phase"] for row in execution_tick["approval_escalations"]], ["execution"])
        self.assertNotIn("approval_escalations", self.c.manage("tick", {}))
        current = self.current(item)
        self.assertEqual(current["progress"]["escalations"]["execution"]["owner"], "coordinator:original")
        for phase in ("delivery", "decision"):
            self.assertEqual(current["progress"]["escalations"][phase]["status"], "DELIVERED")

    def test_result_needs_owner_review_and_dot_cannot_close(self):
        item = self.record(self.claim(self.decide(self.prepare()), "execution"), "execution", "CHECKPOINT_APPLIED")
        unknown = self.c.manage("approval_result", {**self.key(item), "expected_revision": item["progress"]["revision"],
                               "result_kind": "UNKNOWN", "result_ref": "fixture:unconfirmed"})
        with self.assertRaisesRegex(CoordinatorError, "RESULT_REVIEW_REQUIRED"):
            self.c.manage("approval_close", {**self.key(item), "expected_revision": unknown["progress"]["revision"],
                          "acceptor_principal": "owner:alpha", "result_ref": "fixture:unconfirmed",
                          "disposition": "reviewed", "evidence_ref": "fixture:review"})
        result = self.c.manage("approval_result", {**self.key(item), "expected_revision": unknown["progress"]["revision"],
                              "result_kind": "EXECUTED", "result_ref": "fixture:execution-readback"})
        closure = {**self.key(result), "expected_revision": result["progress"]["revision"],
                   "acceptor_principal": "owner:alpha", "result_ref": "fixture:execution-readback",
                   "disposition": "accepted this maintenance scope", "evidence_ref": "fixture:owner-native-answer"}
        with self.assertRaisesRegex(CoordinatorError, "BUSINESS_ACCEPTOR_REQUIRED"):
            self.c.manage("approval_close", {**closure, "acceptor_principal": "dot:alpha"})
        closed = self.c.manage("approval_close", closure)
        self.assertTrue(closed["progress"]["closed"])
        self.assertEqual(self.c.manage("approval_close", closure)["progress"], closed["progress"])
        self.assertEqual(closed["progress"]["execution"]["result_kind"], "EXECUTED")
        for acceptor in ("cloud-dot", "dot:alpha"):
            with self.subTest(acceptor=acceptor), self.assertRaisesRegex(CoordinatorError, "BUSINESS_ACCEPTOR_REQUIRED"):
                self.prepare(approval_id="bad-acceptor", acceptor_principal=acceptor)

    def test_interrupted_execution_stays_due_until_original_executor_accepts(self):
        item = self.claim(self.decide(self.prepare()), "execution")
        self.now += timedelta(hours=2)
        interrupted = self.current(item)
        self.assertEqual(interrupted["progress"]["execution"]["status"], "INTENT")
        self.assertEqual(interrupted["due_phases"], ["execution"])
        raised = self.c.manage("tick", {})
        self.assertEqual([entry["phase"] for entry in raised["approval_escalations"]], ["execution"])
        self.assertNotIn("approval_escalations", self.c.manage("tick", {}))
        unknown = self.record(self.current(item), "execution", "UNKNOWN")
        self.assertEqual(unknown["due_phases"], ["execution"])
        self.assertFalse(self.claim(unknown, "execution")["send"])
        applied = self.record(unknown, "execution", "CHECKPOINT_APPLIED")
        self.assertEqual(applied["due_phases"], ["execution"])
        self.assertNotIn("approval_escalations", self.c.manage("tick", {}))
        accepted = self.record(applied, "execution", "ACCEPTED", evidence_ref="fixture:original-executor-receipt")
        self.assertEqual(accepted["progress"]["execution"]["handoff_ref"], "fixture:original-executor-receipt")
        self.assertEqual(accepted["due_phases"], [])

    def test_old_acceptor_cannot_make_a_new_acceptance_after_owner_handoff(self):
        item = self.record(self.claim(self.decide(self.prepare()), "execution"), "execution", "CHECKPOINT_APPLIED")
        self.activate("alpha", 1, "owner:replacement")
        # A late actual result remains recordable after the role changes.
        result = self.c.manage("approval_result", {**self.key(item), "expected_revision": item["progress"]["revision"],
                              "result_kind": "EXECUTED", "result_ref": "fixture:late-original-result"})
        closure = {**self.key(result), "expected_revision": result["progress"]["revision"],
                   "acceptor_principal": "owner:alpha", "result_ref": "fixture:late-original-result",
                   "disposition": "accepted", "evidence_ref": "fixture:old-owner-answer"}
        with self.assertRaisesRegex(CoordinatorError, "STALE_GENERATION"):
            self.c.manage("approval_close", closure)
        with self.assertRaisesRegex(CoordinatorError, "INVALID_TEXT"):
            self.c.manage("approval_close", {**closure, "acceptor_principal": "owner:replacement", "acceptor_generation": 2})
        closed = self.c.manage("approval_close", {**closure, "acceptor_principal": "owner:replacement", "acceptor_generation": 2,
                              "handoff_ref": "fixture:task-acceptance-handoff", "verification_ref": "fixture:verified-native-replacement"})
        self.assertFalse(closed["generation_current"])
        self.assertTrue(closed["progress"]["closed"])
        self.assertEqual(closed["progress"]["acceptance"]["acceptor_generation"], 2)

    def test_confirmed_execution_not_sent_reuses_original_scope_checkpoint_and_executor(self):
        original = self.decide(self.prepare())
        intent = self.claim(original, "execution")
        unknown = self.record(intent, "execution", "UNKNOWN")
        not_sent = self.record(unknown, "execution", "NOT_SENT", evidence_ref="fixture:verified-no-effect")
        self.assertEqual(not_sent["progress"]["execution"]["status"], "NOT_DISPATCHED")
        resumed = self.claim(not_sent, "execution")
        self.assertTrue(resumed["send"])
        self.assertNotEqual(resumed["action_id"], intent["action_id"])
        self.assertEqual(resumed["scope_hash"], original["scope_hash"])
        for field in ("checkpoint", "executor_ref", "dispatcher"):
            self.assertEqual(resumed["spec"][field], original["spec"][field])
        # Known nonexecution after a withdrawal stops this action instead of granting a retry.
        withdrawn = self.decide(resumed, "WITHDRAWN", supersedes_ref=original["progress"]["decision"]["source_ref"])
        stopped = self.record(withdrawn, "execution", "NOT_SENT", evidence_ref="fixture:verified-not-submitted")
        self.assertEqual(stopped["progress"]["execution"]["status"], "STOPPED")
        self.assertFalse(self.claim(stopped, "execution")["send"])

    def test_shared_followup_and_independent_branch_remain_due_while_approval_waits(self):
        for key in ("shared-observer", "independent-branch"):
            self.c.manage("role_followup_put", {"project_id": "alpha", "followup_key": key, "generation": 1,
                          "task_ref": self.spec()["task_ref"], "receipt_ref": "fixture:original-receipt",
                          "next_action": "Collect independently ready branches and unresolved items.",
                          "action_owner": "coordinator:original", "next_check_at": "2026-11-06T01:01:00Z"})
        waiting = self.prepare(followup_key="shared-observer")
        self.now += timedelta(minutes=2)
        due = self.c.manage("role_followup_list", {"project_id": "alpha", "due_only": True})["items"]
        self.assertEqual({row["followup_key"] for row in due}, {"shared-observer", "independent-branch"})
        for row in due:
            claim = self.c.manage("role_followup_claim", {"project_id": "alpha", "followup_key": row["followup_key"],
                                 "generation": 1, "expected_revision": row["revision"]})
            self.assertTrue(claim["send"])
        self.assertEqual(self.current(waiting)["progress"]["decision"]["status"], "PENDING")

    def test_only_one_execution_claim_across_connections(self):
        approved = self.decide(self.prepare())
        data = {**self.key(approved), "expected_revision": approved["progress"]["revision"], "stage": "execution"}
        def claim(_):
            return Coordinator(self.state, clock=lambda: self.now).manage("approval_claim", data)["send"]
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(claim, (1, 2))), [False, True])

    def test_old_ledger_remains_readable_without_mutating_its_results(self):
        old = self.root / "old"
        old.mkdir(mode=0o700)
        path = old / "project-coordinator.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript((ROOT / "tests/fixtures/coordinator-ledger-before-cloud-dot.sql").read_text())
            before = db.execute("SELECT * FROM results").fetchall()
            event_before = db.execute("SELECT * FROM events").fetchall()
        path.chmod(0o600)
        readonly = Coordinator(old, clock=lambda: self.now, read_only=True)
        self.assertEqual(readonly.manage("approval_list", {})["items"], [])
        with sqlite3.connect(path) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='approvals'").fetchone())
        upgraded = Coordinator(old, clock=lambda: self.now)
        self.assertEqual(upgraded.manage("approval_list", {})["items"], [])
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT * FROM results").fetchall(), before)
            self.assertEqual(db.execute("SELECT * FROM events").fetchall(), event_before)


if __name__ == "__main__":
    unittest.main()
