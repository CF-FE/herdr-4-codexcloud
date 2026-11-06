"""Private approval state in the coordinator's existing ledger.

The CLI can prepare/transport/reconcile approvals, but cannot import a user
decision. receive_decision requires a host-owned verifier of the original
answer, not a message author/role supplied by a task. No default verifier is
provided: the current Herdr host has no attested user-answer intake.
"""

from dataclasses import asdict, dataclass
import json
import re

if __package__:
    from .project_coordinator import (CLOUD_DOT_ALIASES, _acceptor_key, _generation, canonical,
                                     canonical_hash, fields, identifier, require, text, timestamp)
else:
    from project_coordinator import (CLOUD_DOT_ALIASES, _acceptor_key, _generation, canonical,
                                    canonical_hash, fields, identifier, require, text, timestamp)


KEYS = "project_id approval_id scope_version"
UNHANDED_EXECUTION = ("NOT_DISPATCHED", "INTENT", "UNKNOWN", "CHECKPOINT_APPLIED")


def row_for(db, key):
    for name in ("project_id", "approval_id"):
        identifier(key[name])
    require(type(key["scope_version"]) is int and key["scope_version"] > 0, "INVALID_SCOPE_VERSION")
    row = db.execute("SELECT * FROM approvals WHERE project_id=? AND approval_id=? AND scope_version=?",
                     tuple(key[k] for k in KEYS.split())).fetchone()
    require(row is not None, "APPROVAL_NOT_FOUND")
    return row, json.loads(row["spec"]), json.loads(row["progress"])


def view(c, db, row, spec, progress):
    project = c._project(db, row["project_id"])
    return {"seq": row["seq"], "spec": spec, "scope_hash": row["spec_hash"], "progress": progress,
            "generation_current": project["assignment_state"] == "BOUND"
            and project["generation"] == spec["generation"] and project["owner_principal"] == spec["owner_principal"],
            "due_phases": due_phases(c, spec, progress)}


def due_phases(c, spec, progress):
    if not progress["current"] or progress["closed"]:
        return []
    due = []
    if progress["delivery"]["status"] != "DELIVERED" and c._now() >= timestamp(spec["delivery_due_at"]):
        due.append("delivery")
    if c._now() >= timestamp(spec["escalate_at"]):
        if progress["decision"]["status"] == "PENDING" or progress.get("decision_conflict"):
            due.append("decision")
        elif (progress["execution"]["status"] in UNHANDED_EXECUTION
              and not progress["execution"].get("handoff_ref")
              and (progress["decision"]["status"] == "APPROVED" or progress["stop_requested"])):
            due.append("execution")
    return due


def save(c, db, row, spec, progress, kind, evidence):
    progress["revision"] += 1
    db.execute("UPDATE approvals SET progress=? WHERE seq=?", (canonical(progress).decode(), row["seq"]))
    c._event(db, spec["project_id"], spec.get("request_id"), "APPROVAL_" + kind, "local:approval-adapter",
             {**{k: spec[k] for k in KEYS.split()}, "revision": progress["revision"], **evidence})
    return view(c, db, row, spec, progress)


def current(c, db, spec, progress):
    p = c._project(db, spec["project_id"])
    require(progress["current"] and not progress["closed"], "APPROVAL_NOT_CURRENT")
    require(p["assignment_state"] == "BOUND" and p["generation"] == spec["generation"]
            and p["owner_principal"] == spec["owner_principal"], "STALE_GENERATION")


