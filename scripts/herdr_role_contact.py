"""Resolve one locally appointed owner in Herdr, or prepare its original resume command.

This Mac-side helper never starts an agent, submits a prompt or records an owner reply.
"""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import uuid

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # fixed release sibling under python -I
    from project_coordinator import role_followup_page
else:
    from .project_coordinator import role_followup_page


ROOT = Path.home() / "Library/Application Support/ProjectCoordinator"
HERDR = Path.home() / ".local/bin/herdr"
CODEX = Path.home() / ".local/bin/codex"


class NativeCommandError(ValueError):
    """Native failure with a verified error code when available."""

    def __init__(self, code: str | None = None, *, not_sent: bool = False):
        super().__init__("NATIVE_LOOKUP_UNCONFIRMED")
        self.code = code
        self.not_sent = not_sent


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def private(path):
    mode = path.stat()
    require(path.resolve() == path and mode.st_uid == os.getuid() and not (mode.st_mode & 0o077),
            "PRIVATE_OWNER_PATH_REQUIRED")
    return json.loads(path.read_bytes())


def read_route(root, project):
    require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", project), "INVALID_PROJECT")
    directory = root / "handoffs/herdr-project-coordinator"
    caller = private(directory / "binding.json")
    path = directory / "owners" / (project + ".json")
    route = private(path)
    require(caller["role"] == "project_coordinator" and route["version"] == 1 and
            route["project_id"] == project and route["role"] == "project_owner" and
            route["session"] == caller["session"] and route["locator"]["pane_id"] != caller["pane"],
            "ROLE_ROUTE_MISMATCH")
    # Never accept a user-supplied database, endpoint or generation as authority.
    database = root / "state/project-coordinator.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT owner_principal, endpoint, generation, assignment_state FROM projects "
                         "WHERE project_id=?", (project,)).fetchone()
        thread = route["thread_id"]
        require(str(uuid.UUID(thread)) == thread and row == (route["owner_principal"], "codex-cli:mac:" + thread,
                                                             route["generation"], "BOUND"), "STALE_APPOINTMENT")
        db.row_factory = sqlite3.Row
        followups = role_followup_page(db, {"project_id": project, "generation": route["generation"]})
    args = route["resume_argv"]
    profile = route.get("runtime_profile", "readonly-contact-v1")
    require(profile in {"readonly-contact-v1", "project-owner-v1"}, "RESUME_POLICY_MISMATCH")
    approval, sandbox = ("on-request", "workspace-write") if profile == "project-owner-v1" else ("never", "read-only")
    require(isinstance(args, list) and all(isinstance(a, str) and a and "\0" not in a for a in args)
            and args[:7] == ["--no-daemon", "-a", approval, "-s", sandbox, "-C", route["cwd"]]
            and args[-2:] == ["resume", thread], "RESUME_POLICY_MISMATCH")
    if profile == "project-owner-v1":
        authorization = route.get("authorization_ref")
        require(isinstance(authorization, str) and authorization.strip() and "\0" not in authorization,
                "OWNER_RUNTIME_AUTHORIZATION_REQUIRED")
        # The host reviews the complete argv, including tools and writable roots.
        # Digest format: UTF-8 JSON array, ensure_ascii=False, separators=(",", ":").
        digest = hashlib.sha256(json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        require(route.get("resume_argv_sha256") == digest, "OWNER_RUNTIME_ARGV_HASH_MISMATCH")
        forbidden = {"--ask-for-approval", "--sandbox", "--profile", "--cd", "--full-auto",
                     "--dangerously-bypass-approvals-and-sandbox", "--yolo", "--"}
        require(all(a not in {"resume", "exec"} and a.split("=", 1)[0] not in forbidden
                    and not any(a.startswith(flag) for flag in ("-a", "-s", "-p", "-C"))
                    for a in args[7:-2]), "OWNER_RUNTIME_POLICY_OVERRIDE")
    return path, route, caller, followups


def _native_reply(raw):
    def unique(pairs):
        result = dict(pairs)
        require(len(result) == len(pairs), "DUPLICATE_NATIVE_REPLY_KEY")
        return result

    reply = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(reply, dict), "INVALID_NATIVE_REPLY")
    return reply


