"""Exact model admission and native start/readback for bound Herdr targets."""
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess

from allowlists import DEFAULT_ROOT, LibraryError, preview_agent, read_active_agent_policy, resolve_agent_default
from herdr_session_model import DEFAULT_MODEL, codex_model_override, codex_option, require_session_model
from project_workflow_template import digest, require


LOCATION = ("workspace_id", "tab_id", "pane_id", "terminal_id")
HERDR = Path.home() / ".local/bin/herdr"
MAC_ROUTE = {"environment_id": "mac-work", "worker_id": "mac-codex", "backend_id": "current-default"}
ROUTE_FIELDS = (*MAC_ROUTE, "provider")
MODEL_FIELDS = ("provider", "model_type", "model", "reasoning_effort")


class HostError(ValueError):
    pass


def native(session, *command):
    if platform.system() != "Darwin" or os.getenv("HERDR_ENV") != "1" or os.getenv("HERDR_SESSION") != session:
        raise HostError("the authorized local Herdr session is required")
    timeout = 35 if command[:2] == ("agent", "start") else 8
    env = {key: value for key, value in os.environ.items() if key != "DECISION_SERVICE_TOKEN"}
    try:
        result = subprocess.run([str(HERDR), "--session", session, *command],
                                capture_output=True, timeout=timeout, env=env)
        if result.returncode:
            raise HostError("native command outcome unconfirmed")
        return json.loads(result.stdout)["result"]
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, TypeError):
        raise HostError("native command outcome unconfirmed") from None


def non_model_args(argv, cwd, provider):
    """Keep approved TUI options and reject a conflicting provider override."""
    switches = {"--no-daemon", "--no-alt-screen", "--strict-config"}
    values = {"-a", "--ask-for-approval", "-s", "--sandbox", "-C", "--cd", "-p", "--profile",
              "--add-dir", "--enable", "--disable", "-c", "--config", "-m", "--model"}
    require(isinstance(argv, list) and all(isinstance(arg, str) and arg and "\0" not in arg for arg in argv),
            "invalid approved Codex argv")
    retained = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        key, separator, value = codex_option(arg)
        if key in switches and not separator:
            retained.append(arg)
            index += 1
            continue
        require(key in values, "use documented new Codex TUI options in the approved argv")
        if separator:
            pair = [arg]
            index += 1
        else:
            require(index + 1 < len(argv) and not argv[index + 1].startswith("-"), "missing Codex option value")
            value = argv[index + 1]
            pair = argv[index:index + 2]
            index += 2
        if key in {"-C", "--cd"}:
            require(value == cwd, "Codex working directory differs from the authorized pane")
        codex_model_override(key, value)
        if key in {"-m", "--model"}:
            continue
        if key in {"-c", "--config"}:
            config_key, equals, config_value = value.partition("=")
            config_key = config_key.strip()
            require(equals and re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", config_key),
                    "unsupported Codex configuration key")
            if config_key == "model_provider":
                require(config_value.strip() in (provider, json.dumps(provider), f"'{provider}'"),
                        "Codex provider override conflicts with the authorized binding")
                continue
            if config_key in {"model", "model_reasoning_effort"}:
                continue
        retained.extend(pair)
    require("--no-daemon" in retained, "the approved Mac Herdr Codex options must include --no-daemon")
    return retained


def matching_candidates(selection, route, policy, policy_ref, library):
    """Intersect the requested fields, current host capabilities and the exact policy entries."""
    needs = selection["requirements"]
    require(isinstance(needs, dict) and isinstance(selection["capabilities"], list)
            and type(needs.get("input_tokens", 0)) is int and needs.get("input_tokens", 0) >= 0,
            "invalid task requirements or capabilities")
    for key in ("tools", "modalities", "models", "providers", "efforts"):
        require(key not in needs or isinstance(needs[key], list), "task capability requirements must be arrays")
    environment = route["environment_id"]
    defaults = resolve_agent_default(policy, environment)
    models = needs.get("models", [defaults["model"] or DEFAULT_MODEL])
    for model in models:
        require_session_model(model)
    efforts = needs.get("efforts", [defaults["reasoning_effort"]] if defaults["reasoning_effort"] is not None else [])
    capabilities = []
    for row in selection["capabilities"]:
        require(isinstance(row, dict), "capabilities must contain objects")
        if any(row.get(field, environment if field == "environment_id" else None) != route[field]
               for field in ROUTE_FIELDS):
            continue
        if (not set(needs.get("tools", [])) <= set(row["tools"])
                or not set(needs.get("modalities", [])) <= set(row["modalities"])
                or needs.get("input_tokens", 0) > row["context_window"]
                or row["model"] not in models
                or ("providers" in needs and row["provider"] not in needs["providers"])):
            continue
        capabilities.append(row)
    matched, _, _ = preview_agent(policy, capabilities, library)
    choices = []
    for match in matched:
        entry = match["entry"]
        effort = entry["reasoning_effort"]
        if effort not in efforts:
            continue
        model = {key: entry[key] for key in MODEL_FIELDS}
        choices.append({"candidate_id": route["worker_id"] + "-" + digest(model),
                        "description": f'{entry["model"]} ({effort or "not applicable"})',
                        "parameters": model, "metadata": {"worker_id": route["worker_id"],
                        "backend_id": route["backend_id"],
                        "capability_ref": match["capability_sources"][0], "model_policy": policy_ref}})
    return sorted(choices, key=lambda choice: choice["candidate_id"])


