"""Mac Codex new-unit selection against an isolated native host substitute."""
import hashlib
import json
from pathlib import Path
import unittest

from test_project_workflow_decision import ServiceDouble, example_checkpoint
from allowlists import DEFAULT_FILE, DEFAULT_ROOT, read_active_agent_policy
from herdr_model_selection import HostError, apply, candidates, reconcile_model
from project_workflow_decision import choose, integration, reconcile, record_result, subject


def planned_candidates(selection, binding):
    policy, ref, library = read_active_agent_policy(DEFAULT_FILE, DEFAULT_ROOT)
    return candidates(selection, binding["provider"], policy, ref, library)


class NativeDouble:
    def __init__(self, binding):
        self.binding = binding
        self.thread_id = "native-thread-1"
        self.ready = False
        self.start_lost = False
        self.lookup_unavailable = False
        self.calls = []
        self.argv = None

    def __call__(self, session, *command):
        assert session == self.binding["session"]
        self.calls.append(command)
        cwd = self.binding["cwd"]
        pane = {key: self.binding[key] for key in ("workspace_id", "tab_id", "pane_id", "terminal_id")}
        pane.update(cwd=cwd, foreground_cwd=cwd, launch_pending=False)
        if command[:2] == ("agent", "list"):
            return {"agents": [{"name": self.binding["agent"], **pane}] if self.ready else []}
        if command[:2] == ("pane", "list"):
            return {"panes": [pane]}
        if command[:2] == ("pane", "process-info"):
            return {"process_info": {"shell_pid": 10, "pane_id": pane["pane_id"],
                                     "foreground_process_group_id": 20 if self.ready else 10,
                                     "foreground_processes": [{"pid": 20 if self.ready else 10, "cwd": cwd,
                                                               "argv": ["codex", *self.argv] if self.ready else ["zsh"]}]}}
        if command[:2] == ("agent", "start"):
            self.argv = list(command[command.index("--") + 1:])
            if self.start_lost:
                raise HostError("isolated lost launch acknowledgement")
            self.ready = True
            return {}
        if command[:2] == ("agent", "get") and self.ready and not self.lookup_unavailable:
            return {"agent": {**pane, "agent": "codex", "name": self.binding["agent"],
                              "agent_session": {"agent": "codex", "kind": "id", "value": self.thread_id}}}
        raise HostError("isolated target unavailable")


def setup_model(checkpoint, route):
    workflow = checkpoint.native("status")
    cwd = str(checkpoint.workflow.parents[2])
    binding = {"mode": "new-unit", "project_id": workflow["project"], "task_id": workflow["task"],
               "authorization_ref": "issue:authorized-worker", "unit_id": route["unit_id"],
               "action_id": workflow["action"]["id"], "session": "isolated-session", "agent": "isolated-worker",
               "workspace_id": "workspace", "tab_id": "tab", "pane_id": "pane", "terminal_id": "terminal",
               "cwd": cwd, "provider": "openai",
               "codex_args": ["--no-daemon", "-a", "on-request", "-s", "workspace-write", "-C", cwd,
                              "-c", 'shell_environment_policy.inherit="all"', "--model=previous",
                              "-c", 'model_reasoning_effort="low"']}
    selection = {"requirements": {"tools": ["shell"], "modalities": ["text"], "input_tokens": 400},
                 "recall": "skip", "capabilities": [
                     {"worker_id": "mac-codex", "backend_id": "current-default", "provider": "openai",
                      "model_type": "reasoning", "model": "gpt-6.1-sol", "supported_efforts": ["high"],
                      "tools": ["shell"], "modalities": ["text"], "context_window": 2048,
                      "evidence_ref": "project:mac-capability-7"},
                 ]}
    return binding, selection, NativeDouble(binding)


def example_rollout(checkpoint, binding, host, *, effort="high"):
    rollout = checkpoint.state.parent / "isolated-codex.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": host.thread_id, "cwd": binding["cwd"], "model_provider": "openai"}},
        {"type": "turn_context", "payload": {"turn_id": "unit-turn-1", "cwd": binding["cwd"],
                                            "model": "gpt-6.1-sol", "effort": effort}},
    ]
    rollout.write_text("".join(json.dumps(row) + "\n" for row in records))
    rollout.chmod(0o600)
    return rollout


