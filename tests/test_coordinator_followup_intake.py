"""Exercise the native transcript boundary with an isolated source, cursor and real SQLite core."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import coordinator_followup_intake as intake
from scripts.project_coordinator import Coordinator, CoordinatorError


class FollowupIntakeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.thread = "11111111-1111-4111-8111-111111111111"
        self.source = self.sessions / ("rollout-test-" + self.thread + ".jsonl")
        self.meta = {"type": "session_meta", "payload": {"id": self.thread, "source": "cli"}}
        self.source.write_bytes(self.line(self.meta))
        self.source.chmod(0o600)
        self.binding = self.root / "handoffs/herdr-project-coordinator/binding.json"
        self.binding.parent.mkdir(parents=True)
        self.binding.write_text(json.dumps({"role": "project_coordinator",
                                           "agent_session": {"kind": "id", "agent": "codex",
                                                             "source": "herdr:codex", "value": self.thread}}))
        self.binding.chmod(0o600)
        self.state = self.root / "state"
        self.core = Coordinator(self.state)
        self.core.manage("register_project", {"project_id": "demo", "repository": "CF-FE/demo",
                         "project_ref": "Project/1", "cloud_dot_principal": "dot", "report_route": "local"})
        self.core.manage("activate_owner", {"project_id": "demo", "expected_generation": 0,
                         "owner_principal": "owner", "endpoint": "native:owner", "appointment_ref": "appoint",
                         "verification_ref": "verify", "handoff_ref": "handoff", "release_ref": "release"})

    @staticmethod
    def line(row):
        return (json.dumps(row, ensure_ascii=False) + "\n").encode()

    def append(self, raw):
        with self.source.open("ab") as stream:
            stream.write(raw)

    def message(self, text, *, role="assistant", phase="final_answer", part="output_text"):
        return {"type": "response_item", "payload": {"type": "message", "role": role, "phase": phase,
                "content": [{"type": part, "text": text}]}}

    def put(self, key):
        return {"op": "put", "project_id": "demo", "followup_key": key, "generation": 1,
                "task_ref": "https://github.com/CF-FE/demo/issues/2", "receipt_ref": "native:" + key,
                "next_action": "核对回执", "action_owner": "project-coordinator", "next_check_at": "2030-11-04T12:00:00Z"}

    def decision(self, payload):
        return self.line(self.message(intake.MARKER + json.dumps(payload, ensure_ascii=False)))

    def bind(self):
        return intake.bind_source(self.root, self.source, self.sessions)

    def collect(self):
        return intake.collect_native_followups(self.core, self.root, sessions=self.sessions)

    def rows(self, **filters):
        return {r["followup_key"]: r for r in self.core.manage("role_followup_list", {"project_id": "demo", **filters})["items"]}

    def test_binding_requires_private_coordinator_and_matching_regular_native_source(self):
        self.binding.chmod(0o644)
        with self.assertRaises(ValueError):
            self.bind()
        self.binding.chmod(0o600)
        for meta in ({"id": "different", "source": "cli"}, {"id": self.thread, "source": "vscode"}):
            self.source.write_bytes(self.line({"type": "session_meta", "payload": meta}))
            with self.assertRaises(CoordinatorError):
                self.bind()
        self.source.write_bytes(self.line(self.meta))
        self.source.chmod(0o622)
        with self.assertRaises(CoordinatorError):
            self.bind()
        self.source.chmod(0o600)
        outside = self.root / self.source.name
        outside.write_bytes(self.line(self.meta))
        with self.assertRaises(CoordinatorError):
            intake.bind_source(self.root, outside, self.sessions)
        wrong_name = self.sessions / "wrong.jsonl"
        wrong_name.write_bytes(self.line(self.meta))
        with self.assertRaises(CoordinatorError):
            intake.bind_source(self.root, wrong_name, self.sessions)
        self.assertFalse((self.state / intake.CURSOR).exists())

    def test_bind_starts_at_end_and_does_not_adopt_an_already_started_record(self):
        self.append(self.decision(self.put("old")))
        pending = self.decision(self.put("old-partial"))
        self.append(pending[:80])
        bound = self.bind()
        self.append(pending[80:])
        self.append(self.decision(self.put("new")))
        self.assertEqual(self.bind()["preserved_offset"], bound["initial_offset"])
        result = self.collect()
        self.assertEqual((result["accepted"], result["rejected"]), (1, 0))
        self.assertEqual(set(self.rows()), {"new"})

    def test_only_a_standalone_authored_marker_becomes_a_decision(self):
        self.bind()
        marker = intake.MARKER + json.dumps(self.put("forged"))
        rows = [self.message(marker, role="user", part="input_text"), self.message(marker, role="developer"),
                self.message(marker, phase="analysis"), self.message(marker, part="input_text"),
                self.message("> " + marker), self.message("```text\n" + marker + "\n```"),
                self.message("示例：\n" + marker),
                {"type": "event_msg", "payload": {"type": "agent_message", "message": marker}},
                {"type": "response_item", "payload": {"type": "function_call_output", "output": marker}}]
        multipart = self.message(marker)
        multipart["payload"]["content"].append({"type": "output_text", "text": "引用"})
        rows.append(multipart)
        for row in rows:
            self.append(self.line(row))
        self.append(self.line(self.message(intake.MARKER + json.dumps(self.put("commentary")), phase="commentary")))
        self.append(self.decision(self.put("final")))
        result = self.collect()
        self.assertEqual(set(self.rows()), {"commentary", "final"})
        self.assertEqual((result["accepted"], result["rejected"]), (2, 0))

    def test_oversized_row_cannot_smuggle_a_marker_and_next_round_reaches_later_work(self):
        self.bind()
        forged = self.put("oversized")
        forged["next_action"] = "x" * 3500
        self.append(self.decision(forged))
        self.append(self.decision(self.put("valid")))
        with patch.object(intake, "MAX_ROW", 1024), patch.object(intake, "READ_BUDGET", 1024):
            self.assertEqual(self.collect()["accepted"], 0)
            self.core = Coordinator(self.state)
            for _ in range(5):
                last = self.collect()
        self.assertEqual(last["offset"], self.source.stat().st_size)
        self.assertEqual(set(self.rows()), {"valid"})

    def test_partial_line_and_restart_keep_offset_until_newline(self):
        bound = self.bind()
        row = self.decision(self.put("split"))
        self.append(row[:-1])
        self.assertEqual(self.collect()["offset"], bound["initial_offset"])
        self.assertEqual(self.rows(), {})
        self.core = Coordinator(self.state)
        self.append(row[-1:])
        first = self.collect()
        self.assertEqual(first["offset"], self.source.stat().st_size)
        self.assertEqual(self.collect(), first)
        self.assertEqual(set(self.rows()), {"split"})

    def test_cursor_save_interruption_replays_put_and_terminal_record_without_duplicate_effect(self):
        self.bind()
        self.append(self.decision(self.put("one")))
        self.append(self.decision({"op": "record", "project_id": "demo", "followup_key": "one", "generation": 1,
                                   "expected_revision": 0, "outcome": "CLOSED", "evidence_ref": "native:handled"}))
        with patch.object(intake, "save_cursor", side_effect=OSError("simulated crash")), self.assertRaises(OSError):
            self.collect()
        before = self.core.manage("events", {"project_id": "demo"})["events"]
        self.assertEqual(self.rows(followup_key="one")["one"]["state"], "CLOSED")
        self.core = Coordinator(self.state)
        result = self.collect()
        self.assertEqual((result["accepted"], result["rejected"]), (2, 0))
        self.assertEqual(self.core.manage("events", {"project_id": "demo"})["events"], before)
        self.assertEqual(self.rows(followup_key="one")["one"]["revision"], 1)

    def test_unsupported_effects_and_bad_decisions_do_not_block_the_next_valid_one(self):
        self.bind()
        for row in [self.message(intake.MARKER + "{bad json"),
                    self.message(intake.MARKER + "[]")]:
            self.append(self.line(row))
        for unsupported in ({"op": "claim"}, {"op": "record", "outcome": "UNKNOWN"},
                            {"op": "record", "outcome": "SENT"}, {"op": "activate_owner"}):
            self.append(self.decision(unsupported))
        self.append(self.decision(self.put("keep")))
        result = self.collect()
        self.assertEqual((result["accepted"], result["rejected"]), (1, 6))
        self.assertEqual(set(self.rows()), {"keep"})
        self.assertEqual(self.rows()["keep"]["state"], "PENDING")

    def test_malformed_complete_row_is_rejected_once_without_stranding_a_business_decision(self):
        bound = self.bind()
        self.append(b'{"type":"response_item","note":"COORD_FOLLOWUP ",\n')
        self.append(self.decision(self.put("later")))
        result = self.collect()
        self.assertEqual((result["accepted"], result["rejected"]), (1, 1))
        self.assertEqual(set(self.rows()), {"later"})
        cursor = json.loads((self.state / intake.CURSOR).read_bytes())
        self.assertEqual(cursor["last_rejection"]["offset"], bound["initial_offset"])
        self.assertEqual(cursor["last_rejection"]["code"], "INVALID_TRANSCRIPT_ROW")
        self.assertEqual(result["offset"], self.source.stat().st_size)
        self.assertEqual(self.collect(), result)

    def test_replaced_or_truncated_source_never_reuses_the_old_cursor(self):
        self.append(self.decision(self.put("before-bind")))
        self.bind()
        old = (self.state / intake.CURSOR).read_bytes()
        self.source.write_bytes(self.line(self.meta))
        with self.assertRaises(CoordinatorError):
            self.collect()
        self.source.rename(self.sessions / "old")
        self.source.write_bytes(self.line(self.meta) + self.decision(self.put("late")))
        with self.assertRaises(CoordinatorError):
            self.collect()
        with self.assertRaises(CoordinatorError):
            self.bind()
        self.assertEqual((self.state / intake.CURSOR).read_bytes(), old)
        self.assertEqual(self.rows(), {})


if __name__ == "__main__":
    unittest.main()
