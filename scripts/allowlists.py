#!/usr/bin/env python3
"""Validate local allowlists and preview declared capabilities without executing requests."""

import hashlib
import json
from pathlib import Path
import re
import sys

from worker_library import (
    DEFAULT_ROOT, LibraryError, Parser, load_library, parse_json, read_json, read_json_bytes, require,
)


DEFAULT_FILE = Path(__file__).resolve().parents[1] / "config" / "allowlists" / "agent-models.json"
IDENTITY_FIELDS = ("environment_id", "worker_id", "backend_id", "provider", "model_type", "model")
COMBINATION_FIELDS = (*IDENTITY_FIELDS, "reasoning_effort")
POLICY_FIELDS = {"schema_version", "id", "revision", "status", "owner_role", "entries"}
AGENT_DEFAULT_FIELDS = {"default_environment_id", "default_model", "default_reasoning_effort",
                        "environment_defaults"}
DEFAULT_VALUE_FIELDS = ("model", "reasoning_effort")
ENTRY_FIELDS = {*COMBINATION_FIELDS, "evidence_ref"}
IMAGE_IDENTITY_FIELDS = ("provider", "model_type", "model")
IMAGE_CAPABILITY_FIELDS = {*IMAGE_IDENTITY_FIELDS, "operations", "evidence_ref"}
IMAGE_ENTRY_FIELDS = {*IMAGE_CAPABILITY_FIELDS, "reasoning_effort"}
IMAGE_OPERATIONS = {"generate", "edit"}
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
UNKNOWN_VALUES = {"unknown", "unconfirmed", "not_applicable"}


def identity(value, field, code):
    require(isinstance(value, str) and IDENTITY.fullmatch(value)
            and value.lower() not in UNKNOWN_VALUES,
            f"{field}: expected an explicit identity, not an unknown value or wildcard", code)


def evidence(value, code):
    require(isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and value.lower() not in UNKNOWN_VALUES,
            "evidence_ref: expected an explicit nonempty reference", code)


def check_agent_record(row, library, code, *, optional_environment=False):
    for field in IDENTITY_FIELDS:
        if field == "environment_id" and optional_environment and field not in row:
            continue
        identity(row.get(field), field, code)
    evidence(row.get("evidence_ref"), code)
    if library is None:
        return None
    worker = library["worker_map"].get(row["worker_id"])
    require(worker is not None, f"Unknown Worker: {row['worker_id']}", code)
    require(row["backend_id"] in {backend["id"] for backend in worker["backends"]},
            f"Unknown backend for Worker {row['worker_id']}: {row['backend_id']}", code)
    require(row.get("environment_id", worker["host"]) == worker["host"],
            f"environment_id does not match Worker {row['worker_id']}", code)
    require(worker["runtime"] != "herdr-pane", "A shell-only Worker has no Agent model combination", code)
    return worker


def validate_policy_header(policy):
    code = "INVALID_ALLOWLIST"
    scenario = policy.get("id")
    require(scenario in ("agent-models", "image-models"), "Unsupported allowlist scenario", code)
    allowed_fields = POLICY_FIELDS | AGENT_DEFAULT_FIELDS if scenario == "agent-models" else POLICY_FIELDS
    require(POLICY_FIELDS <= policy.keys() <= allowed_fields,
            "Allowlist fields do not match schema_version 1", code)
    require(type(policy["schema_version"]) is int and policy["schema_version"] == 1,
            "Unsupported allowlist schema_version", code)
    require(type(policy["revision"]) is int and policy["revision"] > 0,
            "revision must be a positive integer", code)
    require(isinstance(policy["status"], str) and policy["status"] in {"draft", "active", "disabled"},
            "Invalid allowlist status", code)
    require(policy["owner_role"] == "project_coordinator", "Invalid allowlist maintenance owner", code)
    require(isinstance(policy["entries"], list), "entries must be an array", code)


def read_policy(path):
    raw = read_json_bytes(path)
    policy = parse_json(raw, path.name)
    validate_policy_header(policy)
    return policy, {
        "policy_id": policy["id"],
        "revision": policy["revision"],
        "policy_sha256": hashlib.sha256(raw).hexdigest(),
    }


def read_active_agent_policy(path, library_root):
    policy, policy_ref = read_policy(path)
    require(policy["id"] == "agent-models" and policy["status"] == "active",
            "An active agent-models allowlist is required for creation", "INACTIVE_AGENT_POLICY")
    library = load_library(library_root)
    validate_agent_policy(policy, library)
    return policy, policy_ref, library


