"""Initialize an editable, self-contained workflow in the project that owns it."""

import argparse
from graphlib import CycleError, TopologicalSorter
import json
import os
from pathlib import Path, PureWindowsPath
from pprint import pformat
import re
import sys
import tempfile
import tokenize


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def fields(value, required, location):
    if not isinstance(value, dict) or value.keys() != required:
        raise ValueError(f"{location} must contain exactly: {', '.join(sorted(required))}")


def name(value, location):
    if not isinstance(value, str) or not value or not value.isprintable() or value.strip() != value:
        raise ValueError(f"{location} must be a nonempty identifier")
    return value


def validate(definition, repo, *, custom_operations):
    fields(definition, {"version", "project", "workflow", "memory", "start", "steps"}, "Definition")
    if type(definition["version"]) is not int or definition["version"] != 1:
        raise ValueError("Expected workflow spec version 1")
    for key in ("project", "workflow"):
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name(definition[key], key)):
            raise ValueError(f"{key} must be a lowercase hyphen-separated ID")

    memory = name(definition["memory"], "memory")
    relative = Path(memory)
    if relative.is_absolute() or PureWindowsPath(memory).drive or "\\" in memory or ".." in relative.parts:
        raise ValueError("memory must be a path relative to the project")
    source = repo / relative
    if not source.is_file() or not source.resolve(strict=True).is_relative_to(repo):
        raise ValueError(f"Project memory must be an existing file inside the project: {memory!r}")

    steps = definition["steps"]
    start = name(definition["start"], "start")
    if not isinstance(steps, dict) or start not in steps:
        raise ValueError("start must name a step in the definition")
    successors = {}
    for step_id, step in steps.items():
        name(step_id, "step ID")
        location = f"Step {step_id!r}"
        kind = step.get("kind") if isinstance(step, dict) else None
        if kind == "program":
            branch = "routes" if "routes" in step else "next"
            fields(step, {"kind", "operation", branch}, location)
            operation = name(step["operation"], f"{location} operation")
            if operation != "verify_files" and not custom_operations:
                raise ValueError(f"{location}: custom operation {operation!r} requires --operations")
            if branch == "routes":
                routes = step["routes"]
                if operation == "verify_files":
                    fields(routes, {"ready", "needs_review"}, f"{location} verify_files routes")
                elif not isinstance(routes, dict) or not routes:
                    raise ValueError(f"{location} needs at least one outcome route")
                for outcome in routes:
                    name(outcome, f"{location} outcome")
                targets = routes.values()
            else:
                targets = [step["next"]]
        elif kind == "decision":
            fields(step, {"kind", "question", "choices"}, location)
            if not isinstance(step["question"], str) or not step["question"].strip():
                raise ValueError(f"{location} needs a question")
            choices = step["choices"]
            if not isinstance(choices, dict) or not choices:
                raise ValueError(f"{location} needs at least one choice")
            for choice in choices:
                name(choice, f"{location} choice")
            targets = choices.values()
        elif kind == "handoff":
            fields(step, {"kind", "instruction", "next"}, location)
            if not isinstance(step["instruction"], str) or not step["instruction"].strip():
                raise ValueError(f"{location} needs a handoff instruction")
            targets = [step["next"]]
        elif kind == "end":
            fields(step, {"kind"}, location)
            targets = []
        else:
            raise ValueError(f"{location}: unknown step kind {kind!r}")
        successors[step_id] = set()
        for target in targets:
            if name(target, f"{location} target") not in steps:
                raise ValueError(f"{location}: missing successor {target!r}")
            successors[step_id].add(target)

    seen = set()
    pending = [start]
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(successors[node])
    if seen != steps.keys():
        raise ValueError(f"Unreachable steps: {', '.join(sorted(steps.keys() - seen))}")
    # Reversing all edges preserves cycles. Only an end step may have zero successors.
    try:
        TopologicalSorter(successors).prepare()
    except CycleError as error:
        raise ValueError(f"Workflow contains a cycle: {error.args[1]}") from error


def initialize(repo, spec, operations=None):
    repo = repo.resolve(strict=True)
    if not repo.is_dir() or not spec.is_file():
        raise ValueError("Expected an existing project directory and a JSON spec file")
    definition = json.loads(spec.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    validate(definition, repo, custom_operations=operations is not None)
    parent = repo / "workflows"
    if parent.is_symlink():
        raise ValueError("Project workflows directory cannot be a symlink")
    target = parent / definition["workflow"]
    if os.path.lexists(target):
        raise FileExistsError(f"Workflow already exists: {target}")
    runner = Path(__file__).with_name("project_workflow_template.py").read_bytes()
    source = ("# Project-owned workflow definition. Edit this file to change future runs.\n"
              "DEFINITION = " + pformat(definition, sort_dicts=False, width=100) + "\n")
    if operations is not None:
        if not operations.is_file():
            raise ValueError("--operations must select an existing trusted Python file")
        # Keep the module docstring and future imports valid, including when the input uses an encoding cookie.
        with tokenize.open(operations) as module:
            source = "# coding: utf-8\n" + module.read() + "\n\n" + source
        compile(source, str(target / "definition.py"), "exec", dont_inherit=True)
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".workflow-init-", dir=parent) as temp:
        ready = Path(temp) / "workflow"
        ready.mkdir()
        (ready / "definition.py").write_text(source, encoding="utf-8")
        (ready / "workflow.py").write_bytes(runner)
        if os.path.lexists(target):
            raise FileExistsError(f"Workflow already exists: {target}")
        # Both files become visible together. A competing generator publishes a nonempty directory.
        ready.rename(target)
    return {"project": definition["project"], "workflow": definition["workflow"], "directory": str(target)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create one new project-owned workflow directory")
    init.add_argument("--repo", type=Path, required=True)
    init.add_argument("--spec", type=Path, required=True)
    init.add_argument("--operations", type=Path, help="trusted Python source defining OPERATIONS; copied without execution")
    args = parser.parse_args(argv)
    try:
        result = initialize(args.repo, args.spec, args.operations)
    except (OSError, ValueError, SyntaxError, RecursionError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
