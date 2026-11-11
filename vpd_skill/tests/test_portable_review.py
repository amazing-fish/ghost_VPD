"""Portable approval reuse: no physical devices, only isolated replay fixtures."""
import pytest
from conftest import world
from test_asset_review import _asset, _review
from test_review_routing import evidence, approve_agent
from test_goal_loop import cli
from vpdtest import cases as C
from vpdtest.runner import run_case
from vpdtest.experience import ExperienceStore
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import write_json


@pytest.mark.parametrize('missing', ['run', 'tree', 'screen'])
def test_missing_old_raw_does_not_revoke_or_block_new_replay(ws, tmp_path, missing):
    case = _asset(ws)
    _, path = evidence(ws, case)
    approve_agent(ws, case)
    original = ws.case_path(case['id']).read_bytes()
    if missing == 'run':
        ws.runs_dir = tmp_path / 'new-machine-runs'
    else:
        (path.parent / 'obs/obs-0001' / ('tree.json' if missing == 'tree' else 'screen.png')).unlink()
    info = _review(ws)
    assert info['valid'] and info['state'] == 'approved'
    assert info['evidence_verification']['status'] == 'not_checked'
    assert info['evidence_verification']['affects_review_validity'] is False
    assert C.preflight_batch(ws, [case['id']])[0]['valid']
    for batch in (False, True):
        run = run_case(ws, case['id'], device_spec=world('motor_hm_base.json'),
                       propose=False, require_asset_review=batch)
        assert run['asset_review']['valid']
        assert run['validation_level'] == 'offline_replay'
        assert run['run_id'] != 'run-fixture-review' and run['steps']
    assert ws.case_path(case['id']).read_bytes() == original


def test_suite_and_experience_callbacks_reach_new_replay_without_old_raw(ws, tmp_path, monkeypatch):
    case = _asset(ws)
    evidence(ws, case)
    approve_agent(ws, case)
    ws.runs_dir = tmp_path / 'other-host-runs'
    write_json(ws.root / 'suites/portable.json', {'id': 'portable', 'cases': [case['id']]})
    result = cli(ws, 'suite', 'portable', '--device', world('motor_hm_base.json'))
    assert len(result['results']) == 1 and result['results'][0]['asset_review']['valid']
    seen = []
    def validate(self, xid, cases, device, callback):
        run = callback(cases[0], device[0], None)  # Real callback retains require_asset_review=True.
        seen.append(run)
        return {'fixture': True}
    monkeypatch.setattr(ExperienceStore, 'validate', validate)
    cli(ws, 'experience', '--app', 'motor_hm', 'validate', 'fixture-xp',
        '--device', world('motor_hm_base.json'), '--cases', case['id'])
    assert len(seen) == 1 and seen[0]['asset_review']['valid'] and seen[0]['steps']


def test_existing_binding_and_rationale_can_promote_without_local_originals(ws, tmp_path):
    case = _asset(ws)
    case['knowledge_used'][0]['rationale'] = 'session-fixture obs-0001'
    (ws.runs_dir / 'session-fixture/obs/obs-0001').mkdir(parents=True)
    write_json(ws.case_path(case['id']), case)
    evidence(ws, case)
    approve_agent(ws, case)
    ws.runs_dir = tmp_path / 'different-host'
    assert _review(ws)['valid']
    from test_asset_readiness import _query
    row = _query(ws, case['id'])['results'][0]
    assert row['issues'] == [] and row['review']['valid']
    result = C.promote(ws, case['id'], 'candidate', KnowledgeStore(ws.root))
    assert result['status'] == 'candidate'


@pytest.mark.parametrize('change', ['content', 'dependency', 'policy', 'context', 'revoke', 'risk', 'reference'])
def test_real_binding_and_static_changes_still_block(ws, tmp_path, change):
    case = _asset(ws)
    evidence(ws, case)
    approve_agent(ws, case)
    ws.runs_dir = tmp_path / 'missing-raw'
    case = ws.load_case(case['id'])
    if change == 'content': case['steps'][1]['expect_state'] = 'settings'
    elif change in ('dependency', 'policy', 'context'):
        case['asset_review']['binding'][change + '_hash'] = 'changed'
    elif change == 'risk': case['authorizations'] = ['vehicle_motion']
    elif change == 'reference': case['asset_review']['independent_ref'] = ''
    else:
        C.revoke_review(ws, case['id'], 'fixture', 'withdrawn')
        case = ws.load_case(case['id'])
    write_json(ws.case_path(case['id']), case)
    with pytest.raises(PermissionError): C.require_review(ws, case)


def test_new_run_result_is_not_copied_from_portable_approval(ws, tmp_path):
    case = _asset(ws)
    old, _ = evidence(ws, case)
    approve_agent(ws, case)
    ws.runs_dir = tmp_path / 'new-evidence'
    run = run_case(ws, case['id'], device_spec=world('motor_hm_popup.json'),
                   require_asset_review=True, propose=False)
    assert old['verdict']['verdict'] == 'passed'
    assert run['asset_review']['valid']
    assert run['verdict']['verdict'] == 'blocked'
    assert run['validation_level'] == 'offline_replay'
    assert run['run_id'] != old['run_id']


def test_reuse_does_not_read_raw_even_when_files_exist(ws, monkeypatch):
    from pathlib import Path
    case = _asset(ws)
    evidence(ws, case)
    approve_agent(ws, case)
    original = Path.read_bytes
    def no_raw(path):
        if path.is_relative_to(ws.runs_dir):
            pytest.fail('portable review must not read raw evidence')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', no_raw)
    assert _review(ws)['valid']


def test_actual_target_change_still_invalidates_portable_binding_without_raw(ws, tmp_path):
    from vpdtest.util import read_json
    case = _asset(ws)
    evidence(ws, case)
    approve_agent(ws, case)
    ws.runs_dir = tmp_path / 'other-host'
    assert _review(ws)['valid']
    path = ws.root / 'apps/motor_hm/targets.json'
    targets = read_json(path)
    targets['targets']['tab_my']['desc'] = 'changed independent target definition'
    write_json(path, targets)
    info = _review(ws)
    assert info['state'] == 'stale' and not info['valid']
    assert info['record']['binding']['dependency_hash'] != info['current']['dependency_hash']
    with pytest.raises(PermissionError): C.preflight_batch(ws, [case['id']])
