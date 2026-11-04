"""Import explicit coordination decisions from one operator-bound native Codex transcript."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from project_coordinator import CoordinatorError, require
    from herdr_role_contact import private
else:
    from .project_coordinator import CoordinatorError, require
    from .herdr_role_contact import private

CURSOR = "native-followup-source.json"
MARKER = "COORD_FOLLOWUP "
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
            elif MARKER.encode() in raw:
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
                    if line.startswith(MARKER) and len(line.splitlines()) == 1:
                        try:
                            payload = json.loads(line[len(MARKER):])
                            require(isinstance(payload, dict), "INVALID_FIELDS")
                            op = payload.pop("op", None)
                            require(op == "put" or (op == "record" and payload.get("outcome")
                                    in ("CLOSED", "RECHECK", "DEFER")), "UNSUPPORTED_FOLLOWUP_DECISION")
                            coordinator.manage("role_followup_" + op, payload)
                            source["accepted"] += 1
                        except (ValueError, RecursionError) as exc:
                            source["last_rejection"] = {"offset": pos, "code": getattr(exc, "code", "INVALID_JSON")}
                            source["rejected"] += 1
            source["offset"] = stream.tell()
    if opened_at != source["offset"]:
        save_cursor(root, source)
    backlog = source["offset"] - opened_at >= READ_BUDGET and source["offset"] < info.st_size
    return {**{k: source[k] for k in ("offset", "accepted", "rejected")}, "backlog": backlog}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["bind-source"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(bind_source(args.root, args.transcript, Path.home() / ".codex/sessions")))
