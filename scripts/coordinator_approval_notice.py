"""Read-only, bounded notices for an explicitly bound local decision entrance.

This projection neither sends nor claims anything. Only a caller with an
operator-approved thread/host/project binding may expose its output in a hook.
The bridge remains a separate consumer of this versioned process interface.
"""

if __package__:
    from .project_coordinator import canonical_hash, fields, identifier, require, text
else:
    from project_coordinator import canonical_hash, fields, identifier, require, text


def snapshot(c, db, data):
    fields(data, "thread_id host_id project_ids", "after limit")
    thread, host = text(data["thread_id"]), text(data["host_id"])
    projects = data["project_ids"]
    require(isinstance(projects, list) and 0 < len(projects) <= 32, "INVALID_PROJECT_IDS")
    for project in projects:
        identifier(project)
    require(len(set(projects)) == len(projects), "INVALID_PROJECT_IDS")
    after, limit = data.get("after", 0), data.get("limit", 25)
    require(type(after) is int and 0 <= after < 2**63 and type(limit) is int and 1 <= limit <= 100,
            "INVALID_PAGE")
    result = {"schema_version": 1, "notices": [], "next_after": after, "more": False}
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='approvals'").fetchone():
        return result
    # Select only the public projection. Private scope, actor bindings, native
    # evidence and checkpoint paths never leave SQLite in this operation.
    rows = db.execute("WITH eligible AS (SELECT a.seq,a.project_id,a.approval_id,a.scope_version,"
                      "a.spec_hash AS scope_hash,json_extract(a.spec,'$.notice_summary') AS summary,"
                      "json_extract(a.spec,'$.task_ref') AS task_ref,json_extract(a.spec,'$.escalate_at') AS due_at,"
                      "json_extract(a.spec,'$.escalation_owner') AS escalation_owner,"
                      "json_extract(a.spec,'$.dispatcher') AS dispatcher,"
                      "json_type(a.progress,'$.decision_conflict')='object' AS disputed,CASE "
                      "WHEN json_type(a.progress,'$.decision_conflict')='object' THEN 'decision' "
                      "WHEN json_extract(a.progress,'$.execution.status')='RESULT_PENDING' THEN 'review' "
                      "WHEN json_extract(a.progress,'$.decision.status')='PENDING' THEN 'decision' "
                      "WHEN json_extract(a.progress,'$.decision.status')='APPROVED' "
                      "AND json_extract(a.progress,'$.execution.status') IN ('NOT_DISPATCHED','INTENT','UNKNOWN','CHECKPOINT_APPLIED') "
                      "AND json_extract(a.progress,'$.execution.handoff_ref') IS NULL THEN 'execution' END AS phase "
                      "FROM approvals a JOIN projects p ON p.project_id=a.project_id WHERE a.seq>? "
                      "AND a.project_id IN (" + ",".join("?" for _ in projects) + ") "
                      "AND json_extract(a.spec,'$.route.thread_id')=? AND json_extract(a.spec,'$.route.host_id')=? "
                      "AND json_extract(a.progress,'$.current')=1 AND json_extract(a.progress,'$.closed')=0 "
                      "AND p.assignment_state='BOUND' AND p.generation=json_extract(a.spec,'$.generation') "
                      "AND p.owner_principal=json_extract(a.spec,'$.owner_principal') "
                      "AND json_type(a.spec,'$.notice_summary')='text') "
                      "SELECT * FROM eligible WHERE phase IS NOT NULL ORDER BY seq LIMIT ?",
                      (after, *projects, thread, host, limit + 1)).fetchall()
    for row in rows[:limit]:
        phase = row["phase"]
        summary = row["summary"]
        if row["disputed"]:
            summary += " 决定存在冲突，待有权审批者澄清。"
        owner = "project_owner" if phase == "review" else row["dispatcher"] if phase == "execution" else row["escalation_owner"]
        # A new conflict is material even if it returns to the same decision
        # phase. New observation timestamps alone never change this identity.
        result["notices"].append({"notice_id": canonical_hash([row["scope_hash"], phase, summary]),
                                  **{key: row[key] for key in ("project_id", "approval_id", "scope_version", "scope_hash")},
                                  "phase": phase, "summary": summary, "task_ref": row["task_ref"],
                                  "action_owner": owner, "due_at": row["due_at"]})
    result["next_after"] = rows[min(len(rows), limit) - 1]["seq"] if rows else after
    result["more"] = len(rows) > limit
    return result
