"""Read one operator-bound Desktop recorder; never infer approval from user text.

The bound assistant confirms the original answer and emits APPROVAL_DECISION.
Only its structured receipt, citing an earlier native user item, is adopted.
"""
import argparse
from contextlib import contextmanager
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import sys
import tempfile
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from project_coordinator import Coordinator, CoordinatorError, canonical, canonical_hash, fields, require, text
    from herdr_role_contact import private
    from coordinator_approvals import VerifiedDecision, receive_decision
    from coordinator_approval_workflow import resume_approval
else:
    from .project_coordinator import Coordinator, CoordinatorError, canonical, canonical_hash, fields, require, text
    from .herdr_role_contact import private
    from .coordinator_approvals import VerifiedDecision, receive_decision
    from .coordinator_approval_workflow import resume_approval

MARKER = "APPROVAL_DECISION "
CURSOR = "native-approval-source.json"
MAX_NATIVE = 8 * 1024 * 1024


def load_policy(path):
    policy = private(Path(path))
    fields(policy, "schema_version binary binary_sha256 codex_home thread_id host_id cwd transcript_path source originator "
           "approver_principal relay_principal project_ids authorization_ref resume_checkpoints")
    require(type(policy["schema_version"]) is int and policy["schema_version"] == 1
            and type(policy["resume_checkpoints"]) is bool, "INVALID_APPROVAL_SOURCE_POLICY")
    require(policy["source"] == "vscode" and policy["originator"] == "Codex Desktop", "UNTRUSTED_RECORDER_SOURCE")
    require(isinstance(policy["project_ids"], list) and bool(policy["project_ids"]), "PROJECT_ALLOWLIST_REQUIRED")
    for value in policy["project_ids"]:
        text(value)
    for key in policy.keys() - {"schema_version", "project_ids", "resume_checkpoints"}:
        text(policy[key])
    return policy


def _identity(policy):
    binary, path = Path(policy["binary"]), Path(policy["transcript_path"])
    require(binary.is_absolute() and hashlib.sha256(binary.read_bytes()).hexdigest() == policy["binary_sha256"],
            "NATIVE_BINARY_CHANGED")
    require(Path(policy["cwd"]).is_absolute() and Path(policy["codex_home"]).is_absolute()
            and path.is_relative_to(Path(policy["codex_home"]) / "sessions") and path == path.resolve(strict=True),
            "UNBOUND_TRANSCRIPT")
    # Desktop history is paginated. Only this identity header is read from JSONL.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not (info.st_mode & 0o022),
                "UNTRUSTED_TRANSCRIPT")
        row = json.loads(stream.readline(256 * 1024))
    meta = row.get("payload", {})
    require(isinstance(meta, dict) and row.get("type") == "session_meta" and all(meta.get(k) == policy[v] for k, v in
            (("id", "thread_id"), ("cwd", "cwd"), ("source", "source"), ("originator", "originator")))
            and meta.get("history_mode") == "paginated", "WRONG_NATIVE_RECORDER")


