"""Synthetic notice projection tests, without hook installation or live decisions."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from coordinator_approvals import VerifiedDecision, receive_decision
from project_coordinator import Coordinator, CoordinatorError, canonical_hash
from test_coordinator_approvals import fixture_spec


class ApprovalNoticeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = datetime(2026, 11, 6, 1, tzinfo=timezone.utc)
        self.c = Coordinator(self.root / "state", clock=lambda: self.now)
        for project in ("alpha", "beta"):
            self.c.manage("register_project", {"project_id": project, "repository": "CF-FE/" + project,
                          "project_ref": "project:" + project, "cloud_dot_principal": "dot:" + project,
                          "report_route": "route:" + project})
            self.activate(project, 0, "owner:" + project)
        self.selector = {"thread_id": "bound-fixture-thread", "host_id": "bound-host", "project_ids": ["alpha"]}

    def activate(self, project, generation, principal):
        return self.c.manage("activate_owner", {"project_id": project, "expected_generation": generation,
                             "owner_principal": principal, "endpoint": "native:" + principal,
                             "appointment_ref": "fixture:appointment", "verification_ref": "fixture:verification",
                             "handoff_ref": "fixture:handoff", "release_ref": "fixture:release"})

    def prepare(self, project="alpha", **changes):
        spec = fixture_spec(project, notice_summary="申请批准本单已冻结的维护范围。", **changes)
        return self.c.manage("approval_prepare", {"spec": spec})

    def key(self, item):
        return {key: item["spec"][key] for key in ("project_id", "approval_id", "scope_version")}

    def read(self, **changes):
        return self.c.manage("approval_notice_snapshot", {**self.selector, **changes})

    def decide(self, item, decision="APPROVED", **changes):
        proof = VerifiedDecision(**{**self.key(item), "scope_hash": item["scope_hash"], "decision": decision,
                                    "approver_principal": "user:fixture", "relay_principal": "relay:fixture",
                                    "source_ref": "fixture:answer:" + decision, "decided_at": self.now.isoformat(), **changes})
        return receive_decision(self.c, self.key(item), "fixture:host-handle", lambda *_: proof)

    def claim(self, item):
        return self.c.manage("approval_claim", {**self.key(item), "expected_revision": item["progress"]["revision"],
                             "stage": "execution"})

    def record(self, item, outcome):
        return self.c.manage("approval_record", {**self.key(item), "expected_revision": item["progress"]["revision"],
                             "stage": "execution", "action_id": item["progress"]["execution"]["action_id"],
                             "outcome": outcome, "evidence_ref": "fixture:execution:" + outcome})

    def test_binding_filters_and_projection_do_not_disclose_private_spec(self):
        item = self.prepare(scope={"secret_scope": "NEVER_EXPOSE_SCOPE"}, authorization_gap="PRIVATE_GAP")
        self.prepare("beta")
        self.prepare(approval_id="wrong-thread", route={**item["spec"]["route"], "thread_id": "other-thread"})
        self.prepare(approval_id="wrong-host", route={**item["spec"]["route"], "host_id": "other-host"})
        self.c.manage("approval_prepare", {"spec": fixture_spec(approval_id="no-public-summary")})
        result = self.read()
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(len(result["notices"]), 1)
        notice = result["notices"][0]
        self.assertEqual(set(notice), {"notice_id", "project_id", "approval_id", "scope_version", "scope_hash",
                                      "phase", "summary", "task_ref", "action_owner", "due_at"})
        self.assertEqual(notice["notice_id"], canonical_hash([item["scope_hash"], "decision", notice["summary"]]))
        self.assertEqual(notice["summary"], item["spec"]["notice_summary"])
        self.assertEqual(result, self.read())
        public = json.dumps(result)
        for private in ("NEVER_EXPOSE_SCOPE", "PRIVATE_GAP", "/fixture/workflow.py", "/fixture/state.json",
                        "user:fixture", "relay:fixture", "fixture:route-authority"):
            self.assertNotIn(private, public)
        for selector in ({"thread_id": "unbound"}, {"host_id": "unbound"}, {"project_ids": ["unknown"]}):
            self.assertEqual(self.read(**selector)["notices"], [])
        self.assertEqual([row["project_id"] for row in self.read(project_ids=["beta"])["notices"]], ["beta"])

    def test_phase_changes_keep_unknown_execution_visible_until_actual_handoff(self):
        item = self.prepare()
        initial = self.read()["notices"][0]
        self.assertEqual(initial["phase"], "decision")
        approved = self.decide(item)
        execution = self.read()["notices"][0]
        self.assertEqual(execution["phase"], "execution")
        self.assertNotEqual(execution["notice_id"], initial["notice_id"])
        self.assertEqual(execution["action_owner"], item["spec"]["dispatcher"])
        intent = self.claim(approved)
        self.assertEqual(self.read()["notices"][0]["notice_id"], execution["notice_id"])
        unknown = self.record(intent, "UNKNOWN")
        self.assertEqual(self.read()["notices"][0]["phase"], "execution")
        checkpoint = self.record(unknown, "CHECKPOINT_APPLIED")
        self.assertEqual(self.read()["notices"][0]["phase"], "execution")
        accepted = self.record(checkpoint, "ACCEPTED")
        self.assertEqual(self.read()["notices"], [])
        unknown_after_handoff = self.record(accepted, "UNKNOWN")
        self.assertEqual(self.read()["notices"], [])
        result = self.c.manage("approval_result", {**self.key(item), "expected_revision": unknown_after_handoff["progress"]["revision"],
                              "result_kind": "EXECUTED", "result_ref": "fixture:actual-result"})
        review = self.read()["notices"][0]
        self.assertEqual((review["phase"], review["action_owner"]), ("review", "project_owner"))
        self.assertNotIn("owner:alpha", json.dumps(review))
        self.c.manage("approval_close", {**self.key(item), "expected_revision": result["progress"]["revision"],
                      "acceptor_principal": "owner:alpha", "result_ref": "fixture:actual-result",
                      "disposition": "accepted", "evidence_ref": "fixture:native-review"})
        self.assertEqual(self.read()["notices"], [])

    def test_conflict_is_visible_as_a_decision_with_fixed_clarification_text(self):
        item = self.prepare()
        initial_id = self.read()["notices"][0]["notice_id"]
        self.decide(self.decide(item), "REJECTED")
        notice = self.read()["notices"][0]
        self.assertNotEqual(notice["notice_id"], initial_id)
        self.assertEqual(notice["phase"], "decision")
        self.assertIn("决定存在冲突", notice["summary"])
        self.assertIn("待有权审批者澄清", notice["summary"])
        self.assertNotIn("fixture:answer", json.dumps(notice))

    def test_superseded_and_stale_generations_are_not_projected(self):
        first = self.prepare()
        second = self.prepare(scope_version=2)
        self.assertEqual([row["scope_version"] for row in self.read()["notices"]], [2])
        self.assertNotEqual(first["scope_hash"], second["scope_hash"])
        self.activate("alpha", 1, "owner:new")
        self.assertEqual(self.read()["notices"], [])

    def test_pagination_is_bounded_and_does_not_claim_or_write(self):
        for key in ("first", "second", "third"):
            self.prepare(approval_id=key)
        before = self.c.path.read_bytes()
        readonly = Coordinator(self.c.path.parent, clock=lambda: self.now, read_only=True)
        first = readonly.manage("approval_notice_snapshot", {**self.selector, "limit": 2})
        self.assertTrue(first["more"])
        second = readonly.manage("approval_notice_snapshot", {**self.selector, "limit": 2, "after": first["next_after"]})
        self.assertFalse(second["more"])
        self.assertEqual([row["approval_id"] for row in first["notices"] + second["notices"]], ["first", "second", "third"])
        self.assertEqual(self.c.path.read_bytes(), before)
        self.assertTrue(all(item["progress"]["delivery"]["status"] == "PENDING"
                            for item in self.c.manage("approval_list", {})["items"]))

    def test_explicit_binding_and_pagination_boundaries_are_required(self):
        invalid = ({"project_ids": []}, {"project_ids": ["alpha"] * 33}, {"project_ids": ["alpha", "alpha"]},
                   {"project_ids": "alpha"}, {"project_ids": [1]}, {"thread_id": ""}, {"host_id": None},
                   {"after": True}, {"after": -1}, {"limit": True}, {"limit": 0}, {"limit": 101})
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(CoordinatorError):
                self.read(**change)
        with self.assertRaisesRegex(CoordinatorError, "INVALID_FIELDS"):
            self.c.manage("approval_notice_snapshot", {"thread_id": "bound-fixture-thread", "host_id": "bound-host"})

    def test_old_database_without_table_is_read_only_and_returns_empty(self):
        old = self.root / "old"
        old.mkdir(mode=0o700)
        path = old / "project-coordinator.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript((ROOT / "tests/fixtures/coordinator-ledger-before-cloud-dot.sql").read_text())
        path.chmod(0o600)
        before = hashlib.sha256(path.read_bytes()).digest()
        readonly = Coordinator(old, read_only=True)
        result = readonly.manage("approval_notice_snapshot", self.selector)
        self.assertEqual(result, {"schema_version": 1, "notices": [], "next_after": 0, "more": False})
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
        with sqlite3.connect(path) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='approvals'").fetchone())


if __name__ == "__main__":
    unittest.main()
