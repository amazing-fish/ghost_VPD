"""#247 isolated composition pilot, NOT calibrated motor_hm/NEXT login targets."""
from copy import deepcopy
from pathlib import Path
import pytest
from test_condition_runner import condition_rig, APP, BUNDLE
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import write_json, read_jsonl

RULE = 'K-PYTEST-LOGIN-IDENTITY'

@pytest.fixture
def pilot(condition_rig, tmp_path, monkeypatch):
    ws, template, _ = condition_rig
    app = ws.app(APP).dir
    write_json(ws.root / 'policy.json', {'version':'synthetic-login', 'allowed_packages':[BUNDLE],
        'allow_text_input':True, 'gated_side_effects':['account_state'], 'max_wait_s':0, 'max_actions_per_run':20})
    targets = {}
    for name,label in [('entry','注册/登录'),('user','synthetic-user-field'),('password','synthetic-password-field'),('submit','合成登录')]:
        targets[name]={'side_effect':'account_state','strategies':[{'kind':'text' if name in ('entry','submit') else 'id','value':label}]}
        if name in ('user','password'): targets[name].update(allow_input=True,secret=True)
    write_json(app/'targets.json', {'targets':targets})
    write_json(app/'states.json', {'states':{
        'identity':{'require_text':['账户就绪：SYNTHETIC-ACCOUNT-A'],'forbid_text':['注册/登录','身份验证']},
        'out':{'require_text':['注册/登录'],'forbid_text':['身份验证','账户就绪','账户就绪：SYNTHETIC-ACCOUNT-A']},
        'form':{'require_text':['合成密码表单']}},'recipes':{},'interrupts':{}})
    write_json(app/'flows.json',{'flows':{'login_candidate':{'steps':[
        {'action':'tap','target':'entry','expect_state':'form','retry':0,'when':{'condition':'needs_login','equals':True}},
        {'action':'input','target':'user','text':'${var:user}','retry':0},
        {'action':'input','target':'password','text':'${var:password}','retry':0},
        {'action':'tap','target':'submit','expect_state':'identity','timeout_s':0,'retry':0,'oracle_refs':[RULE]}]}}})
    k=KnowledgeStore(ws.root)
    k.add({'id':RULE,'kind':'product_rule','subject':'synthetic.login.identity',
        'statement':'合成登录成功必须出现独立目标账号正证，不等于任何VPD业务通过',
        'expected':'目标账号正证','applies_to':{'app':APP,'app_version':'1.0.0'},
        'source':{'type':'reviewer','ref':'pytest synthetic only'}})
    k.confirm(RULE,reviewer='pytest-only',basis='isolated contract, not motor calibration')
    probe=deepcopy(template)
    probe.update(id='PYTEST-LOGIN-PROBE',test_data={},cleanup=[],assertions=[],authorizations=[])
    probe['conditions']={'needs_login':{'kind':'page','true':{'type':'state_is','state':'out'},
        'false':{'type':'state_is','state':'identity'}}}
    probe['steps']=[{'id':'PROBE','action':'wait','duration_s':0,'when':{'condition':'needs_login','equals':True}}]
    login=deepcopy(probe)
    login.update(id='PYTEST-LOGIN-CANDIDATE',authorizations=['account_state'],
        test_data={'user':{'secret':'PYTEST_MOTOR_USER'},'password':{'secret':'PYTEST_MOTOR_PASSWORD'}})
    login['steps']=[{'id':'RECHECK','action':'wait','duration_s':0,'when':{'condition':'needs_login','equals':True}},
        {'id':'LOGIN','action':'flow','flow':'login_candidate','oracle_refs':[RULE]}]
    login['assertions']=[{'id':'IDENTITY','at':'LOGIN','type':'state_is','state':'identity','oracle_refs':[RULE]}]
    def n(text,y,**kw): return {'t':'Text','text':text,'b':[10,y,380,y+40],**kw}
    def page(*nodes): return {'root':{'t':'Column','b':[0,0,400,800],'children':list(nodes)}}
    options={'start':'out','destination':'identity','count':0,'second_start':None}
    original=ReplayDevice.__init__
    def initialized(dev,*args,**kwargs):
        original(dev,*args,**kwargs)
        # Explicit synthetic pre-existing foreground, not an unlogged device action.
        start = options['second_start'] if options['count'] and options['second_start'] else options['start']
        options['count'] += 1
        dev.state.update(foreground=BUNDLE,alive=True,stack=[start])
    monkeypatch.setattr(ReplayDevice,'__init__',initialized)
    for key in ('PYTEST_MOTOR_USER','PYTEST_MOTOR_PASSWORD'):
        monkeypatch.delenv('VPD_SECRET_'+key,raising=False)
    def run(start='out',destination='identity',missing=(),authorize=True,second_start=None):
        options.update(start=start,destination=destination,count=0,second_start=second_start)
        ws.config['secrets']={key:'SYNTHETIC-ONLY-'+key for key in ('PYTEST_MOTOR_USER','PYTEST_MOTOR_PASSWORD') if key not in missing}
        screens={'out':page(n('注册/登录',20,t='Button',click=True,go='form')),
            'form':page(n('合成密码表单',20),n('',90,t='TextInput',id='synthetic-user-field',click=True,bind='user'),
                        n('',150,t='TextInput',id='synthetic-password-field',click=True,bind='pw'),
                        n('合成登录',220,t='Button',click=True,go=destination)),
            'identity':page(n('账户就绪：SYNTHETIC-ACCOUNT-A',20)),
            'other':page(n('账户就绪',20),n('SYNTHETIC-ACCOUNT-B',80)),
            'unknown':page(n('未知页面',20)), 'challenge':page(n('身份验证',20)),
            'challenge_entry':page(n('身份验证',20),n('注册/登录',80,t='Button',click=True,go='form')),
            'other_entry':page(n('账户就绪',20),n('SYNTHETIC-ACCOUNT-B',80),n('注册/登录',140,t='Button',click=True,go='form')),
            'error':page(n('登录失败',20)), 'conflict':page(n('注册/登录',20),n('账户就绪：SYNTHETIC-ACCOUNT-A',80))}
        path=tmp_path/'login-world.json'
        write_json(path,{'bundle':BUNDLE,'app_version':'1.0.0','screen':[400,800],
            'display':{'density':1,'device_form':'bar'},'initial':{'installed':True},'screens':screens,'launch_screen':start})
        spec='replay:'+str(path)
        first=run_case(ws,case=probe,device_spec=spec,propose=False)
        status=first['steps'][0].get('condition',{}).get('status')
        if status!='true': return first,None,status
        second=run_case(ws,case=login,device_spec=spec,authorize=['account_state'] if authorize else [],propose=False)
        return first,second,status
    return run