def matching_cwd(observation, cwd, foreground_cwd_optional):
    foreground_cwd = observation.get("foreground_cwd")
    # Herdr 0.9.5 Windows omits this field. Its process-info cwd is checked separately.
    return observation.get("cwd") == cwd and (
        foreground_cwd == cwd or (foreground_cwd_optional and foreground_cwd is None))


def check_empty(target, host, shell_process, *, foreground_cwd_optional=False):
    agents = host("agent", "list")["agents"]
    require(not any(agent.get("name") == target["agent"] or agent.get("pane_id") == target["pane_id"]
                    for agent in agents), "existing or pending agent on authorized target")
    panes = host("pane", "list", "--workspace", target["workspace_id"])["panes"]
    require(any(all(pane.get(key) == target[key] for key in LOCATION)
                and matching_cwd(pane, target["cwd"], foreground_cwd_optional)
                and not pane.get("launch_pending") for pane in panes), "authorized pane is unconfirmed")
    process = host("pane", "process-info", "--pane", target["pane_id"])["process_info"]
    shell = process["shell_pid"]
    foreground = process["foreground_processes"]
    require(process.get("pane_id") == target["pane_id"] and type(shell) is int and shell > 0
            and process["foreground_process_group_id"] == shell and len(foreground) == 1
            and foreground[0].get("pid") == shell and foreground[0].get("cwd") == target["cwd"]
            and foreground[0].get("argv") and shell_process(foreground[0]["argv"][0]),
            "interactive shell is unconfirmed")


def mac_shell_process(argv0):
    return Path(argv0).name.lstrip("-") in {"zsh", "bash", "sh"}


def mac_agent_process(argv0):
    return Path(argv0).name.startswith("codex")


def mac_launch_argv(base_args, selected):
    argv = [*base_args, "-m", selected["model"], "-c", "model_provider=" + json.dumps(selected["provider"])]
    if selected["reasoning_effort"] is not None:
        argv.extend(("-c", "model_reasoning_effort=" + json.dumps(selected["reasoning_effort"])))
    return argv


def check_policy_at_start(path, selection, route, frozen_ref, selected):
    try:
        policy, current_ref, library = read_active_agent_policy(path, DEFAULT_ROOT)
        return current_ref == frozen_ref and any(
            choice["parameters"] == selected
            for choice in matching_candidates(selection, route, policy, current_ref, library))
    except LibraryError:
        return False


def start_agent(target, host, kind, argv):
    try:
        receipt = host("agent", "start", target["agent"], "--kind", kind, "--pane", target["pane_id"],
                       "--timeout", "30000", "--", *argv)
        require(isinstance(receipt, dict), "native launch acknowledgement is unconfirmed")
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        return "unknown"
    return "ready_acknowledged"


def read_agent_thread(target, host, kind, process_matches, argv_digest, recorded_thread=None, *,
                      session_identity=None, foreground_cwd_optional=False):
    try:
        agent = host("agent", "get", target["agent"])["agent"]
        require(isinstance(agent, dict), "invalid native agent observation")
        identity = agent.get("agent_session") or {}
        require(isinstance(identity, dict), "invalid native session observation")
        thread_id = (session_identity(identity) if session_identity else
                     identity.get("value") if identity.get("kind") == "id" else None)
        require(agent.get("name") == target["agent"] and agent.get("agent") == kind
                and matching_cwd(agent, target["cwd"], foreground_cwd_optional)
                and all(agent.get(key) == target[key] for key in LOCATION)
                and identity.get("agent") == kind and isinstance(thread_id, str) and thread_id,
                "native agent identity is unconfirmed")
        if recorded_thread and thread_id != recorded_thread:
            return None
        if recorded_thread and not foreground_cwd_optional:
            return thread_id
        process = host("pane", "process-info", "--pane", target["pane_id"])["process_info"]
        require(isinstance(process, dict), "invalid native foreground observation")
        leader = process.get("foreground_process_group_id")
        require(process.get("pane_id") == target["pane_id"] and type(leader) is int and leader > 0
                and any(isinstance(item, dict) and item.get("pid") == leader and item.get("cwd") == target["cwd"]
                        and item.get("argv") and process_matches(item["argv"][0])
                        and digest(item["argv"][1:]) == argv_digest
                        for item in process["foreground_processes"]), "bound launch argv is unconfirmed")
        return thread_id
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def require_private(stream):
    info = os.fstat(stream.fileno())
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            "use an owner-private regular file")
