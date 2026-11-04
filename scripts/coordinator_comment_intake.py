"""Stage a local Issue-comment draft, or import a bounded batch after the existing wait tick."""

import argparse
from contextlib import contextmanager
import errno
import fcntl
import heapq
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import uuid

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # fixed release sibling under python -I
    from project_coordinator import Coordinator, CoordinatorError, MAX_MESSAGE, canonical, canonical_hash, fields, identifier, require
else:
    from .project_coordinator import Coordinator, CoordinatorError, MAX_MESSAGE, canonical, canonical_hash, fields, identifier, require


ENTRY = re.compile(r"github-[0-9a-f]{64}\.json")
BATCH = 8
CURSOR = ".github-import-cursor"


@contextmanager
def inbox_directory(path):
    path = Path(path)
    require(path.anchor == "/" and ".." not in path.parts, "PRIVATE_INBOX_REQUIRED")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise CoordinatorError("PRIVATE_INBOX_REQUIRED") from exc
                raise
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and not info.st_mode & 0o077, "PRIVATE_INBOX_REQUIRED")
        yield fd
    finally:
        os.close(fd)


def read_file(path, directory=None):
    before = os.stat(path, dir_fd=directory, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode), "REGULAR_FILE_REQUIRED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(fd)
        require(os.path.samestat(before, info) and stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid() and info.st_nlink == 1, "UNSAFE_FILE")
        if directory is not None:
            require(not info.st_mode & 0o077, "PRIVATE_FILE_REQUIRED")
        raw = stream.read(MAX_MESSAGE + 1)
        require(len(raw) <= MAX_MESSAGE, "MESSAGE_TOO_LARGE")
        return raw


def decode(raw):
    def unique(pairs):
        obj = dict(pairs)
        require(len(obj) == len(pairs), "DUPLICATE_KEY")
        return obj
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except CoordinatorError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CoordinatorError("INVALID_JSON") from exc
    require(isinstance(value, dict), "INVALID_FIELDS")
    return value


def draft(value):
    fields(value, "project_id record_key issue_url public_summary source_ref authorization_ref",
           "request_id needs_confirmation")
    key = [identifier(value["project_id"]), identifier(value["record_key"])]
    # Drafts can come from a less privileged writable checkout. They can never release a posting decision.
    return f"github-{canonical_hash(key)}.json", {**value, "needs_confirmation": True}


def write_atomically(directory, name, raw):
    temp = ".github-tmp-" + uuid.uuid4().hex
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temp, dir_fd=directory)
        except FileNotFoundError:
            pass


def stage(inbox, value):
    name, record = draft(value)
    raw = canonical(record)
    with inbox_directory(inbox) as folder:
        # Only the short local file publication is serialized. Stage never opens the privileged database.
        try:
            fcntl.flock(folder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CoordinatorError("INBOX_BUSY") from exc
        try:
            saved = read_file(name, folder)
        except FileNotFoundError:
            write_atomically(folder, name, raw)
        else:
            require(raw == saved, "STAGED_RECORD_CONFLICT")
    return {"state": "STAGED", "file": name, "record_key": record["record_key"], "needs_confirmation": True}


def tick(coordinator, inbox, tick_input):
    result = coordinator.manage("tick", tick_input)
    summary = {"imported": 0, "rejected": [], "deferred": 0}
    result["github_intake"] = summary
    try:
        with inbox_directory(inbox) as folder, inbox_directory(coordinator.path.parent) as state:
            try:
                cursor = read_file(CURSOR, state).decode("ascii")
            except (OSError, CoordinatorError, UnicodeError):
                # The cursor is a scheduling hint, never an input authority or proof of import.
                cursor = ""
            with os.scandir(folder) as entries:
                names = heapq.nsmallest(BATCH, (e.name for e in entries if ENTRY.fullmatch(e.name)),
                                       key=lambda name: (name <= cursor, name))
            if not names:
                return result
            for name in names:
                # Hold no filesystem lock across the SQLite transaction. Stage leaves existing entries immutable.
                try:
                    raw = read_file(name, folder)
                    expected, value = draft(decode(raw))
                    require(expected == name, "STAGED_KEY_MISMATCH")
                    coordinator.manage("github_enqueue", value)
                except FileNotFoundError:
                    continue  # another importer finished the same immutable entry
                except CoordinatorError as exc:
                    summary["rejected"].append({"file": name, "code": exc.code})
                    continue  # retain the exact original for inspection
                except (sqlite3.Error, OSError):
                    summary["deferred"] += 1
                    continue
                # A crash here leaves the same record to be reconciled by the core's stable key and digest.
                try:
                    fcntl.flock(folder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    summary["deferred"] += 1
                    continue
                try:
                    if read_file(name, folder) == raw:
                        os.unlink(name, dir_fd=folder)
                        os.fsync(folder)
                        summary["imported"] += 1
                except FileNotFoundError:
                    pass
                except (CoordinatorError, OSError):
                    summary["deferred"] += 1
                finally:
                    fcntl.flock(folder, fcntl.LOCK_UN)
            # Rotate even when every attempted file is bad or temporarily unable to commit.
            write_atomically(state, CURSOR, names[-1].encode("ascii"))
    except (CoordinatorError, OSError, UnicodeError) as exc:
        summary["error"] = exc.code if isinstance(exc, CoordinatorError) else "INBOX_UNAVAILABLE"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for op in ("stage", "tick"):
        command = commands.add_parser(op)
        command.add_argument("--inbox", type=Path, required=True)
        command.add_argument("--input", type=Path, required=True)
        if op == "tick":
            command.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        value = decode(read_file(args.input))
        output = stage(args.inbox, value) if args.operation == "stage" else tick(Coordinator(args.state_dir), args.inbox, value)
    except (CoordinatorError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": {"code": exc.code if isinstance(exc, CoordinatorError) else "LOCAL_IO_ERROR"}}),
              file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
