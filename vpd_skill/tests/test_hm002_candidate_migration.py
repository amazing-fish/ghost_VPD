"""Offline draft migration of actual approved HM002; no production promotion."""
from copy import deepcopy
from pathlib import Path
import hashlib
import pytest
from vpdtest import cases as C
from vpdtest.util import read_json, write_json
from vpdtest.runner import run_case
from vpdtest.devices.replay import ReplayDevice
from vpdtest.devices import DeviceResult

ROOT = Path(__file__).resolve().parents[2]
DRAFT = ROOT / 'docs/validation/candidates/HM002-navigation-245'

@pytest.fixture
def candidate(ws):
    path = ws.app('motor_hm').dir / 'targets.json'
    targets = read_json(path)
    patch = read_json(DRAFT / 'targets.patch.json')['targets']
    assert not set(patch) & set(targets['targets'])
    targets['targets'].update(patch)
    write_json(path, targets)
    return read_json(DRAFT / 'TC-MOTOR-HM-002.draft.json')

def world(tmp_path, variant='car', broken=False, offbar=False):
    def screen(selected):
        tabs=[]
        for i,name in enumerate(['my'] + (['car'] if variant in ('car','both') else []) + (['buy'] if variant in ('buy','both') else [])):
            tabs.append({'t':'Column','b':[i*120,700,i*120+110,780], 'click':True,
                         'go':'my' if name=='my' or broken else 'car',
                         'attrs':{'selected':str(name==selected).lower()},
                         'children':[{'t':'Text','text':{'my':'我的','car':'爱车','buy':'购车'}[name], 'b':[i*120+10,720,i*120+100,750]}]})
        children=[{'t':'TabBar','b':[0,700,400,800],'children':tabs}]
        if offbar: children.append({'t':'Text','text':'爱车','b':[0,200,100,240]})
        return {'root':{'t':'Column','b':[0,0,400,800],'children':children}}
    path=tmp_path/'hm245-world.json'
    write_json(path,{'bundle':'app.huawei.hmos.motor','app_version':'3.0.3.200',
        'device':{'model':'SYNTHETIC-ONLY','os':'Replay'},'screen':[400,800],
        'display':{'density':1,'device_form':'bar'},'initial':{'installed':True},
        'launch_screen':'my','screens':{'my':screen('my'),'car':screen('car')}})
    return 'replay:'+str(path)

def test_candidate_minimal_delta_and_exact_baseline(ws,candidate):
    source=ws.load_case('TC-MOTOR-HM-002')
    manifest=read_json(DRAFT/'baseline.json')
    assert hashlib.sha256((ROOT/manifest['source_path']).read_text(encoding='utf-8').encode('utf-8')).hexdigest()==manifest['canonical_source_sha256']
    expected=deepcopy(source)
    for key in C.REVIEW_METADATA: expected.pop(key,None)
    expected['status']='draft'
    expected['conditions']=candidate['conditions']
    guard={'condition':'hm_car_navigation_variant_245','equals':True}
    for step in expected['steps'][1:]:step['when']=guard
    for assertion in expected['assertions']:assertion['guard']=guard
    assert candidate==expected
    assert C.content_hash(source)==manifest['approved_binding_reference_only']['content_hash']
    assert C.content_hash(candidate)!=C.content_hash(source)
    assert not any(key in candidate for key in C.REVIEW_METADATA-{'status'})
    assert not (ROOT/'vpd_skill/data/cases/TC-MOTOR-HM-002.draft.json').exists()

def test_old_approval_does_not_authorize_candidate(ws,candidate):
    assert C.review_status(ws,ws.load_case('TC-MOTOR-HM-002'))['valid']
    from vpdtest.knowledge import KnowledgeStore
    assert not C.lint(candidate,ws.app('motor_hm'),KnowledgeStore(ws.root),ws.policy())
    state=C.review_status(ws,candidate)
    assert not state['valid'] and not state['required'],state
    forged=deepcopy(candidate); forged['asset_review']=ws.load_case('TC-MOTOR-HM-002')['asset_review']
    assert not C.review_status(ws,forged)['valid']
    assert not C.replay_basis(candidate)['ok']

