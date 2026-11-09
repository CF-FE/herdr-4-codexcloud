"""Select and apply a Mac Codex configuration for an authorized new handoff unit."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import stat
import subprocess
import sys

from decision_service_client import DecisionService
from project_workflow_decision import (MODEL_FIELDS, Checkpoint, add_checkpoint_arguments, freeze, handoff_unit,
                                       integration, model_application, reconcile, resolve)
from project_workflow_template import decision_state_digest, digest, encoded, require


LOCATION = ("workspace_id", "tab_id", "pane_id", "terminal_id")
HERDR = Path.home() / ".local/bin/herdr"


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


def non_model_args(argv, cwd):
    """Keep approved TUI startup options, replacing only model and effort overrides."""
    switches = {"--no-daemon", "--no-alt-screen", "--strict-config"}
    values = {"-a", "--ask-for-approval", "-s", "--sandbox", "-C", "--cd", "-p", "--profile",
              "--add-dir", "--enable", "--disable", "-c", "--config", "-m", "--model"}
    require(isinstance(argv, list) and all(isinstance(arg, str) and arg and "\0" not in arg for arg in argv),
            "invalid approved Codex argv")
    retained = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        key, separator, value = arg.partition("=")
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
        if key in {"-m", "--model"}:
            continue
        if key in {"-c", "--config"}:
            config_key, equals, _ = value.partition("=")
            require(equals and re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", config_key.strip()),
                    "unsupported Codex configuration key")
            if config_key.strip() in {"model", "model_reasoning_effort"}:
                continue
        retained.extend(pair)
    require("--no-daemon" in retained, "the approved Mac Herdr Codex options must include --no-daemon")
    return retained


def candidates(selection, provider):
    """Build candidate pairs from the original creator's Mac backend capability observations."""
    needs = selection["requirements"]
    require(isinstance(needs, dict) and isinstance(selection["capabilities"], list)
            and type(needs.get("input_tokens", 0)) is int and needs.get("input_tokens", 0) >= 0,
            "invalid task requirements or capabilities")
    for key in ("tools", "modalities", "models", "providers", "efforts"):
        require(key not in needs or isinstance(needs[key], list), "task capability requirements must be arrays")
    choices = {}
    for row in selection["capabilities"]:
        if row["worker_id"] != "mac-codex" or row["backend_id"] != "current-default" or row["provider"] != provider:
            continue
        if (not set(needs.get("tools", [])) <= set(row["tools"])
                or not set(needs.get("modalities", [])) <= set(row["modalities"])
                or needs.get("input_tokens", 0) > row["context_window"]
                or ("models" in needs and row["model"] not in needs["models"])
                or ("providers" in needs and row["provider"] not in needs["providers"])):
            continue
        identity = {key: row[key] for key in MODEL_FIELDS if key != "reasoning_effort"}
        require(all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value)
                    for value in identity.values()), "invalid Mac model identity")
        require(isinstance(row["evidence_ref"], str) and row["evidence_ref"].strip(), "capability source required")
        require(isinstance(row["supported_efforts"], list), "invalid effort capability")
        for effort in row["supported_efforts"] or [None]:
            require(effort is None or isinstance(effort, str) and re.fullmatch(r"[a-z][a-z0-9_-]*", effort),
                    "invalid supported effort")
            if "efforts" in needs and effort not in needs["efforts"]:
                continue
            model = dict(identity, reasoning_effort=effort)
            key = "mac-codex-" + digest(model)
            choices[key] = {"candidate_id": key, "description": f'{row["model"]} ({effort or "not applicable"})',
                            "parameters": model, "metadata": {"worker_id": row["worker_id"],
                            "backend_id": row["backend_id"], "capability_ref": row["evidence_ref"]}}
    return [choices[key] for key in sorted(choices)]


