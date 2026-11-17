"""Conservative per-review context scope, with historic approvals left intact."""
import pytest
from vpdtest import cases as C
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import read_json, write_json
from test_asset_review import _asset, _approve, _review


def _template(ws, name='unreferenced.png'):
    path = ws.root / 'apps/motor_hm/templates' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'first synthetic image')
    return path


def test_unreferenced_file_no_longer_stales_new_review_and_replay(ws):
    case = _asset(ws)
    approved = _approve(ws)
    old = C.review_status(ws, approved)
    before = C.replay_basis(approved, old['current']['dependency_hash'])
    _template(ws)
    after = _review(ws)
    assert approved['asset_review']['binding']['context_schema'] == 2
    assert after['state'] == 'approved' and after['current'] == old['current']
    assert C.replay_basis(approved, after['current']['dependency_hash']) == before


def test_legacy_empty_manifest_proven_by_hash_needs_no_resign(ws):
    _asset(ws)
    approved = _approve(ws)
    k = KnowledgeStore(ws.root)
    approved['asset_review']['binding'] = C._review_binding(ws, approved, ws.app(approved['app']), k, ws.policy(), context_schema=1)
    write_json(ws.case_path(approved['id']), approved)
    assert _review(ws)['valid']
    _template(ws)
    result = _review(ws)
    assert result['state'] == 'approved'
    assert result['current'] == approved['asset_review']['binding']
    assert 'context_schema' not in result['current']


def test_legacy_nonempty_manifest_is_not_reconstructed_or_auto_resigned(ws):
    _asset(ws); template = _template(ws); approved = _approve(ws)
    kn=KnowledgeStore(ws.root)
    approved['asset_review']['binding'] = C._review_binding(ws, approved, ws.app(approved['app']), kn, ws.policy(), context_schema=1)
    write_json(ws.case_path(approved['id']), approved)
    assert _review(ws)['valid']
    template.write_bytes(b'changed synthetic image')
    assert _review(ws)['state'] == 'stale'


@pytest.mark.parametrize('where', ['direct', 'interrupt', 'unused_target', 'dynamic'])
def test_declared_or_dynamic_template_preserves_full_manifest(ws, where):
    case = _asset(ws)
    p = ws.root / 'apps/motor_hm/targets.json'
    targets = read_json(p)
    if where == 'direct':
        targets['targets']['tab_my']['strategies'].append({'kind':'template','file':'templates/synthetic.png'})
    elif where == 'interrupt':
        sp = ws.root / 'apps/motor_hm/states.json'
        states = read_json(sp)
        targets['targets']['synthetic_interrupt'] = {'strategies':[{'kind':'template','file':'templates/synthetic.png'}]}
        states['interrupts']['synthetic_interrupt'] = {'dismiss_target':'synthetic_interrupt'}
        write_json(sp, states)
    elif where == 'unused_target':
        targets['targets']['synthetic_unused'] = {'strategies':[{'kind':'template','file':'templates/synthetic.png'}]}
    else:
        case['test_data']['synthetic'] = '${dynamic}'
        write_json(ws.case_path(case['id']), case)
    write_json(p, targets)
    # Build the real binding without claiming the synthetic definitions pass lint.
    app=ws.app(case['app']); kn=KnowledgeStore(ws.root); pol=ws.policy()
    before=C._review_binding(ws,case,app,kn,pol)
    _template(ws)
    after=C._review_binding(ws,case,app,kn,pol)
    assert before['context_hash'] != after['context_hash']


def test_policy_and_system_dialog_remain_bound(ws):
    _asset(ws); _approve(ws)
    p=ws.root/'system_dialogs.json'; data=read_json(p); data['synthetic']={'changed':True}; write_json(p,data)
    assert _review(ws)['state']=='stale'


@pytest.mark.parametrize('schema', [True, 2.0, '2', 3, None])
def test_malformed_or_unknown_schema_never_approves(ws, schema):
    _asset(ws); approved=_approve(ws)
    approved['asset_review']['binding']['context_schema']=schema
    write_json(ws.case_path(approved['id']),approved)
    assert not _review(ws)['valid']


def test_metadata_does_not_change_new_review_scope(ws):
    _asset(ws); approved=_approve(ws)
    _template(ws)
    approved['history'].append({'note': '${synthetic history is not a runtime reference}'})
    approved['replays']=[{'run_id':'synthetic', 'note':'${not a strategy}'}]
    write_json(ws.case_path(approved['id']), approved)
    assert _review(ws)['valid']
