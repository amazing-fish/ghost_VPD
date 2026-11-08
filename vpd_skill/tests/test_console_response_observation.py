"""One source observation per HTTP detail response; synthetic files, no listener."""
import io
import json
import os
import threading
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from vpdtest.console import progress
from vpdtest.console.server import make_handler


def records(*rows):
    return b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in rows)


def finished(case="OLD", verdict="passed"):
    return records({"event": "run_start", "case": case, "steps": ["S1"]},
                   {"event": "step_done", "step": "S1", "outcome": verdict},
                   {"event": "run_end", "verdict": verdict})


def result(verdict="passed", informal=False):
    return {"verdict": {"verdict": verdict, "label": verdict, "attribution": "synthetic",
                        "coverage": {"verified": [] if informal else ["A"],
                                     "unverified": ["A"] if informal else []}},
            "validation_level": "offline_replay" if informal else "real_device",
            "informal": informal, "asset_review": {"state": "stale" if informal else "approved"},
            "steps": [{"step_id": "S1", "outcome": verdict}],
            "cleanup": [{"step_id": "C1", "outcome": verdict}],
            "assertions": [{"id": "A", "result": verdict}]}


def diagnostic(path):
    return {"schema_version": 1, "run_id": path.name, "dir": str(path), "state": "tool_error",
            "phase": "report", "step": None, "error_type": "OSError", "persisted": True,
            "safety": {"motion_armed": None, "motion_timeout": None, "safety_hold": None}}


def write_record(path, record):
    path.write_text(json.dumps(record), encoding="utf-8")


def make_run(root, data=None, name="run-synthetic", metadata=None):
    path = root / name
    path.mkdir()
    if data is not None:
        (path / "events.jsonl").write_bytes(data)
    if metadata is not None:
        write_record(path / "run.json", metadata)
    return path


class MemorySocket:
    def __init__(self, endpoint):
        self.input = io.BytesIO((f"GET {endpoint} HTTP/1.1\r\nHost: localhost:8765\r\n"
                                "X-VPD-Token: synthetic-token\r\nConnection: close\r\n\r\n").encode())
        self.output = bytearray()

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.extend(data)


def http_detail(path):
    # Exercise parsing, authentication, routing and JSON serialization, not do_GET in isolation.
    handler = make_handler(SimpleNamespace(runs_dir=path.parent), "synthetic-token", {"port": 8765})
    request = MemorySocket("/api/runs/" + path.name)
    handler(request, ("127.0.0.1", 23456), SimpleNamespace())
    headers, body = bytes(request.output).split(b"\r\n\r\n", 1)
    assert headers.startswith(b"HTTP/1.1 200 "), headers
    return json.loads(body)


class Reader:
    def __init__(self, stream, source, trace):
        self.stream, self.source, self.trace = stream, source, trace

    def __enter__(self):
        return self

    def __exit__(self, *args):
        outcome = self.stream.__exit__(*args)
        callback = self.trace.after_close.pop(self.source, None)
        if callback:
            assert self.stream.closed
            self.trace.mutations[self.source] += 1
            callback()
        return outcome

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def read(self, *args):
        data = self.stream.read(*args)
        self.trace.bytes[self.source] += len(data if isinstance(data, bytes) else data.encode("utf-8"))
        callback = self.trace.after_read.pop(self.source, None)
        if callback:
            callback()
        return data


