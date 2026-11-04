"""E1 only: action-bound frame recording, gaps, scope, dispatch and verdict contracts."""
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_console import fake_jpeg
from verdict_fixtures import NAV_CASE, SETTINGS_RULE

from vpdtest import transient
from vpdtest.cases import execution_structure_issues
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.observe import RunStore
from vpdtest.runner import run_case
from vpdtest.session import step_options
from vpdtest.tree import UiTree
from vpdtest.util import read_json, read_jsonl


class Source:
    def __init__(self, hub, initial=True):
        self.hub, self.initial, self.stopped = hub, initial, False

    def start(self):
        if self.initial:
            self.hub.push(fake_jpeg(body=b"baseline"))

    def stop(self):
        self.stopped = True


def capture(tmp_path, *, foreground="test.app", initial=True):
    ctx = SimpleNamespace(device=SimpleNamespace(foreground=lambda: foreground),
                          app=SimpleNamespace(bundle="test.app"), actions=0,
                          policy=SimpleNamespace(screenshot_allowed=lambda p:p=="test.app"),
                          store=RunStore(tmp_path/'runs'))
    sources = []
    def factory(device, hub):
        source = Source(hub, initial);sources.append(source);return source
    cap = transient.TransientCapture(ctx, {"id":"SUBMIT","capture_transient":{"post_s":0.05,"ready_timeout_s":0.05}}, 1,
                                     source_factory=factory)
    return cap, ctx, sources


def test_preserves_trigger_baseline_and_ephemeral_frame_with_hashes(tmp_path):
    cap,ctx,sources=capture(tmp_path)
    assert cap.start()
    cap.mark_trigger();cap.receipt(DeviceResult('tap',True,True));ctx.actions=1
    toast=fake_jpeg(body=b"short toast not present in later tree")
    sources[0].hub.push(toast)
    out=cap.finish()
    assert out['status']=='captured' and sources[0].stopped
    assert [f['phase'] for f in out['frames']]==['baseline','after_trigger']
    last=out['frames'][-1]
    assert (ctx.store.dir/last['path']).read_bytes()==toast
    assert hashlib.sha256(toast).hexdigest()==last['sha256']
    assert out['trigger']['sent'] is True and not out['negative_evidence']
    assert read_json(ctx.store.dir/out['manifest'])==out


@pytest.mark.parametrize('fg',[None,'other.app'])
def test_unknown_or_other_foreground_never_starts_or_persists_frames(tmp_path,fg):
    cap,ctx,sources=capture(tmp_path,foreground=fg)
    assert not cap.start() and sources==[]
    out=cap.finish()
    assert out['status']=='withheld' and not out['frames']
    assert not list(ctx.store.dir.rglob('*.jpeg'))


@pytest.mark.parametrize('fg',[None,'other.app'])
def test_end_foreground_change_withholds_entire_stream(tmp_path,fg):
    cap,ctx,sources=capture(tmp_path);assert cap.start();cap.mark_trigger()
    sources[0].hub.push(fake_jpeg(body=b'toast'))
    ctx.device.foreground=lambda:fg
    out=cap.finish()
    assert out['status']=='withheld' and not out['frames'] and sources[0].stopped
    assert not list(ctx.store.dir.rglob('*.jpeg'))


def test_no_first_frame_blocks_trigger_readiness(tmp_path):
    cap,ctx,sources=capture(tmp_path,initial=False)
    assert not cap.start()
    out=cap.finish()
    assert out['status']=='unavailable' and not out['trigger'] and sources[0].stopped


def test_known_source_failure_after_baseline_cannot_arm_trigger(tmp_path):
    cap,ctx,sources=capture(tmp_path);assert cap.start()
    sources[0].hub.set_status('error','stream failed after warmup')
    assert cap.mark_trigger() is False and cap.trigger is None
    assert cap.finish()['status']=='unavailable'


def test_actual_dispatch_rechecks_source_after_first_frame(verdict_ws,monkeypatch):
    start=Source.start
    def failed(source):
        start(source)
        source.hub.set_status('error','stream stopped before physical tap')
    monkeypatch.setattr(Source,'start',failed)
    run,_=run_capture(verdict_ws,monkeypatch)
    step=run['steps'][2]
    assert step['outcome']=='blocked'
    assert step['attempts'][0]['transient']['trigger'] is None
    events=read_jsonl(Path(run['dir'])/'events.jsonl')
    attempts=[e for e in events if e['event']=='dispatch' and e.get('step')=='S3']
    assert attempts and all(e['sent'] is False for e in attempts)


def test_no_post_frame_is_gap_not_absence(tmp_path):
    cap,ctx,_=capture(tmp_path);assert cap.start();cap.mark_trigger();cap.receipt(DeviceResult('tap',True,True))
    out=cap.finish()
    assert out['status']=='gap' and out['negative_evidence'] is False
    assert out['largest_observed_gap_s']>=0.05


