#!/usr/bin/env python3
"""Single-Mac coordinator ledger. Only a trusted local operator/adapter opens this DB.

Public methods take a principal established by the caller's authenticated transport.
The local CLI is privileged. Never expose it or manage()/delivery() as a remote RPC.
The core records source-attributed evidence; it does not execute commands or send mail.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import uuid


COORDINATOR_ID = "cloud-dot-project-coordinator"
# The old ledger ID is part of hashed report content and event-id derivation.
LEGACY_COORDINATOR_ID = "g8-project-coordinator"
LEGACY_PRINCIPAL_COLUMN = "g8_principal"
MAX_MESSAGE = 256 * 1024
DEFAULT_BUDGET = {"contact_attempts": 1, "production_writes": 0, "audio_generations": 0}
GOAL_BUDGET = {"dispatch_attempts": 1, "production_writes": 0, "audio_generations": 0}
GOAL_ACTIONS = {"coordinate_existing_owner", "verify_existing_evidence", "plan_independent_review", "report_progress"}
UNRESOLVED = ("INTENT", "UNKNOWN", "SUBMITTED", "OWNER_READ")
CONTACT_REASONS = {"CHANNEL_UNAVAILABLE", "OWNER_OFFLINE", "IDENTITY_MISMATCH", "STALE_GENERATION",
                   "POLICY_DENIED", "SEND_FAILED", "NATIVE_SUBMISSION", "RECEIPT_MISMATCH",
                   "ACK_UNCONFIRMED", "UNKNOWN_CAUSE"}


class CoordinatorError(ValueError):
    def __init__(self, code, *, conflicts=()):
        super().__init__(code)
        self.code = code
        self.conflicts = conflicts


def require(condition, code):
    if not condition:
        raise CoordinatorError(code)


def canonical(value, *, max_bytes=MAX_MESSAGE):
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise CoordinatorError("INVALID_JSON") from error
    require(len(data) <= max_bytes, "MESSAGE_TOO_LARGE")
    return data


def canonical_hash(value, *, max_bytes=MAX_MESSAGE):
    return hashlib.sha256(canonical(value, max_bytes=max_bytes)).hexdigest()


def text(value, maximum=2048):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and "\x00" not in value, "INVALID_TEXT")
    return value


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value),
            "INVALID_ID")
    return value


def fields(data, required, optional=""):
    must, may = set(required.split()), set(optional.split())
    require(isinstance(data, dict) and must <= data.keys() <= must | may, "INVALID_FIELDS")


def _generation(value):
    require(type(value) is int and 0 <= value < 2**63 - 1, "INVALID_GENERATION")
    return value


def timestamp(value):
    try:
        date = datetime.fromisoformat(text(value, 64).replace("Z", "+00:00"))
        require(date.tzinfo is not None, "TIMEZONE_REQUIRED")
        return date.astimezone(timezone.utc)
    except (OverflowError, ValueError) as error:
        raise CoordinatorError("INVALID_TIME") from error


def strings(value, required=False):
    require(isinstance(value, list) and len(value) <= 64 and (value or not required), "INVALID_LIST")
    for item in value:
        text(item)


def canonical_ref(value):
    return value == value.strip() and value.isprintable()


def validate_goal(goal, task_ref, deadline):
    """A bounded existing-owner assignment; neither a new owner nor a tool grant."""
    fields(goal, "objective work_key acceptance_criteria evidence_refs related_request_ids scope_refs "
                 "allowed_actions next_check_at review_requirement")
    text(goal["objective"], 32768)
    for name in ("acceptance_criteria", "evidence_refs", "scope_refs", "allowed_actions"):
        strings(goal[name], required=True)
        require(len(set(goal[name])) == len(goal[name]), "DUPLICATE_GOAL_VALUE")
    require(all(canonical_ref(ref) for ref in goal["scope_refs"]), "NONCANONICAL_SCOPE_REF")
    strings(goal["related_request_ids"])
    for request_id in goal["related_request_ids"]:
        identifier(request_id)
    require(set(goal["allowed_actions"]) <= GOAL_ACTIONS, "GOAL_ACTION_NOT_ALLOWED")
    require(goal["review_requirement"] in ("none", "independent_listening"), "INVALID_REVIEW_REQUIREMENT")
    require(goal["work_key"] == canonical_hash({"task_ref": task_ref, "scope_refs": sorted(goal["scope_refs"])}),
            "WORK_KEY_MISMATCH")
    require(timestamp(goal["next_check_at"]) <= timestamp(deadline), "INVALID_CHECKPOINT")


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
 project_id TEXT PRIMARY KEY, repository TEXT NOT NULL, project_ref TEXT NOT NULL,
 cloud_dot_principal TEXT NOT NULL, report_route TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
 owner_principal TEXT, endpoint TEXT, assignment_state TEXT NOT NULL DEFAULT 'UNVERIFIED');
CREATE TABLE IF NOT EXISTS assignments(
 project_id TEXT NOT NULL REFERENCES projects, generation INTEGER NOT NULL, proof TEXT NOT NULL,
 PRIMARY KEY(project_id,generation));
CREATE TABLE IF NOT EXISTS creations(
 project_id TEXT NOT NULL REFERENCES projects, creation_id TEXT NOT NULL, generation INTEGER NOT NULL,
 input_hash TEXT NOT NULL, state TEXT NOT NULL, candidate_ref TEXT, evidence_ref TEXT,
 PRIMARY KEY(project_id,creation_id));
CREATE UNIQUE INDEX IF NOT EXISTS open_creation ON creations(project_id)
 WHERE state IN ('INTENT','UNKNOWN','CREATED');
CREATE TABLE IF NOT EXISTS requests(
 project_id TEXT NOT NULL REFERENCES projects, request_id TEXT NOT NULL, principal TEXT NOT NULL,
 kind TEXT NOT NULL, idem TEXT NOT NULL, input_hash TEXT NOT NULL, body TEXT NOT NULL,
 target_generation INTEGER NOT NULL, created_at TEXT NOT NULL, waiting TEXT NOT NULL,
 cancel_ref TEXT, unblock_ref TEXT,
 PRIMARY KEY(project_id,request_id), UNIQUE(project_id,principal,kind,idem));
CREATE TABLE IF NOT EXISTS contacts(
 project_id TEXT NOT NULL, request_id TEXT NOT NULL, action_id TEXT NOT NULL UNIQUE,
 generation INTEGER NOT NULL, owner TEXT NOT NULL, endpoint TEXT NOT NULL,
 state TEXT NOT NULL, transport_ref TEXT, read_ref TEXT, reply_hash TEXT, reason TEXT,
 PRIMARY KEY(project_id,request_id), FOREIGN KEY(project_id,request_id) REFERENCES requests);
CREATE TABLE IF NOT EXISTS results(
 project_id TEXT NOT NULL, request_id TEXT NOT NULL, version INTEGER NOT NULL,
 result_hash TEXT NOT NULL, body TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, read_ref TEXT,
 PRIMARY KEY(project_id,request_id,version), UNIQUE(project_id,request_id,result_hash),
 FOREIGN KEY(project_id,request_id) REFERENCES requests);
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects, request_id TEXT,
 kind TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL, body TEXT NOT NULL,
 event_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS notifications(
 seq INTEGER PRIMARY KEY REFERENCES events, project_id TEXT NOT NULL, request_id TEXT,
 recipient TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wait_schedule(
 project_id TEXT NOT NULL, request_id TEXT NOT NULL, revision INTEGER NOT NULL, material_hash TEXT NOT NULL,
 due TEXT, phase TEXT NOT NULL, deadline TEXT NOT NULL, PRIMARY KEY(project_id,request_id),
 FOREIGN KEY(project_id,request_id) REFERENCES requests);
CREATE INDEX IF NOT EXISTS coordinator_due ON wait_schedule(due,project_id,request_id) WHERE due IS NOT NULL;
CREATE INDEX IF NOT EXISTS project_due ON wait_schedule(project_id,due,request_id) WHERE due IS NOT NULL;
CREATE INDEX IF NOT EXISTS project_event_kind ON events(project_id,kind,seq);
CREATE TABLE IF NOT EXISTS goals(
 project_id TEXT NOT NULL, work_key TEXT NOT NULL, request_id TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'ADMITTED', accepted_seq INTEGER, last_seq INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(project_id,work_key), UNIQUE(project_id,request_id),
 FOREIGN KEY(project_id,request_id) REFERENCES requests);
CREATE TABLE IF NOT EXISTS goal_updates(
 project_id TEXT NOT NULL, request_id TEXT NOT NULL, seq INTEGER NOT NULL,
 report_hash TEXT NOT NULL, result_version INTEGER NOT NULL,
 PRIMARY KEY(project_id,request_id,seq),
 FOREIGN KEY(project_id,request_id) REFERENCES requests,
 FOREIGN KEY(project_id,request_id,result_version) REFERENCES results(project_id,request_id,version));
"""


