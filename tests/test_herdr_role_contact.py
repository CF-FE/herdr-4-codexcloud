import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import herdr_role_contact as contact
from scripts.project_coordinator import Coordinator


class RoleContactTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        roles = self.root / "handoffs/herdr-project-coordinator"
        (roles / "owners").mkdir(parents=True, mode=0o700)
        self.route_file = roles / "owners/test.json"
        self.thread = "11111111-2222-4333-8444-555555555555"
        self.ref = {"kind": "id", "agent": "codex", "value": "coordinator-thread", "source": "herdr:codex"}
        coordinator = {"role": "project_coordinator", "session": "test-session", "workspace": "w1",
                       "pane": "w1:p1", "tab": "w1:t1", "agent": "coordinator", "agent_session": self.ref}
        self.loc = {"workspace_id": "w1", "tab_id": "w1:t1", "pane_id": "w1:p2", "terminal_id": "term_a"}
        self.cwd = str(self.root)
        self.args = ["--no-daemon", "-a", "never", "-s", "read-only", "-C", self.cwd, "resume", self.thread]
        digest = contact.hashlib.sha256(b"test").hexdigest()
        route = {"version": 1, "role": "project_owner", "project_id": "test", "generation": 1,
                 "thread_id": self.thread, "owner_principal": "owner:test", "agent": "owner",
                 "session": "test-session", "cwd": self.cwd, "locator": self.loc, "resume_argv": self.args,
                 "codex_sha256": digest, "config_sha256": digest,
                 "last_confirmed_contact": {"turn_id": "old-confirmed-turn"}}
        for p, data in ((roles / "binding.json", coordinator), (self.route_file, route)):
            p.write_text(json.dumps(data))
            p.chmod(0o600)
        self.core = Coordinator(self.root / "state")
        self.db = self.core.path
        self.core.manage("register_project", {"project_id": "test", "repository": "CF-FE/test", "project_ref": "p",
                         "cloud_dot_principal": "dot", "report_route": "local"})
        self.core.manage("activate_owner", {"project_id": "test", "expected_generation": 0,
                         "owner_principal": route["owner_principal"], "endpoint": "codex-cli:mac:" + self.thread,
                         "appointment_ref": "appoint", "verification_ref": "verify",
                         "handoff_ref": "handoff", "release_ref": "release"})
        self.owner = dict(self.loc, agent="codex", name="owner", cwd=self.cwd, foreground_cwd=self.cwd,
                          agent_status="idle", interactive_ready=True)
        self.panes = [dict(self.owner)]
        self.agents = [self.owner]
        self.proc = {"pane_id": self.loc["pane_id"], "shell_pid": 12, "foreground_process_group_id": 13,
                     "foreground_processes": [{"pid": 13, "argv": ["codex", *self.args], "cwd": self.cwd}]}
        self.calls = []
        env = patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_SESSION": "test-session", "HERDR_PANE_ID": "w1:p1"})
        env.start()
        self.addCleanup(env.stop)
        (self.root / ".codex").mkdir()
        (self.root / ".codex/config.toml").write_bytes(b"test")
        self.binary = self.root / "codex"
        self.binary.write_bytes(b"test")

    def query(self, session, *cmd):
        self.assertEqual(session, "test-session")
        self.calls.append(cmd)
        if cmd == ("agent", "get", "coordinator"):
            return {"agent": dict(name="coordinator", pane_id="w1:p1", tab_id="w1:t1", workspace_id="w1",
                                  agent="codex", agent_session=self.ref)}
        if cmd == ("agent", "get", "owner"):
            return {"agent": self.owner}
        if cmd == ("pane", "list", "--workspace", "w1"):
            return {"panes": self.panes}
        if cmd == ("agent", "list"):
            return {"agents": self.agents}
        if cmd == ("pane", "process-info", "--pane", self.proc["pane_id"]):
            return {"process_info": self.proc}
        self.fail(f"Unexpected Herdr operation: {cmd}")

    def run_op(self, action, replacement=None):
        with patch.object(contact, "CODEX", self.binary), patch.object(Path, "home", return_value=self.root):
            return contact.operate(action, "test", replacement=replacement, root=self.root, query=self.query)

    def test_show_offline_and_generation_fence(self):
        self.assertEqual(self.run_op("show")["state"], "REGISTERED_ROUTE_ONLY")
        self.assertEqual(self.run_op("show")["runtime_profile"], "readonly-contact-v1")
        self.assertEqual(self.calls, [])
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE projects SET generation=2")
        with self.assertRaisesRegex(ValueError, "STALE_APPOINTMENT"):
            self.run_op("resolve")
        self.assertEqual(self.calls, [])

    def test_recovered_native_identity_sees_original_unclosed_work_without_replaying(self):
        key = {"project_id": "test", "generation": 1, "followup_key": "check-original"}
        item = self.core.manage("role_followup_put", dict(key, task_ref="issue:1", receipt_ref="native:initial",
                                next_action="read prior receipt", action_owner="coordinator",
                                next_check_at="2026-11-05T15:00:00Z"))
        self.assertEqual(self.run_op("show")["role_followups"]["items"], [item])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.run_op("resolve")["role_followups"]["items"], [item])
        self.assertIn(("pane", "process-info", "--pane", self.loc["pane_id"]), self.calls)
        self.core.manage("role_followup_record", dict(key, expected_revision=0, outcome="CLOSED",
                         evidence_ref="native:handling-turn"))
        self.assertFalse(self.run_op("resolve")["role_followups"]["items"])

    def test_actor_mismatch_denied_before_native_calls(self):
        with patch.dict(os.environ, {"HERDR_PANE_ID": self.loc["pane_id"]}):
            with self.assertRaisesRegex(ValueError, "NATIVE_COORDINATOR_REQUIRED"):
                self.run_op("resolve")
        self.assertEqual(self.calls, [])

    def test_bound_owner_and_no_hook_fallback(self):
        self.assertEqual(self.run_op("resolve")["identity_evidence"], "HERDR_TERMINAL_AND_RESUME_ARGV")
        self.owner["agent_session"] = {"agent": "codex", "kind": "id", "value": self.thread}
        self.assertEqual(self.run_op("resolve")["identity_evidence"], "HERDR_AGENT_SESSION")
        self.owner["agent_status"] = "working"
        self.assertEqual(self.run_op("resolve")["state"], "OWNER_NOT_READY")
        self.owner["agent_session"]["value"] = "wrong"
        with self.assertRaisesRegex(ValueError, "OWNER_THREAD_MISMATCH"):
            self.run_op("resolve")
        self.owner.pop("agent_session")
        self.proc["foreground_processes"][0]["argv"].insert(1, "--different-policy")
        with self.assertRaisesRegex(ValueError, "OWNER_RESUME_ARGV_UNCONFIRMED"):
            self.run_op("resolve")

    def shell(self, pane):
        self.proc.update(pane_id=pane, foreground_process_group_id=12,
                         foreground_processes=[{"pid": 12, "argv": ["-zsh"], "cwd": self.cwd}])
        self.agents = []

    def test_resume_plan_is_same_thread_same_permissions_and_never_starts(self):
        with self.assertRaisesRegex(ValueError, "EXISTING_AGENT"):
            self.run_op("prepare-resume")
        self.shell(self.loc["pane_id"])
        plan = self.run_op("prepare-resume")
        self.assertEqual(plan["argv"][-len(self.args):], self.args)
        self.assertEqual(plan["state"], "RESUME_PREPARED_NOT_STARTED")
        self.assertEqual(contact.shlex.split(plan["command"]), plan["argv"])
        self.assertNotIn("start", {word for cmd in self.calls for word in cmd})
        self.binary.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "REVIEWED_RUNTIME_CHANGED"):
            self.run_op("prepare-resume")

    def test_relocated_pane_requires_old_absence_new_identity_and_preserves_binding(self):
        new = "w1:p3"
        old = json.loads(self.route_file.read_bytes())
        self.panes.append(dict(self.panes[0], pane_id=new, terminal_id="term_b"))
        with self.assertRaisesRegex(ValueError, "PRIOR_PANE_NOT_RELEASED"):
            self.run_op("prepare-resume", new)
        self.panes.pop(0)
        self.shell(new)
        self.assertEqual(self.run_op("prepare-resume", new)["locator"]["terminal_id"], "term_b")
        with self.assertRaisesRegex(ValueError, "OWNER_TARGET_MISMATCH"):
            self.run_op("bind-replacement", new)
        self.owner.update(pane_id=new, terminal_id="term_b", agent_session={
            "kind": "id", "agent": "codex", "value": self.thread})
        with self.assertRaisesRegex(ValueError, "OWNER_RESUME_ARGV_UNCONFIRMED"):
            self.run_op("resolve")
        self.proc.update(foreground_process_group_id=13,
                         foreground_processes=[{"pid": 13, "argv": ["codex", *self.args], "cwd": self.cwd}])
        resolved = self.run_op("resolve")
        self.assertEqual((resolved["state"], resolved["locator"]["pane_id"], resolved["locator_updated"]),
                         ("READY_FOR_CONTACT", new, False))
        self.assertEqual(json.loads(self.route_file.read_bytes()), old)
        self.assertTrue(self.run_op("bind-replacement", new)["locator_updated"])
        now = json.loads(self.route_file.read_bytes())
        self.assertEqual({**now, "locator": old["locator"]}, old)
        self.assertEqual(now["locator"]["pane_id"], new)
        self.assertEqual(self.run_op("resolve")["state"], "READY_FOR_CONTACT")

    def owner_runtime(self, extra=()):
        route = json.loads(self.route_file.read_bytes())
        route["runtime_profile"] = "project-owner-v1"
        route["authorization_ref"] = "user:repair-owner-capability-gap"
        route["resume_argv"] = ["--no-daemon", "-a", "on-request", "-s", "workspace-write", "-C", self.cwd,
                                "-c", 'shell_environment_policy.inherit="all"',
                                "--disable", "multi_agent", *extra, "resume", self.thread]
        route["resume_argv_sha256"] = contact.hashlib.sha256(
            json.dumps(route["resume_argv"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.route_file.write_text(json.dumps(route))
        return route

    def test_project_owner_resume_preserves_original_identity_and_pending_work(self):
        key = {"project_id": "test", "generation": 1, "followup_key": "pending-original"}
        item = self.core.manage("role_followup_put", dict(key, task_ref="issue:1", receipt_ref="native:prior",
                                next_action="collect existing worker", action_owner="coordinator",
                                next_check_at="2026-11-05T15:00:00Z"))
        route = self.owner_runtime()
        self.shell(self.loc["pane_id"])
        plan = self.run_op("prepare-resume")
        self.assertEqual(plan["runtime_profile"], "project-owner-v1")
        self.assertEqual(plan["argv"][-len(route["resume_argv"]):], route["resume_argv"])
        self.assertEqual((plan["thread_id"], plan["generation"], plan["owner_principal"]),
                         (self.thread, 1, "owner:test"))
        self.assertEqual(plan["role_followups"]["items"], [item])
        self.assertEqual(json.loads(self.route_file.read_bytes()), route)
        self.assertNotIn("start", {word for cmd in self.calls for word in cmd})

    def test_project_owner_identity_does_not_hide_a_running_legacy_process(self):
        route = self.owner_runtime()
        self.owner["agent_session"] = {"agent": "codex", "kind": "id", "value": self.thread}
        with self.assertRaisesRegex(ValueError, "OWNER_RESUME_ARGV_UNCONFIRMED"):
            self.run_op("resolve")
        self.proc["foreground_processes"][0]["argv"] = ["codex", *route["resume_argv"]]
        resolved = self.run_op("resolve")
        self.assertEqual(resolved["state"], "READY_FOR_CONTACT")
        self.assertEqual(resolved["identity_evidence"], "HERDR_AGENT_SESSION_AND_RESUME_ARGV")

    def test_project_owner_requires_authorization_and_exact_argv_digest(self):
        for field, value, reason in (("authorization_ref", " ", "OWNER_RUNTIME_AUTHORIZATION_REQUIRED"),
                                     ("resume_argv_sha256", "0" * 64, "OWNER_RUNTIME_ARGV_HASH_MISMATCH"),
                                     ("runtime_profile", "unknown", "RESUME_POLICY_MISMATCH")):
            with self.subTest(field=field):
                route = self.owner_runtime()
                route[field] = value
                self.route_file.write_text(json.dumps(route))
                with self.assertRaisesRegex(ValueError, reason):
                    self.run_op("show")
                self.assertEqual(self.calls, [])

    def test_project_owner_rejects_policy_overrides_even_with_matching_digest(self):
        for extra in (("-a", "never"), ("-s", "danger-full-access"), ("-sread-only",),
                      ("--ask-for-approval=never",), ("--sandbox", "read-only"),
                      ("--profile", "other"), ("-pother",), ("--cd=/tmp",),
                      ("--dangerously-bypass-approvals-and-sandbox",), ("--yolo",),
                      ("--full-auto",), ("resume",), ("exec",), ("--",)):
            with self.subTest(extra=extra):
                self.owner_runtime(extra)
                with self.assertRaisesRegex(ValueError, "OWNER_RUNTIME_POLICY_OVERRIDE"):
                    self.run_op("show")
                self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
