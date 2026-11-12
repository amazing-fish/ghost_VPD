"""Completed synthetic Replay evidence survives optional proposal failure."""
import copy
import errno
import traceback
from pathlib import Path

import pytest

from verdict_fixtures import make_verdict_workspace, NAV_CASE, NAV_RULE, SETTINGS_RULE
from vpdtest import runner, experience, cli, workspace
from vpdtest.cases import record_replay, replay_basis, has_execution_error
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import SKILL_ROOT, read_json, read_jsonl, write_json


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'load_config', lambda: {})
    ws = make_verdict_workspace(tmp_path, SKILL_ROOT)
    # This fault-injection suite needs a candidate-producing adaptation, not a
    # production locator defect. Keep the ambiguous strategy local to this rig.
    targets_path = ws.app('motor_hm').dir / 'targets.json'
    targets = read_json(targets_path)
    targets['targets']['tab_my']['strategies'].insert(0, {
        'kind': 'text', 'value': '我的', 'source': 'synthetic proposal-failure trigger'})
    write_json(targets_path, targets)
    data = read_json(ws.root / 'knowledge/items.json')
    for item in data['items']:
        if item['id'] in (NAV_RULE, SETTINGS_RULE):
            item['applies_to']['app_version'] = ['3.0.3.200', '3.0.4.100']
    write_json(ws.root / 'knowledge/items.json', data)
    for kid in (NAV_RULE, SETTINGS_RULE):
        KnowledgeStore(ws.root).confirm(kid, reviewer='synthetic-test')
    return ws, 'replay:' + str(SKILL_ROOT / 'fixtures/worlds/motor_hm_drift.json')


def fault(rig, monkeypatch, kind):
    xs = experience.ExperienceStore(rig[0].root, 'motor_hm')
    if kind in ('corrupt', 'disabled'):
        xs.dir.mkdir(parents=True, exist_ok=True)
        xs.items_path.write_text('{synthetic malformed candidates', encoding='utf-8')
    elif kind == 'candidate':
        original = experience.write_json
        def write(path, data, **kw):
            if Path(path) == xs.items_path:
                raise PermissionError(errno.EACCES, 'synthetic candidate-only fault')
            return original(path, data, **kw)
        monkeypatch.setattr(experience, 'write_json', write)
    elif kind == 'ledger':
        def append(path, data):
            assert Path(path) == xs.ledger_path
            raise OSError(errno.ENOSPC, 'synthetic ledger-only fault')
        monkeypatch.setattr(experience, 'append_jsonl', append)
    return xs


def capture(monkeypatch):
    before, errors = [], []
    original = experience.ExperienceStore.propose_from_run
    def propose(self, run):
        before.append(copy.deepcopy(run))
        try:
            return original(self, run)
        except (Exception, KeyboardInterrupt) as error:
            errors.append(error)
            raise
    monkeypatch.setattr(experience.ExperienceStore, 'propose_from_run', propose)
    return before, errors


