"""Outcome tests for the opt-in Lead adapter; no real native command is run."""

import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import worker_library
import herdr_executor


def canonical_hash(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


class InjectedProcessInterruption(BaseException):
    """Exercises interrupt-style errors, including the adapter's final cleanup."""


class NativeFixture:
    """The only fake: Herdr's argv/JSON boundary, with observable prompt effects."""

    def __init__(self, cwd, *, agent_name="contract-agent", pane_id="contract-pane"):
        self.cwd = str(cwd)
        self.agent_name = agent_name
        self.pane_id = pane_id
        self.calls = []
        self.prompts = []
        self.fail_before_prompt = False
        self.fail_after_prompt = False
        self.crash_before_prompt = False
        self.crash_after_prompt = False
        self.old_turn = "Earlier unrelated response; never this intent's result."

    def agent(self):
        return {
            "name": self.agent_name, "agent": "codex", "pane_id": self.pane_id,
            "cwd": self.cwd, "foreground_cwd": self.cwd,
            "agent_status": "idle", "interactive_ready": True,
            "launch_pending": False, "terminal_id": "contract-terminal",
            "workspace_id": "contract-workspace", "tab_id": "contract-tab",
            "revision": 7, "last_response": self.old_turn,
        }

    def __call__(self, argv, *, timeout):
        self.calls.append((list(argv), timeout))
        command = argv[3:]
        if command[:2] == ["agent", "prompt"]:
            if self.crash_before_prompt:
                raise InjectedProcessInterruption()
            if self.fail_before_prompt:
                raise OSError("fake send boundary unavailable")
            self.prompts.append(list(argv))
            if self.crash_after_prompt:
                raise InjectedProcessInterruption()
            if self.fail_after_prompt:
                raise TimeoutError("fake response lost after native acceptance")
            result = {"type": "agent_prompted", "agent": self.agent()}
        elif command[:2] == ["agent", "get"]:
            result = {"type": "agent_info", "agent": self.agent()}
        elif command[:2] == ["pane", "get"]:
            pane = self.agent()
            pane.pop("name")
            pane["focused"] = False
            result = {"type": "pane_info", "pane": pane}
        else:
            raise AssertionError("Unexpected native operation in the opt-in adapter")
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"id": "fixture-native-response", "result": result}), "",
        )


class ExecutorContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.workspace = self.directory / "workspace"
        self.workspace.mkdir()
        self.state_dir = self.directory / "state"
        self.output = self.workspace / "artifact.txt"
        self.manifest = self.workspace / "result-manifest.json"
        self.now = datetime(2026, 11, 3, 2, 0, 0, tzinfo=timezone.utc)
        self.native = NativeFixture(self.workspace)
        self.target = {
            "session": "contract-session", "pane_id": "contract-pane",
            "agent_name": "contract-agent", "cwd": str(self.workspace),
        }
        self.policy = {
            "schema_version": 1, "repository": "CF-FE/herdr-4-codexcloud",
            "project_id": "herdr-4-codexcloud",
            "herdr_binary": str(self.directory / "fake-herdr-not-executable"),
            "allowed_roots": [str(self.workspace)],
            "targets": [{
                **self.target, "worker_id": "mac-codex", "backend_id": "current-default",
                "agent_kind": "codex", "provider": "openai", "model": "test-model",
                "auth_ref": "lead:auth-checked", "permissions_ref": "lead:permissions-checked",
            }],
        }
        self.task = {
            "task_id": "contract-task", "project_id": "herdr-4-codexcloud",
            "goal": "Write the authorized bounded artifact and its result manifest.",
            "authorization_ref": "review:explicit-opt-in", "dispatch_owner": "project-lead",
            "report_to": "review-contract", "report_route": "original-contract-task",
            "owned_paths": [str(self.workspace)], "input_refs": ["fixture-input@fixed-v1"],
            "acceptance_criteria": ["The saved artifact matches the reported bytes and hash."],
            "output_refs": [str(self.output)],
        }

    def draft(self, task=None):
        task_file = self.directory / "task.json"
        task_file.write_text(json.dumps(task or self.task), encoding="utf-8")
        library = worker_library.load_library(worker_library.DEFAULT_ROOT)
        role = worker_library.render_role(library, "software_implementation", None, render=True)
        return worker_library.prepare(library, SimpleNamespace(
            worker="mac-codex", backend="current-default", project=None, task_file=task_file,
        ), role)

    def executor(self, *, policy=None, native=None):
        return herdr_executor.Executor(
            self.state_dir, self.policy if policy is None else policy,
            catalog_root=worker_library.DEFAULT_ROOT,
            transport=self.native if native is None else native, clock=lambda: self.now,
        )

    def envelope(self, intent_id="contract-intent", *, task=None, target=None):
        task = copy.deepcopy(self.task if task is None else task)
        target = copy.deepcopy(self.target if target is None else target)
        payload = {
            "schema_version": 1, "origin_task_id": task["task_id"],
            "sender": "dot", "recipient": "mac-coordinator",
            "report_route": task["report_route"], "target": target,
            "tracking": {
                "repository": "CF-FE/herdr-4-codexcloud",
                # Fictional fixture references, not the real Project or its items.
                "project_url": "https://github.com/orgs/CF-FE/projects/987654321",
                "item_id": 987654321,
                "issue_url": "https://github.com/CF-FE/herdr-4-codexcloud/issues/987654321",
                "iteration": {"state": "UNSCHEDULED", "reason": "Explicit test fixture."},
            },
            "content": "Produce the bound artifact; literal input $(never-executed); 'quoted'.",
        }
        return {
            "schema_version": 1, "intent_id": intent_id,
            "job": {"job_id": "job-" + intent_id, "payload_hash": canonical_hash(payload),
                    "payload": payload},
            "draft": self.draft(task), "target": target,
            "authority": {
                "task_ref": task["task_id"], "lead": "project-lead", "writer": "contract-agent",
                "tracking_read_ref": "lead:tracking-read", "owned_paths": task["owned_paths"],
                "start_before": "2026-11-03T02:01:00Z", "deadline": "2026-11-03T02:02:00Z",
            },
            "budget": {"model_turns": 1, "rpc_calls": 12, "timeout_seconds": 10},
            "outputs": {"manifest_path": str(self.manifest), "paths": task["output_refs"]},
        }

    @staticmethod
    def rehash_payload(envelope):
        envelope["job"]["payload_hash"] = canonical_hash(envelope["job"]["payload"])

    def dispatch(self, executor, envelope):
        return executor.dispatch(envelope, approve_sha256=canonical_hash(envelope))

    def write_result(self, envelope, *, content=b"bounded artifact\n", mutate=None):
        self.output.write_bytes(content)
        manifest = {
            "schema_version": 1, "intent_id": envelope["intent_id"],
            "job_id": envelope["job"]["job_id"],
            "payload_hash": envelope["job"]["payload_hash"], "status": "REPORTED_COMPLETED",
            "files": [{"path": str(self.output), "sha256": hashlib.sha256(content).hexdigest(),
                       "bytes": len(content)}],
            "summary": "The bounded artifact was produced; Lead acceptance remains separate.",
        }
        if mutate:
            mutate(manifest)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def corrupt_temporary_journal(self, intent_id, mutation, *, replace_digest=False):
        """Fault injection only; these tests make no claim against same-user tampering."""
        connection = sqlite3.connect(self.state_dir / "executor.sqlite3")
        try:
            row = connection.execute("SELECT envelope FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            self.assertIsNotNone(row)
            envelope = json.loads(row[0])
            mutation(envelope)
            encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if replace_digest:
                connection.execute("UPDATE intents SET envelope=?,input_hash=? WHERE intent_id=?",
                                   (encoded, canonical_hash(envelope), intent_id))
            else:
                connection.execute("UPDATE intents SET envelope=? WHERE intent_id=?", (encoded, intent_id))
            connection.commit()
        finally:
            connection.close()

    def test_invalid_authority_hash_placeholder_project_and_target_never_prompt(self):
        def no_authority(value):
            del value["authority"]

        def wrong_hash(value):
            value["job"]["payload_hash"] = "0" * 64

        def placeholder(value):
            value["target"]["pane_id"] = "<ACTUAL_PANE_ID>"
            value["job"]["payload"]["target"] = copy.deepcopy(value["target"])
            self.rehash_payload(value)

        def wrong_project(value):
            value["job"]["payload"]["tracking"]["repository"] = "CF-FE/another-project"
            self.rehash_payload(value)

        def unknown_target(value):
            value["target"]["agent_name"] = "unapproved-agent"
            value["job"]["payload"]["target"] = copy.deepcopy(value["target"])
            self.rehash_payload(value)

        for mutation in (no_authority, wrong_hash, placeholder, wrong_project, unknown_target):
            with self.subTest(mutation=mutation.__name__):
                value = self.envelope(mutation.__name__)
                mutation(value)
                with self.assertRaises(herdr_executor.ExecutorError):
                    self.dispatch(self.executor(), value)
                self.assertEqual(self.native.prompts, [])

    def test_approval_binds_the_entire_envelope(self):
        value = self.envelope()
        with self.assertRaises(herdr_executor.ExecutorError):
            self.executor().dispatch(value, approve_sha256="0" * 64)
        self.assertEqual(self.native.prompts, [])

    def test_payload_target_cannot_differ_from_the_approved_native_target(self):
        value = self.envelope()
        other_target = dict(self.target, pane_id="another-pane", agent_name="another-agent")
        self.policy["targets"].append({**self.policy["targets"][0], **other_target})
        value["job"]["payload"]["target"] = other_target
        self.rehash_payload(value)
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            self.dispatch(self.executor(), value)
        self.assertEqual(caught.exception.code, "PAYLOAD_TARGET_MISMATCH")
        self.assertEqual(self.native.calls, [])

    def test_removing_target_permission_stops_a_saved_intents_native_queries(self):
        value = self.envelope()
        self.dispatch(self.executor(), value)
        call_count = len(self.native.calls)
        changed_policy = copy.deepcopy(self.policy)
        changed_policy["targets"][0].update(pane_id="replacement-pane", agent_name="replacement-agent")
        resumed = self.executor(policy=changed_policy)
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            resumed.inspect(value["intent_id"])
        self.assertEqual(caught.exception.code, "TARGET_NOT_ALLOWED")
        self.assertEqual(len(self.native.calls), call_count)

    def test_collect_rejects_journal_drift_toward_an_unowned_artifact(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        outside = self.directory / "outside-owned-paths.txt"
        data = b"Temporary unowned artifact must not become an approved output.\n"
        outside.write_bytes(data)
        self.corrupt_temporary_journal(
            value["intent_id"], lambda saved: saved["outputs"].update(paths=[str(outside)]),
        )
        self.manifest.write_text(json.dumps({
            "schema_version": 1, "intent_id": value["intent_id"],
            "job_id": value["job"]["job_id"], "payload_hash": value["job"]["payload_hash"],
            "status": "REPORTED_COMPLETED", "summary": "Unapproved drift fixture.",
            "files": [{"path": str(outside), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}],
        }), encoding="utf-8")
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            executor.collect(value["intent_id"])
        self.assertEqual(caught.exception.code, "JOURNAL_INTEGRITY_ERROR")

    def test_inspect_rejects_changed_journal_body_before_any_native_query(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        call_count = len(self.native.calls)
        self.corrupt_temporary_journal(
            value["intent_id"], lambda saved: saved["outputs"].update(paths=[str(self.directory / "unowned.txt")]),
        )
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            executor.inspect(value["intent_id"])
        self.assertEqual(caught.exception.code, "JOURNAL_INTEGRITY_ERROR")
        self.assertEqual(len(self.native.calls), call_count)

    def test_duplicate_dispatch_rejects_damaged_original_journal_body(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        call_count = len(self.native.calls)
        self.corrupt_temporary_journal(
            value["intent_id"], lambda saved: saved["draft"]["task"].update(goal="Accidental stored body drift."),
        )
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            self.dispatch(executor, value)
        self.assertEqual(caught.exception.code, "JOURNAL_INTEGRITY_ERROR")
        self.assertEqual(len(self.native.calls), call_count)
        self.assertEqual(len(self.native.prompts), 1)

    def test_journal_intent_must_match_its_record_even_when_body_checksum_matches(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        call_count = len(self.native.calls)
        self.corrupt_temporary_journal(
            value["intent_id"], lambda saved: saved.update(intent_id="another-intent"), replace_digest=True,
        )
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            executor.inspect(value["intent_id"])
        self.assertEqual(caught.exception.code, "JOURNAL_INTEGRITY_ERROR")
        self.assertEqual(len(self.native.calls), call_count)

    def test_dispatch_uses_explicit_argv_and_literal_prompt_without_shell(self):
        value = self.envelope()
        result = self.dispatch(self.executor(), value)
        self.assertEqual(result["state"], "SUBMITTED")
        self.assertEqual(len(self.native.prompts), 1)
        command = self.native.prompts[0]
        self.assertEqual(command[:6], [self.policy["herdr_binary"], "--session",
                                     self.target["session"], "agent", "prompt", self.target["agent_name"]])
        self.assertEqual(len(command), 7, "The complete prompt must be one literal argv element")
        self.assertIn("$(never-executed)", command[6])
        self.assertNotIn("--wait", command)
        self.assertTrue(all(argv[:3] == command[:3] for argv, _ in self.native.calls))
        self.assertTrue(any(argv[3:] == ["pane", "get", self.target["pane_id"]]
                            for argv, _ in self.native.calls))

    def test_worker_receives_the_complete_lead_approved_task_contract(self):
        task = copy.deepcopy(self.task)
        task.update(
            goal="GOAL_SENTINEL_e1b86a_write_only_the_bound_artifact",
            input_refs=["INPUT_SENTINEL_8d490a@frozen-version"],
            acceptance_criteria=["ACCEPTANCE_SENTINEL_352fb1_verify_fixed_input"],
            report_to="REPORT_TO_SENTINEL_219d7c_original-reviewer",
        )
        self.assertEqual(self.dispatch(self.executor(), self.envelope(task=task))["state"], "SUBMITTED")
        prompt = self.native.prompts[0][-1]
        for sentinel in (task["goal"], task["input_refs"][0],
                         task["acceptance_criteria"][0], task["report_to"]):
            with self.subTest(contract_value=sentinel):
                self.assertTrue(sentinel in prompt, "Missing approved task value: " + sentinel)

    def test_same_intent_is_single_send_and_changed_content_conflicts(self):
        value = self.envelope()
        first = self.dispatch(self.executor(), value)
        second = self.dispatch(self.executor(), copy.deepcopy(value))
        self.assertEqual(first["intent_id"], second["intent_id"])
        self.assertEqual(len(self.native.prompts), 1)
        changed = copy.deepcopy(value)
        changed["job"]["payload"]["content"] = "Changed task content must not overwrite the intent."
        self.rehash_payload(changed)
        with self.assertRaises(herdr_executor.ExecutorError):
            self.dispatch(self.executor(), changed)
        self.assertEqual(len(self.native.prompts), 1)

    def test_prompt_errors_before_or_after_acceptance_are_unknown_without_resend(self):
        for accepted in (False, True):
            with self.subTest(native_accepted=accepted):
                self.state_dir = self.directory / ("state-after" if accepted else "state-before")
                native = NativeFixture(self.workspace)
                native.fail_before_prompt = not accepted
                native.fail_after_prompt = accepted
                executor = self.executor(native=native)
                value = self.envelope("after" if accepted else "before")
                first = self.dispatch(executor, value)
                self.assertEqual(first["state"], "UNKNOWN")
                native.fail_before_prompt = native.fail_after_prompt = False
                resumed = self.executor(native=native)
                self.assertEqual(resumed.inspect(value["intent_id"])["state"], "UNKNOWN")
                self.assertEqual(self.dispatch(resumed, value)["state"], "UNKNOWN")
                prompt_attempts = [argv for argv, _ in native.calls if argv[3:5] == ["agent", "prompt"]]
                self.assertEqual(len(prompt_attempts), 1)
                self.assertEqual(len(native.prompts), int(accepted))

    def test_idle_and_an_old_turn_do_not_supply_this_intents_result(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        with self.assertRaises(herdr_executor.ExecutorError):
            executor.collect(value["intent_id"])
        self.assertEqual(executor.inspect(value["intent_id"])["state"], "SUBMITTED")
        self.assertEqual(len(self.native.prompts), 1)

    def test_interrupt_exception_stays_unknown_on_restart_without_another_prompt(self):
        for accepted in (False, True):
            with self.subTest(native_accepted=accepted):
                self.state_dir = self.directory / ("crash-after" if accepted else "crash-before")
                native = NativeFixture(self.workspace)
                native.crash_before_prompt = not accepted
                native.crash_after_prompt = accepted
                value = self.envelope("crash-after" if accepted else "crash-before")
                with self.assertRaises(InjectedProcessInterruption):
                    self.dispatch(self.executor(native=native), value)
                native.crash_before_prompt = native.crash_after_prompt = False
                resumed = self.executor(native=native)
                self.assertEqual(resumed.inspect(value["intent_id"])["state"], "UNKNOWN")
                self.assertEqual(self.dispatch(resumed, value)["state"], "UNKNOWN")
                attempts = [argv for argv, _ in native.calls if argv[3:5] == ["agent", "prompt"]]
                self.assertEqual(len(attempts), 1)
                self.assertEqual(len(native.prompts), int(accepted))

    def test_hard_process_exit_recovers_unresolved_send_without_resending(self):
        child_code = r'''
import json, os, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_herdr_executor_contract import NativeFixture, canonical_hash, herdr_executor
request = json.loads(Path(sys.argv[2]).read_text())
class HardExitNative(NativeFixture):
    def __call__(self, argv, *, timeout):
        if argv[3:5] == ["agent", "prompt"]:
            with open(request["attempt_log"], "ab", buffering=0) as handle:
                handle.write(b"attempt\n")
                if request["accepted"]:
                    handle.write(b"accepted\n")
                os.fsync(handle.fileno())
            os._exit(73)
        return super().__call__(argv, timeout=timeout)
executor = herdr_executor.Executor(
    request["state_dir"], request["policy"], catalog_root=request["catalog_root"],
    transport=HardExitNative(request["cwd"]),
    clock=lambda: datetime.fromisoformat(request["now"]),
)
executor.dispatch(request["envelope"], approve_sha256=canonical_hash(request["envelope"]))
raise AssertionError("The fake must exit inside the native send boundary")
'''
        for accepted in (False, True):
            with self.subTest(native_accepted=accepted):
                name = "hard-after" if accepted else "hard-before"
                self.state_dir = self.directory / name
                value = self.envelope(name)
                attempt_log = self.directory / (name + ".attempts")
                request_file = self.directory / (name + ".json")
                request_file.write_text(json.dumps({
                    "accepted": accepted, "attempt_log": str(attempt_log),
                    "state_dir": str(self.state_dir), "policy": self.policy,
                    "catalog_root": str(worker_library.DEFAULT_ROOT),
                    "cwd": str(self.workspace), "now": self.now.isoformat(), "envelope": value,
                }), encoding="utf-8")
                child = subprocess.run(
                    [sys.executable, "-B", "-c", child_code, str(ROOT / "tests"), str(request_file)],
                    shell=False, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(child.returncode, 73, child.stdout + child.stderr)
                expected_log = "attempt\naccepted\n" if accepted else "attempt\n"
                self.assertEqual(attempt_log.read_text(), expected_log)
                native = NativeFixture(self.workspace)
                resumed = self.executor(native=native)
                self.assertEqual(resumed.inspect(value["intent_id"])["state"], "UNKNOWN")
                self.assertEqual(self.dispatch(resumed, value)["state"], "UNKNOWN")
                self.assertEqual(native.prompts, [])
                self.assertEqual(attempt_log.read_text(), expected_log)

    def test_cancel_before_send_blocks_dispatch(self):
        value = self.envelope()
        executor = self.executor()
        self.assertEqual(executor.preflight(value)["state"], "PREPARED")
        self.assertEqual(executor.cancel(value["intent_id"])["state"], "CANCELLED")
        self.assertEqual(self.dispatch(executor, value)["state"], "CANCELLED")
        self.assertEqual(self.native.prompts, [])

    def test_cancel_after_send_only_requests_and_does_not_stop_native_agent(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        result = executor.cancel(value["intent_id"])
        self.assertEqual(result["state"], "CANCEL_REQUESTED")
        self.assertEqual(len(self.native.prompts), 1)
        self.assertFalse(any(token in {"stop", "kill", "interrupt", "close"}
                             for argv, _ in self.native.calls for token in argv[3:6]))

    def test_collection_keeps_the_original_cancellation_request_and_timestamp(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        cancelled = executor.cancel(value["intent_id"])
        self.assertIs(cancelled["cancel_requested"], True)
        requested_at = cancelled["cancel_requested_at"]
        self.assertEqual(datetime.fromisoformat(requested_at.replace("Z", "+00:00")), self.now)
        self.now += timedelta(seconds=5)
        self.write_result(value)
        collected = executor.collect(value["intent_id"])
        self.assertEqual(collected["state"], "COLLECTED")
        self.assertIs(collected["cancel_requested"], True)
        self.assertEqual(collected["cancel_requested_at"], requested_at)
        saved = self.executor().inspect(value["intent_id"])
        self.assertIs(saved["cancel_requested"], True)
        self.assertEqual(saved["cancel_requested_at"], requested_at)

    def test_manifest_wrong_association_hash_path_or_size_cannot_collect(self):
        mutations = {
            "intent": lambda value: value.update(intent_id="another-intent"),
            "job": lambda value: value.update(job_id="another-job"),
            "payload": lambda value: value.update(payload_hash="0" * 64),
            "digest": lambda value: value["files"][0].update(sha256="0" * 64),
            "path": lambda value: value["files"][0].update(path=str(self.directory / "outside.txt")),
            "size": lambda value: value["files"][0].update(bytes=999),
        }
        for name, mutation in mutations.items():
            with self.subTest(invalid_manifest=name):
                if self.output.exists():
                    self.output.unlink()
                if self.manifest.exists():
                    self.manifest.unlink()
                self.state_dir = self.directory / ("state-manifest-" + name)
                value = self.envelope("manifest-" + name)
                executor = self.executor()
                self.dispatch(executor, value)
                self.write_result(value, mutate=mutation)
                with self.assertRaises(herdr_executor.ExecutorError):
                    executor.collect(value["intent_id"])
                self.assertEqual(executor.inspect(value["intent_id"])["state"], "SUBMITTED")

    def test_actual_artifact_collects_but_only_lead_can_explicitly_release(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        manifest = self.write_result(value)
        result = executor.collect(value["intent_id"])
        self.assertEqual(result["state"], "COLLECTED")
        self.assertEqual(result["artifacts"]["summary"], manifest["summary"])
        self.assertNotEqual((result.get("hand_back") or {}).get("state"), "LEAD_REPORTED_HAND_BACK")
        self.assertEqual(self.executor().inspect(value["intent_id"])["state"], "COLLECTED")
        released = executor.release(value["intent_id"], evidence_ref="lead:reviewed-artifact-v1")
        self.assertEqual(released["state"], "RELEASED")
        self.assertEqual(released["next_owner"], "PROJECT_LEAD")
        self.assertEqual(released["hand_back"]["state"], "LEAD_REPORTED_HAND_BACK")
        self.assertEqual(released["hand_back"]["evidence_ref"], "lead:reviewed-artifact-v1")

    def test_failed_report_without_artifacts_collects_and_waits_for_lead_release(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        self.write_result(value, mutate=lambda report: report.update(status="REPORTED_FAILED", files=[]))
        self.output.unlink()
        result = executor.collect(value["intent_id"])
        self.assertEqual(result["state"], "COLLECTED")
        self.assertEqual(result["artifacts"]["status"], "REPORTED_FAILED")
        self.assertEqual(result["artifacts"]["files"], [])
        self.assertNotEqual((result.get("hand_back") or {}).get("state"), "LEAD_REPORTED_HAND_BACK")
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            self.dispatch(executor, self.envelope("blocked-until-failure-release"))
        self.assertEqual(caught.exception.code, "RESOURCE_CONFLICT")
        self.assertEqual(executor.release(value["intent_id"], "lead:reviewed-failure")["state"], "RELEASED")

    def test_failed_report_may_preserve_a_verified_planned_subset(self):
        missing = self.workspace / "not-produced.txt"
        task = dict(self.task, output_refs=[str(self.output), str(missing)])
        value = self.envelope(task=task)
        executor = self.executor()
        self.dispatch(executor, value)
        report = self.write_result(value, mutate=lambda result: result.update(status="REPORTED_FAILED"))
        self.assertFalse(missing.exists())
        result = executor.collect(value["intent_id"])
        self.assertEqual(result["state"], "COLLECTED")
        self.assertEqual(result["artifacts"]["status"], "REPORTED_FAILED")
        self.assertEqual(result["artifacts"]["files"], report["files"])
        self.assertNotEqual((result.get("hand_back") or {}).get("state"), "LEAD_REPORTED_HAND_BACK")

    def test_completed_report_requires_every_planned_artifact(self):
        task = dict(self.task, output_refs=[str(self.output), str(self.workspace / "missing.txt")])
        value = self.envelope(task=task)
        executor = self.executor()
        self.dispatch(executor, value)
        self.write_result(value)
        with self.assertRaises(herdr_executor.ExecutorError):
            executor.collect(value["intent_id"])
        self.assertEqual(executor.inspect(value["intent_id"])["state"], "SUBMITTED")

    def test_failed_report_still_rejects_unplanned_duplicate_or_invalid_artifacts(self):
        mutations = {
            "extra": lambda report: report["files"][0].update(path=str(self.workspace / "unplanned.txt")),
            "duplicate": lambda report: report["files"].append(copy.deepcopy(report["files"][0])),
            "digest": lambda report: report["files"][0].update(sha256="0" * 64),
        }
        for name, mutation in mutations.items():
            with self.subTest(invalid_failure=name):
                self.output.unlink(missing_ok=True)
                self.manifest.unlink(missing_ok=True)
                self.state_dir = self.directory / ("failure-invalid-" + name)
                value = self.envelope("failure-invalid-" + name)
                executor = self.executor()
                self.dispatch(executor, value)
                report = self.write_result(value)
                report["status"] = "REPORTED_FAILED"
                mutation(report)
                self.manifest.write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaises(herdr_executor.ExecutorError):
                    executor.collect(value["intent_id"])
                self.assertEqual(executor.inspect(value["intent_id"])["state"], "SUBMITTED")

    def test_release_without_collected_artifacts_is_rejected(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        with self.assertRaises(herdr_executor.ExecutorError):
            executor.release(value["intent_id"], evidence_ref="lead:premature")
        self.assertEqual(executor.inspect(value["intent_id"])["state"], "SUBMITTED")

    def test_pending_intent_reserves_the_target_and_overlapping_owned_paths(self):
        value = self.envelope()
        executor = self.executor()
        self.dispatch(executor, value)
        other_directory = self.workspace / "disjoint-work"
        other_directory.mkdir()
        other_task = dict(self.task, owned_paths=[str(other_directory)],
                          output_refs=[str(other_directory / "artifact.txt")])
        same_target = self.envelope("second-same-target", task=other_task)
        same_target["outputs"]["manifest_path"] = str(other_directory / "result-manifest.json")
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            self.dispatch(executor, same_target)
        self.assertEqual(caught.exception.code, "RESOURCE_CONFLICT")
        other_target = dict(self.target, pane_id="another-pane", agent_name="another-agent")
        self.policy["targets"].append({**self.policy["targets"][0], **other_target})
        other_native = NativeFixture(self.workspace, agent_name="another-agent", pane_id="another-pane")
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            self.dispatch(self.executor(native=other_native),
                          self.envelope("second-same-paths", target=other_target))
        self.assertEqual(caught.exception.code, "RESOURCE_CONFLICT")
        self.assertEqual(len(self.native.prompts), 1)
        self.assertEqual(other_native.prompts, [])

    def test_rpc_budget_survives_restart_and_does_not_allow_additional_native_calls(self):
        value = self.envelope()
        value["budget"]["rpc_calls"] = 3
        self.dispatch(self.executor(), value)
        self.assertEqual(len(self.native.calls), 3)
        resumed = self.executor()
        with self.assertRaises(herdr_executor.ExecutorError) as caught:
            resumed.inspect(value["intent_id"])
        self.assertEqual(caught.exception.code, "RPC_BUDGET_EXHAUSTED")
        self.assertEqual(len(self.native.calls), 3)
        self.assertEqual(len(self.native.prompts), 1)

    def test_expired_unknown_execution_can_be_inspected_without_another_send(self):
        value = self.envelope()
        self.native.fail_after_prompt = True
        self.assertEqual(self.dispatch(self.executor(), value)["state"], "UNKNOWN")
        self.native.fail_after_prompt = False
        self.now += timedelta(minutes=3)
        call_count = len(self.native.calls)
        resumed = self.executor()
        result = resumed.inspect(value["intent_id"])
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(len(self.native.calls), call_count + 1)
        self.assertGreater(self.native.calls[-1][1], 0)
        self.assertLessEqual(self.native.calls[-1][1], value["budget"]["timeout_seconds"])
        self.assertEqual(self.dispatch(resumed, value)["state"], "UNKNOWN")
        self.assertEqual(len(self.native.prompts), 1)

    def test_prepared_intent_does_not_start_after_its_start_deadline(self):
        value = self.envelope()
        executor = self.executor()
        self.assertEqual(executor.preflight(value)["state"], "PREPARED")
        self.now += timedelta(minutes=3)
        self.assertEqual(self.dispatch(executor, value)["state"], "EXPIRED")
        self.assertEqual(self.native.prompts, [])

    def test_real_transport_refuses_outside_herdr_without_launching_a_process(self):
        binary = Path(self.policy["herdr_binary"])
        binary.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        binary.chmod(0o700)
        policy_path = self.directory / "policy.json"
        policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        policy_path.chmod(0o600)
        with patch.dict(os.environ, {"HERDR_ENV": "0"}), \
                patch("subprocess.run", side_effect=AssertionError("No native process is authorized")):
            executor = herdr_executor.Executor(
                self.state_dir, policy_path, catalog_root=worker_library.DEFAULT_ROOT,
                clock=lambda: self.now,
            )
            result = self.dispatch(executor, self.envelope())
        self.assertEqual(result["state"], "FAILED_NOT_DISPATCHED")
        self.assertEqual(result["detail"], "HERDR_CONTEXT_REQUIRED")


if __name__ == "__main__":
    unittest.main()
