"""Physical JSONL boundaries and read-only recovery, using synthetic records only."""
import json
import os

import pytest

from vpdtest import session
from vpdtest.console import progress
from vpdtest.util import append_jsonl, read_jsonl, read_jsonl_recoverable

ENDINGS = [b"\n", b"\r\n", b"\r"]
CHUNKS = [1, 2, 3, 7, 1 << 20]
UNICODE_TEXT = "合成\u0085文字\u2028段落\u2029🙂"


def encoded(record):
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def append_bytes(path, data):
    with path.open("ab") as stream:
        stream.write(data)


def run_records():
    return [{"event": "run_start", "case": "synthetic", "title": UNICODE_TEXT, "steps": ["S1"]},
            {"event": "step_start", "step": "S1"},
            {"event": "step_done", "step": "S1", "outcome": "passed"},
            {"event": "observe", "state": UNICODE_TEXT},
            {"event": "run_end", "verdict": "passed"}]


def make_run(root, name="run-synthetic", ending=b"\r"):
    path = root / name
    path.mkdir()
    (path / "events.jsonl").write_bytes(ending.join(map(encoded, run_records())) + ending)
    return path


@pytest.mark.parametrize("endings", [[b"\n"], [b"\r\n"], [b"\r"], ENDINGS])
@pytest.mark.parametrize("terminated", [False, True])
def test_recoverable_matches_strict_reader_on_physical_lines(tmp_path, endings, terminated):
    records = [{"n": n, "text": "合成\r\n文字"} for n in range(5)]
    data = b"\r\n \t\r"
    for n, record in enumerate(records):
        data += encoded(record)
        if n < len(records) - 1 or terminated:
            ending = endings[n % len(endings)]
            data += ending + b" \t" + ending
    path = tmp_path / "records.jsonl"
    path.write_bytes(data)
    assert read_jsonl(path) == records
    assert read_jsonl_recoverable(path) == (records, None)
    assert path.read_bytes() == data


@pytest.mark.parametrize("data", [b"", b" \t", b"\n\r\n \t\r"])
def test_empty_blank_and_missing_files_are_unchanged(tmp_path, data):
    path = tmp_path / "records.jsonl"
    assert read_jsonl_recoverable(path) == ([], None)
    assert not path.exists()
    path.write_bytes(data)
    assert read_jsonl_recoverable(path) == ([], None)
    assert path.read_bytes() == data


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_unicode_separators_in_json_string_are_preserved(tmp_path, separator):
    record = {"event": "observe", "state": "before" + separator + "after"}
    path = tmp_path / "records.jsonl"
    data = encoded(record) + b"\n"
    path.write_bytes(data)
    assert read_jsonl(path) == [record]
    assert read_jsonl_recoverable(path) == ([record], None)
    assert path.read_bytes() == data


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("ending", [b"", b"\n", b"\r"])
def test_unicode_separators_between_objects_are_not_record_boundaries(tmp_path, separator, ending):
    path = tmp_path / "events.jsonl"
    data = encoded({"event": "observe"}) + separator.encode() + encoded({"event": "run_end"}) + ending
    path.write_bytes(data)
    with pytest.raises(json.JSONDecodeError):
        read_jsonl(path)
    if ending:
        with pytest.raises(json.JSONDecodeError):
            read_jsonl_recoverable(path)
    else:
        records, dropped = read_jsonl_recoverable(path)
        assert records == [] and dropped
    scan = progress._scan(path)
    assert scan.count == 0 and scan.invalid_lines == bool(ending)
    assert path.read_bytes() == data


@pytest.mark.parametrize("ending", ENDINGS)
@pytest.mark.parametrize("interior", [False, True])
def test_terminated_bad_json_is_corruption_including_cr(tmp_path, ending, interior):
    path = tmp_path / "events.jsonl"
    data = b'{"event":"good"}' + ending + b'{"broken":' + ending
    if interior:
        data += b'{"event":"later"}'
    path.write_bytes(data)
    with pytest.raises(json.JSONDecodeError):
        read_jsonl_recoverable(path)
    assert path.read_bytes() == data


@pytest.mark.parametrize("ending", ENDINGS)
def test_unterminated_json_tail_is_dropped_with_bounded_evidence(tmp_path, ending):
    path = tmp_path / "events.jsonl"
    data = encoded({"event": "good"}) + ending + ('{"note":"' + "合成" * 200).encode()
    path.write_bytes(data)
    records, dropped = read_jsonl_recoverable(path)
    assert records == [{"event": "good"}]
    assert dropped["tail"].startswith('{"note":"合成') and dropped["tail"].endswith("…")
    assert len(dropped["tail"]) <= 120
    assert path.read_bytes() == data


