"""Complete, explicitly requested Console history; synthetic files, never devices."""
import json
import os

from vpdtest.console import progress
from vpdtest.util import append_jsonl


def history_run(root, index, stamp=None):
    path = root / f"run-synthetic-{index:04}"
    append_jsonl(path / "events.jsonl", {"event": "run_start", "case": f"TC-SYNTHETIC-{index}",
                                      "title": f"合成历史 {index}", "steps": []})
    append_jsonl(path / "events.jsonl", {"event": "run_end", "verdict": "passed"})
    os.utime(path / "events.jsonl", (stamp if stamp is not None else index + 1700000000,) * 2)
    return path


def test_complete_history_retains_oldest_beyond_default_window(tmp_path):
    for i in range(45):
        history_run(tmp_path, i)
    recent, history = progress.list_runs(tmp_path), progress.list_runs(tmp_path, limit=None)
    assert len(recent) == 40 and len(history) == 45
    assert recent == history[:40]
    assert history[-1]["run_id"] == "run-synthetic-0000"
    detail = progress.detail(tmp_path, history[-1]["run_id"])
    assert detail["title"] == "TC-SYNTHETIC-0" and detail["state"] == "finished"


def test_tied_updates_have_deterministic_run_id_order(tmp_path, monkeypatch):
    paths = [history_run(tmp_path, i, stamp=1700000000) for i in (2, 0, 1)]
    monkeypatch.setattr(type(tmp_path), "iterdir", lambda self: iter(paths))
    first = progress.list_runs(tmp_path, limit=None)
    paths.reverse()
    second = progress.list_runs(tmp_path, limit=None)
    assert first == second
    assert [r["run_id"] for r in first] == ["run-synthetic-0002", "run-synthetic-0001", "run-synthetic-0000"]


def test_full_refresh_replaces_mutated_order_and_preserves_bad_records(tmp_path):
    paths = [history_run(tmp_path, i) for i in range(45)]
    before = progress.list_runs(tmp_path, limit=None)
    with (paths[0] / "events.jsonl").open("a") as f:
        f.write("[]\n")
    os.utime(paths[0] / "events.jsonl", (1800000000,) * 2)
    after = progress.list_runs(tmp_path, limit=None)
    assert before[-1]["run_id"] == after[0]["run_id"]
    assert len({r["run_id"] for r in after}) == len(after) == 45
    assert after[0]["state"] == "unknown" and after[0]["progress_reliable"] is False
    assert all(r["state"] == "finished" for r in after[1:])
    assert json.dumps(after, ensure_ascii=False, allow_nan=False)


def test_history_cache_is_bounded_and_latest_entries_are_retained(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_SCANS", {})
    monkeypatch.setattr(progress, "SCAN_CACHE_LIMIT", 4)
    paths = [history_run(tmp_path, i) for i in range(7)]
    rows = progress.list_runs(tmp_path, limit=None)
    assert len(rows) == 7 and len(progress._SCANS) == 4
    assert str((paths[-1] / "events.jsonl").absolute()) in progress._SCANS
    assert str((paths[0] / "events.jsonl").absolute()) not in progress._SCANS
    restored = progress.detail(tmp_path, paths[0].name)
    assert restored["state"] == "finished" and restored["events"] == 2
    assert len(progress._SCANS) == 4
    # 淘汰不是丢弃记录；再次读取仍完整，缓存命中仍沿原增量协议。
    append_jsonl(paths[0] / "events.jsonl", {"event": "observe", "state": "synthetic"})
    assert progress.summarize(paths[0])["events"] == 3


def test_missing_event_records_also_obey_cache_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "_SCANS", {})
    monkeypatch.setattr(progress, "SCAN_CACHE_LIMIT", 3)
    for i in range(8):
        (tmp_path / f"run-missing-{i}").mkdir()
    rows = progress.list_runs(tmp_path, limit=None)
    assert len(rows) == 8 and len(progress._SCANS) == 3
    assert all(r["state"] == "unknown" for r in rows)
