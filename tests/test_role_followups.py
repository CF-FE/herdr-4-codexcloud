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

    def claim(self, revision=0):
        return self.core.manage("role_followup_claim", dict(self.key, expected_revision=revision))

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
        later = self.record(0, "DEFER", "owner:working", next_check_at=future)
        self.assertEqual(self.record(0, "DEFER", "owner:working", next_check_at=future), later)
        self.assertFalse(self.listing(due_only=True))
        self.assertFalse(self.claim(1)["send"])
        self.core.clock = lambda: datetime(2026, 11, 6, tzinfo=timezone.utc)
        claimed = self.claim(1)
        self.assertTrue(claimed["send"])
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


if __name__ == "__main__":
    unittest.main()