def native(session, *command):
    try:
        process = subprocess.run([str(HERDR), "--session", session, *command], capture_output=True, timeout=8)
        process.check_returncode()
    except subprocess.CalledProcessError as error:
        request_id = code = None
        # Herdr 0.9.5 writes the error envelope to stderr and exits 1.
        # https://github.com/herdrdev/herdr/blob/2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01/src/cli.rs#L745-L753
        if error.returncode == 1 and not error.stdout:
            try:
                reply = _native_reply(error.stderr)
            except (ValueError, RecursionError):
                reply = {}
            body = reply.get("error")
            if ("result" not in reply and isinstance(reply.get("id"), str) and reply["id"]
                    and isinstance(body, dict) and isinstance(body.get("code"), str) and body["code"]
                    and isinstance(body.get("message"), str)):
                request_id, code = reply["id"], body["code"]
        # This prompt rejection is generated before any terminal input is queued.
        # https://github.com/herdrdev/herdr/blob/2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01/src/app/api/agents.rs#L139-L177
        not_sent = (command[:2] == ("agent", "prompt") and request_id == "cli:agent:prompt"
                    and code == "agent_blocked")
        raise NativeCommandError(code, not_sent=not_sent) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise NativeCommandError() from error

    try:
        reply = _native_reply(process.stdout)
        require("error" not in reply and isinstance(reply.get("result"), dict), "INVALID_NATIVE_REPLY")
    except (ValueError, RecursionError) as error:
        raise NativeCommandError() from error
    return reply["result"]


def same_pane(observed, locator, cwd):
    return (all(observed.get(k) == v for k, v in locator.items())
            and observed.get("cwd") == observed.get("foreground_cwd") == cwd)


def check_caller(caller, query):
    require(os.environ.get("HERDR_ENV") == "1" and os.environ.get("HERDR_SESSION") == caller["session"]
            and os.environ.get("HERDR_PANE_ID") == caller["pane"], "NATIVE_COORDINATOR_REQUIRED")
    agent = query(caller["session"], "agent", "get", caller["agent"])["agent"]
    require(agent.get("name") == caller["agent"] and agent.get("pane_id") == caller["pane"]
            and agent.get("workspace_id") == caller["workspace"] and agent.get("tab_id") == caller["tab"]
            and agent.get("agent") == "codex" and agent.get("agent_session") == caller["agent_session"]
            and caller["agent_session"].get("kind") == "id", "COORDINATOR_IDENTITY_MISMATCH")


def check_owner(route, locator, owner, query, *, relocated=False):
    session = route["session"]
    require(owner.get("agent") == "codex" and owner.get("name") == route["agent"]
            and same_pane(owner, locator, route["cwd"]), "OWNER_TARGET_MISMATCH")
    identity = owner.get("agent_session")
    if identity is not None:
        require(identity.get("agent") == "codex" and identity.get("kind") == "id"
                and identity.get("value") == route["thread_id"], "OWNER_THREAD_MISMATCH")
        method = "HERDR_AGENT_SESSION"
    if identity is None or relocated or route.get("runtime_profile") == "project-owner-v1":
        processes = query(session, "pane", "process-info", "--pane", locator["pane_id"])["process_info"]
        require(processes["pane_id"] == locator["pane_id"], "OWNER_PANE_MISMATCH")
        group = processes["foreground_process_group_id"]
        matching = [p for p in processes["foreground_processes"] if p.get("pid") == group and
                    p.get("cwd") == route["cwd"] and p.get("argv") and
                    Path(p["argv"][0]).name == "codex" and p["argv"][1:] == route["resume_argv"]]
        require(len(matching) == 1, "OWNER_RESUME_ARGV_UNCONFIRMED")
        method = "HERDR_TERMINAL_AND_RESUME_ARGV" if identity is None else "HERDR_AGENT_SESSION_AND_RESUME_ARGV"
    ready = (owner.get("agent_status") in ("idle", "done") and owner.get("interactive_ready") is True
             and owner.get("launch_pending", False) is False)
    return {"state": "READY_FOR_CONTACT" if ready else "OWNER_NOT_READY", "identity_evidence": method}


