#!/usr/bin/env python3
"""Explicit, local Herdr execution intents. No mailbox consumer or business acceptance.

Native JSON contract: herdrdev/herdr@ee5bfb5041bb3e4a49d591c72308aae3c81ecdde
(Cargo version 0.9.5), docs/next/api/herdr-api.schema.json. AgentInfo.agent
is the recognized kind; AgentInfo.name is the live agent name.
"""

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import worker_library


MAX_BYTES = 1024 * 1024
MAX_DATABASE_BYTES = 32 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
AGENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
FREE_STATES = {"RELEASED", "CANCELLED", "EXPIRED"}
NATIVE_CODES = {"agent_not_found", "pane_not_found", "agent_not_ready", "agent_blocked",
                "agent_busy", "agent_prompt_stalled", "timeout", "session_not_found",
                "invalid_params", "invalid_request", "method_not_found", "internal_error"}


class ExecutorError(ValueError):
    """A safe, stable code; external exception messages are never forwarded."""

    def __init__(self, code, *, exit_code=None):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def require(condition, code):
    if not condition:
        raise ExecutorError(code)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise ExecutorError("INVALID_JSON") from error


def sha256(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def control(value, code="INVALID_CONTROL", maximum=2048):
    require(isinstance(value, str) and 0 < len(value) <= maximum
            and value == value.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and "<" not in value and ">" not in value
            and not re.search(r"(?:^|[_:\s-])(unknown|replace|placeholder|example_only)(?:$|[_:\s-])",
                              value, re.IGNORECASE), code)
    return value


def identifier(value):
    control(value)
    require(IDENTIFIER.fullmatch(value) is not None
            and value.casefold() not in {"focused", "current", "default", ".", ".."}, "INVALID_IDENTIFIER")
    return value


def absolute_path(value, *, existing=False, directory=False):
    control(value, "INVALID_PATH", 4096)
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "INVALID_PATH")
    for part in (path, *path.parents):
        require(not part.is_symlink(), "SYMLINK_PATH")
    resolved = path.resolve()
    if existing:
        require(resolved.is_dir() if directory else resolved.is_file(), "PATH_NOT_FOUND")
    return resolved


def read_json(path, *, private=False):
    path = absolute_path(str(path), existing=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES, "INPUT_TOO_LARGE")
        if private:
            require(stat.S_IMODE(info.st_mode) == 0o600, "POLICY_NOT_PRIVATE")
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, "INPUT_TOO_LARGE")
    return parse_json(data)


def parse_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def reject_constant(_):
        raise ExecutorError("INVALID_JSON")
    try:
        result = json.loads(data, object_pairs_hook=unique,
                            parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, TypeError, RecursionError) as error:
        raise ExecutorError("INVALID_JSON") from error
    require(isinstance(result, dict), "INVALID_JSON_OBJECT")
    canonical(result)
    return result


def utc(value):
    control(value, "INVALID_TIMESTAMP")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExecutorError("INVALID_TIMESTAMP") from error
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, "INVALID_TIMESTAMP")
    return parsed.astimezone(timezone.utc)


def tracking(value, repository):
    require(isinstance(value, dict)
            and {"repository", "project_url", "item_id", "iteration"} <= value.keys()
            and set(value) - {"repository", "project_url", "item_id", "iteration"}
                in ({"issue_url"}, {"draft_id"}), "INVALID_TRACKING")
    require(value["repository"] == repository, "TRACKING_REPOSITORY_MISMATCH")
    def github_url(url):
        control(url, "INVALID_TRACKING")
        parsed = urlsplit(url)
        require(parsed.scheme == "https" and parsed.netloc == "github.com"
                and not parsed.query and not parsed.fragment, "INVALID_TRACKING")
        return parsed.path
    project = re.fullmatch(r"/orgs/CF-FE/projects/([1-9][0-9]*)", github_url(value["project_url"]))
    require(project is not None and int(project[1]) < 2**63, "INVALID_TRACKING")
    require(type(value["item_id"]) is int and 0 < value["item_id"] < 2**63, "INVALID_TRACKING")
    if "issue_url" in value:
        issue = re.fullmatch("/" + re.escape(repository) + r"/issues/([1-9][0-9]*)",
                             github_url(value["issue_url"]))
        require(issue is not None and int(issue[1]) < 2**63, "INVALID_TRACKING")
    else:
        require(isinstance(value["draft_id"], str)
                and re.fullmatch(r"DI_[A-Za-z0-9_-]{1,125}", value["draft_id"], re.ASCII), "INVALID_TRACKING")
    iteration = value["iteration"]
    require(isinstance(iteration, dict), "INVALID_TRACKING")
    if set(iteration) == {"field_id", "iteration_id"}:
        require(type(iteration["field_id"]) is int and 0 < iteration["field_id"] < 2**63,
                "INVALID_TRACKING")
        control(iteration["iteration_id"], "INVALID_TRACKING", 128)
    else:
        require(set(iteration) == {"state", "reason"} and iteration["state"] == "UNSCHEDULED"
                and isinstance(iteration["reason"], str) and bool(iteration["reason"].strip())
                and len(iteration["reason"]) <= 512, "INVALID_TRACKING")