def prepare(c, db, data):
    fields(data, "spec", "covered_authorization_ref")
    spec = dict(data["spec"])
    fields(spec, KEYS + " generation task_ref scope authorization_gap route dispatcher executor_ref checkpoint "
           "acceptor_principal delivery_due_at escalate_at escalation_owner",
           "request_id authorization_expires_at followup_key notice_summary")
    for name in ("project_id", "approval_id"):
        identifier(spec[name])
    require(type(spec["scope_version"]) is int and 0 < spec["scope_version"] < 2**63, "INVALID_SCOPE_VERSION")
    _generation(spec["generation"])
    for name in ("task_ref", "authorization_gap", "dispatcher", "executor_ref", "acceptor_principal", "escalation_owner"):
        text(spec[name])
    require(_acceptor_key(spec["acceptor_principal"]) not in CLOUD_DOT_ALIASES, "BUSINESS_ACCEPTOR_REQUIRED")
    require(isinstance(spec["scope"], dict) and bool(spec["scope"]), "APPROVAL_SCOPE_REQUIRED")
    if "notice_summary" in spec:
        text(spec["notice_summary"], 1000)
    fields(spec["route"], "kind thread_id host_id authorization_ref approver_principal relay_principal")
    require(spec["route"]["kind"] == "codex_app", "APPROVAL_ROUTE_NOT_IMPLEMENTED")
    for value in spec["route"].values():
        text(value)
    fields(spec["checkpoint"], "runner state project workflow task step source_sha256 input_sha256 choice")
    for value in spec["checkpoint"].values():
        text(value)
    require(spec["checkpoint"]["project"] == spec["project_id"], "CHECKPOINT_PROJECT_MISMATCH")
    for name in ("source_sha256", "input_sha256"):
        require(re.fullmatch("[a-f0-9]{64}", spec["checkpoint"][name]), "INVALID_CHECKPOINT_HASH")
    for name in ("delivery_due_at", "escalate_at", "authorization_expires_at"):
        if name in spec:
            spec[name] = timestamp(spec[name]).isoformat()
    require(timestamp(spec["delivery_due_at"]) <= timestamp(spec["escalate_at"]), "INVALID_APPROVAL_DEADLINES")
    project = c._project(db, spec["project_id"])
    require(spec["acceptor_principal"] != project["cloud_dot_principal"], "BUSINESS_ACCEPTOR_REQUIRED")
    spec["owner_principal"] = project["owner_principal"]
    if "request_id" in spec:
        # A real existing request may be linked. Never synthesize a Mailbox business job.
        request = c._request(db, spec["project_id"], spec["request_id"])
        require(json.loads(request["body"])["task_ref"] == spec["task_ref"], "TASK_BINDING_MISMATCH")
    if "followup_key" in spec:
        followup = c._followup_row(db, spec)
        require(followup["task_ref"] == spec["task_ref"] and followup["generation_current"], "TASK_BINDING_MISMATCH")
    if "covered_authorization_ref" in data:
        return {"state": "BYPASSED", "authorization_ref": text(data["covered_authorization_ref"]),
                "project_id": spec["project_id"], "task_ref": spec["task_ref"]}
    digest = canonical_hash(spec)
    old = db.execute("SELECT * FROM approvals WHERE project_id=? AND approval_id=? ORDER BY scope_version DESC LIMIT 1",
                     (spec["project_id"], spec["approval_id"])).fetchone()
    exact = db.execute("SELECT * FROM approvals WHERE project_id=? AND approval_id=? AND scope_version=?",
                       tuple(spec[k] for k in KEYS.split())).fetchone()
    if exact:
        require(exact["spec_hash"] == digest, "APPROVAL_INPUT_CONFLICT")
        return view(c, db, exact, spec, json.loads(exact["progress"]))
    progress = {"revision": 0, "current": True, "closed": False, "stop_requested": False,
                "next_check_at": spec["delivery_due_at"], "delivery": {"status": "PENDING"},
                "decision": {"status": "PENDING"}, "execution": {"status": "NOT_DISPATCHED"}, "escalations": {}}
    current(c, db, spec, progress)
    if old:
        previous = json.loads(old["progress"])
        require(spec["scope_version"] > old["scope_version"], "STALE_SCOPE_VERSION")
        require(previous["execution"]["status"] in ("NOT_DISPATCHED", "STOPPED") or previous["closed"],
                "RECONCILE_ORIGINAL_EXECUTION")
        previous["current"] = False
        save(c, db, old, json.loads(old["spec"]), previous, "SUPERSEDED", {"new_version": spec["scope_version"]})
    cursor = db.execute("INSERT INTO approvals(project_id,approval_id,scope_version,spec_hash,spec,progress) VALUES(?,?,?,?,?,?)",
                        (spec["project_id"], spec["approval_id"], spec["scope_version"], digest,
                         canonical(spec).decode(), canonical(progress).decode()))
    row = db.execute("SELECT * FROM approvals WHERE seq=?", (cursor.lastrowid,)).fetchone()
    return save(c, db, row, spec, progress, "PREPARED", {"scope_hash": digest})


