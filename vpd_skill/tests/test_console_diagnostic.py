"""Explicit interruption evidence coexists with degraded journals and saved verdicts."""
import json

import pytest

from vpdtest.console import progress
from vpdtest.util import append_jsonl, write_json
from test_console_polling import run_page


def diagnostic(path, state="tool_error"):
    record = {"schema_version": 1, "run_id": path.name, "dir": str(path), "state": state,
              "phase": "step", "step": "S1", "error_type": "OSError", "persisted": True,
              "safety": {"motion_armed": None, "motion_timeout": None, "safety_hold": None}}
    write_json(path / "run_diagnostic.json", record)
    return record


@pytest.mark.parametrize("journal", ["valid", "missing", "corrupt"])
@pytest.mark.parametrize("state", ["tool_error", "interrupted"])
def test_valid_diagnostic_retains_partial_journal_and_saved_verdict(tmp_path, journal, state):
    path = tmp_path / "run-synthetic"
    record = diagnostic(path, state)
    if journal != "missing":
        append_jsonl(path / "events.jsonl", {"event": "run_start", "steps": ["S1"]})
        append_jsonl(path / "events.jsonl", {"event": "step_start", "step": "S1"})
    if journal == "corrupt":
        with (path / "events.jsonl").open("a") as f:
            f.write('{broken\n')
    write_json(path / "run.json", {"verdict": {"verdict": "passed", "label": "passed"}})
    for out in [progress.summarize(path), progress.detail(tmp_path, path.name)]:
        assert out["state"] == state and out["finished"] is False
        assert out["verdict"] == "passed" and out["run_diagnostic"] == record
        if journal != "valid":
            assert out["progress_reliable"] is False and out["read_errors"]
        else:
            assert out["current"] == "S1" and out["done"] == 0


@pytest.mark.parametrize("change", [None, [], {"run_id": "run-other"}, {"schema_version": True},
    {"state": "passed"}, {"persisted": False}, {"phase": []}, {"step": {}}, {"error_type": 42},
    {"safety": {"motion_armed": False}}, {"safety": {"motion_armed": "safe", "motion_timeout": None, "safety_hold": None}}])
def test_invalid_diagnostic_never_creates_terminal_claim(tmp_path, change):
    path = tmp_path / "run-invalid"
    record = diagnostic(path)
    write_json(path / "run_diagnostic.json", {**record, **change} if isinstance(change, dict) else change)
    append_jsonl(path / "events.jsonl", {"event": "run_start"})
    out = progress.summarize(path)
    assert out["state"] == "unknown" and "run_diagnostic" not in out
    assert {"source": "run_diagnostic.json", "reason": "invalid_record"} in out["read_errors"]


def test_missing_diagnostic_does_not_guess_cancellation(tmp_path):
    path = tmp_path / "run-missing"
    append_jsonl(path / "events.jsonl", {"event": "run_start"})
    out = progress.summarize(path, now=(path / "events.jsonl").stat().st_mtime + 121)
    assert out["state"] == "stale" and "run_diagnostic" not in out


def test_invalid_run_json_does_not_erase_valid_failure(tmp_path):
    path = tmp_path / "run-invalid-result"
    diagnostic(path)
    write_json(path / "run.json", [])
    out = progress.detail(tmp_path, path.name)
    assert out["state"] == "tool_error"
    assert {"source": "run.json", "reason": "invalid_record"} in out["read_errors"]
    assert {"source": "events.jsonl", "reason": "missing"} in out["read_errors"]


def test_actual_page_distinguishes_diagnostic_and_saved_verdict():
    run_page(r'''
const row = {...sample('run-diagnostic'), state: 'tool_error', finished: false, verdict: 'passed',
 run_diagnostic: {state: 'tool_error', phase: 'report', step: null, error_type: 'OSError', dir: '/synthetic/run'},
 read_errors: [{source: 'events.jsonl', reason: 'missing'}], progress_reliable: false};
renderRuns([row]);
assert.match($('runs').textContent, /工具异常.*非产品结论/);
assert.match($('runs').textContent, /已保存结论/);
assert.match($('runs').textContent, /不保证设备已停止/);
assert.match($('runs').textContent, /缺少文件/);
selected = row.run_id;
const poll = pollDetail();
pending[0].resolve({...row, tail: [], verdict_detail: {verdict: 'passed', label: '通过'}});
await poll;
assert.match($('detail').textContent, /异常前已保存结论（不表示正常结束）/);
assert.match($('detail').textContent, /证据目录 \/synthetic\/run/);
assert.match($('detail').textContent, /日志缺失不能证明动作未发送或现场安全/);
assert.match($('detail').textContent, /缺少文件/);
''')


@pytest.mark.parametrize("kind", ["suite", "session", "explore", "console"])
def test_non_run_diagnostic_does_not_reclassify(tmp_path, kind):
    path = tmp_path / (kind + "-synthetic")
    diagnostic(path)
    append_jsonl(path / "events.jsonl", {"event": kind + "_start"})
    out = progress.summarize(path)
    assert out["state"] not in ("interrupted", "tool_error") and "run_diagnostic" not in out