def validate_agent_policy(policy, library):
    code = "INVALID_ALLOWLIST"
    seen = set()
    for row in policy["entries"]:
        require(isinstance(row, dict) and set(row) == ENTRY_FIELDS,
                "Each entry must declare one complete model/effort combination", code)
        check_agent_record(row, library, code)
        if row["reasoning_effort"] is not None:
            identity(row["reasoning_effort"], "reasoning_effort", code)
        key = tuple(row[field] for field in COMBINATION_FIELDS)
        require(key not in seen, "Duplicate model/effort combination", code)
        seen.add(key)
    if "default_reasoning_effort" in policy:
        default = policy["default_reasoning_effort"]
        identity(default, "default_reasoning_effort", code)
        if "default_model" not in policy:
            require(not seen or any(key[-1] == default for key in seen),
                    "default_reasoning_effort must occur in a nonempty policy without a default model", code)

    if {"default_environment_id", "default_model", "environment_defaults"} & policy.keys():
        baseline = policy.get("default_environment_id")
        identity(baseline, "default_environment_id", code)
        identity(policy.get("default_model"), "default_model", code)
        require("default_reasoning_effort" in policy, "default_reasoning_effort is required", code)
        overrides = policy.get("environment_defaults", {})
        require(isinstance(overrides, dict), "environment_defaults must be an object", code)
        for environment, values in overrides.items():
            identity(environment, "environment_defaults environment_id", code)
            require(environment != baseline,
                    "Change the shared default fields to update the default_environment_id environment", code)
            require(isinstance(values, dict) and values and values.keys() <= set(DEFAULT_VALUE_FIELDS),
                    "An environment override must specify model, reasoning_effort, or both", code)
            for field, value in values.items():
                identity(value, f"environment_defaults.{environment}.{field}", code)


def resolve_agent_default(policy, environment):
    override = policy.get("environment_defaults", {}).get(environment, {})
    values, sources = {}, {}
    for field in DEFAULT_VALUE_FIELDS:
        baseline_field = f"default_{field}"
        if field in override:
            values[field] = override[field]
            sources[field] = "environment_override"
        elif baseline_field in policy:
            values[field] = policy[baseline_field]
            sources[field] = "shared_default"
        else:
            values[field] = None
            sources[field] = "not_configured"
    return {**values, "sources": sources}


def describe_environment_defaults(policy, environments):
    result = []
    for environment in environments:
        identity(environment, "environment_id", "ARGUMENT_ERROR")
        defaults = resolve_agent_default(policy, environment)
        if defaults["model"] is None or defaults["reasoning_effort"] is None:
            allowlist_status = "default_not_configured"
        elif any(entry["environment_id"] == environment
                 and all(entry[field] == defaults[field] for field in DEFAULT_VALUE_FIELDS)
                 for entry in policy["entries"]):
            allowlist_status = "combination_declared"
        else:
            allowlist_status = "default_combination_not_allowlisted"
        result.append({"environment_id": environment, **defaults,
                       "allowlist_status": allowlist_status, "capability_status": "not_checked"})
    return result


def check_image_record(row, fields, code):
    require(isinstance(row, dict) and set(row) == fields, "Invalid image model record fields", code)
    for field in IMAGE_IDENTITY_FIELDS:
        identity(row[field], field, code)
    require(row["model_type"] == "image_generation", "model_type must be image_generation", code)
    operations = row["operations"]
    require(isinstance(operations, list) and operations
            and all(isinstance(operation, str) and operation in IMAGE_OPERATIONS for operation in operations),
            "operations must list generate or edit", code)
    require(len(operations) == len(set(operations)), "Duplicate image operation", code)
    evidence(row["evidence_ref"], code)
    return tuple(row[field] for field in IMAGE_IDENTITY_FIELDS)


def validate_image_policy(policy):
    code = "INVALID_ALLOWLIST"
    seen = set()
    for row in policy["entries"]:
        key = check_image_record(row, IMAGE_ENTRY_FIELDS, code)
        require(row["reasoning_effort"] is None, "reasoning_effort must be null for image models", code)
        require(key not in seen, "Duplicate image model", code)
        seen.add(key)


