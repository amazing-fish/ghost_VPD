import copy
import json
from pathlib import Path
from vpdtest.layout import check_requirements
from vpdtest.locate import resolve
from vpdtest.tree import UiTree

ROOT = Path(__file__).resolve().parents[1]

def candidate():
    return json.loads((ROOT/'data/cases/TC-HM-S01-FOLD-DRAFT.json').read_text(encoding='utf-8'))

def test_candidate_is_unreviewed_not_replay_evidence():
    c=candidate()
    assert c['status']=='draft'
    for key in ('asset_review','replays','history','product_status'):
        assert key not in c
    assert 'observed' not in json.dumps(c)
    assert c['authorizations']==[]
    assert c['assertions'][0]['target']=='logout_entry'
    assert all(x.get('target')!='logout_entry' for x in c['steps'])

def test_bounded_explicit_scroll_and_fresh_target():
    c=candidate()
    scroll=[s for s in c['steps'] if s['action']=='swipe']
    assert len(scroll)==2
    assert all(s['within']=={'type':'List'} and s['retry']==0 for s in scroll)
    target=next(s['target'] for s in c['steps'] if s['id']=='S4')
    assert target=='settings_entry'

def test_layout_is_explicit_not_pixel_inference():
    assert check_requirements(None,{'wide':True})==[]
    req=candidate()['layout_requirements']
    assert check_requirements(req,{'device_form':'foldable'})==[]
    assert check_requirements(req,{'device_form':'bar'})
    assert check_requirements(req,{})

def test_observed_layout_key_is_not_a_locator_gate():
    tree=UiTree({'attributes':{'type':'Root','bounds':'[0,0][1200,1800]'},'children':[{'attributes':{'type':'Text','text':'设置','bounds':'[100,100][400,200]'}}]})
    target={'strategies':[{'kind':'text','value':'设置'}], 'observed':{'layout_key':'sm/portrait'}}
    lay={'wide':True,'width_class':'md','orientation':'portrait'}
    got=resolve('settings',target,tree,None,ROOT,layout=lay)
    plain=copy.deepcopy(target); plain.pop('observed')
    other=resolve('settings',plain,tree,None,ROOT,layout=lay)
    assert got['locator_fingerprint'] != other['locator_fingerprint']
    assert {k:v for k,v in got.items() if k!='locator_fingerprint'} == {k:v for k,v in other.items() if k!='locator_fingerprint'}
    assert got['status']=='found'


def test_candidate_reuses_registered_target_and_lints_cleanly():
    from vpdtest.cases import lint
    from vpdtest.knowledge import KnowledgeStore
    from vpdtest.workspace import Workspace
    ws=Workspace(str(ROOT/'data'),load_local_config=False)
    issues=lint(candidate(),ws.app('motor_hm'),KnowledgeStore(ws.root),ws.policy())
    assert issues==[]

# Entirely synthetic world; no phone observation or product coverage claim.
def synthetic_world(tmp_path, variant='normal'):
    from vpdtest.devices.replay import load_world
    from conftest import WORLDS
    w=load_world(WORLDS/'motor_hm_fold.json')
    w.pop('extends',None)
    w['app_version']='3.0.3.200'
    w['env_facts']={'account_state':'logged_in'}
    w['screens']['my_tab_middle']=copy.deepcopy(w['screens']['my_tab'])
    w['screens']['my_tab']['on_swipe_up']='my_tab_middle'
    w['screens']['my_tab_middle']['on_swipe_up']='my_tab_scrolled'
    def walk(n):
        yield n
        for child in n.get('children',[]):
            yield from walk(child)
    # Logged-in synthetic precondition, not a captured account identity.
    for screen in w['screens'].values():
        for n in walk(screen['root']):
            if n.get('text')=='注册/登录': n['text']='合成昵称'
    if variant=='missing':
        for name in ('my_tab','my_tab_middle','my_tab_scrolled'):
            for n in walk(w['screens'][name]['root']):
                if n.get('t')=='List': n['t']='Column'; n['scroll']=False
    if variant=='multiple':
        w['screens']['my_tab']['root']['children'].append({'t':'List','scroll':True,'b':[1100,100,2100,2400],'children':[]})
    if variant=='no_settings':
        for n in walk(w['screens']['my_tab_scrolled']['root']):
            if n.get('text')=='设置': n['text']='其他'
    f=tmp_path/f'{variant}.json'; f.write_text(json.dumps(w,ensure_ascii=False),encoding='utf-8')
    return 'replay:'+str(f)

def test_candidate_full_run_and_old_case_counterexample(ws,tmp_path):
    from vpdtest.runner import run_case
    spec=synthetic_world(tmp_path)
    r=run_case(ws,case=candidate(),device_spec=spec,env={'account_state':'logged_in'},propose=False)
    assert r['verdict']['verdict']=='passed',r['verdict']
    assert r['informal'] is True
    assert r['verdict']['coverage']['verified']==['A1','A2']
    assert all(a['result']=='pass' for a in r['verdict']['coverage']['assertions'])
    assert candidate()['status']=='draft'
    for sid in ('S3','S3b'):
        step=next(s for s in r['steps'] if s['step_id']==sid)
        assert step['outcome']=='passed',step
        assert step['attempts'][0]['dispatch']['sent'] is True
        sw=step['attempts'][0]['swipe']
        assert sw['container']['type']=='List' and sw['relative'] is True
        assert 240 < sw['from'][0] < 1000 and 240 < sw['to'][0] < 1000
    assert next(s for s in r['steps'] if s['step_id']=='S4')['outcome']=='passed'
    assert next(s for s in r['steps'] if s['step_id']=='S5')['outcome']=='passed'
    old=run_case(ws,'TC-HM-S01',device_spec=spec,env={'account_state':'logged_in'},propose=False)
    assert old['verdict']['verdict']!='passed'

import pytest
@pytest.mark.parametrize('variant',['missing','multiple','no_settings'])
def test_candidate_does_not_pass_without_required_scope_or_settings(ws,tmp_path,variant):
    from vpdtest.runner import run_case
    r=run_case(ws,case=candidate(),device_spec=synthetic_world(tmp_path,variant),env={'account_state':'logged_in'},propose=False)
    assert r['verdict']['verdict']!='passed'
    if variant in ('missing','multiple'):
        step=next(s for s in r['steps'] if s['step_id']=='S3')
        assert step['attempts'][0]['dispatch']['sent'] is False
