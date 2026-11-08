"""Synthetic record corruption and recovery; the reader must never rewrite evidence."""
import json
import os
from pathlib import Path

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE

from vpdtest.console import progress
from vpdtest.runner import run_case
from vpdtest.util import append_jsonl, write_json


def make_run(root, name="run-synthetic"):
    path = root / name
    append_jsonl(path / "events.jsonl", {"event": "run_start", "case": name, "steps": ["S1"]})
    append_jsonl(path / "events.jsonl", {"event": "step_done", "step": "S1", "outcome": "passed"})
    append_jsonl(path / "events.jsonl", {"event": "run_end", "verdict": "passed"})
    return path


@pytest.mark.parametrize("line", [
    b"[]\n", b"null\n", b"7\n", b'"text"\n', b"{broken\n", b' {"event": 4}\n',
    b'{"event": "case_done", "verdict": []}\n', b'{"event":"run_start","steps":true}\n',
    b'{"event":"suite_start","total":{}}\n', b'{"event":"observe","state":"\xff"}\n',
    b'{"event":"run_start","case":NaN}\n', b'{"event":"observe","extra":1e999}\n',
    b'{"event":"observe","extra":"\\ud800"}\n',
    b'{"event":"run_start","case":{"toString":null}}\n',
    pytest.param(b'{"event":"observe","extra":' + b'[' * 10000 + b']' * 10000 + b'}\n', id="deep-json"),
])
def test_bad_event_is_isolated_and_never_mutates_or_rejudges(tmp_path, line):
    bad = make_run(tmp_path, "run-bad")
    good = make_run(tmp_path, "run-good")
    file = bad / "events.jsonl"
    file.write_bytes(file.read_bytes() + line)
    before = file.read_bytes()
    rows = {r["run_id"]: r for r in progress.list_runs(tmp_path)}
    assert rows[good.name]["state"] == "finished"
    assert rows[bad.name]["state"] == "unknown"
    assert rows[bad.name]["verdict"] == "passed"  # valid original run_end, not a new judgment
    assert rows[bad.name]["progress_reliable"] is False
    assert rows[bad.name]["read_errors"] == [
        {"source": "events.jsonl", "reason": "invalid_record", "count": 1}]
    assert progress.detail(tmp_path, bad.name)["read_errors"] == rows[bad.name]["read_errors"]
    assert file.read_bytes() == before


@pytest.mark.parametrize("record", [[], None, "wrong", {"verdict": []}, {"asset_review": []},
    {"steps": {}}, {"steps": [None]}, {"steps": [{"attempts": [None]}]},
    {"cleanup": [{"sub_steps": [None]}]}, {"assertions": [1]}, {"verdict": {"coverage": []}},
    {"steps": [{"condition": {"records": {}}}]}, {"steps": [{"condition": {"records": [None]}}]},
    {"assertions": [{"guard": {"records": [None]}}]},
    {"verdict": {"label": {"toString": None}}}, {"steps": [{"action": {"toString": None}}]},
    {"verdict": {"coverage": {"verified": "abc"}}},
    {"verdict": {"coverage": {"verified": {"length": {"toString": None}}}}},
    {"steps": [{"condition": {"records": [{"reason": {"toString": None}}]}}]}])
def test_invalid_result_shape_is_diagnosed_in_list_and_detail(tmp_path, record):
    bad = make_run(tmp_path, "run-bad")
    make_run(tmp_path, "run-good")
    write_json(bad / "run.json", record)
    before = (bad / "run.json").read_bytes()
    rows = {r["run_id"]: r for r in progress.list_runs(tmp_path)}
    assert rows["run-good"]["state"] == "finished"
    for result in [rows["run-bad"], progress.detail(tmp_path, "run-bad")]:
        assert result["state"] == "unknown"
        assert {"source": "run.json", "reason": "invalid_record"} in result["read_errors"]
        assert result["verdict"] == "passed"
        assert "verdict_detail" not in result
    assert (bad / "run.json").read_bytes() == before


def test_half_line_waits_then_reports_only_complete_bad_lines(tmp_path):
    path = make_run(tmp_path)
    file = path / "events.jsonl"
    with file.open("ab") as f:
        f.write(b' {"event": "observe", "state":')
    assert "read_errors" not in progress.summarize(path)
    with file.open("ab") as f:
        f.write(b'"home"}\n[]\n')
    out = progress.summarize(path)
    assert out["events"] == 4
    assert out["read_errors"][0]["count"] == 1


@pytest.mark.parametrize("replace_file", [False, True])
def test_equal_length_rewrite_or_replacement_invalidates_cached_verdict(tmp_path, replace_file):
    path = make_run(tmp_path)
    file = path / "events.jsonl"
    assert progress.summarize(path)["verdict"] == "passed"
    old_ns = file.stat().st_mtime_ns
    data = file.read_bytes().replace(b'"verdict": "passed"', b'"verdict": "failed"')
    assert len(data) == file.stat().st_size
    if replace_file:
        replacement = path / "replacement"
        replacement.write_bytes(data)
        os.utime(replacement, ns=(old_ns, old_ns))
        replacement.replace(file)
    else:
        file.write_bytes(data)
        os.utime(file, ns=(old_ns + 1_000_000_000, old_ns + 1_000_000_000))
    result = progress.summarize(path)
    assert result["verdict"] == "failed"
    assert result["events"] == 3 and result["done"] == 1


