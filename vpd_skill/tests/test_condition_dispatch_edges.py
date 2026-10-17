from pathlib import Path

"""Review-only synthetic Executor edge probes; no production oracle is touched."""
from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world
from vpdtest.execute import Executor
from vpdtest.util import read_json, read_jsonl, write_json


def _edge(verdict_ws, tmp_path, *, after_obs=None, flip_after_clear=None):
    spec = _lab_world(tmp_path)
    data = read_json(spec.split(':', 1)[1])
    def screen(marker):
        return {'root': {'t':'Column', 'b':[0,0,1320,2848], 'children':[
            {'t':'Text','text':marker,'b':[10,20,500,80]},
            {'t':'TextInput','id':'field','text':'old','bind':'f','click':True,'b':[40,200,1280,320]},
            {'t':'Button','id':'target','text':'动作','click':True,'b':[40,400,1280,520]}]}}
    data['screens']={'home':screen('CONST'),'counter':screen('COUNTER'),'missing':screen('OTHER')}
    if after_obs:
        data['screens']['home']['after_obs']={'n':after_obs,'go':'counter','replace':True}
    write_json(spec.split(':',1)[1],data)
    ctx = _ctx(verdict_ws,spec)
    ctx.facts={'app':'motor_hm','app_version':'3.0.3.200-replay'}
    ctx.conditions={'cond':{'kind':'page','true':{'type':'text_present','text':'CONST'},'false':{'type':'text_present','text':'COUNTER'}}}
    if flip_after_clear:
        original=ctx.observe
        def observe(label='',screenshot=True):
            if label.endswith(':after-clear'):
                ctx.device.state['stack'][-1]=flip_after_clear
            return original(label,screenshot)
        ctx.observe=observe
    return Executor(ctx)


def _input(*, at=None):
    return {'id':'S2','action':'input','target':{'strategies':[{'kind':'id','value':'field'}], 'allow_input':True,'side_effect':'navigation'},'text':'new','clear':True,'retry':0,
            'when':{'condition':'cond','equals':True,**({'at':at} if at else {})}}


def test_current_condition_input_survives_own_clear(verdict_ws,tmp_path):
    ex=_edge(verdict_ws,tmp_path)
    result=ex.run_step(_input(),'synthetic')
    assert result['outcome']=='passed', result
    assert ex.ctx.device.bound['f']=='new'
    assert result['condition']['action_epoch']>0


def test_historical_input_condition_stays_stale_after_clear(verdict_ws,tmp_path):
    ex=_edge(verdict_ws,tmp_path)
    ex.run_step({'id':'S1','action':'wait','duration_s':0},'synthetic')
    result=ex.run_step(_input(at='S1'),'synthetic')
    assert result['outcome']=='pending',result
    assert ex.ctx.device.bound['f']==''
    assert result.get('effect_unknown') or result.get('action_required')


def test_revalidate_counterproof_prevents_same_epoch_tap(verdict_ws,tmp_path):
    ex=_edge(verdict_ws,tmp_path,after_obs=2)
    result=ex.run_step({'id':'S1','action':'tap','target':{'strategies':[{'kind':'id','value':'target'}],'side_effect':'navigation'},'revalidate':True,'retry':0,
                        'when':{'condition':'cond','equals':True}},'synthetic')
    assert result['outcome']=='skipped',result
    assert ex.ctx.actions==0
    assert result['condition']['status']=='false'


def test_current_input_counterproof_after_clear_does_not_type(verdict_ws,tmp_path):
    ex=_edge(verdict_ws,tmp_path,flip_after_clear='counter')
    result=ex.run_step(_input(),'synthetic')
    assert result['outcome']=='pending',result
    assert result['condition']['status']=='false',result
    assert ex.ctx.device.bound['f']==''
    assert result.get('effect_unknown') or result.get('action_required')


def test_current_input_unknown_after_clear_does_not_type(verdict_ws,tmp_path):
    ex=_edge(verdict_ws,tmp_path,flip_after_clear='missing')
    result=ex.run_step(_input(),'synthetic')
    assert result['outcome']=='pending',result
    assert result['condition']['status']=='unknown'
    assert ex.ctx.device.bound['f']==''
    assert result.get('effect_unknown') or result.get('action_required')


