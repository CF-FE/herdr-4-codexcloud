"""Opt-in Decision Service adapter for a project-owned workflow checkpoint."""
import argparse
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from decision_service_client import DecisionService, DecisionServiceError
from project_workflow_template import decision_state_digest, digest, encoded, read_run, require, save_run


MODEL_FIELDS = ("provider", "model_type", "model", "reasoning_effort")
UNKNOWN_MODEL = dict.fromkeys(MODEL_FIELDS) | {"configuration_status": "unconfirmed", "effort_status": "unconfirmed"}


class Checkpoint:
    def __init__(self, workflow, state):
        self.workflow = Path(workflow).resolve(strict=True)
        self.state = Path(state).resolve(strict=True)
        require(self.workflow.name == "workflow.py" and self.workflow.parent.parent.name == "workflows",
                "use the original generated workflow.py")
        require(not self.state.is_relative_to(self.workflow.parents[2]), "use the original private checkpoint")
        sources = (self.workflow, self.workflow.with_name("definition.py"))
        self.version = digest({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources})

    def native(self, *args):
        env = {key: value for key, value in os.environ.items() if key != "DECISION_SERVICE_TOKEN"}
        result = subprocess.run(
            [sys.executable, str(self.workflow), "--state", str(self.state), *args],
            capture_output=True, text=True, env=env,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or f"workflow exited with status {result.returncode}")
        return json.loads(result.stdout)

    @contextmanager
    def open(self, *, write=False):
        mode = "rw" if write else "ro"
        with closing(sqlite3.connect(self.state.as_uri() + "?mode=" + mode, uri=True, timeout=30)) as db, db:
            if write:
                db.execute("BEGIN IMMEDIATE")
            run = read_run(db, self.version)
            yield run
            if write:
                save_run(db, run)


def integration(run):
    return run.setdefault("decision_integration", {"requests": {}, "actions": {}, "results": {}, "events": {}})


def subject(run):
    ref = {"task_id": run["task"], "workflow_id": run["workflow"], "step": run["step"],
           "source_sha256": run["source_sha256"], "input_sha256": run["input_sha256"]}
    return dict(ref, unit_id=digest([run["project"], ref]), unit_id_source="adapter")


def freeze(run, preset, unit, question, choices, context, recall="auto", bank_ids=None):
    state_digest = decision_state_digest(run)
    request = {"schema_version": 1, "preset": preset, "kind": "choice", "expects_application": True,
               "project_id": run["project"], "subject_ref": unit,
               "snapshot": {"state_digest": state_digest, "candidate_digest": digest(choices)},
               "question": question, "choices": choices, "context": context, "recall": recall}
    if bank_ids is not None:
        request["bank_ids"] = bank_ids
    request_id = "herdr-" + digest(request)
    request["request_id"] = request_id
    return integration(run)["requests"].setdefault(request_id, {"request": request})


def resolve(checkpoint, client, request_id):
    with checkpoint.open() as run:
        entry = integration(run)["requests"][request_id]
        if "decision" in entry:
            return entry["decision"]
        request = entry["request"]
    try:
        record = client.get(request_id=request_id)
    except DecisionServiceError as error:
        if error.code != "not_found" or error.status != 404:
            raise
        try:
            record = client.evaluate(request)
        except DecisionServiceError as failure:
            if failure.code not in {"transport_error", "request_in_progress", "invalid_service_response"}:
                raise
            record = client.get(request_id=request_id)
    require(type(record.get("schema_version")) is int and record["schema_version"] == 1
            and all(record.get(key) == request[key] for key in ("request_id", "project_id", "subject_ref", "snapshot"))
            and record.get("candidate_digest") == request["snapshot"]["candidate_digest"]
            and isinstance(record.get("input_digest"), str) and re.fullmatch(r"[a-f0-9]{64}", record["input_digest"])
            and isinstance(record.get("decision_id"), str) and record["decision_id"].strip()
            and record.get("status") in ("complete", "processing", "unknown"),
            "decision record does not match the frozen request")
    if record["status"] != "complete":
        return record
    candidate = next((choice for choice in request["choices"]
                      if choice["candidate_id"] == record.get("candidate_id")), None)
    if record.get("disposition") == "suggested":
        require(candidate is not None and candidate.get("metadata", {}).get("executable", True)
                and candidate["parameters"] == record.get("parameters"), "suggested candidate does not match the snapshot")
    else:
        require(record.get("disposition") in ("abstain", "needs_review", "needs_replan", "unavailable"),
                "unknown decision disposition")
    decision = {key: record[key] for key in ("decision_id", "status", "disposition", "input_digest", "candidate_digest")}
    decision["candidate_id"] = record.get("candidate_id")
    decision["evidence"] = "decision://" + record["decision_id"]
    with checkpoint.open(write=True) as run:
        saved = integration(run)["requests"][request_id].setdefault("decision", decision)
        require(saved == decision, "decision record changed for the original request")
    return decision


