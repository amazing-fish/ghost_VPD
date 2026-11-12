"""Issue #193: an interrupted comparison cannot retain old adoption eligibility."""
import copy
import json
import traceback
from pathlib import Path

import pytest

from verdict_fixtures import make_verdict_workspace, NAV_CASE
from vpdtest import cli, experience, workspace
from vpdtest.experience import ExperienceStore
from vpdtest.locate import locator_fingerprint
from vpdtest.util import SKILL_ROOT, write_json

XID = 'XP-interruption'
CASES = ['origin', 'independent']
DEVICE = 'synthetic'
TARGET = {'strategies': [{'kind': 'text', 'value': 'a'}, {'kind': 'text', 'value': 'b'}]}


def seed(root, status='candidate'):
    store = ExperienceStore(root, 'motor_hm')
    write_json(store.targets_path, {'targets': {'tab_my': TARGET}})
    store.save_item({'id': XID, 'kind': 'locator_preference',
                     'change': {'target': 'tab_my', 'prefer_strategy_index': 1, 'locator_fingerprint': locator_fingerprint(TARGET)},
                     'status': 'candidate', 'requires_review': False, 'origin_case': 'origin',
                     'evidence': [], 'validations': [], 'history': []})
    if status != 'candidate':
        assert store.validate(XID, CASES, [DEVICE], good)['status'] == 'validated'
    if status == 'adopted':
        store.adopt(XID)
    return store


def good(cid, dev, overlay):
    return {'run_id': f'{cid}-{bool(overlay.trial)}', 'verdict': {'verdict': 'passed'},
            'steps': [{'attempts': [{'locate': {'target': 'tab_my', 'adapted': not overlay.trial, 'locator_fingerprint': locator_fingerprint(TARGET), 'overlay_pref': 1 if overlay.trial else None}}]}]}


