"""Apply a selected configuration through the existing Windows Herdr OMP route."""

import json
import ntpath
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import subprocess


HERDR = Path.home() / ".local/bin/herdr"
ROUTE = {"environment_id": "win-work", "worker_id": "win-omp",
         "backend_id": "cli-proxy-responses", "provider": "cli-proxy-responses"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
SESSION_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


def validate_target(target):
    machine = target.get("machine")
    if not isinstance(machine, str) or not re.fullmatch(r"[0-9a-f]{32}", machine):
        raise ValueError("Win creation requires the exact saved Herdr machine ID")
    session = target.get("session")
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", session):
        raise ValueError("Win creation requires the bound remote Herdr session")
    cwd = target.get("cwd")
    if not isinstance(cwd, str) or not PureWindowsPath(cwd).is_absolute():
        raise ValueError("Win creation requires an absolute Windows working directory")
    session_dir = target.get("session_dir")
    if (not isinstance(session_dir, str) or not PureWindowsPath(session_dir).is_absolute()
            or any(ord(character) < 32 for character in session_dir)):
        raise ValueError("Win creation requires an authorized absolute OMP session directory")


def native(target, *command):
    validate_target(target)
    if (platform.system() != "Darwin" or os.getenv("HERDR_ENV") != "1"
            or not os.getenv("HERDR_SESSION") or not os.getenv("HERDR_PANE_ID")):
        raise ValueError("Win creation requires the original Mac Herdr driver pane")
    if command[:2] not in {("agent", "list"), ("agent", "get"), ("agent", "start"),
                           ("pane", "list"), ("pane", "process-info")}:
        raise ValueError("unsupported Win native creation operation")
    environment = {key: value for key, value in os.environ.items() if key != "DECISION_SERVICE_TOKEN"}

    def query(arguments, timeout):
        try:
            response = subprocess.run([str(HERDR), *arguments], capture_output=True, timeout=timeout,
                                      env=environment, check=True)
            return json.loads(response.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ValueError("Win native command outcome is unconfirmed") from None

    profiles = query(["machine", "list", "--json"], 8)
    if not isinstance(profiles, list) or not all(isinstance(row, dict) for row in profiles):
        raise ValueError("Win saved Herdr machine catalog is unconfirmed")
    matches = [row for row in profiles if row.get("id") == target["machine"]]
    if (len(matches) != 1 or matches[0].get("label") != ROUTE["environment_id"]
            or matches[0].get("session") != target["session"] or matches[0].get("enabled") is not True):
        raise ValueError("Win saved Herdr machine binding changed or is disabled")
    response = query(["--machine", target["machine"], *command], 45 if command[:2] == ("agent", "start") else 15)
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        raise ValueError("Win native command outcome is unconfirmed")
    return response["result"]


def launch_argv(binding, selected):
    if binding.get("route") != ROUTE or binding.get("agent_args") != []:
        raise ValueError("Win creation requires the verified OMP Responses route without startup overrides")
    target = binding["target"]
    validate_target(target)
    model, effort = selected.get("model"), selected.get("reasoning_effort")
    if (selected.get("provider") != ROUTE["provider"] or selected.get("model_type") != "reasoning"
            or not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", model)
            or not isinstance(effort, str) or effort not in EFFORTS):
        raise ValueError("the selected model configuration cannot be represented by the Win OMP route")
    # OMP otherwise honors the workspace's auto-resume preference.
    return ["--model", f"{ROUTE['provider']}/{model}", "--thinking", effort,
            "--session-dir", target["session_dir"]]


def shell_process(argv0):
    return ntpath.basename(argv0).lower().lstrip("-").removesuffix(".exe") in {
        "powershell", "pwsh", "cmd", "bash", "sh", "zsh",
    }


def agent_process(argv0):
    return ntpath.basename(argv0).lower() in {"omp", "omp.exe"}


def session_identity(identity):
    if identity.get("agent") != "omp" or identity.get("source") != "herdr:omp":
        return None
    value = identity.get("value")
    if not isinstance(value, str):
        return None
    if identity.get("kind") == "id" and re.fullmatch(SESSION_ID, value):
        return value
    if identity.get("kind") == "path" and PureWindowsPath(value).is_absolute():
        match = re.fullmatch(rf"\d{{4}}-\d\d-\d\dT\d\d-\d\d-\d\d-\d{{3}}Z_({SESSION_ID})\.jsonl",
                             ntpath.basename(value))
        return match.group(1) if match else None
    return None
