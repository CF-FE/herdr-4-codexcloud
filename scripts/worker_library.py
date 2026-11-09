#!/usr/bin/env python3
"""Read the local Worker/role library and prepare drafts; never start a Worker."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "docs" / "worker-library"
ID = re.compile(r"[a-z][a-z0-9_-]*")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
EVIDENCE_KEYS = (
    "installation", "recognition", "text_roundtrip", "herdr_roundtrip",
    "tools", "business_acceptance",
)
EVIDENCE_STATES = {"PASS", "REPORTED_PASS", "UNKNOWN", "NOT_RUN", "NOT_APPLICABLE"}


class LibraryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LibraryError("ARGUMENT_ERROR", message)


def require(condition, message, code="INVALID_LIBRARY"):
    if not condition:
        raise LibraryError(code, message)


def string(value, name):
    require(isinstance(value, str) and bool(value.strip()), f"{name}: expected nonempty string")


def strings(value, name):
    require(isinstance(value, list), f"{name}: expected string array")
    for item in value:
        string(item, name)


def read_json(path):
    return parse_json(read_json_bytes(path), path.name)


def read_json_bytes(path):
    try:
        return path.read_bytes()
    except OSError as error:
        raise LibraryError("INVALID_JSON", f"Cannot read {path.name}: {error}") from error


def parse_json(data, name):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise LibraryError("INVALID_JSON", f"Nonfinite JSON value: {value}")

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                            parse_constant=reject_constant)
    except LibraryError:
        raise
    except (ValueError, RecursionError) as error:
        raise LibraryError("INVALID_JSON", f"Cannot read {name}: {error}") from error
    require(isinstance(result, dict), f"{name}: expected JSON object")
    return result


def resource(root, name):
    string(name, "resource")
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts
            and "\\" not in name and not re.match(r"[A-Za-z]:", name),
            f"Unsafe resource path: {name}", "INVALID_RESOURCE")
    current = root
    require(not current.is_symlink(), "Library root must not be a symlink", "INVALID_RESOURCE")
    for part in path.parts:
        current = current / part
        require(not current.is_symlink(), f"Symlink resource: {name}", "INVALID_RESOURCE")
    require(current.is_file(), f"Missing resource: {name}", "INVALID_RESOURCE")
    require(current.resolve().is_relative_to(root.resolve()),
            f"Resource escapes library: {name}", "INVALID_RESOURCE")
    return current


def entries(value, name):
    require(isinstance(value, list), f"{name}: expected array")
    result = {}
    for item in value:
        require(isinstance(item, dict), f"{name}: expected objects")
        key = item.get("id")
        require(isinstance(key, str) and ID.fullmatch(key), f"{name}: invalid ID")
        require(key not in result, f"{name}: duplicate ID {key}")
        result[key] = item
    return result


def load_library(root):
    root = root.absolute()
    workers = read_json(resource(root, "workers.json"))
    roles_path = resource(root, workers.get("role_index"))
    roles_root = roles_path.parent
    roles = read_json(roles_path)
    for name, document in (("Worker", workers), ("Role", roles)):
        require(type(document.get("schema_version")) is int
                and document["schema_version"] == 1, f"{name}: unsupported schema_version")
        require(isinstance(document.get("library_version"), str)
                and VERSION.fullmatch(document["library_version"]), f"{name}: invalid library_version")
    worker_map = entries(workers.get("workers"), "workers")
    role_map = entries(roles.get("roles"), "roles")
    project_map = entries(roles.get("projects", []), "projects")
    method_map = entries(roles.get("methods", []), "methods")
    common = roles.get("common")
    require(isinstance(common, dict), "Role common contract is missing")
    require(isinstance(common.get("version"), str) and VERSION.fullmatch(common["version"]),
            "Invalid common contract version")
    resource(roles_root, common.get("resource"))
    for project in project_map.values():
        string(project.get("name"), "project name")
        if "version" in project:
            require(isinstance(project["version"], str) and VERSION.fullmatch(project["version"]),
                    "Invalid project contract version")
        resource(roles_root, project.get("resource"))
    for role in role_map.values():
        string(role.get("name"), "role name")
        require(isinstance(role.get("scope"), str) and role["scope"] in {"workspace", "project"},
                "Invalid role scope")
        require(isinstance(role.get("kind"), str)
                and role["kind"] in {"shared_role", "task_template", "coordinator", "project_owner"},
                "Invalid role kind")
        require(isinstance(role.get("lifecycle", "active"), str)
                and role.get("lifecycle", "active") in {"active", "paused", "retired"},
                "Invalid role lifecycle")
        require("dispatchable" not in role or type(role["dispatchable"]) is bool,
                "Role dispatchable must be boolean")
        require(isinstance(role.get("definition_version"), str)
                and VERSION.fullmatch(role["definition_version"]), "Invalid role definition_version")
        strings(role.get("requirements", []), "role requirements")
        strings(role.get("methods", []), "role methods")
        require(set(role.get("methods", [])) <= method_map.keys(), "Unknown role method")
        resource(roles_root, role.get("resource"))
        if role["scope"] == "project":
            string(role.get("project_id"), "role project_id")
            require(role.get("project_id") in project_map, "Unknown role project")
    for worker in worker_map.values():
        for field in ("name", "host", "summary"):
            string(worker.get(field), f"worker {field}")
        require(isinstance(worker.get("version"), str) and VERSION.fullmatch(worker["version"]),
                "Invalid Worker template version")
        require(isinstance(worker.get("lifecycle"), str)
                and worker["lifecycle"] in {"active", "paused", "retired"}, "Invalid Worker lifecycle")
        require(isinstance(worker.get("runtime"), str)
                and worker["runtime"] in {"herdr-agent", "herdr-pane", "codex-cloud-native"},
                "Invalid Worker runtime")
        if worker["runtime"] == "herdr-agent":
            string(worker.get("agent_kind"), "agent_kind")
        else:
            require(worker.get("agent_kind") is None, "Non-agent runtime cannot define agent_kind")
        for field in ("intended_tasks", "capabilities", "dependencies", "constraints", "launch_notes"):
            strings(worker.get(field), f"worker {field}")
        backends = entries(worker.get("backends"), "backends")
        require(bool(backends), "Worker must have at least one backend")
        for backend in backends.values():
            for field in ("provider", "model", "transport", "evidence_owner", "observed_on"):
                string(backend.get(field), f"backend {field}")
            strings(backend.get("agent_args"), "agent_args")
            strings(backend.get("evidence_refs"), "evidence_refs")
            strings(backend.get("dependencies", []), "backend dependencies")
            evidence = backend.get("evidence")
            require(isinstance(evidence, dict) and all(isinstance(evidence.get(k), str)
                    and evidence[k] in EVIDENCE_STATES
                    for k in EVIDENCE_KEYS), "Invalid backend verification evidence")
    return {"root": root, "workers": workers, "roles": roles, "roles_root": roles_root,
            "worker_map": worker_map, "role_map": role_map, "project_map": project_map,
            "method_map": method_map, "worker_index": root / "workers.json", "role_index": roles_path}


def role_is_executable(role):
    return (role["kind"] not in {"coordinator", "project_owner"}
            and role.get("dispatchable", True) and role.get("lifecycle", "active") == "active")


def render_role(library, role_id, project_id=None, render=False):
    role = library["role_map"].get(role_id)
    require(role is not None, f"Unknown role: {role_id}", "UNKNOWN_ROLE")
    if role["scope"] == "project":
        require(project_id == role["project_id"], "Project role requires its explicit matching project",
                "PROJECT_MISMATCH")
    project = None
    if project_id:
        project = library["project_map"].get(project_id)
        require(project is not None, f"Unknown project: {project_id}", "UNKNOWN_PROJECT")
    names = [library["roles"]["common"]["resource"], role["resource"]]
    if project:
        names.append(project["resource"])
    resources, prompt = [], []
    for name in names:
        path = resource(library["roles_root"], name)
        data = path.read_bytes()
        text = data.decode("utf-8")
        require(bool(text.strip()), f"Empty role resource: {name}", "INVALID_RESOURCE")
        resources.append({"path": path.relative_to(library["root"]).as_posix(),
                          "sha256": hashlib.sha256(data).hexdigest()})
        if render:
            prompt.append(text)
    result = {**role, "library_version": library["roles"]["library_version"],
              "common_version": library["roles"]["common"]["version"],
              "project": project, "resources": resources,
              "methods": [library["method_map"][key] for key in role.get("methods", [])],
              "dispatchable": role_is_executable(role)}
    if render:
        result["prompt"] = "\n\n".join(prompt)
    return result


def candidates(library, role, host=None, worker_id=None, backend_id=None, capabilities=()):
    require(role["dispatchable"], "Organizational navigation is not an executable role",
            "ROLE_NOT_EXECUTABLE")
    if worker_id:
        require(worker_id in library["worker_map"], f"Unknown Worker: {worker_id}", "UNKNOWN_WORKER")
    if backend_id:
        targets = ([library["worker_map"][worker_id]] if worker_id
                   else library["worker_map"].values())
        require(any(b["id"] == backend_id for w in targets for b in w["backends"]),
                f"Unknown backend: {backend_id}", "UNKNOWN_BACKEND")
    required = set(role.get("requirements", [])) | set(capabilities)
    result = []
    for worker in library["worker_map"].values():
        if worker["lifecycle"] != "active" or (host and worker["host"] != host):
            continue
        if worker_id and worker["id"] != worker_id:
            continue
        if not required <= set(worker["capabilities"]):
            continue
        for backend in worker["backends"]:
            if backend_id and backend["id"] != backend_id:
                continue
            result.append({"worker_id": worker["id"], "worker_version": worker["version"],
                           "backend_id": backend["id"], "host": worker["host"],
                           "provider": backend["provider"], "model": backend["model"],
                           "evidence": backend["evidence"], "evidence_refs": backend["evidence_refs"],
                           "evidence_owner": backend["evidence_owner"],
                           "observed_on": backend["observed_on"],
                           "dependencies": worker["dependencies"] + backend.get("dependencies", []),
                           "constraints": worker["constraints"], "live_status": "UNKNOWN",
                           "readiness": "REQUIRES_RUNTIME_PREFLIGHT"})
    return result


def prepare(library, arguments, role):
    worker = library["worker_map"].get(arguments.worker)
    require(worker is not None, f"Unknown Worker: {arguments.worker}", "UNKNOWN_WORKER")
    require(worker["lifecycle"] == "active", "Worker template is paused or retired", "WORKER_INACTIVE")
    if arguments.backend:
        backend = next((b for b in worker["backends"] if b["id"] == arguments.backend), None)
        require(backend is not None, f"Unknown backend: {arguments.backend}", "UNKNOWN_BACKEND")
    else:
        require(len(worker["backends"]) == 1, "Select an explicit backend; no automatic first choice",
                "AMBIGUOUS_BACKEND")
        backend = worker["backends"][0]
    matched = candidates(library, role, worker_id=worker["id"], backend_id=backend["id"])
    require(bool(matched), "Worker does not declare the role's required capabilities",
            "CAPABILITY_MISMATCH")
    task = None
    if arguments.task_file:
        task = read_json(arguments.task_file)
        if "schema_version" in task:
            require(type(task["schema_version"]) is int and task["schema_version"] == 1,
                    "Unsupported task schema_version", "INVALID_TASK")
        for field in ("task_id", "project_id", "goal", "authorization_ref", "dispatch_owner",
                      "report_to", "report_route"):
            string(task.get(field), f"task {field}")
        for field in ("owned_paths", "input_refs", "acceptance_criteria", "output_refs"):
            strings(task.get(field), f"task {field}")
            require(bool(task[field]), f"task {field} must not be empty", "INVALID_TASK")
        if arguments.project:
            require(task["project_id"] == arguments.project, "Task and selected project differ",
                    "PROJECT_MISMATCH")
        if task["project_id"] in library["project_map"]:
            require(arguments.project == task["project_id"],
                    "Known project requires explicit --project to load its contract", "PROJECT_MISMATCH")
    launch = {"mechanism": worker["runtime"], "executed": False,
              "agent_start_argv_template": None, "prompt_argv_template": None,
              "notes": worker["launch_notes"]}
    bindings = ["effective_task_authority_and_budget", "current_writer_and_no_pending_duplicate",
                "live_target_environment_and_tools", "native_execution_refs",
                "actual_provider_model_and_permissions", "result_reader_and_report_route"]
    if worker["runtime"] == "herdr-agent":
        route = (["--session", "<DEDICATED_LOCAL_SESSION>"] if worker["host"] == "mac-work"
                 else ["--machine", "<SAVED_MACHINE_ID_FROM_NATIVE_LIST>"])
        command = ["herdr", *route, "agent", "start", "<NEW_AGENT_NAME>", "--kind",
                   worker["agent_kind"], "--pane", "<NEW_OWNED_PANE_ID>"]
        if backend["agent_args"]:
            command += ["--", *backend["agent_args"]]
        launch["agent_start_argv_template"] = command
        launch["prompt_argv_template"] = ["herdr", *route, "agent", "prompt", "<ACTUAL_AGENT_REF>",
                                           "<ROLE_AND_TASK_PROMPT>", "--wait"]
        bindings.append("new_owned_pane_at_shell_prompt_with_correct_working_directory")
    elif worker["runtime"] == "codex-cloud-native":
        bindings.append("published_cloud_environment_and_authorized_native_task_creation_tool")
    else:
        bindings.append("authorized_command_or_script_ref_and_exact_pane_api")
    if task is None:
        bindings.append("task_id_project_goal_inputs_outputs_acceptance_and_authority")
    if any(arg.startswith("<") and arg.endswith(">") for arg in backend["agent_args"]):
        bindings.append("backend_model_alias_from_target_configuration")
    if role["kind"] == "shared_role" and role["id"] == "workspace_quality":
        bindings.append("independent_review_context_not_involved_in_this_artifact_version")
    if role["project"]:
        bindings.append("project_owner_supplied_current_frozen_inputs_and_domain_dependencies")
    snapshot = {"worker_library_version": library["workers"]["library_version"],
                "worker_index_sha256": hashlib.sha256(library["worker_index"].read_bytes()).hexdigest(),
                "role_library_version": library["roles"]["library_version"],
                "role_index_sha256": hashlib.sha256(library["role_index"].read_bytes()).hexdigest()}
    return {"kind": "worker_request_draft", "dispatch_performed": False,
            "readiness": "REQUIRES_RUNTIME_PREFLIGHT", "live_status": "UNKNOWN",
            "catalog_snapshot": snapshot, "worker": worker, "backend": backend,
            "role": role, "task": task, "launch_plan": launch, "required_bindings": bindings}


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="local library directory")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    commands.add_parser("check")
    listing = commands.add_parser("list")
    listing.add_argument("--host")
    role_list = commands.add_parser("roles")
    role_list.add_argument("--project")
    one_role = commands.add_parser("role")
    one_role.add_argument("role")
    one_role.add_argument("--project")
    one_role.add_argument("--render", action="store_true")
    selection = commands.add_parser("select")
    selection.add_argument("--role", required=True)
    selection.add_argument("--project")
    selection.add_argument("--host")
    selection.add_argument("--worker")
    selection.add_argument("--backend")
    selection.add_argument("--capability", action="append", default=[])
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--worker", required=True)
    preparation.add_argument("--role", required=True)
    preparation.add_argument("--backend")
    preparation.add_argument("--project")
    preparation.add_argument("--task-file", type=Path)
    try:
        arguments = parser.parse_args(argv)
        library = load_library(arguments.root)
        if arguments.command == "check":
            for role in library["role_map"].values():
                project = role.get("project_id") if role["scope"] == "project" else None
                render_role(library, role["id"], project, render=True)
            result = {"valid": True, "worker_count": len(library["worker_map"]),
                      "backend_count": sum(len(w["backends"]) for w in library["worker_map"].values()),
                      "role_count": len(library["role_map"]),
                      "professional_role_count": sum(role_is_executable(r)
                                                     for r in library["role_map"].values()),
                      "project_count": len(library["project_map"]), "dispatch_performed": False,
                      "worker_library_version": library["workers"]["library_version"],
                      "role_library_version": library["roles"]["library_version"]}
        elif arguments.command == "list":
            result = {"workers": [w for w in library["worker_map"].values()
                                  if w["lifecycle"] == "active"
                                  and (not arguments.host or w["host"] == arguments.host)],
                      "library_version": library["workers"]["library_version"],
                      "live_status": "UNKNOWN", "dispatch_performed": False}
        elif arguments.command == "roles":
            if arguments.project:
                require(arguments.project in library["project_map"], "Unknown project", "UNKNOWN_PROJECT")
            result = {"roles": [{**r, "dispatchable": role_is_executable(r)}
                                for r in library["role_map"].values()
                                if r.get("lifecycle", "active") == "active"
                                and (r["scope"] == "workspace" or r.get("project_id") == arguments.project)],
                      "library_version": library["roles"]["library_version"]}
        else:
            role_id = arguments.role
            if arguments.command == "role" and role_id not in library["role_map"]:
                # Old imported navigation IDs are lookup-only, never dispatch aliases.
                matches = [r["id"] for r in library["role_map"].values()
                           if r["kind"] in {"coordinator", "project_owner"} and r.get("source_role_id") == role_id]
                if len(matches) == 1:
                    role_id = matches[0]
            role = render_role(library, role_id, arguments.project,
                               render=arguments.command == "prepare" or getattr(arguments, "render", False))
            if arguments.command == "role":
                result = role
            elif arguments.command == "select":
                result = {"candidates": candidates(library, role, arguments.host, arguments.worker,
                                                   arguments.backend, arguments.capability),
                          "live_status": "UNKNOWN", "dispatch_performed": False,
                          "note": "Capabilities are intended matches, not live verification or authorization."}
            else:
                result = prepare(library, arguments, role)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (LibraryError, OSError, UnicodeError) as error:
        print(json.dumps({"error": {"code": getattr(error, "code", "READ_ERROR"),
                                    "message": str(error)}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