def test_explore_recipe_can_use_current_measured_condition(verdict_ws,tmp_path):
    from vpdtest.explore import explore
    spec=_lab_world(tmp_path)
    data=read_json(spec.split(':',1)[1])
    data['screens']['home']['root']['children'].append({'t':'Text','text':'INDEPENDENT','b':[40,100,500,150]})
    write_json(spec.split(':',1)[1],data)
    app_dir=verdict_ws.app('motor_hm').dir
    meta=read_json(app_dir/'app.json')
    meta['conditions']={'cond':{'kind':'page','true':{'type':'text_present','text':'INDEPENDENT'},'false':{'type':'text_present','text':'COUNTER'}}}
    write_json(app_dir/'app.json',meta)
    write_json(app_dir/'states.json',{'states':{'synthetic_home':{'require_text':['INDEPENDENT']},'synthetic_detail':{'require_text':['详情页']}},'interrupts':{},'recipes':{'synthetic_detail':[{'action':'tap','target':{'strategies':[{'kind':'text','value':'唯一入口'}],'side_effect':'navigation'},'when':{'condition':'cond','equals':True},'expect_state':'synthetic_detail'}]}})
    warm=_ctx(verdict_ws,spec)
    verdict_ws.device=lambda *args,**kwargs:warm.device
    out=explore(verdict_ws,'motor_hm','synthetic_detail',spec,budget=1)
    assert out['exit']['reason']=='frontier_exhausted',out
    assert out['actions']==1
    records=[e['condition'] for e in read_jsonl(Path(out['dir'])/'events.jsonl') if e['event']=='condition']
    assert records and all(r['status']=='true' for r in records)
    assert records[0]['scope']['app_version']=='3.0.3.200-replay'


def test_explore_recipe_false_condition_stays_unexecuted(verdict_ws,tmp_path):
    from vpdtest.explore import explore
    spec=_lab_world(tmp_path)
    data=read_json(spec.split(':',1)[1])
    data['screens']['home']['root']['children'].append({'t':'Text','text':'COUNTER','b':[40,100,500,150]})
    write_json(spec.split(':',1)[1],data)
    app_dir=verdict_ws.app('motor_hm').dir
    meta=read_json(app_dir/'app.json')
    meta['conditions']={'cond':{'kind':'page','true':{'type':'text_present','text':'INDEPENDENT'},'false':{'type':'text_present','text':'COUNTER'}}}
    write_json(app_dir/'app.json',meta)
    write_json(app_dir/'states.json',{'states':{'synthetic_home':{'require_text':['COUNTER']},'synthetic_detail':{'require_text':['详情页']}},'interrupts':{},'recipes':{'synthetic_detail':[{'action':'tap','target':{'strategies':[{'kind':'text','value':'唯一入口'}],'side_effect':'navigation'},'when':{'condition':'cond','equals':True},'expect_state':'synthetic_detail'}]}})
    warm=_ctx(verdict_ws,spec)
    verdict_ws.device=lambda *args,**kwargs:warm.device
    out=explore(verdict_ws,'motor_hm','synthetic_detail',spec,budget=1)
    assert out['exit']['reason']=='reset_failed',out
    assert out['actions']==0
    records=[e['condition'] for e in read_jsonl(Path(out['dir'])/'events.jsonl') if e['event']=='condition']
    assert records and records[0]['status']=='false'
    assert records[0]['scope']['app_version']=='3.0.3.200-replay'


def test_fact_specific_ttl_is_checked_at_actual_dispatch(verdict_ws, tmp_path):
    ex = _edge(verdict_ws, tmp_path)
    ex.ctx.conditions["cond"]["max_age_s"] = 1
    original = ex._locate
    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        ex.ctx.sleep(5)
        return result
    ex._locate = delayed
    result = ex.run_step({"id": "S1", "action": "tap", "target": {"strategies": [{"kind": "id", "value": "target"}],
                         "side_effect": "navigation"}, "retry": 0, "when": {"condition": "cond", "equals": True}}, "synthetic")
    assert result["outcome"] == "pending" and ex.ctx.actions == 0
    assert result["condition"]["status"] == "unknown"
    assert "有效期" in result["condition"]["reason"]
