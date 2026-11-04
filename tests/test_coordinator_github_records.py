"""Exercise the privileged local recorder handoff with temporary state and synthetic readbacks."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/project_coordinator.py"
sys.path.insert(0, str(CLI.parent))
from project_coordinator import Coordinator, CoordinatorError, canonical_hash


class GithubRecordTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.c = Coordinator(self.state)
        for project in ("alpha", "beta"):
            self.c.manage("register_project", {"project_id": project, "repository": "CF-FE/" + project,
                          "project_ref": "project:" + project, "cloud_dot_principal": "cloud:" + project,
                          "report_route": "route:" + project})
        self.key = {"project_id": "alpha", "record_key": "progress-4"}
        self.issue = "https://github.com/CF-FE/alpha/issues/2"
        self.original = {**self.key, "issue_url": self.issue, "public_summary": "完成第一项。\n另两项待核。",
                         "source_ref": "private-source:4", "authorization_ref": "private-authority:1",
                         "needs_confirmation": False}

    def claim(self, revision, **changes):
        return self.c.manage("github_claim", {**self.key, "writer_login": "Recorder1",
                             "expected_revision": revision, **changes})

    def record(self, revision, outcome, **evidence):
        return self.c.manage("github_record", {**self.key, "outcome": outcome, "expected_revision": revision,
                             "evidence_ref": "native:" + outcome, **evidence})

    def submit_query(self, generation):
        return self.c.submit("cloud:alpha", {"project_id": "alpha", "request_id": "req", "job_id": "job",
                             "task_ref": self.issue, "idempotency_key": "job", "kind": "QUERY_BLOCKER",
                             "expected_generation": generation, "deadline": "2099-02-03T00:00:00Z",
                             "question": "Which input is missing?", "authorization_ref": "readonly-query",
                             "payload_hash": canonical_hash("query"), "evidence_refs": [],
                             "budget": {"contact_attempts": 1, "production_writes": 0, "audio_generations": 0}})

    def test_duplicate_and_exact_original_issue(self):
        item = self.c.manage("github_enqueue", self.original)
        self.assertNotIn("private-", item["comment_body"])
        self.assertEqual(self.c.manage("github_enqueue", self.original), item)
        for bad, code in [
            ({"public_summary": "Other"}, "GITHUB_RECORD_CONFLICT"),
            ({"record_key": "duplicate"}, "GITHUB_RECORD_DUPLICATE"),
            ({"project_id": "beta"}, "GITHUB_ISSUE_MISMATCH"),
            ({"issue_url": self.issue + "#issuecomment-12"}, "GITHUB_ISSUE_MISMATCH"),
            ({"issue_url": self.issue.replace("/issues/", "/pull/")}, "GITHUB_ISSUE_MISMATCH"),
            ({"public_summary": "Prepared <!-- coordinator-record:spoof -->"}, "INVALID_PUBLIC_SUMMARY"),
            ({"needs_confirmation": 1}, "INVALID_CONFIRMATION"),
        ]:
            with self.subTest(bad=bad), self.assertRaisesRegex(CoordinatorError, code):
                self.c.manage("github_enqueue", {**self.original, **bad})
        other = self.c.manage("github_list", {"project_id": "beta"})
        self.assertEqual(other["records"], [])
        with self.assertRaisesRegex(CoordinatorError, "GITHUB_RECORD_NOT_FOUND"):
            self.claim(0, project_id="beta")

    def test_registered_request_cross_checks_issue_even_within_same_repository(self):
        self.submit_query(0)
        with self.assertRaisesRegex(CoordinatorError, "GITHUB_ISSUE_MISMATCH"):
            self.c.manage("github_enqueue", {**self.original, "request_id": "req", "issue_url": self.issue + "1"})
        self.assertEqual(self.c.manage("github_enqueue", {**self.original, "request_id": "req"})["request_id"], "req")

    def test_waiting_for_confirmation_does_not_hold_other_records_or_coordinator(self):
        blocked = self.c.manage("github_enqueue", {**self.original, "needs_confirmation": True})
        self.assertEqual(blocked["state"], "WAITING_CONFIRMATION")
        self.assertFalse(self.claim(0)["send"])
        self.c.manage("github_enqueue", {**self.original, "record_key": "progress-5", "public_summary": "第二项已核。"})
        self.assertEqual([r["record_key"] for r in self.c.manage("github_list", {"project_id": "alpha",
                         "state": "PENDING"})["records"]], ["progress-5"])
        self.assertTrue(self.claim(0, record_key="progress-5")["send"])
        self.assertEqual(self.c.manage("tick", {})["processed"], 0)
        # A writer is awaiting confirmation on the first comment and has a live intent on the second.
        # The coordinator can still serve a new foreground request in full in the very same project.
        self.c.manage("activate_owner", {"project_id": "alpha", "expected_generation": 0,
                      "owner_principal": "owner", "endpoint": "native:owner", "appointment_ref": "ref:a",
                      "verification_ref": "ref:v", "handoff_ref": "ref:h", "release_ref": "ref:r"})
        self.submit_query(1)
        contact = self.c.claim_contact("alpha", "req", 1)
        self.assertTrue(contact["send"])
        action = contact["action_id"]
        self.c.record_contact("alpha", "req", action, "SUBMITTED", "fixture:submitted")
        self.c.owner_read("owner", "alpha", "req", action, 1, "native:owner", "fixture:read")
        report = {"blocker": "Input not ready", "impact": "One fixture item pending",
                  "evidence_refs": ["fixture:input"], "verified": ["missing input"], "unverified": [],
                  "next_owner": "fixture-owner", "next_condition": "Input received",
                  "observed_at": "2026-01-01T00:00:00Z"}
        result = self.c.reply("owner", "alpha", "req", action, 1, "native:owner", "fixture:reply", report)
        self.assertEqual(self.c.result("cloud:alpha", "alpha", "req"), result["delivery"])
        items = self.c.manage("github_list", {"project_id": "alpha"})["records"]
        self.assertEqual([(r["record_key"], r["state"]) for r in items],
                         [("progress-4", "WAITING_CONFIRMATION"), ("progress-5", "INTENT")])
        self.assertEqual(self.record(0, "AUTHORIZE")["state"], "PENDING")
        self.assertTrue(self.claim(1)["send"])
        self.assertEqual(self.record(2, "HOLD_UNSENT")["state"], "WAITING_CONFIRMATION")
        with self.assertRaisesRegex(CoordinatorError, "GITHUB_STATE_CONFLICT"):
            self.record(0, "AUTHORIZE")
        self.assertEqual(self.record(3, "CANCEL_UNSENT")["state"], "CANCELLED")
        self.assertFalse(self.claim(4)["send"])

    def test_one_claim_across_connections_recovery_exact_readback_no_duplicate(self):
        item = self.c.manage("github_enqueue", self.original)
        def writer(_):
            return Coordinator(self.state).manage("github_claim", {**self.key, "expected_revision": 0,
                                                                   "writer_login": "Recorder1"})["send"]
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(writer, [0, 1])), [False, True])
        self.c = Coordinator(self.state)
        self.assertFalse(self.claim(1)["send"])  # prior worker may already have issued the mutation
        unknown = self.record(1, "UNKNOWN")
        self.assertEqual(unknown["state"], "UNKNOWN")
        self.assertEqual(self.record(1, "UNKNOWN"), unknown)
        self.assertFalse(self.claim(2)["send"])
        readback = {"comment_url": self.issue + "#issuecomment-701", "observed_body": item["comment_body"],
                    "author_login": "recorder1"}
        for bad in [{"comment_url": self.issue + "3#issuecomment-701"}, {"author_login": "someone-else"},
                    {"observed_body": self.original["public_summary"]}]:
            with self.subTest(bad=bad), self.assertRaisesRegex(CoordinatorError, "GITHUB_READBACK_MISMATCH"):
                self.record(2, "READ_BACK", **{**readback, **bad})
        done = self.record(2, "READ_BACK", **readback)
        self.assertEqual((done["state"], done["comment_url"]), ("RECORDED", readback["comment_url"]))
        self.assertEqual(self.record(2, "READ_BACK", **readback), done)
        self.assertEqual(self.c.manage("github_enqueue", self.original), done)
        self.assertFalse(self.claim(3)["send"])
        events = self.c.manage("events", {"project_id": "alpha"})["events"]
        self.assertEqual(sum(e["kind"] == "GITHUB_RECORD_INTENT" for e in events), 1)

    def test_retry_requires_original_attempt_closed_and_absence_evidence(self):
        self.c.manage("github_enqueue", self.original)
        first = self.claim(0)
        self.record(1, "UNKNOWN")
        with self.assertRaisesRegex(CoordinatorError, "INVALID_FIELDS"):
            self.record(2, "RECONCILED_ABSENT")
        with self.assertRaisesRegex(CoordinatorError, "GITHUB_STATE_CONFLICT"):
            self.record(2, "AUTHORIZE")
        ready = self.record(2, "RECONCILED_ABSENT", closed_attempt_ref="native:terminated-before-commit")
        self.assertEqual(ready["state"], "PENDING")
        self.assertFalse(self.claim(0)["send"])
        second = self.claim(ready["revision"])
        self.assertTrue(second["send"])
        self.assertEqual(second["comment_body"], first["comment_body"])
        with self.assertRaisesRegex(CoordinatorError, "GITHUB_STATE_CONFLICT"):
            self.record(1, "HOLD_UNSENT")

    def test_cli_and_read_only_upgrade_from_previous_schema(self):
        with sqlite3.connect(self.c.path) as db:
            db.execute("DROP TABLE github_records")
        def cli(op, data):
            path = self.root / "input.json"
            path.write_text(json.dumps(data))
            return json.loads(subprocess.check_output([sys.executable, "-B", str(CLI), "--state-dir", str(self.state),
                                                       op, "--input", str(path)], text=True))
        self.assertEqual(cli("github_list", {"project_id": "alpha"})["records"], [])
        with sqlite3.connect(self.c.path) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='github_records'").fetchone())
        self.assertEqual(cli("github_enqueue", self.original)["state"], "PENDING")
        item = cli("github_claim", {**self.key, "expected_revision": 0, "writer_login": "recorder"})
        self.assertTrue(item["send"])
        self.assertEqual(cli("github_record", {**self.key, "expected_revision": 1, "outcome": "UNKNOWN",
                         "evidence_ref": "local:reply-lost"})["state"], "UNKNOWN")
        self.assertEqual(cli("github_list", {"project_id": "alpha", "record_key": "progress-4"}
                             )["records"][0]["revision"], 2)


if __name__ == "__main__":
    unittest.main()
