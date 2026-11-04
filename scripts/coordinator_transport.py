"""项目协调员与原信箱桥、云端Dot客户端之间的接口。

The host supplies two *trusted* ports. The bridge owns its collector, original
jobs and idempotent outbox. The caller port authenticates native sessions and
attests the actual reader. Neither port may derive identity from job JSON, a
shared account's whoami, or a caller-supplied principal. This module does not
install a port, acquire OAuth credentials, poll the mailbox, or open a ledger.
"""

from dataclasses import dataclass
import re
from typing import Callable, Protocol

if __package__:
    from .project_coordinator import canonical_hash
else:
    from project_coordinator import canonical_hash


class TransportError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code):
    if not condition:
        raise TransportError(code)


@dataclass(frozen=True)
class Peer:
    channel: str
    subject: str


@dataclass(frozen=True)
class Grant:
    """Trusted deployment configuration, independent of the message body."""

    peer: Peer
    project_id: str
    principal: str
    sender_alias: str
    actions: frozenset[str]


@dataclass(frozen=True)
class VerifiedTask:
    """Authority readback of a Project item and its Issue/draft, not payload.tracking."""

    repository: str
    project_ref: str
    item_id: int
    task_ref: str
    evidence_ref: str


@dataclass(frozen=True)
class VerifiedJob:
    """The origin proof binds BOTH the original job_id and canonical hash.

    The bridge must keep origin_ref stable across redelivery. A relay's own
    authenticated connection does not by itself attest the original sender.
    """

    origin: Peer
    origin_ref: str
    job_id: str
    payload_hash: str
    record: dict
    task: VerifiedTask


@dataclass(frozen=True)
class VerifiedRead:
    """Receiver-native evidence of reading and acknowledging this exact result."""

    reader: Peer
    evidence_ref: str
    job_id: str
    event_id: str
    version: int
    result_hash: str


@dataclass(frozen=True)
class OwnerObservation:
    """An original, verified native receipt or answer for one contact action."""

    peer: Peer
    endpoint: str
    project_id: str
    request_id: str
    action_id: str
    generation: int
    kind: str
    evidence_ref: str
    report: dict | None = None
    report_hash: str | None = None


class OwnerChannel(Protocol):
    def submit_goal(self, intent: dict, *, verify_current: Callable[[], dict]) -> dict:
        """A separately authorized native GOAL endpoint, never the query adapter.

        Enforce the frozen goal's allowed actions, read scope and zero production /
        audio budget at the execution boundary. Verify current authority immediately
        before the one native submission; retain its action id for reconciliation.
        A submission receipt is not the owner's business acceptance. Later verified
        goal_update observations carry acceptance, progress and completion.
        """
        ...

    def submit_contact(self, intent: dict, *, verify_current: Callable[[], dict]) -> dict:
        """Invoke verify_current at the final controlled dispatch point, then
        enforce the native action, owner, endpoint, generation, read-only
        capabilities, cancellation, deadline and action-id deduplication. Return the
        original project/request/action/generation/owner/endpoint, outcome, reason and
        native evidence_ref. NOT_SENT requires evidence of definite nonacceptance.
        """
        ...

    def verified_observation(self, native_session: object, handle: object) -> OwnerObservation:
        """Resolve a native receipt or an answer with a proof of its exact bytes."""
        ...


class OriginalBridge(Protocol):
    def verified_job(self, job_id: str) -> VerifiedJob:
        """Deliver the original verified job only to its authorized coordinator."""
        ...

    def lookup_report(self, job_id: str, event_id: str) -> dict | None:
        """Return the original outbox record or confirmed absence. Unknown raises."""
        ...

    def enqueue_result(self, *, project_id: str, job_id: str, payload_hash: str,
                       event_id: str, status: str, result: dict) -> dict:
        """Authenticate the coordinator; atomically check association and dedup.

        Return the ordinary bridge outbox record, including final result_hash,
        state and any exact remote receipt. Conflicting event reuse must fail.
        The producer is independent of Codex Stop and the authenticated reader.
        """
        ...


