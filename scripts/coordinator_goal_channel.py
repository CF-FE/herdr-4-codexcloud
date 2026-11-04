"""Explicit native Codex goal channel; disabled until a host binds one approved job.

This module never creates a thread, changes a profile, installs a tool, approves a
request, or provisions credentials. A trusted host must first verify that the
existing profile (v1) or process-only configuration (v2) enforces the listed
organizational actions and asset scope. V2 keeps the owner's original home and
thread; it never copies native history or authentication into a different home.
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
POLICY_KEYS = {'v', 'binary', 'binary_sha256', 'cli_version', 'profile', 'codex_home', 'cwd', 'state_dir',
               'project_id', 'request_id', 'job_id', 'payload_hash', 'owner_principal', 'endpoint',
               'generation', 'authorization_ref', 'work_key', 'scope_refs', 'allowed_actions',
               'capability_evidence_ref', 'capability_manifest', 'capability_manifest_sha256', 'expires_at'}
POLICY_V2_KEYS = (POLICY_KEYS - {'profile'}) | {'process_config', 'process_config_sha256'}
BINDING_KEYS = ('project_id', 'request_id', 'job_id', 'payload_hash', 'owner_principal',
                'endpoint', 'generation', 'authorization_ref')
INTENT_KEYS = (*CONTACT_KEYS, 'kind', 'job_id', 'payload_hash', 'task_ref', 'authorization_ref',
               'budget', 'goal', 'deadline')
DISABLED_FEATURES = frozenset({'apps', 'plugins', 'hooks', 'shell_tool', 'multi_agent', 'multi_agent_v2',
                              'code_mode', 'code_mode_host', 'goals', 'view_image',
                              'image_generation', 'browser_use', 'browser_use_external',
                              'browser_use_full_cdp_access', 'in_app_browser', 'computer_use',
                              'skill_mcp_dependency_install', 'tool_suggest'})
CAPABILITY_KEYS = {'v', 'profile', 'binary_sha256', 'project_id', 'request_id', 'job_id', 'payload_hash',
                   'owner_principal', 'generation', 'work_key', 'scope_refs', 'allowed_actions',
                   'review_evidence_ref', 'expires_at', 'config_files', 'mcp_tools', 'action_tools',
                   'disabled_features', 'enforces_scope', 'production_writes', 'audio_generations',
                   'effective_config_sha256', 'config_layers_sha256'}
CAPABILITY_V2_KEYS = (CAPABILITY_KEYS - {'profile'}) | {'codex_home', 'endpoint', 'process_config_sha256',
                    'owner_exclusivity', 'owner_exclusivity_sha256', 'environment_sha256'}
PROCESS_KEYS = {'v', 'codex_home', 'endpoint', 'mcp_servers', 'disabled_mcp_servers', 'disabled_features'}
PROCESS_SERVER_KEYS = {'python', 'script', 'policy', 'enabled_tools', 'artifacts'}
ORGANIZATION_TOOLS = frozenset({'goal_context', 'read_review_evidence', 'coordinate_independent_review',
                                'read_coordination_report'})
ACTION_TOOL_ALLOWLIST = {
    'coordinate_existing_owner': frozenset({'goal_context'}),
    'verify_existing_evidence': frozenset({'goal_context', 'read_review_evidence'}),
    'plan_independent_review': ORGANIZATION_TOOLS,
    'report_progress': frozenset({'goal_context', 'read_coordination_report'}),
}
TOOL_SOURCE_FILES = ('mcp/project-coordinator-tools/server.py', 'scripts/coordinator_goal_channel.py',
                     'scripts/coordinator_bridge.py', 'scripts/coordinator_transport.py', 'scripts/project_coordinator.py')
EXCLUSIVITY_KEYS = {'v', 'project_id', 'request_id', 'job_id', 'payload_hash', 'owner_principal', 'endpoint',
                    'generation', 'codex_home', 'state', 'reviewer', 'source_control_ref', 'handoff_ref',
                    'observed_at', 'expires_at', 'last_turn_id'}
OWNER_EXCLUSIVITY_MAX_SECONDS = 900


def _toml(value):
    """Encode only the closed, validated override types; never accept raw argv/TOML."""
    if type(value) is bool:
        return 'true' if value else 'false'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, list):
        return '[' + ','.join(_toml(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(_toml(k) + '=' + _toml(value[k]) for k in sorted(value)) + '}'
    raise TransportError('INVALID_NATIVE_PROCESS_CONFIG')


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
    raw = path.read_bytes()
    channel = CodexGoalChannel(_json(raw))
    channel._policy_file = (path, hashlib.sha256(raw).digest())
    return channel


class CodexGoalChannel:
    def __init__(self, policy):
        require(isinstance(policy, dict) and type(policy.get('v')) is int and policy['v'] in (1, 2),
                'INVALID_NATIVE_GOAL_POLICY')
        keys = POLICY_KEYS if policy['v'] == 1 else POLICY_V2_KEYS
        require(set(policy) == keys, 'INVALID_NATIVE_GOAL_POLICY')
        require(all(isinstance(policy[k], str) and policy[k].strip() for k in
                    keys - {'v', 'generation', 'scope_refs', 'allowed_actions'}),
                'INVALID_NATIVE_GOAL_POLICY')
        require(type(policy['generation']) is int and policy['generation'] > 0 and
                (policy['v'] == 2 or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', policy['profile'])),
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
        self.home = _private(policy['codex_home'], True)
        # Every probe and native operation sees the same reviewed home, without
        # inherited provider, profile, feature or arbitrary PATH overrides.
        self.env = {'CODEX_HOME': str(self.home), 'HOME': str(Path.home()),
                    'PATH': os.defpath, 'LANG': 'en_US.UTF-8'}
        self._prepared = None
        self._policy_file = None
        self.binary = Path(policy['binary'])
        self._observations = {}
        self.peer = Peer('mac-codex-cli', self.thread_id)
        self.principal = policy['owner_principal']
        self.project_id = policy['project_id']
        self.release = Path(__file__).resolve().parents[1]

    def _process_config(self):
        """Read a pinned private description of the one supported stdio tool host."""
        raw = _private(self.policy['process_config']).read_bytes()
        require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == self.policy['process_config_sha256'],
                'NATIVE_PROCESS_CONFIG_CHANGED')
        value = _json(raw)
        require(isinstance(value, dict) and set(value) == PROCESS_KEYS and
                type(value['v']) is int and value['v'] == 1 and
                value['codex_home'] == str(self.home) and value['endpoint'] == self.policy['endpoint'],
                'INVALID_NATIVE_PROCESS_CONFIG')
        disabled = value['disabled_features']
        require(isinstance(disabled, list) and
                all(isinstance(n, str) and re.fullmatch('[a-z][a-z0-9_]*', n) for n in disabled) and
                len(disabled) == len(set(disabled)) and set(disabled) >= DISABLED_FEATURES,
                'NATIVE_TOOL_BOUNDARY_REQUIRED')
        servers = value['mcp_servers']
        require(isinstance(servers, dict) and len(servers) == 1, 'INVALID_NATIVE_PROCESS_CONFIG')
        inactive = value['disabled_mcp_servers']
        require(isinstance(inactive, list) and all(isinstance(n, str) and re.fullmatch('[A-Za-z0-9_-]+', n) for n in inactive)
                and len(inactive) == len(set(inactive)) and not set(inactive) & set(servers),
                'INVALID_NATIVE_PROCESS_CONFIG')
        for name, server in servers.items():
            require(isinstance(name, str) and re.fullmatch('[A-Za-z0-9_-]+', name) and
                    isinstance(server, dict) and set(server) == PROCESS_SERVER_KEYS,
                    'INVALID_NATIVE_PROCESS_CONFIG')
            for field in ('python', 'script', 'policy'):
                require(isinstance(server[field], str) and server[field], 'NATIVE_TOOL_ARTIFACT_REQUIRED')
                path = Path(server[field])
                require(path.is_absolute() and path.resolve() == path and path.is_file(),
                        'NATIVE_TOOL_ARTIFACT_REQUIRED')
            require(re.fullmatch(r'python[0-9.]*', Path(server['python']).name) and
                    Path(server['script']) == self.release / TOOL_SOURCE_FILES[0],
                    'NATIVE_TOOL_ARTIFACT_REQUIRED')
            _private(server['policy'])
            require(isinstance(server['enabled_tools'], list) and server['enabled_tools'] and
                    all(isinstance(t, str) for t in server['enabled_tools']) and
                    len(server['enabled_tools']) == len(set(server['enabled_tools'])) and
                    set(server['enabled_tools']) <= ORGANIZATION_TOOLS, 'NATIVE_GOAL_TOOLS_REQUIRED')
            require(isinstance(server['artifacts'], list) and server['artifacts'] and
                    all(isinstance(p, str) for p in server['artifacts']) and
                    len(server['artifacts']) == len(set(server['artifacts'])) and
                    ({server[k] for k in ('python', 'script', 'policy')} |
                     {str(self.release / p) for p in TOOL_SOURCE_FILES}) <= set(server['artifacts']),
                    'NATIVE_TOOL_ARTIFACT_REQUIRED')
        return value

    def _overrides(self):
        value = self._process_config()
        return {'sandbox_mode': 'read-only', 'approval_policy': 'on-request', 'web_search': 'disabled',
                'features': dict.fromkeys(value['disabled_features'], False),
                'mcp_servers': {**{name: {'enabled': False} for name in value['disabled_mcp_servers']},
                    **{name: {'command': server['python'],
                    'args': ['-I', '-S', '-B', server['script'], '--policy', server['policy']],
                    'enabled': True, 'enabled_tools': server['enabled_tools']}
                    for name, server in value['mcp_servers'].items()}}}

    def _override_args(self):
        return [part for key, value in sorted(self._overrides().items()) for part in ('--config', key + '=' + _toml(value))]

    def _check_owner_exclusivity(self, cap, thread=None, deadline=None, *, loaded=True):
        """Host attestation, not an OS-wide native lock or an agent's idle claim."""
        require(isinstance(cap.get('owner_exclusivity'), str) and
                isinstance(cap.get('owner_exclusivity_sha256'), str), 'HOST_OWNER_EXCLUSIVITY_REQUIRED')
        path = _private(cap['owner_exclusivity'])
        require(path != self.cwd and self.cwd not in path.parents and
                path != self.state and self.state not in path.parents, 'HOST_OWNER_EXCLUSIVITY_REQUIRED')
        raw = path.read_bytes()
        require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == cap['owner_exclusivity_sha256'],
                'HOST_OWNER_EXCLUSIVITY_CHANGED')
        proof = _json(raw)
        require(isinstance(proof, dict) and set(proof) == EXCLUSIVITY_KEYS and
                type(proof['v']) is int and proof['v'] == 1 and proof['state'] == 'EXCLUSIVE_RELEASED',
                'HOST_OWNER_EXCLUSIVITY_REQUIRED')
        for key in ('project_id', 'request_id', 'job_id', 'payload_hash', 'owner_principal', 'endpoint',
                    'generation', 'codex_home'):
            require(proof[key] == self.policy[key], 'HOST_OWNER_EXCLUSIVITY_BINDING_MISMATCH')
        require(all(isinstance(proof[k], str) and proof[k].strip() and proof[k] == proof[k].strip() for k in
                    ('reviewer', 'source_control_ref', 'handoff_ref', 'observed_at', 'expires_at')) and
                proof['reviewer'] != self.principal and
                (proof['last_turn_id'] is None or isinstance(proof['last_turn_id'], str) and proof['last_turn_id']),
                'HOST_OWNER_EXCLUSIVITY_REQUIRED')
        try:
            observed = datetime.fromisoformat(proof['observed_at'].replace('Z', '+00:00'))
            expires = datetime.fromisoformat(proof['expires_at'].replace('Z', '+00:00'))
            limit = datetime.fromisoformat((deadline or self.policy['expires_at']).replace('Z', '+00:00'))
        except ValueError as error:
            raise TransportError('HOST_OWNER_EXCLUSIVITY_REQUIRED') from error
        require(all(t.tzinfo is not None for t in (observed, expires, limit)), 'HOST_OWNER_EXCLUSIVITY_REQUIRED')
        now = datetime.now(timezone.utc)
        require(observed <= now < expires <= limit and
                0 < (expires - observed).total_seconds() <= OWNER_EXCLUSIVITY_MAX_SECONDS,
                'HOST_OWNER_EXCLUSIVITY_EXPIRED')
        if thread is not None:
            turns = thread.get('turns')
            allowed = ('idle',) if loaded else ('idle', 'notLoaded')
            require(isinstance(turns, list) and thread.get('status', {}).get('type') in allowed and
                    (turns[-1].get('id') if turns else None) == proof['last_turn_id'] and
                    all(t.get('status') != 'inProgress' for t in turns), 'HOST_OWNER_EXCLUSIVITY_STALE')

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
                set(goal.get('allowed_actions', [])) == set(self.policy['allowed_actions']),
                'NATIVE_GOAL_SCOPE_MISMATCH')
        return goal

    def _args(self):
        # The existing profile is deliberately not overridden: no approvals=never,
        # no --dangerously-bypass, no model/provider/credential or tool configuration.
        return [str(self.binary), '--profile', self.policy['profile'], '-C', str(self.cwd)]

    def _mcp_args(self, *args):
        if self.policy['v'] == 2:
            return [str(self.binary), 'mcp', *self._override_args(), *args]
        return [*self._args(), 'mcp', *args]

    def _probe(self, argv, timeout):
        try:
            result = subprocess.run(argv, capture_output=True, timeout=timeout, cwd=self.cwd, env=self.env, check=False)
        except subprocess.TimeoutExpired as error:
            raise TransportError('NATIVE_CAPABILITY_PROBE_TIMEOUT') from error
        except OSError as error:
            raise TransportError('NATIVE_CAPABILITY_PROBE_UNAVAILABLE') from error
        require(len(result.stdout) <= MAX_NATIVE, 'NATIVE_RESPONSE_TOO_LARGE')
        return result

    def _binary_stamp(self):
        require(self.binary.is_absolute() and self.binary.resolve() == self.binary and self.binary.is_file(),
                'NATIVE_BINARY_CHANGED')
        st = self.binary.stat()
        return st.st_dev, st.st_ino, st.st_size, st.st_ctime_ns

    def _verify_binary(self):
        stamp = self._binary_stamp()
        digest = hashlib.sha256()
        with self.binary.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        require(digest.hexdigest() == self.policy['binary_sha256'] and self._binary_stamp() == stamp,
                'NATIVE_BINARY_CHANGED')
        return stamp

    @staticmethod
    def _expiry(value, code):
        expiry = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(expiry.tzinfo is not None and expiry > datetime.now(timezone.utc), code)

    @contextmanager
    def _admission(self, binding):
        if binding is not None:
            self._binding(binding)
        stamp = self._verify_binary()
        self._expiry(self.policy['expires_at'], 'NATIVE_ADMISSION_EXPIRED')
        version = self._probe([str(self.binary), '--version'], 10)
        require(version.returncode == 0 and version.stdout.decode().strip() == self.policy['cli_version'],
                'NATIVE_VERSION_CHANGED')
        cap = self._read_capabilities()
        if self.policy['v'] == 2:
            self._check_owner_exclusivity(cap, deadline=binding['deadline'] if binding is not None else None)
        with self._reader() as (call, wait_turn):
            self._verify_config(call, cap)
            yield call, wait_turn, cap, stamp

    def preflight(self, binding=None):
        """No thread or model is opened by this configuration-only diagnostic."""
        with self._admission(binding):
            return {'state': 'HOST_ADMISSION_BOUND', 'capability_verification': 'HOST_ATTESTED',
                    'capability_evidence_ref': self.policy['capability_evidence_ref'],
                    'profile': self.policy.get('profile'), 'policy_version': self.policy['v'], 'new_permissions': False}

    @contextmanager
    def prepare_goal(self, binding):
        """Hold the verified native reader across the caller's durable claim."""
        with self._admission(binding) as prepared:
            thread = self._thread(prepared[0])
            if self.policy['v'] == 2:
                self._check_owner_exclusivity(prepared[2], thread, binding['deadline'], loaded=False)
            require(thread.get('status', {}).get('type') not in ('active', 'systemError') and
                    not any(t.get('status') == 'inProgress' for t in thread.get('turns', [])), 'NATIVE_OWNER_BUSY')
            require(self._prepared is None, 'NATIVE_PREPARATION_IN_USE')
            token = object()
            self._prepared = (token, prepared, canonical_hash({k: binding[k] for k in INTENT_KEYS if k != 'action_id'}))
            try:
                yield token
            finally:
                self._prepared = None

    def _read_capabilities(self):
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
        keys = CAPABILITY_KEYS if self.policy['v'] == 1 else CAPABILITY_V2_KEYS
        require(isinstance(cap, dict) and set(cap) == keys and type(cap['v']) is int and cap['v'] == self.policy['v'],
                'INVALID_NATIVE_CAPABILITY_MANIFEST')
        bindings = ('profile',) if self.policy['v'] == 1 else ('codex_home', 'endpoint', 'process_config_sha256')
        for key in (*bindings, 'binary_sha256', 'project_id', 'request_id', 'job_id', 'payload_hash',
                    'owner_principal', 'generation', 'work_key'):
            require(cap[key] == self.policy[key], 'NATIVE_CAPABILITY_BINDING_MISMATCH')
        require(cap['review_evidence_ref'] == self.policy['capability_evidence_ref'] and
                cap['scope_refs'] == self.policy['scope_refs'] and cap['allowed_actions'] == self.policy['allowed_actions'] and
                cap['enforces_scope'] is True and type(cap['production_writes']) is int and cap['production_writes'] == 0 and
                type(cap['audio_generations']) is int and cap['audio_generations'] == 0,
                'NATIVE_CAPABILITY_BINDING_MISMATCH')
        self._expiry(cap['expires_at'], 'NATIVE_CAPABILITY_EXPIRED')
        require(isinstance(cap['config_files'], list) and cap['config_files'], 'NATIVE_CONFIG_CLOSURE_REQUIRED')
        self._check_files(cap)
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
        if self.policy['v'] == 2:
            require(all(tool.split('/', 1)[1] in ACTION_TOOL_ALLOWLIST[action]
                        for action, assigned in action_tools.items() for tool in assigned),
                    'NATIVE_ACTION_TOOL_MISMATCH')
            process = self._process_config()
            require(set(cap['disabled_features']) == set(process['disabled_features']) and
                    expected == {n: s['enabled_tools'] for n, s in process['mcp_servers'].items()},
                    'NATIVE_PROCESS_CAPABILITY_MISMATCH')
        return cap

    def _verify_config(self, call, cap):
        expected = cap['mcp_tools']
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
        require(any(l['name'].get('type') == 'user' and
                    l['name'].get('file') == str(self.home / 'config.toml') for l in layers),
                'NATIVE_CONFIG_CLOSURE_REQUIRED')
        config = effective['config']
        if self.policy['v'] == 2:
            # Check before thread/resume can start an MCP process. A merge that
            # retained any inherited server is rejected, even when tools/list
            # might later omit it. Unknown native projections fail closed.
            actual_servers = config.get('mcp_servers')
            expected_servers = self._overrides()['mcp_servers']
            require(isinstance(actual_servers, dict) and set(actual_servers) == set(expected_servers),
                    'NATIVE_PROCESS_TOOLSET_UNVERIFIED')
            for name, server in expected_servers.items():
                actual_server = actual_servers[name]
                # Fixed CLI alpha.6 config/read serializes McpServerConfig,
                # including these defaults (config/src/mcp_types.rs). Do not
                # remove unknown actual fields: credentials/transport/settings
                # inherited under the enabled server name must still be denied.
                projected = {**server, 'environment_id': 'local', 'tool_timeout_sec': None}
                require(isinstance(actual_server, dict) and
                        (actual_server == projected if server['enabled'] else actual_server.get('enabled') is False),
                        'NATIVE_PROCESS_TOOLSET_UNVERIFIED')
        require(config.get('sandbox_mode') == 'read-only' and config.get('web_search') == 'disabled'
                and config.get('approval_policy') == 'on-request', 'NATIVE_TOOL_BOUNDARY_REQUIRED')
        def disabled(value):
            return value is False or (self.policy['v'] == 2 and isinstance(value, dict) and value.get('enabled') is False)
        require(all(disabled(config.get('features', {}).get(f)) for f in cap['disabled_features']),
                'NATIVE_TOOL_BOUNDARY_CHANGED')
        self._verify_features(call, cap, {})
        listed = self._probe(self._mcp_args('list', '--json'), 15)
        require(listed.returncode == 0, 'NATIVE_CAPABILITY_PROBE_FAILED')
        servers = _json(listed.stdout)
        require(isinstance(servers, list), 'NATIVE_CAPABILITY_PROBE_FAILED')
        actual = {}
        for server in servers:
            require(isinstance(server, dict) and isinstance(server.get('name'), str) and
                    type(server.get('enabled')) is bool, 'NATIVE_CAPABILITY_PROBE_FAILED')
            if server['enabled']:
                name = server['name']
                require(name in expected and name not in actual, 'NATIVE_TOOLSET_CHANGED')
                detail = self._probe(self._mcp_args('get', name, '--json'), 15)
                require(detail.returncode == 0, 'NATIVE_CAPABILITY_PROBE_FAILED')
                details = _json(detail.stdout)
                require(details.get('name') == name and details.get('enabled') is True and
                        not details.get('disabled_tools'), 'NATIVE_TOOL_ALLOWLIST_UNVERIFIED')
                names = details.get('enabled_tools')
                require(isinstance(names, list) and names and all(isinstance(n, str) for n in names) and
                        len(names) == len(set(names)), 'NATIVE_TOOL_ALLOWLIST_UNVERIFIED')
                actual[name] = sorted(names)
        require(actual == {k: sorted(v) for k, v in expected.items()}, 'NATIVE_TOOLSET_CHANGED')

    @staticmethod
    def _verify_features(call, cap, target):
        # shell_tool controls registration of command tools. unified_exec is
        # the backend selector; on this CLI it stays true unless managed policy
        # disables it. An explicit additional host requirement is still enforced.
        listing = call('experimentalFeature/list', {**target, 'limit': 1000})
        require(isinstance(listing.get('data'), list) and listing.get('nextCursor') is None,
                'NATIVE_TOOL_BOUNDARY_UNVERIFIED')
        flags = {item['name']: item['enabled'] for item in listing['data']}
        require(all(flags.get(name) is False for name in cap['disabled_features']),
                'NATIVE_TOOL_BOUNDARY_CHANGED')

    def _check_files(self, cap):
        if self._policy_file:
            path, digest = self._policy_file
            require(hashlib.sha256(_private(path).read_bytes()).digest() == digest, 'NATIVE_GOAL_POLICY_CHANGED')
        hashes = {}
        for record in cap['config_files']:
            require(isinstance(record, dict) and set(record) == {'path', 'sha256'}, 'NATIVE_CONFIG_CLOSURE_REQUIRED')
            path = Path(record['path'])
            require(path.is_absolute() and path.resolve() == path and path.is_file() and path not in hashes,
                    'NATIVE_CONFIG_CLOSURE_REQUIRED')
            hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
            require(hashes[path] == record['sha256'], 'NATIVE_PROFILE_CHANGED')
        # This CLI supports --profile for 'mcp' but not 'app-server'. Accept
        # only a reviewed dedicated home whose base and named layer are identical.
        base = self.home / 'config.toml'
        if self.policy['v'] == 1:
            profile = self.home / (self.policy['profile'] + '.config.toml')
            require(base in hashes and profile in hashes and hashes[base] == hashes[profile],
                    'NATIVE_PROFILE_NOT_EQUIVALENT')
        else:
            require(canonical_hash(self.env) == cap.get('environment_sha256'), 'NATIVE_ENVIRONMENT_CHANGED')
            process = self._process_config()
            required = {base, Path(self.policy['process_config'])}
            required.update(Path(p) for s in process['mcp_servers'].values() for p in s['artifacts'])
            require(required <= set(hashes), 'NATIVE_CONFIG_CLOSURE_REQUIRED')

    @contextmanager
    def _reader(self):
        try:
            argv = [str(self.binary), 'app-server', '--stdio']
            if self.policy['v'] == 2:
                argv.extend(self._override_args())
            app = subprocess.Popen(argv, cwd=self.cwd, env=self.env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as error:
            raise TransportError('NATIVE_READER_UNAVAILABLE') from error
        buf = bytearray()
        counter = 0
        with selectors.DefaultSelector() as selector:
            selector.register(app.stdout, selectors.EVENT_READ)
            finished = {}

            def receive(deadline):
                nonlocal buf
                while b'\n' not in buf:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0 and selector.select(remaining), 'NATIVE_READ_TIMEOUT')
                    part = os.read(app.stdout.fileno(), 65536)
                    require(bool(part), 'NATIVE_READER_EXITED')
                    buf.extend(part)
                    require(len(buf) <= MAX_NATIVE, 'NATIVE_RESPONSE_TOO_LARGE')
                line, _, tail = buf.partition(b'\n')
                buf = bytearray(tail)
                answer = _json(line)
                require(isinstance(answer, dict), 'NATIVE_INVALID_JSON')
                require('method' not in answer or 'id' not in answer, 'NATIVE_APPROVAL_REQUIRED')
                if answer.get('method') == 'turn/completed':
                    params = answer.get('params', {})
                    if params.get('threadId') == self.thread_id:
                        turn = params['turn']
                        finished[turn['id']] = turn['status']
                return answer

            def call(method, params, notification=False):
                nonlocal counter
                counter += 1
                request = {'method': method, 'params': params}
                if not notification:
                    request['id'] = counter
                app.stdin.write(canonical(request, max_bytes=MAX_NATIVE) + b'\n')
                app.stdin.flush()
                if not notification:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        answer = receive(deadline)
                        if answer.get('id') == counter:
                            require(isinstance(answer.get('result'), dict), 'NATIVE_READ_REFUSED')
                            return answer['result']
                    raise TransportError('NATIVE_READ_TIMEOUT')

            def wait_turn(turn_id):
                deadline = time.monotonic() + 300
                while turn_id not in finished:
                    require(time.monotonic() < deadline, 'NATIVE_SUBMISSION_UNCONFIRMED')
                    receive(deadline)
                require(finished[turn_id] == 'completed', 'NATIVE_SUBMISSION_UNCONFIRMED')

            try:
                call('initialize', {'clientInfo': {'name': 'coordinator_goal_reader', 'version': '1'}})
                call('initialized', {}, True)
                yield call, wait_turn
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

    def _thread(self, call=None):
        if call is None:
            self._verify_binary()
            manifest = _private(self.policy['capability_manifest']).read_bytes()
            require(hashlib.sha256(manifest).hexdigest() == self.policy['capability_manifest_sha256'],
                    'NATIVE_CAPABILITY_MANIFEST_CHANGED')
            self._check_files(_json(manifest))
            with self._reader() as (reader, _):
                return self._thread(reader)
        thread = call('thread/read', {'threadId': self.thread_id, 'includeTurns': True}).get('thread')
        require(isinstance(thread, dict) and thread.get('id') == self.thread_id and
                Path(thread.get('cwd', '')).resolve() == self.cwd, 'NATIVE_THREAD_MISMATCH')
        return thread

    def lookup_goal(self, intent):
        """Read only: locate the exact original prompt without creating an action.

        PRESENT is a native reference, never a manufactured dispatch receipt.
        Missing or partial history raises; the caller must retain UNKNOWN. This
        deliberately does not support proving absence in paginated histories.
        """
        intent = self._intent(intent)
        self._binding(intent)
        thread = self._thread()
        require(thread.get('historyMode', 'legacy') == 'legacy' and isinstance(thread.get('turns'), list),
                'NATIVE_HISTORY_INCOMPLETE')
        prompt = self._prompt(intent)
        matches = []
        for turn in thread['turns']:
            require(isinstance(turn, dict) and isinstance(turn.get('id'), str) and
                    turn.get('itemsView', 'full') == 'full' and isinstance(turn.get('items'), list),
                    'NATIVE_HISTORY_INCOMPLETE')
            users = [item for item in turn['items'] if item.get('type') == 'userMessage' and
                     [part.get('text') for part in item.get('content', []) if part.get('type') == 'text'] == [prompt]]
            require(len(users) <= 1, 'NATIVE_TURN_AMBIGUOUS')
            if users:
                matches.append(turn['id'])
        require(len(matches) <= 1, 'NATIVE_TURN_AMBIGUOUS')
        if matches:
            return {'state': 'PRESENT', 'thread_id': self.thread_id, 'turn_id': matches[0],
                    'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
        # notLoaded is a complete durable view, not proof that another native
        # process has yielded control; dispatch independently requires custody.
        require(thread.get('status', {}).get('type') in ('idle', 'notLoaded') and
                all(t.get('status') in ('completed', 'failed', 'interrupted') for t in thread['turns']),
                'NATIVE_HISTORY_INCOMPLETE')
        return {'state': 'ABSENT', 'thread_id': self.thread_id}

    @staticmethod
    def _receipt(intent):
        return {k: intent[k] for k in (*CONTACT_KEYS, 'job_id', 'payload_hash')} | {'kind': 'DELEGATE_GOAL'}

    @classmethod
    def _schema(cls, intent):
        text = {'type': 'string'}
        refs = {'type': 'array', 'minItems': 1, 'items': text}
        common = {'seq': {'type': 'integer', 'minimum': 1}, 'summary': text, 'observed_at': text,
                  'evidence_refs': refs, 'next_owner': text}
        continuing = {**common, 'phase': {'type': 'string', 'enum': ['ACCEPTED', 'IN_PROGRESS', 'BLOCKED']}, 'next_check_at': text}
        completed = {**common, 'phase': {'type': 'string', 'enum': ['COMPLETED']}, 'terminal_condition': text,
                     'criteria_evidence': {'type': 'array', 'minItems': 1, 'items': {
                         'type': 'object', 'properties': {'criterion': {'type': 'string', 'enum': intent['goal']['acceptance_criteria']},
                                                       'evidence_refs': refs},
                         'required': ['criterion', 'evidence_refs'], 'additionalProperties': False}}}
        if intent['goal']['review_requirement'] == 'independent_listening':
            review = {'state': {'type': 'string', 'enum': ['VERIFIED']}, 'reviewer': text, 'producer': text,
                      'scope_refs': refs, 'evidence_refs': refs,
                      'method': {'type': 'string', 'enum': ['human_listening', 'audio_perception']}}
            completed['independent_review'] = {'type': 'object', 'properties': review,
                                               'required': list(review), 'additionalProperties': False}
        alternatives = [{'type': 'object', 'properties': p, 'required': list(p), 'additionalProperties': False}
                        for p in (continuing, completed)]
        receipt = {key: {'type': 'integer' if type(value) is int else 'string', 'enum': [value]}
                   for key, value in cls._receipt(intent).items()}
        return {'type': 'object', 'properties': {'receipt': {
                    'type': 'object', 'properties': receipt, 'required': list(receipt), 'additionalProperties': False},
                'updates': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': {'anyOf': alternatives}}},
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

    def submit_goal(self, intent, *, verify_current, preparation=None):
        intent = self._intent(intent)
        self._binding(intent)
        action = intent.get('action_id')
        require(isinstance(action, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', action),
                'INVALID_NATIVE_ACTION')
        ids = {k: intent[k] for k in CONTACT_KEYS}
        slot = self.state / action
        if slot.exists():
            require((slot / 'intent.json').read_bytes() == canonical(intent), 'NATIVE_ACTION_CONFLICT')
            return {**ids, 'kind': 'DELEGATE_GOAL', 'outcome': 'UNKNOWN', 'reason': 'ACK_UNCONFIRMED',
                    'evidence_ref': 'native-goal-reconcile:' + action}
        if preparation is None:
            with self.prepare_goal(intent) as ready:
                return self.submit_goal(intent, verify_current=verify_current, preparation=ready)
        require(self._prepared is not None and preparation is self._prepared[0], 'NATIVE_PREPARATION_INVALID')
        require(canonical_hash({k: intent[k] for k in INTENT_KEYS if k != 'action_id'}) == self._prepared[2],
                'NATIVE_PREPARATION_INVALID')
        call, wait_turn, cap, stamp = self._prepared[1]
        self._prepared = None  # Consume the capability before any native action.
        prompt = self._prompt(intent)
        schema = self._schema(intent)
        sandbox = {'type': 'readOnly', 'networkAccess': False}
        try:
            # Resume does not submit a user message. Verify this loaded thread,
            # rather than trusting persisted settings from its previous turn.
            self._check_files(cap)
            resume = {'threadId': self.thread_id, 'excludeTurns': True}
            if self.policy['v'] == 2:
                # The same reviewed restrictions also override persisted thread
                # settings. The native response still has to prove they applied.
                resume.update(config=self._overrides(), cwd=str(self.cwd),
                              sandbox='read-only', approvalPolicy='on-request')
            resumed = call('thread/resume', resume)
            require(resumed.get('thread', {}).get('id') == self.thread_id and resumed.get('cwd') == str(self.cwd)
                    and resumed.get('sandbox', {}).get('type') == 'readOnly'
                    and resumed['sandbox'].get('networkAccess', False) is False
                    and resumed.get('approvalPolicy') == 'on-request', 'NATIVE_RESUMED_POLICY_MISMATCH')
            self._verify_features(call, cap, {'threadId': self.thread_id})
            inventory = call('mcpServerStatus/list', {'threadId': self.thread_id, 'limit': 100,
                                                      'detail': 'toolsAndAuthOnly'})
            # Bounded single-round discovery. Refuse incomplete or ambiguous
            # inventories rather than guessing what else the model can call.
            actual = {}
            require(isinstance(inventory.get('data'), list) and inventory.get('nextCursor') is None,
                    'NATIVE_THREAD_TOOLSET_UNVERIFIED')
            for server in inventory['data']:
                name = server.get('name')
                require(name not in actual and isinstance(server.get('tools'), dict)
                        and not server.get('toolsError'), 'NATIVE_THREAD_TOOLSET_UNVERIFIED')
                actual[name] = sorted(server['tools'])
            require(actual == {k: sorted(v) for k, v in cap['mcp_tools'].items()}, 'NATIVE_THREAD_TOOLSET_CHANGED')
            slot.mkdir(mode=0o700)
            _new(slot / 'intent.json', canonical(intent))
            _new(slot / 'prompt.txt', prompt.encode())
            _new(slot / 'report-schema.json', canonical(schema))
            require(self._binary_stamp() == stamp, 'NATIVE_BINARY_CHANGED')
            manifest = _private(self.policy['capability_manifest'])
            require(hashlib.sha256(manifest.read_bytes()).hexdigest() == self.policy['capability_manifest_sha256'],
                    'NATIVE_CAPABILITY_MANIFEST_CHANGED')
            self._check_files(cap)
            self._expiry(cap['expires_at'], 'NATIVE_CAPABILITY_EXPIRED')
            self._expiry(self.policy['expires_at'], 'NATIVE_ADMISSION_EXPIRED')
            if self.policy['v'] == 2:
                self._check_owner_exclusivity(cap, self._thread(call), intent['deadline'])
            verify_current()
            if self.policy['v'] == 2:
                self._check_owner_exclusivity(cap, deadline=intent['deadline'])
        except (ValueError, OSError, TransportError) as error:
            code = getattr(error, 'code', 'NATIVE_PREPARATION_FAILED')
            return {**ids, 'kind': 'DELEGATE_GOAL', 'outcome': 'NOT_SENT', 'reason': 'POLICY_DENIED',
                    'evidence_ref': 'native-goal-not-started:' + action + ':' + code}

        # The turn/start frame is the sole external submission boundary. An
        # error from this line onwards cannot establish that no turn occurred.
        started = call('turn/start', {'threadId': self.thread_id, 'cwd': str(self.cwd),
                       'sandboxPolicy': sandbox, 'approvalPolicy': 'on-request',
                       'input': [{'type': 'text', 'text': prompt, 'text_elements': []}], 'outputSchema': schema})
        turn_id = started.get('turn', {}).get('id')
        require(isinstance(turn_id, str) and turn_id, 'NATIVE_SUBMISSION_UNCONFIRMED')
        _new(slot / 'start-receipt.json', canonical({'thread': self.thread_id, 'turn': turn_id}))
        wait_turn(turn_id)
        turn = self._matching_history(intent, call)[1]
        require(turn['id'] == turn_id, 'NATIVE_TURN_AMBIGUOUS')
        proof = {'thread': self.thread_id, 'turn': turn_id, 'action': action,
                 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
        _new(slot / 'submitted-proof.json', canonical(proof))
        return {**ids, 'kind': 'DELEGATE_GOAL', 'outcome': 'SUBMITTED', 'reason': 'NATIVE_SUBMISSION',
                'evidence_ref': 'native-goal-turn-sha256:' + canonical_hash(proof)}

    def _matching_history(self, intent, call=None):
        intent = self._intent(intent)
        self._binding(intent)
        slot = _private(self.state / intent['action_id'], True)
        require((slot / 'intent.json').read_bytes() == canonical(intent), 'NATIVE_ACTION_CONFLICT')
        prompt = (slot / 'prompt.txt').read_text()
        require(prompt == self._prompt(intent), 'NATIVE_PROMPT_CHANGED')
        matches = []
        history = self._thread(call).get('turns', [])
        for turn in history:
            users = [i for i in turn.get('items', []) if i.get('type') == 'userMessage' and
                     [v.get('text') for v in i.get('content', []) if v.get('type') == 'text'] == [prompt]]
            if users:
                require(len(users) == 1 and turn.get('itemsView', 'full') == 'full', 'NATIVE_TURN_AMBIGUOUS')
                matches.append(turn)
        require(len(matches) == 1, 'NATIVE_TURN_NOT_FOUND_OR_AMBIGUOUS')
        receipt = slot / 'start-receipt.json'
        if receipt.exists():
            require(_json(_private(receipt).read_bytes()) == {'thread': self.thread_id, 'turn': matches[0]['id']},
                    'NATIVE_TURN_AMBIGUOUS')
        return history, matches[0]

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
                    # These four MCP tools exchange scoped JSON/text evidence;
                    # none provides listening. Preserve the native item proof,
                    # but never elevate a model's VERIFIED claim into acceptance.
                    if (self.policy['v'] == 2 and
                            intent['goal']['review_requirement'] == 'independent_listening' and
                            report.get('phase') == 'COMPLETED'):
                        error = TransportError('NATIVE_LISTENING_CAPABILITY_UNAVAILABLE')
                        error.evidence_ref = 'native-goal-report-sha256:' + canonical_hash(proof)
                        error.validated_handles = tuple(handles)
                        raise error
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