class Coordinator:
    def __init__(self, state_dir, *, clock=None, read_only=False):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.read_only = read_only
        root = Path(state_dir).expanduser().absolute()
        require(not root.is_symlink(), "PRIVATE_STATE_REQUIRED")
        if read_only:
            require(root.is_dir(), "STATE_NOT_INITIALIZED")
        else:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and not info.st_mode & 0o077, "PRIVATE_STATE_REQUIRED")
        self.path = root / "project-coordinator.sqlite3"
        require(not self.path.is_symlink(), "PRIVATE_STATE_REQUIRED")
        if read_only:
            require(self.path.is_file(), "STATE_NOT_INITIALIZED")
        fd = os.open(self.path, (os.O_RDONLY if read_only else os.O_CREAT | os.O_RDWR) |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and not info.st_mode & 0o077, "PRIVATE_STATE_REQUIRED")
        finally:
            os.close(fd)
        # Select the fixed ledger identity before reading evidence or admitting any effect.
        # Never rename a stored principal or rehash old records during an upgrade.
        with self._db(not read_only) as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
            if columns:
                require(("cloud_dot_principal" in columns) != (LEGACY_PRINCIPAL_COLUMN in columns),
                        "UNSUPPORTED_PROJECT_SCHEMA")
            if LEGACY_PRINCIPAL_COLUMN in columns:
                self._principal_column = LEGACY_PRINCIPAL_COLUMN
                self.coordinator_id = LEGACY_COORDINATOR_ID
            else:
                self._principal_column = "cloud_dot_principal"
                self.coordinator_id = COORDINATOR_ID
            if read_only:
                return
            had_schedule = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wait_schedule'").fetchone()
            for statement in SCHEMA.strip().split(";"):
                if statement.strip():
                    db.execute(statement)
            if "reason" not in {row["name"] for row in db.execute("PRAGMA table_info(contacts)")}:
                db.execute("ALTER TABLE contacts ADD COLUMN reason TEXT")
            if not had_schedule:
                # Only on initial schema creation/upgrade, preserve existing waiting responsibilities.
                for row in db.execute("SELECT project_id,request_id,waiting FROM requests"):
                    self._schedule(db, row["project_id"], row["request_id"], json.loads(row["waiting"]))

    @contextmanager
    def _db(self, write=True):
        require(not (self.read_only and write), "READ_ONLY")
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro" if self.read_only else self.path,
                             uri=self.read_only, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _now(self):
        now = self.clock()
        require(isinstance(now, datetime) and now.tzinfo is not None, "INVALID_CLOCK")
        return now.astimezone(timezone.utc)

    def _project(self, db, project_id, principal=None):
        row = db.execute("SELECT * FROM projects WHERE project_id=?", (identifier(project_id),)).fetchone()
        if principal is not None:
            require(row is not None and row[self._principal_column] == text(principal), "ACCESS_DENIED")
        require(row is not None, "PROJECT_NOT_FOUND")
        project = dict(row)
        project["cloud_dot_principal"] = project.pop(self._principal_column)
        return project

    @staticmethod
    def _request(db, project_id, request_id):
        row = db.execute("SELECT * FROM requests WHERE project_id=? AND request_id=?",
                         (project_id, identifier(request_id))).fetchone()
        require(row is not None, "REQUEST_NOT_FOUND")
        return row

    @staticmethod
    def _contact(db, project_id, request_id):
        return db.execute("SELECT * FROM contacts WHERE project_id=? AND request_id=?",
                          (project_id, request_id)).fetchone()

    def _event(self, db, project_id, request_id, kind, source, body, *, notify=False):
        encoded = canonical(body).decode()
        key = canonical_hash([project_id, request_id, kind, source, body])
        cursor = db.execute("INSERT OR IGNORE INTO events(project_id,request_id,kind,source,observed_at,body,event_key)"
                            " VALUES(?,?,?,?,?,?,?)", (project_id, request_id, kind, source,
                                                       self._now().isoformat(), encoded, key))
        if notify and cursor.rowcount:
            db.execute("INSERT INTO notifications(seq,project_id,request_id,recipient)"
                       f" SELECT ?,project_id,?,{self._principal_column} FROM projects WHERE project_id=?",
                       (cursor.lastrowid, request_id, project_id))

    def _schedule(self, db, p, r, wait):
        """A changed condition can rearm its checkpoint. New observation refs alone never rearm it."""
        request = self._request(db, p, r)
        contact = self._contact(db, p, r)
        active = not request["unblock_ref"] and (not request["cancel_ref"]
                         or (contact is not None and contact["state"] in UNRESOLVED))
        material = {k: v for k, v in wait.items() if k != "evidence_ref"}
        material_hash = canonical_hash([bool(active), material])
        old = db.execute("SELECT revision,material_hash FROM wait_schedule WHERE project_id=? AND request_id=?",
                         (p, r)).fetchone()
        if old and old["material_hash"] == material_hash:
            return old["revision"]
        revision = old["revision"] + 1 if old else 1
        deadline = timestamp(wait["deadline"]).isoformat()
        due, phase = deadline, "DEADLINE"
        if "next_check_at" in wait:
            checkpoint = timestamp(wait["next_check_at"]).isoformat()
            if checkpoint < deadline:
                due, phase = checkpoint, "CHECKPOINT"
        if not active:
            due, phase = None, "CLOSED"
        db.execute("INSERT INTO wait_schedule VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,request_id) DO UPDATE"
                   " SET revision=excluded.revision,material_hash=excluded.material_hash,due=excluded.due,"
                   "phase=excluded.phase,deadline=excluded.deadline",
                   (p, r, revision, material_hash, due, phase, deadline))
        return revision

    def _wait(self, db, p, r, wait):
        db.execute("UPDATE requests SET waiting=? WHERE project_id=? AND request_id=?",
                   (canonical(wait).decode(), p, r))
        return self._schedule(db, p, r, wait)

    def route(self, project_id):
        """Privileged local routing view for the verified bridge adapter."""
        with self._db(False) as db:
            return self._project(db, project_id)

    def _status(self, db, project_id, request_id):
        r = self._request(db, project_id, request_id)
        contact = self._contact(db, project_id, request_id)
        latest = db.execute("SELECT version,read_ref FROM results WHERE project_id=? AND request_id=?"
                            " ORDER BY version DESC LIMIT 1", (project_id, request_id)).fetchone()
        body = json.loads(r["body"])
        schedule = db.execute("SELECT revision,phase FROM wait_schedule WHERE project_id=? AND request_id=?",
                              (project_id, request_id)).fetchone()
        if latest:
            state = "CLOUD_DOT_READ" if latest["read_ref"] else "RESULT_AVAILABLE"
        elif r["cancel_ref"]:
            state = "CANCEL_REQUESTED" if contact else "CANCELLED_BEFORE_CONTACT"
        elif contact:
            state = contact["state"]
        elif timestamp(body["deadline"]) <= self._now():
            state = "EXPIRED_BEFORE_CONTACT"
        else:
            project = self._project(db, project_id)
            state = ("READY" if project["owner_principal"] and project["generation"] == r["target_generation"]
                     else "WAITING_OWNER")
        result = {"coordinator_id": self.coordinator_id, "project_id": project_id, "request_id": request_id,
                "job_id": body["job_id"], "payload_hash": body["payload_hash"], "state": state, "target_generation": r["target_generation"],
                "contact_state": contact["state"] if contact else None,
                "contact_reason": contact["reason"] if contact else None,
                "action_id": contact["action_id"] if contact else None,
                "owner_read": bool(contact and contact["read_ref"]), "cancel_requested": bool(r["cancel_ref"]),
                "stopped": bool(contact and contact["state"] == "STOPPED"), "unblocked": bool(r["unblock_ref"]),
                "result_version": latest["version"] if latest else None,
                "cloud_dot_read": bool(latest and latest["read_ref"]), "waiting": json.loads(r["waiting"]),
                "wait_revision": schedule["revision"], "waiting_active": schedule["phase"] != "CLOSED",
                "observed_at": self._now().isoformat()}
        if r["kind"] == "DELEGATE_GOAL":
            goal = db.execute("SELECT work_key,state,accepted_seq,last_seq FROM goals WHERE project_id=? AND request_id=?",
                              (project_id, request_id)).fetchone()
            result["goal"] = dict(goal)
            result.update(kind="DELEGATE_GOAL", goal_state=goal["state"], last_seq=goal["last_seq"],
                          work_key=goal["work_key"], accepted=goal["accepted_seq"] is not None)
        return result

    @staticmethod
    def _require_bridge_origin(db, project_id, request_id, origin):
        row = db.execute("SELECT body FROM events WHERE project_id=? AND request_id=? AND kind='REQUEST_ACCEPTED'"
                         " ORDER BY seq LIMIT 1", (project_id, request_id)).fetchone()
        recorded = json.loads(row["body"]).get("bridge_origin") if row else None
        require(recorded is not None, "ORIGIN_BINDING_UNATTESTED")
        require(recorded == origin, "ORIGIN_BINDING_CONFLICT")

    def verify_bridge_origin(self, project_id, request_id, origin):
        with self._db(False) as db:
            self._require_bridge_origin(db, project_id, request_id, origin)

    def submit(self, principal, request, *, bridge_origin=None):
        common = "project_id request_id job_id task_ref idempotency_key kind expected_generation deadline " \
                 "authorization_ref payload_hash evidence_refs budget"
        fields(request, common, "question goal")
        require(request["kind"] in ("QUERY_BLOCKER", "DELEGATE_GOAL"), "OPERATION_NOT_ALLOWED")
        is_goal = request["kind"] == "DELEGATE_GOAL"
        fields(request, common + (" goal" if is_goal else " question"))
        p, r = identifier(request["project_id"]), identifier(request["request_id"])
        with self._db() as db:
            project = self._project(db, p, text(principal))
            identifier(request["job_id"])
            idem = identifier(request["idempotency_key"])
            for key in ("task_ref", "authorization_ref"):
                text(request[key])
            if is_goal:
                require(re.fullmatch(r"https://github\.com/" + re.escape(project["repository"]) +
                                     r"/issues/[1-9][0-9]*", request["task_ref"]) or
                        re.fullmatch(r"DI_[A-Za-z0-9_-]{1,125}", request["task_ref"]), "CANONICAL_TASK_REQUIRED")
                validate_goal(request["goal"], request["task_ref"], request["deadline"])
            else:
                text(request["question"], 32768)
            strings(request["evidence_refs"])
            if bridge_origin is not None:
                fields(bridge_origin, "channel subject origin_ref task_evidence_ref")
                for value in bridge_origin.values():
                    text(value)
                require(request["evidence_refs"][-2:] == [bridge_origin["origin_ref"], bridge_origin["task_evidence_ref"]],
                        "ORIGIN_BINDING_CONFLICT")
            expected = _generation(request["expected_generation"])
            require(isinstance(request["payload_hash"], str) and
                    re.fullmatch("[a-f0-9]{64}", request["payload_hash"]), "INVALID_HASH")
            b = request["budget"]
            require(isinstance(b, dict) and b == (GOAL_BUDGET if is_goal else DEFAULT_BUDGET)
                    and all(type(v) is int for v in b.values()),
                    "INVALID_BUDGET")
            digest = canonical_hash(request)
            prior = db.execute("SELECT request_id,input_hash FROM requests WHERE project_id=? AND "
                               "principal=? AND kind=? AND idem=?", (p, principal, request["kind"], idem)).fetchone()
            if prior:
                require(prior["input_hash"] == digest, "IDEMPOTENCY_CONFLICT")
                if bridge_origin is not None:
                    self._require_bridge_origin(db, p, prior["request_id"], bridge_origin)
                return self._status(db, p, prior["request_id"])
            if is_goal:
                # A goal is fenced to its original owner generation. Reserving
                # assets while no one holds that generation cannot lead to a send.
                require(project["owner_principal"] is not None and project["assignment_state"] == "BOUND",
                        "OWNER_UNVERIFIED")
                goal = request["goal"]
                original = db.execute("SELECT request_id,work_key FROM goals WHERE project_id=? AND work_key=?",
                                      (p, goal["work_key"])).fetchone()
                if original:
                    raise CoordinatorError("WORK_ALREADY_DELEGATED", conflicts=[dict(original)])
                # A new request, Issue, or partial batch cannot bypass an asset
                # reservation. The project owns the scope, not an Issue alias.
                conflicts = []
                scopes = set(goal["scope_refs"])
                for existing in db.execute("SELECT r.body,g.request_id,g.work_key FROM goals g"
                                           " JOIN requests r USING(project_id,request_id)"
                                           " WHERE g.project_id=? ORDER BY g.request_id", (p,)):
                    body = json.loads(existing["body"])
                    # Older ledgers may contain padded IDs; reserve their scopes
                    # without changing the original request, work key, or hash.
                    if not scopes.isdisjoint(ref.strip() for ref in body["goal"]["scope_refs"]):
                        conflicts.append({"request_id": existing["request_id"], "work_key": existing["work_key"]})
                if conflicts:
                    raise CoordinatorError("WORK_SCOPE_OVERLAP", conflicts=conflicts)
                require(timestamp(goal["next_check_at"]) > self._now(), "CHECKPOINT_EXPIRED")
            require(expected == project["generation"], "STALE_GENERATION")
            require(timestamp(request["deadline"]) > self._now(), "DEADLINE_EXPIRED")
            wait = {"follow_up_owner": self.coordinator_id, "action_owner": self.coordinator_id,
                    "reason": "CONTACT_ADMISSION" if project["owner_principal"] else "VERIFY_APPOINTMENT",
                    "next_condition": "Verified project owner and approved contact route",
                    "deadline": request["deadline"], "escalate_to": principal,
                    "evidence_ref": request["task_ref"]}
            if is_goal:
                wait["next_check_at"] = request["goal"]["next_check_at"]
            try:
                db.execute("INSERT INTO requests(project_id,request_id,principal,kind,idem,input_hash,body,"
                           "target_generation,created_at,waiting) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (p, r, principal, request["kind"], idem, digest, canonical(request).decode(),
                            expected, self._now().isoformat(), canonical(wait).decode()))
            except sqlite3.IntegrityError as error:
                raise CoordinatorError("REQUEST_ID_CONFLICT") from error
            if is_goal:
                db.execute("INSERT INTO goals(project_id,work_key,request_id) VALUES(?,?,?)",
                           (p, request["goal"]["work_key"], r))
            self._schedule(db, p, r, wait)
            evidence = {"input_hash": digest}
            if bridge_origin is not None:
                evidence["bridge_origin"] = bridge_origin
            self._event(db, p, r, "REQUEST_ACCEPTED", principal, evidence, notify=True)
            return self._status(db, p, r)

    def status(self, principal, project_id, request_id):
        with self._db(False) as db:
            self._project(db, project_id, text(principal))
            return self._status(db, project_id, request_id)

    def inspect(self, project_id, request_id):
        with self._db(False) as db:
            return self._status(db, project_id, request_id)

    def goal_context(self, project_id, request_id):
        """Host-only immutable input for preflight or the original native readback.

        No intent is created. A previous action keeps its original owner binding,
        so recovery never silently redirects an unknown execution to a successor.
        """
        with self._db(False) as db:
            project = self._project(db, project_id)
            request = self._request(db, project_id, request_id)
            require(request['kind'] == 'DELEGATE_GOAL', 'OPERATION_NOT_ALLOWED')
            body = json.loads(request['body'])
            contact = self._contact(db, project_id, request_id)
            return {'project_id': project_id, 'request_id': request_id, 'kind': 'DELEGATE_GOAL',
                    'action_id': contact['action_id'] if contact else None,
                    'generation': contact['generation'] if contact else request['target_generation'],
                    'owner_principal': contact['owner'] if contact else project['owner_principal'],
                    'endpoint': contact['endpoint'] if contact else project['endpoint'],
                    **{k: body[k] for k in ('job_id', 'payload_hash', 'task_ref', 'authorization_ref',
                                          'goal', 'budget', 'deadline')}}

    def goal_for_work(self, principal, project_id, work_key):
        """Read the original work reservation; never create a replacement request."""
        require(isinstance(work_key, str) and re.fullmatch("[a-f0-9]{64}", work_key), "INVALID_HASH")
        with self._db(False) as db:
            self._project(db, project_id, text(principal))
            row = db.execute("SELECT request_id FROM goals WHERE project_id=? AND work_key=?",
                             (project_id, work_key)).fetchone()
            require(row is not None, "WORK_NOT_FOUND")
            return self._status(db, project_id, row["request_id"])

    def claim_contact(self, project_id, request_id, expected_generation):
        """Only send when send=True. The persisted intent consumes the entire one-contact budget."""
        with self._db() as db:
            project = self._project(db, project_id)
            request = self._request(db, project_id, request_id)
            require(project["generation"] == _generation(expected_generation) == request["target_generation"],
                    "STALE_GENERATION")
            require(project["owner_principal"] is not None, "OWNER_UNVERIFIED")
            body = json.loads(request["body"])
            before = self._contact(db, project_id, request_id)
            if not before:
                require(not request["cancel_ref"], "CANCEL_REQUESTED")
                require(timestamp(body["deadline"]) > self._now(), "DEADLINE_EXPIRED")
                action = "contact-" + uuid.uuid4().hex
                db.execute("INSERT INTO contacts(project_id,request_id,action_id,generation,owner,endpoint,state)"
                           " VALUES(?,?,?,?,?,?,'INTENT')", (project_id, request_id, action, expected_generation,
                                                             project["owner_principal"], project["endpoint"]))
                self._event(db, project_id, request_id, "CONTACT_INTENT", self.coordinator_id,
                            {"action_id": action, "generation": expected_generation})
                wait = json.loads(request["waiting"])
                wait.update(action_owner=project["owner_principal"], reason="AWAIT_OWNER_REPLY",
                            next_condition="Verified receipt or reply on the original contact action", evidence_ref=action)
                self._wait(db, project_id, request_id, wait)
            contact = self._contact(db, project_id, request_id)
            result = {"send": before is None, "action_id": contact["action_id"], "project_id": project_id,
                    "request_id": request_id, "generation": contact["generation"], "owner_principal": contact["owner"],
                    "endpoint": contact["endpoint"], "deadline": body["deadline"],
                    "job_id": body["job_id"], "payload_hash": body["payload_hash"], "task_ref": body["task_ref"],
                    "authorization_ref": body["authorization_ref"], "budget": body["budget"]}
            if request["kind"] == "DELEGATE_GOAL":
                result.update(kind="DELEGATE_GOAL", goal=body["goal"])
            else:
                result.update(kind="QUERY_BLOCKER", question=body["question"])
            return result

    def check_contact(self, project_id, request_id, action_id, generation):
        """Re-read admission at the final controlled send point. This cannot fence an unrelated native writer."""
        with self._db(False) as db:
            project = self._project(db, project_id)
            request = self._request(db, project_id, request_id)
            contact = self._contact(db, project_id, request_id)
            require(contact and contact["action_id"] == identifier(action_id), "ACTION_MISMATCH")
            require(project["generation"] == request["target_generation"] == contact["generation"] ==
                    _generation(generation), "STALE_GENERATION")
            require((project["owner_principal"], project["endpoint"]) == (contact["owner"], contact["endpoint"]),
                    "OWNER_BINDING_MISMATCH")
            require(not request["cancel_ref"], "CANCEL_REQUESTED")
            require(contact["state"] == "INTENT", "CONTACT_ALREADY_DECIDED")
            deadline = json.loads(request["body"])["deadline"]
            require(timestamp(deadline) > self._now(), "DEADLINE_EXPIRED")
            return {"action_id": action_id, "generation": generation, "owner_principal": contact["owner"],
                    "endpoint": contact["endpoint"], "deadline": deadline}

    def record_contact(self, project_id, request_id, action_id, outcome, evidence_ref, *, reason=None):
        require(outcome in ("SUBMITTED", "UNKNOWN", "NOT_SENT"), "INVALID_TRANSPORT_OUTCOME")
        text(evidence_ref)
        if reason is None:
            reason = {"SUBMITTED": "NATIVE_SUBMISSION", "NOT_SENT": "SEND_FAILED", "UNKNOWN": "UNKNOWN_CAUSE"}[outcome]
        require(isinstance(reason, str) and reason in CONTACT_REASONS
                and (reason == "NATIVE_SUBMISSION") == (outcome == "SUBMITTED"), "INVALID_CONTACT_REASON")
        with self._db() as db:
            row = self._contact(db, project_id, request_id)
            require(row and row["action_id"] == identifier(action_id), "ACTION_MISMATCH")
            if row["state"] in ("INTENT", "UNKNOWN"):
                db.execute("UPDATE contacts SET state=?,transport_ref=?,reason=? WHERE action_id=?",
                           (outcome, evidence_ref, reason, action_id))
                req = self._request(db, project_id, request_id)
                wait = json.loads(req["waiting"])
                if not req["cancel_ref"]:
                    if outcome == "SUBMITTED":
                        wait.update(action_owner=row["owner"], reason="AWAIT_OWNER_REPLY",
                                    next_condition="Verified receipt or reply on the original contact action")
                    else:
                        wait.update(action_owner=self.coordinator_id,
                                    reason="RECONCILE_ORIGINAL_CONTACT" if outcome == "UNKNOWN" else "CONTACT_NOT_SENT",
                                    next_condition="Read original native action evidence and report the next authorized step")
                wait["evidence_ref"] = evidence_ref
                wait["contact_reason"] = reason
                self._wait(db, project_id, request_id, wait)
            elif row["state"] != outcome:
                require(row["state"] in ("OWNER_READ", "REPLIED", "STOPPED") and outcome != "NOT_SENT",
                        "TRANSPORT_CONFLICT")
            self._event(db, project_id, request_id, "TRANSPORT_" + outcome, "local:transport",
                        {"action_id": action_id, "evidence_ref": evidence_ref, "reason": reason},
                        notify=outcome in ("UNKNOWN", "NOT_SENT") and row["state"] == "INTENT")
            return self._status(db, project_id, request_id)

    def _verified_contact(self, db, principal, p, r, action_id, epoch, endpoint):
        row = self._contact(db, p, r)
        require(row and row["owner"] == text(principal) and row["action_id"] == identifier(action_id)
                and row["generation"] == _generation(epoch) and row["endpoint"] == text(endpoint),
                "ACCESS_DENIED")
        return row, self._project(db, p)["generation"] == epoch

    def owner_read(self, principal, project_id, request_id, action_id, generation, endpoint, evidence_ref):
        text(evidence_ref)
        with self._db() as db:
            require(self._request(db, project_id, request_id)["kind"] == "QUERY_BLOCKER", "OPERATION_NOT_ALLOWED")
            row, current = self._verified_contact(db, principal, project_id, request_id, action_id, generation, endpoint)
            self._event(db, project_id, request_id, "OWNER_READ" if current else "LATE_OWNER_READ", principal,
                        {"action_id": action_id, "evidence_ref": evidence_ref})
            if current:
                require(row["state"] not in ("NOT_SENT", "STOPPED"), "CONTACT_TERMINAL")
                db.execute("UPDATE contacts SET read_ref=COALESCE(read_ref,?),"
                           " state=CASE WHEN state='REPLIED' THEN state ELSE 'OWNER_READ' END WHERE action_id=?",
                           (evidence_ref, action_id))
                req = self._request(db, project_id, request_id)
                if not req["cancel_ref"] and row["state"] not in ("REPLIED", "OWNER_READ"):
                    wait = json.loads(req["waiting"])
                    wait.update(action_owner=principal, reason="OWNER_READ_AWAIT_REPLY",
                                next_condition="Verified owner reply on the original contact action", evidence_ref=evidence_ref)
                    self._wait(db, project_id, request_id, wait)
            return {"accepted": current, "late": not current}

    def _save_result(self, db, p, r, result):
        digest = canonical_hash(result)
        existing = db.execute("SELECT version FROM results WHERE project_id=? AND request_id=? AND result_hash=?",
                              (p, r, digest)).fetchone()
        if existing:
            return self._delivery(db, p, r, existing["version"])
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM results WHERE project_id=? AND request_id=?",
                             (p, r)).fetchone()[0]
        event_id = "coordinate-" + canonical_hash([self.coordinator_id, p, r, result["job_id"], version])
        db.execute("INSERT INTO results(project_id,request_id,version,result_hash,body,event_id) VALUES(?,?,?,?,?,?)",
                   (p, r, version, digest, canonical(result).decode(), event_id))
        self._event(db, p, r, "RESULT_AVAILABLE", self.coordinator_id, {"version": version, "result_hash": digest},
                    notify=True)
        return self._delivery(db, p, r, version)

    @staticmethod
    def _delivery(db, p, r, version):
        require(type(version) is int and 0 < version < 2**63, "INVALID_VERSION")
        row = db.execute("SELECT * FROM results WHERE project_id=? AND request_id=? AND version=?",
                         (p, r, version)).fetchone()
        require(row is not None, "RESULT_NOT_READY")
        body = json.loads(row["body"])
        return {"project_id": p, "request_id": r, "job_id": body["job_id"],
                "payload_hash": body["payload_hash"], "version": version,
                "result_hash": row["result_hash"], "result": body, "event_id": row["event_id"]}

    def reply(self, principal, project_id, request_id, action_id, generation, endpoint, evidence_ref, report):
        text(evidence_ref)
        fields(report, "blocker impact evidence_refs verified unverified next_owner next_condition observed_at")
        for key in ("blocker", "impact", "next_owner", "next_condition"):
            text(report[key], 8192)
        for key in ("evidence_refs", "verified", "unverified"):
            strings(report[key], required=key == "evidence_refs")
        timestamp(report["observed_at"])
        with self._db() as db:
            require(self._request(db, project_id, request_id)["kind"] == "QUERY_BLOCKER", "OPERATION_NOT_ALLOWED")
            row, current = self._verified_contact(db, principal, project_id, request_id, action_id, generation, endpoint)
            request = json.loads(self._request(db, project_id, request_id)["body"])
            result = {"schema_version": 1, "kind": "BLOCKER_REPLY", "project_id": project_id,
                      "request_id": request_id, "job_id": request["job_id"], "payload_hash": request["payload_hash"],
                      "task_ref": request["task_ref"], "action_id": action_id, "generation": generation,
                      "source_principal": principal, "source_evidence_ref": evidence_ref, "report": report}
            if not current:
                self._event(db, project_id, request_id, "LATE_OWNER_REPLY", principal, result)
                return {"accepted": False, "late": True}
            require(row["state"] not in ("NOT_SENT", "STOPPED"), "CONTACT_TERMINAL")
            digest = canonical_hash(result)
            require(not row["reply_hash"] or row["reply_hash"] == digest, "REPLY_CONFLICT")
            if not row["reply_hash"]:
                self._event(db, project_id, request_id, "VERIFIED_REPLY", principal,
                            {"action_id": action_id, "generation": generation, "evidence_ref": evidence_ref})
                db.execute("UPDATE contacts SET state='REPLIED',reply_hash=? WHERE action_id=?", (digest, action_id))
                wait = {"follow_up_owner": self.coordinator_id, "action_owner": report["next_owner"],
                        "reason": "VERIFY_UNBLOCK_CONDITION", "next_condition": report["next_condition"],
                        "deadline": request["deadline"], "escalate_to": self._project(db, project_id)["cloud_dot_principal"],
                        "evidence_ref": evidence_ref}
                self._wait(db, project_id, request_id, wait)
            return {"accepted": True, "late": False, "delivery": self._save_result(db, project_id, request_id, result)}

    def goal_update(self, principal, project_id, request_id, action_id, generation, endpoint, evidence_ref, report):
        """Apply an authenticated owner's business observation, separately from transport delivery.

        Evidence contracts are checked here; listening quality itself is attested by the
        named native reviewer, not inferred from a counter, ASR output, or this ledger.
        """
        text(evidence_ref)
        base = "seq phase summary observed_at evidence_refs next_owner"
        fields(report, base, "next_check_at terminal_condition criteria_evidence independent_review")
        phase, seq = report["phase"], report["seq"]
        require(phase in ("ACCEPTED", "IN_PROGRESS", "BLOCKED", "COMPLETED"), "INVALID_GOAL_PHASE")
        require(type(seq) is int and 0 < seq < 2**63, "INVALID_GOAL_SEQUENCE")
        text(report["summary"], 8192)
        text(report["next_owner"])
        strings(report["evidence_refs"], required=True)
        observed = timestamp(report["observed_at"])
        digest = canonical_hash(report)
        with self._db() as db:
            request = self._request(db, project_id, request_id)
            require(request["kind"] == "DELEGATE_GOAL", "OPERATION_NOT_ALLOWED")
            contact, current = self._verified_contact(db, principal, project_id, request_id, action_id, generation, endpoint)
            body = json.loads(request["body"])
            goal = body["goal"]
            row = db.execute("SELECT * FROM goals WHERE project_id=? AND request_id=?",
                             (project_id, request_id)).fetchone()
            prior = db.execute("SELECT report_hash,result_version FROM goal_updates WHERE project_id=? AND request_id=? AND seq=?",
                               (project_id, request_id, seq)).fetchone()
            if prior:
                require(prior["report_hash"] == digest, "GOAL_UPDATE_CONFLICT")
                return {"accepted": True, "late": False,
                        "delivery": self._delivery(db, project_id, request_id, prior["result_version"])}
            now = self._now()
            late = not current or request["cancel_ref"] or contact["state"] == "STOPPED" or timestamp(body["deadline"]) <= now
            # Late evidence has its own sequence; it never advances the adopted
            # report cursor or creates a result. Reuse the durable audit log,
            # including evidence written by older installations.
            last_late = db.execute("SELECT json_extract(body,'$.report') FROM events"
                                   " WHERE project_id=? AND request_id=? AND kind='LATE_GOAL_OBSERVATION'"
                                   " ORDER BY seq DESC LIMIT 1",
                                   (project_id, request_id)).fetchone()
            last_late = json.loads(last_late[0]) if last_late else None
            if last_late and seq <= last_late["seq"]:
                late_reports = db.execute("SELECT json_extract(body,'$.report') FROM events"
                                          " WHERE project_id=? AND request_id=? AND kind='LATE_GOAL_OBSERVATION'"
                                          " AND json_extract(body,'$.report.seq')=?",
                                          (project_id, request_id, seq)).fetchall()
                require(late_reports and all(canonical_hash(json.loads(item[0])) == digest for item in late_reports),
                        "GOAL_UPDATE_CONFLICT")
                return {"accepted": False, "late": True}
            late = bool(late or last_late)
            require(late or row["state"] != "COMPLETED", "GOAL_TERMINAL")
            require(seq == max(row["last_seq"], last_late["seq"] if last_late else 0) + 1, "GOAL_SEQUENCE_GAP")
            require(observed <= now, "FUTURE_OBSERVATION")
            require(observed >= timestamp(request["created_at"]), "OBSERVATION_BEFORE_REQUEST")
            if last_late and last_late["seq"] >= row["last_seq"]:
                require(observed >= timestamp(last_late["observed_at"]), "OBSERVATION_TIME_REGRESSION")
            elif row["last_seq"]:
                previous = db.execute("SELECT r.body FROM goal_updates u JOIN results r"
                                      " ON r.project_id=u.project_id AND r.request_id=u.request_id"
                                      " AND r.version=u.result_version WHERE u.project_id=? AND u.request_id=? AND u.seq=?",
                                      (project_id, request_id, row["last_seq"])).fetchone()
                require(observed >= timestamp(json.loads(previous["body"])["report"]["observed_at"]),
                        "OBSERVATION_TIME_REGRESSION")
            if phase == "COMPLETED":
                extra = " terminal_condition criteria_evidence"
                if goal["review_requirement"] == "independent_listening":
                    extra += " independent_review"
                fields(report, base + extra)
                text(report["terminal_condition"], 8192)
                checks = report["criteria_evidence"]
                require(isinstance(checks, list) and len(checks) == len(goal["acceptance_criteria"]),
                        "GOAL_CRITERIA_INCOMPLETE")
                covered = []
                for check in checks:
                    fields(check, "criterion evidence_refs")
                    text(check["criterion"])
                    strings(check["evidence_refs"], required=True)
                    covered.append(check["criterion"])
                require(len(set(covered)) == len(covered) and set(covered) == set(goal["acceptance_criteria"]),
                        "GOAL_CRITERIA_INCOMPLETE")
                if goal["review_requirement"] == "independent_listening":
                    review = report["independent_review"]
                    fields(review, "state reviewer producer scope_refs evidence_refs method")
                    require(review["state"] == "VERIFIED" and review["method"] in ("human_listening", "audio_perception"),
                            "INDEPENDENT_REVIEW_REQUIRED")
                    text(review["reviewer"])
                    text(review["producer"])
                    require(canonical_ref(review["reviewer"]) and canonical_ref(review["producer"]) and
                            review["reviewer"] != review["producer"], "INDEPENDENT_REVIEW_REQUIRED")
                    strings(review["scope_refs"], required=True)
                    strings(review["evidence_refs"], required=True)
                    require(len(review["scope_refs"]) == len(goal["scope_refs"]) and
                            set(review["scope_refs"]) == set(goal["scope_refs"]), "REVIEW_SCOPE_INCOMPLETE")
            else:
                fields(report, base + " next_check_at")
                require(timestamp(report["next_check_at"]) <= timestamp(body["deadline"]), "INVALID_CHECKPOINT")
            result = {"schema_version": 1, "kind": "GOAL_UPDATE", "project_id": project_id,
                      "request_id": request_id, "job_id": body["job_id"], "payload_hash": body["payload_hash"],
                      "task_ref": body["task_ref"], "work_key": goal["work_key"], "action_id": action_id,
                      "generation": generation, "source_principal": principal, "source_evidence_ref": evidence_ref,
                      "goal_state": phase, "report": report}
            if late:
                self._event(db, project_id, request_id, "LATE_GOAL_OBSERVATION", principal, result, notify=True)
                return {"accepted": False, "late": True}
            require(contact["state"] not in ("NOT_SENT", "STOPPED"), "CONTACT_TERMINAL")
            if phase == "ACCEPTED":
                require(row["accepted_seq"] is None, "GOAL_ALREADY_ACCEPTED")
            elif phase in ("IN_PROGRESS", "COMPLETED"):
                require(row["accepted_seq"] is not None, "GOAL_ACCEPTANCE_REQUIRED")
            if phase != "COMPLETED":
                require(timestamp(report["next_check_at"]) > observed, "CHECKPOINT_EXPIRED")
            db.execute("UPDATE goals SET state=?,last_seq=?,accepted_seq=CASE WHEN ?='ACCEPTED' THEN ? ELSE accepted_seq END"
                       " WHERE project_id=? AND request_id=?", (phase, seq, phase, seq, project_id, request_id))
            # An active goal keeps the original owner fenced until completion or an
            # independently attested stop. A business progress report is not a release.
            db.execute("UPDATE contacts SET state=? WHERE action_id=?",
                       ("REPLIED" if phase == "COMPLETED" else "OWNER_READ", action_id))
            if phase == "COMPLETED":
                db.execute("UPDATE requests SET unblock_ref=? WHERE project_id=? AND request_id=?",
                           (evidence_ref, project_id, request_id))
            wait = {"follow_up_owner": self.coordinator_id, "action_owner": report["next_owner"],
                    "reason": "GOAL_" + phase,
                    "next_condition": report.get("terminal_condition", report["summary"]),
                    "deadline": body["deadline"], "escalate_to": self._project(db, project_id)["cloud_dot_principal"],
                    "evidence_ref": evidence_ref}
            if phase != "COMPLETED":
                wait["next_check_at"] = report["next_check_at"]
            self._wait(db, project_id, request_id, wait)
            delivery = self._save_result(db, project_id, request_id, result)
            db.execute("INSERT INTO goal_updates VALUES(?,?,?,?,?)",
                       (project_id, request_id, seq, digest, delivery["version"]))
            self._event(db, project_id, request_id, "GOAL_" + phase, principal,
                        {"action_id": action_id, "seq": seq, "report_hash": digest, "evidence_ref": evidence_ref}, notify=True)
            return {"accepted": True, "late": False, "delivery": delivery}

    def delivery(self, project_id, request_id, version=1):
        with self._db(False) as db:
            return self._delivery(db, project_id, request_id, version)

    def pilot_outbox_version(self, project_id, request_id, *, version=None, receipt=None):
        """Next contiguous version, advanced only by the local pump's verified outbox receipt."""
        with self._db(version is not None) as db:
            row = db.execute("SELECT body FROM events WHERE project_id=? AND request_id=? AND kind='PILOT_RESULT_ENQUEUED'"
                             " ORDER BY seq DESC LIMIT 1", (project_id, request_id)).fetchone()
            next_version = json.loads(row["body"])["version"] + 1 if row else 1
            if version is None:
                return next_version
            original = self._delivery(db, project_id, request_id, version)
            require(receipt["state"] in ("PENDING", "SENT") and original["job_id"] == receipt["job_id"]
                    and original["event_id"] == receipt["event_id"], "OUTBOX_RECEIPT_MISMATCH")
            require(version <= next_version, "OUTBOX_VERSION_GAP")
            if version == next_version:
                self._event(db, project_id, request_id, "PILOT_RESULT_ENQUEUED", "local:original-bridge",
                            {"version": version, "event_id": receipt["event_id"], "wire_hash": receipt["result_hash"]})
                return version + 1
            return next_version

    def result(self, principal, project_id, request_id, version=1):
        with self._db(False) as db:
            self._project(db, project_id, text(principal))
            return self._delivery(db, project_id, request_id, version)

    def ack(self, principal, project_id, request_id, version, result_hash, evidence_ref):
        text(evidence_ref)
        with self._db() as db:
            self._project(db, project_id, text(principal))
            result = self._delivery(db, project_id, request_id, version)
            require(result["result_hash"] == result_hash, "RESULT_HASH_MISMATCH")
            db.execute("UPDATE results SET read_ref=COALESCE(read_ref,?) WHERE project_id=? AND request_id=?"
                       " AND version=?", (evidence_ref, project_id, request_id, version))
            body = {"version": version, "result_hash": result_hash, "evidence_ref": evidence_ref}
            # Replaying an acknowledged receipt must not append a second event under its new name.
            if self._principal_column == LEGACY_PRINCIPAL_COLUMN:
                old_key = canonical_hash([project_id, request_id, "G8_READ", principal, body])
                if db.execute("SELECT 1 FROM events WHERE event_key=?", (old_key,)).fetchone():
                    return self._status(db, project_id, request_id)
            self._event(db, project_id, request_id, "CLOUD_DOT_READ", principal, body)
            return self._status(db, project_id, request_id)

    def cancel(self, principal, project_id, request_id, evidence_ref):
        text(evidence_ref)
        with self._db() as db:
            self._project(db, project_id, text(principal))
            row = self._request(db, project_id, request_id)
            if not row["cancel_ref"]:
                db.execute("UPDATE requests SET cancel_ref=? WHERE project_id=? AND request_id=?",
                           (evidence_ref, project_id, request_id))
                self._event(db, project_id, request_id, "CANCEL_REQUESTED", principal,
                            {"evidence_ref": evidence_ref}, notify=True)
                wait = json.loads(row["waiting"])
                wait.update(reason="RECONCILE_CANCELLED_ACTION", action_owner=self.coordinator_id,
                            next_condition="Establish original action effects and actual stop from native evidence",
                            evidence_ref=evidence_ref)
                self._wait(db, project_id, request_id, wait)
            return self._status(db, project_id, request_id)

    def unblock(self, principal, project_id, request_id, generation, endpoint, condition, evidence_ref):
        """Record the current owner's verification of the previously agreed condition."""
        text(evidence_ref)
        with self._db() as db:
            project = self._project(db, project_id)
            require((project["owner_principal"], project["generation"], project["endpoint"]) ==
                    (text(principal), _generation(generation), text(endpoint)), "ACCESS_DENIED")
            request = self._request(db, project_id, request_id)
            require(request["kind"] == "QUERY_BLOCKER", "OPERATION_NOT_ALLOWED")
            contact = self._contact(db, project_id, request_id)
            require(contact and contact["reply_hash"], "REPLY_REQUIRED")
            wait = json.loads(request["waiting"])
            require(wait["next_condition"] == text(condition, 8192), "CONDITION_MISMATCH")
            if request["unblock_ref"]:
                require(request["unblock_ref"] == evidence_ref, "UNBLOCK_CONFLICT")
                return self._status(db, project_id, request_id)
            db.execute("UPDATE requests SET unblock_ref=? WHERE project_id=? AND request_id=?",
                       (evidence_ref, project_id, request_id))
            self._schedule(db, project_id, request_id, wait)
            body = json.loads(request["body"])
            self._save_result(db, project_id, request_id,
                              {"schema_version": 1, "kind": "OWNER_VERIFIED_UNBLOCK", "project_id": project_id,
                               "request_id": request_id, "job_id": body["job_id"], "payload_hash": body["payload_hash"],
                               "task_ref": body["task_ref"], "generation": generation, "condition": condition,
                               "source_principal": principal, "source_evidence_ref": evidence_ref})
            return self._status(db, project_id, request_id)

    def health(self, principal, project_id):
        """A project-scoped ledger observation. Channel/owner liveness needs native evidence."""
        return self._health(project_id, text(principal))

    def local_health(self, project_id):
        return self._health(project_id, None)

    def _health(self, project_id, principal):
        with self._db(False) as db:
            project = self._project(db, project_id, principal)
            open_requests = (" FROM requests r WHERE project_id=? AND unblock_ref IS NULL"
                             " AND (cancel_ref IS NULL OR EXISTS(SELECT 1 FROM contacts c"
                             " WHERE c.project_id=r.project_id AND c.request_id=r.request_id"
                             " AND c.state IN ('INTENT','UNKNOWN','SUBMITTED','OWNER_READ')))")
            pending = db.execute("SELECT COUNT(*),MIN(created_at)" + open_requests, (project_id,)).fetchone()
            oldest = db.execute("SELECT request_id,waiting" + open_requests + " ORDER BY created_at,request_id LIMIT 1",
                                (project_id,)).fetchone()
            next_due = db.execute("SELECT request_id,due,phase FROM wait_schedule WHERE project_id=? AND due IS NOT NULL"
                                  " ORDER BY due,request_id LIMIT 1", (project_id,)).fetchone()
            observations = {}
            for label, kinds in (("last_intake", ("REQUEST_ACCEPTED",)), ("last_transport_failure", ("TRANSPORT_UNKNOWN",
                                "TRANSPORT_NOT_SENT", "RECOVERED_UNKNOWN")), ("last_owner_reply", ("VERIFIED_REPLY",))):
                marks = ",".join("?" for _ in kinds)
                row = db.execute(f"SELECT request_id,kind,observed_at FROM events WHERE project_id=?"
                                 f" AND kind IN ({marks}) ORDER BY seq DESC LIMIT 1",
                                 (project_id, *kinds)).fetchone()
                observations[label] = dict(row) if row else None
            return {"coordinator_id": self.coordinator_id, "project_id": project_id, "generation": project["generation"],
                    "assignment_state": project["assignment_state"], "observed_at": self._now().isoformat(),
                    "scope": "LOCAL_LEDGER", "open_followups": pending[0], "oldest_open_at": pending[1],
                    "oldest_request": oldest["request_id"] if oldest else None,
                    "oldest_wait": json.loads(oldest["waiting"]) if oldest else None,
                    "next_checkpoint": dict(next_due) if next_due else None, **observations}

    def manage(self, operation, data):
        """Privileged local operations. Never map inbound message.operation to this method."""
        require(operation in ADMIN_OPERATIONS, "ADMIN_OPERATION_NOT_ALLOWED")
        canonical(data)
        with self._db(operation not in ("notifications", "events")) as db:
            return getattr(self, "_admin_" + operation)(db, data)

    def _admin_register_project(self, db, data):
        fields(data, "project_id repository project_ref cloud_dot_principal report_route")
        p = identifier(data["project_id"])
        for key in ("project_ref", "cloud_dot_principal", "report_route"):
            text(data[key])
        require(isinstance(data["repository"], str) and
                re.fullmatch(r"[\w.-]+/[\w.-]+", data["repository"], re.ASCII), "INVALID_REPOSITORY")
        if db.execute("SELECT 1 FROM projects WHERE project_id=?", (p,)).fetchone():
            prev = self._project(db, p)
            require(all(prev[k] == v for k, v in data.items()), "REGISTRATION_CONFLICT")
        else:
            db.execute(f"INSERT INTO projects(project_id,repository,project_ref,{self._principal_column},report_route)"
                       " VALUES(:project_id,:repository,:project_ref,:cloud_dot_principal,:report_route)", data)
            self._event(db, p, None, "PROJECT_REGISTERED", "local:administrator", data)
        return self._project(db, p)

    @staticmethod
    def _no_unresolved_contacts(db, p):
        row = db.execute("SELECT 1 FROM contacts WHERE project_id=? AND state IN (?,?,?,?) LIMIT 1",
                         (p, *UNRESOLVED)).fetchone()
        require(row is None, "OUTSTANDING_CONTACT")

    def _admin_activate_owner(self, db, data):
        fields(data, "project_id expected_generation owner_principal endpoint appointment_ref verification_ref "
                     "handoff_ref release_ref", "creation_id")
        p, expected = identifier(data["project_id"]), _generation(data["expected_generation"])
        for key in ("owner_principal", "endpoint", "appointment_ref", "verification_ref", "handoff_ref", "release_ref"):
            text(data[key])
        project = self._project(db, p)
        proof = canonical(data).decode()
        if project["generation"] == expected + 1:
            old = db.execute("SELECT proof FROM assignments WHERE project_id=? AND generation=?",
                             (p, expected + 1)).fetchone()
            if old and old["proof"] == proof:
                return dict(project)
        require(project["generation"] == expected, "STALE_GENERATION")
        self._no_unresolved_contacts(db, p)
        creation = db.execute("SELECT * FROM creations WHERE project_id=? AND state IN ('INTENT','UNKNOWN','CREATED')",
                              (p,)).fetchone()
        if creation or "creation_id" in data:
            require(creation and creation["creation_id"] == data.get("creation_id") and creation["state"] == "CREATED"
                    and creation["candidate_ref"] == data["endpoint"], "CREATION_NOT_RECONCILED")
            db.execute("UPDATE creations SET state='BOUND' WHERE project_id=? AND creation_id=?", (p, creation["creation_id"]))
        db.execute("INSERT INTO assignments VALUES(?,?,?)", (p, expected + 1, proof))
        db.execute("UPDATE projects SET generation=?,owner_principal=?,endpoint=?,assignment_state='BOUND'"
                   " WHERE project_id=? AND generation=?", (expected + 1, data["owner_principal"], data["endpoint"], p, expected))
        self._event(db, p, None, "OWNER_HANDOFF_ATTESTED", "local:administrator", data)
        return self._project(db, p)

    def _admin_confirm_vacancy(self, db, data):
        fields(data, "project_id expected_generation vacancy_ref release_ref")
        p, expected = identifier(data["project_id"]), _generation(data["expected_generation"])
        text(data["vacancy_ref"])
        text(data["release_ref"])
        project = self._project(db, p)
        require(project["generation"] == expected, "STALE_GENERATION")
        self._no_unresolved_contacts(db, p)
        require(not db.execute("SELECT 1 FROM creations WHERE project_id=? AND state IN ('INTENT','UNKNOWN','CREATED')",
                               (p,)).fetchone(), "CREATION_NOT_RECONCILED")
        # A previously active authority loses its generation even before a successor is known.
        next_generation = expected + int(project["owner_principal"] is not None)
        db.execute("UPDATE projects SET generation=?,owner_principal=NULL,endpoint=NULL,"
                   "assignment_state='VACANCY_CONFIRMED' WHERE project_id=?", (next_generation, p))
        self._event(db, p, None, "VACANCY_ATTESTED", "local:administrator", data)
        return self._project(db, p)

    def _admin_begin_creation(self, db, data):
        fields(data, "project_id expected_generation creation_id vacancy_ref release_ref authorization_ref")
        p, cid, epoch = identifier(data["project_id"]), identifier(data["creation_id"]), _generation(data["expected_generation"])
        for key in ("vacancy_ref", "release_ref", "authorization_ref"):
            text(data[key])
        project = self._project(db, p)
        digest = canonical_hash(data)
        old = db.execute("SELECT * FROM creations WHERE project_id=? AND creation_id=?", (p, cid)).fetchone()
        if old:
            require(old["input_hash"] == digest, "CREATION_CONFLICT")
            return {"create": False, "creation_id": cid, "generation": epoch, "state": old["state"]}
        require(project["generation"] == epoch, "STALE_GENERATION")
        require(project["owner_principal"] is None, "OWNER_ALREADY_BOUND")
        self._no_unresolved_contacts(db, p)
        try:
            db.execute("INSERT INTO creations(project_id,creation_id,generation,input_hash,state) VALUES(?,?,?,?,'INTENT')",
                       (p, cid, epoch, digest))
        except sqlite3.IntegrityError as error:
            raise CoordinatorError("CREATION_NOT_RECONCILED") from error
        db.execute("UPDATE projects SET assignment_state='VACANCY_CONFIRMED' WHERE project_id=?", (p,))
        self._event(db, p, None, "CREATION_INTENT", "local:administrator", data)
        return {"create": True, "creation_id": cid, "generation": epoch, "state": "INTENT"}

    def _admin_record_creation(self, db, data):
        fields(data, "project_id expected_generation creation_id outcome evidence_ref", "candidate_ref")
        p, cid = identifier(data["project_id"]), identifier(data["creation_id"])
        epoch = _generation(data["expected_generation"])
        outcome, evidence = data["outcome"], text(data["evidence_ref"])
        require(outcome in ("UNKNOWN", "NOT_CREATED", "CREATED"), "INVALID_CREATION_OUTCOME")
        candidate = text(data.get("candidate_ref")) if outcome == "CREATED" else None
        require(outcome == "CREATED" or "candidate_ref" not in data, "INVALID_FIELDS")
        project = self._project(db, p)
        old = db.execute("SELECT * FROM creations WHERE project_id=? AND creation_id=?", (p, cid)).fetchone()
        require(old and old["generation"] == epoch, "CREATION_MISMATCH")
        require(project["generation"] == epoch, "STALE_GENERATION")
        require(old["state"] in ("INTENT", "UNKNOWN") or
                (old["state"], old["candidate_ref"], old["evidence_ref"]) == (outcome, candidate, evidence),
                "CREATION_CONFLICT")
        db.execute("UPDATE creations SET state=?,candidate_ref=?,evidence_ref=? WHERE project_id=? AND creation_id=?",
                   (outcome, candidate, evidence, p, cid))
        self._event(db, p, None, "CREATION_" + outcome, "local:native-observer", data)
        return {"create": False, "creation_id": cid, "generation": epoch, "state": outcome}

    def _admin_recover(self, db, data):
        fields(data, "evidence_ref")
        witness = text(data["evidence_ref"])
        contacts = db.execute("SELECT project_id,request_id,action_id FROM contacts WHERE state='INTENT'").fetchall()
        for row in contacts:
            self._event(db, row["project_id"], row["request_id"], "RECOVERED_UNKNOWN", "local:administrator",
                        {"action_id": row["action_id"], "evidence_ref": witness}, notify=True)
            request = self._request(db, row["project_id"], row["request_id"])
            wait = json.loads(request["waiting"])
            wait.update(action_owner=self.coordinator_id, reason="RECONCILE_CANCELLED_ACTION" if request["cancel_ref"]
                        else "RECONCILE_ORIGINAL_CONTACT", next_condition="Read original native action evidence",
                        evidence_ref=witness)
            self._wait(db, row["project_id"], row["request_id"], wait)
        creations = db.execute("SELECT project_id,creation_id FROM creations WHERE state='INTENT'").fetchall()
        for row in creations:
            self._event(db, row["project_id"], None, "CREATION_RECOVERED_UNKNOWN", "local:administrator",
                        {"creation_id": row["creation_id"], "evidence_ref": witness})
        db.execute("UPDATE contacts SET state='UNKNOWN',reason='ACK_UNCONFIRMED' WHERE state='INTENT'")
        db.execute("UPDATE creations SET state='UNKNOWN' WHERE state='INTENT'")
        return {"contacts_unknown": len(contacts), "creations_unknown": len(creations), "resent": 0}

    def _admin_retarget(self, db, data):
        fields(data, "project_id request_id expected_generation authorization_ref")
        p, r = data["project_id"], data["request_id"]
        project, request = self._project(db, p), self._request(db, p, r)
        require(project["owner_principal"] and project["generation"] == _generation(data["expected_generation"]),
                "STALE_GENERATION")
        require(not request["cancel_ref"] and not self._contact(db, p, r), "ACTION_ALREADY_DECIDED")
        require(request["kind"] != "DELEGATE_GOAL" or
                json.loads(request["body"])["expected_generation"] == project["generation"],
                "GOAL_ORIGINAL_GENERATION_MISMATCH")
        text(data["authorization_ref"])
        db.execute("UPDATE requests SET target_generation=? WHERE project_id=? AND request_id=?",
                   (project["generation"], p, r))
        wait = json.loads(request["waiting"])
        wait.update(action_owner=self.coordinator_id, reason="CONTACT_ADMISSION",
                    next_condition="Contact the verified owner within the original request budget",
                    evidence_ref=data["authorization_ref"])
        self._wait(db, p, r, wait)
        self._event(db, p, r, "REQUEST_RETARGET_AUTHORIZED", "local:administrator", data)
        return self._status(db, p, r)

    def _admin_record_stop(self, db, data):
        fields(data, "project_id request_id action_id evidence_ref")
        p, r = data["project_id"], data["request_id"]
        contact = self._contact(db, p, r)
        require(contact and contact["action_id"] == identifier(data["action_id"]), "ACTION_MISMATCH")
        require(contact["state"] in (*UNRESOLVED, "STOPPED"), "CONTACT_TERMINAL")
        text(data["evidence_ref"])
        db.execute("UPDATE contacts SET state='STOPPED' WHERE action_id=?", (contact["action_id"],))
        wait = json.loads(self._request(db, p, r)["waiting"])
        wait.update(reason="NATIVE_STOP_ATTESTED", action_owner=self.coordinator_id,
                    next_condition="Report original action stop and reconcile remaining responsibility",
                    evidence_ref=data["evidence_ref"])
        self._wait(db, p, r, wait)
        self._event(db, p, r, "NATIVE_STOP_ATTESTED", "local:native-observer", data, notify=contact["state"] != "STOPPED")
        return self._status(db, p, r)

    def _admin_set_wait(self, db, data):
        fields(data, "project_id request_id action_owner reason next_condition deadline escalate_to evidence_ref",
               "next_check_at")
        p, r = data["project_id"], data["request_id"]
        request = self._request(db, p, r)
        require(not request["unblock_ref"], "ALREADY_UNBLOCKED")
        for key in ("action_owner", "reason", "next_condition", "escalate_to", "evidence_ref"):
            text(data[key])
        deadline = timestamp(data["deadline"])
        require(deadline <= timestamp(json.loads(request["body"])["deadline"]), "DEADLINE_EXPANDED")
        if "next_check_at" in data:
            require(timestamp(data["next_check_at"]) <= deadline, "INVALID_CHECKPOINT")
        wait = {k: v for k, v in data.items() if k not in ("project_id", "request_id")}
        wait["follow_up_owner"] = self.coordinator_id
        old = json.loads(request["waiting"])
        changed = any(wait.get(k) != old.get(k) for k in (set(old) | set(wait)) - {"evidence_ref"})
        revision = self._wait(db, p, r, wait)
        self._event(db, p, r, "WAIT_RECORDED", "local:administrator", {**wait, "revision": revision}, notify=changed)
        return self._status(db, p, r)

    def _admin_tick(self, db, data):
        """Materialize bounded due reminders only. Native contact and result reads have separate receipts."""
        fields(data, "", "limit project_id")
        limit = data.get("limit", 25)
        require(type(limit) is int and 1 <= limit <= 100, "INVALID_PAGE")
        now = self._now().isoformat()
        where, params = "due IS NOT NULL AND due<=?", (now,)
        if "project_id" in data:
            project = self._project(db, data["project_id"])
            where, params = "project_id=? AND " + where, (project["project_id"], now)
        select = "SELECT * FROM wait_schedule WHERE " + where + " ORDER BY due,project_id,request_id LIMIT ?"
        rows = db.execute(select, (*params, limit)).fetchall()
        items = []
        for entry in rows:
            p, r = entry["project_id"], entry["request_id"]
            wait = json.loads(self._request(db, p, r)["waiting"])
            phase = "DEADLINE" if now >= entry["deadline"] else entry["phase"]
            event = {"revision": entry["revision"], "phase": phase,
                     "due_at": entry["deadline"] if phase == "DEADLINE" else entry["due"],
                     "action_owner": wait["action_owner"], "next_condition": wait["next_condition"],
                     "escalate_to": wait["escalate_to"], "evidence_ref": wait["evidence_ref"]}
            self._event(db, p, r, "WAIT_" + phase, self.coordinator_id, event, notify=True)
            db.execute("UPDATE wait_schedule SET due=?,phase=? WHERE project_id=? AND request_id=?",
                       (entry["deadline"] if phase == "CHECKPOINT" else None,
                        "DEADLINE" if phase == "CHECKPOINT" else "ESCALATED", p, r))
            items.append({"project_id": p, "request_id": r, "phase": phase, "revision": entry["revision"]})
        return {"observed_at": now, "processed": len(items), "more_due": bool(db.execute(select, (*params, 1)).fetchone()),
                "items": items}

    def _admin_report_wait(self, db, data):
        fields(data, "project_id request_id evidence_ref")
        p, r = data["project_id"], data["request_id"]
        request = self._request(db, p, r)
        body = json.loads(request["body"])
        require(not request["unblock_ref"], "ALREADY_UNBLOCKED")
        result = {"schema_version": 1, "kind": "COORDINATOR_WAIT_REPORT",
                  "project_id": p, "request_id": r, "job_id": body["job_id"],
                  "payload_hash": body["payload_hash"], "task_ref": body["task_ref"],
                  "source_principal": self.coordinator_id, "source_evidence_ref": text(data["evidence_ref"]),
                  "waiting": json.loads(request["waiting"])}
        if request["kind"] == "DELEGATE_GOAL":
            goal = db.execute("SELECT state,last_seq FROM goals WHERE project_id=? AND request_id=?", (p, r)).fetchone()
            result.update(original_kind="DELEGATE_GOAL", goal_state=goal["state"], last_seq=goal["last_seq"])
        return self._save_result(db, p, r, result)

    def _admin_notifications(self, db, data):
        fields(data, "project_id", "after limit")
        self._project(db, data["project_id"])
        after, limit = data.get("after", 0), data.get("limit", 50)
        require(type(after) is int and 0 <= after < 2**63 and type(limit) is int and 1 <= limit <= 100, "INVALID_PAGE")
        rows = db.execute("SELECT n.seq,n.project_id,n.request_id,n.recipient,e.kind,e.observed_at"
                          " FROM notifications n JOIN events e ON e.seq=n.seq WHERE n.project_id=?"
                          " AND n.seq>? ORDER BY n.seq LIMIT ?", (data["project_id"], after, limit)).fetchall()
        return {"notifications": [dict(x) for x in rows], "next": rows[-1]["seq"] if rows else after}

    def _admin_events(self, db, data):
        fields(data, "project_id", "request_id after limit")
        p, after, limit = data["project_id"], data.get("after", 0), data.get("limit", 50)
        self._project(db, p)
        require(type(after) is int and 0 <= after < 2**63 and type(limit) is int and 1 <= limit <= 100, "INVALID_PAGE")
        r = identifier(data["request_id"]) if "request_id" in data else None
        rows = db.execute("SELECT seq,project_id,request_id,kind,source,observed_at,body FROM events"
                          " WHERE project_id=? AND (? IS NULL OR request_id=?) AND seq>? ORDER BY seq LIMIT ?",
                          (p, r, r, after, limit)).fetchall()
        return {"events": [{**dict(row), "body": json.loads(row["body"])} for row in rows],
                "next": rows[-1]["seq"] if rows else after}