@contextmanager
def native_reader(policy, *, timeout=2):
    require(0 < timeout <= 2, "INVALID_NATIVE_READ_BUDGET")
    deadline = time.monotonic() + timeout
    app = subprocess.Popen([policy["binary"], "app-server", "--stdio"], cwd=policy["cwd"],
                           env={**os.environ, "CODEX_HOME": policy["codex_home"]},
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buffer, serial = bytearray(), 0
    with selectors.DefaultSelector() as selector:
        selector.register(app.stdout, selectors.EVENT_READ)

        def call(method, params):
            nonlocal serial, buffer
            require(method in {"initialize", "initialized", "thread/read", "thread/turns/list", "thread/items/list"},
                    "READ_ONLY_NATIVE_METHOD_REQUIRED")
            serial += 1
            request = {"method": method, "params": params}
            if method != "initialized":
                request["id"] = serial
            app.stdin.write(canonical(request) + b"\n")
            app.stdin.flush()
            if method == "initialized":
                return {}
            while True:
                require(time.monotonic() < deadline, "NATIVE_READ_TIMEOUT")
                while b"\n" not in buffer:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0 and selector.select(remaining), "NATIVE_READ_TIMEOUT")
                    part = os.read(app.stdout.fileno(), 65536)
                    require(part, "NATIVE_READER_EXITED")
                    buffer.extend(part)
                    require(len(buffer) <= MAX_NATIVE, "NATIVE_RESPONSE_TOO_LARGE")
                line, _, tail = buffer.partition(b"\n")
                buffer = bytearray(tail)
                try:
                    answer = json.loads(line)
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise CoordinatorError("NATIVE_INVALID_JSON") from error
                require(isinstance(answer, dict) and not ("method" in answer and "id" in answer),
                        "NATIVE_INTERACTIVE_REQUEST_REFUSED")
                if answer.get("id") == serial:
                    require(isinstance(answer.get("result"), dict), "NATIVE_READ_REFUSED")
                    return answer["result"]

        try:
            call("initialize", {"clientInfo": {"name": "coordinator_approval_reader", "version": "1"}})
            call("initialized", {})
            yield call
        finally:
            app.terminate()
            try:
                app.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                app.kill()
                app.wait(timeout=0.1)
            app.stdin.close()
            app.stdout.close()


def _thread(call, policy):
    thread = call("thread/read", {"threadId": policy["thread_id"], "includeTurns": False}).get("thread")
    require(isinstance(thread, dict) and all(thread.get(k) == policy[v] for k, v in
            (("id", "thread_id"), ("cwd", "cwd"), ("path", "transcript_path"), ("source", "source"),
             ("originator", "originator"))) and thread.get("historyMode") == "paginated", "NATIVE_THREAD_MISMATCH")
    return thread


def _pages(call, policy, *, limit=32):
    params = {"threadId": policy["thread_id"], "itemsView": "notLoaded", "sortDirection": "desc", "limit": limit}
    cursors = set()
    for _ in range(16):
        page = call("thread/turns/list", params)
        turns = page.get("data")
        require(isinstance(turns, list) and "nextCursor" in page, "NATIVE_HISTORY_INCOMPLETE")
        require(all(isinstance(t, dict) and isinstance(t.get("id"), str)
                    and t.get("itemsView") == "notLoaded" for t in turns), "NATIVE_HISTORY_INCOMPLETE")
        yield turns
        cursor = page["nextCursor"]
        if cursor is None:
            return
        require(isinstance(cursor, str) and cursor and cursor not in cursors, "NATIVE_HISTORY_INCOMPLETE")
        cursors.add(cursor)
        params["cursor"] = cursor
    raise CoordinatorError("NATIVE_HISTORY_INCOMPLETE")


def _items(call, policy, turn_id, *, after=None, reverse=False):
    # One item per page avoids materializing the tools/logs of full turns.
    params = {"threadId": policy["thread_id"], "turnId": turn_id, "limit": 1,
              "sortDirection": "desc" if reverse else "asc"}
    if after is not None:
        params["cursor"] = {"type": "item", "itemId": after}
    cursors = set()
    for _ in range(256):
        page = call("thread/items/list", params)
        require(isinstance(page.get("data"), list) and "nextCursor" in page, "NATIVE_HISTORY_INCOMPLETE")
        for entry in page["data"]:
            require(isinstance(entry, dict), "NATIVE_HISTORY_INCOMPLETE")
            item = entry.get("item")
            require(entry.get("turnId") == turn_id and isinstance(item, dict)
                    and isinstance(item.get("id"), str), "NATIVE_HISTORY_INCOMPLETE")
            yield item
        cursor = page["nextCursor"]
        if cursor is None:
            return
        require(isinstance(cursor, str) and cursor and cursor not in cursors, "NATIVE_HISTORY_INCOMPLETE")
        cursors.add(cursor)
        params["cursor"] = cursor
    raise CoordinatorError("NATIVE_HISTORY_INCOMPLETE")