def check_empty(target, host):
    session = target["session"]
    agents = host(session, "agent", "list")["agents"]
    require(not any(agent.get("name") == target["agent"] or agent.get("pane_id") == target["pane_id"]
                    for agent in agents), "existing or pending agent on authorized target")
    panes = host(session, "pane", "list", "--workspace", target["workspace_id"])["panes"]
    require(any(all(pane.get(key) == target[key] for key in LOCATION)
                and pane.get("cwd") == pane.get("foreground_cwd") == target["cwd"]
                and not pane.get("launch_pending") for pane in panes), "authorized pane is unconfirmed")
    process = host(session, "pane", "process-info", "--pane", target["pane_id"])["process_info"]
    shell = process["shell_pid"]
    foreground = process["foreground_processes"]
    require(process.get("pane_id") == target["pane_id"] and type(shell) is int and shell > 0
            and process["foreground_process_group_id"] == shell and len(foreground) == 1
            and foreground[0].get("pid") == shell and foreground[0].get("cwd") == target["cwd"]
            and foreground[0].get("argv") and Path(foreground[0]["argv"][0]).name.lstrip("-") in {"zsh", "bash", "sh"},
            "interactive shell is unconfirmed")


def apply(checkpoint, client, unit_id, binding, selection, *, host=native):
    require(binding["mode"] == "new-unit", "this adapter creates an authorized new-unit agent")
    target = {key: binding[key] for key in ("session", "agent", "cwd", *LOCATION)}
    require(all(isinstance(value, str) and value.strip() and "\0" not in value for value in target.values())
            and Path(target["cwd"]).is_absolute() and isinstance(binding["authorization_ref"], str)
            and binding["authorization_ref"].strip() and isinstance(binding["provider"], str)
            and binding["provider"].strip(), "an approved native target, provider and authorization are required")
    require(all(not value.startswith("-") for key, value in target.items() if key != "cwd"),
            "invalid native target")
    base_args = non_model_args(binding["codex_args"], target["cwd"])
    binding_digest = digest(binding)
    status = checkpoint.native("status")
    require(status.get("decision_integration_version") == 1, "workflow integration is unavailable")
    with checkpoint.open(write=True) as run:
        data = integration(run)
        require(binding["project_id"] == run["project"] and binding["task_id"] == run["task"]
                and binding["unit_id"] == unit_id, "authorization does not match the original task unit")
        launches = data.setdefault("launches", {})
        existing = launches.get(unit_id)
        if existing is not None:
            require(existing["binding_digest"] == binding_digest, "original launch binding changed")
            request_id = existing["request_id"]
        else:
            require(run["status"] == status["status"] == "WAITING_RECEIPT"
                    and run["action"]["id"] == status["action"]["id"] == binding["action_id"],
                    "new-unit creation requires the original pending handoff")
            unit = handoff_unit(run, data)
            require(unit["unit_id"] == unit_id, "unit is not associated with this handoff")
            choices = candidates(selection, binding["provider"])
            if not choices:
                return {"status": "complete", "disposition": "unavailable", "reason": "no_eligible_mac_codex_configuration"}
            context = selection.get("context", {})
            require(isinstance(context, dict), "selection context must be an object")
            entry = freeze(run, "role_model_selection", unit, "Which eligible Mac Codex configuration fits this unit?",
                           choices, context, selection.get("recall", "auto"), selection.get("bank_ids"))
            request_id = entry["request"]["request_id"]
            require(entry.setdefault("binding_digest", binding_digest) == binding_digest,
                    "frozen model request belongs to another approved binding")
            data["actions"].setdefault(run["step"], run["action"])
    if existing is not None:
        return reconcile_model(checkpoint, client, request_id, host=host)

    decision = resolve(checkpoint, client, request_id)
    if decision["status"] != "complete":
        return {"request_id": request_id, "decision_id": decision["decision_id"], "status": decision["status"]}
    model = model_application({"request": entry["request"], "decision": decision})
    selected = model["recommended_model"]
    if decision["disposition"] != "suggested":
        with checkpoint.open(write=True) as run:
            integration(run)["requests"][request_id].setdefault("model", model)
        return {"request_id": request_id, "decision_id": decision["decision_id"], "status": "complete",
                "disposition": decision["disposition"], "feedback": reconcile(checkpoint, client)}

    argv = [*base_args, "-m", selected["model"]]
    if selected["reasoning_effort"] is not None:
        argv.extend(("-c", "model_reasoning_effort=" + json.dumps(selected["reasoning_effort"])))
    # Inspect the native target outside SQLite. The transaction then fences a
    # concurrent workflow transition or a second caller of this creation path.
    try:
        check_empty(target, host)
    except (ValueError, KeyError, TypeError):
        with checkpoint.open(write=True) as run:
            existing = integration(run)["launches"].get(unit_id)
            if existing:
                require(existing["binding_digest"] == binding_digest, "original launch binding changed")
            else:
                model.update(application_status="unknown", reason="native_target_unconfirmed")
                integration(run)["requests"][request_id]["model"] = model
        if existing:
            return reconcile_model(checkpoint, client, existing["request_id"], host=host)
        return {"request_id": request_id, "decision_id": decision["decision_id"], "status": "target_unconfirmed",
                "feedback": reconcile(checkpoint, client)}
    with checkpoint.open(write=True) as run:
        data = integration(run)
        entry = data["requests"][request_id]
        existing = data["launches"].get(unit_id)
        if existing is not None:
            require(existing["binding_digest"] == binding_digest, "original launch binding changed")
        else:
            if decision_state_digest(run) != entry["request"]["snapshot"]["state_digest"]:
                model.update(application_status="stale", reason="snapshot_changed")
            else:
                require(not any(prior["target"]["session"] == target["session"] and
                                (prior["target"]["pane_id"] == target["pane_id"] or
                                 prior["target"]["agent"] == target["agent"]) for prior in data["launches"].values()),
                        "target already has an original launch intent in this checkpoint")
                data["launches"][unit_id] = {"request_id": request_id, "binding_digest": binding_digest,
                                            "target": target, "argv_digest": digest(argv), "outcome": "unknown"}
                model.update(launch_model=selected, application_status="unknown", reason="launch_unconfirmed")
            entry["model"] = model
    if existing is not None:
        return reconcile_model(checkpoint, client, existing["request_id"], host=host)
    if model["launch_model"] is None:
        return {"request_id": request_id, "decision_id": decision["decision_id"], "status": "stale",
                "feedback": reconcile(checkpoint, client)}
    try:
        host(target["session"], "agent", "start", target["agent"], "--kind", "codex", "--pane", target["pane_id"],
             "--timeout", "30000", "--", *argv)
    except HostError:
        # The command can have reached Herdr even without a usable response.
        pass
    else:
        with checkpoint.open(write=True) as run:
            integration(run)["launches"][unit_id]["outcome"] = "ready_acknowledged"
    return reconcile_model(checkpoint, client, request_id, host=host)