class CallerChannel(Protocol):
    def authenticated_peer(self, native_session: object) -> Peer | None:
        """Resolve a channel-owned session handle, never a principal from JSON."""
        ...

    def read_result(self, native_session: object, job_id: str, version: int) -> dict:
        """Read this immutable server version with this caller's access rights."""
        ...

    def verified_read(self, native_session: object, receipt_handle: object) -> VerifiedRead:
        """Resolve a reader-bound receipt with verified original job/version/hash."""
        ...


ACTIONS = frozenset({'submit', 'submit_goal', 'status', 'result', 'ack', 'cancel', 'health',
                     'owner_read', 'owner_reply', 'goal_update'})
MAILBOX_LIMIT = 1024 * 1024
CONTACT_KEYS = ('project_id', 'request_id', 'action_id', 'generation', 'owner_principal', 'endpoint')
CONTACT_REASONS = {
    'SUBMITTED': frozenset({'NATIVE_SUBMISSION'}),
    'NOT_SENT': frozenset({'CHANNEL_UNAVAILABLE', 'OWNER_OFFLINE', 'IDENTITY_MISMATCH',
                           'STALE_GENERATION', 'POLICY_DENIED', 'SEND_FAILED'}),
    'UNKNOWN': frozenset({'RECEIPT_MISMATCH', 'ACK_UNCONFIRMED', 'UNKNOWN_CAUSE'}),
}
REQUEST_FIELDS = {'schema_version', 'request_id', 'idempotency_key', 'kind',
                  'expected_generation', 'deadline', 'authorization_ref', 'evidence_refs', 'budget'}