class Executor:
    """One private journal and lock, with injected transport only for offline tests."""

    def __init__(self, state_dir, policy, catalog_root=worker_library.DEFAULT_ROOT,
                 transport=None, clock=None):
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.policy = (deepcopy(policy) if isinstance(policy, dict) and transport is not None
                       else read_json(policy, private=True))
        p = self.policy
        require(set(p) in ({"schema_version", "repository", "project_id", "herdr_binary", "allowed_roots", "targets"},
                            {"schema_version", "repository", "project_id", "herdr_binary", "allowed_roots", "targets", "max_database_bytes"})
                and type(p.get("schema_version")) is int and p["schema_version"] == 1, "INVALID_POLICY")
        require(p["repository"] == "CF-FE/herdr-4-codexcloud"
                and p["project_id"] == "herdr-4-codexcloud", "POLICY_PROJECT_MISMATCH")
        self.binary = absolute_path(p["herdr_binary"], existing=transport is None)
        if transport is None:
            require(os.access(self.binary, os.X_OK), "BINARY_NOT_EXECUTABLE")
        require(isinstance(p["allowed_roots"], list) and p["allowed_roots"], "INVALID_POLICY")
        self.roots = [absolute_path(v, existing=True, directory=True) for v in p["allowed_roots"]]
        require(isinstance(p["targets"], list), "INVALID_POLICY")
        target_keys = {"session", "pane_id", "agent_name", "cwd", "worker_id", "backend_id",
                       "agent_kind", "provider", "model", "auth_ref", "permissions_ref"}
        for target in p["targets"]:
            require(isinstance(target, dict) and set(target) == target_keys, "INVALID_POLICY_TARGET")
            for key in target_keys - {"cwd"}:
                control(target[key], "INVALID_POLICY_TARGET")
            identifier(target["session"])
            identifier(target["pane_id"])
            require(AGENT_NAME.fullmatch(target["agent_name"]) is not None, "INVALID_POLICY_TARGET")
            cwd = absolute_path(target["cwd"], existing=True, directory=True)
            require(any(cwd.is_relative_to(root) for root in self.roots), "PATH_OUTSIDE_ROOTS")
        self.max_database_bytes = p.get("max_database_bytes", MAX_DATABASE_BYTES)
        require(type(self.max_database_bytes) is int and self.max_database_bytes >= 131072,
                "INVALID_DATABASE_BUDGET")
        self.catalog_root = absolute_path(str(catalog_root), existing=True, directory=True)
        # Bound every source member before worker_library reads role resources.
        for count, member in enumerate(self.catalog_root.rglob("*"), 1):
            require(count <= 256, "CATALOG_TOO_LARGE")
            require(not member.is_symlink(), "SYMLINK_PATH")
            if member.is_file():
                require(member.stat().st_size <= MAX_BYTES, "INPUT_TOO_LARGE")
        try:
            self.library = worker_library.load_library(self.catalog_root)
        except worker_library.LibraryError as error:
            raise ExecutorError("INVALID_CATALOG") from error
        self.state_dir = absolute_path(str(state_dir))
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        self.path = self.state_dir / "executor.sqlite3"
        require(not self.path.is_symlink(), "SYMLINK_PATH")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self._lock(), self._transaction() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS intents (intent_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, envelope TEXT NOT NULL, state TEXT NOT NULL, rpc_used INTEGER NOT NULL DEFAULT 0, native TEXT NOT NULL DEFAULT '{}', artifacts TEXT NOT NULL DEFAULT '[]', detail TEXT NOT NULL DEFAULT '', hand_back TEXT NOT NULL DEFAULT '{}', cancel_requested INTEGER NOT NULL DEFAULT 0, cancel_requested_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            connection.execute("UPDATE intents SET state='UNKNOWN',detail='INTERRUPTED_DISPATCH',updated_at=? WHERE state='DISPATCHING'", (self._now().isoformat(),))

    def _now(self):
        value = self.clock()
        require(isinstance(value, datetime) and value.tzinfo is not None, "INVALID_CLOCK")
        return value.astimezone(timezone.utc)

    @contextmanager
    def _lock(self):
        path = self.state_dir / "executor.lock"
        require(not path.is_symlink(), "SYMLINK_PATH")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "a") as stream:
            os.fchmod(stream.fileno(), 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ExecutorError("EXECUTOR_BUSY") from error
            yield

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=1)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            size = connection.execute("PRAGMA page_size").fetchone()[0]
            require(connection.execute("PRAGMA page_count").fetchone()[0] <= self.max_database_bytes // size,
                    "DATABASE_BUDGET_EXCEEDED")
            connection.execute(f"PRAGMA max_page_count={self.max_database_bytes // size}")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _validate(self, envelope):
        require(isinstance(envelope, dict) and set(envelope) == {"schema_version", "intent_id", "job", "draft", "target", "authority", "budget", "outputs"}, "INVALID_ENVELOPE")
        require(type(envelope["schema_version"]) is int and envelope["schema_version"] == 1,
                "INVALID_ENVELOPE")
        require(len(canonical(envelope)) <= MAX_BYTES, "INPUT_TOO_LARGE")
        identifier(envelope["intent_id"])
        job, target, authority, budget, outputs = (envelope[k] for k in ("job", "target", "authority", "budget", "outputs"))
        require(isinstance(job, dict) and set(job) == {"job_id", "payload_hash", "payload"}, "INVALID_JOB")
        identifier(job["job_id"])
        require(isinstance(job["payload_hash"], str) and DIGEST.fullmatch(job["payload_hash"])
                and job["payload_hash"] == sha256(job["payload"]), "PAYLOAD_HASH_MISMATCH")
        payload = job["payload"]
        require(isinstance(payload, dict) and type(payload.get("schema_version")) is int
                and payload["schema_version"] == 1, "INVALID_JOB")
        for key in ("origin_task_id", "sender", "recipient", "report_route"):
            control(payload.get(key), "INVALID_JOB")
        require(isinstance(payload.get("content"), str) and isinstance(payload.get("target"), dict)
                and payload["target"], "INVALID_JOB")
        tracking(payload.get("tracking"), self.policy["repository"])
        require(isinstance(target, dict) and set(target) == {"session", "pane_id", "agent_name", "cwd"}, "INVALID_TARGET")
        require(payload["target"] == target, "PAYLOAD_TARGET_MISMATCH")
        identifier(target["session"])
        identifier(target["pane_id"])
        require(isinstance(target["agent_name"], str) and AGENT_NAME.fullmatch(target["agent_name"]), "INVALID_TARGET")
        cwd = absolute_path(target["cwd"], existing=True, directory=True)
        require(str(cwd) == target["cwd"] and any(cwd.is_relative_to(root) for root in self.roots),
                "PATH_OUTSIDE_ROOTS")
        allowed = [row for row in self.policy["targets"] if all(row[key] == target[key] for key in target)]
        require(len(allowed) == 1, "TARGET_NOT_ALLOWED")
        selected = allowed[0]
        require(target["pane_id"] != os.environ.get("HERDR_PANE_ID"), "SELF_TARGET_FORBIDDEN")
        require(isinstance(authority, dict) and set(authority) == {"task_ref", "lead", "writer", "tracking_read_ref", "owned_paths", "start_before", "deadline"}, "INVALID_AUTHORITY")
        for key in ("task_ref", "lead", "writer", "tracking_read_ref"):
            control(authority[key], "INVALID_AUTHORITY")
        require(authority["task_ref"] == payload["origin_task_id"], "TASK_ID_MISMATCH")
        start, deadline = utc(authority["start_before"]), utc(authority["deadline"])
        require(start <= deadline, "INVALID_DEADLINE")
        owned = authority["owned_paths"]
        require(isinstance(owned, list) and owned and len(owned) <= 100, "INVALID_OWNED_PATHS")
        owned_paths = [absolute_path(value, existing=True, directory=True) for value in owned]
        require(len(set(owned_paths)) == len(owned_paths)
                and all(str(path) == value and path.is_relative_to(cwd) for path, value in zip(owned_paths, owned)),
                "PATH_OUTSIDE_PROJECT")
        require(isinstance(budget, dict) and set(budget) == {"model_turns", "rpc_calls", "timeout_seconds"}
                and type(budget["model_turns"]) is int and budget["model_turns"] == 1
                and type(budget["rpc_calls"]) is int and 1 <= budget["rpc_calls"] <= 100
                and type(budget["timeout_seconds"]) is int and 1 <= budget["timeout_seconds"] <= 60,
                "INVALID_BUDGET")
        require(isinstance(outputs, dict) and set(outputs) == {"manifest_path", "paths"}
                and isinstance(outputs["paths"], list) and outputs["paths"] and len(outputs["paths"]) <= 100,
                "INVALID_OUTPUTS")
        paths = [absolute_path(value) for value in [outputs["manifest_path"], *outputs["paths"]]]
        require(len(paths) == len(set(paths)) and all(any(path.is_relative_to(root) for root in owned_paths) for path in paths)
                and all(str(path) == value for path, value in zip(paths, [outputs["manifest_path"], *outputs["paths"]])),
                "OUTPUT_PATH_OUTSIDE_OWNERSHIP")
        draft = envelope["draft"]
        require(isinstance(draft, dict) and isinstance(draft.get("worker"), dict)
                and isinstance(draft.get("backend"), dict) and isinstance(draft.get("role"), dict)
                and isinstance(draft.get("task"), dict), "INVALID_DRAFT")
        task = draft["task"]
        require("schema_version" not in task or (type(task["schema_version"]) is int
                and task["schema_version"] == 1), "INVALID_DRAFT_TASK")
        require(task.get("task_id") == authority["task_ref"]
                and task.get("project_id") == self.policy["project_id"], "TASK_ID_MISMATCH")
        require(task.get("owned_paths") == owned and task.get("output_refs") == outputs["paths"], "DRAFT_PATH_MISMATCH")
        worker = self.library["worker_map"].get(selected["worker_id"])
        require(worker is not None and worker["host"] == "mac-work" and worker["runtime"] == "herdr-agent"
                and worker["lifecycle"] == "active" and worker["agent_kind"] == selected["agent_kind"],
                "WORKER_NOT_ALLOWED")
        require(draft["worker"] == worker, "CATALOG_MISMATCH")
        backend = next((row for row in worker["backends"] if row["id"] == selected["backend_id"]), None)
        require(backend is not None and draft["backend"] == backend, "CATALOG_MISMATCH")
        for key in ("provider", "model"):
            require(backend[key] == "UNKNOWN" or backend[key] == selected[key], "BACKEND_POLICY_MISMATCH")
        project = draft["role"].get("project")
        project_id = project.get("id") if isinstance(project, dict) else None
        try:
            role = worker_library.render_role(self.library, draft["role"].get("id"), project_id, render=True)
            require(role["dispatchable"] and role == draft["role"], "ROLE_MISMATCH")
            # prepare validates task structure without using a task file or executing its templates.
            require(bool(worker_library.candidates(self.library, role, host="mac-work", worker_id=worker["id"], backend_id=backend["id"])), "CAPABILITY_MISMATCH")
            require(isinstance(task.get("goal"), str) and bool(task["goal"].strip()), "INVALID_DRAFT_TASK")
            for key in ("authorization_ref", "dispatch_owner", "report_to", "report_route"):
                control(task.get(key), "INVALID_DRAFT_TASK")
            for key in ("owned_paths", "input_refs", "acceptance_criteria", "output_refs"):
                require(isinstance(task.get(key), list) and task[key]
                        and all(isinstance(x, str) and bool(x.strip()) for x in task[key]), "INVALID_DRAFT_TASK")
            expected = worker_library.prepare(self.library, SimpleNamespace(worker=worker["id"], backend=backend["id"], project=project_id, task_file=None), role)
        except worker_library.LibraryError as error:
            raise ExecutorError("INVALID_DRAFT") from error
        # The existing prepare output depends only on whether a task was supplied.
        expected["task"] = deepcopy(task)
        expected["required_bindings"].remove("task_id_project_goal_inputs_outputs_acceptance_and_authority")
        require(expected == draft, "DRAFT_MISMATCH")
        require(task["dispatch_owner"] == authority["lead"] and task["report_route"] == payload["report_route"],
                "TASK_AUTHORITY_MISMATCH")
        return deepcopy(envelope)

    def _row(self, intent_id):
        identifier(intent_id)
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            require(row is not None, "INTENT_NOT_FOUND")
            return self._verified_row(row)

    @staticmethod
    def _verified_row(row):
        # Detect accidental drift before saved data becomes a native target or path.
        # This unkeyed checksum is not protection against an actor who can rewrite both.
        try:
            envelope = parse_json(row["envelope"])
            valid = (sha256(envelope) == row["input_hash"]
                     and envelope.get("intent_id") == row["intent_id"])
        except (ExecutorError, TypeError, KeyError):
            valid = False
        require(valid, "JOURNAL_INTEGRITY_ERROR")
        return dict(row)

    def _summary(self, row):
        envelope = json.loads(row["envelope"])
        result = {"schema_version": 1, "intent_id": row["intent_id"], "job_id": envelope["job"]["job_id"],
                  "payload_hash": envelope["job"]["payload_hash"], "input_hash": row["input_hash"],
                  "state": row["state"], "rpc_used": row["rpc_used"], "rpc_limit": envelope["budget"]["rpc_calls"],
                  "cancel_requested": bool(row["cancel_requested"]),
                  "cancel_requested_at": row["cancel_requested_at"] or None,
                  "native": json.loads(row["native"]), "artifacts": json.loads(row["artifacts"]),
                  "detail": row["detail"], "next_owner": "PROJECT_LEAD", "business_accepted": False}
        if row["hand_back"] != "{}":
            result["hand_back"] = json.loads(row["hand_back"])
        return result

    def _update(self, intent_id, **changes):
        require(set(changes) <= {"state", "native", "artifacts", "detail", "hand_back",
                                "cancel_requested", "cancel_requested_at"}, "INTERNAL_ERROR")
        changes["updated_at"] = self._now().isoformat()
        with self._transaction() as connection:
            connection.execute("UPDATE intents SET " + ",".join(key + "=?" for key in changes) + " WHERE intent_id=?",
                               [*changes.values(), intent_id])
        return self._row(intent_id)

    def _register(self, envelope):
        intent_id, digest = envelope["intent_id"], sha256(envelope)
        with self._transaction() as connection:
            old = connection.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if old is not None:
                old = self._verified_row(old)
                require(old["input_hash"] == digest, "INTENT_CONFLICT")
                return old, False
            target = envelope["target"]
            paths = [Path(value) for value in envelope["authority"]["owned_paths"]]
            for row in connection.execute("SELECT * FROM intents"):
                row = self._verified_row(row)
                if row["state"] in FREE_STATES:
                    continue
                other = json.loads(row["envelope"])
                same_target = (other["target"]["session"] == target["session"] and
                               (other["target"]["pane_id"] == target["pane_id"] or other["target"]["agent_name"] == target["agent_name"]))
                overlaps = any(a.is_relative_to(b) or b.is_relative_to(a) for a in paths
                               for b in map(Path, other["authority"]["owned_paths"]))
                require(not same_target and not overlaps, "RESOURCE_CONFLICT")
            now = self._now().isoformat()
            connection.execute("INSERT INTO intents(intent_id,input_hash,envelope,state,created_at,updated_at) VALUES (?,?,?,'PREPARED',?,?)",
                               (intent_id, digest, canonical(envelope).decode(), now, now))
            row = connection.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            return dict(row), True

    def _rpc(self, row, command, operation_deadline, *, prompt=False, reconcile=False):
        envelope = json.loads(row["envelope"])
        require(any(all(allowed[key] == value for key, value in envelope["target"].items())
                    for allowed in self.policy["targets"]), "TARGET_NOT_ALLOWED")
        if self.transport is None:
            require(os.environ.get("HERDR_ENV") == "1", "HERDR_CONTEXT_REQUIRED")
            require(envelope["target"]["pane_id"] != os.environ.get("HERDR_PANE_ID"), "SELF_TARGET_FORBIDDEN")
        remaining = operation_deadline - time.monotonic()
        if not reconcile:
            remaining = min(remaining, (utc(envelope["authority"]["deadline"]) - self._now()).total_seconds())
        require(remaining > 0, "DEADLINE_EXCEEDED")
        timeout = min(60, envelope["budget"]["timeout_seconds"], remaining)
        with self._transaction() as connection:
            fresh = connection.execute("SELECT rpc_used FROM intents WHERE intent_id=?", (row["intent_id"],)).fetchone()
            require(fresh[0] < envelope["budget"]["rpc_calls"], "RPC_BUDGET_EXHAUSTED")
            connection.execute("UPDATE intents SET rpc_used=rpc_used+1,updated_at=? WHERE intent_id=?",
                               (self._now().isoformat(), row["intent_id"]))
            if prompt:
                connection.execute("UPDATE intents SET state='DISPATCHING',detail='' WHERE intent_id=?", (row["intent_id"],))
        argv = [str(self.binary), "--session", envelope["target"]["session"], *command]
        try:
            if self.transport is None:
                response = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=timeout)
            else:
                response = self.transport(argv, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise ExecutorError("NATIVE_TIMEOUT") from error
        except OSError as error:
            raise ExecutorError("NATIVE_OS_ERROR") from error
        require(type(response.returncode) is int and isinstance(response.stdout, str)
                and isinstance(response.stderr, str), "NATIVE_PROTOCOL_ERROR")
        require(len(response.stdout.encode("utf-8")) <= MAX_BYTES and len(response.stderr.encode("utf-8")) <= MAX_BYTES,
                "NATIVE_OUTPUT_TOO_LARGE")
        if response.returncode != 0:
            code = "NATIVE_ERROR"
            try:
                reported = parse_json(response.stderr).get("error", {}).get("code")
                if reported in NATIVE_CODES:
                    code = "NATIVE_" + reported.upper()
            except (ExecutorError, AttributeError, TypeError):
                pass
            raise ExecutorError(code, exit_code=response.returncode if -255 <= response.returncode <= 255 else None)
        result = parse_json(response.stdout).get("result")
        require(isinstance(result, dict), "NATIVE_PROTOCOL_ERROR")
        return result

    def _agent(self, result, envelope, *, ready=False, response_type="agent_info"):
        require(result.get("type") == response_type and isinstance(result.get("agent"), dict), "NATIVE_PROTOCOL_ERROR")
        agent, target = result["agent"], envelope["target"]
        policy = next(row for row in self.policy["targets"] if all(row[k] == target[k] for k in target))
        require(agent.get("name") == target["agent_name"] and agent.get("pane_id") == target["pane_id"]
                and agent.get("agent") == policy["agent_kind"] and agent.get("cwd") == target["cwd"],
                "NATIVE_TARGET_MISMATCH")
        require(agent.get("foreground_cwd") in (None, target["cwd"]), "NATIVE_TARGET_MISMATCH")
        require(agent.get("agent_status") in {"idle", "working", "blocked", "done", "unknown"}, "NATIVE_PROTOCOL_ERROR")
        for key in ("terminal_id", "workspace_id", "tab_id"):
            require(isinstance(agent.get(key), str) and bool(agent[key]), "NATIVE_PROTOCOL_ERROR")
        # Pinned AgentInfo uses serde(default, skip_serializing_if = is_false).
        pending = agent.get("launch_pending", False)
        require(type(pending) is bool, "NATIVE_PROTOCOL_ERROR")
        if ready:
            require(agent["agent_status"] in {"idle", "done"} and agent.get("interactive_ready") is True
                    and not pending, "NATIVE_AGENT_NOT_READY")
        observation = {key: agent.get(key) for key in ("name", "pane_id", "agent", "cwd", "foreground_cwd", "agent_status",
                                               "interactive_ready", "terminal_id", "workspace_id", "tab_id")}
        observation["launch_pending"] = pending
        return observation

    def _preflight(self, row, operation_deadline):
        envelope = json.loads(row["envelope"])
        now = self._now()
        if now >= utc(envelope["authority"]["start_before"]) or now >= utc(envelope["authority"]["deadline"]):
            return self._update(row["intent_id"], state="EXPIRED", detail="START_DEADLINE_EXCEEDED")
        for value in [envelope["outputs"]["manifest_path"], *envelope["outputs"]["paths"]]:
            path = absolute_path(value)
            require(not path.exists(), "OUTPUT_ALREADY_EXISTS")
        agent = self._agent(self._rpc(row, ["agent", "get", envelope["target"]["agent_name"]], operation_deadline), envelope, ready=True)
        result = self._rpc(row, ["pane", "get", envelope["target"]["pane_id"]], operation_deadline)
        require(result.get("type") == "pane_info" and isinstance(result.get("pane"), dict), "NATIVE_PROTOCOL_ERROR")
        pane = result["pane"]
        for key in ("pane_id", "agent", "cwd", "terminal_id", "workspace_id", "tab_id"):
            require(pane.get(key) == agent[key], "NATIVE_TARGET_MISMATCH")
        require(pane.get("foreground_cwd") in (None, envelope["target"]["cwd"])
                and pane.get("agent_status") in {"idle", "done"}, "NATIVE_AGENT_NOT_READY")
        return self._update(row["intent_id"], native=canonical({"agent": agent, "observed_at": self._now().isoformat(),
                            "provider_model_evidence": "LEAD_POLICY_ASSERTION"}).decode())

    def preflight(self, envelope):
        envelope = self._validate(envelope)
        with self._lock():
            row, added = self._register(envelope)
            if not added:
                return self._summary(row)
            try:
                row = self._preflight(row, time.monotonic() + envelope["budget"]["timeout_seconds"])
            except ExecutorError as error:
                row = self._update(row["intent_id"], state="FAILED_NOT_DISPATCHED", detail=error.code)
            except Exception:
                row = self._update(row["intent_id"], state="FAILED_NOT_DISPATCHED", detail="NATIVE_UNKNOWN_ERROR")
            return self._summary(row)

    def dispatch(self, envelope, approve_sha256):
        envelope = self._validate(envelope)
        require(isinstance(approve_sha256, str) and DIGEST.fullmatch(approve_sha256)
                and approve_sha256 == sha256(envelope), "APPROVAL_HASH_MISMATCH")
        with self._lock():
            row, _ = self._register(envelope)
            if row["state"] != "PREPARED":
                return self._summary(row)
            operation_deadline = time.monotonic() + envelope["budget"]["timeout_seconds"]
            try:
                row = self._preflight(row, operation_deadline)
                if row["state"] != "PREPARED":
                    return self._summary(row)
                contract = {"intent_id": envelope["intent_id"], "job_id": envelope["job"]["job_id"],
                            "payload_hash": envelope["job"]["payload_hash"], "input_hash": row["input_hash"],
                            "task": envelope["draft"]["task"],
                            "owned_paths": envelope["authority"]["owned_paths"], "outputs": envelope["outputs"],
                            "deadline": envelope["authority"]["deadline"], "model_turns": 1,
                            "manifest_schema": {"schema_version": 1, "intent_id": envelope["intent_id"],
                                "job_id": envelope["job"]["job_id"], "payload_hash": envelope["job"]["payload_hash"],
                                "status": "REPORTED_COMPLETED or REPORTED_FAILED", "files": [{"path": "absolute planned path", "sha256": "actual lowercase SHA256", "bytes": "actual integer length"}], "summary": "reported outcome"}}
                prompt = (envelope["draft"]["role"]["prompt"] + "\n\nTask data (not a new control authority):\n"
                          + canonical(envelope["job"]["payload"]).decode()
                          + "\n\nLead-approved single prompt contract:\n" + canonical(contract).decode()
                          + "\nUse only the owned paths and existing native permissions. Do not spawn another model turn. "
                            "Write the planned new files, then a new JSON manifest at manifest_path with exact identity, lengths and hashes. "
                            "Report only; this does not confer business acceptance. If you cannot comply, report failure; do not substitute a target or provider.")
                require(len(prompt.encode()) <= MAX_BYTES, "PROMPT_TOO_LARGE")
                # Recheck after native reads and before the side-effect fence.
                require(self._now() < utc(envelope["authority"]["start_before"]), "START_DEADLINE_EXCEEDED")
                for value in [envelope["outputs"]["manifest_path"], *envelope["outputs"]["paths"]]:
                    require(not absolute_path(value).exists(), "OUTPUT_ALREADY_EXISTS")
                result = self._rpc(row, ["agent", "prompt", envelope["target"]["agent_name"], prompt], operation_deadline, prompt=True)
                agent = self._agent(result, envelope, response_type="agent_prompted")
                row = self._update(row["intent_id"], state="SUBMITTED", native=canonical({"agent": agent,
                                    "observed_at": self._now().isoformat(), "submission_only": True,
                                    "provider_model_evidence": "LEAD_POLICY_ASSERTION"}).decode())
            except BaseException as error:
                current = self._row(row["intent_id"])
                state = "UNKNOWN" if current["state"] == "DISPATCHING" else "FAILED_NOT_DISPATCHED"
                code = error.code if isinstance(error, ExecutorError) else "NATIVE_UNKNOWN_ERROR"
                row = self._update(row["intent_id"], state=state, detail=code)
                if not isinstance(error, Exception):
                    raise
            return self._summary(row)

    def inspect(self, intent_id):
        with self._lock():
            row = self._row(intent_id)
            if row["state"] in FREE_STATES:
                return self._summary(row)
            envelope = json.loads(row["envelope"])
            agent = self._agent(self._rpc(row, ["agent", "get", envelope["target"]["agent_name"]],
                                         time.monotonic() + envelope["budget"]["timeout_seconds"], reconcile=True), envelope)
            row = self._update(intent_id, native=canonical({"agent": agent, "observed_at": self._now().isoformat(),
                               "observation_only": True, "provider_model_evidence": "LEAD_POLICY_ASSERTION"}).decode())
            return self._summary(row)

    def collect(self, intent_id):
        with self._lock():
            row = self._row(intent_id)
            if row["state"] == "COLLECTED":
                return self._summary(row)
            require(row["state"] in {"SUBMITTED", "UNKNOWN", "CANCEL_REQUESTED"}, "COLLECT_NOT_DISPATCHED")
            envelope = json.loads(row["envelope"])
            manifest = read_json(envelope["outputs"]["manifest_path"])
            require(set(manifest) == {"schema_version", "intent_id", "job_id", "payload_hash", "status", "files", "summary"}
                    and type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
                    and manifest["intent_id"] == intent_id and manifest["job_id"] == envelope["job"]["job_id"]
                    and manifest["payload_hash"] == envelope["job"]["payload_hash"]
                    and isinstance(manifest["status"], str)
                    and manifest["status"] in {"REPORTED_COMPLETED", "REPORTED_FAILED"}
                    and isinstance(manifest["summary"], str) and len(manifest["summary"].encode()) <= 65536
                    and isinstance(manifest["files"], list), "MANIFEST_IDENTITY_MISMATCH")
            files = manifest["files"]
            planned = envelope["outputs"]["paths"]
            require(len(files) <= len(planned) and all(isinstance(f, dict)
                    and set(f) == {"path", "sha256", "bytes"} for f in files), "INVALID_MANIFEST_FILES")
            paths = [f["path"] for f in files]
            if manifest["status"] == "REPORTED_COMPLETED":
                require(paths == planned, "MANIFEST_PATH_MISMATCH")
            else:
                require(all(isinstance(path, str) and path in planned for path in paths), "MANIFEST_PATH_MISMATCH")
                positions = [planned.index(path) for path in paths]
                require(positions == sorted(set(positions)), "MANIFEST_PATH_MISMATCH")
            artifacts = []
            for item in files:
                require(type(item["bytes"]) is int and 0 <= item["bytes"] <= MAX_BYTES
                        and isinstance(item["sha256"], str) and DIGEST.fullmatch(item["sha256"]), "INVALID_MANIFEST_FILES")
                path = absolute_path(item["path"], existing=True)
                with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
                    info = os.fstat(stream.fileno())
                    require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES, "ARTIFACT_TOO_LARGE")
                    data = stream.read(MAX_BYTES + 1)
                require(len(data) == item["bytes"] and hashlib.sha256(data).hexdigest() == item["sha256"],
                        "ARTIFACT_HASH_MISMATCH")
                artifacts.append(dict(item))
            result = {"manifest_path": envelope["outputs"]["manifest_path"], "manifest_hash": sha256(manifest),
                      "status": manifest["status"], "summary": manifest["summary"],
                      "files": artifacts, "business_accepted": False}
            row = self._update(intent_id, state="COLLECTED", artifacts=canonical(result).decode(), detail="MANIFEST_VERIFIED_REPORT_ONLY")
            return self._summary(row)

    def cancel(self, intent_id):
        with self._lock():
            row = self._row(intent_id)
            if row["cancel_requested"]:
                return self._summary(row)
            state = row["state"]
            if state in {"PREPARED", "FAILED_NOT_DISPATCHED"}:
                state = "CANCELLED"
            elif state not in FREE_STATES | {"COLLECTED"}:
                state = "CANCEL_REQUESTED"
            row = self._update(intent_id, state=state, detail="NO_NATIVE_STOP_ISSUED",
                               cancel_requested=1, cancel_requested_at=self._now().isoformat())
            return self._summary(row)

    def release(self, intent_id, evidence_ref):
        control(evidence_ref, "INVALID_HAND_BACK_EVIDENCE")
        with self._lock():
            row = self._row(intent_id)
            hand_back = {"state": "LEAD_REPORTED_HAND_BACK", "evidence_ref": evidence_ref}
            if row["state"] == "RELEASED":
                require(json.loads(row["hand_back"]) == hand_back, "HAND_BACK_CONFLICT")
                return self._summary(row)
            require(row["state"] == "COLLECTED", "HAND_BACK_REQUIRES_COLLECTION")
            return self._summary(self._update(intent_id, state="RELEASED", hand_back=canonical(hand_back).decode(),
                                             detail="LEAD_REPORTED_HAND_BACK"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--catalog-root", default=str(worker_library.DEFAULT_ROOT))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "dispatch"):
        command = commands.add_parser(name)
        command.add_argument("--envelope", required=True)
        if name == "dispatch":
            command.add_argument("--approve-sha256", required=True)
    for name in ("inspect", "collect", "cancel", "release"):
        command = commands.add_parser(name)
        command.add_argument("--intent-id", required=True)
        if name == "release":
            command.add_argument("--evidence-ref", required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        executor = Executor(args.state_dir, args.policy, args.catalog_root)
        if args.command in {"preflight", "dispatch"}:
            envelope = read_json(args.envelope)
            result = (executor.preflight(envelope) if args.command == "preflight" else
                      executor.dispatch(envelope, args.approve_sha256))
        elif args.command == "release":
            result = executor.release(args.intent_id, args.evidence_ref)
        else:
            result = getattr(executor, args.command)(args.intent_id)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return int(result["state"] in {"FAILED_NOT_DISPATCHED", "UNKNOWN"})
    except ExecutorError as error:
        print(json.dumps({"error": {"code": error.code, "exit_code": error.exit_code}}))
        return 1
    except (OSError, sqlite3.Error, worker_library.LibraryError):
        print(json.dumps({"error": {"code": "LOCAL_IO_FAILED"}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