def choose(checkpoint, client, *, context=None, recall="auto", bank_ids=None, request_id=None):
    status = checkpoint.native("status")
    require(status.get("decision_integration_version") == 1,
            "use a new workflow generated with Decision Service integration")
    with checkpoint.open(write=True) as run:
        if request_id is None:
            require(run["status"] == status["status"] == "WAITING_DECISION" and run["step"] == status["step"]
                    and run["decision"] == status["decision"], "no current workflow decision")
            original = run["decision"]
            choices = [{"candidate_id": choice, "description": f"{choice} → {target}",
                        "parameters": {"choice": choice, "next_step": target}}
                       for choice, target in sorted(original["choices"].items())]
            replan = "needs-replan"
            while replan in original["choices"]:
                replan += "-fallback"
            choices.append({"candidate_id": replan, "description": "None of the allowed actions fit",
                            "parameters": {}, "metadata": {"executable": False, "disposition": "needs_replan"}})
            entry = freeze(run, "workflow_next_action", subject(run), original["question"], choices,
                           {} if context is None else context, recall, bank_ids)
            request_id = entry["request"]["request_id"]
        else:
            entry = integration(run)["requests"][request_id]
            require(entry["request"]["preset"] == "workflow_next_action", "request is not a workflow decision")
    decision = resolve(checkpoint, client, request_id)
    response = {"request_id": request_id, "unit_id": entry["request"]["subject_ref"]["unit_id"],
                "decision_id": decision["decision_id"], "status": decision["status"]}
    if decision["status"] != "complete":
        return response
    response["disposition"] = decision["disposition"]
    request = entry["request"]
    step = request["subject_ref"]["step"]
    expected = request["snapshot"]["state_digest"]
    error = None
    if decision["disposition"] == "suggested":
        with checkpoint.open() as run:
            eligible = decision_state_digest(run) == expected
        if eligible:
            try:
                checkpoint.native("decide", "--step", step, "--choice", decision["candidate_id"],
                                  "--evidence", decision["evidence"], "--expected-state-digest", expected)
            except ValueError as failure:
                error = str(failure)
    # Read back even when native decide returned an error after its commit.
    checkpoint.native("status")
    response["feedback"] = reconcile(checkpoint, client)
    with checkpoint.open() as run:
        current = integration(run)["requests"][request_id]
        response["application_status"] = current["application_status"]
    if error is not None:
        response["workflow_error"] = error
    return response


def observe_unit(run, data, unit):
    """Use durable native handoff receipts, or a completed local workflow segment."""
    step = unit["step"]
    sequence = run["completed_steps"] + [run["step"]]
    if step not in sequence:
        return "unknown", None, None
    after = sequence[sequence.index(step):]
    # The first handoff closes this unit. A later decision begins a different unit.
    for current in after:
        active = run["action"]
        action = data["actions"].get(current) or (active if active and active["step"] == current else None)
        if action:
            ref = {"source": "herdr", "ids": {"action_id": action["id"]}}
            collected = next((r for r in run["receipts"].get(action["id"], []) if r["status"] == "COLLECTED"), None)
            if collected:
                return "ended", ref, [collected["evidence"]]
            return "unknown" if run["status"] == "UNKNOWN" else "running", ref, None
        if current != step and (current in run["decisions"] or
                                (current == run["step"] and run["status"] == "WAITING_DECISION")):
            return "ended", None, []
    if run["status"] == "FINISHED":
        return "ended", None, []
    if run["status"] == "WAITING_DECISION" and run["step"] == step:
        return "not_started", None, None
    return "unknown", None, None