def probe(policy, *, reader=native_reader, timeout=2):
    require(0 < timeout <= 2, "INVALID_NATIVE_READ_BUDGET")
    deadline = time.monotonic() + timeout
    _identity(policy)
    with reader(policy, timeout=max(0.001, deadline - time.monotonic())) as call:
        thread = _thread(call, policy)
        turns = next(_pages(call, policy, limit=1))
        if turns:
            next(_items(call, policy, turns[0]["id"], reverse=True), None)
    return {"verified": True, "thread_id": thread["id"], "history_mode": thread["historyMode"]}


def save_cursor(path, source):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(canonical(source))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stream.name, path)


def _source_lock(operation):
    @wraps(operation)
    def locked(coordinator, policy, cursorpath=None, **kwargs):
        path = Path(cursorpath) if cursorpath else coordinator.path.parent / CURSOR
        descriptor = os.open(path.with_suffix(".lock"), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as lock:
            info = os.fstat(lock.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not (info.st_mode & 0o077),
                    "PRIVATE_APPROVAL_CURSOR_LOCK_REQUIRED")
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"busy": 1}
            return operation(coordinator, policy, path, **kwargs)
    return locked


@_source_lock
def bind(coordinator, policy, cursorpath=None, *, reader=native_reader, timeout=2):
    require(0 < timeout <= 2, "INVALID_NATIVE_READ_BUDGET")
    deadline = time.monotonic() + timeout
    path = Path(cursorpath) if cursorpath else coordinator.path.parent / CURSOR
    if path.exists():
        old = private(path)
        require(old["policy_hash"] == canonical_hash(policy), "RECONCILE_OLD_APPROVAL_SOURCE_FIRST")
        return {"bound": True, "preserved_anchor": old["anchor"]}
    _identity(policy)
    with reader(policy, timeout=max(0.001, deadline - time.monotonic())) as call:
        _thread(call, policy)
        turns = next(_pages(call, policy, limit=1))
        latest = turns[0] if turns else None
        item = next(_items(call, policy, latest["id"], reverse=True), None) if latest else None
    anchor = {"turn_id": latest["id"], "item_id": item["id"] if item else None} if latest else None
    save_cursor(path, {"policy_hash": canonical_hash(policy), "anchor": anchor, "accepted": 0, "rejected": 0})
    return {"bound": True, "anchor": anchor}


def _adopt(coordinator, policy, call, pages, turns, turn_id, item):
    line = item["text"]
    require(len(line.splitlines()) == 1, "INVALID_APPROVAL_RECEIPT")
    payload = json.loads(line[len(MARKER):])
    fields(payload, "project_id approval_id scope_version scope_hash decision user_response_ref decided_at", "supersedes_ref")
    require(payload["project_id"] in policy["project_ids"], "UNBOUND_APPROVAL_PROJECT")
    ref = payload.pop("user_response_ref")
    fields(ref, "turn_id item_id")
    while not any(t["id"] == ref["turn_id"] for t in turns):
        page = next(pages, None)
        require(page is not None, "ORIGINAL_USER_RESPONSE_REQUIRED")
        turns.extend(page)
    relay_n = next(n for n, t in enumerate(turns) if t["id"] == turn_id)
    user_n = next(n for n, t in enumerate(turns) if t["id"] == ref["turn_id"])
    require(user_n >= relay_n, "ORIGINAL_USER_RESPONSE_REQUIRED")
    found = False
    for original in _items(call, policy, ref["turn_id"]):
        if user_n == relay_n and original["id"] == item["id"]:
            break  # The claimed answer did not precede its recorder.
        if original["id"] == ref["item_id"]:
            found = original.get("type") == "userMessage"
            break
    require(found, "ORIGINAL_USER_RESPONSE_REQUIRED")
    key = {k: payload[k] for k in ("project_id", "approval_id", "scope_version")}
    source_ref = (f"codex-native:{policy['host_id']}:{policy['thread_id']}:user:{ref['turn_id']}/{ref['item_id']}"
                  f":relay:{turn_id}/{item['id']}")
    def verify(snapshot, _):
        route = snapshot["spec"]["route"]
        require(all(route[k] == policy[k] for k in
                    ("thread_id", "host_id", "approver_principal", "relay_principal")), "UNBOUND_APPROVAL_ROUTE")
        return VerifiedDecision(**payload, approver_principal=policy["approver_principal"],
                                relay_principal=policy["relay_principal"], source_ref=source_ref)
    return key, source_ref, receive_decision(coordinator, key, source_ref, verify)


