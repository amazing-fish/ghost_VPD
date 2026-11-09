"""Local CI's version gate, exit status and non-destructive evidence contract."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'run_local_ci.py'
spec = importlib.util.spec_from_file_location('ghost_local_ci', SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_pins_read_exact_versions_and_comments(tmp_path):
    path = tmp_path / 'requirements.txt'
    path.write_text('# comment\npytest==9.1.1 # inline\n\nlxml==5.4.0\n')
    assert ci.requirements(path) == {'pytest': '9.1.1', 'lxml': '5.4.0'}


@pytest.mark.parametrize('text', ['pytest>=9', 'unversioned', 'pytest==9==1'])
def test_non_pinned_requirements_fail_closed(tmp_path, text):
    path = tmp_path / 'requirements.txt'
    path.write_text(text)
    with pytest.raises(ValueError):
        ci.requirements(path)


@pytest.mark.parametrize('version,packages,accepted', [
    ([3, 12, 4], {'pytest': '9.1.1'}, True),
    ([3, 11, 9], {'pytest': '9.1.1'}, False),
    ([3, 13, 1], {'pytest': '9.1.1'}, False),
    ([3, 12, 4], {'pytest': '9.0.2'}, False),
])
def test_environment_gate(monkeypatch, version, packages, accepted):
    info = {'python': version, 'packages': packages}
    monkeypatch.setattr(ci.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(info)))
    if accepted:
        assert ci.verify_environment('python', {'pytest': '9.1.1'}, {}) == info
    else:
        with pytest.raises(RuntimeError):
            ci.verify_environment('python', {'pytest': '9.1.1'}, {})


@pytest.mark.parametrize('stage_exit', [0, 1, 5])
def test_existing_environment_run_records_exit_and_preserves_previous(tmp_path, monkeypatch, stage_exit):
    state = tmp_path / 'state'
    previous = state / 'runs' / 'previous' / 'failure.log'
    previous.parent.mkdir(parents=True)
    previous.write_text('retain')
    monkeypatch.setattr(ci, 'git_output', lambda *args: 'head' if args[0] == 'rev-parse' else '')
    monkeypatch.setattr(ci, 'verify_environment', lambda *a: {'python': [3, 12, 4]})
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k nothing')
    calls = []
    def stage(name, command, run_dir, env):
        calls.append((name, command))
        assert 'PYTEST_ADDOPTS' not in env
        assert Path(env['TMP']).is_relative_to(state)
        assert Path(env['PIP_CACHE_DIR']).is_relative_to(state)
        assert env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
        return stage_exit
    monkeypatch.setattr(ci, 'run_stage', stage)
    assert ci.main(['--skip-install', '--state-dir', str(state)]) == stage_exit
    result = json.loads(next(state.rglob('result.json')).read_text())
    assert result['installer'] == 'skipped'
    assert result['status'] == ('passed' if stage_exit == 0 else 'failed')
    assert result['stages'] == {'pytest': stage_exit}
    assert calls[0][0] == 'pytest'
    assert 'tmp_path_retention_policy=failed' in calls[0][1]
    assert not any(arg.startswith('--basetemp') for arg in calls[0][1])
    assert previous.read_text() == 'retain'


def test_version_mismatch_does_not_run_tests(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, 'git_output', lambda *a: '')
    def mismatch(*a):
        raise RuntimeError('Pinned dependency mismatch')
    monkeypatch.setattr(ci, 'verify_environment', mismatch)
    monkeypatch.setattr(ci, 'run_stage', lambda *a: pytest.fail('pytest must not run'))
    assert ci.main(['--skip-install', '--state-dir', str(tmp_path)]) == 1
    result = json.loads(next(tmp_path.rglob('result.json')).read_text())
    assert result['status'] == 'blocked'
    assert result['stages'] == {}


def test_cloud_workflow_has_only_explicit_opt_in():
    workflow = (SCRIPT.parents[1] / '.github' / 'workflows' / 'vpd-skill-tests.yml').read_text()
    trigger = workflow.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
    assert 'workflow_dispatch:' in trigger
    assert 'pull_request:' not in trigger
    assert 'push:' not in trigger
    assert 'schedule:' not in trigger
    assert 'default: false' in trigger
    assert 'if: ${{ inputs.allow_cloud_minutes }}' in workflow

@pytest.mark.skipif(ci.os.name != 'nt', reason='Windows installer path')
@pytest.mark.parametrize('install_exit', [0, 7])
def test_runner_forwards_skip_doctor_and_records_install(tmp_path, monkeypatch, install_exit):
    monkeypatch.setattr(ci, 'git_output', lambda *a: '')
    monkeypatch.setattr(ci.subprocess, 'check_output', lambda *a, **k: '3.12')
    monkeypatch.setattr(ci.shutil, 'which', lambda name: 'pwsh')
    monkeypatch.setattr(ci, 'verify_environment', lambda *a: {'python': [3, 12, 4]})
    calls = []
    def stage(name, command, run_dir, env):
        calls.append(name)
        if name == 'install':
            assert '-SkipDoctor' in command
            assert '-LinkSkill' not in command
            return install_exit
        assert install_exit == 0
        return 0
    monkeypatch.setattr(ci, 'run_stage', stage)
    assert ci.main(['--state-dir', str(tmp_path)]) == (1 if install_exit else 0)
    result = json.loads(next(tmp_path.rglob('result.json')).read_text())
    assert result['installer'] == ('failed' if install_exit else 'passed')
    assert '-SkipDoctor' in result['install_command']
    assert calls == (['install'] if install_exit else ['install', 'pytest'])


def test_missing_git_records_blocked_result(tmp_path, monkeypatch):
    def missing(*args):
        raise FileNotFoundError('git is unavailable')
    monkeypatch.setattr(ci, 'git_output', missing)
    assert ci.main(['--skip-install', '--state-dir', str(tmp_path)]) == 1
    result = json.loads(next(tmp_path.rglob('result.json')).read_text())
    assert result['status'] == 'blocked'
    assert 'git is unavailable' in result['error']


def test_interrupted_test_stage_keeps_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, 'git_output', lambda *args: '')
    monkeypatch.setattr(ci, 'verify_environment', lambda *a: {})
    def interrupted(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(ci, 'run_stage', interrupted)
    assert ci.main(['--skip-install', '--state-dir', str(tmp_path)]) == 130
    result = json.loads(next(tmp_path.rglob('result.json')).read_text())
    assert result['status'] == 'interrupted'
    assert result['exit_code'] == 130