def handoff_unit(run, data):
    """Use the adopting workflow decision's unit if it led to this handoff."""
    action = run["action"]
    for entry in data["requests"].values():
        request, decision = entry["request"], entry.get("decision")
        if request["preset"] != "workflow_next_action" or decision is None:
            continue
        unit = request["subject_ref"]
        if run["decisions"].get(unit["step"]) != {"choice": decision["candidate_id"], "evidence": decision["evidence"]}:
            continue
        _, ref, _ = observe_unit(run, data, unit)
        if ref and ref["ids"]["action_id"] == action["id"]:
            return unit
    return subject(run)


def queue_event(run, data, *, kind, key, unit, decision_ids, state, execution_ref, observation, payload, observed_at):
    event_id = key + "." + kind
    facts = {"schema_version": 1, "project_id": run["project"], "event_id": event_id, "kind": kind,
             "decision_ids": sorted(decision_ids), "subject_ref": unit, "execution_state": state,
             "coverage_observation": {"scope_ref": unit["task_id"], "scope_complete": False, "units": [observation]},
             "payload": payload}
    if execution_ref is not None:
        facts["execution_ref"] = execution_ref
    history = data["events"].setdefault(event_id, [])
    if history and history[-1]["facts"] == facts:
        return
    revision = len(history) + 1
    event = dict(facts, revision=revision, observed_at=observed_at,
                 coverage_observation=dict(facts["coverage_observation"], observed_at=observed_at))
    if revision > 1:
        event["supersedes_revision"] = revision - 1
    history.append({"facts": deepcopy(facts), "event": deepcopy(event)})


def model_application(entry, original_launch=None):
    request, decision = entry["request"], entry["decision"]
    selected = next((choice["parameters"] for choice in request["choices"]
                     if choice["candidate_id"] == decision["candidate_id"]), None)
    if original_launch and original_launch["request_id"] != request["request_id"]:
        status, reason = "not_applied", "original_launch_bound"
    elif decision["disposition"] != "suggested":
        status, reason = "not_applied", decision["disposition"]
    else:
        status, reason = "unknown", "launch_intent_not_recorded"
    return {"recommended_model": selected, "launch_model": None, "effective_model": UNKNOWN_MODEL,
            "application_status": status, "reason": reason}