@_source_lock
def collect(coordinator, policy, cursorpath=None, *, reader=native_reader, timeout=2):
    deadline = time.monotonic() + timeout
    require(0 < timeout <= 2, "INVALID_NATIVE_READ_BUDGET")
    path = Path(cursorpath) if cursorpath else coordinator.path.parent / CURSOR
    source = private(path)
    require(source["policy_hash"] == canonical_hash(policy), "RECONCILE_OLD_APPROVAL_SOURCE_FIRST")
    _identity(policy)
    pending, consumed = False, 0
    with reader(policy, timeout=max(0.001, deadline - time.monotonic())) as call:
        _thread(call, policy)
        pages, turns, anchor = iter(_pages(call, policy)), [], source["anchor"]
        for page in pages:
            turns.extend(page)
            if anchor is not None and any(t["id"] == anchor["turn_id"] for t in page):
                break
        visible = turns
        if anchor is not None:
            positions = [n for n, t in enumerate(turns) if t["id"] == anchor["turn_id"]]
            require(len(positions) == 1, "NATIVE_APPROVAL_ANCHOR_LOST")
            visible = turns[:positions[0] + 1]
        for turn in reversed(visible):
            require(time.monotonic() < deadline, "NATIVE_READ_TIMEOUT")
            turn_id, status = turn["id"], turn.get("status")
            require(status in {"completed", "inProgress", "interrupted", "failed"}, "NATIVE_HISTORY_INCOMPLETE")
            if status == "inProgress":
                pending = True
                break  # The original watcher will collect after TurnComplete.
            if status in {"interrupted", "failed"}:
                source["anchor"] = {"turn_id": turn_id, "item_id": None}
                save_cursor(path, source)  # Skip partial output; later receipts remain reachable.
                continue
            after = anchor["item_id"] if anchor and turn_id == anchor["turn_id"] else None
            for item in _items(call, policy, turn_id, after=after):
                require(time.monotonic() < deadline, "NATIVE_READ_TIMEOUT")
                if consumed >= 128:
                    pending = True
                    break
                consumed += 1
                line = item.get("text", "") if item.get("type") == "agentMessage" else ""
                adopted = None
                if isinstance(line, str) and line.startswith(MARKER):
                    try:
                        key, source_ref, adopted = _adopt(coordinator, policy, call, pages, turns, turn_id, item)
                    except (ValueError, RecursionError) as error:
                        # A source read/transport failure is not invalid evidence.
                        # Preserve this original receipt for the next watcher round.
                        code = getattr(error, "code", "")
                        if code.startswith("NATIVE_") or code == "READ_ONLY_NATIVE_METHOD_REQUIRED":
                            raise
                        source["rejected"] += 1
                        source["last_rejection"] = {"turn_id": turn_id, "item_id": item["id"],
                                                    "code": getattr(error, "code", "INVALID_APPROVAL_RECEIPT")}
                    if adopted is not None:
                        progress = adopted["progress"]
                        if (policy["resume_checkpoints"] and progress["decision"]["status"] == "APPROVED"
                                and progress["decision"].get("source_ref") == source_ref and not progress.get("decision_conflict")):
                            resume_approval(coordinator, key, timeout=deadline - time.monotonic())
                        source["accepted"] += 1
                source["anchor"] = {"turn_id": turn_id, "item_id": item["id"]}
                save_cursor(path, source)  # Core decision/checkpoint precedes source progress.
            if pending:
                break
    return {"accepted": source["accepted"], "rejected": source["rejected"], "backlog": pending}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("command", choices=("probe", "bind", "collect"))
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy)
        result = probe(policy) if args.command == "probe" else globals()[args.command](Coordinator(args.state_dir), policy)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": getattr(error, "code", str(error))}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
