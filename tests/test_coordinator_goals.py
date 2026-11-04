"""Goal lifecycle tests use temporary ledgers and no native or production services."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from project_coordinator import Coordinator, CoordinatorError, GOAL_BUDGET, canonical_hash


class GoalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state"
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.c = self.reopen()
        self.c.manage("register_project", {"project_id": "alpha", "repository": "CF-FE/alpha",
                      "project_ref": "project:alpha", "cloud_dot_principal": "cloud:alpha",
                      "report_route": "mailbox:original"})
        self.c.manage("activate_owner", self.binding())

    def reopen(self):
        return Coordinator(self.state, clock=lambda: self.now)

    def binding(self, owner="alice", generation=0):
        return {"project_id": "alpha", "expected_generation": generation,
                "owner_principal": "owner:" + owner, "endpoint": "native:" + owner,
                "appointment_ref": "appointment:" + owner, "verification_ref": "verified:" + owner,
                "handoff_ref": "handoff:" + owner, "release_ref": "release:" + owner}

    def request(self, request_id="goal-1", *, review="none", scopes=None):
        scopes = scopes or ["batch:v1:segment-01", "batch:v1:segment-02"]
        goal = {"objective": "Coordinate the existing review and return its original evidence.",
                "work_key": canonical_hash({"task_ref": "https://github.com/CF-FE/alpha/issues/2", "scope_refs": sorted(scopes)}),
                "acceptance_criteria": ["accountable reviewer identified", "evidence returned"],
                "evidence_refs": ["original-batch:v1"], "related_request_ids": ["old-query"],
                "scope_refs": scopes, "allowed_actions": ["coordinate_existing_owner", "verify_existing_evidence",
                    "plan_independent_review", "report_progress"],
                "next_check_at": "2030-01-01T02:00:00Z", "review_requirement": review}
        return {"project_id": "alpha", "request_id": request_id, "job_id": "job-" + request_id,
                "task_ref": "https://github.com/CF-FE/alpha/issues/2", "idempotency_key": request_id, "kind": "DELEGATE_GOAL",
                "expected_generation": 1, "deadline": "2030-01-02T00:00:00Z", "goal": goal,
                "authorization_ref": "authority:single-goal", "payload_hash": canonical_hash({"id": request_id}),
                "evidence_refs": ["original-batch:v1"], "budget": deepcopy(GOAL_BUDGET)}

    def start(self, **kwargs):
        self.original = self.request(**kwargs)
        self.c.submit("cloud:alpha", self.original)
        self.intent = self.c.claim_contact("alpha", self.original["request_id"], 1)
        return self.intent

    def report(self, seq=1, phase="ACCEPTED"):
        result = {"seq": seq, "phase": phase, "summary": "Original task evidence checked.",
                  "observed_at": self.now.isoformat(), "evidence_refs": ["native:evidence:" + str(seq)],
                  "next_owner": "owner:alice"}
        if phase == "COMPLETED":
            result.update(terminal_condition="All original criteria have cited native evidence.",
                          criteria_evidence=[{"criterion": x, "evidence_refs": ["native:criterion:" + str(i)]}
                          for i, x in enumerate(self.original["goal"]["acceptance_criteria"])])
            if self.original["goal"]["review_requirement"] == "independent_listening":
                result["independent_review"] = {"state": "VERIFIED", "reviewer": "reviewer:bob",
                    "producer": "producer:carol", "scope_refs": self.original["goal"]["scope_refs"],
                    "evidence_refs": ["native:listening:original"], "method": "human_listening"}
        else:
            result["next_check_at"] = "2030-01-01T03:00:00Z"
        return result

    def update(self, report=None, coordinator=None):
        report = report or self.report()
        return (coordinator or self.c).goal_update("owner:alice", "alpha", self.original["request_id"],
                self.intent["action_id"], 1, "native:alice", "native:receipt:" + str(report["seq"]), report)

    def denied(self, code, function, *args, **kwargs):
        with self.assertRaises(CoordinatorError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_delivery_acceptance_progress_completion_and_read_are_distinct(self):
        intent = self.start()
        self.assertEqual(intent["kind"], "DELEGATE_GOAL")
        self.assertEqual(intent["goal"], self.original["goal"])
        self.c.record_contact("alpha", "goal-1", intent["action_id"], "SUBMITTED", "native:submission")
        self.assertFalse(self.c.inspect("alpha", "goal-1")["accepted"])
        self.denied("GOAL_ACCEPTANCE_REQUIRED", self.update, self.report(1, "COMPLETED"))
        accepted = self.update()["delivery"]
        self.assertEqual(accepted["result"]["kind"], "GOAL_UPDATE")
        self.assertEqual(self.c.inspect("alpha", "goal-1")["goal_state"], "ACCEPTED")
        progress = self.update(self.report(2, "IN_PROGRESS"))["delivery"]
        final = self.update(self.report(3, "COMPLETED"))["delivery"]
        self.assertEqual([accepted["version"], progress["version"], final["version"]], [1, 2, 3])
        for delivery in (accepted, progress, final):
            self.assertEqual(delivery["job_id"], self.original["job_id"])
            self.assertEqual(delivery["payload_hash"], self.original["payload_hash"])
            self.assertEqual(delivery["result_hash"], canonical_hash(delivery["result"]))
        status = self.c.inspect("alpha", "goal-1")
        self.assertEqual(status["goal_state"], "COMPLETED")
        self.assertFalse(status["cloud_dot_read"])
        self.assertFalse(status["waiting_active"])
        self.c.ack("cloud:alpha", "alpha", "goal-1", 1, accepted["result_hash"], "native:read-v1")
        self.assertFalse(self.c.inspect("alpha", "goal-1")["cloud_dot_read"])
        self.denied("GOAL_TERMINAL", self.update, self.report(4, "IN_PROGRESS"))
        self.assertEqual(self.update(self.report(3, "COMPLETED"))["delivery"], final)

    def test_work_reservation_is_cross_request_and_rejects_partial_overlap(self):
        self.start()
        duplicate = self.request("goal-2")
        duplicate["goal"]["objective"] = "Same scope with different wording"
        duplicate["goal"]["scope_refs"].reverse()
        self.denied("WORK_ALREADY_DELEGATED", self.c.submit, "cloud:alpha", duplicate)
        overlap = self.request("goal-3", scopes=["batch:v1:segment-02", "batch:v1:segment-03"])
        with self.assertRaises(CoordinatorError) as rejected:
            self.c.submit("cloud:alpha", overlap)
        self.assertEqual(rejected.exception.code, "WORK_SCOPE_OVERLAP")
        conflicts = rejected.exception.conflicts
        self.assertEqual(conflicts, [{"request_id": "goal-1", "work_key": self.original["goal"]["work_key"]}])
        existing = self.c.goal_for_work("cloud:alpha", "alpha", conflicts[0]["work_key"])
        self.assertEqual(existing["request_id"], "goal-1")
        self.assertEqual(existing["action_id"], self.intent["action_id"])
        self.denied("ACCESS_DENIED", self.c.goal_for_work, "cloud:other", "alpha", self.original["goal"]["work_key"])

    def test_asset_alias_cannot_bypass_reservation_or_expose_it_to_another_principal(self):
        self.start()
        for value in ("batch:v1:segment-01 ", "\tbatch:v1:segment-01", "batch:v1:\x01segment-01"):
            self.denied("NONCANONICAL_SCOPE_REF", self.c.submit, "cloud:alpha", self.request("other", scopes=[value]))
        with self.assertRaises(CoordinatorError) as denied:
            self.c.submit("untrusted", self.request("other"))
        self.assertEqual(denied.exception.code, "ACCESS_DENIED")
        self.assertFalse(denied.exception.conflicts)

    def test_recovery_replays_timely_acceptance_even_after_its_checkpoint(self):
        self.start()
        accepted = self.report()
        completed = self.report(2, "COMPLETED")
        self.now += timedelta(hours=4)
        recovered = self.reopen()
        self.assertTrue(self.update(accepted, coordinator=recovered)["accepted"])
        self.assertEqual(recovered.manage("tick", {"project_id": "alpha"})["items"][0]["phase"], "CHECKPOINT")
        self.assertTrue(self.update(completed, coordinator=recovered)["accepted"])
        self.assertFalse(recovered.claim_contact("alpha", "goal-1", 1)["send"])
        self.assertEqual(recovered.inspect("alpha", "goal-1")["goal_state"], "COMPLETED")

    def test_late_reports_have_separate_durable_sequence_and_idempotent_receipts(self):
        self.start()
        self.update()
        self.c.cancel("cloud:alpha", "alpha", "goal-1", "cancel:original")
        late_progress, late_final = self.report(2, "IN_PROGRESS"), self.report(3, "COMPLETED")
        self.denied("GOAL_SEQUENCE_GAP", self.update, late_final)
        for report in (late_progress, late_final, late_progress, late_final):
            self.assertEqual(self.update(report, coordinator=self.reopen()), {"accepted": False, "late": True})
        self.denied("GOAL_UPDATE_CONFLICT", self.update, dict(late_progress, summary="different observation"))
        state = self.c.inspect("alpha", "goal-1")
        self.assertEqual((state["goal_state"], state["last_seq"], state["result_version"]), ("ACCEPTED", 1, 1))
        events = self.c.manage("events", {"project_id": "alpha"})["events"]
        self.assertEqual(sum(e["kind"] == "LATE_GOAL_OBSERVATION" for e in events), 2)

    def test_unbound_project_does_not_reserve_the_goal_assets(self):
        ledger = Coordinator(self.state.parent / "unbound", clock=lambda: self.now)
        ledger.manage("register_project", {"project_id": "alpha", "repository": "CF-FE/alpha",
                      "project_ref": "project:alpha", "cloud_dot_principal": "cloud:alpha",
                      "report_route": "mailbox:original"})
        original = self.request()
        original["expected_generation"] = 0
        self.denied("OWNER_UNVERIFIED", ledger.submit, "cloud:alpha", original)
        ledger.manage("activate_owner", self.binding())
        self.assertEqual(ledger.submit("cloud:alpha", self.request())["goal_state"], "ADMITTED")

    def test_new_owner_does_not_inherit_a_goal_before_contact(self):
        original = self.request()
        self.c.submit("cloud:alpha", original)
        self.c.manage("activate_owner", self.binding("bob", 1))
        self.denied("GOAL_ORIGINAL_GENERATION_MISMATCH", self.c.manage, "retarget",
                    {"project_id": "alpha", "request_id": "goal-1", "expected_generation": 2,
                     "authorization_ref": "local:retarget"})
        self.denied("STALE_GENERATION", self.c.claim_contact, "alpha", "goal-1", 2)
        self.assertIsNone(self.c.inspect("alpha", "goal-1")["action_id"])

    def test_concurrent_duplicate_submit_has_one_durable_reservation(self):
        coordinators = [self.reopen(), self.reopen()]
        requests = [self.request("goal-1"), self.request("goal-2")]
        def submit(index):
            try:
                return coordinators[index].submit("cloud:alpha", requests[index])["request_id"]
            except CoordinatorError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, range(2)))
        self.assertEqual(outcomes.count("WORK_ALREADY_DELEGATED"), 1)
        with sqlite3.connect(self.c.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM goals").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM requests").fetchone()[0], 1)

    def test_unknown_after_interruption_never_repeats_dispatch(self):
        intent = self.start()
        self.assertTrue(intent["send"])
        recovered = self.reopen()
        recovered.manage("recover", {"evidence_ref": "original-sender-stopped"})
        again = recovered.claim_contact("alpha", "goal-1", 1)
        self.assertFalse(again["send"])
        self.assertEqual(again["action_id"], intent["action_id"])
        self.assertEqual(recovered.inspect("alpha", "goal-1")["contact_state"], "UNKNOWN")
        self.denied("CONTACT_ALREADY_DECIDED", recovered.check_contact, "alpha", "goal-1", intent["action_id"], 1)
        self.update(coordinator=recovered)
        self.assertEqual(recovered.inspect("alpha", "goal-1")["goal_state"], "ACCEPTED")

    def test_original_sequence_hash_survives_restart_and_conflicts_are_rejected(self):
        self.start()
        first = self.update()["delivery"]
        self.assertEqual(self.update(coordinator=self.reopen())["delivery"], first)
        changed = dict(self.report(), summary="Changed meaning")
        self.denied("GOAL_UPDATE_CONFLICT", self.update, changed)
        self.denied("GOAL_SEQUENCE_GAP", self.update, self.report(3, "IN_PROGRESS"))
        self.denied("GOAL_ALREADY_ACCEPTED", self.update, self.report(2))
        self.assertEqual(self.c.inspect("alpha", "goal-1")["last_seq"], 1)

    def test_blocked_has_owner_and_checkpoint_but_is_not_completion_or_new_dispatch(self):
        self.start()
        self.update(self.report(1, "BLOCKED"))
        self.assertFalse(self.c.inspect("alpha", "goal-1")["accepted"])
        self.assertTrue(self.c.inspect("alpha", "goal-1")["waiting_active"])
        self.assertFalse(self.c.claim_contact("alpha", "goal-1", 1)["send"])
        self.update(self.report(2, "ACCEPTED"))
        self.update(self.report(3, "BLOCKED"))
        self.update(self.report(4, "IN_PROGRESS"))
        self.assertEqual(self.c.inspect("alpha", "goal-1")["goal_state"], "IN_PROGRESS")

    def test_cancel_and_deadline_keep_late_evidence_without_advancing_goal(self):
        self.start()
        self.c.cancel("cloud:alpha", "alpha", "goal-1", "cancel:original")
        self.denied("CANCEL_REQUESTED", self.c.check_contact, "alpha", "goal-1", self.intent["action_id"], 1)
        observation = self.report()
        self.assertEqual(self.update(observation), {"accepted": False, "late": True})
        self.assertEqual(self.c.inspect("alpha", "goal-1")["goal_state"], "ADMITTED")
        self.assertFalse(self.c.inspect("alpha", "goal-1")["stopped"])
        self.now += timedelta(days=2)
        self.assertEqual(self.c.submit("cloud:alpha", self.original)["request_id"], "goal-1")
        self.assertEqual(self.update(observation), {"accepted": False, "late": True})
        events = self.c.manage("events", {"project_id": "alpha"})["events"]
        self.assertIn("LATE_GOAL_OBSERVATION", [event["kind"] for event in events])

    def test_generation_handoff_needs_actual_stop_and_old_owner_cannot_advance(self):
        self.start()
        self.update()
        self.denied("OUTSTANDING_CONTACT", self.c.manage, "activate_owner", self.binding("bob", 1))
        self.c.manage("record_stop", {"project_id": "alpha", "request_id": "goal-1",
                      "action_id": self.intent["action_id"], "evidence_ref": "native:old-action-stopped"})
        late_progress = self.report(2, "IN_PROGRESS")
        self.assertEqual(self.update(late_progress), {"accepted": False, "late": True})
        self.c.manage("activate_owner", self.binding("bob", 1))
        self.assertEqual(self.update(late_progress), {"accepted": False, "late": True})
        self.assertEqual(self.c.inspect("alpha", "goal-1")["last_seq"], 1)
        self.denied("STALE_GENERATION", self.c.claim_contact, "alpha", "goal-1", 2)

    def test_completion_requires_exact_criteria_and_independent_listening_evidence(self):
        self.start(review="independent_listening")
        self.update()
        final = self.report(2, "COMPLETED")
        missing = deepcopy(final)
        missing["criteria_evidence"].pop()
        self.denied("GOAL_CRITERIA_INCOMPLETE", self.update, missing)
        for method in ("ASR", "technical_checks"):
            invalid = deepcopy(final)
            invalid["independent_review"]["method"] = method
            self.denied("INDEPENDENT_REVIEW_REQUIRED", self.update, invalid)
        same_author = deepcopy(final)
        same_author["independent_review"]["reviewer"] = same_author["independent_review"]["producer"]
        self.denied("INDEPENDENT_REVIEW_REQUIRED", self.update, same_author)
        same_author["independent_review"]["reviewer"] += " "
        self.denied("INDEPENDENT_REVIEW_REQUIRED", self.update, same_author)
        same_author["independent_review"]["reviewer"] = "reviewer:\u200breported"
        self.denied("INDEPENDENT_REVIEW_REQUIRED", self.update, same_author)
        partial = deepcopy(final)
        partial["independent_review"]["scope_refs"] = partial["independent_review"]["scope_refs"][:1]
        self.denied("REVIEW_SCOPE_INCOMPLETE", self.update, partial)
        self.assertTrue(self.update(final)["accepted"])

    def test_query_endpoints_cannot_accept_goal_and_goal_cannot_expand_authority(self):
        self.start()
        args = ("owner:alice", "alpha", "goal-1", self.intent["action_id"], 1, "native:alice", "native:read")
        self.denied("OPERATION_NOT_ALLOWED", self.c.owner_read, *args)
        query_report = {"blocker": "x", "impact": "x", "evidence_refs": ["x"], "verified": [], "unverified": [],
                        "next_owner": "x", "next_condition": "x", "observed_at": self.now.isoformat()}
        self.denied("OPERATION_NOT_ALLOWED", self.c.reply, *args, query_report)
        request = self.request("goal-2", scopes=["different:scope"])
        request["goal"]["allowed_actions"].append("generate_audio")
        self.denied("GOAL_ACTION_NOT_ALLOWED", self.c.submit, "cloud:alpha", request)
        request = self.request("goal-2", scopes=["different:scope"])
        request["budget"]["audio_generations"] = 1
        self.denied("INVALID_BUDGET", self.c.submit, "cloud:alpha", request)
        request = self.request("goal-2", scopes=["different:scope"])
        request["goal"]["work_key"] = "a" * 64
        self.denied("WORK_KEY_MISMATCH", self.c.submit, "cloud:alpha", request)

    def test_progress_after_deadline_is_only_an_observation(self):
        self.start()
        self.update()
        self.now += timedelta(days=2)
        self.assertEqual(self.update(self.report(2, "COMPLETED")), {"accepted": False, "late": True})
        self.assertEqual(self.c.inspect("alpha", "goal-1")["goal_state"], "ACCEPTED")
        self.assertEqual(self.c.inspect("alpha", "goal-1")["result_version"], 1)

    def test_checkpoints_are_bounded_and_types_are_strict(self):
        request = self.request()
        request["goal"]["next_check_at"] = "2031-01-01T00:00:00Z"
        self.denied("INVALID_CHECKPOINT", self.c.submit, "cloud:alpha", request)
        request = self.request()
        request["budget"]["dispatch_attempts"] = True
        self.denied("INVALID_BUDGET", self.c.submit, "cloud:alpha", request)
        self.start()
        report = self.report()
        report["seq"] = True
        self.denied("INVALID_GOAL_SEQUENCE", self.update, report)
        report = self.report()
        report["next_check_at"] = "2030-01-01T00:00:00Z"
        self.denied("CHECKPOINT_EXPIRED", self.update, report)

    def test_task_aliases_cannot_create_second_work_and_wait_versions_are_separate(self):
        request = self.request()
        request["task_ref"] = "github:CF-FE/alpha#2"
        request["goal"]["work_key"] = canonical_hash({"task_ref": request["task_ref"],
                                                      "scope_refs": sorted(request["goal"]["scope_refs"])})
        self.denied("CANONICAL_TASK_REQUIRED", self.c.submit, "cloud:alpha", request)
        self.start()
        wait = self.c.manage("report_wait", {"project_id": "alpha", "request_id": "goal-1",
                                           "evidence_ref": "native:pending"})
        self.assertEqual(wait["result"]["original_kind"], "DELEGATE_GOAL")
        self.assertEqual(wait["result"]["goal_state"], "ADMITTED")
        self.assertEqual(wait["version"], 1)
        accepted = self.update()["delivery"]
        self.assertEqual(accepted["version"], 2)
        self.assertEqual(accepted["result"]["report"]["seq"], 1)
        self.assertEqual(self.update()["delivery"], accepted)

    def test_same_project_asset_scope_cannot_be_dispatched_again_under_another_issue(self):
        self.start()
        duplicate = self.request("goal-2")
        duplicate["task_ref"] = "https://github.com/CF-FE/alpha/issues/3"
        duplicate["goal"]["work_key"] = canonical_hash({"task_ref": duplicate["task_ref"],
                                                       "scope_refs": sorted(duplicate["goal"]["scope_refs"])})
        self.denied("WORK_SCOPE_OVERLAP", self.c.submit, "cloud:alpha", duplicate)
        self.denied("REQUEST_NOT_FOUND", self.c.claim_contact, "alpha", "goal-2", 1)
        self.update()
        self.update(self.report(2, "COMPLETED"))
        self.denied("WORK_SCOPE_OVERLAP", self.c.submit, "cloud:alpha", duplicate)
        with sqlite3.connect(self.c.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM requests").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM contacts").fetchone()[0], 1)

    def test_new_observations_cannot_predate_request_or_previous_adopted_report(self):
        self.start()
        ancient = self.report()
        ancient["observed_at"] = "2000-01-01T00:00:00Z"
        self.denied("OBSERVATION_BEFORE_REQUEST", self.update, ancient)
        self.assertEqual(self.c.inspect("alpha", "goal-1")["last_seq"], 0)
        self.now += timedelta(minutes=5)
        accepted_report = self.report()
        accepted = self.update(accepted_report)["delivery"]
        self.now += timedelta(minutes=5)
        regressed = self.report(2, "IN_PROGRESS")
        regressed["observed_at"] = "2030-01-01T00:04:59Z"
        self.denied("OBSERVATION_TIME_REGRESSION", self.update, regressed)
        self.assertEqual(self.c.inspect("alpha", "goal-1")["last_seq"], 1)
        same_time = self.report(2, "IN_PROGRESS")
        same_time["observed_at"] = accepted_report["observed_at"]
        self.assertTrue(self.update(same_time)["accepted"])
        self.assertEqual(self.update(accepted_report)["delivery"], accepted)


if __name__ == "__main__":
    unittest.main()
