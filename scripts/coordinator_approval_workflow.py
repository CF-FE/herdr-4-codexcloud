"""Adopt one verified approval into its original project-owned checkpoint.

The caller owns approval-source verification and the original dispatch route.
This adapter never starts a workflow or submits a handoff to an executor.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

if __package__:
    from .project_coordinator import canonical_hash, require, timestamp
else:
    from project_coordinator import canonical_hash, require, timestamp


class WorkflowCheckpoint:
    def __init__(self, spec):
        checkpoint = spec.get("checkpoint") if isinstance(spec, dict) else None
        keys = {"runner", "state", "project", "workflow", "task", "step", "source_sha256",
                "input_sha256", "choice"}
        if not isinstance(checkpoint, dict) or checkpoint.keys() != keys:
            raise ValueError("invalid workflow checkpoint fields")
        if any(not isinstance(value, str) or not value.strip() for value in checkpoint.values()):
            raise ValueError("workflow checkpoint values must be nonempty strings")
        self.checkpoint = dict(checkpoint)
        self.runner = Path(checkpoint["runner"])
        self.state = Path(checkpoint["state"])
        if not self.runner.is_absolute() or self.runner.name != "workflow.py" or not self.state.is_absolute():
            raise ValueError("workflow runner and checkpoint need their original absolute paths")
        for field in ("source_sha256", "input_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", checkpoint[field]):
                raise ValueError("invalid workflow " + field)

    def _call(self, *args):
        # Verify before executing even status: a changed runner must not run merely
        # to report its own source-version conflict. Hash the original two files
        # using the project runner's existing version contract.
        sources = {name: hashlib.sha256(self.runner.with_name(name).read_bytes()).hexdigest()
                   for name in ("workflow.py", "definition.py")}
        source_hash = hashlib.sha256(json.dumps(sources, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode()).hexdigest()
        if source_hash != self.checkpoint["source_sha256"]:
            raise ValueError("workflow source version conflict; restore the original project revision")
        result = subprocess.run([sys.executable, "-I", "-B", str(self.runner), "--state", str(self.state), *args],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError(f"workflow {args[0]} failed: {result.stderr.strip()}")
        run = json.loads(result.stdout)
        if not isinstance(run, dict):
            raise ValueError("invalid workflow status")
        for field in ("project", "workflow", "task", "source_sha256", "input_sha256"):
            if run.get(field) != self.checkpoint[field]:
                raise ValueError("workflow checkpoint identity conflict: " + field)
        decisions = run.get("decisions")
        if not isinstance(decisions, dict):
            raise ValueError("invalid workflow decision history")
        step = self.checkpoint["step"]
        if step in decisions:
            record = decisions[step]
            if not isinstance(record, dict) or record.keys() != {"choice", "evidence"}:
                raise ValueError("invalid recorded workflow decision")
        else:
            if run.get("status") != "WAITING_DECISION" or run.get("step") != step:
                raise ValueError("not the pending workflow decision")
            decision = run.get("decision")
            if (not isinstance(decision, dict) or not isinstance(decision.get("choices"), dict)
                    or self.checkpoint["choice"] not in decision["choices"]):
                raise ValueError("approval choice is not allowed by the original workflow")
        return run

    def status(self):
        # The standard project runner opens SQLite read-only and skips project
        # definition loading for this command; it does not advance any step.
        return self._call("status")

    def resume(self, evidence_ref, *, final_gate=None):
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise ValueError("empty verified approval evidence reference")
        run = self.status()
        previous = run["decisions"].get(self.checkpoint["step"])
        decision = {"choice": self.checkpoint["choice"], "evidence": evidence_ref}
        if previous is not None:
            if previous != decision:
                raise ValueError("approval conflicts with the recorded workflow decision")
            return run
        if final_gate is not None:
            adopted = final_gate()
            if adopted is not None:
                require(adopted["decisions"].get(self.checkpoint["step"]) == decision,
                        "WORKFLOW_DECISION_CONFLICT")
                return adopted
        return self._call("decide", "--step", self.checkpoint["step"], "--choice", self.checkpoint["choice"],
                          "--evidence", evidence_ref)


def resume_approval(coordinator, key):
    """Adopt an already verified approval; leave all external dispatch to its owner.

    An interrupted import is reconciled against the original checkpoint. An
    execution intent is never interpreted as permission to submit a new job.
    """
    require(isinstance(key, dict) and key.keys() == {"project_id", "approval_id", "scope_version"},
            "INVALID_APPROVAL_KEY")
    items = coordinator.manage("approval_list", key)["items"]
    require(len(items) == 1, "APPROVAL_NOT_FOUND")
    ledger = items[0]
    spec, progress = ledger["spec"], ledger["progress"]
    scope_hash = ledger["scope_hash"]
    checkpoint = WorkflowCheckpoint(spec)
    evidence = progress["decision"].get("source_ref")
    readonly = {"CHECKPOINT_APPLIED", "ACCEPTED", "RUNNING", "RESULT_PENDING"}

    def refresh():
        nonlocal ledger, progress
        latest = coordinator.manage("approval_list", key)["items"]
        require(len(latest) == 1 and latest[0]["scope_hash"] == scope_hash, "APPROVAL_SCOPE_CONFLICT")
        ledger = latest[0]
        progress = ledger["progress"]

    def final_gate():
        # Run after resume's own status, immediately before any new decide. A
        # withdrawal received during either query must stop the pending import.
        refresh()
        if progress["execution"]["status"] in readonly:
            return checkpoint.status()
        require(ledger["generation_current"] and progress["current"], "APPROVAL_NOT_CURRENT")
        require(not progress["closed"], "APPROVAL_CLOSED")
        require(progress["decision"]["status"] == "APPROVED" and not progress["stop_requested"],
                "APPROVED_DECISION_REQUIRED")
        require("decision_conflict" not in progress, "APPROVAL_DECISION_CONFLICT")
        require(progress["decision"]["source_ref"] == evidence, "APPROVAL_DECISION_CHANGED")
        if "authorization_expires_at" in spec:
            require(coordinator._now() <= timestamp(spec["authorization_expires_at"]), "AUTHORIZATION_EXPIRED")
        return None

    run = checkpoint.status()  # Source/identity errors must not consume an intent.
    previous = run["decisions"].get(spec["checkpoint"]["step"])
    if previous is None:
        adopted = final_gate()
        if adopted is not None:
            return {"run": adopted, "ledger": ledger}
        if progress["execution"]["status"] == "NOT_DISPATCHED":
            claimed = coordinator.manage("approval_claim", {**key, "expected_revision": progress["revision"],
                                                            "stage": "execution"})
            require(claimed["send"] or claimed["progress"]["execution"]["status"] in ("INTENT", "UNKNOWN")
                    or claimed["progress"]["execution"]["status"] in readonly, "APPROVAL_EXECUTION_NOT_CLAIMED")
        else:
            require(progress["execution"]["status"] in ("INTENT", "UNKNOWN"), "APPROVAL_EXECUTION_STATE_CONFLICT")
        run = checkpoint.resume(evidence, final_gate=final_gate)

    # A decision already in the project checkpoint is historical fact. Re-read
    # before recording it, and preserve any newer stop/withdrawal without rollback.
    refresh()
    if (progress["execution"]["status"] in readonly or not ledger["generation_current"]
            or not progress["current"] or progress["closed"] or progress["stop_requested"]
            or "decision_conflict" in progress or progress["decision"]["status"] != "APPROVED"):
        return {"run": run, "ledger": ledger}
    require(run["decisions"].get(spec["checkpoint"]["step"]) ==
            {"choice": spec["checkpoint"]["choice"], "evidence": progress["decision"]["source_ref"]},
            "WORKFLOW_DECISION_CONFLICT")
    require(progress["execution"]["status"] in ("INTENT", "UNKNOWN"), "APPROVAL_EXECUTION_STATE_CONFLICT")
    action_id = (run.get("action") or {}).get("id")
    reference = "checkpoint:" + canonical_hash({"decision_ref": evidence, "checkpoint": spec["checkpoint"]})
    reference += ":action:" + (action_id or "none")
    ledger = coordinator.manage("approval_record", {
        **key, "expected_revision": progress["revision"], "stage": "execution",
        "action_id": progress["execution"]["action_id"], "outcome": "CHECKPOINT_APPLIED", "evidence_ref": reference})
    return {"run": run, "ledger": ledger}
