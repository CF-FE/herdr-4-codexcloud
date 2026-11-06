"""Codex App decision-entry adapter; requires tools in the ACTUAL calling host.

This is not a background bridge to desktop tools. Herdr's existing driver does
not acquire App tools by importing this module. A host supplies call_tool only
after binding/authorizing this route; absent that capability we fail before
claiming or disabling any existing followup. Read-back proves entry delivery,
never authorship of a user decision.
"""

import json

if __package__:
    from .project_coordinator import canonical_hash, fields, require
    from .coordinator_approvals import receive_decision
else:
    from project_coordinator import canonical_hash, fields, require
    from coordinator_approvals import receive_decision


READ_TOOL = "mcp__codex_app__read_thread"
SEND_TOOL = "mcp__codex_app__send_message_to_thread"


def tool_data(response):
    require(isinstance(response, dict) and not response.get("isError"), "APPROVAL_ENTRY_UNCONFIRMED")
    if isinstance(response.get("structuredContent"), dict):
        return response["structuredContent"]
    content = response.get("content", [])
    require(len(content) == 1 and content[0].get("type") == "text", "APPROVAL_ENTRY_UNCONFIRMED")
    value = json.loads(content[0]["text"])
    require(isinstance(value, dict), "APPROVAL_ENTRY_UNCONFIRMED")
    return value


