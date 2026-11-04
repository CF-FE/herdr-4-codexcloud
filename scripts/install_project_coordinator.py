#!/usr/bin/env python3
"""Prepare a private, fixed-commit Mac coordinator release and its local timer."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CORE_FILES = ('scripts/project_coordinator.py', 'scripts/coordinator_transport.py')
BRIDGE = 'scripts/coordinator_bridge.py'
GOAL_CHANNEL = 'scripts/coordinator_goal_channel.py'
GOAL_TOOLS = 'mcp/project-coordinator-tools/server.py'
ROLE_CONTACT = 'scripts/herdr_role_contact.py'
COMMENT_INTAKE = 'scripts/coordinator_comment_intake.py'
FILES = (*CORE_FILES, BRIDGE, GOAL_CHANNEL, GOAL_TOOLS, ROLE_CONTACT, COMMENT_INTAKE)
LABEL = 'io.projectcoordinator.waits'
DEFAULT_ROOT = Path.home() / 'Library/Application Support/ProjectCoordinator'


def private_directory(path, *, create=True):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Expected a private directory owned by the current account')


def install(revision, destination, *, repository=ROOT, github_inbox=None):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('A full Git commit is required')
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repository)
    if git('rev-parse', '--verify', revision + '^{commit}').decode().strip() != revision:
        raise ValueError('Not a commit')
    payload = {}
    # Older fixed commits can still be verified/reinstalled using their original manifest.
    has_bridge = bool(git('ls-tree', revision, '--', BRIDGE).strip())
    selected = (*CORE_FILES, *((BRIDGE,) if has_bridge else ()))
    if git('ls-tree', revision, '--', GOAL_CHANNEL).strip():
        selected += (GOAL_CHANNEL,)
    if git('ls-tree', revision, '--', GOAL_TOOLS).strip():
        if BRIDGE not in selected or GOAL_CHANNEL not in selected:
            raise ValueError('The organization tools require the complete coordinator release')
        selected += (GOAL_TOOLS,)
    if git('ls-tree', revision, '--', ROLE_CONTACT).strip():
        selected += (ROLE_CONTACT,)
    if git('ls-tree', revision, '--', COMMENT_INTAKE).strip():
        selected += (COMMENT_INTAKE,)
    if github_inbox is not None:
        github_inbox = Path(github_inbox)
        if COMMENT_INTAKE not in selected or not github_inbox.is_absolute() or github_inbox != github_inbox.resolve():
            raise ValueError('Expected a fixed release with comment intake and an absolute, non-symlink inbox')
        private_directory(github_inbox, create=False)
    for name in selected:
        entry = git('ls-tree', revision, '--', name).decode().strip().split()
        if len(entry) != 4 or entry[0] not in ('100644', '100755') or entry[1] != 'blob' or entry[3] != name:
            raise ValueError('The selected commit lacks an ordinary coordinator source file')
        payload[name] = git('cat-file', 'blob', entry[2])
    destination = Path(destination).expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    for path in [destination, destination / 'state', destination / 'releases']:
        private_directory(path)
    release = destination / 'releases' / revision
    interpreter = str(Path(sys.executable).resolve(strict=True))
    payload['tick-input.json'] = b'{"limit":25}\n'
    config = {'Label': LABEL, 'StartInterval': 60, 'RunAtLoad': True,
              'ProgramArguments': [interpreter, '-I', '-B', str(release / FILES[0]),
                                   '--state-dir', str(destination / 'state'), 'tick',
                                   '--input', str(release / 'tick-input.json')]}
    if github_inbox is not None:
        config['ProgramArguments'] = [interpreter, '-I', '-B', str(release / COMMENT_INTAKE), 'tick',
                                      '--inbox', str(github_inbox), '--state-dir', str(destination / 'state'),
                                      '--input', str(release / 'tick-input.json')]
    payload['observer.plist'] = plistlib.dumps(config)
    if has_bridge:
        pump = {'Label': 'io.projectcoordinator.bridge-pump', 'StartInterval': 60, 'RunAtLoad': True,
                'ProgramArguments': [interpreter, '-I', '-B', str(release.resolve() / BRIDGE),
                                     '--config', str(destination.resolve() / 'bridge-port.json'), 'pump']}
        payload['bridge-pump.plist'] = plistlib.dumps(pump)
    receipt = {'source_commit': revision, 'scope': 'LOCAL_WAIT_OBSERVER', 'python': interpreter,
               'files': {key: hashlib.sha256(data).hexdigest() for key, data in payload.items()}}
    payload['receipt.json'] = (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode()
    if release.exists() or release.is_symlink():
        private_directory(release, create=False)
        for name, data in payload.items():
            path = release / name
            for parent in path.relative_to(release).parents:
                if parent != Path('.'):
                    private_directory(release / parent, create=False)
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or path.read_bytes() != data):
                raise ValueError('Existing release differs from its frozen inputs')
    else:
        with tempfile.TemporaryDirectory(dir=release.parent, prefix='.install-') as tmp:
            ready = Path(tmp) / 'release'
            ready.mkdir(mode=0o700)
            for name, data in payload.items():
                path = ready / name
                for parent in reversed(path.relative_to(ready).parents):
                    if parent != Path('.'):
                        private_directory(ready / parent)
                path.write_bytes(data)
                path.chmod(0o600)
            ready.rename(release)
    return {'release': str(release), 'state_dir': str(destination / 'state'), **receipt}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--destination', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--github-inbox', type=Path, help='Existing private foreground staging directory')
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.revision, args.destination, github_inbox=args.github_inbox), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f'Install failed: {error}\n')