def read_turn(rollout, turn_id, thread_id, cwd):
    """Read a specifically identified native turn, without forwarding transcript data."""
    with rollout.open("rb") as stream:
        require_private(stream)
        header = json.loads(stream.readline())
        meta = header["payload"]
        require(header["type"] == "session_meta" and meta["id"] == thread_id and meta["cwd"] == cwd,
                "Codex rollout does not match the bound thread and directory")
        observed = None
        for line in stream:
            if not line.endswith(b"\n"):
                break  # The live writer may still be appending this record.
            item = json.loads(line)
            payload = item.get("payload", {})
            if item["type"] == "turn_context" and payload.get("turn_id") == turn_id and payload.get("cwd") == cwd:
                observed = {"provider": meta.get("model_provider"), "model": payload.get("model"),
                            "reasoning_effort": payload.get("effort")}
        return observed


def reconcile_model(checkpoint, client, request_id, *, rollout=None, turn_id=None, host=native):
    with checkpoint.open() as run:
        data = integration(run)
        entry = data["requests"][request_id]
        request = entry["request"]
        unit_id = request["subject_ref"]["unit_id"]
        launch = data.get("launches", {}).get(unit_id)
        require(launch is not None and launch["request_id"] == request_id, "no original model launch to reconcile")
        target = launch["target"]
    thread_id = None
    try:
        agent = host(target["session"], "agent", "get", target["agent"])["agent"]
        identity = agent.get("agent_session") or {}
        require(agent.get("name") == target["agent"] and agent.get("agent") == "codex"
                and agent.get("cwd") == agent.get("foreground_cwd") == target["cwd"]
                and all(agent.get(key) == target[key] for key in LOCATION)
                and identity.get("agent") == "codex" and identity.get("kind") == "id"
                and isinstance(identity.get("value"), str) and identity["value"],
                "native Codex identity is unconfirmed")
        if not launch.get("thread_id"):
            process = host(target["session"], "pane", "process-info", "--pane", target["pane_id"])["process_info"]
            require(process.get("pane_id") == target["pane_id"] and any(
                item.get("pid") == process["foreground_process_group_id"] and item.get("cwd") == target["cwd"]
                and item.get("argv") and Path(item["argv"][0]).name.startswith("codex")
                and digest(item["argv"][1:]) == launch["argv_digest"]
                for item in process["foreground_processes"]), "bound Codex launch argv is unconfirmed")
        thread_id = identity["value"]
    except (ValueError, KeyError, TypeError):
        pass
    observed = None
    # A prior verified binding can be used after the role exits. Never adopt a
    # different thread found in the same terminal on a later lookup.
    recorded_thread = launch.get("thread_id")
    if recorded_thread and thread_id != recorded_thread:
        thread_id = None
    evidence_thread = thread_id or recorded_thread
    if rollout is not None and evidence_thread:
        observed = read_turn(rollout, turn_id, evidence_thread, target["cwd"])
    with checkpoint.open(write=True) as run:
        data = integration(run)
        launch = data["launches"][unit_id]
        model = data["requests"][request_id]["model"]
        if thread_id:
            require(launch.setdefault("thread_id", thread_id) == thread_id, "native thread changed during reconciliation")
            model.setdefault("execution_ref", {"source": "herdr", "ids": {
                "pane_id": target["pane_id"], "thread_id": thread_id,
            }})
            if not model.get("effective_candidate_id") and model["reason"] == "launch_unconfirmed":
                model["reason"] = "effective_configuration_unconfirmed"
        if observed and all(value is None or isinstance(value, str) and value.strip() for value in observed.values()):
            identities = [item for item in request["choices"] if all(
                item["parameters"][key] == observed[key] for key in ("provider", "model"))]
            types = {item["parameters"]["model_type"] for item in identities}
            model_type = next(iter(types)) if len(types) == 1 else None
            effort = observed["reasoning_effort"]
            effort_status = "set" if effort else ("not_applicable" if identities and all(
                item["parameters"]["reasoning_effort"] is None for item in identities) else "unconfirmed")
            effective = dict(observed, model_type=model_type, effort_status=effort_status,
                             configuration_status="confirmed" if model_type and effort_status != "unconfirmed"
                             else "unconfirmed")
            ids = model["execution_ref"]["ids"]
            require(ids.setdefault("turn_id", turn_id) == turn_id, "model evidence is for another native turn")
            effective_id = next((item["candidate_id"] for item in identities
                                 if item["parameters"]["reasoning_effort"] == effort), None)
            model.update(effective_model=effective, effective_candidate_id=effective_id)
            if effective["configuration_status"] == "confirmed":
                match = all(effective[key] == model["recommended_model"][key] for key in MODEL_FIELDS)
                model.update(application_status="applied" if match else "not_applied",
                             reason=None if match else "effective_configuration_differs")
            else:
                model.update(application_status="unknown", reason="effective_configuration_unconfirmed")
        output = {"request_id": request_id, "decision_id": entry["decision"]["decision_id"], "mode": "new-unit",
                  "launch_outcome": launch["outcome"], "thread_id": launch.get("thread_id"),
                  **{key: model[key] for key in ("application_status", "reason", "recommended_model",
                                                "launch_model", "effective_model")}}
    output["feedback"] = reconcile(checkpoint, client)
    return output


