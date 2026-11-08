"""CLI preview, exclusive creation and review contracts; synthetic scope registration."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from vpdtest import cases
from vpdtest.cli import build_parser
from vpdtest.commands import cmd_case
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import read_json, write_json


def _snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def _prepare(ws, tmp_path):
    targets_path = ws.root / 'apps/qiankun_hm/targets.json'
    data = read_json(targets_path)
    data['targets']['template_test_name'] = {
        'label': 'Synthetic dedicated name container', 'side_effect': 'read_only',
        'forbid_tap': True, 'status': 'synthetic',
        'strategies': [{'kind': 'id', 'value': 'synthetic-name-container'}],
        'template_scope': {'role': 'parking_name', 'shape': 'text_container',
                           'calibration': {'status': 'verified', 'source': 'synthetic_fixture',
                                           'ref': 'fixture/scope.json', 'validation_level': 'synthetic'}}}
    write_json(targets_path, data)
    spec = {'template_id': 'TC-QK-240', 'new_case_id': 'TC-QK-240-TEMPLATE-TEST',
            'scope_target': 'template_test_name',
            'scope_source': {'source': 'synthetic_fixture', 'ref': 'fixture/scope.json'},
            'expected_source': {'source': 'test_data', 'ref': 'fixture/independent-expectation.json'},
            'expected_name': '合成园区'}
    path = tmp_path / 'input.json'
    write_json(path, spec)
    return path, spec


def _command(ws, path, *flags):
    args = build_parser().parse_args(['case', 'instantiate', '--input', str(path), *flags])
    return cmd_case(ws, args)


def test_preview_has_no_file_lock_or_device_side_effects(ws, tmp_path, monkeypatch):
    path, spec = _prepare(ws, tmp_path)
    before = _snapshot(ws.root)
    monkeypatch.setattr(ws, 'device', lambda *a, **k: pytest.fail('preview touched a device'))
    result = _command(ws, path)
    assert result['mode'] == 'preview'
    assert result['case']['status'] == 'draft' and result['case']['id'] == spec['new_case_id']
    assert result['issues'] == []
    assert result['asset_review']['required'] and result['asset_review']['state'] == 'pending'
    assert _snapshot(ws.root) == before
    assert not ws.runs_dir.exists()


def test_write_only_creates_the_frozen_draft_and_does_not_approve(ws, tmp_path, monkeypatch):
    path, spec = _prepare(ws, tmp_path)
    before = _snapshot(ws.root)
    monkeypatch.setattr(ws, 'device', lambda *a, **k: pytest.fail('creation touched a device'))
    preview = _command(ws, path)
    result = _command(ws, path, '--write')
    assert result['mode'] == 'created'
    saved = ws.load_case(spec['new_case_id'])
    assert saved == preview['case'] == result['case']
    assert not any(key in saved for key in ('asset_review', 'replays', 'product_status'))
    after = _snapshot(ws.root)
    assert set(after) - set(before) == {f"cases/{spec['new_case_id']}.json"}
    assert all(after[key] == value for key, value in before.items())
    assert result['hash'] == preview['hash'] and result['deps'] == preview['deps']
    with pytest.raises(PermissionError, match='尚无有效人工审核'):
        cases.promote(ws, spec['new_case_id'], 'candidate', KnowledgeStore(ws.root), dry_run=True)


def test_write_refuses_existing_case_without_overwriting(ws, tmp_path):
    path, spec = _prepare(ws, tmp_path)
    sentinel = {'id': spec['new_case_id'], 'untouched': 'existing-user-data'}
    write_json(ws.case_path(spec['new_case_id']), sentinel)
    before = _snapshot(ws.root)
    with pytest.raises(FileExistsError):
        _command(ws, path, '--write')
    assert _snapshot(ws.root) == before


def test_concurrent_creation_has_one_winner_and_no_partial_overwrite(ws, tmp_path):
    path, spec = _prepare(ws, tmp_path)
    def create():
        try:
            return _command(ws, path, '--write')['mode']
        except FileExistsError:
            return 'exists'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: create(), range(2))) == ['created', 'exists']
    assert ws.load_case(spec['new_case_id']) == _command(ws, path)['case']


def test_lint_failure_preview_is_read_only_and_write_refuses(ws, tmp_path):
    path, spec = _prepare(ws, tmp_path)
    knowledge_path = ws.root / 'knowledge/items.json'
    data = read_json(knowledge_path)
    data['items'] = [item for item in data['items'] if item['id'] != 'K-QK-TPL-240']
    write_json(knowledge_path, data)
    before = _snapshot(ws.root)
    preview = _command(ws, path)
    assert preview['issues'] and any('不存在' in issue['problem'] for issue in preview['issues'])
    with pytest.raises(ValueError, match='lint 未通过'):
        _command(ws, path, '--write')
    assert _snapshot(ws.root) == before


def test_all_new_rule_proposals_remain_unconfirmed_and_app_scoped(ws):
    knowledge = KnowledgeStore(ws.root)
    for suffix in ('240', '241', '243'):
        proposal = knowledge.get('K-QK-TPL-' + suffix)
        assert proposal['status'] == 'unconfirmed'
        assert proposal['applies_to'] == {'app': 'qiankun_hm'}
        assert not any(key.startswith('confirmed_') for key in proposal)
        old = knowledge.get('K-QK-IDX-' + suffix)
        assert old['status'] == 'confirmed'
        assert old['applies_to']['app_version'] == '1.0.26.100'


def test_template_origin_and_source_require_review_even_after_relabelling(ws, tmp_path):
    path, spec = _prepare(ws, tmp_path)
    case = _command(ws, path)['case']
    case['origin'] = {'type': 'manual'}
    info = cases.review_status(ws, case)
    assert info['required'] and info['state'] == 'pending'
    assert 'source:template' in info['origin_signals']
