import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "allowlists.py"
DEFAULT_POLICY = ROOT / "config" / "allowlists" / "agent-models.json"


class AllowlistFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.entry = {
            "environment_id": "mac-work",
            "worker_id": "mac-codex",
            "backend_id": "current-default",
            "provider": "synthetic-provider",
            "model_type": "reasoning",
            "model": "synthetic-model",
            "reasoning_effort": "high",
            "evidence_ref": "fixture:approved-candidate",
        }
        self.policy = {
            "schema_version": 1,
            "id": "agent-models",
            "revision": 1,
            "status": "draft",
            "owner_role": "project_coordinator",
            "entries": [self.entry],
        }
        self.capability = {key: value for key, value in self.entry.items() if key != "reasoning_effort"}
        self.capability.update(supported_efforts=["high"], evidence_ref="fixture:capability")
        self.policy_path = self.directory / "policy.json"
        self.capability_path = self.directory / "capabilities.json"

    def run_cli(self, command="validate", *, capabilities=None, default=False, library_root=None,
                environments=()):
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        argv = [sys.executable, "-B", str(SCRIPT), command]
        if not default:
            argv.extend(("--file", str(self.policy_path)))
        if library_root is not None:
            argv.extend(("--library-root", str(library_root)))
        for environment in environments:
            argv.extend(("--environment", environment))
        if command == "preview":
            document = {"capabilities": [self.capability] if capabilities is None else capabilities}
            self.capability_path.write_text(json.dumps(document), encoding="utf-8")
            argv.extend(("--capabilities", str(self.capability_path)))
        result = subprocess.run(argv, cwd=self.directory, capture_output=True, text=True, timeout=10,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        return result, json.loads(result.stdout or result.stderr)


class AllowlistTests(AllowlistFixture):
    def test_default_path_reads_valid_policy_without_effects(self):
        result, data = self.run_cli(default=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(data["valid"])
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["policy_sha256"], hashlib.sha256(DEFAULT_POLICY.read_bytes()).hexdigest())
        self.assertFalse(data["creation_enforced"])
        self.assertFalse(data["dispatch_performed"])

    def test_custom_draft_validates_without_activation(self):
        result, data = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["status"], "draft")
        self.assertEqual(data["entry_count"], 1)
        self.assertEqual(data["policy_sha256"], hashlib.sha256(self.policy_path.read_bytes()).hexdigest())

    def test_preview_matches_exact_tuple_but_draft_is_not_enabled(self):
        result, data = self.run_cli("preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_count"], 1)
        self.assertEqual(data["matched_entries"][0]["capability_sources"], ["fixture:capability"])
        self.assertEqual(data["readiness"], "not_enabled")
        self.assertFalse(data["creation_enforced"])

    def test_capabilities_do_not_form_cross_model_or_effort_combinations(self):
        other_model = {**self.capability, "model": "another-model"}
        wrong_effort = {**self.capability, "supported_efforts": ["low"]}
        result, data = self.run_cli("preview", capabilities=[other_model, wrong_effort])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_count"], 0)
        self.assertEqual(data["excluded_entries"][0]["reason"], "effort_capability_not_observed")

    def test_default_effort_preserves_all_explicit_matches_and_does_not_choose_a_model(self):
        self.policy["default_reasoning_effort"] = "high"
        self.policy["entries"].extend([
            {**self.entry, "reasoning_effort": "low"},
            {**self.entry, "model": "second-model"},
            {**self.entry, "model": "third-model", "reasoning_effort": "low"},
            {**self.entry, "model": "fourth-model"},
        ])
        capabilities = [
            {**self.capability, "supported_efforts": ["low", "high"]},
            {**self.capability, "model": "second-model"},
            {**self.capability, "model": "third-model", "supported_efforts": ["low", "high"]},
            {**self.capability, "model": "fourth-model", "supported_efforts": ["low"]},
        ]
        result, data = self.run_cli("preview", capabilities=capabilities)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["scenario"], "agent-models")
        self.assertEqual(data["default_reasoning_effort"], "high")
        self.assertEqual([(row["entry"]["model"], row["entry"]["reasoning_effort"])
                          for row in data["matched_entries"]],
                         [("synthetic-model", "high"), ("synthetic-model", "low"),
                          ("second-model", "high"), ("third-model", "low")])
        self.assertEqual([row["entry"]["model"] for row in data["default_matched_entries"]],
                         ["synthetic-model", "second-model"])
        self.assertEqual({row["scope"]["model"]: row["reason"] for row in data["default_excluded_scopes"]},
                         {"third-model": "default_effort_not_allowlisted",
                          "fourth-model": "effort_capability_not_observed"})

    def test_declared_default_must_exist_in_a_nonempty_policy(self):
        for value in ("low", None, ["high"]):
            with self.subTest(value=value):
                self.policy["default_reasoning_effort"] = value
                result, data = self.run_cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"]["code"], "INVALID_ALLOWLIST")
                self.assertIn("default_reasoning_effort", data["error"]["message"])

    def test_environment_defaults_inherit_without_a_worker_registration(self):
        self.policy.update(default_environment_id="mac-work", default_model="synthetic-model",
                           default_reasoning_effort="high")
        environments = ("mac-work", "win-work", "codex-cloud", "linux-herdr", "future-environment")
        result, data = self.run_cli("defaults", environments=environments,
                                    library_root=self.directory / "absent")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["default_environment_id"], "mac-work")
        defaults = data["environment_defaults"]
        self.assertEqual([row["environment_id"] for row in defaults], list(environments))
        self.assertEqual([(row["model"], row["reasoning_effort"]) for row in defaults],
                         [("synthetic-model", "high")] * len(environments))
        self.assertEqual([row["sources"] for row in defaults],
                         [{"model": "shared_default", "reasoning_effort": "shared_default"}] * len(environments))
        self.assertEqual([row["allowlist_status"] for row in defaults],
                         ["combination_declared"] + ["default_combination_not_allowlisted"] * 4)
        self.assertTrue(all(row["capability_status"] == "not_checked" for row in defaults))
        self.assertFalse(data["worker_library_checked"])
        self.assertFalse(data["creation_enforced"])

    def test_environment_overrides_apply_field_by_field(self):
        self.policy.update(default_environment_id="mac-work", default_model="synthetic-model",
                           default_reasoning_effort="high",
                           environment_defaults={
                               "win-work": {"model": "other-model"},
                               "codex-cloud": {"reasoning_effort": "medium"},
                               "linux-herdr": {"model": "third-model", "reasoning_effort": "max"},
                           })
        result, data = self.run_cli("defaults", environments=("win-work", "codex-cloud", "linux-herdr"))
        self.assertEqual(result.returncode, 0, result.stderr)
        defaults = data["environment_defaults"]
        self.assertEqual([(row["model"], row["reasoning_effort"], row["sources"]) for row in defaults], [
            ("other-model", "high", {"model": "environment_override", "reasoning_effort": "shared_default"}),
            ("synthetic-model", "medium", {"model": "shared_default", "reasoning_effort": "environment_override"}),
            ("third-model", "max", {"model": "environment_override", "reasoning_effort": "environment_override"}),
        ])
        self.assertTrue(all(row["allowlist_status"] == "default_combination_not_allowlisted" for row in defaults))

    def test_invalid_defaults_and_overrides_are_rejected(self):
        for changes in ({"default_model": "UNKNOWN"}, {"default_reasoning_effort": None},
                        {"environment_defaults": {"*": {"model": "other-model"}}},
                        {"environment_defaults": {"win-work": {"provider": "other-provider"}}},
                        {"environment_defaults": {"win-work": {"reasoning_effort": ["high"]}}},
                        {"environment_defaults": {"win-work": {"model": None}}},
                        {"environment_defaults": {"win-work": {}}},
                        {"environment_defaults": []}):
            with self.subTest(changes=changes):
                self.policy = {**self.policy, "default_environment_id": "mac-work", "default_model": "synthetic-model",
                               "default_reasoning_effort": "high", "environment_defaults": {}, **changes}
                result, data = self.run_cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"]["code"], "INVALID_ALLOWLIST")
        self.policy["environment_defaults"] = {"mac-work": {"reasoning_effort": "medium"}}
        result, data = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("default_environment_id", data["error"]["message"])
        self.policy["environment_defaults"] = {}
        del self.policy["default_reasoning_effort"]
        result, data = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(data["error"]["code"], "INVALID_ALLOWLIST")

    def test_explicit_models_remain_matched_but_do_not_replace_the_default(self):
        self.policy.update(default_environment_id="mac-work", default_model="synthetic-model",
                           default_reasoning_effort="high")
        self.policy["entries"].append({**self.entry, "model": "other-model"})
        other_capability = {**self.capability, "model": "other-model"}
        result, data = self.run_cli("preview", capabilities=[self.capability, other_capability])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_count"], 2)
        self.assertEqual([row["entry"]["model"] for row in data["default_matched_entries"]], ["synthetic-model"])
        result, data = self.run_cli("preview", capabilities=[other_capability])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["default_matched_entries"], [])
        self.assertEqual({row["scope"]["model"]: row["reason"] for row in data["default_excluded_scopes"]},
                         {"synthetic-model": "model_capability_not_observed",
                          "other-model": "model_is_not_default"})
        self.policy["default_environment_id"] = "synthetic-base-environment"
        self.policy["environment_defaults"] = {"mac-work": {"model": "other-model"}}
        result, data = self.run_cli("preview", capabilities=[self.capability, other_capability])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row["entry"]["model"] for row in data["default_matched_entries"]], ["other-model"])

    def test_legacy_effort_only_default_has_no_implicit_model(self):
        self.policy["default_reasoning_effort"] = "high"
        result, data = self.run_cli("defaults", environments=("mac-work",))
        self.assertEqual(result.returncode, 0, result.stderr)
        default = data["environment_defaults"][0]
        self.assertIsNone(default["model"])
        self.assertEqual(default["reasoning_effort"], "high")
        self.assertEqual(default["allowlist_status"], "default_not_configured")

    def test_unobserved_model_is_excluded(self):
        result, data = self.run_cli("preview", capabilities=[{**self.capability, "provider": "another-provider"}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_count"], 0)
        self.assertEqual(data["excluded_entries"][0]["reason"], "model_capability_not_observed")

    def test_active_and_disabled_files_still_do_not_enforce_or_dispatch(self):
        for status, readiness in (("active", "offline_only"), ("disabled", "not_enabled")):
            with self.subTest(status=status):
                self.policy["status"] = status
                result, data = self.run_cli("preview")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(data["readiness"], readiness)
                self.assertFalse(data["creation_enforced"])
                self.assertFalse(data["dispatch_performed"])

    def test_empty_allowlist_and_empty_capabilities_do_not_allow_a_default(self):
        result, data = self.run_cli("preview", capabilities=[])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_count"], 0)
        self.policy["entries"] = []
        self.policy["default_reasoning_effort"] = "high"
        result, data = self.run_cli("preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_entries"], [])
        self.assertEqual(data["excluded_entries"], [])
        self.assertEqual(data["default_matched_entries"], [])
        self.assertEqual(data["default_excluded_scopes"], [])
        self.assertFalse(data["creation_enforced"])

    def test_null_effort_needs_explicit_not_applicable_capability(self):
        self.entry["reasoning_effort"] = None
        self.capability["supported_efforts"] = []
        result, data = self.run_cli("preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_count"], 0)
        self.capability["supported_efforts"] = [None]
        result, data = self.run_cli("preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_count"], 1)

    def test_unknown_or_wildcard_effort_is_rejected(self):
        for value in ("UNKNOWN", "*", ["high"], False):
            with self.subTest(value=value):
                self.entry["reasoning_effort"] = value
                result, data = self.run_cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"]["code"], "INVALID_ALLOWLIST")

    def test_duplicate_combinations_are_rejected_even_with_other_evidence(self):
        self.policy["entries"].append({**self.entry, "evidence_ref": "fixture:duplicate"})
        result, data = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Duplicate", data["error"]["message"])

    def test_invalid_worker_backend_or_environment_is_rejected(self):
        original = copy.deepcopy(self.entry)
        for field, value in (("worker_id", "missing-worker"), ("backend_id", "missing-backend"),
                             ("environment_id", "win-work")):
            with self.subTest(field=field):
                self.policy["entries"] = [{**original, field: value}]
                result, data = self.run_cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"]["code"], "INVALID_ALLOWLIST")

    def test_missing_extra_or_unknown_policy_fields_do_not_silently_pass(self):
        original = copy.deepcopy(self.policy)
        for change in ({"revision": True}, {"status": "approved"}, {"id": "other-scenario"}, {"enabled": True}):
            with self.subTest(change=change):
                self.policy = {**original, **change}
                result, data = self.run_cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"]["code"], "INVALID_ALLOWLIST")
        self.policy = original
        del self.entry["model_type"]
        self.policy["entries"] = [self.entry]
        result, data = self.run_cli()
        self.assertEqual(result.returncode, 2)

    def test_optional_capability_environment_is_derived_and_existing_fields_are_accepted(self):
        self.capability.pop("environment_id")
        self.capability.update(tools=["shell"], modalities=["text"], context_window=200000)
        result, data = self.run_cli("preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_count"], 1)

    def test_invalid_capabilities_are_rejected_without_a_traceback(self):
        for change in ({"environment_id": "win-work"}, {"supported_efforts": "high"},
                       {"supported_efforts": [["high"]]}, {"supported_efforts": ["UNKNOWN"]},
                       {"evidence_ref": ""}):
            with self.subTest(change=change):
                result, data = self.run_cli("preview", capabilities=[{**self.capability, **change}])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"]["code"], "INVALID_CAPABILITIES")
                self.assertNotIn("Traceback", result.stderr)

    def test_malformed_and_duplicate_key_json_are_rejected(self):
        documents = {
            "truncated": ('{"id":', "INVALID_JSON"),
            "duplicate": ('{"id":"agent-models","id":"agent-models"}', "INVALID_LIBRARY"),
            "nonfinite": ('{"revision":NaN}', "INVALID_JSON"),
            "deeply nested": ('{"entries":' + "[" * 2000 + "]" * 2000 + "}", "INVALID_JSON"),
        }
        if hasattr(sys, "get_int_max_str_digits"):
            documents["oversized integer"] = ('{"revision":' + "9" * 4301 + "}", "INVALID_JSON")
        for name, (document, code) in documents.items():
            with self.subTest(name=name):
                self.policy_path.write_text(document)
                result = subprocess.run([sys.executable, "-B", str(SCRIPT), "validate", "--file", str(self.policy_path)],
                                        cwd=self.directory, capture_output=True, text=True, timeout=10,
                                        env=dict(os.environ, PYTHONINTMAXSTRDIGITS="4300"))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr)["error"]["code"], code)
                self.assertNotIn("Traceback", result.stderr)