def require_private(stream):
    info = os.fstat(stream.fileno())
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            "use an owner-private regular file")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_checkpoint_arguments(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("apply", help="apply once on the original creator's authorized new-unit target")
    start.add_argument("--unit-id", required=True)
    start.add_argument("--binding", required=True, type=Path, help="owner-private original new-unit binding JSON")
    start.add_argument("--selection", required=True, type=Path, help="task requirements and reviewed Mac capability JSON")
    recover = commands.add_parser("reconcile", help="inspect the original launch and supplement pending feedback")
    recover.add_argument("--request-id", required=True)
    recover.add_argument("--rollout", type=Path, help="exact owner-private Codex rollout for the original turn")
    recover.add_argument("--turn-id", help="original work unit's native Codex turn")
    args = parser.parse_args(argv)
    try:
        checkpoint = Checkpoint(args.workflow, args.state)
        client = DecisionService.from_env()
        if args.command == "apply":
            with args.binding.open("rb") as stream:
                require_private(stream)
                binding = json.load(stream)
            result = apply(checkpoint, client, args.unit_id, binding, json.loads(args.selection.read_bytes()))
        else:
            require(bool(args.rollout) == bool(args.turn_id), "supply both the rollout and its original turn ID")
            result = reconcile_model(checkpoint, client, args.request_id, rollout=args.rollout, turn_id=args.turn_id)
        print(encoded(result))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
