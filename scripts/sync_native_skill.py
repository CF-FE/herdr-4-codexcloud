#!/usr/bin/env python3
"""Rebuild the fixed repository-native organization skill snapshot from its sole source."""

import argparse
import shutil
import sys
from pathlib import Path


SOURCE = Path("skill/organization-collaboration")
SNAPSHOT = Path(".agents/skills/organization-collaboration")


def check_directory_path(root: Path, relative: Path) -> None:
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"{path.relative_to(root)}: 快照源与目标路径不能是符号链接")
        if path.exists() and not path.is_dir():
            raise ValueError(f"{path.relative_to(root)}: 应为目录")


def read_skill_files(root: Path, relative: Path) -> dict[Path, bytes]:
    check_directory_path(root, relative)
    directory = root / relative
    if not directory.is_dir():
        raise ValueError(f"{relative}: 缺少技能目录")
    files = {}
    directories = [directory]
    while directories:
        # Unlike rglob, iterdir propagates unreadable-directory errors.
        for path in sorted(directories.pop().iterdir()):
            if path.is_symlink():
                raise ValueError(f"{path.relative_to(root)}: 技能资源不能是符号链接")
            if path.is_file():
                files[path.relative_to(directory)] = path.read_bytes()
            elif path.is_dir():
                directories.append(path)
            else:
                raise ValueError(f"{path.relative_to(root)}: 应为普通文件或目录")
    if Path("SKILL.md") not in files:
        raise ValueError(f"{relative / 'SKILL.md'}: 缺少技能入口")
    return files


def check_native_skill(root: Path) -> list[str]:
    errors = []
    trees = []
    for relative in (SOURCE, SNAPSHOT):
        try:
            trees.append(read_skill_files(root, relative))
        except (OSError, ValueError) as error:
            errors.append(str(error))
    if errors:
        return errors
    source, snapshot = trees
    for path in sorted(source.keys() - snapshot.keys()):
        errors.append(f"{SNAPSHOT / path}: 快照缺少源文件")
    for path in sorted(snapshot.keys() - source.keys()):
        errors.append(f"{SNAPSHOT / path}: 快照有多余文件")
    for path in sorted(source.keys() & snapshot.keys()):
        if source[path] != snapshot[path]:
            errors.append(f"{SNAPSHOT / path}: 与 {SOURCE / path} 字节不一致")
    return errors


def sync_native_skill(root: Path) -> int:
    files = read_skill_files(root, SOURCE)
    check_directory_path(root, SNAPSHOT)
    destination = root / SNAPSHOT
    if destination.exists():
        shutil.rmtree(destination)
    for relative, content in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    try:
        count = sync_native_skill(arguments.root.absolute())
    except (OSError, ValueError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(f"{SOURCE} -> {SNAPSHOT}: {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
