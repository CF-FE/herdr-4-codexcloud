#!/usr/bin/env python3
"""Check repository structure without installing or executing any artifacts."""

import argparse
import re
import sys
from pathlib import Path


ENTRYPOINTS = {"mcp": "README.md", "skill": "SKILL.md", "plugin": "README.md"}
ENTRY_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def check_repository(root: Path) -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    counts = {category: 0 for category in ENTRYPOINTS}

    def report(path: Path, reason: str) -> None:
        errors.append(f"{path.relative_to(root)}: {reason}")

    def check_document(path: Path) -> None:
        if path.is_symlink():
            report(path, "入口不能是符号链接")
            return
        try:
            if not path.is_file():
                report(path, "缺少文档文件")
            elif not path.read_text(encoding="utf-8").strip():
                report(path, "文档不能为空")
        except (OSError, UnicodeError):
            report(path, "无法读取 UTF-8 文档")

    check_document(root / "README.md")
    check_document(root / "AGENTS.md")
    for category, entrypoint in ENTRYPOINTS.items():
        directory = root / category
        if directory.is_symlink():
            report(directory, "分类目录不能是符号链接")
            continue
        try:
            if not directory.is_dir():
                report(directory, "缺少分类目录")
                continue
            check_document(directory / "README.md")
            for entry in sorted(directory.iterdir()):
                if entry.is_symlink():
                    # README was already checked above.
                    if entry.name != "README.md":
                        report(entry, "条目不能是符号链接")
                    continue
                if not entry.is_dir():
                    continue
                counts[category] += 1
                if not ENTRY_ID.fullmatch(entry.name):
                    report(entry, "ID 须为小写字母或数字组成的单词，以单个连字符分隔")
                check_document(entry / entrypoint)
        except OSError:
            report(directory, "无法读取分类目录")
    return errors, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the directory above this script)",
    )
    arguments = parser.parse_args()
    errors, counts = check_repository(arguments.root.absolute())
    for error in errors:
        print(f"FAIL {error}", file=sys.stderr)
    print(" ".join(f"{category}={count}" for category, count in counts.items()))
    print("仅结构检查，不代表运行可用")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