class Trace:
    def __init__(self, monkeypatch, path):
        self.opens, self.bytes, self.parsed, self.exists, self.mutations = (Counter() for _ in range(5))
        self.scans, self.streams = [], []
        self.after_close, self.after_read, self.after_exists, self.fail_open = {}, {}, {}, {}
        original_open, original_exists = Path.open, progress._exists
        original_scan, original_load = progress._scan, progress._load_record

        def open_file(file, mode="r", *args, **kwargs):
            if file.parent != path or mode not in ("r", "rb"):
                return original_open(file, mode, *args, **kwargs)
            self.opens[file.name] += 1
            if file.name in self.fail_open:
                raise self.fail_open.pop(file.name)
            reader = Reader(original_open(file, mode, *args, **kwargs), file.name, self)
            self.streams.append(reader)
            return reader

        def exists(file):
            if file.parent == path:
                self.exists[file.name] += 1
            present = original_exists(file)
            callback = self.after_exists.pop(file.name, None) if file.parent == path else None
            if callback:
                callback()
            return present

        def scan(file, **kwargs):
            self.scans.append(file)
            return original_scan(file, **kwargs)

        def load(raw):
            row = original_load(raw)
            self.parsed["events" if isinstance(row, dict) and "event" in row else "metadata"] += 1
            return row

        monkeypatch.setattr(Path, "open", open_file)
        monkeypatch.setattr(progress, "_exists", exists)
        monkeypatch.setattr(progress, "_scan", scan)
        monkeypatch.setattr(progress, "_load_record", load)

    def assert_once(self, path, run_opens=0, diagnostic_opens=0):
        assert self.scans == [path / "events.jsonl"]
        assert self.opens["events.jsonl"] <= 1
        assert self.opens["run.json"] == run_opens
        assert self.opens["run_diagnostic.json"] == diagnostic_opens
        assert all(reader.closed for reader in self.streams)


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(progress, "_SCANS", {})


@pytest.mark.parametrize("mode", ["cold", "warm", "append", "mismatch"])
def test_http_reads_one_observation_with_incremental_parse_cost(tmp_path, monkeypatch, mode):
    original = finished()
    path = make_run(tmp_path, original, metadata=result())
    file = path / "events.jsonl"
    if mode != "cold":
        http_detail(path)
    data, event_parses, read_bytes = original, (3 if mode == "cold" else 0), len(original)
    if mode == "append":
        data += records({"event": "observe", "state": "home"})
        event_parses, read_bytes = 1, len(data)
    elif mode == "mismatch":
        data = finished("NEW", "failed") + records({"event": "observe", "state": "home"})
        event_parses, read_bytes = 4, len(original) + len(data)
    file.write_bytes(data)
    run_bytes = (path / "run.json").read_bytes()
    trace = Trace(monkeypatch, path)
    out = http_detail(path)
    trace.assert_once(path, run_opens=1)
    assert trace.opens["events.jsonl"] == 1
    assert trace.bytes == {"events.jsonl": read_bytes, "run.json": len(run_bytes)}
    assert trace.parsed["events"] == event_parses and trace.parsed["metadata"] == 1
    assert out["events"] == len(out["tail"]) == (4 if mode in ("append", "mismatch") else 3)
    assert "read_errors" not in out
    monkeypatch.undo()
    assert file.read_bytes() == data and (path / "run.json").read_bytes() == run_bytes


@pytest.mark.parametrize("change", ["replace", "append", "invalid", "remove"])
def test_http_event_first_close_change_keeps_one_generation(tmp_path, monkeypatch, change):
    original = records({"event": "run_start", "case": "OLD", "steps": ["S1"]})
    path = make_run(tmp_path, original)
    file = path / "events.jsonl"
    os.utime(file, (1700000000, 1700000000))
    trace = Trace(monkeypatch, path)

    def mutate():
        if change == "remove":
            file.unlink()
            return
        replacement = {"replace": finished("NEW", "failed"),
                       "append": original + records({"event": "step_done", "outcome": "passed"},
                                                    {"event": "run_end", "verdict": "passed"}),
                       "invalid": original + b"[]\n"}[change]
        if change in ("append", "invalid"):
            with file.open("ab") as stream:
                stream.write(replacement[len(original):])
        else:
            file.write_bytes(replacement)
        os.utime(file, (1800000000, 1800000000))

    trace.after_close["events.jsonl"] = mutate
    out = http_detail(path)
    assert trace.mutations == {"events.jsonl": 1}  # Hook does not depend on a second read happening.
    assert out["tail"] == [json.loads(original)]
    assert out["events"] == 1 and out["done"] == 0 and not out["finished"]
    assert out["updated"] == 1700000000 and out["title"] == "OLD" and out["state"] == "stale"
    assert "read_errors" not in out
    trace.assert_once(path)
    next_out = http_detail(path)
    if change in ("replace", "append"):
        assert next_out["finished"] and next_out["events"] == len(next_out["tail"]) == 3
        assert next_out["title"] == ("NEW" if change == "replace" else "OLD")
    else:
        error = {"source": "events.jsonl", "reason": "missing" if change == "remove" else "invalid_record"}
        if change == "invalid":
            error["count"] = 1
        assert next_out["read_errors"] == [error]
    if change != "remove":
        assert next_out["updated"] == 1800000000