def test_frame_bound_reports_drops_without_claiming_complete_coverage(tmp_path,monkeypatch):
    monkeypatch.setattr(transient,'MAX_FRAMES',2)
    cap,ctx,sources=capture(tmp_path);assert cap.start();cap.mark_trigger()
    for i in range(4):sources[0].hub.push(fake_jpeg(body=str(i).encode()))
    out=cap.finish()
    assert len(out['frames'])==2 and out['dropped_frames']==3 and not out['negative_evidence']


def test_invalid_frame_and_source_stop_failure_are_retained(tmp_path):
    cap,ctx,sources=capture(tmp_path);assert cap.start();cap.mark_trigger()
    sources[0].hub.push(b'not a jpeg')
    sources[0].stop=lambda:(_ for _ in ()).throw(RuntimeError('capture stop failed'))
    out=cap.finish()
    assert {p['kind'] for p in out['problems']}=={'invalid_frame','source_stop_error'}
    assert out['status']=='gap'


@pytest.mark.parametrize('value',[None,[],3,{'post_s':0},{'post_s':float('nan')},{'post_s':True},{'post_s':11},{'unknown':1}])
def test_bad_options_rejected(value):
    assert transient.options_error({'action':'tap','capture_transient':value})


def seen_fixture(text='toast'):
    children=[{'attributes':{'type':'Text','text':str(i),'bounds':f'[0,{i*10}][99,{i*10+9}]'}} for i in range(70)]
    children.append({'attributes':{'type':'Text','text':text,'bounds':'[0,710][99,730]'}})
    tree=UiTree({'attributes':{'type':'Column','bounds':'[0,0][100,1000]'},'children':children})
    obs={'obs_id':'obs-0008','fresh':True,'foreground':'test.app','action_epoch':1,'tree':{'status':'ok'},'texts':tree.visible_texts()[:60]}
    cap={'capture_id':'capture-test','status':'captured','post_s':1,'trigger':{'sent':True,'action_epoch_before':0},
         'observations':[{'obs':'obs-0008','t':0.1,'action_epoch':1}],'frames':[]}
    step={'attempts':[{'transient':cap}]}
    return tree,obs,cap,step


def test_positive_complete_tree_beyond_first_sixty_is_seen():
    tree,obs,cap,step=seen_fixture()
    assert 'toast' not in obs['texts']
    holds,ev=transient.seen({'pattern':'toast'},step,lambda _:(obs,tree),'test.app')
    assert holds is True and ev['matches'][0]['obs']=='obs-0008'
    assert transient.seen({'pattern':'toast','expected':False},step,lambda _:(obs,tree),'test.app')[0] is False


@pytest.mark.parametrize('change',[{'fresh':False},{'foreground':'other.app'},{'action_epoch':2},{'tree':{'status':'error'}}])
def test_stale_wrong_scope_or_later_action_cannot_supply_seen(change):
    tree,obs,cap,step=seen_fixture();obs.update(change)
    assert transient.seen({'pattern':'toast'},step,lambda _:(obs,tree),'test.app')[0] is None


@pytest.mark.parametrize('expected',[True,False])
def test_missing_toast_is_unknown_even_with_frames_and_complete_normal_tree(expected):
    tree,obs,cap,step=seen_fixture('different')
    holds,ev=transient.seen({'pattern':'toast','expected':expected},step,lambda _:(obs,tree),'test.app')
    assert holds is None and not ev['negative_evidence']


def test_min_count_requires_distinct_positive_observations():
    tree,obs,cap,step=seen_fixture()
    cap['observations'].append(dict(cap['observations'][0]))
    assert transient.seen({'pattern':'toast','min_count':2},step,lambda _:(obs,tree),'test.app')[0] is None


@pytest.mark.parametrize('stamp',[-0.1,2])
def test_outside_trigger_window_cannot_supply_seen(stamp):
    tree,obs,cap,step=seen_fixture();cap['observations'][0]['t']=stamp
    assert transient.seen({'pattern':'toast'},step,lambda _:(obs,tree),'test.app')[0] is None


def test_cli_request_options_survive_session_conversion():
    assert step_options('tap',{'capture_transient':{'post_s':3}})=={'capture_transient':{'post_s':3}}


def install_frames(monkeypatch, *, initial=True):
    active={}
    def factory(dev,hub):
        source=Source(hub,initial);active['source']=source;return source
    monkeypatch.setattr(transient,'make_source',factory)
    original=ReplayDevice.tap
    def tap(dev,*args):
        result=original(dev,*args)
        if active.get('source'):active['source'].hub.push(fake_jpeg(body=b'ephemeral synthetic frame'))
        return result
    monkeypatch.setattr(ReplayDevice,'tap',tap)
    return active


def run_capture(verdict_ws,monkeypatch,*,initial=True,pattern='never-in-tree',confirmed=False):
    active=install_frames(monkeypatch,initial=initial)
    case=copy.deepcopy(verdict_ws.load_case(NAV_CASE));case['id']='TC-TEST-CAPTURE'
    case['steps'][2]['capture_transient']={'post_s':0.2,'ready_timeout_s':0.05}
    case['steps'][2]['retry']=2
    case['assertions']=[{'id':'AT','at':'S3','type':'transient_text_seen','pattern':pattern,'oracle_refs':[SETTINGS_RULE]}]
    if confirmed:KnowledgeStore(verdict_ws.root).confirm(SETTINGS_RULE,reviewer='pytest-synthetic')
    result=run_case(verdict_ws,case=case,device_spec='replay:motor_hm_base.json',propose=False)
    return result,active


