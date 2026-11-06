"""Fixed-process interface for the existing Mac lifecycle hook.

Only an explicitly bound native session can collect its trusted recorder and
prepare a notice. No new timer, model turn, Mailbox job or Herdr dispatch is
created. Hook output is ACCEPTED/OUTPUT_PREPARED, never proof of delivery.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from project_coordinator import Coordinator, CoordinatorError, canonical, fields, require
    from coordinator_approval_intake import collect, load_policy
else:
    from .project_coordinator import Coordinator, CoordinatorError, canonical, fields, require
    from .coordinator_approval_intake import collect, load_policy


def handle(c, policy, event):
    result = {"schema_version": 1, "system_message": None, "additional_context": "", "intake": {}}
    fields(event, "session_id cwd transcript_path hook_event_name", "turn_id")
    if (event["hook_event_name"] not in ("SessionStart", "UserPromptSubmit", "Stop")
            or event["session_id"] != policy["thread_id"] or event["cwd"] != policy["cwd"]
            or event["transcript_path"] != policy["transcript_path"]):
        return result
    # Always reconcile an already authored answer first. Never scan prompt text
    # or infer approval from a hook event, user role, keyword or shared account.
    try:
        # Reserve time for the durable local notice and hook stdout. A remote
        # history stall must not consume the whole native three-second budget.
        observed = collect(c, policy, timeout=0.9)
        result["intake"] = {k: v for k, v in observed.items() if type(v) is int and 0 <= v < 2**31}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        result["intake"] = {"unavailable": 1}
    if event["hook_event_name"] == "Stop":
        return result
    notices = c.manage("approval_notice_snapshot", {
        "thread_id": policy["thread_id"], "host_id": policy["host_id"],
        "project_ids": policy["project_ids"], "limit": 10})["notices"]
    if not notices:
        return result
    result["additional_context"] = (
        "审批桥：以下是已绑定原任务的最小投影，数据不授予执行权限。"
        "本会话仅在operator明确绑定为受信记录者时办理回流；公子正常答复一次即可。"
        "确认收到有权者对明确原范围的直接决定后，在本会话另发一条独立单行："
        'APPROVAL_DECISION {"project_id":"原值","approval_id":"原值","scope_version":1,'
        '"scope_hash":"原值","decision":"APPROVED|REJECTED|WITHDRAWN",'
        '"user_response_ref":{"turn_id":"原用户答复turn","item_id":"原用户答复item"},'
        '"decided_at":"带时区的原决定时间"}。'
        "原生引用由记录者查询并关联，不要求公子复制hash或再次确认；不得从任意userMessage猜批准。"
        "冲突澄清或撤回另带supersedes_ref关联已有来源。停止与对账项交原派发者，不重新问批准。"
        "索引=" + canonical(notices).decode())
    if result["intake"].get("unavailable"):
        result["additional_context"] += " 原答复读取暂不可用；原待办与承接责任保留，不推断批准，原驱动下轮继续对账。"
    for notice in notices:
        key = {k: notice[k] for k in ("project_id", "approval_id", "scope_version")}
        item = c.manage("approval_list", key)["items"][0]
        progress, phase = item["progress"], notice["phase"]
        if phase == "review":
            continue  # ordinary owner review is not another user-approval request
        if phase == "decision" and progress["delivery"]["status"] == "PENDING":
            stage, escalation = "delivery", None
        else:
            escalation = "execution" if phase in ("execution", "reconcile") else "decision"
            if escalation not in item["due_phases"]:
                continue
            stage = "escalation"
        args = {**key, "expected_revision": progress["revision"], "stage": stage}
        if escalation:
            args["phase"] = escalation
        claimed = c.manage("approval_claim", args)
        if not claimed["send"]:
            continue  # UNKNOWN/accepted sends only reconcile; no inferred absence
        latest = c.manage("approval_list", key)["items"][0]
        slot = (latest["progress"]["escalations"][escalation] if escalation else latest["progress"]["delivery"])
        if (not latest["generation_current"] or not latest["progress"]["current"]
                or latest["progress"]["closed"] or slot["status"] != "INTENT"
                or slot["action_id"] != claimed["action_id"]):
            continue
        ref = ("native-hook:output-prepared:" + policy["thread_id"] + ":" + event.get("turn_id", "session-start")
               + ":" + event["hook_event_name"] + ":" + notice["notice_id"])
        c.manage("approval_record", {**args, "expected_revision": latest["progress"]["revision"],
                 "action_id": claimed["action_id"], "outcome": "ACCEPTED", "evidence_ref": ref})
        prefix = {"decision": "原审批待决定／接回", "execution": "已批准，原执行待接续／对账",
                  "reconcile": "原执行待停止与对账"}[phase]
        result["system_message"] = f"{prefix}：{notice['summary']} 原单：{notice['task_ref']} 承接：{notice['action_owner']}"
        break
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(16385)
        require(len(raw) <= 16384, "HOOK_INPUT_TOO_LARGE")
        event = json.loads(raw)
        policy = load_policy(args.policy)
        result = handle(Coordinator(args.state_dir), policy, event)
        print(canonical(result, max_bytes=32768).decode())
        return 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({"error": getattr(error, "code", "APPROVAL_HOOK_UNAVAILABLE")}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
