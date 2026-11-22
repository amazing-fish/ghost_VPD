"""Published motor_hm regression approvals reuse portable bindings, never substitute for a new run."""
import hashlib
import json
from pathlib import Path
import pytest
from conftest import world
from vpdtest import cases as C
from vpdtest.runner import run_case
from vpdtest.util import write_json

ASSETS = [
 (
  'TC-HM-S01',
  '220e6c5e32a4',
  '12d98ffe6920',
  '3ee281779185bac6823537733e8e6c91ea323c11392f942bc26cf2699de8d3b2',
  '515054e86da9',
  '9c4436c1b914',
 ),
 (
  'TC-MOTOR-HM-002',
  'b4de659323c5',
  '25f15f39c405',
  'f37abb3da55f22137821c92514079a29449f8cf942854acc45d6a1fef24abd7f',
  'b4de659323c5',
  '19d7bdc10603',
 ),
]

@pytest.mark.parametrize('cid,content,deps,review_hash,historical_content,historical_deps', ASSETS)
def test_revalidated_assets_are_portable_without_local_run_originals(
    ws, cid, content, deps, review_hash, historical_content, historical_deps
):
    case = ws.load_case(cid)
    assert not list(ws.runs_dir.glob('run-*'))
    assert case['status'] == 'regression'
    assert C.content_hash(case) == content
    assert case['asset_review']['binding']['review_hash'] == review_hash
    info = C.review_status(ws, case)
    assert info['valid'] and info['state'] == 'approved', info
    assert info['current']['dependency_hash'] == deps
    assert case['asset_review']['binding']['dependency_hash'] == deps
    # Issue #282 / #234 updated motor_hm dialog handling and TC-HM-S01 same-session account guard;
    # no fabricated replay is added for the new hash, while the historical #236 independent-agent
    # review and its 3 real-device replays stay intact.
    assert C.replay_basis(case, deps)['replays'] == []
    if historical_content == content:
        basis = C.replay_basis(case, historical_deps)
        assert basis['ok'] and len(basis['replays']) == 3, basis
        assert all(r['validation_level'] == 'real_device' and r['verdict'] == 'passed' for r in basis['rows'])
        hist_replays = basis['replays']
    else:
        assert C.replay_basis(case, historical_deps)['replays'] == []
        hist_rows = [r for r in case.get('replays', []) if r.get('hash') == historical_content and r.get('deps') == historical_deps]
        assert len(hist_rows) == 3
        assert all(r['validation_level'] == 'real_device' and r['verdict'] == 'passed' for r in hist_rows)
        hist_replays = [r['run_id'] for r in hist_rows]
    historical = next(
        h for h in reversed(case['history'])
        if h.get('event') == 'asset_review'
        and h.get('binding', {}).get('dependency_hash') == historical_deps
    )
    assert historical['evidence_run'] in hist_replays
    assert C.preflight_batch(ws, [cid])[0]['valid']
    meta = json.dumps({k: case[k] for k in C.REVIEW_METADATA if k in case})
    assert '192.168.' not in meta and 'D:' not in meta
    doc, marker = historical['independent_ref'].split('#original-review-sha256=')
    assert len(marker) == 64
    root = Path(__file__).resolve().parents[2]
    assert (root / doc).is_file()
    original = (root / doc).with_suffix('.original.txt')
    assert hashlib.sha256(original.read_text(encoding="utf-8").encode("utf-8")).hexdigest() == marker.lower()

@pytest.mark.parametrize('cid,content,deps,review_hash,historical_content,historical_deps', ASSETS)
def test_new_execution_does_not_inherit_historical_pass(
    ws, cid, content, deps, review_hash, historical_content, historical_deps
):
    case = ws.load_case(cid)
    original = ws.case_path(cid).read_bytes()
    assert C.review_status(ws, case)['valid']
    run = run_case(ws, cid, device_spec=world('motor_hm_popup.json'), propose=False, require_asset_review=True)
    assert run['asset_review']['valid']
    assert run['run_id'] not in {r['run_id'] for r in case['replays']}
    assert run['validation_level'] == 'offline_replay'
    assert run['steps']
    assert run['verdict']['verdict'] != 'passed'
    assert ws.case_path(cid).read_bytes() == original


@pytest.mark.parametrize('cid,content,deps,review_hash,historical_content,historical_deps', ASSETS)
def test_stale_binding_still_obeys_existing_admission_contract(
    ws, cid, content, deps, review_hash, historical_content, historical_deps
):
    # Deliberate stale binding only in copied test data, never production assets.
    case = ws.load_case(cid)
    case['asset_review']['binding']['dependency_hash'] = 'synthetic-previous-dependency'
    write_json(ws.case_path(cid), case)
    case = ws.load_case(cid)
    original = ws.case_path(cid).read_bytes()
    info = C.review_status(ws, case)
    assert not info['valid'] and info['state'] == 'stale'
    if info['required']:
        with pytest.raises(PermissionError, match='审核失效'):
            C.preflight_batch(ws, [cid])
        with pytest.raises(PermissionError, match='审核失效'):
            run_case(ws, cid, device_spec=world('motor_hm_popup.json'), propose=False, require_asset_review=True)
        assert not list(ws.runs_dir.glob('run-*'))
    else:
        # Existing contract: ordinary non-exploration cases do not require approval.
        # This is not valid approved-asset reuse; explicitly expose the stale result.
        assert not C.preflight_batch(ws, [cid])[0]['valid']
        run = run_case(ws, cid, device_spec=world('motor_hm_popup.json'), propose=False, require_asset_review=True)
        assert not run['asset_review']['valid']
        assert run['asset_review']['state'] == 'stale'
        assert run['run_id'] not in {r['run_id'] for r in case['replays']}
        assert run['validation_level'] == 'offline_replay'
        assert run['steps']
        assert run['verdict']['verdict'] != 'passed'
    assert ws.case_path(cid).read_bytes() == original