@pytest.mark.parametrize("bad", [b"\xff", b"\x80", b"\xc0\xaf", b"\xe5A", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"])
@pytest.mark.parametrize("suffix", [b"", b"\n", b"\r", b"\r\n", b'\r{"event":"later"}\r'])
def test_invalid_utf8_after_valid_json_is_not_truncation(tmp_path, bad, suffix):
    path = tmp_path / "events.jsonl"
    data = b'{"event":"good"}\r{"event":"act_result","seq":1}' + bad + suffix
    path.write_bytes(data)
    with pytest.raises(UnicodeDecodeError):
        read_jsonl_recoverable(path)
    assert path.read_bytes() == data


@pytest.mark.parametrize("incomplete", [b"\xe5", b"\xe5\xb7", b"\xf0\x9f\x98"])
@pytest.mark.parametrize("prefix", [b"", b'{"note":"', b'{"event":"act_result","seq":1}'])
def test_utf8_tail_never_accepts_complete_json_prefix(tmp_path, incomplete, prefix):
    path = tmp_path / "events.jsonl"
    data = b'{"event":"good"}\r' + prefix + incomplete
    path.write_bytes(data)
    records, dropped = read_jsonl_recoverable(path)
    assert records == [{"event": "good"}]
    assert dropped["tail"] == prefix.decode() + f"<utf8:{len(incomplete)}B>…"
    assert len(dropped["tail"]) <= 120
    with pytest.raises(UnicodeDecodeError):
        read_jsonl(path)
    assert path.read_bytes() == data


@pytest.mark.parametrize("suffix", ENDINGS + [b'\r{"event":"later"}'])
@pytest.mark.parametrize("incomplete", [b"\xe5", b"\xe5\xb7", b"\xf0\x9f\x98"])
def test_incomplete_utf8_in_terminated_or_interior_record_is_corruption(tmp_path, suffix, incomplete):
    path = tmp_path / "events.jsonl"
    data = b'{"event":"good"}\r{"note":"' + incomplete + suffix
    path.write_bytes(data)
    with pytest.raises(UnicodeDecodeError):
        read_jsonl_recoverable(path)
    assert path.read_bytes() == data


def test_long_utf8_tail_evidence_is_bounded(tmp_path):
    path = tmp_path / "events.jsonl"
    data = ('{"note":"' + "文" * 300).encode() + b"\xe5\xb7"
    path.write_bytes(data)
    records, dropped = read_jsonl_recoverable(path)
    assert records == [] and len(dropped["tail"]) <= 120
    assert dropped["tail"].endswith("<utf8:2B>…")
    assert path.read_bytes() == data


@pytest.mark.parametrize("ending", [b""] + ENDINGS)
@pytest.mark.parametrize("chunk", CHUNKS)
def test_append_keeps_original_boundary_for_all_readers(tmp_path, monkeypatch, ending, chunk):
    monkeypatch.setattr(progress, "_READ_CHUNK", chunk)
    path = tmp_path / "events.jsonl"
    first = {"event": "observe", "state": UNICODE_TEXT}
    second = {"event": "console_end"}
    original = encoded(first) + ending
    path.write_bytes(original)
    assert read_jsonl_recoverable(path) == ([first], None)
    scan = progress._scan(path)
    assert scan.count == bool(ending)  # Console waits for a physical terminator, even for valid JSON.
    append_jsonl(path, second)
    expected = original + (b"" if ending else b"\n") + json.dumps(second, ensure_ascii=False).encode() + b"\n"
    assert path.read_bytes() == expected
    assert read_jsonl(path) == [first, second]
    assert read_jsonl_recoverable(path) == ([first, second], None)
    for _ in range(3):
        assert progress._scan(path) is scan
        assert scan.count == 2 and scan.invalid_lines == 0 and scan.pending == b""
    assert path.read_bytes() == expected


@pytest.mark.parametrize("endings", [[b"\n"], [b"\r\n"], [b"\r"], ENDINGS])
@pytest.mark.parametrize("chunk", CHUNKS)
def test_console_reads_physical_records_across_chunks(tmp_path, monkeypatch, endings, chunk):
    monkeypatch.setattr(progress, "_READ_CHUNK", chunk)
    path = tmp_path / "run-synthetic"
    path.mkdir()
    data = b"\r\n \t\r"
    for n, record in enumerate(run_records()):
        ending = endings[n % len(endings)]
        data += encoded(record) + ending + b" \t" + ending
    file = path / "events.jsonl"
    file.write_bytes(data)
    for _ in range(3):
        out = progress.summarize(path)
        assert out["events"] == 5 and out["done"] == 1 and out["total"] == 1
        assert out["state"] == "finished" and out["verdict"] == "passed"
        assert "read_errors" not in out
        scan = progress._scan(file)
        assert scan.last_obs_state == UNICODE_TEXT and scan.tail == run_records()
        assert scan.offset == len(data) and scan.pending == b"" and scan.invalid_lines == 0
    assert file.read_bytes() == data


@pytest.mark.parametrize("chunk", CHUNKS)
def test_console_utf8_and_crlf_split_between_polls(tmp_path, monkeypatch, chunk):
    monkeypatch.setattr(progress, "_READ_CHUNK", chunk)
    path = tmp_path / "events.jsonl"
    records = run_records()
    observe = encoded(records[3])
    split = observe.index("🙂".encode()) + 2
    data = encoded(records[0]) + b"\r" + observe[:split]
    path.write_bytes(data)
    scan = progress._scan(path)
    assert scan.count == 1 and scan.invalid_lines == 0 and scan.pending == observe[:split]
    assert progress._scan(path) is scan and scan.count == 1
    for extra, count, pending in [
        (observe[split:] + b"\r", 2, b""),
        (b"\n\r\n" + encoded(records[2]) + b"\r", 3, b""),
        (b"\n" + encoded(records[4]), 3, encoded(records[4])),
        (b"\r", 4, b""),
        (b"\n", 4, b""),
    ]:
        append_bytes(path, extra)
        data += extra
        for _ in range(2):
            assert progress._scan(path) is scan
            assert scan.count == count and scan.invalid_lines == 0 and scan.pending == pending
            assert scan.offset == len(data)
        assert path.read_bytes() == data
    assert scan.steps_done == 1 and scan.last_obs_state == UNICODE_TEXT
    assert scan.ended["run_end"]["verdict"] == "passed"


@pytest.mark.parametrize("ending", ENDINGS)
@pytest.mark.parametrize("bad", [
    b'{"broken":', b'{"event":7}', b'{"event":"observe","state":[]}',
    b'{"event":"run_start","steps":true}', b'{"event":"suite_start","total":false}',
    b'{"event":"observe","extra":NaN}', b'{"event":"observe","extra":"\\ud800"}',
    b'{"event":"observe","state":"\xff"}',
])
def test_console_corruption_isolated_without_rejudging_originals(tmp_path, ending, bad):
    damaged = make_run(tmp_path, "run-damaged", ending)
    healthy = make_run(tmp_path, "run-healthy", ending)
    file = damaged / "events.jsonl"
    append_bytes(file, bad + ending)
    originals = {p: (p / "events.jsonl").read_bytes() for p in [damaged, healthy]}
    for _ in range(2):
        rows = {r["run_id"]: r for r in progress.list_runs(tmp_path)}
        assert rows[healthy.name]["state"] == "finished"
        for out in [rows[damaged.name], progress.detail(tmp_path, damaged.name)]:
            assert out["state"] == "unknown" and out["progress_reliable"] is False
            assert out["events"] == 5 and out["done"] == 1 and out["verdict"] == "passed"
            assert out["read_errors"] == [{"source": "events.jsonl", "reason": "invalid_record", "count": 1}]
            json.dumps(out, ensure_ascii=False, allow_nan=False).encode("utf-8")
        assert progress._scan(file).last_obs_state == UNICODE_TEXT
    assert {p: (p / "events.jsonl").read_bytes() for p in originals} == originals


@pytest.mark.parametrize("change", ["truncate", "replace", "equal_length_repair"])
@pytest.mark.parametrize("ending", ENDINGS)
def test_console_changed_files_discard_cached_records(tmp_path, ending, change):
    path = make_run(tmp_path, ending=ending)
    file = path / "events.jsonl"
    original = file.read_bytes()
    if change == "equal_length_repair":
        append_bytes(file, b'{"event":null}' + ending)
        assert progress.summarize(path)["state"] == "unknown"
        replacement = original + b'{"event":"ok"}' + ending
        assert len(replacement) == file.stat().st_size
    else:
        assert progress.summarize(path)["verdict"] == "passed"
        replacement = original.replace(b'"passed"', b'"failed"')
        if change == "truncate":
            replacement = encoded({"event": "run_start", "case": "restarted", "steps": []}) + ending
    old_scan = progress._scan(file)
    old_ns = file.stat().st_mtime_ns
    if change == "replace":
        other = path / "replacement.jsonl"
        other.write_bytes(replacement)
        os.utime(other, ns=(old_ns, old_ns))
        other.replace(file)
    else:
        file.write_bytes(replacement)
        os.utime(file, ns=(old_ns + 1_000_000_000, old_ns + 1_000_000_000))
    result = progress.summarize(path)
    assert progress._scan(file) is not old_scan and "read_errors" not in result
    if change == "truncate":
        assert result["events"] == 1 and result["done"] == 0 and result["verdict"] is None
        assert result["finished"] is False and result["title"] == "restarted"
    else:
        assert result["events"] == (6 if change == "equal_length_repair" else 5)
        assert result["verdict"] == ("passed" if change == "equal_length_repair" else "failed")
    assert file.read_bytes() == replacement


def test_console_cache_eviction_rereads_cr_records_and_pending_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "SCAN_CACHE_LIMIT", 2)
    paths = [make_run(tmp_path, f"run-synthetic-{n}") for n in range(3)]
    file = paths[0] / "events.jsonl"
    append_bytes(file, b'{"event":"observe","state":"pending"}')
    first = progress._scan(file)
    for path in paths[1:]:
        assert progress.summarize(path)["events"] == 5
    assert len(progress._SCANS) == 2 and str(file.absolute()) not in progress._SCANS
    restored = progress._scan(file)
    assert restored is not first and restored.count == 5 and restored.pending == first.pending
    append_bytes(file, b"\r\n")
    for _ in range(2):
        assert progress._scan(file) is restored and restored.count == 6
        assert restored.last_obs_state == "pending" and restored.pending == b""
    assert len(progress._SCANS) == 2


def session_records():
    return [{"event": "session_open", "ts": "synthetic", "identity": {"app": "synthetic"}},
            {"event": "act_request", "ts": "synthetic", "seq": 1, "step_id": "S1", "request": {"action": "tap"}},
            {"event": "dispatch", "op": "tap", "sent": True}]


@pytest.mark.parametrize("ending", ENDINGS)
def test_session_truncated_act_result_stays_unresolved_and_blocked(tmp_path, ending):
    file = tmp_path / "events.jsonl"
    data = ending.join(map(encoded, session_records())) + ending
    data += encoded({"event": "act_result", "seq": 1, "outcome": "passed", "effect_unknown": False}) + b"\xe5\xb7"
    file.write_bytes(data)
    for _ in range(2):
        trail = session.trail(tmp_path)
        item = next(it for it in trail["items"] if it.get("seq") == 1)
        assert item["kind"] == "incomplete" and item["effect_unknown"] and item["dispatched"]
        assert "outcome" not in item and "record" not in item and trail["actions"] == 1
        assert set(trail["truncated_tail"]) == {"events"}
        blocker = session.append_blocker(trail)
        assert blocker["status"] == "session_log_incomplete" and blocker["dispatched"] is False
        assert blocker["files"] == ["events.jsonl"]
        assert blocker["unresolved_actions"] == [{"seq": 1, "step": "S1"}]
        assert session.summarize(trail)["write_blocker"] == blocker
        assert file.read_bytes() == data and not (tmp_path / "steps.jsonl").exists()


@pytest.mark.parametrize("ending", ENDINGS)
def test_session_truncated_steps_never_exposes_valid_json_prefix(tmp_path, ending):
    events = session_records() + [{"event": "act_result", "seq": 1, "outcome": "passed"}]
    event_bytes = ending.join(map(encoded, events)) + ending
    step_bytes = encoded({"seq": 1, "result": {"outcome": "passed", "note": "synthetic"}}) + b"\xf0\x9f"
    (tmp_path / "events.jsonl").write_bytes(event_bytes)
    (tmp_path / "steps.jsonl").write_bytes(step_bytes)
    for _ in range(2):
        trail = session.trail(tmp_path)
        item = next(it for it in trail["items"] if it.get("seq") == 1)
        # A real complete result event is retained; the truncated detail record is not evidence.
        assert item["kind"] == "act" and item["outcome"] == "passed" and item["record"] is None
        assert trail["actions"] == 1 and set(trail["truncated_tail"]) == {"steps"}
        blocker = session.append_blocker(trail)
        assert blocker["status"] == "session_log_incomplete" and blocker["files"] == ["steps.jsonl"]
        assert blocker["unresolved_actions"] == []
        assert (tmp_path / "events.jsonl").read_bytes() == event_bytes
        assert (tmp_path / "steps.jsonl").read_bytes() == step_bytes
