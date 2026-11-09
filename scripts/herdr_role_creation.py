"""Prepare, start once and reconcile an authorized new Herdr role candidate."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile

from allowlists import DEFAULT_FILE, DEFAULT_ROOT, read_active_agent_policy
from herdr_agent_creation import (LOCATION, MAC_ROUTE, ROUTE_FIELDS, check_empty, check_policy_at_start,
                                  mac_agent_process, mac_launch_argv, mac_shell_process, matching_candidates,
                                  native, non_model_args, read_agent_thread, require_private, start_agent)
from project_coordinator import Coordinator, canonical_hash, fields, identifier, text
from project_workflow_template import digest, require
from worker_library import parse_json


ROOT = Path.home() / "Library/Application Support/ProjectCoordinator"
ROLES = {"project_coordinator", "project_owner", "executor"}
TARGET_FIELDS = ("session", "agent", "cwd", *LOCATION)
SHA256 = re.compile(r"[0-9a-f]{64}")


def read_private_json(path):
    require(path.resolve() == path, "use an absolute, non-symlink owner-private path")
    with path.open("rb") as stream:
        require_private(stream)
        raw = stream.read()
    return parse_json(raw, path.name), hashlib.sha256(raw).hexdigest()


def validate_binding(binding):
    fields(binding, "schema_version role project_id task_ref unit_id creation_id authorization_ref "
           "target route agent_args selection", "owner_creation initialization_ref dispatch_ref input_sha256")
    role = binding["role"]
    require(type(binding["schema_version"]) is int and binding["schema_version"] == 1 and role in ROLES,
            "unsupported role creation binding")
    for key in ("project_id", "unit_id", "creation_id"):
        identifier(binding[key])
    text(binding["task_ref"])
    text(binding["authorization_ref"])
    role_fields = {"project_coordinator": {"initialization_ref"}, "project_owner": {"owner_creation"},
                   "executor": {"dispatch_ref", "input_sha256"}}
    require(set(binding).intersection(set.union(*role_fields.values())) == role_fields[role],
            "original role-specific authorization evidence is required")
    if role == "project_owner":
        owner = binding["owner_creation"]
        fields(owner, "expected_generation vacancy_ref release_ref")
        require(type(owner["expected_generation"]) is int and owner["expected_generation"] >= 0,
                "expected owner generation is required")
        text(owner["vacancy_ref"])
        text(owner["release_ref"])
    elif role == "project_coordinator":
        text(binding["initialization_ref"])
    else:
        text(binding["dispatch_ref"])
        require(isinstance(binding["input_sha256"], str) and SHA256.fullmatch(binding["input_sha256"]),
                "original Worker input digest is required")

    route = binding["route"]
    fields(route, " ".join(ROUTE_FIELDS))
    for value in route.values():
        require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value),
                "invalid creation route identity")
    target = binding["target"]
    fields(target, " ".join(TARGET_FIELDS), "machine session_dir")
    for key in TARGET_FIELDS:
        value = target[key]
        require(isinstance(value, str) and value and not value.startswith("-") and not any(
            ord(character) < 32 for character in value), "invalid native target")
    require(isinstance(binding["selection"], dict), "reviewed selection capabilities are required")


def adapter(binding):
    route, target = binding["route"], binding["target"]
    if route["environment_id"] == MAC_ROUTE["environment_id"]:
        require(all(route[key] == value for key, value in MAC_ROUTE.items())
                and not {"machine", "session_dir"} & target.keys() and Path(target["cwd"]).is_absolute(),
                "unsupported Mac creation route")
        return ("codex", lambda *command: native(target["session"], *command),
                mac_shell_process, mac_agent_process, None,
                lambda selected: mac_launch_argv(non_model_args(binding["agent_args"], target["cwd"],
                                                                route["provider"]), selected))
    if route["environment_id"] == "win-work" and route["worker_id"] == "win-omp":
        require(binding["role"] == "executor",
                "Win OMP creation is limited to Workers; coordinator and project owner lifecycles are Mac-only")
        import herdr_win_creation as win
        win.validate_target(target)
        return ("omp", lambda *command: win.native(target, *command), win.shell_process, win.agent_process,
                win.session_identity,
                lambda selected: win.launch_argv(binding, selected))
    raise ValueError("no native creation adapter for this route")


def role_directory(root):
    directory = root / "handoffs/herdr-project-coordinator"
    info = directory.lstat()
    require(directory.resolve() == directory and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            "use the existing owner-private role handoff directory")
    return directory


@contextmanager
def role_lock(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def save_receipt(path, receipt):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write((json.dumps(receipt, ensure_ascii=False, allow_nan=False) + "\n").encode())
            temporary.flush()
            os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def owner_details(binding, binding_sha):
    key = {field: binding[field] for field in ("project_id", "creation_id")}
    intent = {**key, **binding["owner_creation"], "authorization_ref": binding["authorization_ref"],
              "binding_sha256": binding_sha}
    return key, intent


def admit_role(binding, binding_sha, directory, root):
    role = binding["role"]
    if role == "project_coordinator":
        registration = directory / "binding.json"
        require(not (registration.exists() or registration.is_symlink()),
                "coordinator binding present; reconcile the original native role")
    if role == "project_owner":
        key, intent = owner_details(binding, binding_sha)
        coordinator = Coordinator(root / "state", read_only=True)
        view = coordinator.manage("creation_status", key)
        require(view["creation"] is None, "owner creation already registered; reconcile the original intent")
        project = coordinator.route(binding["project_id"])
        require(project["generation"] == intent["expected_generation"] and project["owner_principal"] is None,
                "current owner or generation differs; reconcile the original role")


def plan_creation(binding, binding_sha, policy_path, render):
    policy, policy_ref, library = read_active_agent_policy(policy_path, DEFAULT_ROOT)
    candidates = matching_candidates(binding["selection"], binding["route"], policy, policy_ref, library)
    require(len(candidates) == 1, "select exactly one eligible model and reasoning effort")
    candidate = candidates[0]
    model = candidate["parameters"]
    argv = render(model)
    return {
        "role": binding["role"], "project_id": binding["project_id"], "task_ref": binding["task_ref"],
        "unit_id": binding["unit_id"], "creation_id": binding["creation_id"],
        "authorization_ref": binding["authorization_ref"], "target": binding["target"],
        "route": binding["route"], "binding_sha256": binding_sha, "model_policy": policy_ref,
        "launch_model": model, "capability_ref": candidate["metadata"]["capability_ref"],
        "argv_sha256": digest(argv),
    }, argv


def observe_creation(path, receipt, host, kind, agent_process, session_identity, binding, root):
    row = None
    if binding["role"] == "project_owner":
        key, intent = owner_details(binding, receipt["binding_sha256"])
        row = Coordinator(root / "state", read_only=True).manage("creation_status", key)["creation"]
        if row is None:
            require(receipt["launch_outcome"] in {"unknown", "not_started"} and not receipt.get("thread_id"),
                    "original owner creation ledger conflicts with native launch evidence")
            if receipt["launch_outcome"] == "unknown":
                # The role lock excludes an active start. Native dispatch is
                # downstream of a committed owner creation intent.
                receipt.update(launch_outcome="not_started", reason="owner_creation_not_registered")
                save_receipt(path, receipt)
            return receipt
        require(row["input_hash"] == canonical_hash(intent), "original owner creation binding differs")
    stopped_before_start = receipt["launch_outcome"] == "not_started"
    if not stopped_before_start:
        original_thread = receipt.get("thread_id")
        thread = read_agent_thread(receipt["target"], host, kind, agent_process, receipt["argv_sha256"], original_thread,
                                   session_identity=session_identity, foreground_cwd_optional=kind == "omp")
        if thread and not original_thread:
            namespace = "codex-cli:mac" if kind == "codex" else "omp-cli:win"
            receipt.update(thread_id=thread, candidate_state="created", candidate_ref=f"{namespace}:{thread}")
            save_receipt(path, receipt)
    if row and row["state"] in {"INTENT", "UNKNOWN"}:
        outcome = ("NOT_CREATED" if stopped_before_start else
                   "CREATED" if receipt.get("candidate_ref") else "UNKNOWN")
        result = {**key, "expected_generation": intent["expected_generation"], "outcome": outcome,
                  "evidence_ref": f"file:{path}"}
        if outcome == "CREATED":
            result["candidate_ref"] = receipt["candidate_ref"]
        Coordinator(root / "state").manage("record_creation", result)
    return receipt


def operate(operation, binding_path, *, prepared_sha=None, root=ROOT, policy_path=DEFAULT_FILE, get_adapter=adapter):
    binding, binding_sha = read_private_json(binding_path)
    validate_binding(binding)
    directory = role_directory(root)
    role = binding["role"]
    # One live coordinator initialization per authority and one live Worker
    # creation per original unit. Verified zero-effect attempts keep their receipts.
    key = (role if role == "project_coordinator" else
           [role, binding["project_id"], binding["creation_id"]] if role == "project_owner" else
           [role, binding["project_id"], binding["task_ref"], binding["unit_id"]])
    stem = "native-creation-" + digest(key)
    path = directory / (stem + "-" + digest(binding["creation_id"]) + ".json")
    kind, host, shell_process, agent_process, session_identity, render = get_adapter(binding)
    with role_lock(directory):
        if path.exists() or path.is_symlink():
            receipt, _ = read_private_json(path)
            require(receipt["binding_sha256"] == binding_sha,
                    "original creation binding changed; reconcile its original receipt")
            if operation != "reconcile":
                return receipt
            return observe_creation(path, receipt, host, kind, agent_process, session_identity, binding, root)
        require(operation != "reconcile", "no original technical creation receipt")
        for previous_path in directory.glob(stem + "-*.json"):
            previous, _ = read_private_json(previous_path)
            require(previous["launch_outcome"] == "not_started" and all(
                previous[field] == binding[field] for field in ("project_id", "task_ref", "unit_id")),
                "original task unit has a launch; reconcile its original receipt")
        admit_role(binding, binding_sha, directory, root)
        plan, argv = plan_creation(binding, binding_sha, policy_path, render)
        plan_sha = digest(plan)
        if operation == "prepare":
            return {**plan, "prepared_sha256": plan_sha, "launch_outcome": "not_started"}
        require(operation == "start" and prepared_sha == plan_sha,
                "prepared creation changed; run prepare against the current binding and policy")
        for previous_path in directory.glob("native-creation-*.json"):
            previous, _ = read_private_json(previous_path)
            previous_target = previous["target"]
            if (previous["launch_outcome"] != "not_started" and not previous.get("thread_id")
                    and all(previous_target.get(key) == plan["target"].get(key) for key in ("machine", "session"))):
                require(all(previous_target[key] != plan["target"][key] for key in ("pane_id", "agent")),
                        "target has an unresolved launch; reconcile the original receipt")
        check_empty(binding["target"], host, shell_process, foreground_cwd_optional=kind == "omp")
        if role == "project_coordinator":
            admit_role(binding, binding_sha, directory, root)
        receipt = {**plan, "prepared_sha256": plan_sha, "receipt_path": str(path), "launch_outcome": "unknown",
                   "candidate_state": "unconfirmed", "thread_id": None, "effective_model": None}
        save_receipt(path, receipt)
        if role == "project_owner":
            _, intent = owner_details(binding, binding_sha)
            try:
                result = Coordinator(root / "state").manage("begin_creation", intent)
                require(result["create"], "owner creation already registered")
            except ValueError:
                receipt["launch_outcome"] = "not_started"
                receipt["reason"] = "owner_creation_not_admitted"
                save_receipt(path, receipt)
                raise
        if not check_policy_at_start(policy_path, binding["selection"], binding["route"], plan["model_policy"],
                                     plan["launch_model"]):
            receipt["launch_outcome"] = "not_started"
            receipt["reason"] = "agent_model_policy_changed"
            save_receipt(path, receipt)
            return observe_creation(path, receipt, host, kind, agent_process, session_identity, binding, root)
        if role == "project_owner":
            key, intent = owner_details(binding, binding_sha)
            view = Coordinator(root / "state", read_only=True).manage("creation_status", key)
            row = view["creation"]
            if not (row and row["state"] == "INTENT" and row["input_hash"] == canonical_hash(intent)
                    and row["generation"] == view["current_generation"] == intent["expected_generation"]):
                receipt.update(launch_outcome="not_started", reason="owner_creation_changed")
                save_receipt(path, receipt)
                return receipt
        receipt["launch_outcome"] = start_agent(binding["target"], host, kind, argv)
        save_receipt(path, receipt)
        return observe_creation(path, receipt, host, kind, agent_process, session_identity, binding, root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("prepare", "start", "reconcile"):
        command = commands.add_parser(operation)
        command.add_argument("--binding", required=True, type=Path, help="original owner-private creation binding")
        if operation == "start":
            command.add_argument("--prepared-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = operate(args.operation, args.binding.absolute(), prepared_sha=getattr(args, "prepared_sha256", None))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 1 if args.operation == "start" and result["launch_outcome"] == "not_started" else 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