def assert_metadata(out, expected):
    assert out["verdict"] == out["verdict_detail"]["verdict"] == expected["verdict"]["verdict"]
    assert out["coverage"] == expected["verdict"]["coverage"]
    assert out["steps"][0]["outcome"] == out["cleanup"][0]["outcome"] == expected["verdict"]["verdict"]
    assert out["assertions"] == expected["assertions"]
    for field in ("validation_level", "informal", "asset_review"):
        assert out[field] == expected[field]


def test_http_run_first_close_rewrite_keeps_context_and_verdict_together(tmp_path, monkeypatch):
    old, new = result(), result("failed", True)
    path = make_run(tmp_path, finished(), metadata=old)
    trace = Trace(monkeypatch, path)
    trace.after_close["run.json"] = lambda: write_record(path / "run.json", new)
    out = http_detail(path)
    assert trace.mutations == {"run.json": 1}
    assert_metadata(out, old)
    trace.assert_once(path, run_opens=1)
    assert_metadata(http_detail(path), new)


def test_http_equal_size_event_rewrite_does_not_mix_verdicts(tmp_path, monkeypatch):
    old, new = finished(), finished("NEW", "failed")
    assert len(old) == len(new)
    path = make_run(tmp_path, old)
    trace = Trace(monkeypatch, path)
    trace.after_close["events.jsonl"] = lambda: (path / "events.jsonl").write_bytes(new)
    out = http_detail(path)
    assert trace.mutations == {"events.jsonl": 1}
    assert out["verdict"] == out["tail"][-1]["verdict"] == "passed"
    assert out["title"] == out["tail"][0]["case"] == "OLD"
    trace.assert_once(path)
    refreshed = http_detail(path)
    assert refreshed["verdict"] == refreshed["tail"][-1]["verdict"] == "failed"


def test_http_observation_survives_another_request_during_run_read(tmp_path, monkeypatch):
    original = records({"event": "run_start", "case": "OLD", "steps": ["S1"]})
    path = make_run(tmp_path, original, metadata=result())
    file = path / "events.jsonl"
    cached = progress._scan(file)
    trace = Trace(monkeypatch, path)
    other = []

    def interleave():
        with file.open("ab") as stream:
            stream.write(records({"event": "step_done", "outcome": "passed"}, {"event": "run_end"}))
        worker = threading.Thread(target=lambda: other.append(http_detail(path)), daemon=True)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive(), "run.json I/O must not hold the scanner lock"
        assert other and other[0]["events"] == 3
        assert progress._SCANS[str(file.absolute())] is cached and cached.count == 3

    trace.after_read["run.json"] = interleave
    out = http_detail(path)
    assert out["events"] == 1 and not out["finished"] and out["tail"] == [json.loads(original)]
    assert trace.scans == [file, file] and trace.opens["events.jsonl"] == trace.opens["run.json"] == 2
    assert all(reader.closed for reader in trace.streams)


@pytest.mark.parametrize("missing", [False, True])
def test_detached_scan_copies_inside_lock_and_keeps_default_cache_identity(tmp_path, monkeypatch, missing):
    path = make_run(tmp_path, None if missing else finished())
    file = path / "events.jsonl"
    original = progress._Scan.copy
    copied = []

    def copy(scan):
        assert progress._SCANS_LOCK.locked()
        copied.append(scan)
        return original(scan)

    monkeypatch.setattr(progress._Scan, "copy", copy)
    snapshot = progress._scan(file, detached=True)
    cached = progress._SCANS[str(file.absolute())]
    assert copied == [cached] and snapshot is not cached and snapshot.__dict__ == cached.__dict__
    assert snapshot.tail is not cached.tail and snapshot.ended is not cached.ended
    assert snapshot.verdicts is not cached.verdicts and snapshot.missing is missing
    if not missing:
        with file.open("ab") as stream:
            stream.write(records({"event": "observe", "state": "new"}))
        assert progress._scan(file) is cached and cached.count == 4 and snapshot.count == 3