@pytest.mark.parametrize('product', ['passed', 'failed'])
@pytest.mark.parametrize('kind', ['corrupt', 'candidate', 'ledger'])
def test_cli_preserves_core_and_error_qualification(rig, monkeypatch, product, kind):
    ws, spec = rig
    if product == 'failed':
        case = ws.load_case(NAV_CASE)
        case['assertions'][0]['expected'] = False
        write_json(ws.case_path(NAV_CASE), case)
    xs = fault(rig, monkeypatch, kind)
    before, errors = capture(monkeypatch)
    assert cli.main(['--workspace', str(ws.root), 'run', NAV_CASE, '--device', spec, '--record-replay']) == 3
    assert len(before) == len(errors) == 1
    d = Path(errors[0].run_diagnostic['dir'])
    run = read_json(d / 'run.json')
    assert run['verdict']['product_verdict'] == product
    for key in ('steps', 'cleanup', 'assertions', 'started', 'knowledge_snapshot', 'facts', 'ui_alerts'):
        assert run[key] == before[0][key]
    for key in ('coverage', 'failure_signature', 'first_anomaly', 'product_failed', 'alerts', 'not_verified'):
        assert run['verdict'][key] == before[0]['verdict'][key]
    assert len(run['steps']) == 4 and len(list((d / 'obs').iterdir())) == 17
    assert run['verdict']['verdict'] == 'error'
    assert run['verdict']['execution']['status'] == 'error'
    assert run['verdict']['execution']['errors'][-1]['stage'] == 'experience'
    assert 'experience_candidates' not in run  # Unknown, not an asserted empty/complete list.
    report = (d / 'report.md').read_text(encoding='utf-8')
    assert '经验候选生成未完成' in report and '可能部分写入' in report
    assert 'synthetic candidate-only fault' not in report
    assert errors[0].run_diagnostic['phase'] == 'experience'
    assert not any(e['event'] == 'run_end' for e in read_jsonl(d / 'events.jsonl'))
    assert not ws.load_case(NAV_CASE).get('replays')
    if kind == 'ledger':
        assert read_json(xs.items_path)['items']
    # Explicit manual recording is allowed, but cannot establish qualification.
    record_replay(ws, NAV_CASE, run)
    case = record_replay(ws, NAV_CASE, {**run, 'run_id': run['run_id'] + '-second'})
    assert all(has_execution_error(row) for row in case['replays'])
    assert not replay_basis(case, run['case']['deps'])['ok']


@pytest.mark.parametrize('secondary', [None, 'json', 'render', 'report'])
@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_original_error_and_independent_saves(rig, monkeypatch, secondary, error_type):
    ws, spec = rig
    original = error_type('do not duplicate this payload')
    snapshots = []
    def fail(self, run):
        snapshots.append(copy.deepcopy(run))
        # Delayed finalization crosses a clock boundary without changing observation time.
        monkeypatch.setattr(runner, 'now_iso', lambda: '2099-01-01T00:00:00+00:00')
        raise original
    monkeypatch.setattr(experience.ExperienceStore, 'propose_from_run', fail)
    write = runner.write_json
    def fail_json(path, data, **kw):
        if Path(path).name == 'run.json':
            raise OSError('secondary JSON')
        return write(path, data, **kw)
    if secondary == 'json':
        monkeypatch.setattr(runner, 'write_json', fail_json)
    if secondary == 'render':
        def fail_render(run):
            raise KeyboardInterrupt('secondary rendering')
        monkeypatch.setattr(runner, 'render_run', fail_render)
    if secondary == 'report':
        original_write = Path.write_text
        def fail_report(self, *args, **kw):
            if self.name == 'report.md':
                raise OSError('secondary report')
            return original_write(self, *args, **kw)
        monkeypatch.setattr(Path, 'write_text', fail_report)
    with pytest.raises(error_type) as caught:
        runner.run_case(ws, NAV_CASE, device_spec=spec)
    assert caught.value is original
    assert 'fail' in [f.name for f in traceback.extract_tb(original.__traceback__)]
    d = Path(original.run_diagnostic['dir'])
    assert original.run_diagnostic['phase'] == 'experience'
    assert (d / 'run.json').exists() == (secondary != 'json')
    assert (d / 'report.md').exists() == (secondary not in ('render', 'report'))
    if secondary != 'json':
        run = read_json(d / 'run.json')
        assert run['finished'] == '2099-01-01T00:00:00+00:00' > snapshots[0]['finished']
        assert run['started'] == snapshots[0]['started']
        assert run['steps'] == snapshots[0]['steps']
        assert 'do not duplicate this payload' not in str(run)


def test_cli_cancellation_is_130(rig, monkeypatch):
    def fail(self, run):
        raise KeyboardInterrupt()
    monkeypatch.setattr(experience.ExperienceStore, 'propose_from_run', fail)
    ws, spec = rig
    assert cli.main(['--workspace', str(ws.root), 'run', NAV_CASE, '--device', spec]) == 130
    assert (next(ws.runs_dir.iterdir()) / 'run.json').exists()


