"""The pinned native CLI must not rewrite the original thread's security on a v2 handoff.

The optional smoke uses a fresh home, a loopback-only scripted Responses provider
and an inert stdio tool. No credentials, user thread or model are accessed.
"""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import unittest
from unittest.mock import patch

import test_coordinator_goal_channel as fixtures


class LocalProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.server.requests += 1
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        item = {'type': 'message', 'id': 'fixture-item', 'role': 'assistant', 'phase': 'final_answer',
                'content': [{'type': 'output_text', 'text': 'Fixture acknowledgement.'}]}
        for kind, fields in [
            ('response.created', {'response': {'id': 'fixture-response'}}),
            ('response.output_item.done', {'output_index': 0, 'item': item}),
            ('response.completed', {'response': {'id': 'fixture-response', 'status': 'completed', 'output': [item]}}),
        ]:
            data = json.dumps({'type': kind, **fields})
            self.wfile.write(f'event: {kind}\ndata: {data}\n\n'.encode())

    def log_message(self, *_):
        pass


STDIO_TOOL = """import json, sys
for line in sys.stdin:
    req = json.loads(line)
    if 'id' not in req:
        continue
    method = req['method']
    if method == 'initialize':
        value = {'protocolVersion': req['params']['protocolVersion'], 'capabilities': {'tools': {}},
                 'serverInfo': {'name': 'fixture', 'version': '1'}}
    elif method == 'tools/list':
        value = {'tools': [{'name': 'goal_context', 'description': 'Fixture only',
                            'inputSchema': {'type': 'object', 'properties': {}}}]}
    else:
        raise SystemExit('Unexpected tool operation')
    print(json.dumps({'jsonrpc': '2.0', 'id': req['id'], 'result': value}), flush=True)
"""


@unittest.skipUnless(os.environ.get('CODEX_GOAL_SMOKE_BINARY'), 'set CODEX_GOAL_SMOKE_BINARY for real CLI smoke')
class NativeThreadPolicy(unittest.TestCase):
    def exercise(self, *, reject):
        f = fixtures.NativeGoalChannelTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        server = HTTPServer(('127.0.0.1', 0), LocalProvider)
        server.requests = 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        binary = Path(os.environ['CODEX_GOAL_SMOKE_BINARY']).resolve(strict=True)
        with binary.open('rb') as stream:
            digest = sha256()
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block)
        f.policy.update(binary=str(binary), binary_sha256=digest.hexdigest())
        f.cap['binary_sha256'] = digest.hexdigest()
        local_config = (
            'approval_policy="on-request"\nsandbox_mode="workspace-write"\nweb_search="disabled"\n'
            'model="gpt-5.4"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="loopback"\n'
            f'base_url="http://127.0.0.1:{server.server_port}/v1"\nwire_api="responses"\n'
            'requires_openai_auth=false\nsupports_websockets=false\nrequest_max_retries=0\nstream_max_retries=0\n'
            '[mcp_servers.wide]\ncommand="/usr/bin/false"\nenabled=false\n')
        f.config_source.write_text(local_config)
        env = {'HOME': str(f.root), 'CODEX_HOME': str(f.root), 'PATH': os.defpath, 'LANG': 'en_US.UTF-8'}
        cmd = [str(binary), '-s', 'read-only', '-a', 'never', 'exec', '--skip-git-repo-check', '--json',
               'Create an isolated local fixture.']
        seeded = subprocess.run(cmd, env=env, cwd=f.cwd, capture_output=True, timeout=30, check=True)
        thread_id = next(o['thread_id'] for l in seeded.stdout.splitlines()
                         if (o := json.loads(l)).get('type') == 'thread.started')
        f.intent['endpoint'] = f.policy['endpoint'] = 'codex-cli:mac:' + thread_id
        f.native['id'] = thread_id
        f.use_process_policy()
        f.config_source.write_text(local_config)
        (f.root/'release'/fixtures.TOOL_SOURCE_FILES[0]).write_text(STDIO_TOOL)
        with patch.dict(os.environ, {'HOME': str(f.root)}):
            channel = f.process_channel()
            f.policy['cli_version'] = channel._probe([str(binary), '--version'], 10).stdout.decode().strip()
            f.cap['environment_sha256'] = fixtures.canonical_hash(channel.env)
            with channel._reader() as (read, _):
                effective = read('config/read', {'cwd': str(f.cwd), 'includeLayers': True})
                original = channel._thread(read)
            f.cap['effective_config_sha256'] = fixtures.canonical_hash(effective['config'])
            f.cap['config_layers_sha256'] = fixtures.canonical_hash([
                {k: layer[k] for k in ('name', 'version')} for layer in effective['layers']])
            proof = json.loads(f.exclusivity_path.read_bytes())
            proof.update(original_approval_policy='never', last_turn_id=original['turns'][-1]['id'])
            f.exclusivity_path.write_bytes(fixtures.canonical(proof))
            f.cap['owner_exclusivity_sha256'] = sha256(f.exclusivity_path.read_bytes()).hexdigest()
            for pin in f.cap['config_files']:
                pin['sha256'] = sha256(Path(pin['path']).read_bytes()).hexdigest()
            f.refresh_capability()
        # Read only synthetic thread security metadata to verify durable postconditions,
        # including the fine-grained sandbox representation rather than a CLI summary.
        def security():
            uri = next(f.root.glob('state_*.sqlite')).as_uri() + '?mode=ro'
            with sqlite3.connect(uri, uri=True) as db:
                return db.execute('SELECT approval_mode, sandbox_policy FROM threads WHERE id=?', (thread_id,)).fetchone()
        self.assertEqual(original.get('historyMode'), 'paginated')
        self.assertEqual(f.channel.lookup_goal(f.intent)['state'], 'ABSENT')
        before = security()
        self.assertEqual(before[0], 'never')
        self.assertEqual(server.requests, 1)
        checked = []
        def fence():
            checked.append(True)
            if reject:
                raise ValueError('Synthetic final fence refusal')
        result = f.channel.submit_goal(f.intent, verify_current=fence)
        self.assertEqual(result['outcome'], 'NOT_SENT' if reject else 'SUBMITTED', result)
        self.assertEqual(checked, [True])
        self.assertEqual(server.requests, 1 if reject else 2)
        self.assertEqual(security(), before)
        located = f.channel.lookup_goal(f.intent)
        self.assertEqual(located['state'], 'ABSENT' if reject else 'PRESENT')
        if not reject:
            receipt = json.loads((f.state/f.intent['action_id']/'start-receipt.json').read_bytes())
            self.assertEqual((located['thread_id'], located['turn_id']), (thread_id, receipt['turn']))

    def test_after_accepted_loopback_turn(self):
        self.exercise(reject=False)

    def test_after_refusal_following_resume(self):
        self.exercise(reject=True)
