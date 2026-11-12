"""Prepare or submit one authorized contact to the locally bound project coordinator."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # fixed release siblings under python -I
    from coordinator_comment_intake import decode, inbox_directory, read_file, write_atomically
    from coordinator_followup_wake import NATIVE_ERRORS, check_driver, check_identity, read_binding, ready, ready_agent
    from herdr_role_contact import HERDR, ROOT, NativeCommandError, native
    from project_coordinator import Coordinator, CoordinatorError, canonical, canonical_hash, fields, identifier, require, text
else:
    from .coordinator_comment_intake import decode, inbox_directory, read_file, write_atomically
    from .coordinator_followup_wake import NATIVE_ERRORS, check_driver, check_identity, read_binding, ready, ready_agent
    from .herdr_role_contact import HERDR, ROOT, NativeCommandError, native
    from .project_coordinator import Coordinator, CoordinatorError, canonical, canonical_hash, fields, identifier, require, text


def action_key(task_ref, action_id):
    require(re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/issues/[1-9][0-9]*", text(task_ref),
                         re.ASCII), "TASK_ISSUE_REQUIRED")
    return "directed-contact-" + canonical_hash([task_ref, identifier(action_id)])


def read_intent(path):
    with inbox_directory(path.parent) as folder:
        intent = decode(read_file(path.name, folder))
        fields(intent, "version project_id task_ref handoff_ref action_id generation driver_pane "
                       "driver_terminal_id target_terminal_id prompt_file prompt_sha256")
        require(type(intent["version"]) is int and intent["version"] == 1
                and type(intent["generation"]) is int and intent["generation"] >= 0, "INVALID_INTENT")
        for name in ("project_id", "handoff_ref", "driver_pane", "driver_terminal_id", "target_terminal_id",
                     "prompt_file"):
            identifier(intent[name])
        key = action_key(intent["task_ref"], intent["action_id"])
        raw = read_file(intent["prompt_file"], folder)
    require(hashlib.sha256(raw).hexdigest() == intent["prompt_sha256"], "PROMPT_HASH_MISMATCH")
    body = text(raw.decode("utf-8"), 32768)
    prompt = (f'HANDOFF_REF={intent["handoff_ref"]}\nCONTACT_ACTION_ID={intent["action_id"]}\n'
              f'TASK_REF={intent["task_ref"]}\n\n{body}')
    return intent, prompt, key


def contact_plan(root, intent):
    project = Coordinator(root / "state", read_only=True).route(intent["project_id"])
    require(re.fullmatch(r"https://github\.com/" + re.escape(project["repository"]) + r"/issues/[1-9][0-9]*",
                         intent["task_ref"]), "TASK_PROJECT_MISMATCH")
    require(project["generation"] == intent["generation"], "STALE_GENERATION")
    binding = read_binding(root)
    require(binding["pane"] != intent["driver_pane"] and intent["driver_terminal_id"] != intent["target_terminal_id"],
            "SEPARATE_DRIVER_REQUIRED")
    argv = [str(HERDR), "--session", binding["session"], "terminal", "attach", intent["driver_terminal_id"]]
    plan = {"state": "PLAN_ONLY", "project_id": intent["project_id"], "task_ref": intent["task_ref"],
            "action_id": intent["action_id"], "driver_pane": intent["driver_pane"], "attach_argv": argv,
            "attach_command": shlex.join(argv),
            "plan_sha256": canonical_hash({"intent": intent, "binding": binding})}
    return plan, binding


def read_record(folder, key, task_ref, action_id):
    try:
        record = decode(read_file(key + ".json", folder))
    except FileNotFoundError:
        return None
    scope = record.get("scope")
    require(record.get("version") == 1 and record.get("state") == "UNKNOWN"
            and isinstance(scope, dict) and scope.get("task_ref") == task_ref and scope.get("action_id") == action_id,
            "INVALID_CONTACT_RECORD")
    try:
        receipt = decode(read_file(key + ".receipt.json", folder))
    except FileNotFoundError:
        return record
    fields(receipt, "intent_sha256 state reason recorded_at", "native_receipt_sha256")
    require(receipt["intent_sha256"] == record["intent_sha256"]
            and receipt["state"] in ("UNKNOWN", "SUBMITTED", "NOT_SENT"), "INVALID_CONTACT_RECEIPT")
    return {**record, **receipt}


def status(task_ref, action_id, *, root=ROOT):
    with inbox_directory(root / "state") as folder:
        record = read_record(folder, action_key(task_ref, action_id), task_ref, action_id)
    return record if record is not None else {"state": "NO_RECORD", "task_ref": task_ref, "action_id": action_id}


def prompt_once(binding, terminal_id, prompt, query):
    # Recheck the live agent after the reservation's disk sync. Herdr takes only a pane target in the prompt request.
    try:
        agent = ready_agent(binding, query)
        require(agent.get("terminal_id") == terminal_id, "TARGET_TERMINAL_MISMATCH")
    except NATIVE_ERRORS as error:
        reason = error.code if isinstance(error, CoordinatorError) else "FINAL_TARGET_UNCONFIRMED"
        return {"state": "NOT_SENT", "reason": reason}

    try:
        reply = query(binding["session"], "agent", "prompt", binding["pane"], prompt)
        require(reply.get("type") == "agent_prompted", "UNCONFIRMED_SUBMISSION")
        check_identity(reply["agent"], binding)
        require(reply["agent"].get("terminal_id") == terminal_id, "TARGET_TERMINAL_MISMATCH")
        return {"state": "SUBMITTED", "reason": "agent_prompted", "native_receipt_sha256": canonical_hash(reply)}
    except NativeCommandError as error:
        if error.not_sent and error.code == "agent_blocked":
            return {"state": "NOT_SENT", "reason": "agent_blocked"}
        return {"state": "UNKNOWN", "reason": "NATIVE_PROMPT_UNCONFIRMED"}
    except NATIVE_ERRORS as error:
        reason = error.code if isinstance(error, CoordinatorError) else "INVALID_NATIVE_RECEIPT"
        return {"state": "UNKNOWN", "reason": reason}


def send(path, plan_sha256, *, root=ROOT, query=native):
    intent, prompt, key = read_intent(path)
    intent_sha256 = canonical_hash(intent)
    with inbox_directory(root / "state") as folder:
        # Scope the lock to explicit contacts in this state directory, separate from the watcher lock file.
        try:
            fcntl.flock(folder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CoordinatorError("DIRECTED_CONTACT_BUSY") from None

        prior = read_record(folder, key, intent["task_ref"], intent["action_id"])
        if prior is not None:
            require(prior.get("intent_sha256") == intent_sha256 and prior.get("plan_sha256") == plan_sha256,
                    "CONTACT_INTENT_CONFLICT")
            return {**prior, "duplicate": True}

        plan, binding = contact_plan(root, intent)
        require(plan["plan_sha256"] == plan_sha256, "CONTACT_PLAN_CHANGED")
        driver_pane = intent["driver_pane"]
        require(os.environ.get("HERDR_PANE_ID") == driver_pane, "NATIVE_DRIVER_REQUIRED")
        check_driver(binding, query)
        session = binding["session"]
        driver = query(session, "pane", "get", driver_pane)["pane"]
        require(driver.get("pane_id") == driver_pane and driver.get("terminal_id") == intent["driver_terminal_id"],
                "DRIVER_TERMINAL_MISMATCH")
        target = ready(binding, query)
        require(target.get("terminal_id") == intent["target_terminal_id"], "TARGET_TERMINAL_MISMATCH")
        fresh_plan, _ = contact_plan(root, intent)
        require(fresh_plan["plan_sha256"] == plan_sha256, "CONTACT_PLAN_CHANGED")

        record = {"version": 1, "state": "UNKNOWN", "reason": "NATIVE_RECEIPT_UNCONFIRMED",
                  "scope": {key: intent[key] for key in ("project_id", "task_ref", "handoff_ref", "action_id", "generation")},
                  "intent_sha256": intent_sha256, "plan_sha256": plan_sha256,
                  "target": {"session": session, "pane_id": binding["pane"], "terminal_id": intent["target_terminal_id"],
                             "agent_session": binding["agent_session"]},
                  "reserved_at": datetime.now(timezone.utc).isoformat()}
        # The reservation remains UNKNOWN on interruption, including a lost reply or a failed receipt write.
        write_atomically(folder, key + ".json", canonical(record))
        receipt = prompt_once(binding, intent["target_terminal_id"], prompt, query)
        receipt.update(intent_sha256=intent_sha256, recorded_at=datetime.now(timezone.utc).isoformat())
        receipt_file = key + ".receipt.json"
        try:
            write_atomically(folder, receipt_file, canonical(receipt))
        except OSError:
            # Publication may have renamed the receipt before its directory sync failed.
            try:
                os.unlink(receipt_file, dir_fd=folder)
                os.fsync(folder)
            except FileNotFoundError:
                pass
            except OSError:
                return {**record, "reason": "RECEIPT_CLEANUP_UNCONFIRMED"}
            return {**record, "reason": "RECEIPT_PERSIST_FAILED"}
        return {**record, **receipt}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="print the local terminal attach command").add_argument("intent", type=Path)
    submit = commands.add_parser("send", help="submit once from the confirmed driver")
    submit.add_argument("intent", type=Path)
    submit.add_argument("--plan-sha256", required=True)
    lookup = commands.add_parser("status", help="read the original action's local contact receipt")
    lookup.add_argument("task_ref")
    lookup.add_argument("action_id")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            intent, _, _ = read_intent(args.intent)
            result, _ = contact_plan(ROOT, intent)
        elif args.command == "send":
            result = send(args.intent, args.plan_sha256)
        else:
            result = status(args.task_ref, args.action_id)
    except (CoordinatorError, NativeCommandError) as error:
        print(json.dumps({"state": "STOP", "reason": str(error)}))
        return 2
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError) as error:
        print(json.dumps({"state": "STOP", "reason": "CONTACT_INPUT_OR_PREFLIGHT_UNCONFIRMED",
                          "error_type": type(error).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 1 if args.command == "send" and result["state"] != "SUBMITTED" else 0


if __name__ == "__main__":
    sys.exit(main())