def test_actual_runner_captures_visual_only_toast_without_inventing_product_pass(verdict_ws,monkeypatch):
    run,active=run_capture(verdict_ws,monkeypatch)
    step=run['steps'][2];cap=step['attempts'][0]['transient']
    assert step['outcome'].startswith('passed') and len(step['attempts'])==1
    assert cap['status']=='captured' and active['source'].stopped
    assert run['assertions'][0]['result']=='undeterminable' and run['verdict']['verdict']=='pending'
    report=(Path(run['dir'])/'report.md').read_text(encoding='utf-8')
    assert '短暂提示帧证据' in report and cap['manifest'] in report
    assert run['validation_level']=='offline_replay'


def test_actual_runner_no_source_frame_never_dispatches_submit(verdict_ws,monkeypatch):
    run,_=run_capture(verdict_ws,monkeypatch,initial=False)
    step=run['steps'][2]
    assert step['outcome']=='blocked' and len(step['attempts'])==1
    assert step['attempts'][0]['transient']['trigger'] is None
    events=read_jsonl(Path(run['dir'])/'events.jsonl')
    assert not [e for e in events if e['event']=='dispatch' and e.get('step')=='S3' and e.get('sent')]


def test_transient_uses_existing_oracle_gate(verdict_ws,monkeypatch):
    run,_=run_capture(verdict_ws,monkeypatch,pattern='设置')
    assert run['assertions'][0]['result']=='pending'


def test_confirmed_synthetic_oracle_and_positive_tree_can_pass(verdict_ws,monkeypatch):
    run,_=run_capture(verdict_ws,monkeypatch,pattern='设置',confirmed=True)
    assert run['assertions'][0]['result']=='pass'


def test_explicit_capture_fault_is_tool_error_not_product_absence():
    tree,obs,cap,step=seen_fixture('different')
    cap['problems']=[{'kind':'error','detail':'transport lost'}]
    holds,ev=transient.seen({'pattern':'toast'},step,lambda _:(obs,tree),'test.app')
    assert holds is None and ev['execution_error']['attribution']=='tool'


def test_evidence_write_error_keeps_dispatch_and_never_retries(verdict_ws,monkeypatch):
    finish=transient.TransientCapture.finish
    def broken(cap):
        finish(cap)
        raise OSError('synthetic evidence write failure')
    monkeypatch.setattr(transient.TransientCapture,'finish',broken)
    run,_=run_capture(verdict_ws,monkeypatch)
    step=run['steps'][2]
    assert step['outcome']=='error' and len(step['attempts'])==1
    assert step['attempts'][0]['dispatch']['sent'] is True
    assert step['attempts'][0]['transient']['status']=='error'


def test_postcondition_failure_does_not_resubmit_captured_action(verdict_ws,monkeypatch):
    install_frames(monkeypatch)
    case=copy.deepcopy(verdict_ws.load_case(NAV_CASE))
    case['steps'][2].update(capture_transient={'post_s':0.05},expect_state='car_tab',timeout_s=0.01,retry=2)
    run=run_case(verdict_ws,case=case,device_spec='replay:motor_hm_base.json',propose=False)
    step=run['steps'][2]
    assert len(step['attempts'])==1 and step['attempts'][0]['dispatch']['sent'] is True
    events=read_jsonl(Path(run['dir'])/'events.jsonl')
    assert len([e for e in events if e['event']=='dispatch' and e.get('step')=='S3' and e.get('op')=='tap' and e.get('sent')])==1


def test_actual_cli_capture_request_and_session_evidence_are_retained(verdict_ws,monkeypatch):
    from test_goal_loop import cli
    install_frames(monkeypatch)
    base=['--app','motor_hm','--device','replay:motor_hm_base.json','--session','transient-cli']
    cli(verdict_ws,'act','launch',*base)
    cli(verdict_ws,'act','tap','--target','tab_my',*base)
    out=cli(verdict_ws,'act','tap','--target','settings_entry','--capture-transient','0.05',*base)
    assert out['result']['attempts'][0]['transient']['status']=='captured'
    events=read_jsonl(verdict_ws.runs_dir/'session-transient-cli'/'events.jsonl')
    request=[e for e in events if e['event']=='act_request'][-1]['request']
    assert request['capture_transient']=={'post_s':0.05}


def test_capture_definition_rejects_motion_before_device_creation(verdict_ws,monkeypatch):
    app=verdict_ws.app('motor_hm');app.targets['settings_entry']['side_effect']='vehicle_motion'
    case=verdict_ws.load_case(NAV_CASE);case['steps'][2]['capture_transient']={'post_s':1}
    issues=execution_structure_issues(case,app)
    assert any('短提示' in x['problem'] for x in issues)