@pytest.mark.parametrize("failure", ["missing", "invalid", "unreadable"])
def test_http_result_failure_is_not_retried_in_same_response(tmp_path, monkeypatch, failure):
    path = make_run(tmp_path, finished(), metadata=None if failure == "missing" else [])
    trace = Trace(monkeypatch, path)
    repair = lambda: write_record(path / "run.json", result())
    if failure == "missing":
        trace.after_exists["run.json"] = repair
    elif failure == "invalid":
        trace.after_close["run.json"] = repair
    else:
        repair()
        trace.fail_open["run.json"] = PermissionError("synthetic denied read")
    out = http_detail(path)
    assert "verdict_detail" not in out and "steps" not in out
    assert out["verdict"] == "passed"  # Original event evidence, not a newly computed verdict.
    assert trace.exists["run.json"] == 1
    trace.assert_once(path, run_opens=0 if failure == "missing" else 1)
    if failure == "missing":
        assert "read_errors" not in out
    else:
        assert out["read_errors"] == [{"source": "run.json", "reason": "invalid_record" if failure == "invalid" else failure}]
    assert_metadata(http_detail(path), result())


@pytest.mark.parametrize("kind", ["run", "suite", "session", "explore", "console"])
def test_optional_result_detail_does_not_promote_unfinished_summary(tmp_path, monkeypatch, kind):
    path = make_run(tmp_path, records({"event": kind + "_start"}), name=kind + "-synthetic", metadata=result())
    trace = Trace(monkeypatch, path)
    summary = progress.summarize(path)
    assert not trace.opens["run.json"] and not summary["finished"]
    trace.scans.clear()
    trace.opens.clear()
    out = http_detail(path)
    trace.assert_once(path, run_opens=1)
    assert out["verdict_detail"] == result()["verdict"] and out["steps"][0]["step_id"] == "S1"
    assert not out["finished"]
    assert not {"validation_level", "informal", "asset_review"} & out.keys()
    assert out.get("verdict") is None


@pytest.mark.parametrize("tail", [0, -1, -60, 1, 60, 100])
def test_detail_preserves_tail_slice_semantics(tmp_path, monkeypatch, tail):
    data = finished() + records(*({"event": "observe", "state": str(i)} for i in range(70)))
    path = make_run(tmp_path, data, metadata=result())
    trace = Trace(monkeypatch, path)
    out = progress.detail(tmp_path, path.name, tail=tail)
    expected = [json.loads(row) for row in data.splitlines()][-progress.TAIL_KEEP:][-tail:]
    assert out["tail"] == expected and out["steps"][0]["step_id"] == "S1"
    trace.assert_once(path, run_opens=1)


@pytest.mark.parametrize("failure", ["missing", "invalid", "unreadable"])
def test_http_events_failure_has_one_observation_and_source_error(tmp_path, monkeypatch, failure):
    data = None if failure == "missing" else finished() + (b"[]\n" if failure == "invalid" else b"")
    path = make_run(tmp_path, data)
    trace = Trace(monkeypatch, path)
    if failure == "unreadable":
        trace.fail_open["events.jsonl"] = PermissionError("synthetic denied read")
    out = http_detail(path)
    error = {"source": "events.jsonl", "reason": "invalid_record" if failure == "invalid" else failure}
    if failure == "invalid":
        error["count"] = 1
    assert out["read_errors"] == [error] and out["state"] == "unknown"
    assert out["progress_reliable"] is False
    assert len(out["tail"]) == out["events"] == (3 if failure == "invalid" else 0)
    trace.assert_once(path)
    expected_stamp = path.stat().st_mtime if failure == "missing" else (path / "events.jsonl").stat().st_mtime
    assert out["updated"] == pytest.approx(expected_stamp)