def reconcile(checkpoint, client):
    """Reconstruct and resend feedback from the checkpoint without advancing it."""
    with checkpoint.open() as run:
        if "decision_integration" not in run:
            return {"received": 0, "pending": []}
    with checkpoint.open(write=True) as run:
        data = integration(run)
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        units = {}
        for entry in data["requests"].values():
            decision = entry.get("decision")
            if decision is None:
                continue
            request = entry["request"]
            unit = request["subject_ref"]
            step = unit["step"]
            selected = decision["candidate_id"]
            if request["preset"] == "workflow_next_action":
                recorded = run["decisions"].get(step)
                if decision["disposition"] != "suggested":
                    status, reason = "not_applied", decision["disposition"]
                elif recorded == {"choice": selected, "evidence": decision["evidence"]}:
                    status, reason = "applied", None
                elif decision_state_digest(run) != request["snapshot"]["state_digest"]:
                    status, reason = "stale", "snapshot_changed"
                else:
                    status, reason = "unknown", "choice_not_recorded"
                model_fields = {}
                effective_id = selected if status == "applied" else None
                participates = status == "applied"
            else:
                model = entry.get("model") or model_application(entry, data.get("launches", {}).get(unit["unit_id"]))
                status, reason = model["application_status"], model["reason"]
                model_fields = {key: model[key] for key in ("recommended_model", "launch_model", "effective_model")}
                effective_id = model.get("effective_candidate_id")
                participates = model["launch_model"] is not None
            entry["application_status"] = status
            payload = {"application_status": status, "recommended_candidate_id": selected,
                       "effective_candidate_id": effective_id,
                       "reason_codes": [reason] if reason else [], "evidence_refs": [decision["evidence"]],
                       **model_fields}
            group = units.setdefault(unit["unit_id"], {"unit": unit, "applications": [], "model": None})
            group["applications"].append((decision["decision_id"], participates, payload))
            if model_fields and participates:
                group["model"] = model
        for unit_id, group in units.items():
            unit = group["unit"]
            state, execution_ref, refs = observe_unit(run, data, unit)
            model = group["model"]
            if model and model.get("execution_ref"):
                execution_ref = {"source": "herdr", "ids": {
                    **(execution_ref or {}).get("ids", {}), **model["execution_ref"]["ids"],
                }}
            outcome = data["results"].get(unit_id, {})
            if "execution_status" in outcome:
                state = "ended"
            participants = [key for key, active, _ in group["applications"] if active]
            observation = {"unit_id": unit_id, "state": "pending" if state in {"running", "not_started"} else state,
                           "expects_result": bool(participants)}
            if execution_ref is not None:
                observation["execution_ref"] = execution_ref
            for key, _, payload in group["applications"]:
                # A declined suggestion has its own application status, while
                # coverage describes the original work unit shared by all decisions.
                application_state = state
                if application_state == "running" and (not model or not model.get("execution_ref", {}).get("ids", {}).get("turn_id")):
                    application_state = "unknown"
                queue_event(run, data, kind="application", key=key, unit=unit, decision_ids=[key],
                            state=application_state, execution_ref=execution_ref,
                            observation=observation, payload=payload, observed_at=now)
            if participants and state == "ended":
                # A handoff receipt confirms collection. Only an explicit
                # outcome can establish how the external execution ended.
                payload = {"execution_status": "unknown" if "action_id" in (execution_ref or {}).get("ids", {})
                           else "completed",
                           "quality_status": "pending", "result_refs": refs or [],
                           "evidence_refs": [], **outcome}
                if model:
                    payload["effective_model"] = model["effective_model"]
                queue_event(run, data, kind="result", key=unit_id, unit=unit, decision_ids=participants,
                            state="ended", execution_ref=execution_ref, observation=observation,
                            payload=payload, observed_at=now)
        pending = [item["event"] for history in data["events"].values()
                   for item in history if "receipt_id" not in item]
    errors = []
    received = 0
    for event in pending:
        try:
            receipt = client.feedback(event)
        except DecisionServiceError as failure:
            errors.append({"event_id": event["event_id"], "revision": event["revision"], "code": failure.code})
            continue
        with checkpoint.open(write=True) as run:
            integration(run)["events"][event["event_id"]][event["revision"] - 1]["receipt_id"] = receipt["receipt_id"]
        received += 1
    return {"received": received, "pending": errors}


def update_quality(checkpoint, unit_id, quality_status, evidence_refs):
    require(quality_status in {"pending", "unknown", "passed", "failed"}, "invalid quality status")
    require(quality_status in {"pending", "unknown"} or evidence_refs, "quality conclusion needs source evidence")
    with checkpoint.open(write=True) as run:
        data = integration(run)
        previous = data["events"].get(unit_id + ".result")
        require(previous, "no terminal unit result to revise; reconcile first")
        data["results"].setdefault(unit_id, {}).update(quality_status=quality_status, evidence_refs=evidence_refs)


def record_result(checkpoint, unit_id, execution_status, result_refs, evidence_refs):
    require(execution_status in {"completed", "failed", "cancelled"} and evidence_refs,
            "a terminal outcome needs original source evidence")
    with checkpoint.open(write=True) as run:
        data = integration(run)
        require(any(entry["request"]["subject_ref"]["unit_id"] == unit_id and (
            entry.get("application_status") == "applied" or entry.get("model", {}).get("launch_model") is not None
        ) for entry in data["requests"].values()), "no adopted route or recorded launch for this unit")
        data["results"].setdefault(unit_id, {}).update(
            execution_status=execution_status, result_refs=result_refs, evidence_refs=evidence_refs)


