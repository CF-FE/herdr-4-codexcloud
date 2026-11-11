from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.project_coordinator import Coordinator, CoordinatorError


class RoleFollowupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name) / "state"
        self.clock = lambda: datetime(2026, 11, 4, 13, tzinfo=timezone.utc)
        self.core = Coordinator(self.state, clock=self.clock)
        self.core.manage("register_project", {"project_id": "demo", "repository": "CF-FE/demo",
                         "project_ref": "projects/1", "cloud_dot_principal": "dot", "report_route": "local"})
        self.appoint(0)
        self.key = {"project_id": "demo", "followup_key": "read-only-receipt", "generation": 1}
        self.original = dict(self.key, task_ref="https://github.com/CF-FE/demo/issues/2", receipt_ref="native:original",
                             next_action="核原只读回执并向负责人汇报", action_owner="project-coordinator",
                             next_check_at="2026-11-04T12:59:00Z")

    def appoint(self, old):
        self.core.manage("activate_owner", {"project_id": "demo", "expected_generation": old,
                         "owner_principal": f"owner:{old+1}", "endpoint": f"native:{old+1}",
                         "appointment_ref": "appoint", "verification_ref": "verify",
                         "handoff_ref": "handoff", "release_ref": "release"})

    def put(self, **changes):
        return self.core.manage("role_followup_put", {**self.original, **changes})

    def claim(self, revision=0, **observation):
        return self.core.manage("role_followup_claim", dict(self.key, expected_revision=revision, **observation))

    def record(self, revision, outcome, ref, **extra):
        return self.core.manage("role_followup_record", dict(self.key, expected_revision=revision, outcome=outcome,
                                                               evidence_ref=ref, **extra))

    def listing(self, **query):
        return self.core.manage("role_followup_list", query)["items"]

    def test_restart_replay_conflict_terminal_receipt_without_a_mailbox_request(self):
        original = self.put()
        self.assertEqual((original["state"], original["generation_current"]), ("PENDING", True))
        self.assertEqual(self.listing(due_only=True), [original])
        self.core = Coordinator(self.state, clock=self.clock)
        self.assertEqual(self.put(), original)
        with self.assertRaises(CoordinatorError):
            self.put(receipt_ref="native:unrelated")
        with self.assertRaises(CoordinatorError):
            self.put(production_released=True)
        with self.assertRaises(CoordinatorError):
            self.record(0, "CLOSED", "native:original")
        closed = self.record(0, "CLOSED", "native:verified-new-turn")
        self.assertEqual(self.put(), closed)
        self.assertEqual(self.record(0, "CLOSED", "native:verified-new-turn"), closed)
        self.assertEqual(self.listing(), [])
        self.assertEqual(self.listing(project_id="demo", followup_key=self.key["followup_key"]), [closed])
        self.assertFalse(self.claim(1)["send"])
        with sqlite3.connect(self.core.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM requests").fetchone()[0], 0)

    def test_one_reminder_can_be_claimed_and_unknown_is_never_implicitly_retried(self):
        self.put()
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(self.claim, [0] * 4))
        self.assertEqual(sum(c["send"] for c in claims), 1)
        action = claims[0]["action_id"]
        self.assertTrue(all(c["action_id"] == action for c in claims))
        with self.assertRaises(CoordinatorError):
            self.record(1, "UNKNOWN", "lost", action_id="wrong")
        unknown = self.record(1, "UNKNOWN", "transport:lost", action_id=action)
        self.assertEqual(unknown["state"], "UNKNOWN")
        self.assertFalse(self.claim(2)["send"])
        self.assertEqual(self.listing(due_only=True), [])
        self.assertEqual(self.listing(project_id="demo", generation=1), [unknown])
        sent = self.record(2, "SUBMITTED", "transport:found", action_id=action)
        self.assertEqual(sent["state"], "SUBMITTED")
        self.assertEqual(self.record(3, "CLOSED", "native:confirmed-owner-turn")["state"], "CLOSED")

    def test_defer_and_confirmed_not_sent_advance_checkpoint_without_duplicate_sends(self):
        self.put()
        future = "2026-11-05T11:00:00Z"
        with self.assertRaisesRegex(CoordinatorError, "INVALID_CHECKPOINT"):
            self.record(0, "DEFER", "owner:working", next_check_at=self.original["next_check_at"])
        later = self.record(0, "DEFER", "owner:working", next_check_at=future)
        self.assertEqual(self.record(0, "DEFER", "owner:working", next_check_at=future), later)
        self.assertFalse(self.listing(due_only=True))
        self.assertFalse(self.claim(1)["send"])
        self.core.clock = lambda: datetime(2026, 11, 6, tzinfo=timezone.utc)
        claimed = self.claim(1)
        self.assertTrue(claimed["send"])
        with self.assertRaisesRegex(CoordinatorError, "INVALID_CHECKPOINT"):
            self.record(2, "NOT_SENT", "native:closed-without-send", action_id=claimed["action_id"],
                        next_check_at=future)
        next_time = "2026-11-07T11:00:00Z"
        self.record(2, "NOT_SENT", "native:closed-without-send", action_id=claimed["action_id"], next_check_at=next_time)
        self.core.clock = lambda: datetime(2026, 11, 8, tzinfo=timezone.utc)
        fresh = self.claim(3)
        self.assertTrue(fresh["send"])
        self.assertNotEqual(fresh["action_id"], claimed["action_id"])

    def test_handoff_never_retargets_old_work_and_exact_lookup_keeps_history(self):
        self.put()
        action = self.claim()["action_id"]
        self.record(1, "SUBMITTED", "native:submitted", action_id=action)
        self.appoint(1)
        self.assertEqual(self.listing(due_only=True), [])
        with self.assertRaises(CoordinatorError):
            self.claim()
        with self.assertRaises(CoordinatorError):
            self.put(generation=2)
        for outcome, extra in (("CLOSED", {}), ("RECHECK", {"next_check_at": "2026-11-06T00:00:00Z"})):
            with self.assertRaisesRegex(CoordinatorError, "STALE_GENERATION"):
                self.record(2, outcome, "native:late", **extra)
        history = self.listing(project_id="demo", followup_key=self.key["followup_key"])
        self.assertFalse(history[0]["generation_current"])
        self.assertEqual(self.listing(project_id="demo", generation=2), [])

    def test_a_verified_handled_reminder_can_set_a_distinct_future_recheck(self):
        self.put()
        old_action = self.claim()["action_id"]
        future = {"next_check_at": "2026-11-06T00:00:00Z"}
        self.record(1, "UNKNOWN", "native:timeout", action_id=old_action)
        with self.assertRaisesRegex(CoordinatorError, "FOLLOWUP_STATE_CONFLICT"):
            self.record(2, "RECHECK", "native:owner-still-busy", **future)
        self.record(2, "SUBMITTED", "transport:reconciled", action_id=old_action)
        with self.assertRaisesRegex(CoordinatorError, "INVALID_CHECKPOINT"):
            self.record(3, "RECHECK", "native:handled-owner-still-busy", next_check_at=self.original["next_check_at"])
        for existing in ("native:original", "transport:reconciled", old_action):
            with self.assertRaisesRegex(CoordinatorError, "HANDLING_RECEIPT_REQUIRED"):
                self.record(3, "RECHECK", existing, **future)
        recheck = self.record(3, "RECHECK", "native:handled-owner-still-busy", **future)
        self.assertEqual(self.record(3, "RECHECK", "native:handled-owner-still-busy", **future), recheck)
        self.assertEqual((recheck["state"], recheck["action_id"]), ("PENDING", None))
        self.assertFalse(self.listing(due_only=True))
        self.core.clock = lambda: datetime(2026, 11, 7, tzinfo=timezone.utc)
        second = self.claim(4)
        self.assertTrue(second["send"])
        self.assertNotEqual(second["action_id"], old_action)
        self.assertFalse(self.claim(4)["send"])
        self.record(5, "SUBMITTED", "native:second-submission", action_id=second["action_id"])
        with self.assertRaisesRegex(CoordinatorError, "HANDLING_RECEIPT_REQUIRED"):
            self.record(6, "RECHECK", "native:handled-owner-still-busy", next_check_at="2026-11-08T00:00:00Z")

    def test_read_only_old_ledger_does_not_migrate_and_bounded_pages_cover_all(self):
        with sqlite3.connect(self.core.path) as db:
            db.execute("DROP TABLE role_followups")
        path = self.state.parent / "input.json"
        path.write_text(json.dumps({"project_id": "demo"}))
        script = Path(__file__).resolve().parents[1] / "scripts/project_coordinator.py"
        output = subprocess.check_output([sys.executable, "-I", "-B", str(script), "--state-dir", str(self.state),
                                          "role_followup_list", "--input", str(path)])
        self.assertEqual(json.loads(output)["items"], [])
        with sqlite3.connect(self.core.path) as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='role_followups'").fetchone())
        self.core = Coordinator(self.state, clock=self.clock)
        for i in range(3):
            self.put(followup_key=f"item-{i}", receipt_ref=f"receipt:{i}")
        page = self.core.manage("role_followup_list", {"limit": 2, "due_only": True})
        self.assertTrue(page["more"])
        self.assertEqual([r["followup_key"] for r in self.listing(due_only=True, after=page["next_after"])], ["item-2"])

    def test_old_schema_and_original_registration_survive_upgrade(self):
        registered = self.put()
        self.claim()
        with sqlite3.connect(self.core.path) as db:
            db.execute("ALTER TABLE role_followups DROP COLUMN trigger_spec")
            db.execute("ALTER TABLE role_followups DROP COLUMN last_trigger")
        legacy = Coordinator(self.state, clock=self.clock, read_only=True)
        before = legacy.manage("role_followup_list", self.key)["items"][0]
        with sqlite3.connect(self.core.path) as db:
            self.assertNotIn("trigger_spec", {row[1] for row in db.execute("PRAGMA table_info(role_followups)")})
        self.core = Coordinator(self.state, clock=self.clock)
        self.assertEqual(self.put(), before)
        self.assertEqual(before["revision"], registered["revision"] + 1)
        self.assertFalse(self.claim(before["revision"])["send"])

    def test_trigger_claim_is_atomic_and_recheck_consumes_only_claimed_fingerprint(self):
        spec = {"task_id": "original-task", "path": "original/decision.json", "fields": ["decision", "id"],
                "baseline_sha256": "a" * 64, "deadline_at": "2026-11-05T12:00:00Z"}
        first = self.put(trigger=spec)
        self.assertEqual(first, self.put(trigger={**spec, "fields": ["id", "decision"],
                                                  "deadline_at": "2026-11-05T20:00:00+08:00"}))
        self.assertFalse(self.claim(evidence_sha256="a" * 64)["send"])
        self.assertFalse(self.claim(source_reason="EVIDENCE_MISSING")["send"])
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: self.claim(evidence_sha256="b" * 64), range(4)))
        self.assertEqual(sum(item["send"] for item in claims), 1)
        action = claims[0]["action_id"]
        self.assertEqual(claims[0]["last_trigger"], {"kind": "EVIDENCE_CHANGED", "evidence_sha256": "b" * 64})
        self.record(1, "UNKNOWN", "transport:lost", action_id=action)
        self.record(2, "SUBMITTED", "transport:reconciled", action_id=action)
        self.assertFalse(self.claim(3, evidence_sha256="c" * 64)["send"])
        with self.assertRaisesRegex(CoordinatorError, "ACTION_MISMATCH"):
            self.record(1, "RECHECK", "native:handled", action_id="wrong", next_check_at="2026-11-04T14:00:00Z")
        with self.assertRaisesRegex(CoordinatorError, "FOLLOWUP_STATE_CONFLICT"):
            self.record(0, "RECHECK", "native:handled", action_id=action, next_check_at="2026-11-04T14:00:00Z")
        recheck = {"action_id": action, "next_check_at": "2026-11-04T14:00:00Z"}
        updated = self.record(1, "RECHECK", "native:handled", **recheck)
        self.assertEqual(updated, self.record(1, "RECHECK", "native:handled", **recheck))
        self.assertEqual(updated["revision"], 4)
        self.assertEqual(updated["trigger"]["baseline_sha256"], "b" * 64)
        self.core = Coordinator(self.state, clock=lambda: datetime(2026, 11, 4, 15, tzinfo=timezone.utc))
        self.assertFalse(self.claim(4, evidence_sha256="b" * 64)["send"])
        self.assertTrue(self.claim(4, evidence_sha256="c" * 64)["send"])

    def test_trigger_source_is_an_explicit_scoped_json_path(self):
        spec = {"task_id": "task", "path": "decision.json", "fields": ["decision"], "baseline_sha256": None}
        for bad in ({"task_id": "../other"}, {"path": "/tmp/decision.json"}, {"path": "dir/../decision.json"},
                    {"path": "dir//decision.json"}, {"fields": ["decision", "decision"]},
                    {"baseline_sha256": "not-a-hash"}, {"deadline_at": "2026-11-04T12:58:59Z"}):
            with self.subTest(bad=bad), self.assertRaises(CoordinatorError):
                self.put(trigger={**spec, **bad})
        self.assertEqual(self.listing(), [])

    def test_evidence_recheck_cannot_postpone_the_unconsumed_deadline(self):
        deadline = "2026-11-04T13:05:00+00:00"
        spec = {"task_id": "task", "path": "decision.json", "fields": ["decision"],
                "baseline_sha256": "a" * 64, "deadline_at": deadline}
        self.put(trigger=spec)
        claim = self.claim(evidence_sha256="b" * 64)
        self.assertEqual(claim["last_trigger"]["kind"], "EVIDENCE_CHANGED")
        action = claim["action_id"]
        self.record(1, "SUBMITTED", "transport:known", action_id=action)
        pending = self.record(2, "RECHECK", "native:handled", action_id=action,
                              next_check_at="2026-11-04T16:00:00Z")
        self.assertEqual(pending["next_check_at"], deadline)
        self.assertEqual(pending["trigger"]["deadline_at"], deadline)
        self.assertEqual(self.listing(due_only=True), [])
        self.core.clock = lambda: datetime(2026, 11, 4, 13, 6, tzinfo=timezone.utc)
        self.assertEqual(self.listing(due_only=True), [pending])
        due = self.claim(3, evidence_sha256="b" * 64)
        self.assertTrue(due["send"])
        self.assertEqual(due["last_trigger"]["kind"], "DEADLINE")
        after_backoff = self.record(4, "NOT_SENT", "native:presend-rejected", action_id=due["action_id"],
                                    next_check_at="2026-11-04T13:07:00Z")
        self.assertEqual(after_backoff["trigger"]["deadline_at"], deadline)
        self.assertEqual(self.listing(due_only=True), [])

    def test_legacy_driver_intent_for_trigger_item_requires_exact_receipt_to_rearm(self):
        spec = {"task_id": "task", "path": "decision.json", "fields": ["decision"], "baseline_sha256": None}
        self.put(trigger=spec)
        action = self.claim(evidence_sha256="b" * 64)["action_id"]
        self.record(1, "SUBMITTED", "transport:known", action_id=action)
        with sqlite3.connect(self.core.path) as db:
            db.execute("UPDATE role_followups SET last_trigger=NULL")
        self.assertEqual(self.listing()[0]["blocked_reason"], "FOLLOWUP_TRIGGER_RECEIPT_MISSING")
        extra = {"next_check_at": "2026-11-04T14:00:00Z"}
        with self.assertRaisesRegex(CoordinatorError, "FOLLOWUP_STATE_CONFLICT"):
            self.record(2, "RECHECK", "native:handled", **extra)
        self.assertEqual(self.record(2, "RECHECK", "native:handled", action_id=action, **extra)["trigger"], spec)


if __name__ == "__main__":
    unittest.main()