def tick(c, db, *, limit, project_id=None):
    """The existing timer raises one owned escalation per due phase, without sending.

    PENDING is a delivery obligation, not a notification/transport receipt. A
    host adapter must claim it before sending; no unchanged model wake is made.
    """
    args = [c._now().isoformat(), c._now().isoformat()]
    where = ""
    if project_id is not None:
        where = " AND a.project_id=?"
        args.append(project_id)
    rows = db.execute("SELECT a.* FROM approvals a JOIN projects p ON p.project_id=a.project_id WHERE "
                      "json_extract(a.progress,'$.current')=1 AND json_extract(a.progress,'$.closed')=0 "
                      "AND p.generation=json_extract(a.spec,'$.generation') AND p.assignment_state='BOUND' "
                      "AND p.owner_principal=json_extract(a.spec,'$.owner_principal') AND ("
                      "(json_extract(a.spec,'$.delivery_due_at')<=? AND json_extract(a.progress,'$.delivery.status')<>'DELIVERED' "
                      "AND json_type(a.progress,'$.escalations.delivery') IS NULL) OR "
                      "(json_extract(a.spec,'$.escalate_at')<=? AND ("
                      "((json_extract(a.progress,'$.decision.status')='PENDING' OR json_type(a.progress,'$.decision_conflict') IS NOT NULL) "
                      "AND json_type(a.progress,'$.escalations.decision') IS NULL) OR "
                      "((json_extract(a.progress,'$.decision.status')='APPROVED' OR json_extract(a.progress,'$.stop_requested')=1) "
                      "AND json_extract(a.progress,'$.execution.status') IN ('NOT_DISPATCHED','INTENT','UNKNOWN','CHECKPOINT_APPLIED') "
                      "AND json_type(a.progress,'$.execution.handoff_ref') IS NULL "
                      "AND json_type(a.progress,'$.escalations.execution') IS NULL))))" + where + " ORDER BY a.seq LIMIT ?",
                      (*args, limit)).fetchall()
    items = []
    for row in rows:
        spec, progress = json.loads(row["spec"]), json.loads(row["progress"])
        for phase in due_phases(c, spec, progress):
            if phase not in progress["escalations"]:
                progress["escalations"][phase] = {"status": "PENDING", "raised_at": c._now().isoformat(),
                                                  "owner": spec["escalation_owner"]}
                item = save(c, db, row, spec, progress, "ESCALATION_DUE", {"phase": phase,
                            "action_owner": spec["escalation_owner"], "destination": spec["route"]["thread_id"]})
                items.append({"project_id": spec["project_id"], "approval_id": spec["approval_id"],
                              "scope_version": spec["scope_version"], "phase": phase,
                              "delivery_status": item["progress"]["escalations"][phase]["status"]})
    return items


