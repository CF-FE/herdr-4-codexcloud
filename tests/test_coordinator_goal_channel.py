"""Real subprocess/I/O tests against an isolated fake native executable, no model."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from coordinator_goal_channel import CodexGoalChannel, DISABLED_FEATURES, load_goal_channel
from coordinator_transport import TransportError
from project_coordinator import canonical, canonical_hash, GOAL_BUDGET


FAKE = r'''import json,sys,pathlib
root=pathlib.Path(__file__).parent
args=sys.argv[1:]
mode=(root/'mode').read_text() if (root/'mode').exists() else ''
if args==['--version']:
 print('codex-fixture 1');sys.exit()
if 'mcp' in args:
 print(json.dumps([] if mode=='no-tools' else [{'name':'bounded','enabled':True,'enabled_tools':['coordinate'] if mode!='tool-drift' else ['unapproved']}]))
 sys.exit()
if 'features' in args:
 for name in json.loads((root/'disabled.json').read_text()):print(name,'stable','true' if mode=='shell-on' and name=='shell_tool' else 'false')
 sys.exit()
if 'app-server' in args:
 assert args[-2:]==['app-server','--stdio']
 for line in sys.stdin:
  req=json.loads(line)
  if req['method']=='initialized':continue
  if req['method']=='initialize':value={}
  elif req['method']=='config/read':
   value=json.loads((root/'effective.json').read_text())
   if mode=='effective-drift':value['config']['sandbox_mode']='danger-full-access'
   if mode=='layer-drift':value['layers'].append({'name':{'type':'project','dotCodexFolder':str(root/'new-project')},'version':'new'})
  elif req['method']=='thread/read':
   if mode=='approval':
    print(json.dumps({'id':999,'method':'item/commandExecution/requestApproval','params':{}}),flush=True)
    continue
   value={'thread':json.loads((root/'native.json').read_text())}
  else:value={}
  print(json.dumps({'id':req['id'],'result':value}),flush=True)
 sys.exit()
assert 'exec' in args and 'resume' in args
assert not any(x in args for x in ['--dangerously-bypass-approvals-and-sandbox','--full-auto','-a','-s','-c'])
prompt=sys.stdin.read();intent=json.loads(prompt.split('FROZEN_INTENT\n',1)[1])
calls=json.loads((root/'calls.json').read_text());calls.append(args);(root/'calls.json').write_text(json.dumps(calls))
thread=json.loads((root/'native.json').read_text())
receipt={k:intent[k] for k in ['project_id','request_id','action_id','generation','owner_principal','endpoint','job_id','payload_hash','kind']}
if mode=='wrong-receipt':receipt['job_id']='other'
answer={'receipt':receipt,'updates':[{'seq':1,'phase':'ACCEPTED','summary':'Accepted within the existing scope.','observed_at':'2026-11-04T01:00:00Z','evidence_refs':['fixture:accept'],'next_owner':intent['owner_principal'],'next_check_at':'2099-01-01T01:00:00Z'}]}
turn={'id':'native-turn-1','status':'completed','items':[{'id':'user-1','type':'userMessage','content':[{'type':'text','text':prompt}]},{'id':'assistant-1','type':'agentMessage','phase':'final_answer','text':json.dumps(answer)}]}
if mode!='stdout-only':thread['turns'].append(turn);(root/'native.json').write_text(json.dumps(thread))
print(json.dumps({'type':'turn.completed'}))
sys.exit(1 if mode=='lost-ack' else 0)
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
        self.config_source = self.root / 'reviewed-profile.toml'
        self.config_source.write_text('reviewed bounded fixture profile\n')
        effective={'config':{'sandbox_mode':'read-only','web_search':'disabled','approval_policy':'on-request'},
                   'layers':[{'name':{'type':'user','file':str(self.config_source),'profile':'bounded-goal'},'version':'fixture-config-1'}]}
        (self.root/'effective.json').write_text(json.dumps(effective))
        self.cap = {'v':1,'profile':'bounded-goal','binary_sha256':hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            **{k:self.intent[k] for k in ('project_id','request_id','job_id','payload_hash','owner_principal','generation')},
            'work_key':goal['work_key'],'scope_refs':scopes,'allowed_actions':goal['allowed_actions'],
            'review_evidence_ref':'fixture:independent-profile-review','expires_at':'2099-01-02T00:00:00Z',
            'config_files':[{'path':str(self.config_source),'sha256':hashlib.sha256(self.config_source.read_bytes()).hexdigest()}],
            'mcp_tools':{'bounded':['coordinate']},'action_tools':{'coordinate_existing_owner':['bounded/coordinate']},
            'disabled_features':sorted(DISABLED_FEATURES),'enforces_scope':True,'production_writes':0,'audio_generations':0,
            'effective_config_sha256':canonical_hash(effective['config']),'config_layers_sha256':canonical_hash(effective['layers'])}
        self.cap_path = self.root / 'capability.json'
        self.cap_path.write_bytes(canonical(self.cap)); self.cap_path.chmod(0o600)
        self.policy = {'v':1,'binary':str(self.binary),'binary_sha256':self.cap['binary_sha256'],
            'cli_version':'codex-fixture 1','profile':'bounded-goal','cwd':str(self.cwd),'state_dir':str(self.state),
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

    def test_real_subprocess_resume_and_independent_native_proof(self):
        checks=[]
        receipt=self.channel.submit_goal(self.intent,verify_current=lambda:checks.append('checked'))
        self.assertEqual(receipt['outcome'],'SUBMITTED')
        self.assertEqual(len(checks),2)
        self.assertEqual(len(self.calls()),1)
        self.assertEqual(self.calls()[0], [
            '--profile', self.policy['profile'], '-C', str(self.cwd),
            'exec', 'resume', self.thread_id, '--skip-git-repo-check', '--json',
            '--output-schema', str(self.state / self.intent['action_id'] / 'report-schema.json'), '-'])
        [(session,handle)]=self.channel.reconcile({**self.intent,'send':False})
        observed=self.channel.verified_observation(session,handle)
        self.assertEqual(observed.kind,'goal_update')
        self.assertEqual(observed.report['phase'],'ACCEPTED')
        self.assertEqual(self.channel.authenticated_peer(session),self.channel.peer)
        self.assertIsNone(self.channel.authenticated_peer(object()))
        self.assert_code('NATIVE_OBSERVATION_UNVERIFIED',lambda:self.channel.verified_observation(session,object()))

    def test_restart_and_lost_ack_never_repeat_native_submission(self):
        self.mode('lost-ack')
        self.assert_code('NATIVE_SUBMISSION_UNCONFIRMED',lambda:self.channel.submit_goal(self.intent,verify_current=lambda:None))
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
        self.mode('shell-on')
        self.assert_code('NATIVE_TOOL_BOUNDARY_CHANGED',lambda:self.channel.preflight(self.intent))
        self.mode('')
        self.cap['expires_at']='2000-01-01T00:00:00Z'
        self.cap_path.write_bytes(canonical(self.cap))
        policy={**self.policy,'capability_manifest_sha256':hashlib.sha256(self.cap_path.read_bytes()).hexdigest()}
        self.assert_code('NATIVE_CAPABILITY_EXPIRED',lambda:CodexGoalChannel(policy).preflight(self.intent))

    def test_native_approval_is_never_answered_or_auto_approved(self):
        self.mode('approval')
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
        self.assertEqual(schema['properties']['receipt']['const']['job_id'],'job')
        self.assertIn('seq',schema['properties']['updates']['items']['oneOf'][0]['required'])
        p.chmod(0o644)
        self.assert_code('NATIVE_PRIVATE_PATH_REQUIRED',lambda:load_goal_channel(p))

    def test_capability_probe_timeouts_are_safe_errors_before_dispatch(self):
        real_run=subprocess.run
        for command in ['--version','mcp','features']:
            def run(argv, **kwargs):
                if command in argv:
                    raise subprocess.TimeoutExpired(argv,kwargs.get('timeout',1))
                return real_run(argv,**kwargs)
            with self.subTest(command=command),patch('coordinator_goal_channel.subprocess.run',side_effect=run):
                self.assert_code('NATIVE_CAPABILITY_PROBE_TIMEOUT',lambda:self.channel.preflight(self.intent))
        self.assertEqual(self.calls(),[])
        self.assertEqual(list(self.state.iterdir()),[])


if __name__=='__main__':unittest.main()