def operate(action, project, *, replacement=None, root=ROOT, query=native):
    path, route, caller, followups = read_route(root, project)
    locator = route["locator"]
    result = {k: route[k] for k in ("project_id", "generation", "owner_principal", "thread_id", "cwd",
                                   "session", "agent", "last_confirmed_contact")}
    result["locator"] = locator
    result["role_followups"] = followups
    result["runtime_profile"] = route.get("runtime_profile", "readonly-contact-v1")
    if route.get("runtime_profile") == "project-owner-v1":
        result["authorization_ref"] = route["authorization_ref"]
        result["resume_argv_sha256"] = route["resume_argv_sha256"]
    if action == "show":
        return dict(result, state="REGISTERED_ROUTE_ONLY")
    check_caller(caller, query)
    owner = None
    if action in ("resolve", "bind-replacement"):
        owner = query(route["session"], "agent", "get", route["agent"])["agent"]
    if action == "resolve":
        if owner.get("pane_id") == locator["pane_id"]:
            return dict(result, **check_owner(route, locator, owner, query))
        # A sandboxed coordinator can use a recovered target immediately. Its
        # cached location can later be persisted by a trusted local recorder.
        replacement = owner.get("pane_id")
        require(isinstance(replacement, str) and replacement, "OWNER_TARGET_MISMATCH")

    # An explicit, successful native enumeration distinguishes an absent pane from
    # a timed-out get. Only inspect the registered session and caller workspace.
    panes = query(route["session"], "pane", "list", "--workspace", caller["workspace"])["panes"]
    chosen = replacement or locator["pane_id"]
    if replacement:
        require(chosen not in (caller["pane"], locator["pane_id"]) and
                locator["workspace_id"] == caller["workspace"] and
                all(p["pane_id"] != locator["pane_id"] for p in panes), "PRIOR_PANE_NOT_RELEASED")
    pane = next((p for p in panes if p.get("pane_id") == chosen), None)
    require(pane is not None, "TARGET_PANE_NOT_FOUND_IN_REGISTERED_WORKSPACE")
    new = {key: pane[key] for key in ("workspace_id", "tab_id", "pane_id", "terminal_id")}
    require(all(isinstance(v, str) and v for v in new.values()) and new["workspace_id"] == caller["workspace"]
            and same_pane(pane, new if replacement else locator, route["cwd"]), "PANE_LOCATION_CHANGED")
    result["locator"] = new
    if action in ("resolve", "bind-replacement"):
        require(replacement, "REPLACEMENT_REQUIRED")
        proof = check_owner(route, new, owner, query, relocated=True)
        if action == "resolve":
            return dict(result, **proof, locator_updated=False, locator_source="VERIFIED_RELOCATION")
        require(proof["state"] == "READY_FOR_CONTACT", "REPLACEMENT_NOT_READY")
        # Serialize locator changes only; a concurrent authority change fences the
        # route on every later use. Never update the appointment or receipt itself.
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            require(read_route(root, project)[1] == route, "ROUTE_CHANGED")
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
                temp.write((json.dumps(dict(route, locator=new), ensure_ascii=False, indent=2) + "\n").encode())
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp.name, path)
        finally:
            os.close(directory_fd)
        return dict(result, **proof, locator_updated=True)
    require(action == "prepare-resume", "INVALID_ACTION")
    agents = query(route["session"], "agent", "list")["agents"]
    require(all(a.get("name") != route["agent"] and a.get("pane_id") != chosen and
                (a.get("agent_session") or {}).get("value") != route["thread_id"] for a in agents),
            "EXISTING_AGENT_OR_UNCERTAIN_LAUNCH")
    processes = query(route["session"], "pane", "process-info", "--pane", chosen)["process_info"]
    shell = processes["shell_pid"]
    foreground = processes["foreground_processes"]
    require(not pane.get("launch_pending") and processes["pane_id"] == chosen and type(shell) is int and shell > 0
            and processes["foreground_process_group_id"] == shell and len(foreground) == 1
            and foreground[0].get("pid") == shell and foreground[0].get("cwd") == route["cwd"]
            and Path(foreground[0]["argv"][0]).name.lstrip("-") in {"zsh", "bash", "sh"}, "SHELL_NOT_CONFIRMED")
    # The approved recovery uses the default, pinned home. A new project
    # config can introduce undisabled tools despite a matching global file.
    home_config = Path.home() / ".codex/config.toml"
    cwd = Path(route["cwd"])
    require(not any((folder / ".codex/config.toml").exists() and folder / ".codex/config.toml" != home_config
                    for folder in (cwd, *cwd.parents)), "REVIEWED_RUNTIME_CHANGED")
    for name, file in (("codex_sha256", CODEX), ("config_sha256", home_config)):
        with file.open("rb") as stream:
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        require(digest.hexdigest() == route[name], "REVIEWED_RUNTIME_CHANGED")
    argv = [str(HERDR), "--session", route["session"], "agent", "start", route["agent"],
            "--kind", "codex", "--pane", chosen, "--timeout", "300000", "--", *route["resume_argv"]]
    return dict(result, state="RESUME_PREPARED_NOT_STARTED", argv=argv, command=shlex.join(argv),
                preconditions=["Original controller and any other host have handed back the thread",
                               "Reconcile pending starts, prompts and original request before one start"],
                after="Verify the native target and the corresponding owner turn; timeout is UNKNOWN")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("show", "resolve", "prepare-resume", "bind-replacement"))
    parser.add_argument("project")
    parser.add_argument("--replacement-pane")
    args = parser.parse_args()
    try:
        require(not args.replacement_pane or args.action in ("bind-replacement", "prepare-resume"),
                "UNEXPECTED_REPLACEMENT")
        print(json.dumps(operate(args.action, args.project, replacement=args.replacement_pane), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