def preview_agent(policy, capabilities, library):
    code = "INVALID_CAPABILITIES"
    declared = []
    for row in capabilities:
        require(isinstance(row, dict), "capabilities must contain objects", code)
        worker = check_agent_record(row, library, code, optional_environment=True)
        efforts = row.get("supported_efforts")
        require(isinstance(efforts, list), "supported_efforts must be an array", code)
        for effort in efforts:
            if effort is not None:
                identity(effort, "supported_efforts", code)
        require(len(efforts) == len(set(efforts)), "Duplicate supported effort", code)
        declared.append({**row, "environment_id": row.get("environment_id", worker["host"])})

    matched, excluded = [], []
    default_matched, default_exclusions = [], {}
    defaults_by_environment = {
        environment: resolve_agent_default(policy, environment)
        for environment in dict.fromkeys(entry["environment_id"] for entry in policy["entries"])
    }
    for entry in policy["entries"]:
        worker = library["worker_map"][entry["worker_id"]]
        rows = [row for row in declared if all(row[field] == entry[field] for field in IDENTITY_FIELDS)]
        sources = sorted({row["evidence_ref"] for row in rows
                          if entry["reasoning_effort"] in row["supported_efforts"]})
        if worker["lifecycle"] != "active":
            reason = "worker_not_active"
        elif not rows:
            reason = "model_capability_not_observed"
        elif not sources:
            reason = "effort_capability_not_observed"
        else:
            reason = None
        if reason is None:
            matched.append({"entry": entry, "capability_sources": sources})
        else:
            excluded.append({"entry": entry, "reason": reason})
        defaults = defaults_by_environment[entry["environment_id"]]
        if defaults["reasoning_effort"] is not None:
            scope = {field: entry[field] for field in IDENTITY_FIELDS}
            key = tuple(scope.values())
            if defaults["model"] is not None and entry["model"] != defaults["model"]:
                default_exclusions.setdefault(key, {"scope": scope, "reason": "model_is_not_default"})
            elif entry["reasoning_effort"] == defaults["reasoning_effort"]:
                default_exclusions[key] = {"scope": scope, "reason": reason}
                if reason is None:
                    default_matched.append(matched[-1])
            else:
                default_exclusions.setdefault(key, {"scope": scope, "reason": "default_effort_not_allowlisted"})
    return matched, excluded, {
        "default_matched_entries": default_matched,
        "default_excluded_scopes": [row for row in default_exclusions.values() if row["reason"] is not None],
    }


def preview_image(policy, capabilities):
    declared = {}
    for row in capabilities:
        key = check_image_record(row, IMAGE_CAPABILITY_FIELDS, "INVALID_CAPABILITIES")
        declared.setdefault(key, []).append(row)

    matched, excluded = [], []
    for entry in policy["entries"]:
        rows = declared.get(tuple(entry[field] for field in IMAGE_IDENTITY_FIELDS), [])
        matched_operations, excluded_operations = [], []
        for operation in entry["operations"]:
            sources = sorted({row["evidence_ref"] for row in rows if operation in row["operations"]})
            if sources:
                matched_operations.append({"operation": operation, "capability_sources": sources})
            else:
                excluded_operations.append(operation)
        if matched_operations:
            matched.append({"entry": entry, "matched_operations": matched_operations,
                            "excluded_operations": excluded_operations})
        else:
            reason = "operation_capability_not_observed" if rows else "model_capability_not_observed"
            excluded.append({"entry": entry, "reason": reason})
    return matched, excluded


def main(argv=None):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in ("validate", "preview", "defaults"):
        command = commands.add_parser(name)
        command.add_argument("--file", type=Path, default=DEFAULT_FILE, help="local allowlist JSON")
        command.add_argument("--library-root", type=Path, default=DEFAULT_ROOT,
                             help="local Worker/role library for Agent validate and preview")
        if name == "preview":
            command.add_argument("--capabilities", type=Path, required=True, help="declared capability JSON")
        if name == "defaults":
            command.add_argument("--environment", required=True, action="append", help="environment ID; repeatable")
    try:
        arguments = parser.parse_args(argv)
        policy, policy_ref = read_policy(arguments.file)
        scenario = policy["id"]
        defaults_only = arguments.command == "defaults"
        require(not defaults_only or scenario == "agent-models",
                "defaults is only available for agent-models", "INVALID_ALLOWLIST")
        if scenario == "agent-models":
            library = None if defaults_only else load_library(arguments.library_root)
            validate_agent_policy(policy, library)
        else:
            validate_image_policy(policy)
        result = {
            "valid": True,
            **policy_ref,
            "scenario": scenario,
            "schema_version": policy["schema_version"],
            "status": policy["status"],
            "entry_count": len(policy["entries"]),
            "readiness": "offline_only" if policy["status"] == "active" else "not_enabled",
            "creation_enforced": False,
            "dispatch_performed": False,
        }
        if scenario == "agent-models":
            result.update(default_environment_id=policy.get("default_environment_id"),
                          default_model=policy.get("default_model"),
                          default_reasoning_effort=policy.get("default_reasoning_effort"))
        if defaults_only:
            result.update(worker_library_checked=False,
                          environment_defaults=describe_environment_defaults(policy, arguments.environment))
        if arguments.command == "preview":
            capabilities = read_json(arguments.capabilities).get("capabilities")
            require(isinstance(capabilities, list), "capabilities must be an array", "INVALID_CAPABILITIES")
            if scenario == "agent-models":
                matched, excluded, defaults = preview_agent(policy, capabilities, library)
                result.update(defaults)
            else:
                matched, excluded = preview_image(policy, capabilities)
            result.update(matched_entries=matched, excluded_entries=excluded,
                          matched_count=len(matched), excluded_count=len(excluded))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (LibraryError, OSError, UnicodeError) as error:
        print(json.dumps({"error": {"code": getattr(error, "code", "READ_ERROR"),
                                    "message": str(error)}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
