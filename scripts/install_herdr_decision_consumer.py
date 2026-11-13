#!/usr/bin/env python3
"""Stage the fixed Decision Service consumer in a new private directory."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SESSION_MODEL = "scripts/herdr_session_model.py"
FILES = (
    "scripts/project_workflow.py",
    "scripts/project_workflow_template.py",
    "scripts/decision_service_client.py",
    "scripts/project_workflow_decision.py",
    "scripts/herdr_model_selection.py",
    "scripts/herdr_agent_creation.py",
    SESSION_MODEL,
    "scripts/herdr_role_creation.py",
    "scripts/herdr_win_creation.py",
    "scripts/project_coordinator.py",
    "scripts/allowlists.py",
    "scripts/worker_library.py",
    "config/allowlists/agent-models.json",
    "config/allowlists/image-models.json",
    "docs/worker-library/workers.json",
    "docs/worker-library/roles/index.json",
)
ROLE_RESOURCES = "docs/worker-library/roles"


def install(revision, destination, *, repository=ROOT):
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Use the complete fixed Git commit SHA")

    def git(*arguments):
        return subprocess.check_output(["git", "--no-replace-objects", *arguments], cwd=repository)

    if git("rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip() != revision:
        raise ValueError("The revision must identify a Git commit")

    payload = {}
    tree = git("ls-tree", "-rz", revision, "--", *FILES, ROLE_RESOURCES)
    for entry in tree.decode().split("\0")[:-1]:
        metadata, name = entry.split("\t", 1)
        mode, kind, object_id = metadata.split()
        if (mode not in {"100644", "100755"} or kind != "blob"
                or ".." in PurePosixPath(name).parts):
            raise ValueError(f"Expected an ordinary release file: {name}")
        payload[f"app/{name}"] = git("cat-file", "blob", object_id)
    # Older fixed commits predate the shared module. For newer commits the
    # packaged allowlist import below also verifies that dependency.
    missing = {f"app/{name}" for name in FILES if name != SESSION_MODEL} - payload.keys()
    if missing:
        raise ValueError(f"Fixed commit is missing release files: {', '.join(sorted(missing))}")

    destination = Path(destination).expanduser().absolute()
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists; use a new release directory")

    receipt = {
        "source_repository": "https://github.com/CF-FE/herdr-4-codexcloud",
        "source_commit": revision,
        "python": str(Path(sys.executable).resolve(strict=True)),
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()},
        "installation": "FILES_INSTALLED_NO_DISPATCH",
    }
    payload["install-receipt.json"] = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    with tempfile.TemporaryDirectory(dir=parent, prefix=".herdr-decision-consumer-") as temporary:
        stage = Path(temporary) / "release"
        stage.mkdir(mode=0o700)
        for name, data in payload.items():
            path = stage / name
            for directory in reversed(path.parent.relative_to(stage).parents):
                (stage / directory).mkdir(mode=0o700, exist_ok=True)
            path.parent.mkdir(mode=0o700, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o600)
        app = stage / "app"
        for policy in ("agent-models.json", "image-models.json"):
            validation = json.loads(subprocess.check_output(
                [receipt["python"], "-E", "-B", "-s", str(app / "scripts/allowlists.py"),
                 "validate", "--file", str(app / "config/allowlists" / policy)],
                cwd=app,
                stderr=subprocess.STDOUT,
            ))
            if validation.get("policy_id") != Path(policy).stem:
                raise ValueError(f"Policy identity does not match its filename: {policy}")
        # Serialize same-parent publishers and publish only the complete release.
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            if destination.exists() or destination.is_symlink():
                raise ValueError("Destination appeared during preparation")
            stage.rename(destination)
        finally:
            os.close(directory_fd)
    return {"destination": str(destination), **receipt}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=("To upgrade an existing absolute invocation path, stage beside it. With no consumer "
                "invocation in flight, preserve the old directory and move the verified release into "
                "its place. For rollback, preserve the new directory and restore the old directory "
                "to that path. Keep workflow state at its original location. The 8612a9a baseline "
                "predates allowlist enforcement. Use it for reconciliation of existing work and "
                "pause new creation while it is active."),
    )
    parser.add_argument("--revision", required=True, help="Reviewed fixed source commit")
    parser.add_argument("--destination", type=Path, required=True, help="A new release directory")
    args = parser.parse_args()
    try:
        result = install(args.revision, args.destination)
    except subprocess.CalledProcessError as error:
        detail = (error.output or b"").decode(errors="replace").strip()
        parser.exit(2, f"Install failed: {detail or error}\n")
    except (OSError, ValueError) as error:
        parser.exit(2, f"Install failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
