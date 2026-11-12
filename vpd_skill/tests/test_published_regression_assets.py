"""Real published assets, fresh workspace, no private original evidence."""
import json
from pathlib import Path
import pytest
from conftest import world
from vpdtest import cases as C
from vpdtest.runner import run_case

ASSETS = [
 ('TC-HM-S01', '515054e86da9', 'ceda4e9f6e57', '61d354464b8e4b24c568224e60ba9db5be020a29eb251f18ea035d8610480e51'),
 ('TC-MOTOR-HM-002', 'b4de659323c5', '23a0aa57d7bb', 'f37abb3da55f22137821c92514079a29449f8cf942854acc45d6a1fef24abd7f'),
]

@pytest.mark.parametrize('cid,content,deps,review_hash', ASSETS)
def test_published_asset_is_complete_and_portable(ws, cid, content, deps, review_hash):
    case = ws.load_case(cid)
    assert not list(ws.runs_dir.glob('run-*'))
    assert case['status'] == 'regression'
    assert C.content_hash(case) == content
    assert case['asset_review']['binding']['review_hash'] == review_hash
    info = C.review_status(ws, case)
    assert info['valid'], info
    assert info['evidence_verification']['status'] == 'not_checked'
    assert C.preflight_batch(ws, [cid])[0]['valid']
    basis = C.replay_basis(case, deps)
    assert basis['ok'] and len(basis['replays']) == 2, basis
    assert len({r['device'] for r in basis['rows']}) == 1
    meta = json.dumps({k: case[k] for k in C.REVIEW_METADATA if k in case})
    assert '192.168.' not in meta and 'D:' not in meta
    doc, marker = case['asset_review']['independent_ref'].split('#original-review-sha256=')
    assert len(marker) == 64
    assert (Path(__file__).resolve().parents[2] / doc).is_file()

@pytest.mark.parametrize('cid,content,deps,review_hash', ASSETS)
def test_new_execution_does_not_inherit_historical_pass(ws, cid, content, deps, review_hash):
    case = ws.load_case(cid)
    original = ws.case_path(cid).read_bytes()
    run = run_case(ws, cid, device_spec=world('motor_hm_popup.json'), propose=False, require_asset_review=True)
    assert run['asset_review']['valid']
    assert run['run_id'] not in {r['run_id'] for r in case['replays']}
    assert run['validation_level'] == 'offline_replay'
    assert run['steps']
    assert run['verdict']['verdict'] != 'passed'
    assert ws.case_path(cid).read_bytes() == original
