"""Real subprocess/I/O tests against an isolated fake native executable, no model."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from coordinator_goal_channel import CodexGoalChannel, DISABLED_FEATURES, TOOL_SOURCE_FILES, load_goal_channel
from coordinator_transport import TransportError
from project_coordinator import canonical, canonical_hash, GOAL_BUDGET


FAKE = r'''import ast,io,json,os,sys,pathlib,tokenize
root=pathlib.Path(__file__).parent
args=sys.argv[1:]
mode=(root/'mode').read_text() if (root/'mode').exists() else ''
if args==['--version']:
 print('codex-fixture 1');sys.exit()
overrides={}; clean=[];i=0
while i<len(args):
 if args[i]=='--config':
  key,value=args[i+1].split('=',1);tokens=[]
  for t in tokenize.generate_tokens(io.StringIO(value).readline):
   s=':' if t.type==tokenize.OP and t.string=='=' else {'true':'True','false':'False'}.get(t.string,t.string) if t.type==tokenize.NAME else t.string
   tokens.append((t.type,s))
  overrides[key]=ast.literal_eval(tokenize.untokenize(tokens));i+=2
 else:clean.append(args[i]);i+=1
effective=json.loads((root/'effective.json').read_text())
if overrides:
 assert os.environ['CODEX_HOME']==str(root)
 assert not any(k in os.environ for k in ['PYTHONPATH','CODEX_PROFILE','OPENAI_API_KEY','BASH_ENV'])
 base=json.loads((root/'base-effective.json').read_text())
 # Model the native recursive merge, including structured-feature disabling.
 def merge(a,b):
  if isinstance(a,dict) and b is False and 'enabled' in a:return {**a,'enabled':False}
  if isinstance(a,dict) and isinstance(b,dict):return {k:merge(a.get(k),b[k]) if k in b else a[k] for k in a.keys()|b.keys()}
  return b
 effective['config']=merge(base,json.loads(json.dumps(overrides)))
 for server in effective['config']['mcp_servers'].values():
  server.setdefault('environment_id','local');server.setdefault('tool_timeout_sec',None)
 if mode=='inherited-server':effective['config']['mcp_servers']['unreviewed']={'enabled':True,'command':'do-not-run'}
 if mode=='ignored-disable':effective['config']['mcp_servers']['wide']['enabled']=True
 if mode=='inherited-enabled-field':effective['config']['mcp_servers']['bounded']['env']={'UNREVIEWED':'fixture'}
 with (root/'argv.jsonl').open('a') as f:f.write(json.dumps({'args':args,'home':os.environ['CODEX_HOME'],'overrides':overrides})+'\n')
if 'mcp' in args:
 if overrides:
  servers=effective['config']['mcp_servers'];assert clean[0]=='mcp'
 else:
  assert args[:4]==['--profile','bounded-goal','-C',str(root/'work')]
  servers={'bounded':{'enabled':True,'enabled_tools':['coordinate']}}
 if 'list' in args:value=[] if mode=='no-tools' else [{'name':n,'enabled':v.get('enabled',True)} for n,v in servers.items()]
 else:
  name=clean[-2];value={'name':name,'enabled':True,'enabled_tools':servers[name]['enabled_tools'] if mode!='tool-drift' else ['unapproved'],'disabled_tools':None}
 print(json.dumps(value));sys.exit()
assert clean==['app-server','--stdio']
def emit(value):print(json.dumps(value),flush=True)
for line in sys.stdin:
 req=json.loads(line); method=req['method'];params=req['params']
 if method=='initialized':continue
 if method=='initialize':value={}
 elif method=='config/read':
  value=json.loads(json.dumps(effective))
  if mode=='effective-drift':value['config']['sandbox_mode']='danger-full-access'
  if mode=='layer-drift':value['layers'].append({'name':{'type':'project','dotCodexFolder':str(root/'new-project')},'version':'new'})
 elif method=='experimentalFeature/list':
  names=json.loads((root/'disabled.json').read_text())
  value={'nextCursor':None,'data':[{'name':n,'enabled':n=='goals' and mode=='runtime-goals-on'} for n in names]}
 elif method=='thread/read':
  if mode in ('approval','patch-approval','dynamic-tool'):
   method={'approval':'item/commandExecution/requestApproval','patch-approval':'item/fileChange/requestApproval','dynamic-tool':'item/tool/call'}[mode]
   emit({'id':999,'method':method,'params':{}});continue
  value={'thread':json.loads((root/'native.json').read_text())}
 elif method=='thread/turns/list':
  assert params['itemsView']=='full' and params['sortDirection']=='desc' and params['threadId']==json.loads((root/'native.json').read_text())['id']
  idx=int(params.get('cursor','0'));pages=json.loads((root/'pages.json').read_text());value=pages[idx]
  with (root/'pages-read').open('a') as seen:seen.write(str(idx)+'\n')
 elif method=='thread/resume':
  thread=json.loads((root/'native.json').read_text())
  expected={'threadId':thread['id'],'excludeTurns':True}
  if overrides:
   expected['config']={k:overrides[k] for k in ('features','mcp_servers','web_search')}
   started=[n for n,s in effective['config']['mcp_servers'].items() if s.get('enabled',True)]
   (root/'started-tools.json').write_text(json.dumps(started))
   thread['status']={'type':'idle'};(root/'native.json').write_text(json.dumps(thread))
  assert params==expected
  value={'thread':thread,'cwd':thread['cwd'],'sandbox':{'type':'dangerFullAccess' if mode=='wide-resume' else 'readOnly'},'approvalPolicy':'on-request'}
  if mode=='unknown-resume':value.pop('sandbox')
 elif method=='mcpServerStatus/list':
  assert params['threadId']==json.loads((root/'native.json').read_text())['id']
  tools=overrides['mcp_servers']['bounded']['enabled_tools'] if overrides else ['coordinate']
  value={'data':[{'name':'bounded','runtimeStatus':'connected','tools':{t:{} for t in (['extra'] if mode=='wide-tools' else tools)}}],'nextCursor':None}
  if overrides:
   value['data'].append({'name':'wide','runtimeStatus':'disabled','tools':{'extra':{}} if mode=='disabled-with-tools' else {}})
  if mode=='partial-tools':value['nextCursor']='more'
 elif method=='turn/start':
  thread=json.loads((root/'native.json').read_text());assert params['threadId']==thread['id']
  if overrides:
   assert not {'approvalPolicy','sandboxPolicy','permissions','cwd'} & params.keys()
  else:
   assert params['sandboxPolicy']=={'type':'readOnly','networkAccess':False} and params['approvalPolicy']=='on-request'
  assert params['outputSchema']['properties']['updates']['items']['anyOf']
  prompt=params['input'][0]['text'];intent=json.loads(prompt.split('FROZEN_INTENT\n',1)[1])
  calls=json.loads((root/'calls.json').read_text());calls.append(params);(root/'calls.json').write_text(json.dumps(calls))
  receipt={k:intent[k] for k in ['project_id','request_id','action_id','generation','owner_principal','endpoint','job_id','payload_hash','kind']}
  if mode=='wrong-receipt':receipt['job_id']='other'
  answer={'receipt':receipt,'updates':[{'seq':1,'phase':'ACCEPTED','summary':'Accepted within the existing scope.','observed_at':'2026-11-04T01:00:00Z','evidence_refs':['fixture:accept'],'next_owner':intent['owner_principal'],'next_check_at':'2099-01-01T01:00:00Z'}]}
  turn={'id':'native-turn-1','status':'completed','items':[{'id':'user-1','type':'userMessage','content':[{'type':'text','text':prompt}]},{'id':'assistant-1','type':'agentMessage','phase':'final_answer','text':json.dumps(answer)}]}
  if mode!='stdout-only':thread['turns'].append(turn);(root/'native.json').write_text(json.dumps(thread))
  if mode=='lost-ack':sys.exit(1)
  emit({'method':'turn/completed','params':{'threadId':thread['id'],'turn':turn}})
  value={'turn':turn}
 else:raise AssertionError(method)
 emit({'id':req['id'],'result':value})
'''


class NativeGoalChannelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.root.chmod(0o700)
        self.binary = self.root / 'native-fixture'
        self.binary.write_text('#!' + str(Path(sys.executable).resolve()) + '\n' + FAKE)
        self.binary.chmod(0o700)
        self.cwd = self.root / 'work'; self.cwd.mkdir(mode=0o700)
        self.state = self.root / 'state'; self.state.mkdir(mode=0o700)
        self.thread_id = '00000000-0000-4000-8000-000000000001'
        self.native = {'id': self.thread_id, 'cwd': str(self.cwd), 'turns': []}
        self.save_native()
        (self.root / 'calls.json').write_text('[]')
        (self.root / 'disabled.json').write_text(json.dumps(sorted(DISABLED_FEATURES)))
        scopes = ['fixture:batch:sha256:abc']
        goal = {'objective': 'Coordinate the existing review.',
                'work_key': canonical_hash({'task_ref':'https://github.com/CF-FE/fixture/issues/1','scope_refs':scopes}),
                'scope_refs':scopes,'allowed_actions':['coordinate_existing_owner'],
                'acceptance_criteria':['accountable owner accepted'],'evidence_refs':['fixture:source'],
                'related_request_ids':[], 'next_check_at':'2099-01-01T01:00:00Z','review_requirement':'none'}
        self.intent = {'project_id':'fixture','request_id':'request','action_id':'contact-fixture','generation':1,
                'owner_principal':'owner:fixture','endpoint':'codex-cli:mac:'+self.thread_id,
                'job_id':'job','payload_hash':'a'*64,'authorization_ref':'fixture:approved',
                'task_ref':'https://github.com/CF-FE/fixture/issues/1','kind':'DELEGATE_GOAL',
                'budget':dict(GOAL_BUDGET),'goal':goal,'deadline':'2099-01-02T00:00:00Z'}
        self.config_source = self.root / 'config.toml'
        self.config_source.write_text('reviewed bounded fixture profile\n')
        named=self.root/'bounded-goal.config.toml'
        named.write_bytes(self.config_source.read_bytes())
        effective={'config':{'sandbox_mode':'read-only','web_search':'disabled','approval_policy':'on-request','features':dict.fromkeys(DISABLED_FEATURES,False)},
                   'layers':[{'name':{'type':'user','file':str(self.config_source)},'version':'fixture-config-1'}]}
        (self.root/'effective.json').write_text(json.dumps(effective))
        self.effective = effective
        self.cap = {'v':1,'profile':'bounded-goal','binary_sha256':hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            **{k:self.intent[k] for k in ('project_id','request_id','job_id','payload_hash','owner_principal','generation')},
            'work_key':goal['work_key'],'scope_refs':scopes,'allowed_actions':goal['allowed_actions'],
            'review_evidence_ref':'fixture:independent-profile-review','expires_at':'2099-01-02T00:00:00Z',
            'config_files':[{'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in (named,self.config_source)],
            'mcp_tools':{'bounded':['coordinate']},'action_tools':{'coordinate_existing_owner':['bounded/coordinate']},
            'disabled_features':sorted(DISABLED_FEATURES),'enforces_scope':True,'production_writes':0,'audio_generations':0,
            'effective_config_sha256':canonical_hash(effective['config']),'config_layers_sha256':canonical_hash(effective['layers'])}
        self.cap_path = self.root / 'capability.json'
        self.cap_path.write_bytes(canonical(self.cap)); self.cap_path.chmod(0o600)
        self.policy = {'v':1,'binary':str(self.binary),'binary_sha256':self.cap['binary_sha256'],
            'cli_version':'codex-fixture 1','profile':'bounded-goal','codex_home':str(self.root),'cwd':str(self.cwd),'state_dir':str(self.state),
            **{k:self.intent[k] for k in ('project_id','request_id','job_id','payload_hash','owner_principal','endpoint','generation','authorization_ref')},
            'work_key':goal['work_key'],'scope_refs':scopes,'allowed_actions':goal['allowed_actions'],
            'capability_evidence_ref':self.cap['review_evidence_ref'],'capability_manifest':str(self.cap_path),
            'capability_manifest_sha256':hashlib.sha256(self.cap_path.read_bytes()).hexdigest(),
            'expires_at':'2099-01-02T00:00:00Z'}
        self.channel = CodexGoalChannel(self.policy)

    def save_native(self):
        (self.root/'native.json').write_text(json.dumps(self.native))

    def mode(self, value):
        (self.root/'mode').write_text(value)

    def calls(self):
        return json.loads((self.root/'calls.json').read_text())

    def assert_code(self, code, fn):
        with self.assertRaises(TransportError) as e:fn()
        self.assertEqual(e.exception.code,code)

    def process_channel(self, policy=None):
        # An isolated installed-release fixture, not a runtime path override.
        with patch('coordinator_goal_channel.__file__', str(self.root/'release/scripts/coordinator_goal_channel.py')):
            channel = CodexGoalChannel(policy or self.policy)
        if channel.policy['v'] == 2:
            channel.bind_organization_parent(self.root / 'core-state')
        return channel

    def use_process_policy(self):
        self.native['status']={'type':'notLoaded'};self.save_native()
        self.config_source.write_text('sandbox_mode="workspace-write"\n[features]\nhooks=true\n'
                                      '[mcp_servers.wide]\ncommand="do-not-run"\n')
        (self.root/'bounded-goal.config.toml').unlink()
        artifacts=[]
        for name in TOOL_SOURCE_FILES:
            path=self.root/'release'/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('# isolated code artifact, never executed\n');artifacts.append(str(path))
        core_dir = self.root/'core-state'; core_dir.mkdir(mode=0o700)
        tool_state = self.root/'organization-actions'; tool_state.mkdir(mode=0o700)
        binding = {k:self.intent[k] for k in ('project_id','request_id','job_id','payload_hash','owner_principal',
                                               'endpoint','generation','authorization_ref','deadline')}
        binding.update({k:self.intent['goal'][k] for k in ('work_key','scope_refs','allowed_actions')})
        tool_policy = self.root/'tool-policy.json'
        tool_policy.write_bytes(canonical({'v':1,'role':'project_owner','binding':binding,'expires_at':binding['deadline'],
                'core_state_dir':str(core_dir),'state_dir':str(tool_state),'review_contact_attempts':0,
                'reviewer':None,'evidence':[],'source_bridge':None}))
        tool_policy.chmod(0o600)
        python=str(Path(sys.executable).resolve())
        artifacts.extend([python,str(tool_policy)])
        process={'v':1,'codex_home':str(self.root),'endpoint':self.intent['endpoint'],
                 'disabled_features':sorted(DISABLED_FEATURES),'disabled_mcp_servers':['wide'],
                 'mcp_servers':{'bounded':{'python':python,'script':str(self.root/'release'/TOOL_SOURCE_FILES[0]),
                    'policy':str(tool_policy),'enabled_tools':['goal_context'],'artifacts':artifacts}}}
        path=self.root/'process-config.json';path.write_bytes(canonical(process));path.chmod(0o600)
        self.policy={k:v for k,v in self.policy.items() if k!='profile'}
        self.policy.update(v=2,process_config=str(path),process_config_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.channel=self.process_channel()
        overlay=self.channel._overrides()
        base={'sandbox_mode':'workspace-write','approval_policy':'never','web_search':'enabled',
              'features':dict.fromkeys(DISABLED_FEATURES,True),
              'mcp_servers':{'wide':{'command':'do-not-run','enabled':True}}}
        base['features']['code_mode']={'enabled':True,'max_threads':5}
        (self.root/'base-effective.json').write_text(json.dumps(base))
        self.effective['config']=deepcopy(overlay)
        self.effective['config']['mcp_servers']['wide']={'command':'do-not-run','enabled':False}
        for server in self.effective['config']['mcp_servers'].values():
            server.update(environment_id='local',tool_timeout_sec=None)
        self.effective['config']['features']['code_mode']={'enabled':False,'max_threads':5}
        self.effective['layers'].append({'name':{'type':'sessionFlags'},'version':canonical_hash(overlay)})
        (self.root/'effective.json').write_text(json.dumps(self.effective))
        self.cap={k:v for k,v in self.cap.items() if k!='profile'}
        now=datetime.now(timezone.utc)
        proof={k:self.policy[k] for k in ('project_id','request_id','job_id','payload_hash','owner_principal',
                                         'endpoint','generation','codex_home')}
        proof.update(v=1,state='EXCLUSIVE_RELEASED',reviewer='host:independent-reviewer',
                     source_control_ref='fixture:original-host',handoff_ref='fixture:released',
                     observed_at=now.isoformat(),expires_at=(now+timedelta(minutes=10)).isoformat(),last_turn_id=None,
                     original_approval_policy='on-request',original_sandbox={'type':'readOnly','networkAccess':False})
        self.exclusivity_path=self.root/'owner-exclusivity.json'
        self.exclusivity_path.write_bytes(canonical(proof));self.exclusivity_path.chmod(0o600)
        self.cap.update(v=2,codex_home=str(self.root),endpoint=self.intent['endpoint'],
                        process_config_sha256=self.policy['process_config_sha256'],
                        owner_exclusivity=str(self.exclusivity_path),
                        owner_exclusivity_sha256=hashlib.sha256(self.exclusivity_path.read_bytes()).hexdigest(),
                        environment_sha256=canonical_hash(self.channel.env),
                        config_files=[{'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                                      for p in [self.config_source,path,*artifacts]],
                        mcp_tools={'bounded':['goal_context']},
                        action_tools={'coordinate_existing_owner':['bounded/goal_context']},
                        effective_config_sha256=canonical_hash(self.effective['config']),
                        config_layers_sha256=canonical_hash(self.effective['layers']))
        self.refresh_capability()

    def refresh_capability(self):
        self.cap_path.write_bytes(canonical(self.cap))
        self.policy['capability_manifest_sha256']=hashlib.sha256(self.cap_path.read_bytes()).hexdigest()
        self.channel=self.process_channel() if self.policy['v']==2 else CodexGoalChannel(self.policy)

    def test_process_overlay_keeps_original_home_and_thread_without_global_edits(self):
        self.use_process_policy()
        original=self.config_source.read_bytes()
        with patch.dict(os.environ,{'CODEX_HOME':'/untrusted','CODEX_PROFILE':'wide','PYTHONPATH':'/untrusted',
                                    'OPENAI_API_KEY':'fixture-not-a-credential','BASH_ENV':'/untrusted'}):
            result=self.channel.submit_goal(self.intent,verify_current=lambda:None)
        self.assertEqual(result['outcome'],'SUBMITTED')
        self.assertEqual(self.config_source.read_bytes(),original)
        self.assertFalse((self.root/'bounded-goal.config.toml').exists())
        self.assertEqual(json.loads((self.root/'started-tools.json').read_text()),['bounded'])
        invocations=[json.loads(s) for s in (self.root/'argv.jsonl').read_text().splitlines()]
        self.assertEqual({v['home'] for v in invocations},{str(self.root)})
        self.assertEqual(len({canonical_hash(v['overrides']) for v in invocations}),1)
        self.assertEqual(self.calls()[0]['threadId'],self.thread_id)
        self.assertFalse({'sandboxPolicy','approvalPolicy','permissions','cwd'} & self.calls()[0].keys())
        self.assertEqual(json.loads((self.root/'native.json').read_text())['status']['type'],'idle')

    def test_process_overlay_rejects_inherited_server_before_any_resume(self):
        self.use_process_policy()
        for mode in ('inherited-server','ignored-disable','inherited-enabled-field'):
            self.mode(mode)
            changed=deepcopy(self.effective['config'])
            if mode=='inherited-server':changed['mcp_servers']['unreviewed']={'enabled':True,'command':'do-not-run'}
            elif mode=='ignored-disable':changed['mcp_servers']['wide']['enabled']=True
            else:changed['mcp_servers']['bounded']['env']={'UNREVIEWED':'fixture'}
            self.cap['effective_config_sha256']=canonical_hash(changed);self.refresh_capability()
            self.assert_code('NATIVE_PROCESS_TOOLSET_UNVERIFIED',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
            self.assertFalse((self.root/'started-tools.json').exists())
        self.assertEqual(self.calls(),[])

    def test_process_overlay_drift_and_incomplete_tool_artifacts_reject(self):
        self.use_process_policy()
        self.cap['config_files']=[p for p in self.cap['config_files'] if not p['path'].endswith('/coordinator_bridge.py')]
        self.refresh_capability()
        self.assert_code('NATIVE_CONFIG_CLOSURE_REQUIRED',lambda:self.channel.preflight(self.intent))
        path=Path(self.policy['process_config']);path.write_text('{}')
        self.assert_code('NATIVE_PROFILE_CHANGED',lambda:self.channel.preflight(self.intent))
        self.assertEqual(self.calls(),[])

    def test_process_overlay_rejects_unknown_resumed_policy_and_tools(self):
        self.use_process_policy()
        for mode in ('wide-resume','unknown-resume','wide-tools','partial-tools','disabled-with-tools'):
            self.mode(mode)
            result=self.channel.submit_goal(self.intent,verify_current=lambda:None)
            self.assertEqual(result['outcome'],'NOT_SENT')
        self.assertEqual(self.calls(),[])
        self.assertEqual(list(self.state.iterdir()),[])

    def test_process_overlay_lost_ack_is_not_replayed(self):
        self.use_process_policy();self.mode('lost-ack')
        self.assert_code('NATIVE_READER_EXITED',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.mode('');channel=self.process_channel()
        self.assertEqual(channel.submit_goal(self.intent,verify_current=lambda:None)['outcome'],'UNKNOWN')
        self.assertEqual(len(channel.reconcile(self.intent)),1)
        self.assertEqual(len(self.calls()),1)

    def test_process_overlay_requires_fresh_bound_host_exclusivity(self):
        self.use_process_policy()
        original=json.loads(self.exclusivity_path.read_text())
        for changes,code in [({'state':'UNKNOWN'},'HOST_OWNER_EXCLUSIVITY_REQUIRED'),
                             ({'original_approval_policy':'unverified'},'NATIVE_ORIGINAL_POLICY_UNVERIFIED'),
                             ({'original_sandbox':{'type':'workspaceWrite'}},'NATIVE_ORIGINAL_POLICY_UNVERIFIED'),
                             ({'generation':2},'HOST_OWNER_EXCLUSIVITY_BINDING_MISMATCH'),
                             ({'observed_at':'2000-01-01T00:00:00Z'},'HOST_OWNER_EXCLUSIVITY_EXPIRED'),
                             ({'reviewer':self.intent['owner_principal']},'HOST_OWNER_EXCLUSIVITY_REQUIRED')]:
            self.exclusivity_path.write_bytes(canonical({**original,**changes}))
            self.cap['owner_exclusivity_sha256']=hashlib.sha256(self.exclusivity_path.read_bytes()).hexdigest()
            self.refresh_capability()
            self.assert_code(code,lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.assertEqual(self.calls(),[])
        self.assertFalse((self.root/'started-tools.json').exists())

    def test_process_overlay_checks_exclusivity_again_at_final_fence(self):
        self.use_process_policy()
        def change():
            self.exclusivity_path.write_text('{}')
        result=self.channel.submit_goal(self.intent,verify_current=change)
        self.assertEqual(result['outcome'],'NOT_SENT')
        self.assertIn('HOST_OWNER_EXCLUSIVITY_CHANGED',result['evidence_ref'])
        self.assertEqual(self.calls(),[])

    def test_process_environment_change_is_rejected_before_resume(self):
        self.use_process_policy()
        self.channel.env['HOME']=str(self.cwd)
        self.assert_code('NATIVE_ENVIRONMENT_CHANGED',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.assertEqual(self.calls(),[])
        self.assertFalse((self.root/'started-tools.json').exists())

    def test_expired_host_exclusivity_preserves_existing_unknown_readback(self):
        self.use_process_policy();self.mode('lost-ack')
        self.assert_code('NATIVE_READER_EXITED',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.mode('')
        proof=json.loads(self.exclusivity_path.read_text())
        proof.update(observed_at='2000-01-01T00:00:00Z',expires_at='2000-01-01T00:01:00Z')
        self.exclusivity_path.write_bytes(canonical(proof))
        # No renewal, changed admission proof, or second turn is needed for
        # reading the already frozen action through its verified native source.
        channel=self.process_channel()
        self.assertEqual(channel.submit_goal(self.intent,verify_current=lambda:None)['outcome'],'UNKNOWN')
        self.assertEqual(channel.lookup_goal(self.intent)['state'],'PRESENT')
        self.assertEqual(len(channel.reconcile(self.intent)),1)
        self.assertEqual(len(self.calls()),1)

    def test_process_overlay_new_native_turn_invalidates_host_exclusivity(self):
        self.use_process_policy()
        self.native['turns']=[{'id':'unreleased-later-turn','status':'completed','items':[]}];self.save_native()
        self.assert_code('HOST_OWNER_EXCLUSIVITY_STALE',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.assertEqual(self.calls(),[])

    def test_process_action_map_cannot_grant_coordination_to_verify_or_report(self):
        self.use_process_policy()
        self.intent['goal']['allowed_actions']=['verify_existing_evidence','report_progress']
        self.policy['allowed_actions']=self.intent['goal']['allowed_actions']
        self.cap['allowed_actions']=self.intent['goal']['allowed_actions']
        self.cap['mcp_tools']={'bounded':['coordinate_independent_review']}
        self.cap['action_tools']={a:['bounded/coordinate_independent_review'] for a in self.cap['allowed_actions']}
        self.refresh_capability()
        self.assert_code('NATIVE_ACTION_TOOL_MISMATCH',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.assertFalse((self.root/'started-tools.json').exists())
        self.assertFalse((self.root/'argv.jsonl').exists())
        self.assertEqual(self.calls(),[])

    def add_self_reported_listening_completion(self):
        native=json.loads((self.root/'native.json').read_text())
        reply=json.loads(native['turns'][0]['items'][-1]['text'])
        reply['updates'].append({'seq':2,'phase':'COMPLETED','summary':'Self-reported listening complete.',
            'observed_at':'2026-11-04T01:01:00Z','evidence_refs':['fixture:unverified-listening'],
            'next_owner':self.intent['owner_principal'],'terminal_condition':'Self-reported completion.',
            'criteria_evidence':[{'criterion':c,'evidence_refs':['fixture:unverified-listening']}
                                 for c in self.intent['goal']['acceptance_criteria']],
            'independent_review':{'state':'VERIFIED','reviewer':'fixture:reviewer','producer':'fixture:producer',
                'scope_refs':self.intent['goal']['scope_refs'],'evidence_refs':['fixture:unverified-listening'],
                'method':'audio_perception'}})
        native['turns'][0]['items'][-1]['text']=json.dumps(reply)
        (self.root/'native.json').write_text(json.dumps(native))

    def test_process_listening_completion_is_not_accepted_from_native_self_report(self):
        self.intent['goal']['review_requirement']='independent_listening'
        self.use_process_policy()
        self.channel.submit_goal(self.intent,verify_current=lambda:None)
        self.add_self_reported_listening_completion()
        with self.assertRaises(TransportError) as caught:
            self.channel.reconcile(self.intent)
        self.assertEqual(caught.exception.code,'NATIVE_LISTENING_CAPABILITY_UNAVAILABLE')
        self.assertTrue(caught.exception.evidence_ref.startswith('native-goal-report-sha256:'))
        accepted=[self.channel.verified_observation(s,h).report for s,h in caught.exception.validated_handles]
        self.assertEqual([r['phase'] for r in accepted],['ACCEPTED'])
        proofs=list((self.state/self.intent['action_id']).glob('native-item-*.json'))
        self.assertEqual(len(proofs),1)
        self.assertEqual(json.loads(proofs[0].read_text())['turn_id'],'native-turn-1')
        self.assertEqual(self.channel.lookup_goal(self.intent)['state'],'PRESENT')
        self.assertEqual(self.channel.submit_goal(self.intent,verify_current=lambda:None)['outcome'],'UNKNOWN')
        self.assertEqual(len(self.calls()),1)

    def test_rejected_completion_does_not_hide_a_later_correction(self):
        self.intent['goal']['review_requirement'] = 'independent_listening'
        self.use_process_policy()
        self.channel.submit_goal(self.intent, verify_current=lambda: None)
        self.add_self_reported_listening_completion()
        self.assert_code('NATIVE_LISTENING_CAPABILITY_UNAVAILABLE', lambda: self.channel.reconcile(self.intent))

        path = self.root / 'native.json'
        native = json.loads(path.read_text())
        original = json.loads(native['turns'][0]['items'][-1]['text'])
        correction = dict(original['updates'][0], seq=2, phase='IN_PROGRESS',
                          summary='Reviewer accepted the batch and supplied the first receipt time.')
        reply = {'receipt': original['receipt'], 'updates': [correction]}
        native['turns'].append({'id': 'correction-turn', 'status': 'completed', 'items': [
            {'type': 'agentMessage', 'id': 'correction-message', 'text': json.dumps(reply)}]})
        path.write_text(json.dumps(native))

        channel = self.process_channel()
        reports = [channel.verified_observation(s, h).report for s, h in channel.reconcile(self.intent)]
        self.assertEqual([(r['seq'], r['phase']) for r in reports], [(1, 'ACCEPTED'), (2, 'IN_PROGRESS')])
        self.assertEqual(len(self.calls()), 1)

    def test_legacy_listening_report_contract_is_unchanged(self):
        self.intent['goal']['review_requirement']='independent_listening'
        self.channel.submit_goal(self.intent,verify_current=lambda:None)
        self.add_self_reported_listening_completion()
        reports=[self.channel.verified_observation(s,h).report for s,h in self.channel.reconcile(self.intent)]
        self.assertEqual([r['phase'] for r in reports],['ACCEPTED','COMPLETED'])

    def test_lookup_is_read_only_and_requires_complete_idle_history_for_absence(self):
        self.native['status']={'type':'idle'};self.save_native()
        self.assertEqual(self.channel.lookup_goal(self.intent)['state'],'ABSENT')
        self.assertEqual(list(self.state.iterdir()),[])
        self.native['turns']=[{'id':'existing','status':'completed','items':[
            {'type':'userMessage','content':[{'type':'text','text':self.channel._prompt(self.intent)}]}]}]
        self.save_native()
        found=self.channel.lookup_goal(self.intent)
        self.assertEqual((found['state'],found['turn_id']),('PRESENT','existing'))
        self.assertEqual(self.calls(),[]);self.assertEqual(list(self.state.iterdir()),[])
        self.native['turns'][0].update(itemsView='summary',items=[]);self.save_native()
        self.assert_code('NATIVE_HISTORY_INCOMPLETE',lambda:self.channel.lookup_goal(self.intent))
        self.native['turns']=[];self.native['historyMode']='paginated';self.save_native()
        partial={'id':'partial','status':'completed','itemsView':'summary','items':[]}
        hit={'id':'exact','status':'completed','itemsView':'full','items':[
            {'type':'userMessage','content':[{'type':'text','text':self.channel._prompt(self.intent)}]}]}
        pages=self.root/'pages.json'
        pages.write_text(json.dumps([{'data':[partial],'nextCursor':'1'},{'data':[hit],'nextCursor':'2'}]))
        self.assertEqual(self.channel.lookup_goal(self.intent)['turn_id'],'exact')
        self.assertEqual((self.root/'pages-read').read_text().splitlines(),['0','1'])
        pages.write_text(json.dumps([{'data':[partial],'nextCursor':'1'},{'data':[],'nextCursor':None}]))
        self.assert_code('NATIVE_HISTORY_INCOMPLETE',lambda:self.channel.lookup_goal(self.intent))
        pages.write_text(json.dumps([{'data':[],'nextCursor':'1'},{'data':[],'nextCursor':'1'}]))
        self.assert_code('NATIVE_HISTORY_INCOMPLETE',lambda:self.channel.lookup_goal(self.intent))
        pages.write_text(json.dumps([{'data':[],'nextCursor':'1'},{'data':[],'nextCursor':None}]))
        self.assertEqual(self.channel.lookup_goal(self.intent)['state'],'ABSENT')
        self.native['historyMode']='legacy';self.native['status']={'type':'active'};self.save_native()
        self.assert_code('NATIVE_HISTORY_INCOMPLETE',lambda:self.channel.lookup_goal(self.intent))

    def test_real_subprocess_resume_and_independent_native_proof(self):
        checks=[]
        receipt=self.channel.submit_goal(self.intent,verify_current=lambda:checks.append('checked'))
        self.assertEqual(receipt['outcome'],'SUBMITTED')
        self.assertEqual(len(checks),1)
        self.assertEqual(len(self.calls()),1)
        self.assertEqual(self.calls()[0]['threadId'], self.thread_id)
        self.assertEqual(self.calls()[0]['input'][0]['text'], self.channel._prompt(self.intent))
        [(session,handle)]=self.channel.reconcile({**self.intent,'send':False})
        observed=self.channel.verified_observation(session,handle)
        self.assertEqual(observed.kind,'goal_update')
        self.assertEqual(observed.report['phase'],'ACCEPTED')
        self.assertEqual(self.channel.authenticated_peer(session),self.channel.peer)
        self.assertIsNone(self.channel.authenticated_peer(object()))
        self.assert_code('NATIVE_OBSERVATION_UNVERIFIED',lambda:self.channel.verified_observation(session,object()))

    def test_restart_and_lost_ack_never_repeat_native_submission(self):
        self.mode('lost-ack')
        self.assert_code('NATIVE_READER_EXITED',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        reopened=CodexGoalChannel(self.policy)
        self.mode('')
        self.assertEqual(reopened.submit_goal(self.intent,verify_current=lambda:None)['outcome'],'UNKNOWN')
        self.assertEqual(len(self.calls()),1)
        self.assertEqual(len(reopened.reconcile(self.intent)),1)

    def test_stdout_is_not_native_submission_evidence(self):
        self.mode('stdout-only')
        self.assert_code('NATIVE_TURN_NOT_FOUND_OR_AMBIGUOUS',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.assertEqual(len(self.calls()),1)

    def test_later_native_turn_progress_is_read_without_resume(self):
        self.channel.submit_goal(self.intent,verify_current=lambda:None)
        native=json.loads((self.root/'native.json').read_text())
        reply=json.loads(native['turns'][0]['items'][-1]['text'])
        reply['updates'][0].update(seq=2,phase='IN_PROGRESS',summary='Original evidence checked.')
        native['turns'].append({'id':'later-turn','status':'completed','items':[
            {'type':'agentMessage','id':'ordinary','text':'Unrelated prose.'},
            {'type':'agentMessage','id':'later-report','text':json.dumps(reply)}]})
        (self.root/'native.json').write_text(json.dumps(native))
        reports=[self.channel.verified_observation(s,h).report for s,h in self.channel.reconcile(self.intent)]
        self.assertEqual([r['seq'] for r in reports],[1,2])
        self.assertEqual(len(self.calls()),1)

    def test_other_job_reply_is_not_accepted(self):
        self.mode('wrong-receipt')
        self.channel.submit_goal(self.intent,verify_current=lambda:None)
        self.assert_code('NATIVE_REPORT_NOT_READY',lambda:self.channel.reconcile(self.intent))

    def test_same_native_item_bytes_are_immutable_across_restart(self):
        self.channel.submit_goal(self.intent,verify_current=lambda:None)
        self.channel.reconcile(self.intent)
        native=json.loads((self.root/'native.json').read_text())
        native['turns'][0]['items'][-1]['text'] += ' '
        (self.root/'native.json').write_text(json.dumps(native))
        self.assert_code('NATIVE_REPORT_CHANGED',lambda:CodexGoalChannel(self.policy).reconcile(self.intent))

    def test_old_query_profile_and_tool_drift_reject_before_reservation(self):
        for mode in ['no-tools','tool-drift']:
            self.mode(mode)
            self.assert_code('NATIVE_TOOLSET_CHANGED',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.assertEqual(self.calls(),[])
        self.assertEqual(list(self.state.iterdir()),[])

    def test_profile_config_and_capability_evidence_drift_reject(self):
        self.config_source.write_text('unexpected same-name full-access profile')
        self.assert_code('NATIVE_PROFILE_CHANGED',lambda:self.channel.preflight(self.intent))
        self.cap_path.write_text('{}')
        self.assert_code('NATIVE_CAPABILITY_MANIFEST_CHANGED',lambda:self.channel.preflight(self.intent))

    def test_effective_and_inherited_configuration_drift_reject(self):
        self.mode('effective-drift')
        self.assert_code('NATIVE_EFFECTIVE_CONFIG_CHANGED',lambda:self.channel.preflight(self.intent))
        self.mode('layer-drift')
        self.assert_code('NATIVE_CONFIG_LAYERS_CHANGED',lambda:self.channel.preflight(self.intent))

    def test_capability_expiry_and_builtin_escape_reject(self):
        self.mode('runtime-goals-on')
        self.assert_code('NATIVE_TOOL_BOUNDARY_CHANGED',lambda:self.channel.preflight(self.intent))
        self.mode('')
        self.effective['config']['features']['shell_tool']=True
        (self.root/'effective.json').write_text(json.dumps(self.effective))
        self.cap['effective_config_sha256']=canonical_hash(self.effective['config'])
        self.cap_path.write_bytes(canonical(self.cap))
        self.channel.policy['capability_manifest_sha256']=hashlib.sha256(self.cap_path.read_bytes()).hexdigest()
        self.assert_code('NATIVE_TOOL_BOUNDARY_CHANGED',lambda:self.channel.preflight(self.intent))
        self.cap['expires_at']='2000-01-01T00:00:00Z'
        self.cap_path.write_bytes(canonical(self.cap))
        policy={**self.policy,'capability_manifest_sha256':hashlib.sha256(self.cap_path.read_bytes()).hexdigest()}
        self.assert_code('NATIVE_CAPABILITY_EXPIRED',lambda:CodexGoalChannel(policy).preflight(self.intent))

    def test_native_approval_is_never_answered_or_auto_approved(self):
        for mode in ('approval','patch-approval','dynamic-tool'):
            self.mode(mode)
            self.assert_code('NATIVE_APPROVAL_REQUIRED',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.assertEqual(self.calls(),[])

    def test_binding_scope_and_expiry_fail_closed(self):
        changed=deepcopy(self.intent);changed['goal']['scope_refs']=['other-asset']
        self.assert_code('NATIVE_GOAL_SCOPE_MISMATCH',lambda:self.channel.preflight(changed))
        expired=CodexGoalChannel({**self.policy,'expires_at':'2000-01-01T00:00:00Z'})
        self.assert_code('NATIVE_ADMISSION_EXPIRED',lambda:expired.preflight(self.intent))
        changed=deepcopy(self.intent);changed['budget']['audio_generations']=1
        self.assert_code('NATIVE_GOAL_BUDGET_MISMATCH',lambda:self.channel.preflight(changed))

    def test_private_policy_and_receipt_schema_are_explicit(self):
        p=self.root/'goal-channel.json';p.write_bytes(canonical(self.policy));p.chmod(0o600)
        self.assertEqual(load_goal_channel(p).peer,self.channel.peer)
        schema=self.channel._schema(self.intent)
        self.assertEqual(schema['properties']['receipt']['properties']['job_id']['enum'],['job'])
        self.assertIn('seq',schema['properties']['updates']['items']['anyOf'][0]['required'])
        p.chmod(0o644)
        self.assert_code('NATIVE_PRIVATE_PATH_REQUIRED',lambda:load_goal_channel(p))

    def test_rejects_broader_profile_actions_and_inherited_thread_tools(self):
        extra=deepcopy(self.policy)
        extra['allowed_actions'].append('verify_existing_evidence')
        self.assert_code('NATIVE_GOAL_SCOPE_MISMATCH',lambda:CodexGoalChannel(extra).preflight(self.intent))
        for mode in ('wide-resume', 'wide-tools'):
            self.mode(mode)
            self.assertEqual(self.channel.submit_goal(self.intent,verify_current=lambda:None)['outcome'],'NOT_SENT')
        self.assertEqual(self.calls(),[])

    def test_busy_and_fence_failures_do_not_start_a_turn(self):
        self.native['status']={'type':'active','activeFlags':[]}
        self.save_native()
        self.assert_code('NATIVE_OWNER_BUSY', lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
        self.native['status']={'type':'idle'}
        self.save_native()
        def deny():
            raise TransportError('CONTACT_STALE')
        with patch.object(self.channel,'_verify_config',wraps=self.channel._verify_config) as verify:
            with self.channel.prepare_goal(self.intent) as ready:
                receipt=self.channel.submit_goal(self.intent,verify_current=deny,preparation=ready)
                self.assertEqual(receipt['outcome'],'NOT_SENT')
            self.assertEqual(verify.call_count,1)
        self.assertEqual(self.calls(),[])

    def test_expired_dispatch_permission_still_allows_bound_receipt_read(self):
        self.channel.submit_goal(self.intent,verify_current=lambda:None)
        closed=CodexGoalChannel({**self.policy,'expires_at':'2000-01-01T00:00:00Z'})
        self.assertEqual(len(closed.reconcile(self.intent)),1)
        self.assertEqual(len(self.calls()),1)

    def test_named_profile_must_match_reviewed_runtime_home(self):
        f=self.root/'bounded-goal.config.toml'
        f.write_text('different profile')
        self.cap['config_files'][0]['sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
        self.cap_path.write_bytes(canonical(self.cap))
        self.channel.policy['capability_manifest_sha256']=hashlib.sha256(self.cap_path.read_bytes()).hexdigest()
        self.assert_code('NATIVE_PROFILE_NOT_EQUIVALENT',lambda:self.channel.preflight(self.intent))

    @unittest.skipUnless(os.environ.get('CODEX_GOAL_SMOKE_BINARY'), 'set CODEX_GOAL_SMOKE_BINARY for real CLI preflight')
    def test_native_cli_credential_free_preflight_without_a_thread_or_model(self):
        binary=Path(os.environ['CODEX_GOAL_SMOKE_BINARY']).resolve(strict=True)
        home=self.root/'isolated-codex'; home.mkdir(mode=0o700)
        contents=('sandbox_mode="read-only"\nweb_search="disabled"\napproval_policy="on-request"\n'
                  '[mcp_servers.bounded]\ncommand="/usr/bin/false"\nenabled_tools=["coordinate"]\n[features]\n'
                  + ''.join(f'{name}=false\n' for name in sorted(DISABLED_FEATURES)))
        sources=[home/'config.toml',home/'bounded-goal.config.toml']
        for f in sources:f.write_text(contents)
        digest=hashlib.sha256()
        with binary.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024), b''):digest.update(chunk)
        policy={**self.policy,'binary':str(binary),'binary_sha256':digest.hexdigest(),'codex_home':str(home)}
        with patch.dict(os.environ,{'HOME':str(self.root)}):
            # Setup reads the actual version and effective projection solely in the disposable home.
            preview=CodexGoalChannel(policy)
            policy['cli_version']=preview._probe([str(binary),'--version'],10).stdout.decode().strip()
            with preview._reader() as (read,_):
                effective=read('config/read',{'cwd':str(self.cwd),'includeLayers':True})
            cap={**self.cap,'binary_sha256':digest.hexdigest(),
                 'config_files':[{'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in sources],
                 'effective_config_sha256':canonical_hash(effective['config']),
                 'config_layers_sha256':canonical_hash([{k:l[k] for k in ('name','version')} for l in effective['layers']])}
            self.cap_path.write_bytes(canonical(cap))
            policy['capability_manifest_sha256']=hashlib.sha256(self.cap_path.read_bytes()).hexdigest()
            result=CodexGoalChannel(policy).preflight(self.intent)
        self.assertEqual(result['state'],'HOST_ADMISSION_BOUND')
        self.assertEqual(list(self.state.iterdir()),[])

    def test_capability_probe_timeouts_are_safe_errors_before_dispatch(self):
        real_run=subprocess.run
        for command in ['--version','mcp']:
            def run(argv, **kwargs):
                if command in argv:
                    raise subprocess.TimeoutExpired(argv,kwargs.get('timeout',1))
                return real_run(argv,**kwargs)
            with self.subTest(command=command),patch('coordinator_goal_channel.subprocess.run',side_effect=run):
                self.assert_code('NATIVE_CAPABILITY_PROBE_TIMEOUT',lambda:self.channel.preflight(self.intent))
        self.assertEqual(self.calls(),[])
        self.assertEqual(list(self.state.iterdir()),[])


if __name__=='__main__':unittest.main()
