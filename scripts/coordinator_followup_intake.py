"""Import explicit coordination decisions from one operator-bound native Codex transcript."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from project_coordinator import CoordinatorError, canonical_hash, fields, require, text
    from herdr_role_contact import private
    from coordinator_approval_intake import load_policy
else:
    from .project_coordinator import CoordinatorError, canonical_hash, fields, require, text
    from .herdr_role_contact import private
    from .coordinator_approval_intake import load_policy

CURSOR = "native-followup-source.json"
MARKER = "COORD_FOLLOWUP "
PREPARE_MARKER = "COORD_APPROVAL_PREPARE "
MAX_ROW = 256 * 1024
READ_BUDGET = 4 * 1024 * 1024


def save_cursor(root, source):
    dest = root / "state" / CURSOR
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as stream:
        stream.write(json.dumps(source).encode())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stream.name, dest)


def open_source(root, path, sessions):
    caller = private(root / "handoffs/herdr-project-coordinator/binding.json")
    identity = caller["agent_session"]
    require(caller["role"] == "project_coordinator" and identity["kind"] == "id", "INVALID_COORDINATOR")
    require(path.is_relative_to(sessions) and path == path.resolve(strict=True)
            and path.name.endswith("-" + identity["value"] + ".jsonl"), "UNBOUND_TRANSCRIPT")
    stream = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb")
    try:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not (info.st_mode & 0o022),
                "UNTRUSTED_TRANSCRIPT")
        meta = json.loads(stream.readline(MAX_ROW))
        require(meta.get("type") == "session_meta" and meta["payload"].get("id") == identity["value"]
                and meta["payload"].get("source") == "cli", "WRONG_NATIVE_THREAD")
        return stream
    except BaseException:
        stream.close()
        raise


def bind_source(root, path, sessions):
    # Explicit one-time activation. Older messages are never replayed as newly authorized decisions.
    root, path = Path(root), Path(path)
    with open_source(root, path, sessions) as stream:
        info = os.fstat(stream.fileno())
        dest = root / "state" / CURSOR
        if dest.exists():
            old = private(dest)
            require((old["path"], old["dev"], old["ino"]) == (str(path), info.st_dev, info.st_ino),
                    "RECONCILE_OLD_TRANSCRIPT_FIRST")
            return {"bound": True, "preserved_offset": old["offset"]}
        stream.seek(info.st_size - 1)
        source = {"path": str(path), "dev": info.st_dev, "ino": info.st_ino, "offset": info.st_size,
                  "discard": stream.read(1) != b"\n", "accepted": 0, "rejected": 0}
        save_cursor(root, source)
        return {"bound": True, "initial_offset": source["offset"]}


def prepare_approval(coordinator, root, payload, source_ref, offset):
    """The bound coordinator proposes; only the existing watcher writes the ledger."""
    fields(payload, "spec")
    spec = payload["spec"]
    require(isinstance(spec, dict), "INVALID_APPROVAL_PROPOSAL")
    try:
        policy = load_policy(root / "native-approval-policy.json")
    except OSError as error:
        raise CoordinatorError("APPROVAL_PREPARE_POLICY_UNAVAILABLE") from error
    except ValueError as error:
        raise CoordinatorError("APPROVAL_PREPARE_POLICY_INVALID") from error
    require(spec.get("project_id") in policy["project_ids"], "UNBOUND_APPROVAL_PROJECT")
    route = {"kind": "codex_app", **{key: policy[key] for key in
             ("thread_id", "host_id", "authorization_ref", "approver_principal", "relay_principal")}}
    require(spec.get("route") == route, "UNBOUND_APPROVAL_ROUTE")
    text(spec.get("notice_summary"), 1000)
    result = coordinator.manage("approval_prepare", payload)
    receipt = {**{key: result["spec"][key] for key in ("project_id", "approval_id", "scope_version")},
               "scope_hash": result["scope_hash"], "source_ref": source_ref, "offset": offset}
    # The same original message after a cursor-write failure adds neither a
    # second approval nor a second source receipt. No authority/dispatch occurs.
    with coordinator._db() as db:
        coordinator._event(db, spec["project_id"], spec.get("request_id"), "APPROVAL_PREPARE_PROPOSAL",
                           source_ref, receipt)
    return receipt


def collect_native_followups(coordinator, root, *, sessions=None):
    root = Path(root)
    source = private(root / "state" / CURSOR)
    opened_at = source["offset"]
    with open_source(root, Path(source["path"]), sessions or Path.home() / ".codex/sessions") as stream:
        info = os.fstat(stream.fileno())
        require(info.st_dev == source["dev"] and info.st_ino == source["ino"] and info.st_size >= opened_at,
                "RECONCILE_REPLACED_TRANSCRIPT")
        stream.seek(opened_at)
        while stream.tell() - opened_at < READ_BUDGET:
            pos = stream.tell()
            raw = stream.readline(MAX_ROW)
            if not raw:
                break
            if not raw.endswith(b"\n"):
                if len(raw) < MAX_ROW:
                    break  # writer has not committed this JSONL line
                source["discard"] = True
            elif source["discard"]:
                source["discard"] = False
            elif MARKER.encode() in raw or PREPARE_MARKER.encode() in raw:
                try:
                    row = json.loads(raw)
                    require(isinstance(row, dict) and isinstance(row.get("payload"), dict),
                            "INVALID_TRANSCRIPT_ROW")
                except (CoordinatorError, ValueError, RecursionError) as exc:
                    source["last_rejection"] = {"offset": pos, "code": getattr(exc, "code", "INVALID_TRANSCRIPT_ROW")}
                    source["rejected"] += 1
                    source["offset"] = stream.tell()
                    continue
                msg = row["payload"]
                parts = msg.get("content")
                if (row.get("type") == "response_item" and msg.get("type") == "message"
                        and msg.get("role") == "assistant" and msg.get("phase") in ("commentary", "final_answer")
                        and isinstance(parts, list) and len(parts) == 1 and isinstance(parts[0], dict)
                        and parts[0].get("type") == "output_text" and isinstance(parts[0].get("text"), str)):
                    line = parts[0]["text"].strip()
                    marker = next((m for m in (MARKER, PREPARE_MARKER) if line.startswith(m)), None)
                    if marker and len(line.splitlines()) == 1:
                        source_ref = "native:coordinator:" + canonical_hash(
                            [source["path"], source["dev"], source["ino"], pos, hashlib.sha256(raw).hexdigest()])
                        try:
                            payload = json.loads(line[len(marker):])
                            require(isinstance(payload, dict), "INVALID_FIELDS")
                            if marker == PREPARE_MARKER:
                                source["last_approval_prepare"] = prepare_approval(coordinator, root, payload, source_ref, pos)
                            else:
                                op = payload.pop("op", None)
                                require(op == "put" or (op == "record" and payload.get("outcome")
                                        in ("CLOSED", "RECHECK", "DEFER")), "UNSUPPORTED_FOLLOWUP_DECISION")
                                coordinator.manage("role_followup_" + op, payload)
                            source["accepted"] += 1
                        except (OSError, sqlite3.Error, ValueError, RecursionError) as exc:
                            code = getattr(exc, "code", "")
                            host_failure = isinstance(exc, (OSError, sqlite3.Error)) or code in {
                                "APPROVAL_PREPARE_POLICY_UNAVAILABLE", "APPROVAL_PREPARE_POLICY_INVALID"}
                            if host_failure:
                                if marker == PREPARE_MARKER:
                                    source["last_failure"] = {"offset": pos, "source_ref": source_ref,
                                                              "code": code or type(exc).__name__}
                                    source["offset"] = pos  # not processed; reconcile this original proposal next round
                                    save_cursor(root, source)
                                raise
                            source["last_rejection"] = {"offset": pos, "code": getattr(exc, "code", "INVALID_JSON"),
                                                        "source_ref": source_ref}
                            source["rejected"] += 1
            source["offset"] = stream.tell()
    if opened_at != source["offset"]:
        save_cursor(root, source)
    backlog = source["offset"] - opened_at >= READ_BUDGET and source["offset"] < info.st_size
    return {**{k: source[k] for k in ("offset", "accepted", "rejected")}, "backlog": backlog,
            **{k: source[k] for k in ("last_approval_prepare", "last_rejection", "last_failure") if k in source}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["bind-source"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(bind_source(args.root, args.transcript, Path.home() / ".codex/sessions")))