class CoordinatorTransport:
    """In-process boundary. Only named caller methods may be exposed remotely.

    deliver, contact and enqueue are host-internal operations. The service owning each
    actual endpoint must also enforce project/object/action access on raw reads,
    listing and writes. A wrapper cannot restrict another direct mailbox client.
    """

    def __init__(self, coordinator, *, grants, recipient, bridge: OriginalBridge,
                 callers: CallerChannel | None, owners: OwnerChannel | None = None):
        self.core, self.bridge, self.callers, self.owners = coordinator, bridge, callers, owners
        require(isinstance(recipient, str) and bool(recipient.strip()), 'INVALID_RECIPIENT')
        self.recipient = recipient
        self.grants = {}
        for grant in grants:
            require(isinstance(grant, Grant) and isinstance(grant.peer, Peer)
                    and all(isinstance(x, str) and x.strip() for x in
                            (grant.project_id, grant.principal, grant.sender_alias,
                             grant.peer.channel, grant.peer.subject))
                    and isinstance(grant.actions, frozenset) and grant.actions
                    and grant.actions <= ACTIONS, 'INVALID_GRANT')
            key = grant.peer, grant.project_id
            require(key not in self.grants, 'AMBIGUOUS_GRANT')
            self.grants[key] = grant

    def _grant(self, peer, project_id, action):
        require(isinstance(peer, Peer) and isinstance(peer.channel, str)
                and isinstance(peer.subject, str) and isinstance(project_id, str), 'ACCESS_DENIED')
        grant = self.grants.get((peer, project_id))
        require(grant is not None and action in grant.actions, 'ACCESS_DENIED')
        return grant

    def _caller(self, session, project_id, action):
        require(self.callers is not None, 'CALLER_CHANNEL_UNBOUND')
        return self._grant(self.callers.authenticated_peer(session), project_id, action)

    def _original(self, job_id):
        require(self.bridge is not None, 'BRIDGE_UNBOUND')
        delivery = self.bridge.verified_job(job_id)
        require(isinstance(delivery, VerifiedJob) and isinstance(delivery.origin, Peer)
                and isinstance(delivery.origin.channel, str) and isinstance(delivery.origin.subject, str)
                and isinstance(delivery.origin_ref, str) and delivery.origin_ref.strip()
                and isinstance(delivery.record, dict), 'ORIGIN_UNVERIFIED')
        record = delivery.record
        payload = record.get('payload')
        require(isinstance(payload, dict) and record.get('job_id') == delivery.job_id == job_id
                and canonical_hash(payload, max_bytes=MAILBOX_LIMIT) == record.get('payload_hash') == delivery.payload_hash,
                'ORIGINAL_JOB_MISMATCH')
        return delivery, payload

    @staticmethod
    def _verify_task(delivery, payload, route):
        task, tracking = delivery.task, payload.get('tracking')
        require(isinstance(task, VerifiedTask) and isinstance(tracking, dict)
                and isinstance(task.evidence_ref, str) and task.evidence_ref.strip()
                and tracking.get('repository') == task.repository == route['repository']
                and tracking.get('project_url') == task.project_ref == route['project_ref']
                and type(task.item_id) is int and type(tracking.get('item_id')) is int
                and task.item_id == tracking['item_id'] and task.item_id > 0, 'PROJECT_BINDING_MISMATCH')
        if 'issue_url' in tracking and 'draft_id' not in tracking:
            issue = tracking['issue_url']
            match = re.fullmatch(r'https://github\.com/' + re.escape(task.repository) + r'/issues/([1-9][0-9]*)',
                                 issue) if isinstance(issue, str) else None
            require(match and task.task_ref == issue and payload.get('origin_task_id') in
                    (issue, f'github:{task.repository}#{match[1]}'), 'TASK_BINDING_MISMATCH')
        else:
            draft = tracking.get('draft_id')
            require('issue_url' not in tracking and isinstance(draft, str) and re.fullmatch(r'DI_[A-Za-z0-9_-]{1,125}', draft)
                    and payload.get('origin_task_id') == task.task_ref == draft, 'TASK_BINDING_MISMATCH')

    @staticmethod
    def _origin(delivery):
        return {'channel': delivery.origin.channel, 'subject': delivery.origin.subject, 'origin_ref': delivery.origin_ref,
                'task_evidence_ref': delivery.task.evidence_ref}

    def _verify_route(self, delivery, payload, project_id, *, admitted_request=None):
        grant = self.grants.get((delivery.origin, project_id))
        require(grant is not None, 'ACCESS_DENIED')
        route = self.core.route(project_id)
        require(grant.principal == route['cloud_dot_principal'], 'ACCESS_DENIED')
        require(payload.get('sender') == grant.sender_alias, 'SENDER_BINDING_MISMATCH')
        require(payload.get('recipient') == self.recipient and payload.get('report_route') == route['report_route']
                and payload.get('target') == {'service': 'project-coordinator', 'project_id': project_id},
                'WRONG_RECIPIENT')
        require(type(payload.get('schema_version')) is int and payload['schema_version'] == 1, 'INVALID_JOB_SCHEMA')
        self._verify_task(delivery, payload, route)
        if admitted_request is not None:
            self.core.verify_bridge_origin(project_id, admitted_request, self._origin(delivery))

    def deliver(self, job_id, *, expected_project_id=None, expected_payload_hash=None, expected_request_id=None):
        """The original bridge can acknowledge delivery after core.submit commits."""
        delivery, payload = self._original(job_id)
        target = payload.get('target')
        require(isinstance(target, dict) and set(target) == {'service', 'project_id'}
                and target['service'] == 'project-coordinator', 'WRONG_RECIPIENT')
        project_id = target['project_id']
        require(expected_project_id is None or expected_project_id == project_id, 'PILOT_BINDING_MISMATCH')
        require(expected_payload_hash is None or expected_payload_hash == delivery.payload_hash, 'PILOT_BINDING_MISMATCH')
        self._verify_route(delivery, payload, project_id)
        require(delivery.record.get('cancel_requested') is False, 'JOB_CANCELLED_OR_UNVERIFIED')
        body = payload.get('coordinator_request')
        is_goal = isinstance(body, dict) and body.get('kind') == 'DELEGATE_GOAL'
        grant = self._grant(delivery.origin, project_id, 'submit_goal' if is_goal else 'submit')
        require(isinstance(body, dict) and set(body) == REQUEST_FIELDS | ({'goal'} if is_goal else set())
                and type(body['schema_version']) is int and body['schema_version'] == 1
                and body['kind'] in ('QUERY_BLOCKER', 'DELEGATE_GOAL') and isinstance(body['evidence_refs'], list)
                and len(body['evidence_refs']) <= 62,
                'INVALID_COORDINATOR_REQUEST')
        require(expected_request_id is None or body['request_id'] == expected_request_id, 'PILOT_BINDING_MISMATCH')
        request = {k: v for k, v in body.items() if k != 'schema_version'}
        request.update(project_id=project_id, job_id=job_id, payload_hash=delivery.payload_hash,
                       task_ref=delivery.task.task_ref if is_goal else payload.get('origin_task_id'),
                       evidence_refs=[*body['evidence_refs'], delivery.origin_ref, delivery.task.evidence_ref])
        if not is_goal:
            request['question'] = payload.get('content')
        return self.core.submit(grant.principal, request, bridge_origin=self._origin(delivery))

    def _request_state(self, grant, project_id, request_id):
        state = self.core.status(grant.principal, project_id, request_id)
        delivery, payload = self._original(state['job_id'])
        require(delivery.origin == grant.peer, 'ACCESS_DENIED')
        require(delivery.payload_hash == state['payload_hash'], 'ORIGINAL_JOB_MISMATCH')
        self._verify_route(delivery, payload, project_id, admitted_request=request_id)
        return state

    def status(self, session, project_id, request_id):
        grant = self._caller(session, project_id, 'status')
        return self._request_state(grant, project_id, request_id)

    def health(self, session, project_id):
        grant = self._caller(session, project_id, 'health')
        return self.core.health(grant.principal, project_id)

    def cancel(self, session, project_id, request_id, evidence_ref):
        grant = self._caller(session, project_id, 'cancel')
        self._request_state(grant, project_id, request_id)
        return self.core.cancel(grant.principal, project_id, request_id, evidence_ref)

    def _wire(self, outcome, reader=None):
        delivery, payload = self._original(outcome['job_id'])
        require(reader is None or delivery.origin == reader, 'ACCESS_DENIED')
        association = payload.get('coordinator_request')
        require(delivery.payload_hash == outcome['payload_hash']
                and isinstance(association, dict) and association.get('request_id') == outcome['request_id'],
                'ORIGINAL_JOB_MISMATCH')
        require(canonical_hash(outcome['result']) == outcome['result_hash'], 'CORE_RESULT_MISMATCH')
        self._verify_route(delivery, payload, outcome['project_id'], admitted_request=outcome['request_id'])
        # Pre-attach exactly the tracking the bridge would add. This lets both
        # sides verify the final wire bytes without equating local/server versions.
        return {'schema_version': 1, 'type': 'PROJECT_COORDINATOR_RESULT',
                'project_id': outcome['project_id'], 'request_id': outcome['request_id'],
                'job_id': outcome['job_id'], 'payload_hash': outcome['payload_hash'],
                'core_version': outcome['version'], 'core_result_hash': outcome['result_hash'],
                'result': outcome['result'], 'task_tracking': payload['tracking']}

    def enqueue(self, project_id, request_id, version=1):
        """Recover a durable answer using the bridge producer identity."""
        outcome = self.core.delivery(project_id, request_id, version)
        wire = self._wire(outcome)
        job_id, event_id = outcome['job_id'], outcome['event_id']
        expected = {'job_id': job_id, 'event_id': event_id, 'status': 'REPORTED_COMPLETED',
                    'result_hash': canonical_hash(wire, max_bytes=MAILBOX_LIMIT)}
        try:
            record = self.bridge.lookup_report(job_id, event_id)
            if record is None:
                record = self.bridge.enqueue_result(project_id=project_id, job_id=job_id,
                    payload_hash=outcome['payload_hash'], event_id=event_id,
                    status=expected['status'], result=wire)
        except (TimeoutError, ConnectionError):
            return {**expected, 'state': 'UNKNOWN', 'version': None}
        require(isinstance(record, dict) and all(record.get(k) == v for k, v in expected.items())
                and record.get('state') in ('PENDING', 'SENT'), 'OUTBOX_RECEIPT_MISMATCH')
        receipt = record.get('receipt')
        if record['state'] == 'PENDING':
            require(receipt is None, 'OUTBOX_RECEIPT_MISMATCH')
            return {**expected, 'state': 'PENDING', 'version': None}
        require(isinstance(receipt, dict) and all(receipt.get(k) == v for k, v in expected.items())
                and type(receipt.get('version')) is int and receipt['version'] > 0,
                'OUTBOX_RECEIPT_MISMATCH')
        return {**expected, 'state': 'SENT', 'version': receipt['version']}

    def result(self, session, project_id, request_id, *, mailbox_version, core_version=1):
        grant = self._caller(session, project_id, 'result')
        require(type(mailbox_version) is int and mailbox_version > 0, 'INVALID_MAILBOX_VERSION')
        outcome = self.core.result(grant.principal, project_id, request_id, core_version)
        wire = self._wire(outcome, grant.peer)
        record = self.callers.read_result(session, outcome['job_id'], mailbox_version)
        require(isinstance(record, dict) and record.get('job_id') == outcome['job_id']
                and record.get('event_id') == outcome['event_id'] and type(record.get('version')) is int
                and record['version'] == mailbox_version and record.get('status') == 'REPORTED_COMPLETED'
                and canonical_hash(record.get('result'), max_bytes=MAILBOX_LIMIT) == record.get('result_hash')
                    == canonical_hash(wire, max_bytes=MAILBOX_LIMIT),
                'SERVER_RESULT_MISMATCH')
        return {'job_id': outcome['job_id'], 'event_id': outcome['event_id'], 'version': mailbox_version,
                'result_hash': record['result_hash'], 'result': wire}

    def ack(self, session, project_id, request_id, *, mailbox_version, receipt_handle, core_version=1):
        grant = self._caller(session, project_id, 'ack')
        require(type(mailbox_version) is int and mailbox_version > 0, 'INVALID_MAILBOX_VERSION')
        outcome = self.core.result(grant.principal, project_id, request_id, core_version)
        proof = self.callers.verified_read(session, receipt_handle)
        require(isinstance(proof, VerifiedRead) and proof.reader == grant.peer
                and (proof.job_id, proof.event_id) == (outcome['job_id'], outcome['event_id'])
                and type(proof.version) is int and proof.version == mailbox_version
                and proof.result_hash == canonical_hash(self._wire(outcome, grant.peer), max_bytes=MAILBOX_LIMIT)
                and isinstance(proof.evidence_ref, str) and proof.evidence_ref.strip(),
                'READ_RECEIPT_MISMATCH')
        return self.core.ack(grant.principal, project_id, request_id,
                             core_version, outcome['result_hash'], proof.evidence_ref)

    def contact(self, project_id, request_id, expected_generation):
        """Trusted local worker: consume one durable send intent, then call once."""
        require(self.core.inspect(project_id, request_id).get('kind', 'QUERY_BLOCKER') == 'QUERY_BLOCKER',
                'USE_GOAL_DISPATCH')
        require(self.owners is not None, 'OWNER_CHANNEL_UNBOUND')
        intent = self.core.claim_contact(project_id, request_id, expected_generation)
        ids = {key: intent[key] for key in CONTACT_KEYS}
        if not intent['send']:
            return {**ids, 'native_attempted': False, 'outcome': 'RECONCILE_ORIGINAL'}
        command = {**ids, 'kind': 'QUERY_BLOCKER', **{k: intent[k] for k in
                   ('job_id', 'payload_hash', 'task_ref', 'authorization_ref', 'budget', 'question', 'deadline')}}
        checked = False

        def verify_current():
            nonlocal checked
            checked = False
            permit = self.core.check_contact(project_id, request_id, intent['action_id'], expected_generation)
            checked = True
            return permit

        try:
            receipt = self.owners.submit_contact(command, verify_current=verify_current)
            require(isinstance(receipt, dict) and type(receipt.get('generation')) is int
                    and all(receipt.get(k) == v for k, v in ids.items())
                    and isinstance(receipt.get('outcome'), str) and receipt['outcome'] in CONTACT_REASONS
                    and isinstance(receipt.get('reason'), str) and receipt['reason'] in CONTACT_REASONS[receipt['outcome']]
                    and (receipt['outcome'] != 'SUBMITTED' or checked)
                    and isinstance(receipt.get('evidence_ref'), str) and receipt['evidence_ref'].strip()
                    and len(receipt['evidence_ref']) <= 2048 and '\x00' not in receipt['evidence_ref'],
                    'NATIVE_RECEIPT_MISMATCH')
            outcome, reason, evidence = receipt['outcome'], receipt['reason'], receipt['evidence_ref']
        except Exception as error:
            # The external call may have taken effect. Preserve the intent and
            # a safe local diagnostic reference; a later call must not re-send.
            outcome = 'UNKNOWN'
            reason = 'RECEIPT_MISMATCH' if isinstance(error, TransportError) else 'ACK_UNCONFIRMED'
            evidence = f'local:{reason}:{intent["action_id"]}'
        self.core.record_contact(project_id, request_id, intent['action_id'], outcome, evidence, reason=reason)
        return {**ids, 'native_attempted': True, 'outcome': outcome, 'reason': reason, 'evidence_ref': evidence}

    def dispatch_goal(self, project_id, request_id, expected_generation):
        """One explicit native delegation; redelivery and UNKNOWN only reconcile.

        No automatic owner creation, credential provisioning, worker start or
        production permission is inferred from accepting a goal into the ledger.
        """
        require(self.owners is not None and callable(getattr(self.owners, 'submit_goal', None)),
                'GOAL_CHANNEL_UNBOUND')
        state = self.core.inspect(project_id, request_id)
        require(state.get('kind') == 'DELEGATE_GOAL', 'NOT_A_GOAL')

        def source_gate():
            original, payload = self._original(state['job_id'])
            require(original.payload_hash == state['payload_hash'], 'ORIGINAL_JOB_MISMATCH')
            self._verify_route(original, payload, project_id, admitted_request=request_id)
            self._grant(original.origin, project_id, 'submit_goal')
            require(original.record.get('cancel_requested') is False, 'JOB_CANCELLED_OR_UNVERIFIED')

        source_gate()
        intent = self.core.claim_contact(project_id, request_id, expected_generation)
        ids = {key: intent[key] for key in CONTACT_KEYS}
        if not intent['send']:
            return {**ids, 'native_attempted': False, 'outcome': 'RECONCILE_ORIGINAL'}
        command = {**ids, 'kind': 'DELEGATE_GOAL', **{k: intent[k] for k in
                   ('job_id', 'payload_hash', 'task_ref', 'authorization_ref', 'budget', 'goal', 'deadline')}}
        checked = False

        def verify_current():
            nonlocal checked
            checked = False
            source_gate()
            permit = self.core.check_contact(project_id, request_id, intent['action_id'], expected_generation)
            checked = True
            return permit

        try:
            receipt = self.owners.submit_goal(command, verify_current=verify_current)
            require(isinstance(receipt, dict) and type(receipt.get('generation')) is int
                    and all(receipt.get(k) == v for k, v in ids.items())
                    and receipt.get('kind') == 'DELEGATE_GOAL'
                    and isinstance(receipt.get('outcome'), str) and receipt['outcome'] in CONTACT_REASONS
                    and isinstance(receipt.get('reason'), str) and receipt['reason'] in CONTACT_REASONS[receipt['outcome']]
                    and (receipt['outcome'] != 'SUBMITTED' or checked)
                    and isinstance(receipt.get('evidence_ref'), str) and receipt['evidence_ref'].strip()
                    and len(receipt['evidence_ref']) <= 2048 and '\x00' not in receipt['evidence_ref'],
                    'NATIVE_RECEIPT_MISMATCH')
            outcome, reason, evidence = receipt['outcome'], receipt['reason'], receipt['evidence_ref']
        except Exception as error:
            outcome = 'UNKNOWN'
            reason = 'RECEIPT_MISMATCH' if isinstance(error, TransportError) else 'ACK_UNCONFIRMED'
            evidence = f'local:{reason}:{intent["action_id"]}'
        self.core.record_contact(project_id, request_id, intent['action_id'], outcome, evidence, reason=reason)
        return {**ids, 'native_attempted': True, 'outcome': outcome, 'reason': reason, 'evidence_ref': evidence}

    def _owner_observation(self, session, project_id, request_id, handle, action):
        grant = self._caller(session, project_id, action)
        require(self.owners is not None, 'OWNER_CHANNEL_UNBOUND')
        observed = self.owners.verified_observation(session, handle)
        require(isinstance(observed, OwnerObservation) and observed.peer == grant.peer
                and (observed.project_id, observed.request_id, observed.kind) == (project_id, request_id, action)
                and isinstance(observed.evidence_ref, str) and observed.evidence_ref.strip(),
                'OWNER_RECEIPT_MISMATCH')
        return (grant.principal, project_id, request_id, observed.action_id,
                observed.generation, observed.endpoint, observed.evidence_ref), observed

    def owner_read(self, session, project_id, request_id, handle):
        args, observation = self._owner_observation(session, project_id, request_id, handle, 'owner_read')
        require(observation.report is None and observation.report_hash is None, 'OWNER_RECEIPT_MISMATCH')
        return self.core.owner_read(*args)

    def owner_reply(self, session, project_id, request_id, handle):
        args, observation = self._owner_observation(session, project_id, request_id, handle, 'owner_reply')
        require(isinstance(observation.report, dict) and canonical_hash(observation.report) == observation.report_hash,
                'OWNER_REPLY_HASH_MISMATCH')
        return self.core.reply(*args, observation.report)

    def goal_update(self, session, project_id, request_id, handle):
        args, observation = self._owner_observation(session, project_id, request_id, handle, 'goal_update')
        require(isinstance(observation.report, dict) and canonical_hash(observation.report) == observation.report_hash,
                'OWNER_REPLY_HASH_MISMATCH')
        state = self.core.inspect(project_id, request_id)
        require(state.get('kind') == 'DELEGATE_GOAL', 'NOT_A_GOAL')
        # Reading a native report does not prove the original task is still live.
        # On an unavailable/revoked source leave the persisted native observation
        # untouched; it can be reconciled by the same handle and report sequence.
        original, payload = self._original(state['job_id'])
        require(original.payload_hash == state['payload_hash'], 'ORIGINAL_JOB_MISMATCH')
        self._verify_route(original, payload, project_id, admitted_request=request_id)
        grant = self._grant(original.origin, project_id, 'submit_goal')
        cancelled = original.record.get('cancel_requested')
        require(type(cancelled) is bool, 'JOB_CANCELLED_OR_UNVERIFIED')
        if cancelled:
            self.core.cancel(grant.principal, project_id, request_id,
                             f'bridge:cancel:{original.job_id}:{original.payload_hash}')
        return self.core.goal_update(*args, observation.report)