def dispatches(run):
    return [e for e in read_jsonl(Path(run['dir'])/'events.jsonl') if e.get('event')=='dispatch']


def test_ready_identity_is_read_only_without_credentials(pilot):
    first,second,status=pilot(start='identity',missing=['PYTEST_MOTOR_USER','PYTEST_MOTOR_PASSWORD'])
    assert status=='false' and second is None
    assert not dispatches(first)


@pytest.mark.parametrize('missing',[['PYTEST_MOTOR_USER'],['PYTEST_MOTOR_PASSWORD'],['PYTEST_MOTOR_USER','PYTEST_MOTOR_PASSWORD']])
def test_any_missing_credential_stops_before_even_entry_tap(pilot,missing):
    first,second,status=pilot(missing=missing)
    assert status=='true' and second['verdict']['verdict']=='blocked'
    assert not dispatches(first) and not dispatches(second)
    assert any(x['key'].startswith('secret.') for x in second['env']['unmet'])
    # Preflight has concrete unmet requirements, NOT runtime action_required.
    assert not second['verdict'].get('action_required')
    notice = credential_handoff(second)
    assert set(notice['required']) == set(missing)
    assert '重新观察' in notice['next']


def test_password_flow_needs_independent_target_identity(pilot):
    first,second,status=pilot()
    assert second['verdict']['verdict']=='passed',second['verdict']
    assert second['assertions'][0]['verified'] and second['assertions'][0]['result']=='pass'
    assert not dispatches(first)
    assert {e.get('op') for e in dispatches(second)} <= {'tap','input_text'}


@pytest.mark.parametrize('start',['unknown','challenge','error','other','conflict','challenge_entry','other_entry'])
def test_unknown_challenge_error_or_wrong_identity_does_not_select_login(pilot,start):
    first,second,status=pilot(start=start)
    assert status=='unknown' and second is None
    assert not dispatches(first)


@pytest.mark.parametrize('destination',['unknown','challenge','error','other'])
def test_disappeared_entry_is_not_login_success(pilot,destination):
    _,run,_=pilot(destination=destination)
    assert run['verdict']['verdict']!='passed'
    assert not any(a['result']=='pass' for a in run['assertions'])


def test_account_authorization_is_not_bypassed(pilot):
    _,run,_=pilot(authorize=False)
    assert run['verdict']['verdict']=='blocked'
    assert not dispatches(run)


def credential_handoff(run):
    """Test-only caller presentation; never patch the engine's returned verdict."""
    required = [x['key'].removeprefix('secret.') for x in run.get('env', {}).get('unmet', [])
                if x['key'].startswith('secret.')]
    return {'required': required, 'next': '在本机配置所列密钥，再重新观察当前页面；不要续发旧步骤'}


def test_observation_error_never_selects_login(pilot,monkeypatch):
    monkeypatch.setattr(ReplayDevice,'dump_tree',lambda *a,**kw:
        DeviceResult('dump_tree',True,False,'timeout','synthetic collection failure'))
    first,second,status=pilot()
    assert status=='error' and second is None
    assert first['verdict']['verdict']=='error'
    assert not dispatches(first)


@pytest.mark.parametrize('second_start',['identity','other_entry','challenge_entry','unknown'])
def test_second_phase_reobserves_before_any_action(pilot,second_start):
    first,second,status=pilot(second_start=second_start)
    assert status=='true'
    assert second['verdict']['verdict']!='passed'
    assert not dispatches(first) and not dispatches(second)
    assert next(s for s in second['steps'] if s['step_id']=='LOGIN')['outcome']=='not_run'

def test_synthetic_secrets_are_references_in_public_run_metadata(pilot):
    _,run,_=pilot()
    for filename in ('run.json','events.jsonl'):
        text=(Path(run['dir'])/filename).read_text(encoding='utf-8')
        assert 'SYNTHETIC-ONLY-PYTEST_MOTOR_USER' not in text
        assert 'SYNTHETIC-ONLY-PYTEST_MOTOR_PASSWORD' not in text

@pytest.mark.parametrize('changed_page', ['challenge_entry', 'other_entry', 'unknown', 'identity'])
def test_first_flow_action_rechecks_current_page_after_probe(pilot, monkeypatch, changed_page):
    from vpdtest.execute import Executor
    original = Executor.run_step
    def changed(self, step, case_id):
        if step.get('id') == 'LOGIN':
            self.ctx.device.state['stack'] = [changed_page]
        return original(self, step, case_id)
    monkeypatch.setattr(Executor, 'run_step', changed)
    first, second, status = pilot()
    assert status == 'true'
    assert not dispatches(first) and not dispatches(second)
    assert second['verdict']['verdict'] != 'passed'
    assert not any(a['result'] == 'pass' for a in second['assertions'])