def add_checkpoint_arguments(parser):
    parser.add_argument("--workflow", required=True, help="original PROJECT/workflows/<id>/workflow.py")
    parser.add_argument("--state", required=True, help="original private SQLite checkpoint")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_checkpoint_arguments(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    apply = commands.add_parser("choose", help="evaluate and apply an authorized non-approval workflow choice")
    apply.add_argument("--request-id", help="recover a previously frozen request")
    apply.add_argument("--context", type=Path, help="minimal, approved JSON context object")
    apply.add_argument("--recall", choices=("auto", "skip"), default="auto")
    apply.add_argument("--bank", action="append")
    commands.add_parser("resume", help="resume the original workflow and then reconcile feedback")
    receive = commands.add_parser("receive", help="import the original handoff receipt and reconcile feedback")
    receive.add_argument("--action", required=True)
    receive.add_argument("--status", choices=("UNKNOWN", "COLLECTED"), required=True)
    receive.add_argument("--evidence", required=True)
    commands.add_parser("reconcile", help="rebuild and resend feedback without advancing the workflow")
    quality = commands.add_parser("quality", help="revise an existing unit result from source evidence")
    quality.add_argument("--unit-id", required=True)
    quality.add_argument("--status", required=True, choices=("pending", "unknown", "passed", "failed"))
    quality.add_argument("--evidence", action="append", default=[])
    result = commands.add_parser("record-result", help="import an already observed terminal unit result")
    result.add_argument("--unit-id", required=True)
    result.add_argument("--status", required=True, choices=("completed", "failed", "cancelled"))
    result.add_argument("--result-ref", action="append", default=[])
    result.add_argument("--evidence", action="append", required=True)
    commands.add_parser("local-status", help="read checkpoint associations without sending feedback")
    get = commands.add_parser("get", help="read the service record without advancing or sending feedback")
    key = get.add_mutually_exclusive_group(required=True)
    key.add_argument("--request-id")
    key.add_argument("--decision-id")
    args = parser.parse_args(argv)
    try:
        checkpoint = Checkpoint(args.workflow, args.state)
        if args.command == "local-status":
            with checkpoint.open() as run:
                data = integration(run)
                output = {"handoff_unit": handoff_unit(run, data) if run["status"] == "WAITING_RECEIPT" else None,
                          "requests": [{"request_id": key, "subject_ref": entry["request"]["subject_ref"],
                                        "decision": entry.get("decision"),
                                        "application_status": entry.get("application_status")}
                                       for key, entry in data["requests"].items()],
                          "pending": sum("receipt_id" not in item for rows in data["events"].values() for item in rows)}
        else:
            client = DecisionService.from_env()
            if args.command == "choose":
                context = json.loads(args.context.read_bytes()) if args.context else {}
                require(isinstance(context, dict), "context must be a JSON object")
                require(not args.request_id or (not args.context and args.recall == "auto" and args.bank is None),
                        "recover a request with its frozen context and recall settings")
                output = choose(checkpoint, client, context=context, recall=args.recall, bank_ids=args.bank,
                                request_id=args.request_id)
            elif args.command == "get":
                output = client.get(decision_id=args.decision_id, request_id=args.request_id)
            elif args.command == "quality":
                update_quality(checkpoint, args.unit_id, args.status, args.evidence)
                output = reconcile(checkpoint, client)
            elif args.command == "record-result":
                record_result(checkpoint, args.unit_id, args.status, args.result_ref, args.evidence)
                output = reconcile(checkpoint, client)
            elif args.command == "reconcile":
                output = reconcile(checkpoint, client)
            else:
                native_args = [args.command]
                if args.command == "receive":
                    native_args.extend(("--action", args.action, "--status", args.status, "--evidence", args.evidence))
                try:
                    output = {"workflow": checkpoint.native(*native_args)}
                except ValueError as error:
                    output = {"workflow_error": str(error)}
                output["feedback"] = reconcile(checkpoint, client)
        print(encoded(output))
    except (ValueError, OSError, KeyError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    return int("workflow_error" in output)


if __name__ == "__main__":
    raise SystemExit(main())
