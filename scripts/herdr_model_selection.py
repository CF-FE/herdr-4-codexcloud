"""Select and apply a Mac Codex configuration for an authorized new handoff unit."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from allowlists import DEFAULT_FILE, DEFAULT_ROOT, LibraryError, read_active_agent_policy
from herdr_agent_creation import (LOCATION, MAC_ROUTE, HostError, check_empty, check_policy_at_start,
                                  mac_agent_process, mac_launch_argv, mac_shell_process, matching_candidates,
                                  native, non_model_args, read_agent_thread, require_private, start_agent)
from decision_service_client import DecisionService
from project_workflow_decision import (MODEL_FIELDS, Checkpoint, add_checkpoint_arguments, freeze, handoff_unit,
                                       integration, model_application, reconcile, resolve)
from project_workflow_template import decision_state_digest, digest, encoded, require


def candidates(selection, provider, policy, policy_ref, library):
    return matching_candidates(selection, dict(MAC_ROUTE, provider=provider), policy, policy_ref, library)


def apply(checkpoint, client, unit_id, binding, selection, *, host=native, policy_path=DEFAULT_FILE):
    require(binding["mode"] == "new-unit", "this adapter creates an authorized new-unit agent")
    target = {key: binding[key] for key in ("session", "agent", "cwd", *LOCATION)}
    require(all(isinstance(value, str) and value.strip() and "\0" not in value for value in target.values())
            and Path(target["cwd"]).is_absolute() and isinstance(binding["authorization_ref"], str)
            and binding["authorization_ref"].strip() and isinstance(binding["provider"], str)
            and binding["provider"].strip(), "an approved native target, provider and authorization are required")
    require(all(not value.startswith("-") for key, value in target.items() if key != "cwd"),
            "invalid native target")

    def target_host(*command):
        return host(target["session"], *command)

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
            base_args = non_model_args(binding["codex_args"], target["cwd"], binding["provider"])
            try:
                policy, policy_ref, library = read_active_agent_policy(policy_path, DEFAULT_ROOT)
            except LibraryError as error:
                return {"status": "policy_blocked", "disposition": "unavailable",
                        "reason": error.code, "launch_outcome": "not_started"}
            choices = candidates(selection, binding["provider"], policy, policy_ref, library)
            if not choices:
                return {"status": "unavailable", "disposition": "unavailable", "launch_outcome": "not_started",
                        "reason": "no_eligible_mac_codex_configuration", "model_policy": policy_ref}
            context = selection.get("context", {})
            require(isinstance(context, dict), "selection context must be an object")
            entry = freeze(run, "role_model_selection", unit, "Which eligible Mac Codex configuration fits this unit?",
                           choices, context, selection.get("recall", "auto"), selection.get("bank_ids"))
            request_id = entry["request"]["request_id"]
            require(entry.setdefault("binding_digest", binding_digest) == binding_digest,
                    "frozen model request belongs to another approved binding")
            require(entry.setdefault("model_policy", policy_ref) == policy_ref,
                    "model allowlist differs from the frozen request")
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

    argv = mac_launch_argv(base_args, selected)
    # Inspect the native target outside SQLite. The transaction then fences a
    # concurrent workflow transition or a second caller of this creation path.
    try:
        check_empty(target, target_host, mac_shell_process)
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
            elif not check_policy_at_start(policy_path, selection, dict(MAC_ROUTE, provider=binding["provider"]),
                                           entry["model_policy"], selected):
                model.update(application_status="stale", reason="agent_model_policy_changed")
            if model["application_status"] != "stale":
                require(not any(prior["target"]["session"] == target["session"] and
                                (prior["target"]["pane_id"] == target["pane_id"] or
                                 prior["target"]["agent"] == target["agent"]) for prior in data["launches"].values()),
                        "target already has an original launch intent in this checkpoint")
                data["launches"][unit_id] = {"request_id": request_id, "binding_digest": binding_digest,
                                            "target": target, "argv_digest": digest(argv), "outcome": "unknown",
                                            "model_policy": entry["model_policy"]}
                model.update(launch_model=selected, application_status="unknown", reason="launch_unconfirmed")
            entry["model"] = model
    if existing is not None:
        return reconcile_model(checkpoint, client, existing["request_id"], host=host)
    if model["launch_model"] is None:
        return {"request_id": request_id, "decision_id": decision["decision_id"], "status": "stale",
                "reason": model["reason"], "launch_outcome": "not_started", "model_policy": policy_ref,
                "feedback": reconcile(checkpoint, client)}
    if start_agent(target, target_host, "codex", argv) == "ready_acknowledged":
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
    def target_host(*command):
        return host(target["session"], *command)

    recorded_thread = launch.get("thread_id")
    thread_id = read_agent_thread(target, target_host, "codex", mac_agent_process, launch["argv_digest"], recorded_thread)
    observed = None
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
        if "model_policy" in launch:
            output["model_policy"] = launch["model_policy"]
    output["feedback"] = reconcile(checkpoint, client)
    return output


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
    return 1 if result.get("status") in {"policy_blocked", "stale"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