class CodexAppDecisionEntry:
    def __init__(self, *, call_tool=None, capability_check=None, decision_verifier=None):
        # These are trusted host functions, never selected/deserialized by a job.
        self.call_tool = call_tool
        self.capability_check = capability_check
        self.decision_verifier = decision_verifier

    def _available(self, spec):
        require(callable(self.call_tool) and callable(self.capability_check), "MISSING_COORDINATOR_DECISION_CHANNEL")
        require(self.capability_check(spec["route"]) is True, "MISSING_COORDINATOR_DECISION_CHANNEL")

    @staticmethod
    def _item(c, key):
        fields(key, "project_id approval_id scope_version")
        items = c.manage("approval_list", key)["items"]
        require(len(items) == 1, "APPROVAL_NOT_FOUND")
        return items[0]

    @staticmethod
    def _slot(item, stage, phase):
        return item["progress"]["escalations"].get(phase, {"status": "PENDING"}) if stage == "escalation" else item["progress"]["delivery"]

    @staticmethod
    def _prompt(item, stage, phase, action):
        spec = item["spec"]
        # Freeze only content explicitly prepared for the bound private decision entry.
        scope = json.dumps(spec["scope"], ensure_ascii=False, sort_keys=True, indent=2)
        prefix = "公子，原任务有一项需要新增授权的动作，请按正常方式答复。" if stage == "delivery" else (
            "公子，原审批事项已到约定升级期限，承接人需处理送达或接续缺口。")
        return (f"{prefix}\n\n原任务：{spec['task_ref']}\n审批范围：\n{scope}\n"
                f"新增授权原因：{spec['authorization_gap']}\n"
                f"跟进责任人：{spec['escalation_owner']}\n"
                f"<!-- coordinator-approval:{item['scope_hash']}:{stage}:{phase or '-'}:{action} -->")

    def query(self, c, key, *, stage="delivery", phase=None):
        """Bounded positive reconciliation. Absence is inconclusive and NEVER rearms sending."""
        require(stage in ("delivery", "escalation"), "INVALID_APPROVAL_STAGE")
        item = self._item(c, key)
        spec = item["spec"]
        self._available(spec)
        slot = self._slot(item, stage, phase)
        if slot["status"] == "DELIVERED" or not slot.get("action_id"):
            return item
        prompt = self._prompt(item, stage, phase, slot["action_id"])
        action_id = slot["action_id"]
        args = {"threadId": spec["route"]["thread_id"], "hostId": spec["route"]["host_id"],
                "turnLimit": 25, "includeOutputs": False, "maxOutputCharsPerItem": 32768}
        cursors = set()
        for _ in range(8):
            data = tool_data(self.call_tool(READ_TOOL, args))
            thread = data.get("thread", {})
            require(thread.get("id") == args["threadId"] and thread.get("hostId") == args["hostId"],
                    "APPROVAL_ENTRY_MISMATCH")
            for turn in data.get("turns", []):
                for message in turn.get("items", []):
                    # An exact native user-visible prompt is sufficient for delivery only.
                    if (message.get("type") == "userMessage" and message.get("content") == [{"type": "text", "text": prompt}]
                            and message.get("id") and turn.get("id")):
                        item = self._item(c, key)  # decision may have arrived while reading
                        slot = self._slot(item, stage, phase)
                        if slot["status"] == "DELIVERED":
                            return item
                        require(slot.get("action_id") == action_id, "ACTION_MISMATCH")
                        payload = {**key, "expected_revision": item["progress"]["revision"], "stage": stage,
                                   "action_id": slot["action_id"], "outcome": "DELIVERED",
                                   "evidence_ref": f"codex-app:{args['hostId']}:{args['threadId']}:{turn['id']}:{message['id']}"}
                        if phase is not None:
                            payload["phase"] = phase
                        return c.manage("approval_record", payload)
            page = data.get("page", {})
            if page.get("hasMore") is False:
                return item
            cursor = page.get("nextCursor")
            require(isinstance(cursor, str) and cursor and cursor not in cursors, "APPROVAL_ENTRY_HISTORY_INCOMPLETE")
            cursors.add(cursor)
            args["cursor"] = cursor
        return item  # bounded query; a future positive read may reconcile this original action

    def send(self, c, key, *, stage="delivery", phase=None):
        require(stage in ("delivery", "escalation"), "INVALID_APPROVAL_STAGE")
        item = self._item(c, key)
        self._available(item["spec"])  # no intent or legacy reminder changes when the host lacks tools
        payload = {**key, "expected_revision": item["progress"]["revision"], "stage": stage}
        if phase is not None:
            payload["phase"] = phase
        claim = c.manage("approval_claim", payload)
        if not claim["send"]:
            return self.query(c, key, stage=stage, phase=phase)
        latest = self._item(c, key)
        slot = self._slot(latest, stage, phase)
        if (not latest["generation_current"] or not latest["progress"]["current"]
                or latest["progress"]["closed"] or slot.get("action_id") != claim["action_id"]
                or slot["status"] != "INTENT"):
            return latest
        route = claim["spec"]["route"]
        prompt = self._prompt(claim, stage, phase, claim["action_id"])
        try:
            response = self.call_tool(SEND_TOOL, {"threadId": route["thread_id"], "hostId": route["host_id"], "prompt": prompt})
            require(isinstance(response, dict) and not response.get("isError"), "APPROVAL_ENTRY_UNCONFIRMED")
        except (OSError, TimeoutError, ValueError):
            # A transport error cannot establish absence. Do not resend after UNKNOWN.
            item = self._item(c, key)
            slot = self._slot(item, stage, phase)
            if slot["status"] == "INTENT":
                c.manage("approval_record", {**payload, "expected_revision": item["progress"]["revision"],
                         "action_id": claim["action_id"], "outcome": "UNKNOWN",
                         "evidence_ref": "codex-app:send-unconfirmed:" + claim["action_id"]})
            raise
        item = self._item(c, key)
        slot = self._slot(item, stage, phase)
        if slot["status"] == "INTENT":
            c.manage("approval_record", {**payload, "expected_revision": item["progress"]["revision"],
                     "action_id": claim["action_id"], "outcome": "ACCEPTED",
                     "evidence_ref": "codex-app:accepted:sha256:" + canonical_hash(response)})
        return self.query(c, key, stage=stage, phase=phase)

    def receive(self, c, key, source_handle):
        # No role=user scan, keyword classifier, GitHub-login shortcut or user hash-copy step.
        return receive_decision(c, key, source_handle, self.decision_verifier)