def operate(c, db, operation, data):
    if operation == "prepare":
        return prepare(c, db, data)
    if operation == "list":
        fields(data, "", "project_id approval_id scope_version after limit")
        after, limit = data.get("after", 0), data.get("limit", 25)
        require(type(after) is int and 0 <= after < 2**63 and type(limit) is int and 1 <= limit <= 100, "INVALID_PAGE")
        clauses, args = ["seq>?"], [after]
        for name in KEYS.split():
            if name in data:
                clauses.append(name + "=?")
                args.append(data[name])
        rows = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='approvals'").fetchone():
            rows = db.execute("SELECT * FROM approvals WHERE " + " AND ".join(clauses) + " ORDER BY seq LIMIT ?",
                              (*args, limit + 1)).fetchall()
        items = [view(c, db, row, json.loads(row["spec"]), json.loads(row["progress"])) for row in rows[:limit]]
        return {"items": items, "next_after": items[-1]["seq"] if items else after, "more": len(rows) > limit}

    require(operation in ("claim", "record", "recheck", "result", "close"), "ADMIN_OPERATION_NOT_ALLOWED")
    row, spec, progress = row_for(db, data)
    if operation == "claim":
        fields(data, KEYS + " expected_revision stage", "phase")
        current(c, db, spec, progress)
        stage = data["stage"]
        require(stage in ("delivery", "execution", "escalation"), "INVALID_APPROVAL_STAGE")
        if stage == "escalation":
            require(data.get("phase") in ("delivery", "decision", "execution"), "INVALID_ESCALATION_PHASE")
            slot = progress["escalations"].setdefault(data["phase"], {"status": "PENDING"})
            ready = data["phase"] in due_phases(c, spec, progress)
        else:
            require("phase" not in data, "INVALID_FIELDS")
            slot, ready = progress[stage], True
        if stage == "execution":
            ready = (progress["decision"]["status"] == "APPROVED" and not progress["stop_requested"]
                     and not progress.get("decision_conflict"))
            if "authorization_expires_at" in spec:
                ready = ready and c._now() <= timestamp(spec["authorization_expires_at"])
        eligible = "NOT_DISPATCHED" if stage == "execution" else "PENDING"
        if not ready or slot["status"] != eligible or progress["revision"] != data["expected_revision"]:
            return {**view(c, db, row, spec, progress), "send": False}
        attempt = slot.get("attempt", 0) + 1
        slot.update(status="INTENT", attempt=attempt,
                    action_id="approval-" + canonical_hash([row["spec_hash"], stage, data.get("phase"), attempt]))
        return {**save(c, db, row, spec, progress, "INTENT", data), "send": True, "action_id": slot["action_id"]}

    fields(data, KEYS + " expected_revision " + {
        "record": "stage action_id outcome evidence_ref", "recheck": "next_check_at evidence_ref",
        "result": "result_kind result_ref", "close": "acceptor_principal result_ref disposition evidence_ref",
    }[operation], "phase" if operation == "record" else
           "acceptor_generation handoff_ref verification_ref" if operation == "close" else "")
    digest = canonical_hash(data)
    if progress.get("last_report_hash") == digest:
        return view(c, db, row, spec, progress)
    require(progress["revision"] == data["expected_revision"], "APPROVAL_REVISION_CONFLICT")
    require(not progress["closed"], "APPROVAL_CLOSED")
    if operation == "record":
        # Late native receipts must remain reconcilable after a handoff or withdrawal.
        stage, outcome = data["stage"], data["outcome"]
        require(stage in ("delivery", "execution", "escalation"), "INVALID_APPROVAL_STAGE")
        if stage == "escalation":
            require(data.get("phase") in progress["escalations"], "INVALID_ESCALATION_PHASE")
            slot = progress["escalations"][data["phase"]]
        else:
            require("phase" not in data, "INVALID_FIELDS")
            slot = progress[stage]
        require(slot.get("action_id") == data["action_id"], "ACTION_MISMATCH")
        allowed = {"INTENT": {"UNKNOWN", "ACCEPTED", "DELIVERED", "NOT_SENT", "CHECKPOINT_APPLIED"},
                   "UNKNOWN": {"ACCEPTED", "DELIVERED", "NOT_SENT", "CHECKPOINT_APPLIED"},
                   "ACCEPTED": {"DELIVERED", "RUNNING", "UNKNOWN"},
                   "CHECKPOINT_APPLIED": {"ACCEPTED", "RUNNING", "UNKNOWN"}, "RUNNING": {"UNKNOWN"}}
        require(outcome in allowed.get(slot["status"], set()), "APPROVAL_STATE_CONFLICT")
        require((stage == "execution" and outcome != "DELIVERED")
                or (stage != "execution" and outcome not in ("CHECKPOINT_APPLIED", "RUNNING")), "INVALID_APPROVAL_OUTCOME")
        text(data["evidence_ref"])
        if outcome == "NOT_SENT":
            status = "PENDING"
            if stage == "execution":
                status = "STOPPED" if progress["stop_requested"] else "NOT_DISPATCHED"
                slot.pop("handoff_ref", None)
            slot.update(status=status, evidence_ref=data["evidence_ref"])
        else:
            slot.update(status=outcome, evidence_ref=data["evidence_ref"])
            if stage == "execution" and outcome in ("ACCEPTED", "RUNNING"):
                slot["handoff_ref"] = data["evidence_ref"]
    elif operation == "recheck":
        current(c, db, spec, progress)
        # Merely observing or changing a filename never rearms a send/escalation intent.
        at = timestamp(data["next_check_at"])
        require(at > c._now(), "INVALID_CHECKPOINT")
        text(data["evidence_ref"])
        progress["next_check_at"] = min(at, timestamp(spec["escalate_at"])).isoformat()
    elif operation == "result":
        require(data["result_kind"] in ("EXECUTED", "CANCELLED", "FAILED", "UNKNOWN"), "INVALID_APPROVAL_RESULT")
        require(progress["execution"]["status"] != "RESULT_PENDING", "APPROVAL_RESULT_CONFLICT")
        require(progress["execution"]["status"] not in ("NOT_DISPATCHED", "STOPPED")
                or (data["result_kind"] == "CANCELLED" and progress["decision"]["status"] in ("REJECTED", "WITHDRAWN")),
                "EXECUTION_RECEIPT_REQUIRED")
        text(data["result_ref"])
        progress["execution"].update(status="UNKNOWN" if data["result_kind"] == "UNKNOWN" else "RESULT_PENDING",
                                      result_kind=data["result_kind"], result_ref=data["result_ref"])
    elif operation == "close":
        project = c._project(db, spec["project_id"])
        require(project["assignment_state"] == "BOUND", "STALE_GENERATION")
        if project["generation"] == spec["generation"] and project["owner_principal"] == spec["owner_principal"]:
            require(data["acceptor_principal"] == spec["acceptor_principal"], "BUSINESS_ACCEPTOR_REQUIRED")
        else:
            # Old results remain recordable, but a new acceptance decision needs
            # the current owner's explicit, verified task handoff reference.
            require(data.get("acceptor_generation") == project["generation"], "STALE_GENERATION")
            require(data["acceptor_principal"] == project["owner_principal"], "BUSINESS_ACCEPTOR_REQUIRED")
            for name in ("handoff_ref", "verification_ref"):
                text(data.get(name))
        require(data["acceptor_principal"] != project["cloud_dot_principal"]
                and _acceptor_key(data["acceptor_principal"]) not in CLOUD_DOT_ALIASES, "BUSINESS_ACCEPTOR_REQUIRED")
        require(progress["execution"]["status"] == "RESULT_PENDING"
                and data["result_ref"] == progress["execution"]["result_ref"], "RESULT_REVIEW_REQUIRED")
        for name in ("disposition", "evidence_ref"):
            text(data[name])
        progress.update(closed=True, acceptance={k: data[k] for k in
                        ("acceptor_principal", "result_ref", "disposition", "evidence_ref",
                         "acceptor_generation", "handoff_ref", "verification_ref") if k in data})
    progress["last_report_hash"] = digest
    return save(c, db, row, spec, progress, operation.upper(), data)


