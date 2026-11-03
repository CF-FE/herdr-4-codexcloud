#!/usr/bin/env python3
"""Install a fixed Herdr executor revision without starting Herdr or a model."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = Path.home() / "Library/Application Support/HerdrExecutionAdapter"


def install(revision, destination, herdr_binary, allowed_roots):
    """A new independent installation; existing state/config are never replaced."""
    destination = Path(destination).expanduser().absolute()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Use the complete fixed Git commit SHA")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists; preserve its configuration and journal")
    binary = Path(herdr_binary).expanduser()
    if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("Use the existing absolute Herdr executable path")
    roots = [str(Path(p).expanduser().resolve(strict=True)) for p in allowed_roots]
    if not roots or any(not Path(p).is_dir() for p in roots):
        raise ValueError("At least one existing project directory is required")
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=ROOT, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "ls-tree", "-r", commit, "--", "docs/worker-library"], cwd=ROOT, text=True
    )
    files = ["scripts/herdr_executor.py", "scripts/worker_library.py", "docs/herdr-executor.md"]
    for line in tree.splitlines():
        meta, path = line.split("\t", 1)
        if meta.split()[0] != "100644":
            raise ValueError("Only ordinary catalog files may be installed")
        files.append(path)
    if not tree:
        raise ValueError("The fixed revision has no Worker catalog")
    # Read every fixed object before creating the destination.
    data = {name: subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)
            for name in files}
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".herdr-executor-", dir=destination.parent))
    try:
        os.chmod(staging, 0o700)
        hashes = {}
        for name, blob in data.items():
            path = staging / "app" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
            path.chmod(0o600)
            hashes[name] = hashlib.sha256(blob).hexdigest()
        for directory, _, _ in os.walk(staging):
            os.chmod(directory, 0o700)
        (staging / "state").mkdir(mode=0o700)
        policy = {"schema_version": 1, "herdr_binary": str(binary),
                  "repository": "CF-FE/herdr-4-codexcloud", "project_id": "herdr-4-codexcloud",
                  "allowed_roots": roots, "targets": []}
        receipt = {"source_repository": "https://github.com/CF-FE/herdr-4-codexcloud",
                   "source_commit": commit, "files": hashes,
                   "installation": "FILES_INSTALLED_NO_TARGETS", "dispatch_performed": False,
                   "python": sys.executable, "state": "state", "policy": "config.json"}
        for name, content in [("config.json", policy), ("install-receipt.json", receipt)]:
            path = staging / name
            path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")
            path.chmod(0o600)
        # Same-parent rename publishes the complete copy, never a half-written app.
        if destination.exists():
            raise ValueError("Destination appeared during preparation")
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"source_commit": commit, "installed_files": len(hashes),
            "destination": str(destination), "dispatch_performed": False, "targets": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="Reviewed fixed source commit")
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--herdr-binary", type=Path, required=True)
    parser.add_argument("--allowed-root", type=Path, action="append", required=True)
    args = parser.parse_args()
    try:
        result = install(args.revision, args.destination, args.herdr_binary, args.allowed_root)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"Install failed: {type(error).__name__}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
