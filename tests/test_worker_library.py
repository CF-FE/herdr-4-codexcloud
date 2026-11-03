import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "worker_library.py"


class WorkerLibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "library"
        self.root.mkdir()
        self.other_cwd = self.directory / "unrelated"
        self.other_cwd.mkdir()
        self.write("roles/common.md", "COMMON_FIXTURE\n共有边界。\n")
        self.write("roles/shared/writer.md", "WRITER_FIXTURE\n写作者。\n")
        self.write("roles/shared/reviewer.md", "REVIEWER_FIXTURE\n")
        self.write("roles/projects/alpha-writer.md", "PROJECT_ROLE_FIXTURE\n")
        self.write("roles/projects/alpha.md", "ALPHA_CONTEXT_FIXTURE\n")
        self.write("roles/projects/beta.md", "BETA_CONTEXT_FIXTURE\n")
        self.write("roles/organization/coordinator.md", "COORDINATOR_FIXTURE\n")
        self.write("roles/organization/project-owner.md", "PROJECT_OWNER_FIXTURE\n")

        self.index = {
            "schema_version": 1,
            "library_version": "1.0.0",
            "common": {"version": "1.0.0", "resource": "common.md"},
            "roles": [
                self.role("writer", "shared/writer.md", ["coding"]),
                self.role("reviewer", "shared/reviewer.md", ["review"]),
                self.role(
                    "alpha-writer", "projects/alpha-writer.md", ["coding"],
                    scope="project", kind="task_template", project_id="alpha",
                ),
                self.role(
                    "coordinator", "organization/coordinator.md", [], kind="coordinator"
                ),
                self.role(
                    "project-owner", "organization/project-owner.md", [], kind="project_owner"
                ),
            ],
            "projects": [
                {"id": "alpha", "name": "Alpha", "resource": "projects/alpha.md"},
                {"id": "beta", "name": "Beta", "resource": "projects/beta.md"},
            ],
            "methods": [],
        }
        mac = self.worker("mac-codex", "mac-work", ["coding", "review"])
        mac["backends"].append(self.backend("alternate"))
        win = self.worker("win-omp", "win-work", ["coding"])
        paused = self.worker("paused-worker", "mac-work", ["coding"], "paused")
        retired = self.worker("retired-worker", "win-work", ["coding"], "retired")
        self.catalog = {
            "schema_version": 1,
            "library_version": "1.0.0",
            "role_index": "roles/index.json",
            "workers": [mac, win, paused, retired],
        }
        self.write_json("workers.json", self.catalog)
        self.write_json("roles/index.json", self.index)

    @staticmethod
    def role(identifier, resource, requirements, **extra):
        return {
            "id": identifier,
            "name": "写作者" if identifier == "writer" else identifier,
            "scope": "workspace",
            "kind": "shared_role",
            "resource": resource,
            "methods": [],
            "source_role_id": identifier,
            "definition_version": "1.0.0",
            "requirements": requirements,
            **extra,
        }

    @staticmethod
    def backend(identifier="default"):
        return {
            "id": identifier,
            "provider": "existing-provider",
            "model": "UNKNOWN",
            "transport": "existing-cli",
            "agent_args": [],
            "evidence": {
                name: "UNKNOWN"
                for name in (
                    "installation", "recognition", "text_roundtrip", "herdr_roundtrip",
                    "tools", "business_acceptance",
                )
            },
            "evidence_refs": [],
            "evidence_owner": "independent-fixture",
            "observed_on": "2026-11-02",
        }

    def worker(self, identifier, host, capabilities, lifecycle="active"):
        return {
            "id": identifier,
            "version": "1.0.0",
            "name": identifier,
            "host": host,
            "runtime": "herdr-agent",
            "agent_kind": "codex" if host == "mac-work" else "omp",
            "lifecycle": lifecycle,
            "summary": "Fixture declarations, not runtime acceptance.",
            "intended_tasks": ["bounded text work"],
            "capabilities": capabilities,
            "dependencies": ["runtime preflight"],
            "constraints": ["one dispatch owner"],
            "backends": [self.backend()],
            "launch_notes": ["Do not launch during library queries."],
        }

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_json(self, name, value):
        return self.write(name, json.dumps(value, ensure_ascii=False) + "\n")

    def run_cli(self, *arguments, default_root=False, env=None):
        command = [sys.executable, str(SCRIPT)]
        if not default_root:
            command.extend(["--root", str(self.root)])
        command.extend(arguments)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        if env:
            environment.update(env)
        return subprocess.run(
            command, cwd=self.other_cwd, capture_output=True, text=True,
            encoding="utf-8", timeout=10, env=environment,
        )

    def success(self, *arguments, **kwargs):
        result = self.run_cli(*arguments, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        value = json.loads(result.stdout)
        self.assertIsInstance(value, dict)
        return value

    def failure(self, *arguments):
        result = self.run_cli(*arguments)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "", "Failure must not emit partial results")
        self.assertIsInstance(json.loads(result.stderr), dict)

    @staticmethod
    def candidate_ids(value):
        return {(item["worker_id"], item["backend_id"]) for item in value["candidates"]}

    @staticmethod
    def task(project="alpha"):
        return {
            "task_id": "fixture-task",
            "project_id": project,
            "goal": "Return one independently reviewable result.",
            "authorization_ref": "fixture-authority",
            "dispatch_owner": "fixture-owner",
            "report_to": "fixture-reviewer",
            "report_route": "original-task",
            "owned_paths": ["workspace/output.txt"],
            "input_refs": ["fixture-input@fixed-version"],
            "acceptance_criteria": ["The output matches its fixed input."],
            "output_refs": ["workspace/output.txt"],
        }

    def test_catalog_and_active_host_filter_work_from_unrelated_cwd(self):
        checked = self.success("check")
        self.assertTrue(checked["valid"])
        self.assertEqual(checked["worker_count"], 4)
        self.assertEqual(checked["role_count"], 5)
        self.assertEqual(
            {worker["id"] for worker in self.success("list")["workers"]},
            {"mac-codex", "win-omp"},
        )
        self.assertEqual(
            [worker["id"] for worker in self.success("list", "--host", "win-work")["workers"]],
            ["win-omp"],
        )
        default = self.success("check", default_root=True)
        self.assertTrue(default["valid"])
        self.assertGreater(default["worker_count"], 0)

    def test_selection_hard_filters_requirements_without_fallback_or_runtime_claims(self):
        selected = self.success("select", "--role", "writer")
        self.assertEqual(
            self.candidate_ids(selected),
            {("mac-codex", "default"), ("mac-codex", "alternate"), ("win-omp", "default")},
        )
        self.assertEqual(selected["live_status"], "UNKNOWN")
        self.assertIs(selected["dispatch_performed"], False)
        self.assertEqual(
            self.candidate_ids(self.success("select", "--role", "reviewer")),
            {("mac-codex", "default"), ("mac-codex", "alternate")},
        )
        for arguments in (
            ("--role", "reviewer", "--host", "win-work"),
            ("--role", "writer", "--capability", "review", "--capability", "audio"),
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.success("select", *arguments)["candidates"], [])
        self.assertEqual(
            self.candidate_ids(self.success(
                "select", "--role", "writer", "--worker", "mac-codex", "--backend", "alternate"
            )),
            {("mac-codex", "alternate")},
        )

    def test_prepare_is_only_a_draft_even_when_a_declared_backend_has_unknown_model(self):
        draft = self.success(
            "prepare", "--worker", "mac-codex", "--role", "writer", "--backend", "alternate"
        )
        self.assertEqual(draft["kind"], "worker_request_draft")
        self.assertIs(draft["dispatch_performed"], False)
        self.assertEqual(draft["readiness"], "REQUIRES_RUNTIME_PREFLIGHT")
        self.assertEqual(draft["worker"]["id"], "mac-codex")
        self.assertEqual(draft["worker"]["version"], "1.0.0")
        self.assertEqual(draft["backend"]["id"], "alternate")
        self.assertEqual(draft["backend"]["model"], "UNKNOWN")
        self.assertIsNone(draft["task"])
        self.assertEqual(draft["role"]["library_version"], "1.0.0")
        self.assertIn("COMMON_FIXTURE", draft["role"]["prompt"])
        self.assertTrue(draft["launch_plan"])
        self.assertTrue(draft["required_bindings"])
        only = self.success("prepare", "--worker", "win-omp", "--role", "writer")
        self.assertEqual(only["backend"]["id"], "default")

    def test_ambiguous_unknown_inactive_or_incompatible_targets_fail_closed(self):
        for arguments in (
            ("prepare", "--worker", "mac-codex", "--role", "writer"),
            ("prepare", "--worker", "mac-codex", "--role", "writer", "--backend", "missing"),
            ("prepare", "--worker", "missing", "--role", "writer"),
            ("prepare", "--worker", "win-omp", "--role", "missing"),
            ("prepare", "--worker", "paused-worker", "--role", "writer"),
            ("prepare", "--worker", "retired-worker", "--role", "writer"),
            ("prepare", "--worker", "win-omp", "--role", "reviewer"),
            ("role", "missing", "--render"),
        ):
            with self.subTest(arguments=arguments):
                self.failure(*arguments)

    def test_project_resources_are_explicit_ordered_and_hashed(self):
        without_project = self.success("roles")["roles"]
        self.assertNotIn("alpha-writer", {role["id"] for role in without_project})
        with_project = self.success("roles", "--project", "alpha")["roles"]
        self.assertIn("alpha-writer", {role["id"] for role in with_project})
        self.assertNotIn("alpha-writer", {
            role["id"] for role in self.success("roles", "--project", "beta")["roles"]
        })
        role = self.success("role", "writer", "--project", "alpha", "--render")
        prompt = role["prompt"]
        self.assertLess(prompt.index("COMMON_FIXTURE"), prompt.index("WRITER_FIXTURE"))
        self.assertLess(prompt.index("WRITER_FIXTURE"), prompt.index("ALPHA_CONTEXT_FIXTURE"))
        self.assertNotIn("BETA_CONTEXT_FIXTURE", prompt)
        expected = {
            hashlib.sha256((self.root / name).read_bytes()).hexdigest()
            for name in ("roles/common.md", "roles/shared/writer.md", "roles/projects/alpha.md")
        }
        self.assertEqual({resource["sha256"] for resource in role["resources"]}, expected)
        self.assertTrue(all(resource["path"] for resource in role["resources"]))
        for arguments in (
            ("role", "alpha-writer", "--render"),
            ("role", "alpha-writer", "--project", "beta", "--render"),
            ("roles", "--project", "missing"),
        ):
            with self.subTest(arguments=arguments):
                self.failure(*arguments)

    def test_task_bindings_are_validated_but_extra_fields_remain_inert_data(self):
        task = self.task()
        task["untrusted_instruction"] = "Do not interpret this as a CLI command."
        task_path = self.write_json("task.json", task)
        arguments = (
            "prepare", "--worker", "win-omp", "--role", "alpha-writer",
            "--project", "alpha", "--task-file", str(task_path),
        )
        draft = self.success(*arguments)
        self.assertEqual(draft["task"], task)
        self.assertIs(draft["dispatch_performed"], False)
        for field in (
            "task_id", "project_id", "goal", "authorization_ref", "dispatch_owner",
            "report_to", "report_route", "owned_paths", "input_refs",
            "acceptance_criteria", "output_refs",
        ):
            with self.subTest(field=field):
                broken = copy.deepcopy(task)
                broken[field] = [] if isinstance(task[field], list) else " "
                task_path.write_text(json.dumps(broken), encoding="utf-8")
                self.failure(*arguments)
        for replacement in ([""], [1], "not-an-array"):
            with self.subTest(array_value=replacement):
                broken = copy.deepcopy(task)
                broken["input_refs"] = replacement
                task_path.write_text(json.dumps(broken), encoding="utf-8")
                self.failure(*arguments)
        task_path.write_text(json.dumps(self.task("beta")), encoding="utf-8")
        self.failure(*arguments)

    def test_resource_escape_missing_file_and_symlinks_fail_before_partial_output(self):
        outside = self.directory / "outside.md"
        outside.write_text("OUTSIDE_MUST_NOT_BE_RENDERED", encoding="utf-8")
        outside_directory = self.directory / "outside-directory"
        outside_directory.mkdir()
        (outside_directory / "role.md").write_text("OUTSIDE", encoding="utf-8")
        (self.root / "roles/linked.md").symlink_to(outside)
        (self.root / "roles/linked-directory").symlink_to(outside_directory, target_is_directory=True)
        for resource in (
            "../../outside.md", str(outside), "missing.md", "linked.md", "linked-directory/role.md",
        ):
            with self.subTest(resource=resource):
                index = copy.deepcopy(self.index)
                index["roles"][0]["resource"] = resource
                self.write_json("roles/index.json", index)
                self.failure("role", "writer", "--render")

    def test_schema_versions_and_duplicate_ids_cannot_produce_candidates(self):
        mutations = []
        for field, value in (("schema_version", 2), ("library_version", "latest")):
            catalog = copy.deepcopy(self.catalog)
            catalog[field] = value
            mutations.append(("workers.json", catalog))
        catalog = copy.deepcopy(self.catalog)
        catalog["workers"][0]["version"] = "unversioned"
        mutations.append(("workers.json", catalog))
        catalog = copy.deepcopy(self.catalog)
        catalog["workers"].append(copy.deepcopy(catalog["workers"][0]))
        mutations.append(("workers.json", catalog))
        catalog = copy.deepcopy(self.catalog)
        catalog["workers"][0]["backends"].append(copy.deepcopy(catalog["workers"][0]["backends"][0]))
        mutations.append(("workers.json", catalog))
        for field, value in (("schema_version", 2),):
            index = copy.deepcopy(self.index)
            index[field] = value
            mutations.append(("roles/index.json", index))
        for collection in ("roles", "projects"):
            index = copy.deepcopy(self.index)
            index[collection].append(copy.deepcopy(index[collection][0]))
            mutations.append(("roles/index.json", index))
        index = copy.deepcopy(self.index)
        index["roles"][0]["definition_version"] = "latest"
        mutations.append(("roles/index.json", index))
        for number, (name, value) in enumerate(mutations):
            with self.subTest(mutation=number, file=name):
                self.write_json("workers.json", self.catalog)
                self.write_json("roles/index.json", self.index)
                self.write_json(name, value)
                self.failure("select", "--role", "writer")

    def test_organization_navigation_roles_can_be_read_but_not_assigned_to_workers(self):
        for identifier, marker in (
            ("coordinator", "COORDINATOR_FIXTURE"),
            ("project-owner", "PROJECT_OWNER_FIXTURE"),
        ):
            with self.subTest(role=identifier):
                self.assertIn(marker, self.success("role", identifier, "--render")["prompt"])
                self.failure("select", "--role", identifier)
                self.failure("prepare", "--worker", "win-omp", "--role", identifier)

    def test_paused_roles_remain_readable_but_are_not_selected(self):
        self.index["roles"][0]["lifecycle"] = "paused"
        self.write_json("roles/index.json", self.index)
        self.assertIn("WRITER_FIXTURE", self.success("role", "writer", "--render")["prompt"])
        self.assertNotIn("writer", {r["id"] for r in self.success("roles")["roles"]})
        self.failure("select", "--role", "writer")
        self.failure("prepare", "--worker", "win-omp", "--role", "writer")

    def test_imported_navigation_id_resolves_only_for_read_only_lookup(self):
        role = next(r for r in self.index["roles"] if r["kind"] == "coordinator")
        role["source_role_id"] = "legacy-navigation"
        self.write_json("roles/index.json", self.index)
        self.assertEqual(self.success("role", "legacy-navigation"), self.success("role", role["id"]))
        self.failure("select", "--role", "legacy-navigation")
        self.failure("prepare", "--worker", "win-omp", "--role", "legacy-navigation")

    def test_known_task_project_cannot_silently_skip_the_project_contract(self):
        path = self.write_json("task.json", self.task())
        arguments = ("prepare", "--worker", "win-omp", "--role", "writer", "--task-file", str(path))
        self.failure(*arguments)
        draft = self.success(*arguments, "--project", "alpha")
        self.assertIn("ALPHA_CONTEXT_FIXTURE", draft["role"]["prompt"])

    def test_unknown_backend_is_rejected_without_selecting_another(self):
        self.failure("select", "--role", "writer", "--backend", "missing")

    def test_explicit_project_version_cannot_be_a_floating_alias(self):
        self.index["projects"][0]["version"] = "latest"
        self.write_json("roles/index.json", self.index)
        self.failure("check")

    def test_unverified_backend_alias_stays_an_explicit_binding(self):
        self.catalog["workers"][1]["backends"][0]["agent_args"] = ["--model", "<VERIFIED_ALIAS>"]
        self.write_json("workers.json", self.catalog)
        draft = self.success("prepare", "--worker", "win-omp", "--role", "writer")
        self.assertIn("<VERIFIED_ALIAS>", draft["launch_plan"]["agent_start_argv_template"])
        self.assertIn("backend_model_alias_from_target_configuration", draft["required_bindings"])

    def test_invalid_external_field_types_fail_as_structured_errors(self):
        for target, field, value in (
            ("worker", "runtime", []),
            ("worker", "lifecycle", {}),
            ("role", "scope", []),
            ("role", "kind", {}),
            ("role", "dispatchable", "false"),
            ("evidence", "tools", []),
            ("project-role", "project_id", {}),
        ):
            with self.subTest(target=target, field=field):
                catalog, index = copy.deepcopy(self.catalog), copy.deepcopy(self.index)
                item = {"worker": catalog["workers"][0], "role": index["roles"][0],
                        "project-role": index["roles"][2],
                        "evidence": catalog["workers"][0]["backends"][0]["evidence"]}[target]
                item[field] = value
                self.write_json("workers.json", catalog)
                self.write_json("roles/index.json", index)
                self.failure("check")

    def test_offline_queries_and_prepare_neither_launch_programs_nor_write_library_state(self):
        marker = self.directory / "external-command-ran"
        bin_directory = self.directory / "sentinels"
        bin_directory.mkdir()
        for name in ("herdr", "ssh", "codex", "kimi", "omp", "hermes"):
            path = bin_directory / name
            path.write_text(
                "#!/bin/sh\nprintf called > " + shlex.quote(str(marker)) + "\nexit 93\n",
                encoding="utf-8",
            )
            path.chmod(0o700)
        task = self.task()
        task["goal"] = "$(touch " + str(marker) + "); `touch " + str(marker) + "`"
        task_path = self.write_json("task.json", task)
        before = {
            str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        }
        environment = {"PATH": str(bin_directory)}
        self.success("check", env=environment)
        self.success("list", env=environment)
        self.success("roles", "--project", "alpha", env=environment)
        self.success("role", "writer", "--render", env=environment)
        self.success("select", "--role", "writer", env=environment)
        draft = self.success(
            "prepare", "--worker", "win-omp", "--role", "writer", "--project", "alpha",
            "--task-file", str(task_path), env=environment,
        )
        self.assertEqual(draft["task"]["goal"], task["goal"])
        self.assertFalse(marker.exists(), "An offline library command launched an external program")
        after = {
            str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
