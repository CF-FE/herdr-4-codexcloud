"""Coordinator contract tests use two independent OS processes and temporary local ledgers."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from project_coordinator import Coordinator, CoordinatorError, canonical_hash


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
DEADLINE = "2030-01-02T00:00:00Z"


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.c = self.reopen()
        for project in ("alpha", "beta"):
            self.c.manage("register_project", {"project_id": project, "repository": "CF-FE/" + project,
                          "project_ref": "project:" + project, "g8_principal": "g8:" + project,
                          "report_route": "g8-route:" + project})

    def reopen(self):
        return Coordinator(self.state, clock=lambda: NOW)

    def binding(self, name="alice", generation=0, project="alpha"):
        return {"project_id": project, "expected_generation": generation, "owner_principal": "owner:" + name,
                "endpoint": "native:" + name, "appointment_ref": "fixed-appointment:" + name,
                "verification_ref": "verified-channel:" + name, "handoff_ref": "handoff-accepted:" + name,
                "release_ref": "original-writer-release-or-vacancy:" + name}

    def request(self, project="alpha", generation=1):
        return {"project_id": project, "request_id": "query-1", "job_id": "job-" + project,
                "task_ref": "authority:" + project, "idempotency_key": "original-query", "kind": "QUERY_BLOCKER",
                "expected_generation": generation, "deadline": DEADLINE, "question": "Which input batch is blocked?",
                "authorization_ref": "read-only-authority", "payload_hash": canonical_hash({"project": project}),
                "evidence_refs": ["batch-ref:v1"], "budget": {"contact_attempts": 1, "production_writes": 0,
                                                             "audio_generations": 0}}

    def contact(self):
        self.c.manage("activate_owner", self.binding())
        self.c.submit("g8:alpha", self.request())
        return self.c.claim_contact("alpha", "query-1", 1)

    def report(self):
        return {"blocker": "Awaiting review of the fixed batch.", "impact": "Technical count remains partial.",
                "evidence_refs": ["batch-ref:v1"], "verified": ["one item read"], "unverified": ["remaining items"],
                "next_owner": "independent-reviewer", "next_condition": "Reviewer accepts assigned batch",
                "observed_at": "2030-01-01T00:00:00Z"}

    def denied(self, code, function, *args, **kwargs):
        with self.assertRaises(CoordinatorError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_project_isolation_idempotency_and_input_integrity(self):
        for project in ("alpha", "beta"):
            self.c.manage("activate_owner", self.binding(project=project))
            original = self.request(project)
            first = self.c.submit("g8:" + project, original)
            replay = self.reopen().submit("g8:" + project, deepcopy(original))
            self.assertEqual(first["job_id"], replay["job_id"])
            self.assertEqual(first["payload_hash"], original["payload_hash"])
            changed = dict(original, question="different batch")
            self.denied("IDEMPOTENCY_CONFLICT", self.c.submit, "g8:" + project, changed)
        for target in ("alpha", "missing"):
            self.denied("ACCESS_DENIED", self.c.status, "g8:beta", target, "query-1")
            self.denied("ACCESS_DENIED", self.c.result, "g8:beta", target, "query-1")
            self.denied("ACCESS_DENIED", self.c.cancel, "g8:beta", target, "query-1", "x")
        self.denied("ACCESS_DENIED", self.c.submit, "transport:shared-account", self.request())
        self.denied("INVALID_FIELDS", self.c.submit, "g8:alpha", dict(self.request(), principal="owner:alice"))
        self.denied("OPERATION_NOT_ALLOWED", self.c.submit, "g8:alpha", dict(self.request(), kind="GENERATE_AUDIO"))
        self.denied("INVALID_BUDGET", self.c.submit, "g8:alpha",
                    dict(self.request(), budget={"contact_attempts": 1, "production_writes": 1, "audio_generations": 0}))
        self.denied("DEADLINE_EXPIRED", self.c.submit, "g8:alpha",
                    dict(self.request(), request_id="old", idempotency_key="other", deadline="2029-01-01T00:00:00Z"))

    def test_owner_read_reply_g8_exact_read_and_unblock_are_independent(self):
        action = self.contact()["action_id"]
        self.c.record_contact("alpha", "query-1", action, "SUBMITTED", "native:sender-receipt")
        self.assertFalse(self.c.status("g8:alpha", "alpha", "query-1")["owner_read"])
        self.denied("ACCESS_DENIED", self.c.owner_read, "g8:alpha", "alpha", "query-1", action, 1, "native:alice", "fake")
        self.denied("ACCESS_DENIED", self.c.reply, "owner:alice", "alpha", "query-1", action, 1,
                    "other-endpoint", "native:reply", self.report())
        self.assertTrue(self.c.owner_read("owner:alice", "alpha", "query-1", action, 1, "native:alice",
                                          "native:read")["accepted"])
        args = ("owner:alice", "alpha", "query-1", action, 1, "native:alice", "native:reply", self.report())
        delivered = self.c.reply(*args)["delivery"]
        again = self.reopen().reply(*args)["delivery"]
        self.assertEqual(delivered, again)
        self.assertEqual(delivered["result_hash"], canonical_hash(delivered["result"]))
        self.assertEqual(delivered["payload_hash"], self.request()["payload_hash"])
        self.assertEqual(self.reopen().result("g8:alpha", "alpha", "query-1"), delivered)
        before = self.c.status("g8:alpha", "alpha", "query-1")
        self.assertFalse(before["g8_read"])
        self.assertEqual(before["waiting"]["action_owner"], "independent-reviewer")
        self.denied("ACCESS_DENIED", self.c.ack, "g8:beta", "alpha", "query-1", 1, delivered["result_hash"], "unknown")
        self.denied("RESULT_HASH_MISMATCH", self.c.ack, "g8:alpha", "alpha", "query-1", 1, "0" * 64, "wrong")
        acked = self.c.ack("g8:alpha", "alpha", "query-1", 1, delivered["result_hash"], "native:g8-read-1")
        self.assertTrue(acked["g8_read"])
        self.assertFalse(acked["unblocked"])
        self.denied("CONDITION_MISMATCH", self.c.unblock, "owner:alice", "alpha", "query-1", 1,
                    "native:alice", "different condition", "review-ref")
        verified = self.c.unblock("owner:alice", "alpha", "query-1", 1, "native:alice",
                                  self.report()["next_condition"], "reviewer:accepted-assignment")
        self.assertTrue(verified["unblocked"])
        self.assertEqual(verified["result_version"], 2)
        self.assertFalse(verified["g8_read"], "reading v1 never confirms v2")
        self.assertEqual(self.c.delivery("alpha", "query-1", 1), delivered)
        health = self.c.health("g8:alpha", "alpha")
        self.assertEqual(health["open_followups"], 0)
        self.assertEqual(health["last_owner_reply"]["kind"], "VERIFIED_REPLY")

    def test_no_duplicate_send_after_crash_cancel_blocks_new_effects_and_old_reply_is_attributed(self):
        claim = self.contact()
        self.assertTrue(claim["send"])
        action = claim["action_id"]
        # Simulate interruption after admission, before local knowledge of native acceptance.
        recovered = self.reopen()
        self.assertEqual(recovered.manage("recover", {"evidence_ref": "old-writer-stopped"})["contacts_unknown"], 1)
        again = recovered.claim_contact("alpha", "query-1", 1)
        self.assertFalse(again["send"])
        self.assertEqual(again["action_id"], action)
        self.denied("OUTSTANDING_CONTACT", recovered.manage, "activate_owner", self.binding("bob", 1))
        result = recovered.cancel("g8:alpha", "alpha", "query-1", "cancel-request")
        self.assertTrue(result["cancel_requested"])
        self.assertFalse(result["stopped"])
        self.assertEqual(result["contact_state"], "UNKNOWN")
        self.assertEqual(recovered.health("g8:alpha", "alpha")["open_followups"], 1)
        recovered.manage("record_stop", {"project_id": "alpha", "request_id": "query-1",
                                        "action_id": action, "evidence_ref": "native:original-action-stopped"})
        recovered.manage("activate_owner", self.binding("bob", 1))
        self.denied("STALE_GENERATION", recovered.claim_contact, "alpha", "query-1", 1)
        late = recovered.reply("owner:alice", "alpha", "query-1", action, 1, "native:alice", "late:receipt", self.report())
        self.assertEqual(late, {"accepted": False, "late": True})
        self.denied("RESULT_NOT_READY", recovered.result, "g8:alpha", "alpha", "query-1")
        self.assertIn("LATE_OWNER_REPLY", [e["kind"] for e in recovered.manage("events", {"project_id": "alpha"})["events"]])

    def test_cancellation_before_send_never_admits_contact(self):
        self.c.manage("activate_owner", self.binding())
        self.c.submit("g8:alpha", self.request())
        self.c.cancel("g8:alpha", "alpha", "query-1", "explicit cancellation")
        self.denied("CANCEL_REQUESTED", self.c.claim_contact, "alpha", "query-1", 1)
        self.assertFalse(self.c.inspect("alpha", "query-1")["stopped"])

    def test_unknown_owner_wait_report_and_explicit_retarget_keep_original_request(self):
        original = self.request(generation=0)
        self.assertEqual(self.c.submit("g8:alpha", original)["state"], "WAITING_OWNER")
        self.denied("OWNER_UNVERIFIED", self.c.claim_contact, "alpha", "query-1", 0)
        wait = {"project_id": "alpha", "request_id": "query-1", "action_owner": "registry-custodian",
                "reason": "OWNER_ROUTE_UNKNOWN", "next_condition": "Custodian confirms the existing native route",
                "next_check_at": "2030-01-01T02:00:00Z", "deadline": DEADLINE, "escalate_to": "g8:alpha",
                "evidence_ref": "directory:lookup-1"}
        self.c.manage("set_wait", wait)
        first = self.c.manage("notifications", {"project_id": "alpha"})["notifications"]
        self.c.manage("set_wait", wait)
        self.c.manage("set_wait", dict(wait, evidence_ref="directory:lookup-2"))
        self.assertEqual(first, self.c.manage("notifications", {"project_id": "alpha"})["notifications"])
        result = self.c.manage("report_wait", {"project_id": "alpha", "request_id": "query-1", "evidence_ref": "local:pending"})
        self.assertEqual(result["result"]["kind"], "COORDINATOR_WAIT_REPORT")
        self.c.ack("g8:alpha", "alpha", "query-1", 1, result["result_hash"], "native:wait-read")
        self.c.manage("activate_owner", self.binding())
        self.denied("STALE_GENERATION", self.c.claim_contact, "alpha", "query-1", 1)
        self.c.manage("retarget", {"project_id": "alpha", "request_id": "query-1", "expected_generation": 1,
                                  "authorization_ref": "g8:allow-original-query-to-verified-owner"})
        replay = self.c.submit("g8:alpha", original)
        self.assertEqual(replay["target_generation"], 1)
        self.assertEqual(replay["payload_hash"], original["payload_hash"])
        self.assertTrue(self.c.claim_contact("alpha", "query-1", 1)["send"])

    def test_two_processes_can_admit_only_one_creation_and_activation_requires_reconciled_candidate(self):
        def create(cid):
            return {"project_id": "alpha", "expected_generation": 0, "creation_id": cid,
                    "vacancy_ref": "formal-registry:confirmed-empty", "release_ref": "previous-writer:released",
                    "authorization_ref": "local:one-creation"}
        paths = []
        for cid in ("candidate-1", "candidate-2"):
            p = self.root / (cid + ".json")
            p.write_text(json.dumps(create(cid)))
            paths.append(p)
        processes = [subprocess.Popen([sys.executable, str(ROOT / "scripts/project_coordinator.py"),
                              "--state-dir", str(self.state), "begin_creation", "--input", str(p)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for p in paths]
        responses = [p.communicate(timeout=15) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes), [0, 2])
        winner = next(json.loads(out)["creation_id"] for out, _ in responses if out)
        self.assertFalse(self.c.manage("begin_creation", create(winner))["create"])
        self.c.manage("recover", {"evidence_ref": "creator-restarted"})
        self.assertEqual(self.c.manage("begin_creation", create(winner))["state"], "UNKNOWN")
        self.denied("CREATION_NOT_RECONCILED", self.c.manage, "activate_owner", self.binding())
        self.c.manage("record_creation", {"project_id": "alpha", "expected_generation": 0, "creation_id": winner,
                                        "outcome": "CREATED", "evidence_ref": "native:instance-found",
                                        "candidate_ref": "native:alice"})
        self.denied("CREATION_NOT_RECONCILED", self.c.manage, "activate_owner", dict(self.binding("bob"), creation_id=winner))
        activation = dict(self.binding(), creation_id=winner)
        self.assertEqual(self.c.manage("activate_owner", activation)["generation"], 1)
        self.assertEqual(self.reopen().manage("activate_owner", activation)["generation"], 1)
        self.denied("STALE_GENERATION", self.c.manage, "activate_owner", self.binding("bob", 0))
        self.assertEqual(self.c.route("alpha")["owner_principal"], "owner:alice")

    def test_cli_has_no_principal_impersonation_and_inspection_does_not_ack(self):
        action = self.contact()["action_id"]
        self.c.reply("owner:alice", "alpha", "query-1", action, 1, "native:alice", "native:reply", self.report())
        path = self.root / "input.json"
        path.write_text(json.dumps({"project_id": "alpha", "request_id": "query-1"}))
        command = [sys.executable, str(ROOT / "scripts/project_coordinator.py"), "--state-dir", str(self.state)]
        result = subprocess.run([*command, "inspect", "--input", str(path)], capture_output=True, text=True, check=True)
        self.assertFalse(json.loads(result.stdout)["g8_read"])
        for args in (["ack"], ["reply"], ["submit"], ["inspect", "--principal", "g8:alpha"]):
            completed = subprocess.run([*command, *args, "--input", str(path)], capture_output=True)
            self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.c.status("g8:alpha", "alpha", "query-1")["g8_read"])
        self.assertEqual(os.stat(self.c.path).st_mode & 0o777, 0o600)

    def test_tick_drains_due_index_in_bounded_batches_and_only_rearms_material_changes(self):
        self.c.manage("activate_owner", self.binding())
        for n in range(3):
            request = dict(self.request(), request_id=f"req-{n}", job_id=f"job-{n}", idempotency_key=f"key-{n}")
            self.c.submit("g8:alpha", request)
            wait = {"project_id": "alpha", "request_id": request["request_id"], "action_owner": "custodian",
                    "reason": "VERIFY_INPUT_BATCH", "next_condition": "Fixed batch index available",
                    "next_check_at": "2030-01-01T01:00:00Z", "deadline": DEADLINE, "escalate_to": "g8:alpha",
                    "evidence_ref": "source:v1"}
            self.c.manage("set_wait", wait)
        self.c.clock = lambda: datetime(2030, 1, 1, 2, tzinfo=timezone.utc)
        processed = [self.c.manage("tick", {"limit": 1}) for _ in range(4)]
        self.assertEqual([r["processed"] for r in processed], [1, 1, 1, 0])
        self.assertEqual([r["more_due"] for r in processed], [True, True, False, False])
        self.assertEqual({r["items"][0]["request_id"] for r in processed[:3]}, {"req-0", "req-1", "req-2"})
        self.c.manage("set_wait", dict(wait, evidence_ref="source:v2-same-condition"))
        self.assertEqual(self.c.manage("tick", {"project_id": "alpha"})["processed"], 0)
        self.c.clock = lambda: datetime(2030, 1, 3, tzinfo=timezone.utc)
        expirations = self.c.manage("tick", {})
        self.assertEqual([r["phase"] for r in expirations["items"]], ["DEADLINE"] * 3)
        self.assertEqual(self.c.manage("tick", {})["processed"], 0)
        events = self.c.manage("notifications", {"project_id": "alpha"})["notifications"]
        self.assertEqual(sum(e["kind"] == "WAIT_CHECKPOINT" for e in events), 3)
        self.assertEqual(sum(e["kind"] == "WAIT_DEADLINE" for e in events), 3)
        self.assertTrue(all(self.c.inspect("alpha", f"req-{n}")["action_id"] is None for n in range(3)))
        self.assertFalse(self.c.inspect("alpha", "req-0")["g8_read"])

    def test_tick_separates_cancelled_contact_reconciliation_from_answered_and_resolved_blockers(self):
        self.c.manage("activate_owner", self.binding())
        for suffix in ("cancelled", "sent-cancel", "answered", "resolved"):
            request = dict(self.request(), request_id=suffix, idempotency_key=suffix, job_id="job-" + suffix)
            self.c.submit("g8:alpha", request)
            if suffix == "cancelled":
                self.c.cancel("g8:alpha", "alpha", suffix, "cancelled-before-send")
                continue
            action = self.c.claim_contact("alpha", suffix, 1)["action_id"]
            if suffix == "sent-cancel":
                pending_action = action
                self.c.cancel("g8:alpha", "alpha", suffix, "cancelled-after-intent")
                continue
            result = self.c.reply("owner:alice", "alpha", suffix, action, 1, "native:alice",
                                  "native:reply-" + suffix, self.report())["delivery"]
            self.c.ack("g8:alpha", "alpha", suffix, 1, result["result_hash"], "g8:read-" + suffix)
            if suffix == "resolved":
                self.c.unblock("owner:alice", "alpha", suffix, 1, "native:alice",
                               self.report()["next_condition"], "native:owner-verified")
        self.c.clock = lambda: datetime(2030, 1, 3, tzinfo=timezone.utc)
        items = self.c.manage("tick", {})["items"]
        self.assertEqual({r["request_id"] for r in items}, {"answered", "sent-cancel"})
        self.assertEqual(self.c.inspect("alpha", "sent-cancel")["waiting"]["reason"], "RECONCILE_CANCELLED_ACTION")
        self.assertFalse(self.c.inspect("alpha", "sent-cancel")["stopped"])
        self.assertFalse(self.c.inspect("alpha", "answered")["unblocked"])
        self.c.manage("record_stop", {"project_id": "alpha", "request_id": "sent-cancel",
                                     "action_id": pending_action, "evidence_ref": "native:actually-stopped"})
        self.assertFalse(self.c.inspect("alpha", "sent-cancel")["waiting_active"])
        self.assertTrue(self.c.inspect("alpha", "resolved")["unblocked"])
        self.assertEqual(self.c.manage("tick", {})["processed"], 0)

    def test_read_only_operator_inspection_does_not_initialize_or_modify_database(self):
        missing = self.root / "missing"
        self.denied("STATE_NOT_INITIALIZED", Coordinator, missing, read_only=True)
        self.assertFalse(missing.exists())
        self.contact()
        before = self.c.path.read_bytes()
        read = Coordinator(self.state, clock=lambda: NOW, read_only=True)
        self.assertFalse(read.inspect("alpha", "query-1")["g8_read"])
        self.assertEqual(read.local_health("alpha")["scope"], "LOCAL_LEDGER")
        self.denied("READ_ONLY", read.manage, "tick", {})
        self.assertEqual(self.c.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