ADMIN_OPERATIONS = {"register_project", "activate_owner", "confirm_vacancy", "begin_creation", "record_creation",
                    "recover", "retarget", "record_stop", "set_wait", "report_wait", "tick", "notifications", "events"}
CLI_READS = {"inspect": "inspect", "health": "local_health", "route": "route", "delivery": "delivery"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("operation", choices=sorted(ADMIN_OPERATIONS | CLI_READS.keys()))
    parser.add_argument("--input", type=Path, help="local JSON file with operation fields (optional for tick)")
    args = parser.parse_args(argv)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "DUPLICATE_KEY")
            value[key] = item
        return value
    try:
        require(args.input or args.operation == "tick", "INPUT_REQUIRED")
        raw = b"{}"
        if args.input:
            with args.input.open("rb") as stream:
                raw = stream.read(MAX_MESSAGE + 1)
        require(len(raw) <= MAX_MESSAGE, "MESSAGE_TOO_LARGE")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        require(isinstance(data, dict), "INVALID_FIELDS")
        canonical(data)
        c = Coordinator(args.state_dir, read_only=args.operation in CLI_READS or args.operation in ("events", "notifications"))
        result = (c.manage(args.operation, data) if args.operation in ADMIN_OPERATIONS
                  else getattr(c, CLI_READS[args.operation])(**data))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except CoordinatorError as error:
        code = error.code
    except (TypeError, ValueError, RecursionError):
        code = "INVALID_INPUT"
    except (OSError, sqlite3.Error):
        code = "LOCAL_STORE_ERROR"
    print(json.dumps({"error": {"code": code}}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