@pytest.mark.parametrize('kind', ['healthy', 'disabled'])
def test_controls_keep_candidate_contract(rig, monkeypatch, kind):
    xs = fault(rig, monkeypatch, kind)
    ws, spec = rig
    before, errors = capture(monkeypatch)
    first = runner.run_case(ws, NAV_CASE, device_spec=spec, propose=kind != 'disabled')
    second = runner.run_case(ws, NAV_CASE, device_spec=spec, propose=kind != 'disabled')
    assert first['verdict']['verdict'] == second['verdict']['verdict'] == 'passed'
    assert not errors
    assert second['experience_candidates'] == []
    if kind == 'disabled':
        assert first['experience_candidates'] == [] and not before
        assert xs.items_path.read_text(encoding='utf-8').startswith('{synthetic')
    else:
        assert first['experience_candidates']
        items = read_json(xs.items_path)['items']
        assert [item['id'] for item in items] == first['experience_candidates']
        assert all(len(item['evidence']) == 2 for item in items)


def test_suite_does_not_continue_after_proposal(rig, monkeypatch):
    from vpdtest import commands
    ws, spec = rig
    monkeypatch.setattr(workspace.Workspace, 'load_suite', lambda self, name: {'cases': [NAV_CASE, NAV_CASE]})
    monkeypatch.setattr(commands.case_mod, 'preflight_batch', lambda *args: None)
    monkeypatch.setattr(runner, 'require_review', runner.review_status)
    fault(rig, monkeypatch, 'ledger')
    before, errors = capture(monkeypatch)
    assert cli.main(['--workspace', str(ws.root), 'suite', 'synthetic', '--device', spec]) == 3
    assert len(before) == len(errors) == 1
    suite = next(ws.runs_dir.glob('suite-*'))
    events = read_jsonl(suite / 'events.jsonl')
    assert sum(e['event'] == 'case_start' for e in events) == 1
    assert not any(e['event'] in ('case_done', 'suite_end') for e in events)
    assert (Path(errors[0].run_diagnostic['dir']) / 'run.json').exists()


@pytest.mark.parametrize('mode', ['active', 'trial'])
def test_overlay_loading_still_fails_before_execution(rig, monkeypatch, mode):
    ws, spec = rig
    xs = fault(rig, monkeypatch, 'corrupt')
    write_json(xs.active_path, {'version': 1, 'items': ['missing']} if mode == 'active'
               else {'version': 0, 'items': []})
    before, errors = capture(monkeypatch)
    with pytest.raises(ValueError):
        if mode == 'trial':
            runner.run_case(ws, NAV_CASE, device_spec=spec, overlay=xs.overlay(trial='missing'))
        else:
            runner.run_case(ws, NAV_CASE, device_spec=spec)
    assert not before and not errors
    assert not list(ws.runs_dir.glob('*/run.json'))


def test_preserves_existing_errors_and_alerts(rig, monkeypatch):
    ws, spec = rig
    original = runner.case_verdict
    preserved = {}
    def verdict(*args, **kwargs):
        result = original(*args, **kwargs)
        error = {'kind': 'step', 'result': 'error', 'attribution': 'tool', 'stage': 'synthetic_existing'}
        alert = {'kind': 'synthetic_existing', 'action_required': 'keep this safety signal'}
        result['execution']['errors'].append(error)
        result['findings'].append(error)
        result['alerts'].append(alert)
        result['action_required'] = alert['action_required']
        result['first_anomaly'] = {'step': 'S1', 'attempt': 1, 'status': 'error', 'reason': 'keep first anomaly'}
        preserved.update(copy.deepcopy(result))
        return result
    monkeypatch.setattr(runner, 'case_verdict', verdict)
    fault(rig, monkeypatch, 'ledger')
    with pytest.raises(OSError) as error:
        runner.run_case(ws, NAV_CASE, device_spec=spec)
    run = read_json(Path(error.value.run_diagnostic['dir']) / 'run.json')
    v = run['verdict']
    assert v['execution']['errors'][:-1] == preserved['execution']['errors']
    assert v['findings'][:-1] == preserved['findings']
    for key in ('alerts', 'action_required', 'first_anomaly', 'product_verdict'):
        assert v[key] == preserved[key]
    report = (Path(error.value.run_diagnostic['dir']) / 'report.md').read_text(encoding='utf-8')
    assert report.index('需要人工接管') < report.index('经验候选生成未完成') < report.index('**结论：')