@pytest.mark.parametrize('status', ['candidate', 'validated', 'adopted'])
@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize('fail_at', [1, 2, 3, 4])
def test_interruption_preserves_completed_pairs_and_prior_history(tmp_path, status, error_type, fail_at):
    store = seed(tmp_path / 'data', status)
    before, active = copy.deepcopy(store.get(XID)), store.active()
    calls = []
    error = error_type('SECRET must not be persisted')

    def fail(cid, dev, overlay):
        calls.append((cid, dev))
        if len(calls) == fail_at:
            raise error
        run = good(cid, dev, overlay)
        if len(calls) == 2:
            run['verdict']['verdict'] = 'failed'
        return run

    with pytest.raises(error_type) as caught:
        store.validate(XID, CASES, [DEVICE], fail)
    assert caught.value is error
    assert traceback.extract_tb(error.__traceback__)[-1].name == 'fail'
    saved = store.get(XID)
    result = saved['validations'][-1]
    assert saved['validations'][:-1] == before['validations']
    assert saved['history'][:len(before['history'])] == before['history']
    assert saved['status'] == ('rejected' if fail_at >= 3 else 'candidate')
    assert len(result['rows']) == (1 if fail_at >= 3 else 0)
    assert result['completed'] is False
    assert result['interruption'] == {'case': CASES[(fail_at - 1) // 2], 'device': DEVICE,
                                      'phase': 'trial' if fail_at % 2 == 0 else 'baseline',
                                      'error_type': error_type.__name__}
    plan = [{'case': c, 'device': DEVICE, 'phase': p} for c in CASES for p in ('baseline', 'trial')]
    assert result['not_run'] == plan[fail_at:]
    assert 'SECRET' not in json.dumps(saved)
    assert store.active() == active
    if status == 'adopted':
        assert store.overlay().locator_pref('tab_my', target=TARGET) == 1
    with pytest.raises(PermissionError):
        store.adopt(XID)
    assert store.active() == active
    assert store.validate(XID, CASES, [DEVICE], good)['status'] == 'validated'


@pytest.mark.parametrize('error_type', [ValueError, KeyboardInterrupt])
def test_comparison_failure_excludes_unfinished_pair(tmp_path, monkeypatch, error_type):
    store = seed(tmp_path / 'data', 'validated')
    error = error_type('SECRET')
    def fail(*args):
        raise error
    monkeypatch.setattr(experience, '_comparable_coverage', fail)
    with pytest.raises(error_type) as caught:
        store.validate(XID, CASES, [DEVICE], good)
    assert caught.value is error
    result = store.get(XID)['validations'][-1]
    assert result['interruption']['phase'] == 'comparison'
    assert result['rows'] == [] and result['status'] == 'candidate'
    assert result['not_run'] == [{'case': 'independent', 'device': DEVICE, 'phase': p}
                                 for p in ('baseline', 'trial')]


@pytest.mark.parametrize('failure', [OSError, KeyboardInterrupt])
def test_failed_preinvalidation_never_invokes_runner(tmp_path, monkeypatch, failure):
    store = seed(tmp_path / 'data', 'validated')
    before = store.get(XID)
    error = failure('write failed')
    def save(item):
        raise error
    monkeypatch.setattr(store, 'save_item', save)
    def forbidden(*args):
        pytest.fail('runner invoked before eligibility invalidation was durable')
    with pytest.raises(failure) as caught:
        store.validate(XID, CASES, [DEVICE], forbidden)
    assert caught.value is error and store.get(XID) == before


@pytest.mark.parametrize('failure', [OSError, KeyboardInterrupt])
@pytest.mark.parametrize('target', ['save_item', 'log'])
def test_failure_recording_never_masks_original(tmp_path, monkeypatch, failure, target):
    store = seed(tmp_path / 'data', 'validated')
    original = KeyboardInterrupt('first')
    actual = getattr(store, target)
    calls = []
    def broken(*args, **kwargs):
        calls.append(True)
        if target == 'save_item' and len(calls) == 1:
            return actual(*args, **kwargs)
        raise failure('secondary')
    monkeypatch.setattr(store, target, broken)
    def fail(*args):
        assert store.get(XID)['status'] == 'candidate'
        raise original
    with pytest.raises(KeyboardInterrupt) as caught:
        store.validate(XID, CASES, [DEVICE], fail)
    assert caught.value is original
    assert traceback.extract_tb(original.__traceback__)[-1].name == 'fail'
    assert store.get(XID)['status'] == 'candidate'
    with pytest.raises(PermissionError):
        store.adopt(XID)


def test_candidate_and_adopted_do_not_add_preexecution_write(tmp_path, monkeypatch):
    for status in ('candidate', 'adopted'):
        store = seed(tmp_path / status / 'data', status)
        actual = store.save_item
        calls = []
        def save(item):
            calls.append(True)
            actual(item)
        monkeypatch.setattr(store, 'save_item', save)
        def run(*args):
            assert calls == []
            return good(*args)
        assert store.validate(XID, CASES, [DEVICE], run)['status'] == 'validated'
        assert len(calls) == 1


@pytest.mark.parametrize('kind', ['valid', 'extra', 'missing', 'outside', 'mismatch', 'malicious'])
def test_only_verified_safe_diagnostic_reference_is_saved(tmp_path, kind):
    root = tmp_path / 'data'
    store = seed(root)
    error = RuntimeError('SECRET')
    directory = tmp_path / ('outside' if kind == 'outside' else 'runs') / 'run-synthetic'
    record = {'schema_version': 1, 'run_id': directory.name, 'dir': str(directory),
              'state': 'tool_error', 'error_type': 'RuntimeError', 'persisted': True}
    if kind == 'extra':
        record['secret'] = 'SECRET'
    if kind == 'mismatch':
        record['error_type'] = 'ValueError'
    if kind != 'missing':
        write_json(directory / 'run_diagnostic.json', record)
    error.run_diagnostic = record if kind != 'malicious' else {'secret': 'SECRET'}
    def fail(*args):
        raise error
    with pytest.raises(RuntimeError):
        store.validate(XID, CASES, [DEVICE], fail)
    result = store.get(XID)['validations'][-1]
    assert 'SECRET' not in json.dumps(result)
    assert ('run_diagnostic' in result['interruption']) == (kind in ('valid', 'extra'))
    if kind in ('valid', 'extra'):
        assert result['interruption']['run_diagnostic'] == {'run_id': directory.name,
                                                           'path': str(directory / 'run_diagnostic.json')}


@pytest.mark.parametrize('interrupt', [False, True])
def test_real_cli_invalidation_and_exit_contract(tmp_path, monkeypatch, capsys, interrupt):
    monkeypatch.setattr(workspace, 'load_config', lambda: {})
    ws = make_verdict_workspace(tmp_path, SKILL_ROOT)
    store = seed(ws.root, 'validated')
    monkeypatch.setattr(cli, 'Workspace', lambda *args: ws)
    world = tmp_path / 'malformed-world.json'
    world.write_text('{synthetic malformed replay', encoding='utf-8')
    if interrupt:
        def fail(*args, **kwargs):
            raise KeyboardInterrupt('synthetic')
        monkeypatch.setattr(workspace, 'create_device', fail)
    code = cli.main(['--workspace', str(ws.root), 'experience', 'validate', XID,
                     '--app', 'motor_hm', '--cases', NAV_CASE, '--device', 'replay:' + str(world)])
    output = json.loads(capsys.readouterr().out)
    assert code == (130 if interrupt else 3), output
    result = store.get(XID)['validations'][-1]
    assert result['status'] == 'candidate' and result['completed'] is False
    assert result['interruption']['phase'] == 'baseline'
    diagnostic = output['run_diagnostic']
    assert result['interruption']['run_diagnostic'] == {
        'run_id': diagnostic['run_id'],
        'path': str(Path(diagnostic['dir']) / 'run_diagnostic.json')}
    assert Path(diagnostic['dir']).parent == ws.runs_dir
    assert result['interruption']['error_type'] == ('KeyboardInterrupt' if interrupt else 'JSONDecodeError')
    assert cli.main(['--workspace', str(ws.root), 'experience', 'adopt', XID, '--app', 'motor_hm']) == 3
    assert store.active()['items'] == []


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_prior_improvement_does_not_promote_unfinished_validation(tmp_path, error_type):
    store = seed(tmp_path / 'data', 'validated')
    calls = []
    error = error_type('synthetic')
    def run(cid, dev, overlay):
        calls.append(True)
        if len(calls) == 3:
            raise error
        return good(cid, dev, overlay)
    # The first pair is independently improving but the requested plan is not complete.
    with pytest.raises(error_type):
        store.validate(XID, ['independent', 'origin'], [DEVICE], run)
    result = store.get(XID)['validations'][-1]
    assert len(result['rows']) == 1 and result['status'] == 'candidate'
    assert result['exercised_cases'] == ['independent']


def test_prior_regression_survives_later_comparison_failure(tmp_path, monkeypatch):
    store = seed(tmp_path / 'data', 'validated')
    actual = experience._comparable_coverage
    comparisons = []
    error = ValueError('synthetic')
    def compare(*args):
        comparisons.append(True)
        if len(comparisons) == 2:
            raise error
        return actual(*args)
    monkeypatch.setattr(experience, '_comparable_coverage', compare)
    def run(cid, dev, overlay):
        result = good(cid, dev, overlay)
        if cid == 'origin' and overlay.trial:
            result['verdict']['verdict'] = 'failed'
        return result
    with pytest.raises(ValueError) as caught:
        store.validate(XID, CASES, [DEVICE], run)
    result = store.get(XID)['validations'][-1]
    assert caught.value is error
    assert result['status'] == 'rejected' and len(result['rows']) == 1
    assert result['rows'][0]['regression'] is True
    assert result['interruption']['phase'] == 'comparison' and result['not_run'] == []


def test_unreadable_diagnostic_attribute_does_not_drop_validation(tmp_path):
    class Failure(RuntimeError):
        @property
        def run_diagnostic(self):
            raise KeyboardInterrupt('secondary')
    store = seed(tmp_path / 'data')
    error = Failure('first')
    def fail(*args):
        raise error
    with pytest.raises(Failure) as caught:
        store.validate(XID, CASES, [DEVICE], fail)
    assert caught.value is error
    result = store.get(XID)['validations'][-1]
    assert result['status'] == 'candidate' and 'run_diagnostic' not in result['interruption']
