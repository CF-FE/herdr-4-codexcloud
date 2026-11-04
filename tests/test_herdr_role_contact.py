import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import herdr_role_contact as contact


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
        self.db = self.root / "state/project-coordinator.sqlite3"
        self.db.parent.mkdir()
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE projects(project_id,owner_principal,endpoint,generation,assignment_state)")
            db.execute("INSERT INTO projects VALUES('test','owner:test',?,1,'BOUND')", ("codex-cli:mac:" + self.thread,))
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
        if cmd == ("pane", "process-info", self.proc["pane_id"]):
            return {"process_info": self.proc}
        self.fail(f"Unexpected Herdr operation: {cmd}")

    def run_op(self, action, replacement=None):
        with patch.object(contact, "CODEX", self.binary), patch.object(Path, "home", return_value=self.root):
            return contact.operate(action, "test", replacement=replacement, root=self.root, query=self.query)

    def test_show_offline_and_generation_fence(self):
        self.assertEqual(self.run_op("show")["state"], "REGISTERED_ROUTE_ONLY")
        self.assertEqual(self.calls, [])
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE projects SET generation=2")
        with self.assertRaisesRegex(ValueError, "STALE_APPOINTMENT"):
            self.run_op("resolve")
        self.assertEqual(self.calls, [])

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


if __name__ == "__main__":
    unittest.main()
