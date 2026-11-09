"""Generated workflow, durable checkpoints and a loopback Decision Service substitute."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from decision_service_client import DecisionService, DecisionServiceError
from project_workflow import initialize
from project_workflow_decision import Checkpoint, choose, reconcile, update_quality


class ServiceDouble:
    def __init__(self, test, prefix=""):
        self.records = {}
        self.events = {}
        self.posts = []
        self.selection = "inspect"
        self.on_evaluate = None
        self.drop_evaluate = False
        self.drop_feedback = False
        self.truncate_responses = False
        double = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, value, *, truncate=False):
                payload = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload[:len(payload) // 2] if truncate else payload)

            def do_GET(self):
                if self.headers["Authorization"] != "Bearer isolated":
                    self.reply(401, {"error": {"code": "unauthenticated"}})
                    return
                location = urlsplit(self.path)
                route = prefix + "/v1/decisions"
                if location.path != route and not location.path.startswith(route + "/"):
                    self.reply(404, {"error": {"code": "not_found"}})
                    return
                if location.query:
                    record = double.records.get(parse_qs(location.query)["request_id"][0])
                else:
                    record = next((record for record in double.records.values()
                                   if record["decision_id"] == location.path.split("/")[-1]), None)
                if record is None:
                    self.reply(404, {"error": {"code": "not_found"}})
                else:
                    self.reply(200, record)

            def do_POST(self):
                if self.headers["Authorization"] != "Bearer isolated":
                    self.reply(401, {"error": {"code": "unauthenticated"}})
                    return
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                double.posts.append((self.path, payload))
                if self.path == prefix + "/v1/decisions/evaluate":
                    choice_id = double.selection
                    candidate = next(item for item in payload["choices"] if item["candidate_id"] == choice_id)
                    record = {key: payload[key] for key in ("schema_version", "project_id", "request_id",
                                                            "subject_ref", "snapshot")}
                    record.update(decision_id="d-" + str(len(double.records)), status="complete",
                                  disposition=candidate.get("metadata", {}).get("disposition", "suggested"),
                                  candidate_id=choice_id, parameters=candidate["parameters"],
                                  input_digest="1" * 64, candidate_digest=payload["snapshot"]["candidate_digest"])
                    double.records[payload["request_id"]] = record
                    if double.on_evaluate:
                        double.on_evaluate()
                    if double.drop_evaluate:
                        return
                    self.reply(200, record, truncate=double.truncate_responses)
                elif self.path == prefix + "/v1/decision-feedback":
                    key = payload["event_id"], payload["revision"]
                    prior = double.events.get(key)
                    if prior is not None and prior != payload:
                        self.reply(409, {"error": {"code": "revision_conflict"}})
                        return
                    double.events[key] = payload
                    if double.drop_feedback:
                        return
                    self.reply(200, {"event_id": key[0], "revision": key[1], "received": True,
                                     "receipt_id": str(key), "duplicate": prior is not None},
                               truncate=double.truncate_responses)
                else:
                    self.reply(404, {"error": {"code": "not_found"}})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        test.addCleanup(server.server_close)
        test.addCleanup(thread.join, 5)
        test.addCleanup(server.shutdown)
        self.client = DecisionService(f"http://127.0.0.1:{server.server_port}{prefix}", "isolated", timeout=2)


def example_checkpoint(test, *, fail_after_choice=False):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    base = Path(tmp.name)
    repo = base / "project"
    repo.mkdir()
    (repo / "memory.md").write_text("Local project rules")
    spec = base / "spec.json"
    steps = {
        "route": {"kind": "decision", "question": "Which route?", "choices": {"inspect": "review", "done": "end"}},
        "review": {"kind": "handoff", "instruction": "Inspect.", "next": "end"},
        "end": {"kind": "end"},
    }
    operations = None
    if fail_after_choice:
        steps["route"]["choices"]["inspect"] = "fail"
        steps["fail"] = {"kind": "program", "operation": "broken", "next": "review"}
        operations = base / "operations.py"
        operations.write_text("def broken(root, run):\n    raise ValueError('isolated step failure')\n"
                              "OPERATIONS = {'broken': broken}\n")
    spec.write_text(json.dumps({"project": "example", "version": 1, "workflow": "example",
                                "memory": "memory.md", "start": "route", "steps": steps}))
    initialize(repo, spec, operations)
    state = base / "state.sqlite"
    input_file = base / "input.json"
    input_file.write_text("{}")
    workflow = repo / "workflows/example/workflow.py"
    # start creates the original checkpoint, so construct the adapter afterwards.
    subprocess.run([sys.executable, str(workflow), "--state", str(state), "start", "--task", "issue-7",
                    "--input", str(input_file)], check=True, capture_output=True)
    return Checkpoint(workflow, state)


class WorkflowDecisionTests(unittest.TestCase):
    def test_gateway_prefix_is_preserved_for_evaluation_readback_and_feedback(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self, prefix="/decision-service")
        outcome = choose(checkpoint, http.client)
        self.assertEqual(outcome["application_status"], "applied")
        self.assertEqual(http.client.get(decision_id=outcome["decision_id"])["request_id"], outcome["request_id"])
        self.assertEqual([path for path, _ in http.posts], [
            "/decision-service/v1/decisions/evaluate",
            "/decision-service/v1/decision-feedback",
        ])

    def test_gateway_prefix_rejects_ambiguous_path_segments(self):
        for path in ("/prefix/../other", "/prefix/./other", "/prefix/%2e%2e", "/prefix//other"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(DecisionServiceError, "invalid_service_url"):
                    DecisionService("https://service.example" + path, "isolated")

    def test_lost_decision_and_feedback_ack_recover_without_business_replay(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        http.drop_evaluate = True
        http.drop_feedback = True
        outcome = choose(checkpoint, http.client)
        self.assertEqual(outcome["application_status"], "applied")
        action = checkpoint.native("status")["action"]["id"]
        self.assertEqual(len(outcome["feedback"]["pending"]), 1)
        original_event = next(iter(http.events.values()))
        checkpoint.native("receive", "--action", action, "--status", "COLLECTED", "--evidence", "artifact:original")
        self.assertIsNone(checkpoint.native("status")["action"])
        http.drop_feedback = False
        recovered = Checkpoint(checkpoint.workflow, checkpoint.state)
        with patch.object(recovered, "native", side_effect=AssertionError("reconcile must not run a workflow")):
            self.assertEqual(reconcile(recovered, http.client), {"received": 3, "pending": []})
        self.assertEqual(http.events[(original_event["event_id"], 1)], original_event)
        result = http.events[(outcome["unit_id"] + ".result", 1)]
        self.assertEqual(result["execution_ref"]["ids"], {"action_id": action})
        self.assertEqual(result["payload"]["execution_status"], "unknown")
        self.assertEqual(result["payload"]["quality_status"], "pending")
        self.assertEqual(result["payload"]["result_refs"], ["artifact:original"])
        self.assertNotIn("usage", result["payload"])
        update_quality(recovered, outcome["unit_id"], "passed", ["approval:quality-7"])
        reconcile(recovered, http.client)
        revised = http.events[(result["event_id"], 2)]
        self.assertEqual(revised["supersedes_revision"], 1)
        self.assertEqual(revised["payload"]["quality_status"], "passed")
        self.assertEqual(revised["decision_ids"], result["decision_ids"])
        self.assertEqual(sum(path.endswith("/evaluate") for path, _ in http.posts), 1)

    def test_application_is_read_back_after_advance_failure(self):
        checkpoint = example_checkpoint(self, fail_after_choice=True)
        http = ServiceDouble(self)
        outcome = choose(checkpoint, http.client)
        self.assertIn("isolated step failure", outcome["workflow_error"])
        self.assertEqual(outcome["application_status"], "applied")
        run = checkpoint.native("status")
        self.assertEqual(run["status"], "RUNNABLE")
        self.assertEqual(run["decisions"]["route"], {"choice": "inspect", "evidence": "decision://d-0"})
        self.assertEqual(len(http.events), 1)

    def test_truncated_http_responses_follow_the_same_recovery_paths(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        http.truncate_responses = True
        outcome = choose(checkpoint, http.client)
        self.assertEqual(outcome["application_status"], "applied")
        self.assertEqual([item["code"] for item in outcome["feedback"]["pending"]], ["transport_error"])
        http.truncate_responses = False
        self.assertEqual(reconcile(checkpoint, http.client), {"received": 1, "pending": []})
        self.assertEqual(len(http.events), 1)
        self.assertEqual(sum(path.endswith("/evaluate") for path, _ in http.posts), 1)

    def test_competing_native_decision_stales_the_suggestion(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        http.on_evaluate = lambda: checkpoint.native("decide", "--step", "route", "--choice", "done",
                                                     "--evidence", "authorized-operator")
        outcome = choose(checkpoint, http.client)
        self.assertEqual(outcome["application_status"], "stale")
        self.assertEqual(checkpoint.native("status")["decisions"]["route"]["choice"], "done")
        self.assertEqual(len(http.events), 1)
        application = next(iter(http.events.values()))
        self.assertEqual(application["execution_state"], "ended")
        self.assertEqual(application["coverage_observation"]["units"], [
            {"unit_id": outcome["unit_id"], "state": "ended", "expects_result": False},
        ])

    def test_two_suggestions_share_native_unit_observation_when_only_one_applies(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        adopted = []

        def advance_during_first_request():
            http.on_evaluate = None
            adopted.append(choose(checkpoint, http.client, context={"revision": 2}))

        http.on_evaluate = advance_during_first_request
        stale = choose(checkpoint, http.client, context={"revision": 1})
        self.assertEqual(stale["application_status"], "stale")
        self.assertEqual(adopted[0]["application_status"], "applied")
        self.assertEqual(stale["unit_id"], adopted[0]["unit_id"])
        action = checkpoint.native("status")["action"]["id"]
        checkpoint.native("receive", "--action", action, "--status", "COLLECTED", "--evidence", "artifact:review")
        reconcile(checkpoint, http.client)
        latest = {}
        for event in http.events.values():
            latest[event["event_id"]] = event
        result = latest[stale["unit_id"] + ".result"]
        self.assertEqual(result["decision_ids"], [adopted[0]["decision_id"]])
        for item in (stale, adopted[0]):
            application = latest[item["decision_id"] + ".application"]
            self.assertEqual(application["execution_state"], "ended")
            self.assertEqual(application["coverage_observation"], result["coverage_observation"])

    def test_non_executable_candidate_never_advances(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        http.selection = "needs-replan"
        outcome = choose(checkpoint, http.client)
        self.assertEqual(outcome["disposition"], "needs_replan")
        self.assertEqual(outcome["application_status"], "not_applied")
        self.assertEqual(checkpoint.native("status")["status"], "WAITING_DECISION")
        self.assertEqual(len(http.events), 1)

    def test_get_is_read_only_and_requires_identity(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        outcome = choose(checkpoint, http.client)
        before = len(http.posts)
        self.assertEqual(http.client.get(decision_id=outcome["decision_id"])["request_id"], outcome["request_id"])
        self.assertEqual(len(http.posts), before)
        with self.assertRaises(DecisionServiceError) as caught:
            DecisionService(http.client.base_url, "wrong").get(request_id=outcome["request_id"])
        self.assertEqual(caught.exception.code, "unauthenticated")


if __name__ == "__main__":
    unittest.main()
