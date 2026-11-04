"""Explicit native Codex goal channel; disabled until a host binds one approved job.

This module never creates a thread, changes a profile, installs a tool, approves a
request, or provisions credentials. A trusted host must first verify that the
named EXISTING profile enforces the listed organizational actions and asset scope.
That verification is a separate admission artifact, not a claim made by a goal.
The native profile and its platform approval policy remain authoritative.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
import uuid

if __package__:
    from .coordinator_transport import CONTACT_KEYS, OwnerObservation, Peer, TransportError, require
    from .project_coordinator import canonical, canonical_hash, GOAL_ACTIONS, GOAL_BUDGET
else:
    from coordinator_transport import CONTACT_KEYS, OwnerObservation, Peer, TransportError, require
    from project_coordinator import canonical, canonical_hash, GOAL_ACTIONS, GOAL_BUDGET


MAX_NATIVE = 8 * 1024 * 1024
POLICY_KEYS = {'v', 'binary', 'binary_sha256', 'cli_version', 'profile', 'cwd', 'state_dir',
               'project_id', 'request_id', 'job_id', 'payload_hash', 'owner_principal', 'endpoint',
               'generation', 'authorization_ref', 'work_key', 'scope_refs', 'allowed_actions',
               'capability_evidence_ref', 'capability_manifest', 'capability_manifest_sha256', 'expires_at'}
BINDING_KEYS = ('project_id', 'request_id', 'job_id', 'payload_hash', 'owner_principal',
                'endpoint', 'generation', 'authorization_ref')
INTENT_KEYS = (*CONTACT_KEYS, 'kind', 'job_id', 'payload_hash', 'task_ref', 'authorization_ref',
               'budget', 'goal', 'deadline')
DISABLED_FEATURES = frozenset({'apps', 'plugins', 'hooks', 'shell_tool', 'multi_agent', 'multi_agent_v2',
                              'code_mode', 'code_mode_host', 'unified_exec', 'view_image',
                              'image_generation', 'browser_use', 'browser_use_external',
                              'browser_use_full_cdp_access', 'in_app_browser', 'computer_use',
                              'skill_mcp_dependency_install', 'tool_suggest'})
CAPABILITY_KEYS = {'v', 'profile', 'binary_sha256', 'project_id', 'request_id', 'job_id', 'payload_hash',
                   'owner_principal', 'generation', 'work_key', 'scope_refs', 'allowed_actions',
                   'review_evidence_ref', 'expires_at', 'config_files', 'mcp_tools', 'action_tools',
                   'disabled_features', 'enforces_scope', 'production_writes', 'audio_generations',
                   'effective_config_sha256', 'config_layers_sha256'}


def _private(path, directory=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'NATIVE_PRIVATE_PATH_REQUIRED')
    info = path.stat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            'NATIVE_PRIVATE_PATH_REQUIRED')
    return path


def _json(raw):
    def unique(pairs):
        out = {}
        for k, v in pairs:
            require(k not in out, 'NATIVE_DUPLICATE_KEY')
            out[k] = v
        return out
    require(len(raw) <= MAX_NATIVE, 'NATIVE_RESPONSE_TOO_LARGE')
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise TransportError('NATIVE_INVALID_JSON') from error


def _new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def load_goal_channel(path):
    """Load an already approved private single-job binding; no defaults grant execution."""
    path = _private(path)
    require(path.stat().st_size <= 65536, 'NATIVE_POLICY_TOO_LARGE')
    return CodexGoalChannel(_json(path.read_bytes()))


class CodexGoalChannel:
    def __init__(self, policy):
        require(isinstance(policy, dict) and set(policy) == POLICY_KEYS and
                type(policy['v']) is int and policy['v'] == 1, 'INVALID_NATIVE_GOAL_POLICY')
        require(all(isinstance(policy[k], str) and policy[k].strip() for k in
                    POLICY_KEYS - {'v', 'generation', 'scope_refs', 'allowed_actions'}),
                'INVALID_NATIVE_GOAL_POLICY')
        require(type(policy['generation']) is int and policy['generation'] > 0 and
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', policy['profile']),
                'INVALID_NATIVE_GOAL_POLICY')
        require(isinstance(policy['scope_refs'], list) and policy['scope_refs'] and
                all(isinstance(x, str) and x for x in policy['scope_refs']) and
                len(set(policy['scope_refs'])) == len(policy['scope_refs']) and
                isinstance(policy['allowed_actions'], list) and policy['allowed_actions'] and
                all(isinstance(x, str) for x in policy['allowed_actions']) and
                set(policy['allowed_actions']) <= GOAL_ACTIONS,
                'INVALID_NATIVE_GOAL_SCOPE')
        require(policy['endpoint'].startswith('codex-cli:mac:'), 'UNSUPPORTED_NATIVE_GOAL_ENDPOINT')
        self.thread_id = policy['endpoint'][len('codex-cli:mac:'):]
        require(str(uuid.UUID(self.thread_id)) == self.thread_id, 'INVALID_NATIVE_THREAD')
        self.policy = dict(policy)
        self.cwd = _private(policy['cwd'], True)
        self.state = _private(policy['state_dir'], True)
        self.binary = Path(policy['binary'])
        self._observations = {}
        self.peer = Peer('mac-codex-cli', self.thread_id)
        self.principal = policy['owner_principal']
        self.project_id = policy['project_id']

    @staticmethod
    def _intent(value):
        require(isinstance(value, dict) and all(k in value for k in INTENT_KEYS), 'NATIVE_GOAL_BINDING_MISMATCH')
        return {k: value[k] for k in INTENT_KEYS}

    def _binding(self, intent):
        require(isinstance(intent, dict) and intent.get('kind') == 'DELEGATE_GOAL' and
                all(intent.get(k) == self.policy[k] for k in BINDING_KEYS), 'NATIVE_GOAL_BINDING_MISMATCH')
        require(intent.get('budget') == GOAL_BUDGET and
                all(type(v) is int for v in intent['budget'].values()), 'NATIVE_GOAL_BUDGET_MISMATCH')
        goal = intent.get('goal')
        require(isinstance(goal, dict) and goal.get('work_key') == self.policy['work_key'] and
                set(goal.get('scope_refs', [])) == set(self.policy['scope_refs']) and
                set(goal.get('allowed_actions', [])) <= set(self.policy['allowed_actions']),
                'NATIVE_GOAL_SCOPE_MISMATCH')
        return goal

    def _args(self):
        # The existing profile is deliberately not overridden: no approvals=never,
        # no --dangerously-bypass, no model/provider/credential or tool configuration.
        return [str(self.binary), '--profile', self.policy['profile'], '-C', str(self.cwd)]

    def _probe(self, argv, timeout):
        try:
            result = subprocess.run(argv, capture_output=True, timeout=timeout, cwd=self.cwd, check=False)
        except subprocess.TimeoutExpired as error:
            raise TransportError('NATIVE_CAPABILITY_PROBE_TIMEOUT') from error
        except OSError as error:
            raise TransportError('NATIVE_CAPABILITY_PROBE_UNAVAILABLE') from error
        require(len(result.stdout) <= MAX_NATIVE, 'NATIVE_RESPONSE_TOO_LARGE')
        return result

    def preflight(self, binding=None):
        if binding is not None:
            self._binding(binding)
        require(self.binary.is_absolute() and self.binary.resolve() == self.binary and self.binary.is_file(),
                'NATIVE_BINARY_CHANGED')
        require(hashlib.sha256(self.binary.read_bytes()).hexdigest() == self.policy['binary_sha256'],
                'NATIVE_BINARY_CHANGED')
        try:
            expiry = datetime.fromisoformat(self.policy['expires_at'].replace('Z', '+00:00'))
        except ValueError as error:
            raise TransportError('INVALID_NATIVE_EXPIRY') from error
        require(expiry.tzinfo is not None and expiry > datetime.now(timezone.utc), 'NATIVE_ADMISSION_EXPIRED')
        result = self._probe([str(self.binary), '--version'], 10)
        require(result.returncode == 0 and result.stdout.decode().strip() == self.policy['cli_version'],
                'NATIVE_VERSION_CHANGED')
        self._capabilities()
        return {'state': 'HOST_ADMISSION_BOUND', 'capability_verification': 'HOST_ATTESTED',
                'capability_evidence_ref': self.policy['capability_evidence_ref'],
                'profile': self.policy['profile'], 'new_permissions': False}

    def _capabilities(self):
        """Verify the host-reviewed closure and the actual enabled tool projection.

        No credential or full native configuration is printed or returned. The
        approved tool implementations must enforce the exact scope; names alone
        are not proof that a tool implements its claimed behavior.
        """
        path = _private(self.policy['capability_manifest'])
        raw = path.read_bytes()
        require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == self.policy['capability_manifest_sha256'],
                'NATIVE_CAPABILITY_MANIFEST_CHANGED')
        cap = _json(raw)
        require(isinstance(cap, dict) and set(cap) == CAPABILITY_KEYS and type(cap['v']) is int and cap['v'] == 1,
                'INVALID_NATIVE_CAPABILITY_MANIFEST')
        for key in ('profile', 'binary_sha256', 'project_id', 'request_id', 'job_id', 'payload_hash',
                    'owner_principal', 'generation', 'work_key'):
            require(cap[key] == self.policy[key], 'NATIVE_CAPABILITY_BINDING_MISMATCH')
        require(cap['review_evidence_ref'] == self.policy['capability_evidence_ref'] and
                cap['scope_refs'] == self.policy['scope_refs'] and cap['allowed_actions'] == self.policy['allowed_actions'] and
                cap['enforces_scope'] is True and type(cap['production_writes']) is int and cap['production_writes'] == 0 and
                type(cap['audio_generations']) is int and cap['audio_generations'] == 0,
                'NATIVE_CAPABILITY_BINDING_MISMATCH')
        expiry = datetime.fromisoformat(cap['expires_at'].replace('Z', '+00:00'))
        require(expiry.tzinfo is not None and expiry > datetime.now(timezone.utc), 'NATIVE_CAPABILITY_EXPIRED')
        require(isinstance(cap['config_files'], list) and cap['config_files'], 'NATIVE_CONFIG_CLOSURE_REQUIRED')
        seen = set()
        for record in cap['config_files']:
            require(isinstance(record, dict) and set(record) == {'path', 'sha256'}, 'NATIVE_CONFIG_CLOSURE_REQUIRED')
            source = Path(record['path'])
            require(source.is_absolute() and source.resolve() == source and source.is_file() and
                    str(source) not in seen, 'NATIVE_CONFIG_CLOSURE_REQUIRED')
            seen.add(str(source))
            require(hashlib.sha256(source.read_bytes()).hexdigest() == record['sha256'], 'NATIVE_PROFILE_CHANGED')
        expected = cap['mcp_tools']
        require(isinstance(expected, dict) and expected and all(isinstance(k, str) and
                re.fullmatch('[A-Za-z0-9_-]+', k) and isinstance(v, list) and v and
                len(v) == len(set(v)) and all(isinstance(t, str) and t for t in v)
                for k, v in expected.items()), 'NATIVE_GOAL_TOOLS_REQUIRED')
        action_tools = cap['action_tools']
        tools = {server + '/' + name for server, names in expected.items() for name in names}
        require(isinstance(action_tools, dict) and set(action_tools) == set(cap['allowed_actions']) and
                all(isinstance(v, list) and v and all(x in tools for x in v) for v in action_tools.values()) and
                {x for v in action_tools.values() for x in v} == tools, 'NATIVE_GOAL_TOOLS_REQUIRED')
        require(isinstance(cap['disabled_features'], list) and set(cap['disabled_features']) >= DISABLED_FEATURES,
                'NATIVE_TOOL_BOUNDARY_REQUIRED')
        with self._reader() as call:
            effective = call('config/read', {'cwd': str(self.cwd), 'includeLayers': True})
        require(isinstance(effective.get('config'), dict) and isinstance(effective.get('layers'), list),
                'NATIVE_EFFECTIVE_CONFIG_UNVERIFIED')
        # Never return or log the full configuration. Its digest plus native layer
        # metadata also catches a newly inserted inherited/project/managed layer.
        require(canonical_hash(effective['config']) == cap['effective_config_sha256'],
                'NATIVE_EFFECTIVE_CONFIG_CHANGED')
        layers = []
        for layer in effective['layers']:
            require(isinstance(layer, dict) and isinstance(layer.get('name'), dict) and
                    isinstance(layer.get('version'), str), 'NATIVE_CONFIG_CLOSURE_REQUIRED')
            layers.append({k: layer[k] for k in ('name', 'version')})
        require(canonical_hash(layers) == cap['config_layers_sha256'], 'NATIVE_CONFIG_LAYERS_CHANGED')
        require(effective['config'].get('sandbox_mode') == 'read-only' and
                effective['config'].get('web_search') == 'disabled', 'NATIVE_TOOL_BOUNDARY_REQUIRED')
        listed = self._probe([*self._args(), 'mcp', 'list', '--json'], 15)
        require(listed.returncode == 0, 'NATIVE_CAPABILITY_PROBE_FAILED')
        servers = _json(listed.stdout)
        require(isinstance(servers, list), 'NATIVE_CAPABILITY_PROBE_FAILED')
        actual = {}
        for server in servers:
            require(isinstance(server, dict) and isinstance(server.get('name'), str) and
                    type(server.get('enabled')) is bool, 'NATIVE_CAPABILITY_PROBE_FAILED')
            if server['enabled']:
                names = server.get('enabled_tools')
                require(isinstance(names, list) and names and all(isinstance(n, str) for n in names) and
                        len(names) == len(set(names)) and server['name'] not in actual,
                        'NATIVE_TOOL_ALLOWLIST_UNVERIFIED')
                actual[server['name']] = sorted(names)
        require(actual == {k: sorted(v) for k, v in expected.items()}, 'NATIVE_TOOLSET_CHANGED')
        result = self._probe([*self._args(), 'features', 'list'], 15)
        require(result.returncode == 0, 'NATIVE_CAPABILITY_PROBE_FAILED')
        flags = {line.split()[0]: line.split()[-1] for line in result.stdout.decode().splitlines() if line.split()}
        require(all(flags.get(f) == 'false' for f in cap['disabled_features']), 'NATIVE_TOOL_BOUNDARY_CHANGED')

    @contextmanager
    def _reader(self):
        try:
            app = subprocess.Popen([*self._args(), 'app-server', '--stdio'], cwd=self.cwd,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as error:
            raise TransportError('NATIVE_READER_UNAVAILABLE') from error
        buf = bytearray()
        counter = 0
        with selectors.DefaultSelector() as selector:
            selector.register(app.stdout, selectors.EVENT_READ)
            def call(method, params, notification=False):
                nonlocal counter, buf
                counter += 1
                request = {'method': method, 'params': params}
                if not notification:
                    request['id'] = counter
                app.stdin.write(canonical(request) + b'\n')
                app.stdin.flush()
                if notification:
                    return None
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if b'\n' not in buf:
                        require(bool(selector.select(max(.01, deadline - time.monotonic()))), 'NATIVE_READ_TIMEOUT')
                        part = os.read(app.stdout.fileno(), 65536)
                        require(bool(part), 'NATIVE_READER_EXITED')
                        buf.extend(part)
                        require(len(buf) <= MAX_NATIVE, 'NATIVE_RESPONSE_TOO_LARGE')
                        continue
                    line, _, tail = buf.partition(b'\n')
                    buf = bytearray(tail)
                    answer = _json(line)
                    # Native permission requests are never approved by this adapter.
                    require('method' not in answer or 'id' not in answer, 'NATIVE_APPROVAL_REQUIRED')
                    if answer.get('id') == counter:
                        require(isinstance(answer.get('result'), dict), 'NATIVE_READ_REFUSED')
                        return answer['result']
                raise TransportError('NATIVE_READ_TIMEOUT')
            try:
                call('initialize', {'clientInfo': {'name': 'coordinator_goal_reader', 'version': '1'}})
                call('initialized', {}, True)
                yield call
            finally:
                app.terminate()
                try:
                    app.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    app.kill()
                    app.wait()
                if app.stdin:
                    app.stdin.close()
                if app.stdout:
                    app.stdout.close()

    def _thread(self):
        with self._reader() as call:
            thread = call('thread/read', {'threadId': self.thread_id, 'includeTurns': True}).get('thread')
        require(isinstance(thread, dict) and thread.get('id') == self.thread_id and
                Path(thread.get('cwd', '')).resolve() == self.cwd, 'NATIVE_THREAD_MISMATCH')
        return thread

    @staticmethod
    def _receipt(intent):
        return {k: intent[k] for k in (*CONTACT_KEYS, 'job_id', 'payload_hash')} | {'kind': 'DELEGATE_GOAL'}

    @classmethod
    def _schema(cls, intent):
        text = {'type': 'string', 'minLength': 1}
        refs = {'type': 'array', 'minItems': 1, 'items': text}
        common = {'seq': {'type': 'integer', 'minimum': 1}, 'summary': text, 'observed_at': text,
                  'evidence_refs': refs, 'next_owner': text}
        continuing = {**common, 'phase': {'enum': ['ACCEPTED', 'IN_PROGRESS', 'BLOCKED']}, 'next_check_at': text}
        completed = {**common, 'phase': {'const': 'COMPLETED'}, 'terminal_condition': text,
                     'criteria_evidence': {'type': 'array', 'minItems': 1, 'items': {
                         'type': 'object', 'properties': {'criterion': {'enum': intent['goal']['acceptance_criteria']},
                                                       'evidence_refs': refs},
                         'required': ['criterion', 'evidence_refs'], 'additionalProperties': False}}}
        if intent['goal']['review_requirement'] == 'independent_listening':
            review = {'state': {'const': 'VERIFIED'}, 'reviewer': text, 'producer': text,
                      'scope_refs': refs, 'evidence_refs': refs,
                      'method': {'enum': ['human_listening', 'audio_perception']}}
            completed['independent_review'] = {'type': 'object', 'properties': review,
                                               'required': list(review), 'additionalProperties': False}
        alternatives = [{'type': 'object', 'properties': p, 'required': list(p), 'additionalProperties': False}
                        for p in (continuing, completed)]
        return {'type': 'object', 'properties': {'receipt': {'const': cls._receipt(intent)},
                'updates': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': {'oneOf': alternatives}}},
                'required': ['receipt', 'updates'], 'additionalProperties': False}

    @classmethod
    def _prompt(cls, intent):
        return ('受控完整目标交办。只在已批准的原有权限、预算和范围内推进；不得自批权限、创建负责人、'
                '扩大工具范围、生成音频或重复派发。必须明确接手；缺能力时报告 BLOCKED 及下一责任与检查时间。'
                '以下 JSON 为冻结任务数据。回复严格为 {receipt, updates}；receipt 原样回显本单身份，'
                'updates 逐条给真实接受、进展、阻塞或有证据的完成报告，绝不以技术送达冒称接受。\n'
                'seq 从1开始连续递增。ACCEPTED才表示明确接手；没有接手不得报IN_PROGRESS或COMPLETED。'
                '每条observed_at用真实有时区时间；非完成next_check_at必须在现在之后且不超过deadline。'
                '完成必须逐项覆盖全部acceptance_criteria。需要听检时真实听辨和独立性证据不可由ASR或技术检查代替。'
                '后续原生回合沿同receipt和下一seq回报，不重新派发本任务。报告JSON schema如下。\n'
                'REPORT_SCHEMA\n' + canonical(cls._schema(intent)).decode() + '\nFROZEN_INTENT\n'
                + canonical(intent).decode())

    def submit_goal(self, intent, *, verify_current):
        intent = self._intent(intent)
        self.preflight(intent)
        action = intent.get('action_id')
        require(isinstance(action, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', action),
                'INVALID_NATIVE_ACTION')
        ids = {k: intent[k] for k in CONTACT_KEYS}
        slot = self.state / action
        if slot.exists():
            require((slot / 'intent.json').read_bytes() == canonical(intent), 'NATIVE_ACTION_CONFLICT')
            return {**ids, 'kind': 'DELEGATE_GOAL', 'outcome': 'UNKNOWN', 'reason': 'ACK_UNCONFIRMED',
                    'evidence_ref': 'native-goal-reconcile:' + action}
        thread = self._thread()
        require(not any(t.get('status') in ('inProgress', 'in_progress', 'running')
                        for t in thread.get('turns', [])), 'NATIVE_OWNER_BUSY')
        verify_current()
        slot.mkdir(mode=0o700)  # Durable single-use reservation, never cleared on failure.
        _new(slot / 'intent.json', canonical(intent))
        prompt = self._prompt(intent)
        _new(slot / 'prompt.txt', prompt.encode())
        _new(slot / 'report-schema.json', canonical(self._schema(intent)))
        # Recheck the reviewed profile/config closure at the final native boundary.
        self.preflight(intent)
        verify_current()
        argv = [*self._args(), 'exec', 'resume', self.thread_id, '--skip-git-repo-check', '--json',
                '--output-schema', str(slot / 'report-schema.json'), '-']
        with (slot / 'events.jsonl').open('xb') as output, (slot / 'stderr.log').open('xb') as err:
            # CLI status/output are diagnostic only. Independent native history below
            # is the only evidence used to attest an actual native submission.
            try:
                completed = subprocess.run(argv, input=prompt.encode(), cwd=self.cwd,
                                           stdout=output, stderr=err, timeout=300, check=False)
            except (OSError, subprocess.TimeoutExpired):
                raise TransportError('NATIVE_SUBMISSION_UNCONFIRMED')
        if completed.returncode:
            raise TransportError('NATIVE_SUBMISSION_UNCONFIRMED')
        turn = self._matching_turn(intent)
        proof = {'thread': self.thread_id, 'turn': turn['id'], 'action': action,
                 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
        _new(slot / 'submitted-proof.json', canonical(proof))
        return {**ids, 'kind': 'DELEGATE_GOAL', 'outcome': 'SUBMITTED', 'reason': 'NATIVE_SUBMISSION',
                'evidence_ref': 'native-goal-turn-sha256:' + canonical_hash(proof)}

    def _matching_history(self, intent):
        intent = self._intent(intent)
        self._binding(intent)
        slot = _private(self.state / intent['action_id'], True)
        require((slot / 'intent.json').read_bytes() == canonical(intent), 'NATIVE_ACTION_CONFLICT')
        prompt = (slot / 'prompt.txt').read_text()
        require(prompt == self._prompt(intent), 'NATIVE_PROMPT_CHANGED')
        matches = []
        history = self._thread().get('turns', [])
        for turn in history:
            users = [i for i in turn.get('items', []) if i.get('type') == 'userMessage' and
                     [v.get('text') for v in i.get('content', []) if v.get('type') == 'text'] == [prompt]]
            if users:
                require(len(users) == 1 and turn.get('itemsView', 'full') == 'full', 'NATIVE_TURN_AMBIGUOUS')
                matches.append(turn)
        require(len(matches) == 1, 'NATIVE_TURN_NOT_FOUND_OR_AMBIGUOUS')
        return history, matches[0]

    def _matching_turn(self, intent):
        return self._matching_history(intent)[1]

    def reconcile(self, intent):
        """Only read the original native turn. Never resume it or make a new turn."""
        intent = self._intent(intent)
        history, first = self._matching_history(intent)
        expected = self._receipt(intent)
        handles = []
        # Native history is chronological. Later reports from this same bound
        # owner may advance the original goal, but must carry its exact receipt.
        for turn in history[history.index(first):]:
            if turn.get('status') != 'completed' or turn.get('itemsView', 'full') != 'full':
                continue
            for item in turn.get('items', []):
                if item.get('type') != 'agentMessage' or item.get('phase') == 'commentary':
                    continue
                raw = item.get('text')
                if not isinstance(raw, str):
                    continue
                try:
                    value = _json(raw.encode())
                except TransportError:
                    continue  # Ordinary unrelated prose is never a business receipt.
                if not isinstance(value, dict) or value.get('receipt') != expected:
                    continue
                require(set(value) == {'receipt', 'updates'} and isinstance(value['updates'], list) and
                        1 <= len(value['updates']) <= 64, 'NATIVE_GOAL_REPORT_SCHEMA')
                proof = {'thread_id': self.thread_id, 'turn_id': turn['id'], 'item_id': item['id'],
                         'reply_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                         'prompt_sha256': hashlib.sha256(self._prompt(intent).encode()).hexdigest()}
                locator = canonical_hash([self.thread_id, turn['id'], item['id']])
                saved = self.state / intent['action_id'] / ('native-item-' + locator + '.json')
                try:
                    _new(saved, canonical(proof))
                except FileExistsError:
                    require(_private(saved).read_bytes() == canonical(proof), 'NATIVE_REPORT_CHANGED')
                for report in value['updates']:
                    require(isinstance(report, dict), 'NATIVE_GOAL_REPORT_SCHEMA')
                    session, handle = object(), object()
                    observation = OwnerObservation(self.peer, intent['endpoint'],
                        intent['project_id'], intent['request_id'], intent['action_id'], intent['generation'],
                        'goal_update', 'native-goal-report-sha256:' + canonical_hash(proof), report, canonical_hash(report))
                    self._observations[session] = (handle, observation)
                    handles.append((session, handle))
        require(bool(handles), 'NATIVE_REPORT_NOT_READY')
        return handles

    def authenticated_peer(self, session):
        saved = self._observations.get(session)
        return saved[1].peer if saved else None

    def verified_observation(self, native_session, handle):
        saved = self._observations.get(native_session)
        require(saved is not None and saved[0] is handle, 'NATIVE_OBSERVATION_UNVERIFIED')
        return saved[1]

    def read_result(self, *_):
        raise TransportError('CLOUD_DOT_READER_UNBOUND')

    def verified_read(self, *_):
        raise TransportError('CLOUD_DOT_READER_UNBOUND')
