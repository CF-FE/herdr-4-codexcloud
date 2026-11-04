"""Local handoff only: all files and the real coordinator ledger live in a temporary directory."""

from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import coordinator_comment_intake as intake
from project_coordinator import Coordinator, CoordinatorError, canonical


class CommentIntakeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.inbox = self.root / "inbox"
        self.inbox.mkdir(mode=0o700)
        self.state = self.root / "state"
        self.note = {"project_id": "alpha", "record_key": "update-1", "issue_url": "https://github.com/CF-FE/alpha/issues/2",
                     "public_summary": "A finished, B pending.", "source_ref": "native:observed",
                     "authorization_ref": "source:only-a-draft"}

    def ledger(self):
        coordinator = Coordinator(self.state)
        coordinator.manage("register_project", {"project_id": "alpha", "repository": "CF-FE/alpha",
                           "project_ref": "project:alpha", "cloud_dot_principal": "cloud:alpha",
                           "report_route": "route:alpha"})
        return coordinator

    def test_isolated_cli_stage_no_state_and_tick_forces_confirmation(self):
        data = self.root / "input.json"
        data.write_text(json.dumps({**self.note, "needs_confirmation": False}))
        cmd = [sys.executable, "-I", "-B", str(SCRIPTS / "coordinator_comment_intake.py")]
        staged = json.loads(subprocess.check_output([*cmd, "stage", "--inbox", str(self.inbox), "--input", str(data)]))
        self.assertFalse(self.state.exists())
        path = self.inbox / staged["file"]
        self.assertEqual(path.read_bytes(), canonical({**self.note, "needs_confirmation": True}))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        # Even a file placed directly in the weaker ingress cannot authorize a GitHub write.
        path.write_text(json.dumps({**self.note, "needs_confirmation": False}))
        self.ledger()
        data.write_text('{"limit":1}')
        done = json.loads(subprocess.check_output([*cmd, "tick", "--inbox", str(self.inbox),
                                                  "--state-dir", str(self.state), "--input", str(data)]))
        self.assertIn("more_due", done)
        self.assertEqual(done["github_intake"]["imported"], 1)
        self.assertFalse(path.exists())
        records = Coordinator(self.state, read_only=True).manage("github_list", {"project_id": "alpha"})["records"]
        self.assertEqual([(r["record_key"], r["state"]) for r in records], [("update-1", "WAITING_CONFIRMATION")])

    def test_stable_filename_atomic_stage_and_conflict(self):
        def publish(_):
            try:
                return intake.stage(self.inbox, self.note)
            except CoordinatorError as exc:
                self.assertEqual(exc.code, "INBOX_BUSY")
                return None
        with ThreadPoolExecutor(3) as pool:
            staged = [s for s in pool.map(publish, range(6)) if s]
        self.assertTrue(staged)
        self.assertEqual(len({v["file"] for v in staged}), 1)
        self.assertEqual(intake.stage(self.inbox, {**self.note, "needs_confirmation": False}), staged[0])
        with self.assertRaisesRegex(CoordinatorError, "STAGED_RECORD_CONFLICT"):
            intake.stage(self.inbox, {**self.note, "public_summary": "different"})
        self.assertEqual(len(list(self.inbox.iterdir())), 1)

    def test_unsafe_entries_remain_untouched_and_good_entries_continue(self):
        c = self.ledger()
        other = self.root / "other.json"
        other.write_text(json.dumps(self.note))
        bad_names = ["github-" + str(i) * 64 + ".json" for i in range(5)]
        (self.inbox / bad_names[0]).symlink_to(other)
        os.mkfifo(self.inbox / bad_names[1], 0o600)
        (self.inbox / bad_names[2]).mkdir()
        os.link(other, self.inbox / bad_names[3])
        (self.inbox / bad_names[4]).write_bytes(b"{bad json")
        (self.inbox / bad_names[4]).chmod(0o600)
        rejected = intake.stage(self.inbox, {**self.note, "record_key": "wrong", "issue_url": "https://github.com/X/b/issues/1"})
        good = intake.stage(self.inbox, self.note)
        (self.inbox / intake.CURSOR).symlink_to(other)
        outcome = intake.tick(c, self.inbox, {})["github_intake"]
        self.assertEqual(outcome["imported"], 1)
        self.assertEqual({r["file"] for r in outcome["rejected"]}, set(bad_names + [rejected["file"]]))
        self.assertFalse((self.inbox / good["file"]).exists())
        self.assertEqual(json.loads(other.read_bytes()), self.note)
        with self.assertRaises(CoordinatorError):
            intake.read_file(self.inbox / bad_names[0])
        shortcut = self.root / "alias"
        shortcut.symlink_to(self.inbox)
        with self.assertRaisesRegex(CoordinatorError, "PRIVATE_INBOX_REQUIRED"):
            intake.stage(shortcut, self.note)
        self.inbox.chmod(0o755)
        try:
            with self.assertRaisesRegex(CoordinatorError, "PRIVATE_INBOX_REQUIRED"):
                intake.stage(self.inbox, self.note)
        finally:
            self.inbox.chmod(0o700)

    def test_bounded_batch_rotates_past_bad_head_and_keeps_transient_failures(self):
        c = self.ledger()
        (self.inbox / intake.CURSOR).mkdir()  # a foreground directory cannot poison the observer's cursor
        for i in range(intake.BATCH):
            path = self.inbox / f"github-{i:064x}.json"
            path.write_bytes(b"{}")
            path.chmod(0o600)
        staged = intake.stage(self.inbox, self.note)
        first = intake.tick(c, self.inbox, {})
        self.assertEqual((first["github_intake"]["imported"], len(first["github_intake"]["rejected"])), (0, intake.BATCH))
        self.assertTrue((self.state / intake.CURSOR).is_file())
        original = c.manage
        calls = []
        def busy(op, data):
            calls.append(op)
            if op == "github_enqueue":
                raise sqlite3.OperationalError("synthetic busy")
            return original(op, data)
        with patch.object(c, "manage", side_effect=busy):
            second = intake.tick(c, self.inbox, {})
        self.assertEqual(calls[0], "tick")
        self.assertEqual(second["github_intake"]["deferred"], 1)
        self.assertTrue((self.inbox / staged["file"]).exists())
        counts = [intake.tick(c, self.inbox, {})["github_intake"]["imported"] for _ in range(2)]
        self.assertEqual(sum(counts), 1)

    def test_suspended_foreground_lock_cannot_block_existing_tick_or_lose_a_draft(self):
        c = self.ledger()
        staged = intake.stage(self.inbox, self.note)
        cmd = [sys.executable, "-I", "-B", str(SCRIPTS / "coordinator_comment_intake.py")]
        data = self.root / "input.json"
        with intake.inbox_directory(self.inbox) as locked:
            fcntl.flock(locked, fcntl.LOCK_EX)
            data.write_text(json.dumps({**self.note, "record_key": "second"}))
            busy = subprocess.run([*cmd, "stage", "--inbox", str(self.inbox), "--input", str(data)],
                                  capture_output=True, timeout=5)
            self.assertEqual(json.loads(busy.stderr)["error"]["code"], "INBOX_BUSY")
            self.assertEqual(json.loads(data.read_bytes())["record_key"], "second")
            data.write_text("{}")
            out = json.loads(subprocess.check_output([*cmd, "tick", "--state-dir", str(self.state),
                             "--inbox", str(self.inbox), "--input", str(data)], timeout=5))
            self.assertIn("more_due", out)
            self.assertEqual(out["github_intake"]["deferred"], 1)
            self.assertTrue((self.inbox / staged["file"]).exists())
        self.assertEqual(intake.tick(c, self.inbox, {})["github_intake"]["imported"], 1)
        self.assertEqual(len(c.manage("github_list", {"project_id": "alpha"})["records"]), 1)

    def test_ancestor_replaced_mid_open_cannot_redirect_observer_cleanup(self):
        c = self.ledger()
        local, redirect = self.root / "local", self.root / "redirect"
        for parent in (local, redirect):
            parent.mkdir()
            (parent / "spool").mkdir(mode=0o700)
            name = intake.stage(parent / "spool", self.note)["file"]
        original_open = os.open
        saved = self.root / "original"
        def swap_at_last_component(path, flags, *args, **kwargs):
            if path == "spool" and not saved.exists():
                self.assertIsNotNone(kwargs.get("dir_fd"))
                local.rename(saved)
                local.symlink_to(redirect)
            return original_open(path, flags, *args, **kwargs)
        with patch.object(intake.os, "open", side_effect=swap_at_last_component):
            outcome = intake.tick(c, local / "spool", {})
        self.assertEqual(outcome["github_intake"]["imported"], 1)
        self.assertFalse((saved / "spool" / name).exists())
        self.assertTrue((redirect / "spool" / name).is_file())
        with self.assertRaisesRegex(CoordinatorError, "PRIVATE_INBOX_REQUIRED"):
            intake.stage(local / "spool", self.note)

    def test_commit_before_cleanup_crash_replays_same_record_and_absent_inbox_cannot_block_tick(self):
        c = self.ledger()
        staged = intake.stage(self.inbox, self.note)
        original = os.unlink
        def crash(path, *args, **kwargs):
            if path == staged["file"]:
                raise RuntimeError("simulated process death")
            return original(path, *args, **kwargs)
        with patch.object(intake.os, "unlink", side_effect=crash), self.assertRaisesRegex(RuntimeError, "process death"):
            intake.tick(c, self.inbox, {})
        recovered = Coordinator(self.state)
        self.assertTrue((self.inbox / staged["file"]).exists())
        self.assertEqual(intake.tick(recovered, self.inbox, {})["github_intake"]["imported"], 1)
        self.assertEqual(len(recovered.manage("github_list", {"project_id": "alpha"})["records"]), 1)
        events = recovered.manage("events", {"project_id": "alpha"})["events"]
        self.assertEqual(sum(r["kind"] == "GITHUB_RECORD_QUEUED" for r in events), 1)
        missing = intake.tick(recovered, self.root / "missing", {})
        self.assertIn("processed", missing)
        self.assertIn("error", missing["github_intake"])


if __name__ == "__main__":
    unittest.main()
