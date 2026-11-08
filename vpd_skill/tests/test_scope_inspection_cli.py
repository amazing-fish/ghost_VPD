"""Entry-point boundaries: saved evidence only, no local secrets or state writes."""
import hashlib
import json

import pytest

from vpdtest import cli, commands, scope_inspection, workspace
from vpdtest.observe import RunStore
from vpdtest.util import write_json


def _spec():
    return {'template_id': 'TC-QK-240', 'candidate_target': {
        'side_effect': 'read_only', 'forbid_tap': True,
        'strategies': [{'kind': 'id', 'value': 'synthetic-name-scope'}]}}


def _input(tmp_path):
    path = tmp_path / 'scope-input.json'
    write_json(path, _spec())
    evidence = tmp_path / 'saved-evidence'
    evidence.mkdir()
    return path, evidence


def _args(ws, path, evidence):
    return ['--workspace', str(ws.root), 'case', 'inspect-scope', '--input', str(path),
            '--evidence-dir', str(evidence), '--obs-id', 'obs-0001']


def _snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def _forbid(*args, **kwargs):
    pytest.fail('offline scope inspection touched configuration, device or a write path')


def _forbid_side_effects(monkeypatch):
    monkeypatch.setattr(workspace, 'load_config', _forbid)
    monkeypatch.setattr(workspace.Workspace, 'device', _forbid)
    monkeypatch.setattr(RunStore, '__init__', _forbid)
    monkeypatch.setattr(commands.case_mod, 'approve', _forbid)
    monkeypatch.setattr(commands.case_mod, 'revoke_review', _forbid)
    monkeypatch.setattr(commands.case_mod, 'promote', _forbid)
    monkeypatch.setattr(commands.KnowledgeStore, 'confirm', _forbid)


@pytest.mark.parametrize('status,errors,expected', [
    ('inspected', [], 0), ('incomplete', [], 0),
    ('conflict', [{'code': 'identity_conflict', 'field': 'source', 'message': 'synthetic conflict'}], 3),
])
def test_cli_routes_only_local_evidence_and_reports_inspection_errors(ws, tmp_path, monkeypatch, capsys,
                                                                    status, errors, expected):
    path, evidence = _input(tmp_path)
    before = _snapshot(tmp_path)
    _forbid_side_effects(monkeypatch)
    calls = []
    def inspect(app, spec, directory, obs_id):
        calls.append((app.id, spec, directory, obs_id))
        return {'mode': 'offline_diagnostic', 'diagnostic_status': status, 'inspection_errors': errors,
                'production_calibration': 'not_granted', 'current_applicability': 'not_checked',
                'semantic_role': 'requires_human_review'}
    monkeypatch.setattr(scope_inspection, 'inspect_saved_scope', inspect)
    assert cli.main(_args(ws, path, evidence)) == expected
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'offline_diagnostic'
    assert calls == [('qiankun_hm', _spec(), str(evidence), 'obs-0001')]
    assert _snapshot(tmp_path) == before
    assert not ws.runs_dir.exists()


def test_real_missing_bundle_is_a_read_only_diagnostic_not_a_product_pass(ws, tmp_path, monkeypatch, capsys):
    path, evidence = _input(tmp_path)
    before = _snapshot(tmp_path)
    _forbid_side_effects(monkeypatch)
    assert cli.main(_args(ws, path, evidence)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'offline_diagnostic'
    assert result['diagnostic_status'] == 'incomplete'
    assert result['issues']
    assert result['production_calibration'] == 'not_granted'
    assert result['current_applicability'] == 'not_checked'
    assert 'verdict' not in result
    assert _snapshot(tmp_path) == before
    assert not ws.runs_dir.exists()


def test_invalid_candidate_cannot_claim_calibration_or_expected_truth(ws, tmp_path, monkeypatch, capsys):
    path, evidence = _input(tmp_path)
    spec = _spec()
    spec['candidate_target']['status'] = 'calibrated_on_device'
    spec['expected_name'] = 'not-an-independent-expected-input'
    write_json(path, spec)
    before = _snapshot(tmp_path)
    _forbid_side_effects(monkeypatch)
    assert cli.main(_args(ws, path, evidence)) == 3
    result = json.loads(capsys.readouterr().out)
    assert result['error'] == 'ValueError'
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize('flag', ['--device', '--write', '--authorize', '--reviewer'])
def test_new_command_has_no_live_device_or_governance_flags(flag):
    args = ['case', 'inspect-scope', '--input', 'scope.json', '--evidence-dir', 'evidence',
            '--obs-id', 'obs-0001', flag]
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(args)
    assert exc.value.code == 3


def test_workspace_config_opt_out_is_local_to_explicit_call(tmp_path, monkeypatch):
    calls = []
    def config():
        calls.append(True)
        return {'device': 'configured-test-device'}
    monkeypatch.setattr(workspace, 'load_config', config)
    normal = workspace.Workspace(str(tmp_path))
    diagnostic = workspace.Workspace(str(tmp_path), load_local_config=False)
    assert normal.config == {'device': 'configured-test-device'}
    assert diagnostic.config == {}
    assert calls == [True]
    assert normal.root == diagnostic.root and normal.runs_dir == diagnostic.runs_dir


def test_existing_cli_commands_keep_configuration_loading(ws, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(workspace, 'load_config', lambda: calls.append(True) or {'device': 'configured-test-device'})
    monkeypatch.setitem(cli.HANDLERS, 'doctor', lambda current, args: {'config': current.config})
    assert cli.main(['--workspace', str(ws.root), 'doctor']) == 0
    assert json.loads(capsys.readouterr().out)['config'] == {'device': 'configured-test-device'}
    assert calls == [True]


def test_cli_rejects_duplicate_candidate_keys_before_inspecting_evidence(ws, tmp_path, monkeypatch, capsys):
    path, evidence = _input(tmp_path)
    text = json.dumps(_spec()).replace('"forbid_tap": true', '"forbid_tap": false, "forbid_tap": true')
    path.write_text(text, encoding='utf-8')
    before = _snapshot(tmp_path)
    _forbid_side_effects(monkeypatch)
    monkeypatch.setattr(scope_inspection, 'inspect_saved_scope', _forbid)
    assert cli.main(_args(ws, path, evidence)) == 3
    result = json.loads(capsys.readouterr().out)
    assert result['error'] == 'ValueError'
    assert _snapshot(tmp_path) == before