class HerdrModelSelectionTests(unittest.TestCase):
    def test_launch_and_native_turn_readback_preserve_binding_and_share_one_result(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        route = choose(checkpoint, http.client, context={"version": 1})
        binding, selection, host = setup_model(checkpoint, route)
        http.selection = planned_candidates(selection, binding)[0]["candidate_id"]
        outcome = apply(checkpoint, http.client, route["unit_id"], binding, selection, host=host)
        self.assertEqual(outcome["application_status"], "unknown")
        self.assertEqual(outcome["thread_id"], host.thread_id)
        self.assertEqual(outcome["recommended_model"]["model"], "gpt-6.1-sol")
        policy_ref = outcome["model_policy"]
        self.assertEqual(policy_ref["revision"], json.loads(DEFAULT_FILE.read_bytes())["revision"])
        self.assertEqual(policy_ref["policy_sha256"], hashlib.sha256(DEFAULT_FILE.read_bytes()).hexdigest())
        with checkpoint.open() as run:
            data = integration(run)
            request = data["requests"][outcome["request_id"]]
            self.assertEqual(request["model_policy"], policy_ref)
            self.assertEqual(request["request"]["choices"][0]["metadata"]["model_policy"], policy_ref)
            self.assertEqual(data["launches"][route["unit_id"]]["model_policy"], policy_ref)
        self.assertEqual(outcome["launch_model"]["reasoning_effort"], "high")
        self.assertIsNone(outcome["effective_model"]["model"])
        self.assertEqual(host.argv, binding["codex_args"][:-3] +
                         ["-m", "gpt-6.1-sol", "-c", 'model_provider="openai"', "-c", 'model_reasoning_effort="high"'])
        self.assertIn(("agent", "start", binding["agent"], "--kind", "codex", "--pane", "pane",
                       "--timeout", "30000", "--", *host.argv), host.calls)
        rollout = example_rollout(checkpoint, binding, host)
        outcome = reconcile_model(checkpoint, http.client, outcome["request_id"],
                                  rollout=rollout, turn_id="unit-turn-1", host=host)
        self.assertEqual(outcome["application_status"], "applied")
        self.assertEqual(outcome["effective_model"]["configuration_status"], "confirmed")
        self.assertEqual(outcome["effective_model"]["reasoning_effort"], "high")
        checkpoint.native("receive", "--action", binding["action_id"], "--status", "COLLECTED",
                          "--evidence", "artifact:isolated-work")
        self.assertIsNone(checkpoint.native("status")["action"])
        reconcile(checkpoint, http.client)
        results = [event for event in http.events.values() if event["kind"] == "result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["decision_ids"], [route["decision_id"], outcome["decision_id"]])
        self.assertEqual(results[0]["execution_ref"]["ids"], {"action_id": binding["action_id"], "pane_id": "pane",
                                                             "thread_id": host.thread_id, "turn_id": "unit-turn-1"})
        self.assertEqual(results[0]["payload"]["effective_model"], outcome["effective_model"])
        current_applications = {}
        for event in http.events.values():
            if event["kind"] == "application":
                current_applications[event["event_id"]] = event
        self.assertTrue(all(event["coverage_observation"] == results[0]["coverage_observation"]
                            for event in current_applications.values()))

    def test_unknown_start_is_never_repeated_even_if_native_lookup_is_empty(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        route = choose(checkpoint, http.client)
        binding, selection, host = setup_model(checkpoint, route)
        host.start_lost = True
        http.selection = planned_candidates(selection, binding)[0]["candidate_id"]
        first = apply(checkpoint, http.client, route["unit_id"], binding, selection, host=host)
        self.assertEqual(first["launch_outcome"], "unknown")
        reopened = type(checkpoint)(checkpoint.workflow, checkpoint.state)
        second = apply(reopened, http.client, route["unit_id"], binding, selection, host=host,
                       policy_path=checkpoint.state.parent / "missing-policy.json")
        self.assertEqual(second["request_id"], first["request_id"])
        self.assertEqual(second["model_policy"], first["model_policy"])
        self.assertIsNone(second["thread_id"])
        self.assertEqual(sum(call[:2] == ("agent", "start") for call in host.calls), 1)
        host.ready = True
        third = reconcile_model(reopened, http.client, first["request_id"], host=host)
        self.assertEqual(third["thread_id"], host.thread_id)
        self.assertEqual(third["application_status"], "unknown")

    def test_successful_start_ack_does_not_bind_a_later_replacement_with_different_argv(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        route = choose(checkpoint, http.client)
        binding, selection, host = setup_model(checkpoint, route)
        http.selection = planned_candidates(selection, binding)[0]["candidate_id"]
        host.lookup_unavailable = True
        first = apply(checkpoint, http.client, route["unit_id"], binding, selection, host=host)
        self.assertEqual(first["launch_outcome"], "ready_acknowledged")
        self.assertIsNone(first["thread_id"])
        host.lookup_unavailable = False
        host.thread_id = "replacement-thread"
        host.argv = ["--no-daemon", "-m", "replacement-model"]
        recovered = reconcile_model(checkpoint, http.client, first["request_id"], host=host)
        self.assertIsNone(recovered["thread_id"])
        self.assertEqual(recovered["application_status"], "unknown")
        self.assertEqual(sum(call[:2] == ("agent", "start") for call in host.calls), 1)

    def test_empty_capability_filter_skips_service_and_host(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        route = choose(checkpoint, http.client)
        binding, selection, host = setup_model(checkpoint, route)
        selection["requirements"]["modalities"] = ["video"]
        posts = len(http.posts)
        outcome = apply(checkpoint, http.client, route["unit_id"], binding, selection, host=host)
        self.assertEqual(outcome["disposition"], "unavailable")
        self.assertEqual(len(http.posts), posts)
        self.assertEqual(host.calls, [])

    def test_defaults_and_explicit_choices_use_only_exact_allowed_capability_pairs(self):
        selection = {"requirements": {}, "capabilities": []}
        row = {"worker_id": "mac-codex", "backend_id": "current-default", "provider": "openai",
               "model_type": "reasoning", "supported_efforts": ["low", "high", "invalid"],
               "tools": [], "modalities": [], "context_window": 2048, "evidence_ref": "project:capability"}
        selection["capabilities"].extend([
            dict(row, model="gpt-6.1-sol"),
            dict(row, model="gpt-5.6-sol", supported_efforts=["high", "ultra"]),
            dict(row, model="another-model"),
            dict(row, model="gpt-5.6-sol", model_type="image_generation", supported_efforts=["low"]),
        ])
        binding = {"provider": "openai"}

        def pairs():
            return {(choice["parameters"]["model"], choice["parameters"]["reasoning_effort"])
                    for choice in planned_candidates(selection, binding)}

        self.assertEqual(pairs(), {("gpt-6.1-sol", "high")})
        selection["requirements"] = {"models": ["gpt-5.6-sol"]}
        self.assertEqual(pairs(), {("gpt-5.6-sol", "high")})
        selection["requirements"] = {"efforts": ["low"]}
        self.assertEqual(pairs(), {("gpt-6.1-sol", "low")})
        selection["requirements"] = {"models": ["gpt-6.1-sol", "gpt-5.6-sol", "another-model"],
                                     "efforts": ["low", "ultra", "invalid"]}
        self.assertEqual(pairs(), {("gpt-6.1-sol", "low"), ("gpt-5.6-sol", "ultra")})
        selection["requirements"] = {"models": ["gpt-5.6-sol"], "efforts": ["low"]}
        self.assertEqual(pairs(), set())

    def test_empty_effort_capability_cannot_authorize_null_effort(self):
        policy, ref, library = read_active_agent_policy(DEFAULT_FILE, DEFAULT_ROOT)
        entry = dict(policy["entries"][0], reasoning_effort=None)
        policy = {**policy, "entries": [entry]}
        capability = {**entry, "tools": [], "modalities": [], "context_window": 2048}
        del capability["reasoning_effort"]
        selection = {"requirements": {"efforts": [None]}, "capabilities": [capability]}
        capability["supported_efforts"] = []
        self.assertEqual(candidates(selection, "openai", policy, ref, library), [])
        capability["supported_efforts"] = [None]
        self.assertIsNone(candidates(selection, "openai", policy, ref, library)[0]["parameters"]["reasoning_effort"])

    def test_legacy_policy_without_default_model_selects_sol_only_with_capability(self):
        policy, ref, library = read_active_agent_policy(DEFAULT_FILE, DEFAULT_ROOT)
        del policy["default_environment_id"], policy["default_model"]
        entries = [row for row in policy["entries"] if row["environment_id"] == "mac-work"
                   and row["reasoning_effort"] == "high"]
        capabilities = [dict(row, supported_efforts=["high"], tools=[], modalities=[], context_window=2048)
                        for row in entries]
        selection = {"requirements": {}, "capabilities": capabilities}
        result = candidates(selection, "openai", policy, ref, library)
        self.assertEqual([row["parameters"]["model"] for row in result], ["gpt-6.1-sol"])
        selection["capabilities"] = [row for row in capabilities if row["model"] == "gpt-5.6-sol"]
        self.assertEqual(candidates(selection, "openai", policy, ref, library), [])

    def test_disabled_or_unreadable_policy_blocks_without_decision_or_native_start(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        route = choose(checkpoint, http.client)
        binding, selection, host = setup_model(checkpoint, route)
        path = checkpoint.state.parent / "agent-models.json"
        policy = json.loads(DEFAULT_FILE.read_bytes())
        posts = len(http.posts)
        for status in ("missing", "draft", "disabled"):
            with self.subTest(status=status):
                if status != "missing":
                    path.write_text(json.dumps(dict(policy, status=status)))
                outcome = apply(checkpoint, http.client, route["unit_id"], binding, selection, host=host,
                                policy_path=path)
                self.assertEqual(outcome["status"], "policy_blocked")
                self.assertEqual(outcome["launch_outcome"], "not_started")
        self.assertEqual(len(http.posts), posts)
        self.assertEqual(host.calls, [])

    def test_policy_byte_change_after_decision_blocks_before_launch_intent(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        route = choose(checkpoint, http.client)
        binding, selection, host = setup_model(checkpoint, route)
        http.selection = planned_candidates(selection, binding)[0]["candidate_id"]
        path = checkpoint.state.parent / "agent-models.json"
        original = DEFAULT_FILE.read_bytes()
        path.write_bytes(original)
        http.on_evaluate = lambda: path.write_bytes(original + b"\n")
        outcome = apply(checkpoint, http.client, route["unit_id"], binding, selection, host=host, policy_path=path)
        self.assertEqual(outcome["status"], "stale")
        self.assertEqual(outcome["reason"], "agent_model_policy_changed")
        self.assertEqual(outcome["launch_outcome"], "not_started")
        self.assertFalse(any(call[:2] == ("agent", "start") for call in host.calls))
        with checkpoint.open() as run:
            data = integration(run)
            self.assertEqual(data["launches"], {})
            entry = data["requests"][outcome["request_id"]]
            self.assertEqual(entry["model"]["application_status"], "stale")
            self.assertEqual(entry["model_policy"]["policy_sha256"], hashlib.sha256(original).hexdigest())

    def test_conflicting_provider_override_blocks_before_decision_or_native_start(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        route = choose(checkpoint, http.client)
        binding, selection, host = setup_model(checkpoint, route)
        binding["codex_args"].extend(["-c", 'model_provider="another-provider"'])
        posts = len(http.posts)
        with self.assertRaisesRegex(ValueError, "provider override conflicts"):
            apply(checkpoint, http.client, route["unit_id"], binding, selection, host=host)
        self.assertEqual(len(http.posts), posts)
        self.assertEqual(host.calls, [])

    def test_model_cannot_create_a_second_unit_for_the_same_adopted_handoff(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        route = choose(checkpoint, http.client)
        binding, selection, host = setup_model(checkpoint, route)
        with checkpoint.open() as run:
            separate_id = subject(run)["unit_id"]
        binding["unit_id"] = separate_id
        with self.assertRaisesRegex(ValueError, "unit is not associated with this handoff"):
            apply(checkpoint, http.client, separate_id, binding, selection, host=host)
        self.assertEqual(host.calls, [])

    def test_actual_effort_mismatch_and_cancelled_result_are_reported_from_source(self):
        checkpoint = example_checkpoint(self)
        http = ServiceDouble(self)
        route = choose(checkpoint, http.client)
        binding, selection, host = setup_model(checkpoint, route)
        http.selection = planned_candidates(selection, binding)[0]["candidate_id"]
        outcome = apply(checkpoint, http.client, route["unit_id"], binding, selection, host=host)
        rollout = example_rollout(checkpoint, binding, host, effort="medium")
        observed = reconcile_model(checkpoint, http.client, outcome["request_id"],
                                   rollout=rollout, turn_id="unit-turn-1", host=host)
        self.assertEqual(observed["application_status"], "not_applied")
        self.assertEqual(observed["effective_model"]["reasoning_effort"], "medium")
        self.assertFalse(any(event["kind"] == "result" for event in http.events.values()))
        record_result(checkpoint, route["unit_id"], "cancelled", [], ["native:cancel-completed"])
        reconcile(checkpoint, http.client)
        result = http.events[(route["unit_id"] + ".result", 1)]
        self.assertEqual(result["payload"]["execution_status"], "cancelled")
        self.assertEqual(result["payload"]["quality_status"], "pending")
        self.assertEqual(result["decision_ids"], [route["decision_id"], outcome["decision_id"]])
        self.assertEqual(checkpoint.native("status")["status"], "WAITING_RECEIPT")


if __name__ == "__main__":
    unittest.main()
