"""Run-only scalar binding, with synthetic names unrelated to field fixtures."""
from copy import deepcopy
import pytest
from vpdtest import runtime_data
from vpdtest.cases import content_hash, execution_structure_issues
from vpdtest.params import Params
from vpdtest.runner import run_case
VALUES = {"fav_spot": "Q-test spot", "fav_waypoint": "R-test point", "fav_spot_number": "X42", "fav_waypoint_number": "Y99"}

def test_missing_fixture_is_rejected_before_any_device(ws, monkeypatch):
    monkeypatch.setattr(ws, 'device', lambda *a, **k: pytest.fail('device must not be created'))
    with pytest.raises(ValueError, match='运行参数'):
        run_case(ws, 'TC-QK-230')

def test_data_is_run_only_and_does_not_change_case_hash(ws):
    case = ws.load_case('TC-QK-230'); original = deepcopy(case)
    values = runtime_data.prepare(case, VALUES)
    assert execution_structure_issues(case, ws.app(case['app'])) == []
    params = Params(case['test_data'], {}, 'run-synthetic'); params.vars.update(values)
    assert params.subst(case['steps'][5]['text']) == VALUES['fav_spot']
    assertions = {a['id']: runtime_data.bind_assertion(a, values) for a in case['assertions']}
    assert assertions['A1-query']['expected'] == VALUES['fav_spot']
    assert assertions['A1']['text'] == VALUES['fav_spot_number']
    assert assertions['A1']['within'] == 'qk_search_name_result_row'
    assert case == original and content_hash(case) == content_hash(original)

@pytest.mark.parametrize('bad', ['', '  ', None, 7, {}, '${var:other}', '${read:R1}', 'a\nb', '\x7f', '\u200b', 'a'*513])
def test_invalid_values_rejected(ws, bad):
    with pytest.raises(ValueError): runtime_data.prepare(ws.load_case('TC-QK-230'), {**VALUES, 'fav_spot': bad})

@pytest.mark.parametrize('key', ['target', 'expect_state', 'when', 'side_effect', 'text_suffix'])
def test_cannot_use_runtime_data_to_change_step_semantics(ws, key):
    c=ws.load_case('TC-QK-230'); c['steps'][5][key]='${var:fav_spot}'
    assert runtime_data.structure_issues(c, ws.app(c['app']))

def test_cannot_bind_runtime_data_in_target_strategy(ws):
    c=ws.load_case('TC-QK-230'); app=ws.app(c['app'])
    app.targets['qk_search_input']['strategies'][0]['value']='${var:fav_spot}'
    assert runtime_data.structure_issues(c, app)

@pytest.mark.parametrize('change', [{'unknown':'x'}, {'fav_spot':'x'}])
def test_unknown_or_partial_values_are_rejected(ws, change):
    with pytest.raises(ValueError): runtime_data.prepare(ws.load_case('TC-QK-230'), change)

def test_static_data_cannot_be_overridden(ws):
    with pytest.raises(ValueError): runtime_data.prepare({'test_data': {'name':'fixed'}}, {'name':'other'})

def test_existing_non_runtime_cases_require_no_new_data():
    assert runtime_data.prepare({'test_data': {'name':'fixed'}}) == {}

def test_nonexact_text_match_is_not_a_runtime_sink(ws):
    c=ws.load_case('TC-QK-230'); c['assertions'][2]['exact']=False
    assert runtime_data.structure_issues(c, ws.app(c['app']))

from test_condition_runner import condition_rig, COMMON_RULE
from test_read_references import _rig, _dispatch
from vpdtest.util import read_json, write_json

@pytest.mark.parametrize('confirmed', [True, False])
def test_real_runner_records_runtime_inputs_without_claiming_measurement(condition_rig, confirmed):
    ws, case, spec = _rig(condition_rig, value='synthetic chosen name')
    case['test_data'] = {'name': {'runtime': True}}
    case['steps'] = [case['steps'][0], {**case['steps'][2], 'text': '${var:name}'}]
    case['assertions'][0].update(text='${var:name}', exact=True)
    if not confirmed:
        path=ws.root / 'knowledge' / 'items.json'; data=read_json(path)
        next(k for k in data['items'] if k['id']==COMMON_RULE)['status']='unconfirmed';write_json(path,data)
    run=run_case(ws, case=case, device_spec=spec, propose=False, runtime_values={'name':'synthetic chosen name'})
    assert len(_dispatch(run)) == 1
    assert run['runtime_data'] == {'values':{'name':'synthetic chosen name'}, 'source':'run_arguments', 'measured':False}
    assert run['test_data']['name'] == 'synthetic chosen name'
    assert run['assertions'][0]['result'] == ('pass' if confirmed else 'pending')
    assert run['case']['hash'] == content_hash(case)


def test_runtime_input_cannot_make_wrong_result_pass(condition_rig):
    ws, case, spec = _rig(condition_rig, value='synthetic chosen name', result='different object')
    case['test_data'] = {'name': {'runtime': True}}
    case['steps'] = [case['steps'][0], {**case['steps'][2], 'text': '${var:name}'}]
    case['assertions'][0].update(text='${var:name}', exact=True)
    run=run_case(ws, case=case, device_spec=spec, propose=False, runtime_values={'name':'synthetic chosen name'})
    assert run['assertions'][0]['result'] == 'fail'

def test_direct_params_cannot_input_a_declaration_or_placeholder():
    params=Params({'name':{'runtime':True}}, {}, 'synthetic')
    with pytest.raises(ValueError, match='尚未设置'):
        params.subst('${var:name}')


def test_cli_duplicate_runtime_values_fail_before_run(ws, monkeypatch):
    from vpdtest.cli import build_parser
    from vpdtest import commands
    args=build_parser().parse_args(['run','TC-QK-230','--data','fav_spot=a','--data','fav_spot=b'])
    monkeypatch.setattr(commands, 'run_case', lambda *a, **k: pytest.fail('no run'))
    with pytest.raises(ValueError, match='重复'):
        commands.cmd_run(ws,args)


def test_truthy_nonboolean_runtime_declaration_is_rejected(ws):
    case=ws.load_case('TC-QK-230'); case['test_data']['fav_spot']={'runtime':1}
    assert runtime_data.structure_issues(case, ws.app(case['app']))


def test_replay_snapshot_retains_runtime_values_and_rejects_same_run_drift(condition_rig):
    from vpdtest.cases import record_replay
    ws, case, spec = _rig(condition_rig, value='synthetic chosen name')
    case['test_data']={'name':{'runtime':True}}
    case['steps']=[case['steps'][0],{**case['steps'][2],'text':'${var:name}'}]
    case['assertions'][0].update(text='${var:name}',exact=True)
    write_json(ws.case_path(case['id']),case)
    run=run_case(ws,case=case,device_spec=spec,propose=False,runtime_values={'name':'synthetic chosen name'})
    saved=record_replay(ws,case['id'],run)
    assert saved['replays'][-1]['runtime_data']==run['runtime_data']
    assert len(record_replay(ws,case['id'],run)['replays'])==1
    changed=deepcopy(run);changed['runtime_data']['values']['name']='another name'
    with pytest.raises(ValueError,match='runtime_data'):
        record_replay(ws,case['id'],changed)
    changed['run_id']+='-other'
    assert len(record_replay(ws,case['id'],changed)['replays'])==2
    absent=deepcopy(run);absent.pop('runtime_data')
    with pytest.raises(ValueError,match='摘要缺失'):
        record_replay(ws,case['id'],absent)