def test_repaired_event_and_result_records_clear_warnings(tmp_path):
    path = make_run(tmp_path)
    file = path / "events.jsonl"
    original = file.read_bytes()
    file.write_bytes(original + b"[]\n")
    write_json(path / "run.json", [])
    assert len(progress.detail(tmp_path, path.name)["read_errors"]) == 2
    file.write_bytes(original)
    write_json(path / "run.json", {"verdict": {"verdict": "passed"}})
    result = progress.detail(tmp_path, path.name)
    assert "read_errors" not in result and result["state"] == "finished"


@pytest.mark.parametrize("source", ["events.jsonl", "run.json"])
def test_unreadable_file_does_not_hide_healthy_run(tmp_path, monkeypatch, source):
    bad = make_run(tmp_path, "run-bad")
    make_run(tmp_path, "run-good")
    write_json(bad / "run.json", {"verdict": {"verdict": "passed"}})
    original = Path.open

    def unavailable(self, *args, **kwargs):
        if self == bad / source:
            raise PermissionError("synthetic denied read")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unavailable)
    rows = {r["run_id"]: r for r in progress.list_runs(tmp_path)}
    assert rows["run-good"]["state"] == "finished"
    for result in [rows["run-bad"], progress.detail(tmp_path, "run-bad")]:
        assert result["state"] == "unknown"
        assert {"source": source, "reason": "unreadable"} in result["read_errors"]


def test_missing_events_and_stat_race_are_visible_without_breaking_list(tmp_path, monkeypatch):
    bad = tmp_path / "run-missing"
    bad.mkdir()
    make_run(tmp_path, "run-good")
    result = progress.detail(tmp_path, bad.name)
    assert result["state"] == "unknown"
    assert result["read_errors"] == [{"source": "events.jsonl", "reason": "missing"}]
    original = Path.stat

    def unavailable(self, *args, **kwargs):
        if self == bad or self == bad / "events.jsonl":
            raise PermissionError("synthetic stat race")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unavailable)
    rows = {r["run_id"]: r for r in progress.list_runs(tmp_path)}
    assert rows["run-good"]["state"] == "finished"
    assert rows["run-missing"]["state"] == "unknown"
    assert progress.detail(tmp_path, bad.name)["state"] == "unknown"


def test_corrupt_only_events_have_no_invented_verdict(tmp_path):
    path = tmp_path / "run-bad"
    path.mkdir()
    (path / "events.jsonl").write_text(json.dumps([]) + "\n")
    result = progress.summarize(path)
    assert result["state"] == "unknown" and result["verdict"] is None
    assert result["done"] == 0


@pytest.mark.parametrize("source", ["events.jsonl", "run.json", "report.md"])
def test_stat_failure_is_isolated_in_list_and_detail(tmp_path, monkeypatch, source):
    bad = make_run(tmp_path, "run-bad")
    make_run(tmp_path, "run-good")
    original = Path.stat

    def unavailable(self, *args, **kwargs):
        if self == bad / source:
            raise PermissionError("synthetic stat denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unavailable)
    rows = {r["run_id"]: r for r in progress.list_runs(tmp_path)}
    assert rows["run-good"]["state"] == "finished"
    detail = progress.detail(tmp_path, "run-bad")
    assert {"source": source, "reason": "unreadable"} in detail["read_errors"]
    if source != "report.md":
        assert detail["state"] == rows["run-bad"]["state"] == "unknown"


@pytest.mark.parametrize("raw", ['{"verdict":{"label":NaN}}', '{"extra":1e999}',
                               '{"extra":"\\ud800"}', '[' * 1100 + ']' * 1100])
def test_non_transportable_run_record_is_isolated(tmp_path, raw):
    path = make_run(tmp_path)
    (path / "run.json").write_text(raw, encoding="utf-8")
    for result in [progress.summarize(path), progress.detail(tmp_path, path.name)]:
        assert {"source": "run.json", "reason": "invalid_record"} in result["read_errors"]
        json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")


def test_self_referencing_event_symlink_is_unreadable(tmp_path):
    bad = tmp_path / "run-bad"
    bad.mkdir()
    make_run(tmp_path, "run-good")
    file = bad / "events.jsonl"
    try:
        file.symlink_to(file.name)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    rows = {r["run_id"]: r for r in progress.list_runs(tmp_path)}
    assert rows["run-good"]["state"] == "finished"
    assert rows["run-bad"]["state"] == "unknown"
    assert {"source": "events.jsonl", "reason": "unreadable"} in rows["run-bad"]["read_errors"]


def test_real_offline_runner_records_remain_healthy(verdict_ws):
    confirm(verdict_ws, COPY_RULE)
    run = run_case(verdict_ws, COPY_CASE, device_spec=world("motor_hm_base.json"),
                   env={"account_state": "logged_out"}, propose=False)
    rows = progress.list_runs(verdict_ws.runs_dir)
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == COPY_CASE
    assert row["total"] == len(run["steps"])
    assert row["state"] == "finished" and "read_errors" not in row
    detail = progress.detail(verdict_ws.runs_dir, row["run_id"])
    assert "read_errors" not in detail
    assert detail["verdict_detail"] == run["verdict"]