@dataclass(frozen=True)
class VerifiedDecision:
    """Only a trusted inlet may construct this after verifying the original answer.

    A transport caller must NOT deserialize this type from arbitrary task JSON.
    The relay is an explicitly bound recorder, not the approver or acceptor.
    """
    project_id: str
    approval_id: str
    scope_version: int
    scope_hash: str
    decision: str
    approver_principal: str
    relay_principal: str
    source_ref: str
    decided_at: str
    supersedes_ref: str = ""


def receive_decision(c, key, source_handle, verifier):
    """Host-only entry: verifier reads the native source and establishes authority.

    Never registered as a CLI, remote RPC, or organization/Worker MCP tool. A
    verifier is host code, not a callable or identity requested by the message.
    Source validation happens outside the SQLite transaction; frozen scope and
    current generation are checked again before adopting its result.
    """
    require(callable(verifier), "MISSING_TRUSTED_DECISION_INTAKE")
    with c._db(False) as db:
        row, spec, progress = row_for(db, key)
        snapshot = view(c, db, row, spec, progress)
    evidence = verifier(snapshot, source_handle)
    require(type(evidence) is VerifiedDecision, "UNVERIFIED_DECISION")
    proof = asdict(evidence)
    canonical(proof)
    with c._db() as db:
        row, spec, progress = row_for(db, key)
        require(all(proof[k] == spec[k] for k in KEYS.split()) and proof["scope_hash"] == row["spec_hash"],
                "DECISION_SCOPE_MISMATCH")
        route = spec["route"]
        require(proof["approver_principal"] == route["approver_principal"]
                and proof["relay_principal"] == route["relay_principal"], "DECISION_IDENTITY_MISMATCH")
        text(proof["source_ref"])
        require(timestamp(proof["decided_at"]) <= c._now(), "INVALID_DECISION_TIME")
        require(proof["decision"] in ("APPROVED", "REJECTED", "WITHDRAWN"), "INVALID_DECISION")
        digest = canonical_hash(proof)
        # Exact late retransmission of an adopted decision never needs a new user answer.
        adopted = db.execute("SELECT 1 FROM events WHERE project_id=? AND kind GLOB 'APPROVAL_DECISION*'"
                             " AND json_extract(body,'$.proof_hash')=?", (spec["project_id"], digest)).fetchone()
        if adopted:
            return view(c, db, row, spec, progress)
        current(c, db, spec, progress)
        previous = progress["decision"]
        conflict = progress.get("decision_conflict")
        clarified = bool(conflict and proof["supersedes_ref"] == conflict["source_ref"])
        withdrawing = (not conflict and proof["decision"] == "WITHDRAWN" and previous["status"] == "APPROVED"
                       and proof["supersedes_ref"] == previous["source_ref"])
        if previous["status"] != "PENDING" and not clarified and not withdrawing:
            if not conflict and previous["status"] == proof["decision"]:
                return save(c, db, row, spec, progress, "DECISION_CONFIRMED", {"proof_hash": digest, **proof})
            # Preserve the original decision and the contrary source. Throwing here
            # would roll back the dispute and leave an old approval executable.
            progress["decision_conflict"] = proof
            progress["stop_requested"] = True
            return save(c, db, row, spec, progress, "DECISION_CONFLICT", {"proof_hash": digest, **proof})
        if proof["decision"] == "APPROVED" and "authorization_expires_at" in spec:
            require(c._now() <= timestamp(spec["authorization_expires_at"]), "AUTHORIZATION_EXPIRED")
        progress["decision"] = {"status": proof["decision"], **{k: proof[k] for k in
                                ("source_ref", "approver_principal", "relay_principal", "decided_at")}}
        progress.pop("decision_conflict", None)
        # A valid answer itself proves receipt; do not wait for a missing transport ACK.
        progress["delivery"].update(status="DELIVERED", evidence_ref=proof["source_ref"])
        if proof["decision"] in ("REJECTED", "WITHDRAWN"):
            progress["stop_requested"] = True
            if progress["execution"]["status"] == "NOT_DISPATCHED":
                progress["execution"]["status"] = "STOPPED"
        elif progress["execution"]["status"] in ("NOT_DISPATCHED", "STOPPED"):
            progress["stop_requested"] = False
            progress["execution"]["status"] = "NOT_DISPATCHED"
        return save(c, db, row, spec, progress, "DECISION", {"proof_hash": digest, **proof})