def test_delayed_proposal_uses_actual_failure_time(rig, monkeypatch):
    import time
    from vpdtest.util import now_iso
    ws, spec = rig
    captured = {}
    def fail(self, run):
        captured['before'] = copy.deepcopy(run)
        time.sleep(0.03)
        captured['failure_time'] = now_iso()
        raise RuntimeError('delayed synthetic proposal')
    monkeypatch.setattr(experience.ExperienceStore, 'propose_from_run', fail)
    with pytest.raises(RuntimeError) as error:
        runner.run_case(ws, NAV_CASE, device_spec=spec)
    d = Path(error.value.run_diagnostic['dir'])
    run = read_json(d / 'run.json')
    assert captured['before']['finished'] < captured['failure_time'] <= run['finished']
    assert run['started'] == captured['before']['started']
    assert run['steps'] == captured['before']['steps']
    observations = [read_json(p / 'observation.json') for p in (d / 'obs').iterdir()]
    assert all(o['ts'] <= captured['before']['finished'] for o in observations)


@pytest.mark.parametrize('secondary_type', [OSError, KeyboardInterrupt])
def test_failure_timestamp_preparation_cannot_replace_proposal_error(rig, monkeypatch, secondary_type):
    ws, spec = rig
    original = RuntimeError('original proposal failure')
    def fail_time():
        raise secondary_type('secondary failure time error')
    def fail_proposal(self, run):
        # Only error finalization observes this fault; real core execution completed.
        assert run['verdict']['product_verdict'] == 'passed'
        assert len(run['steps']) == 4
        monkeypatch.setattr(runner, 'now_iso', fail_time)
        raise original
    monkeypatch.setattr(experience.ExperienceStore, 'propose_from_run', fail_proposal)
    with pytest.raises(RuntimeError) as caught:
        runner.run_case(ws, NAV_CASE, device_spec=spec)
    assert caught.value is original
    assert 'fail_proposal' in [f.name for f in traceback.extract_tb(original.__traceback__)]
    diagnostic = original.run_diagnostic
    assert diagnostic['phase'] == 'experience' and diagnostic['persisted']
    d = Path(diagnostic['dir'])
    assert not (d / 'run.json').exists()
    assert not (d / 'report.md').exists()
    assert len(list((d / 'obs').iterdir())) == 17
    assert not any(e['event'] == 'run_end' for e in read_jsonl(d / 'events.jsonl'))


def test_preserved_error_interval_resets_overlapping_real_replay(rig, monkeypatch):
    """A healthy run during proposal cannot count as recovery after its failure."""
    import time
    ws, spec = rig
    overlapping = []
    def fail_proposal(self, run):
        overlapping.append(runner.run_case(ws, NAV_CASE, device_spec=spec, propose=False))
        time.sleep(0.01)
        raise RuntimeError('proposal failed after overlapping synthetic replay')
    monkeypatch.setattr(experience.ExperienceStore, 'propose_from_run', fail_proposal)
    with pytest.raises(RuntimeError) as error:
        runner.run_case(ws, NAV_CASE, device_spec=spec)
    failed = read_json(Path(error.value.run_diagnostic['dir']) / 'run.json')
    time.sleep(0.01)
    later = runner.run_case(ws, NAV_CASE, device_spec=spec, propose=False)
    assert failed['started'] <= overlapping[0]['started'] < failed['finished'] < later['started']
    record_replay(ws, NAV_CASE, overlapping[0])
    record_replay(ws, NAV_CASE, later)
    case = record_replay(ws, NAV_CASE, failed)  # Delayed ingestion does not change execution order.
    summary = case['replays'][-1]
    assert summary['started'] == failed['started'] and summary['finished'] == failed['finished']
    assert has_execution_error(summary)
    basis = replay_basis(case, failed['case']['deps'])
    assert not basis['ok']
    assert overlapping[0]['run_id'] in basis['timing']['not_after']
    time.sleep(0.01)
    another = runner.run_case(ws, NAV_CASE, device_spec=spec, propose=False)
    case = record_replay(ws, NAV_CASE, another)
    basis = replay_basis(case, failed['case']['deps'])
    assert basis['ok']
    assert set(basis['replays']) == {later['run_id'], another['run_id']}