@pytest.mark.parametrize("journal", [None, b"[]\n", records({"event": "run_start"})])
@pytest.mark.parametrize("bad_result", [False, True])
def test_http_diagnostic_state_and_saved_result_survive_degraded_sources(tmp_path, monkeypatch, journal, bad_result):
    path = make_run(tmp_path, journal, metadata=[] if bad_result else result())
    saved = diagnostic(path)
    write_record(path / "run_diagnostic.json", saved)
    trace = Trace(monkeypatch, path)
    out = http_detail(path)
    trace.assert_once(path, run_opens=1, diagnostic_opens=1)
    assert out["run_diagnostic"] == saved and out["state"] == "tool_error" and not out["finished"]
    if bad_result:
        assert "verdict_detail" not in out and "validation_level" not in out
        assert {"source": "run.json", "reason": "invalid_record"} in out["read_errors"]
    else:
        assert_metadata(out, result())
    if journal in (None, b"[]\n"):
        assert out["read_errors"] and out["progress_reliable"] is False


@pytest.mark.parametrize("failure", ["foreign", "invalid", "unreadable"])
def test_http_invalid_diagnostic_is_read_once_without_summary_promotion(tmp_path, monkeypatch, failure):
    path = make_run(tmp_path, records({"event": "run_start"}), metadata=result())
    saved = diagnostic(path)
    saved["run_id"] = "run-foreign"
    write_record(path / "run_diagnostic.json", [] if failure == "invalid" else saved)
    trace = Trace(monkeypatch, path)
    if failure == "unreadable":
        trace.fail_open["run_diagnostic.json"] = PermissionError("synthetic denied read")
    out = http_detail(path)
    trace.assert_once(path, run_opens=1, diagnostic_opens=1)
    assert out["state"] == "unknown" and not out["finished"] and "run_diagnostic" not in out
    assert "validation_level" not in out and out["verdict"] is None
    assert out["verdict_detail"]["verdict"] == "passed"
    assert out["read_errors"] == [{"source": "run_diagnostic.json",
                                   "reason": "unreadable" if failure == "unreadable" else "invalid_record"}]


@pytest.mark.parametrize("kind", ["suite", "session", "explore", "console"])
def test_http_non_run_diagnostic_is_not_read(tmp_path, monkeypatch, kind):
    path = make_run(tmp_path, records({"event": kind + "_start"}), name=kind + "-synthetic")
    write_record(path / "run_diagnostic.json", diagnostic(path))
    trace = Trace(monkeypatch, path)
    out = http_detail(path)
    trace.assert_once(path)
    assert "run_diagnostic" not in out and out["state"] not in ("tool_error", "interrupted")
    assert not trace.exists["run_diagnostic.json"]


@pytest.mark.parametrize("present", [False, True])
def test_report_is_checked_once_without_reading_contents(tmp_path, monkeypatch, present):
    path = make_run(tmp_path, finished())
    report = path / "report.md"
    if present:
        report.write_text("synthetic report", encoding="utf-8")
    trace = Trace(monkeypatch, path)
    out = http_detail(path)
    assert out["report"] == (str(report) if present else None)
    assert trace.exists["report.md"] == 1 and trace.opens["report.md"] == 0


def test_report_stat_error_preserves_diagnostic_priority(tmp_path, monkeypatch):
    path = make_run(tmp_path, finished())
    write_record(path / "run_diagnostic.json", diagnostic(path))
    original = Path.stat
    attempts = []

    def stat(file, *args, **kwargs):
        if file == path / "report.md":
            attempts.append(file)
            raise PermissionError("synthetic denied stat")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    out = http_detail(path)
    assert attempts == [path / "report.md"] and out["report"] is None
    assert out["state"] == "tool_error" and out["progress_reliable"] is False
    assert out["read_errors"] == [{"source": "report.md", "reason": "unreadable"}]