@pytest.mark.parametrize('variant,verdict,results',[
    ('car','passed',['pass','pass']),('buy','not_applicable',['not_applicable','not_applicable']),
    ('neither','pending',['pending','not_run']),('both','pending',['pending','not_run'])])
def test_actual_candidate_runner(ws,candidate,tmp_path,variant,verdict,results):
    original=ws.case_path('TC-MOTOR-HM-002').read_bytes()
    run=run_case(ws,case=candidate,device_spec=world(tmp_path,variant),propose=False,require_asset_review=True)
    assert run['validation_level']=='offline_replay'
    assert run['verdict']['verdict']==verdict,run['verdict']
    assert [a['result'] for a in run['assertions']]==results
    assert not run['asset_review']['valid']
    if variant!='car':
        assert run['verdict']['coverage']['verified']==[]
        assert not any(s.get('attempts') for s in run['steps'][1:])
    if variant=='buy':assert all(a['verified'] is False for a in run['assertions'])
    assert ws.case_path('TC-MOTOR-HM-002').read_bytes()==original
    assert C.review_status(ws,ws.load_case('TC-MOTOR-HM-002'))['valid']

def test_real_switch_failure_is_not_excused(ws,candidate,tmp_path):
    run=run_case(ws,case=candidate,device_spec=world(tmp_path,broken=True),propose=False)
    assert run['verdict']['verdict']=='failed',run['verdict']
    assert run['verdict']['product_failed']

def test_offbar_car_heading_cannot_override_buy_counterproof(ws,candidate,tmp_path):
    run=run_case(ws,case=candidate,device_spec=world(tmp_path,'buy',offbar=True),propose=False)
    assert run['verdict']['verdict']=='not_applicable',run['verdict']
    assert run['verdict']['coverage']['verified']==[]

def test_collection_error_not_inapplicable(ws,candidate,tmp_path,monkeypatch):
    original=ReplayDevice.dump_tree
    def fail(self,bundle,path):
        if self._in_app():return DeviceResult('dump_tree',True,False,'timeout','synthetic failure')
        return original(self,bundle,path)
    monkeypatch.setattr(ReplayDevice,'dump_tree',fail)
    run=run_case(ws,case=candidate,device_spec=world(tmp_path),propose=False)
    assert run['verdict']['verdict']=='error'
    assert not any(a['result']=='not_applicable' for a in run['assertions'])

@pytest.mark.parametrize('variant',['car','buy'])
def test_without_tabbar_narrow_fallback_cannot_select_variant(ws,candidate,tmp_path,variant):
    spec=world(tmp_path,variant)
    path=Path(spec.split(':',1)[1]); data=read_json(path)
    for screen in data['screens'].values():
        screen['root']['children'][0]['t']='Column'
    write_json(path,data)
    run=run_case(ws,case=candidate,device_spec=spec,propose=False)
    assert run['verdict']['verdict']=='pending',run['verdict']
    assert not any(a['result'] in ('pass','not_applicable') for a in run['assertions'])
    assert run['verdict']['coverage']['verified']==[]


def test_standard_loader_on_disposable_copy_only(ws,candidate,tmp_path):
    from vpdtest.knowledge import KnowledgeStore
    # ws is a disposable fixture copy, never the user's ordinary workspace.
    write_json(ws.case_path(candidate['id']),candidate)
    loaded=ws.load_case(candidate['id'])
    assert loaded==candidate
    assert not C.lint(loaded,ws.app('motor_hm'),KnowledgeStore(ws.root),ws.policy())
    run=run_case(ws,candidate['id'],device_spec=world(tmp_path),propose=False,require_asset_review=True)
    assert run['verdict']['verdict']=='passed'
    assert run['validation_level']=='offline_replay'
    assert not run['asset_review']['valid']
    assert ws.load_case(candidate['id'])==candidate