class ImageAllowlistTests(AllowlistFixture):
    def setUp(self):
        super().setUp()
        self.image_entry = {
            "provider": "synthetic-provider",
            "model_type": "image_generation",
            "model": "synthetic-image-model",
            "reasoning_effort": None,
            "operations": ["generate", "edit"],
            "evidence_ref": "fixture:model-documentation",
        }
        self.image_capability = {key: value for key, value in self.image_entry.items()
                                 if key != "reasoning_effort"}
        self.image_capability.update(operations=["generate"], evidence_ref="fixture:tool-readback")

    def use_image_policy(self):
        self.policy.update(id="image-models", entries=[self.image_entry])

    def test_image_validate_and_preview_do_not_require_worker_library(self):
        self.use_image_policy()
        library_root = self.directory / "absent"
        result, data = self.run_cli(library_root=library_root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["scenario"], "image-models")
        self.assertNotIn("default_reasoning_effort", data)
        result, data = self.run_cli("preview", capabilities=[self.image_capability],
                                    library_root=library_root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["matched_count"], 1)
        self.assertEqual(data["matched_entries"][0]["matched_operations"],
                         [{"operation": "generate", "capability_sources": ["fixture:tool-readback"]}])
        self.assertEqual(data["matched_entries"][0]["excluded_operations"], ["edit"])
        self.assertEqual(data["readiness"], "not_enabled")
        self.assertFalse(data["creation_enforced"])
        self.assertFalse(data["dispatch_performed"])
        result, data = self.run_cli("defaults", environments=("mac-work",))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(data["error"]["code"], "INVALID_ALLOWLIST")

    def test_image_records_require_null_effort_unique_model_and_supported_operations(self):
        self.use_image_policy()
        for change in ({"reasoning_effort": "high"}, {"operations": []}, {"operations": ["render"]},
                       {"operations": ["edit", "edit"]}, {"model_type": "reasoning"}):
            with self.subTest(change=change):
                self.policy["entries"] = [{**self.image_entry, **change}]
                result, data = self.run_cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"]["code"], "INVALID_ALLOWLIST")
        self.policy["entries"] = [self.image_entry, {**self.image_entry, "operations": ["edit"]}]
        result, data = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Duplicate", data["error"]["message"])

    def test_image_preview_requires_explicit_model_and_operation_capability(self):
        self.use_image_policy()
        for capabilities in ([], [{**self.image_capability, "model": "another-image-model"}]):
            with self.subTest(capabilities=capabilities):
                result, data = self.run_cli("preview", capabilities=capabilities)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(data["matched_count"], 0)
                self.assertEqual(data["excluded_entries"][0]["reason"], "model_capability_not_observed")
        self.image_entry["operations"] = ["edit"]
        result, data = self.run_cli("preview", capabilities=[self.image_capability])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data["excluded_entries"][0]["reason"], "operation_capability_not_observed")
        for change in ({"model": "UNKNOWN"}, {"evidence_ref": ""}, {"operations": [["generate"]]}):
            with self.subTest(change=change):
                result, data = self.run_cli("preview", capabilities=[{**self.image_capability, **change}])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(data["error"]["code"], "INVALID_CAPABILITIES")


if __name__ == "__main__":
    unittest.main()
