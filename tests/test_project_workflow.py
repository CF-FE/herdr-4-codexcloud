"""Exercise the generator and each project-owned command through fresh CLI processes."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


GENERATOR = Path(__file__).resolve().parents[1] / "scripts/project_workflow.py"


class ProjectWorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.make_repo("sample-project")
        self.state = self.root / "original.sqlite3"
        self.task = "issue-2-batch-a"
        self.input = self.root / "items.json"
        self.expected = hashlib.sha256(b"reviewed\n").hexdigest()
        self.observed = hashlib.sha256(b"changed\n").hexdigest()
        (self.repo / "assets").mkdir()
        (self.repo / "assets/good.txt").write_bytes(b"reviewed\n")
        (self.repo / "assets/changed.txt").write_bytes(b"changed\n")
        self.items = [{"id": key, "path": f"assets/{key}.txt", "sha256": self.expected}
                      for key in ("good", "missing", "changed")]
        self.input.write_text(json.dumps({"items": self.items}))

    def make_repo(self, project):
        path = self.root / project
        (path / "docs").mkdir(parents=True)
        (path / "docs/project-memory.md").write_text("# Project decisions\n")
        return path

    @staticmethod
    def spec(project="sample-project"):
        return {"version": 1, "project": project, "workflow": "batch-review", "memory": "docs/project-memory.md",
                "start": "inventory", "steps": {
                    "inventory": {"kind": "program", "operation": "verify_files",
                                  "routes": {"ready": "finish", "needs_review": "review"}},
                    "review": {"kind": "decision", "question": "Summarize or inspect?",
                               "choices": {"summarize": "finish", "check": "handoff"}},
                    "handoff": {"kind": "handoff", "instruction": "Inspect the flagged items.", "next": "finish"},
                    "finish": {"kind": "end"}}}

    def cli(self, cwd, script, *args, ok=True):
        # Ignore the developer's PYTHONPATH and site packages; allow ordinary sibling project imports.
        result = subprocess.run([sys.executable, "-E", "-S", "-B", str(script), *map(str, args)], cwd=cwd,
                                text=True, capture_output=True, timeout=10)
        if not ok:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertTrue(result.stderr.strip(), "CLI conflict needs a diagnostic")
            return
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertIsInstance(output, dict)
        return output

    def init(self, repo=None, spec=None, *, operations=None, ok=True):
        repo = self.repo if repo is None else repo
        spec = self.spec() if spec is None else spec
        source = self.root / "init-spec.json"
        source.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        extension = ["--operations", operations] if operations is not None else []
        self.cli(self.root, GENERATOR, "init", "--repo", repo, "--spec", source, *extension, ok=ok)
        return repo / "workflows" / spec["workflow"] / "workflow.py"

    def run_workflow(self, *args, repo=None, state=None, ok=True):
        repo = self.repo if repo is None else repo
        state = self.state if state is None else state
        return self.cli(repo, repo / "workflows/batch-review/workflow.py", "--state", state, *args, ok=ok)

    def start(self, **options):
        return self.run_workflow("start", "--task", self.task, "--input", self.input, **options)

    def decide(self, choice, evidence, **options):
        return self.run_workflow("decide", "--step", "review", "--choice", choice, "--evidence", evidence, **options)

    def receive(self, action, status, evidence, **options):
        return self.run_workflow("receive", "--action", action, "--status", status, "--evidence", evidence, **options)

    def test_program_routes_complete_batch_or_preserves_every_item_for_one_decision(self):
        self.init()
        waiting = self.start()
        self.assertEqual((waiting["task"], waiting["step"], waiting["status"]),
                         (self.task, "review", "WAITING_DECISION"))
        inventory = waiting["outputs"]["inventory"]
        self.assertEqual(inventory["outcome"], "needs_review")
        rows = {item["id"]: item for item in inventory["items"]}
        self.assertEqual(len(inventory["items"]), 3)
        self.assertEqual({k: (v["status"], v["actual_sha256"]) for k, v in rows.items()},
                         {"good": ("MATCH", self.expected), "missing": ("MISSING", None),
                          "changed": ("MISMATCH", self.observed)})
        self.assertEqual([rows[item["id"]]["path"] for item in self.items], [item["path"] for item in self.items])
        self.assertTrue(all(item["expected_sha256"] == self.expected for item in rows.values()))
        self.assertEqual(set(waiting["decision"]["choices"]), {"summarize", "check"})
        self.decide("not-a-choice", "owner-turn-1", ok=False)
        finished = self.decide("summarize", "owner-turn-2")
        self.assertEqual((finished["status"], finished["step"], finished["action"]), ("FINISHED", "finish", None))
        self.assertEqual(finished["outputs"], waiting["outputs"])
        self.assertEqual(finished["decisions"]["review"], {"choice": "summarize", "evidence": "owner-turn-2"})

        self.input.write_text(json.dumps({"items": self.items[:1]}))
        clean = self.start(state=self.root / "clean.sqlite3")
        self.assertEqual((clean["status"], clean["outputs"]["inventory"]["outcome"]), ("FINISHED", "ready"))
        self.assertFalse(clean["decisions"])
        self.assertIsNone(clean["action"])

    def test_handoff_survives_independent_takeover_and_late_duplicate_receipts(self):
        self.init()
        self.start()
        dispatched = self.decide("check", "owner-turn-27")
        self.assertEqual(dispatched["status"], "WAITING_RECEIPT")
        action = dispatched["action"]["id"]
        self.assertTrue(action)
        self.assertEqual(dispatched["action"]["instruction"], self.spec()["steps"]["handoff"]["instruction"])
        self.assertEqual(self.start()["action"]["id"], action)

        # A different checkout gets only the editable workflow and the project's memory.
        takeover = self.make_repo("takeover")
        target = takeover / "workflows/batch-review"
        target.mkdir(parents=True)
        for name in ("definition.py", "workflow.py"):
            shutil.copyfile(self.repo / "workflows/batch-review" / name, target / name)
        shutil.rmtree(self.repo)
        self.input.unlink()
        (self.root / "init-spec.json").unlink()
        self.assertEqual(self.run_workflow("status", repo=takeover)["action"]["id"], action)
        self.assertEqual(self.decide("check", "owner-turn-27", repo=takeover)["action"]["id"], action)
        self.decide("summarize", "owner-turn-90", repo=takeover, ok=False)
        self.receive("foreign-action", "COLLECTED", "irrelevant", repo=takeover, ok=False)
        unknown = self.receive(action, "UNKNOWN", "native-intent-31", repo=takeover)
        self.assertEqual((unknown["status"], unknown["action"]["id"]), ("UNKNOWN", action))
        resumed = self.run_workflow("resume", repo=takeover)
        self.assertEqual((resumed["status"], resumed["action"]["id"]), ("UNKNOWN", action))
        self.receive(action, "UNKNOWN", "native-intent-31", repo=takeover)
        final = self.receive(action, "COLLECTED", "original-manifest", repo=takeover)
        self.assertEqual(final["status"], "FINISHED")
        self.assertIsNone(final["action"])
        self.assertEqual(final["outputs"], dispatched["outputs"])
        self.assertEqual(final["decisions"], dispatched["decisions"])
        expected = [{"status": "UNKNOWN", "evidence": "native-intent-31"},
                    {"status": "COLLECTED", "evidence": "original-manifest"}]
        self.assertEqual(final["receipts"][action], expected)
        for status, ev in (("COLLECTED", "original-manifest"), ("UNKNOWN", "native-intent-31")):
            self.receive(action, status, ev, repo=takeover)
        self.receive(action, "COLLECTED", "conflicting-manifest", repo=takeover, ok=False)
        last = self.run_workflow("resume", repo=takeover)
        self.assertEqual((last["status"], last["receipts"][action]), ("FINISHED", expected))

    def test_existing_run_freezes_task_input_and_both_project_source_files(self):
        workflow = self.init()
        waiting = self.start()
        self.run_workflow("start", "--task", "another-task", "--input", self.input, ok=False)
        original_input = self.input.read_bytes()
        self.input.write_text(json.dumps({"items": self.items[:1]}))
        self.start(ok=False)
        self.input.write_bytes(original_input)
        source = workflow.read_bytes()
        workflow.write_bytes(source + b"\n# Project-local operation change.\n")
        self.run_workflow("resume", ok=False)
        workflow.write_bytes(source)

        definition = workflow.with_name("definition.py")
        original = definition.read_bytes()
        edited = original.replace(b"Summarize or inspect?", b"Use the amended plan?")
        self.assertNotEqual(edited, original)
        definition.write_bytes(edited)
        self.run_workflow("resume", ok=False)
        changed = self.start(state=self.root / "new-run.sqlite3")
        self.assertEqual(changed["decision"]["question"], "Use the amended plan?")
        definition.write_bytes(original)
        resumed = self.run_workflow("resume")
        self.assertEqual((resumed["status"], resumed["decision"], resumed["outputs"]),
                         (waiting["status"], waiting["decision"], waiting["outputs"]))

    def test_plain_next_and_two_projects_get_distinct_actions_for_the_same_task(self):
        second = self.make_repo("another-project")
        shutil.copytree(self.repo / "assets", second / "assets")
        actions = []
        for repo in (self.repo, second):
            spec = self.spec(repo.name)
            spec["steps"]["inventory"] = {"kind": "program", "operation": "verify_files", "next": "review"}
            self.init(repo, spec)
            state = self.root / (repo.name + ".sqlite3")
            self.start(repo=repo, state=state)
            actions.append(self.decide("check", "owner-turn", repo=repo, state=state)["action"]["id"])
        self.assertNotEqual(*actions)

    def test_expense_project_owns_its_custom_operation_and_result_routes(self):
        repo = self.make_repo("expense-project")
        spec = {"version": 1, "project": "expense-project", "workflow": "batch-review",
                "memory": "docs/project-memory.md", "start": "classify",
                "steps": {
                    "classify": {"kind": "program", "operation": "classify_expenses",
                                 "routes": {"within_limit": "finish", "over_limit": "review"}},
                    "review": {"kind": "decision", "question": "Approve the total?",
                               "choices": {"approved": "finish"}},
                    "finish": {"kind": "end"}}}
        operations = self.root / "expense_policy.py"
        operations.write_text(
            'def classify_expenses(project_root, run):\n'
            '    total = sum(run["input"]["amounts_in_cents"])\n'
            '    return {"total": total, "outcome": "over_limit" if total > 10000 else "within_limit"}\n'
            '\n'
            'OPERATIONS = {"classify_expenses": classify_expenses}\n',
            encoding="utf-8")
        generated = self.init(repo, spec, operations=operations)
        self.assertIn("def classify_expenses(", generated.with_name("definition.py").read_text(encoding="utf-8"))
        operations.unlink()
        (self.root / "init-spec.json").unlink()
        # Invoke from a copied project, after the original source, spec and extension are gone.
        independent = self.root / "independent-expenses"
        shutil.copytree(repo, independent)
        shutil.rmtree(repo)
        self.input.write_text(json.dumps({"amounts_in_cents": [2900, 7100]}))
        ready = self.start(repo=independent)
        self.assertEqual((ready["status"], ready["outputs"]["classify"]),
                         ("FINISHED", {"total": 10000, "outcome": "within_limit"}))
        other_state = self.root / "over-limit.sqlite3"
        self.input.write_text(json.dumps({"amounts_in_cents": [2900, 7101]}))
        pending = self.start(repo=independent, state=other_state)
        self.assertEqual((pending["status"], pending["step"], pending["outputs"]["classify"]),
                         ("WAITING_DECISION", "review", {"total": 10001, "outcome": "over_limit"}))
        approved = self.decide("approved", "owner-turn-expenses", repo=independent, state=other_state)
        self.assertEqual((approved["status"], approved["outputs"]), ("FINISHED", pending["outputs"]))

    def test_invalid_graph_and_memory_never_publish_and_overwrite_keeps_project_edits(self):
        for case in ("unknown-start", "missing-target", "conditional-cycle", "unreachable", "missing-memory",
                     "external-memory", "ambiguous-program"):
            with self.subTest(case=case):
                spec = self.spec()
                if case == "unknown-start":
                    spec["start"] = "absent"
                elif case == "missing-target":
                    spec["steps"]["review"]["choices"]["check"] = "absent"
                elif case == "conditional-cycle":
                    spec["steps"]["handoff"]["next"] = "review"
                elif case == "unreachable":
                    spec["steps"]["unused"] = {"kind": "end"}
                elif case == "missing-memory":
                    spec["memory"] = "docs/missing.md"
                elif case == "external-memory":
                    outside = self.root / "outside.md"
                    outside.write_text("private")
                    spec["memory"] = "../outside.md"
                else:
                    spec["steps"]["inventory"]["next"] = "finish"
                target = self.init(spec=spec, ok=False)
                self.assertFalse(target.parent.exists())
                self.assertFalse((self.repo / "workflows/README.md").exists())

        workflow = self.init()
        definition = workflow.with_name("definition.py")
        definition.write_text("# A project-owner edit to keep.\n")
        before = {p.name: p.read_bytes() for p in workflow.parent.iterdir() if p.is_file()}
        readme_before = (self.repo / "workflows/README.md").read_bytes()
        self.init(ok=False)
        self.assertEqual({p.name: p.read_bytes() for p in workflow.parent.iterdir() if p.is_file()}, before)
        self.assertEqual((self.repo / "workflows/README.md").read_bytes(), readme_before)

    def test_two_flows_share_a_readable_index_without_overwriting_project_notes(self):
        self.init()
        readme = self.repo / "workflows/README.md"
        first = readme.read_text(encoding="utf-8")
        self.assertIn("[batch-review](#batch-review)", first)
        self.assertIn("```mermaid\nflowchart TD", first)
        for text in ("program: verify_files", "decision: Summarize or inspect?",
                     "handoff: Inspect the flagged items.", "result: finish", '"needs_review"',
                     "../docs/project-memory.md", "python3 workflows/batch-review/workflow.py"):
            self.assertIn(text, first)
        first_section = first.split("<!-- workflow:batch-review:start -->", 1)[1]
        manual = "\n## Owner notes\nUse the original Issue and Project for current progress.\n"
        readme.write_text(first + manual, encoding="utf-8")
        spec = self.spec()
        spec["workflow"] = "delivery-check"
        self.init(spec=spec)
        updated = readme.read_text(encoding="utf-8")
        self.assertIn(manual, updated)
        self.assertIn(first_section, updated)
        self.assertIn("[delivery-check](#delivery-check)", updated)
        self.assertEqual(updated.count("## batch-review\n"), 1)
        self.assertEqual(updated.count("## delivery-check\n"), 1)
        self.assertEqual(updated.count("```mermaid"), 3)
        # An overview groups workflows; it must not invent an order between independent flows.
        overview = updated.split("<!-- workflow-index:end -->", 1)[0]
        self.assertNotIn('f0 --> f1', overview)

    def test_existing_readme_is_preserved_and_flow_labels_cannot_break_mermaid(self):
        parent = self.repo / "workflows"
        parent.mkdir()
        readme = parent / "README.md"
        notes = "# Existing project workflow notes\n\nKeep this exact handoff policy.\n"
        readme.write_text(notes, encoding="utf-8")
        spec = self.spec()
        spec["steps"]["review"]["question"] = 'Review "A"?\n```\nflowchart LR'
        self.init(spec=spec)
        result = readme.read_text(encoding="utf-8")
        self.assertTrue(result.startswith(notes))
        self.assertIn("#quot;A#quot;", result)
        self.assertEqual(result.count("```mermaid"), 2)
        self.assertNotIn('decision: Review "A"?\n```', result)

    def test_ambiguous_readme_markers_do_not_publish_a_workflow_or_replace_notes(self):
        parent = self.repo / "workflows"
        parent.mkdir()
        readme = parent / "README.md"
        original = "Owner notes\n<!-- workflow-index:start -->\n"
        readme.write_text(original, encoding="utf-8")
        target = self.init(ok=False)
        self.assertFalse(target.parent.exists())
        self.assertEqual(readme.read_text(encoding="utf-8"), original)

    def test_generated_readme_commands_run_from_the_project_root(self):
        self.init()
        self.input.write_text(json.dumps({"items": self.items[:1]}))
        readme = (self.repo / "workflows/README.md").read_text(encoding="utf-8")
        commands = readme.split("```bash\n", 1)[1].split("```", 1)[0]
        commands = commands.replace("python3 ", shlex.quote(sys.executable) + " ")
        commands = commands.replace("--task TASK", "--task " + self.task)
        commands = commands.replace("--input input.json", "--input " + shlex.quote(str(self.input)))
        commands = 'trap \'test -z "${RUN:-}" || rm -rf "$(dirname "$RUN")"\' EXIT\n' + commands
        result = subprocess.run(["bash", "-eu", "-c", commands], cwd=self.repo,
                                env={**os.environ, "TMPDIR": str(self.root)},
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        start, status = map(json.loads, result.stdout.splitlines())
        self.assertEqual(start["status"], "FINISHED")
        self.assertEqual(start["outputs"], status["outputs"])
        self.assertFalse((self.repo / "RUN.sqlite3").exists())

    def test_concurrent_flow_initialization_is_busy_then_preserves_both_entries(self):
        first_spec = self.root / "first-spec.json"
        first_spec.write_text(json.dumps(self.spec()), encoding="utf-8")
        child = (
            "import runpy,sys\nfrom pathlib import Path\n"
            "module=runpy.run_path(sys.argv[1])\n"
            "initialize=module['initialize']\n"
            "original=initialize.__globals__['workflow_readme']\n"
            "def paused(*args):\n"
            "    print('LOCKED',flush=True)\n"
            "    sys.stdin.readline()\n"
            "    return original(*args)\n"
            "initialize.__globals__['workflow_readme']=paused\n"
            "initialize(Path(sys.argv[2]),Path(sys.argv[3]))\n")
        process = subprocess.Popen([sys.executable, "-E", "-S", "-B", "-c", child,
                                    str(GENERATOR), str(self.repo), str(first_spec)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "LOCKED")
            spec = self.spec()
            spec["workflow"] = "delivery-check"
            source = self.root / "second-spec.json"
            source.write_text(json.dumps(spec), encoding="utf-8")
            second = subprocess.run([sys.executable, "-E", "-S", "-B", str(GENERATOR), "init",
                                     "--repo", str(self.repo), "--spec", str(source)],
                                    text=True, capture_output=True, timeout=10)
            self.assertNotEqual(second.returncode, 0, second.stdout)
            self.assertIn("busy", second.stderr)
        finally:
            _, error = process.communicate("continue\n", timeout=10)
            self.assertEqual(process.returncode, 0, error)
        self.init(spec=spec)
        readme = (self.repo / "workflows/README.md").read_text(encoding="utf-8")
        self.assertIn("[batch-review](#batch-review)", readme)
        self.assertIn("[delivery-check](#delivery-check)", readme)
        self.assertFalse((self.repo / "workflows/.init.lock").exists())

    def test_readme_publish_failure_is_recoverable_without_overwriting_workflow_source(self):
        spec = self.root / "recover-spec.json"
        spec.write_text(json.dumps(self.spec()), encoding="utf-8")
        initialize = runpy.run_path(str(GENERATOR))["initialize"]
        with mock.patch.object(Path, "replace", side_effect=OSError("simulated README failure")):
            with self.assertRaisesRegex(ValueError, "rerun the same init"):
                initialize(self.repo, spec)
        target = self.repo / "workflows/batch-review"
        original = {p.name: p.read_bytes() for p in target.iterdir()}
        self.assertEqual(set(original), {"definition.py", "workflow.py"})
        self.assertFalse((target.parent / "README.md").exists())
        self.init()
        self.assertEqual({p.name: p.read_bytes() for p in target.iterdir()}, original)
        self.assertIn("[batch-review](#batch-review)", (target.parent / "README.md").read_text(encoding="utf-8"))
        # Recovery is narrowly for missing documentation; completed init and project edits remain conflicts.
        self.init(ok=False)
        (target.parent / "README.md").unlink()
        (target / "definition.py").write_bytes(original["definition.py"] + b"\n# Owner edit\n")
        self.init(ok=False)
        self.assertFalse((target.parent / "README.md").exists())

    def test_readme_edit_during_init_is_preserved_then_recovered(self):
        parent = self.repo / "workflows"
        parent.mkdir()
        readme = parent / "README.md"
        readme.write_text("Original project notes\n", encoding="utf-8")
        initialize = runpy.run_path(str(GENERATOR))["initialize"]
        original_render = initialize.__globals__["workflow_readme"]
        edited = "Original project notes\nOwner added the new handoff receiver.\n"

        def edit_during_render(*args):
            rendered = original_render(*args)
            readme.write_text(edited, encoding="utf-8")
            return rendered

        spec = self.root / "edit-spec.json"
        spec.write_text(json.dumps(self.spec()), encoding="utf-8")
        with mock.patch.dict(initialize.__globals__, workflow_readme=edit_during_render):
            with self.assertRaisesRegex(ValueError, "README changed.*rerun the same init"):
                initialize(self.repo, spec)
        self.assertEqual(readme.read_text(encoding="utf-8"), edited)
        self.init()
        result = readme.read_text(encoding="utf-8")
        self.assertTrue(result.startswith(edited))
        self.assertIn("[batch-review](#batch-review)", result)


if __name__ == "__main__":
    unittest.main()
