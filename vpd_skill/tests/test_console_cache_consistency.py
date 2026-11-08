"""Synthetic cache rewrites/races: full raw-prefix validation, incremental parsing."""
import copy
import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from vpdtest.console import progress


def records(*events):
    return b"".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")).encode() + b"\n" for e in events)


def run_bytes(case="OLD", verdict="passed"):
    return records({"event": "run_start", "case": case, "steps": ["S1"]},
                   {"event": "step_done", "step": "S1", "outcome": verdict},
                   {"event": "run_end", "verdict": verdict})


def make_file(root, data):
    directory = root / "run-synthetic"
    directory.mkdir()
    file = directory / "events.jsonl"
    file.write_bytes(data)
    return file


def append_bytes(file, data):
    with file.open("ab") as stream:
        stream.write(data)


def key(file):
    return str(file.absolute())


def stat_with(info, **changes):
    values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
    return SimpleNamespace(**(values | changes))


class ObservedReader:
    """Wrap a real descriptor, observing bounded reads and injecting deterministic faults."""

    def __init__(self, stream, reads, after_read=None):
        self.stream, self.reads, self.after_read = stream, reads, after_read

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def read(self, size):
        assert 0 < size <= progress._READ_CHUNK
        data = self.stream.read(size)
        self.reads.append((size, len(data)))
        if self.after_read:
            return self.after_read(self, data)
        return data


def observe_reads(monkeypatch, file, after_read=None):
    original = Path.open
    reads, opened = [], []

    def open_file(self, mode="r", *args, **kwargs):
        stream = original(self, mode, *args, **kwargs)
        if self == file and mode == "rb":
            stream = ObservedReader(stream, reads, after_read)
            opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_file)
    return reads, opened


def assert_summary_matches_fresh(file):
    before = file.read_bytes()
    cached = progress.summarize(file.parent)
    scan = progress._SCANS.pop(key(file))
    fresh = progress.summarize(file.parent)
    assert cached == fresh
    assert file.read_bytes() == before
    return cached, scan


def test_same_inode_growing_rewrite_replaces_false_success(tmp_path):
    original = run_bytes()
    replacement = run_bytes("NEW", "failed") + records({"event": "observe", "state": "home"})
    assert (len(original), len(replacement)) == (142, 177)
    file = make_file(tmp_path, original)
    old = progress._scan(file)
    inode = file.stat().st_ino
    file.write_bytes(replacement)
    assert file.stat().st_ino == inode
    result, scan = assert_summary_matches_fresh(file)
    assert scan is not old
    assert (result["title"], result["last_outcome"], result["verdict"], result["events"]) == \
        ("NEW", "failed", "failed", 4)
    assert "read_errors" not in result


def test_rewrite_discards_every_old_aggregate_and_pending_bytes(tmp_path):
    original = run_bytes() + records(
        {"event": "case_start", "case": "OLD"}, {"event": "case_done", "verdict": "passed"},
        {"event": "motion_armed"}, {"event": "observe", "state": "OLD"}, {"event": "suite_end"},
    ) + b'[]\n{"event":"observe","state":"unfinished'
    file = make_file(tmp_path, original)
    old = progress._scan(file)
    before = copy.deepcopy(old.__dict__)
    new_event = {"event": "run_start", "case": "NEW", "pad": "x" * len(original)}
    replacement = records(new_event)
    file.write_bytes(replacement)
    result, scan = assert_summary_matches_fresh(file)
    assert old.__dict__ == before and scan is not old
    assert scan.head == new_event and scan.tail == [new_event] and scan.count == 1
    assert scan.invalid_lines == scan.steps_done == scan.cases_done == 0
    assert scan.pending == b"" and scan.verdicts == scan.ended == {}
    assert scan.current_case is scan.last_step_event is scan.last_outcome is None
    assert scan.motion is scan.last_obs_state is None
    assert result["title"] == "NEW" and not result["finished"] and result["verdict"] is None
    assert "read_errors" not in result


@pytest.mark.parametrize("grow", [False, True])
@pytest.mark.parametrize("unchanged_ctime", [False, True])
def test_middle_rewrite_detected_even_with_preserved_metadata(tmp_path, monkeypatch, grow, unchanged_ctime):
    head = records({"event": "run_start", "case": "SAME", "pad": "x" * 9000})
    middle = records({"event": "step_done", "outcome": "passed"})
    tail = records({"event": "observe", "state": "home", "pad": "y" * 9000})
    original = head + middle + tail
    file = make_file(tmp_path, original)
    old_stat = file.stat()
    old = progress._scan(file)
    replacement = head + middle.replace(b"passed", b"failed") + tail
    if grow:
        replacement += records({"event": "observe", "state": "next"})
    file.write_bytes(replacement)
    os.utime(file, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    assert file.stat().st_ino == old_stat.st_ino
    assert file.stat().st_mtime_ns == old_stat.st_mtime_ns
    assert replacement[:4096] == original[:4096]
    assert replacement[len(original) - 4096:len(original)] == original[-4096:]
    if unchanged_ctime:
        # Model Windows creation-time ctime or a coarse clock: no metadata-only shortcut.
        original_fstat, original_stat = os.fstat, Path.stat
        monkeypatch.setattr(os, "fstat", lambda fd: stat_with(original_fstat(fd), st_ctime_ns=old_stat.st_ctime_ns))

        def unchanged_stat(self, *args, **kwargs):
            info = original_stat(self, *args, **kwargs)
            return stat_with(info, st_ctime_ns=old_stat.st_ctime_ns) if self == file else info

        monkeypatch.setattr(Path, "stat", unchanged_stat)
    result, scan = assert_summary_matches_fresh(file)
    assert scan is not old and result["last_outcome"] == "failed"
    assert "read_errors" not in result


def test_pending_raw_bytes_are_part_of_verified_prefix(tmp_path):
    prefix = records({"event": "run_start", "case": "SAME"})
    file = make_file(tmp_path, prefix + b'{"event":"observe","state":"OLD')
    old = progress._scan(file)
    assert old.pending.endswith(b"OLD") and old.count == 1
    replacement = prefix + records({"event": "observe", "state": "NEW"})
    file.write_bytes(replacement)
    scan = progress._scan(file)
    assert scan is not old and scan.last_obs_state == "NEW" and scan.invalid_lines == 0
    assert scan.digest == hashlib.sha256(replacement).digest()
    assert file.read_bytes() == replacement


def test_every_poll_reads_full_prefix_but_only_parses_new_completed_records(tmp_path, monkeypatch):
    data = records({"event": "run_start", "case": "SAME"}) + b'{"event":"observe","state":"\xe5'
    file = make_file(tmp_path, data)
    original_load = progress._load_record
    parsed = []

    def load(raw):
        parsed.append(raw)
        return original_load(raw)

    monkeypatch.setattr(progress, "_load_record", load)
    monkeypatch.setattr(progress, "_READ_CHUNK", 7)
    reads, opened = observe_reads(monkeypatch, file)
    scan = progress._scan(file)
    assert len(parsed) == 1 and sum(count for _, count in reads) == len(data)
    assert scan.digest == hashlib.sha256(data).digest()
    for extra, parse_count in [(b"", 0), (b'\x90\x88"}\r', 1), (b'\n', 0), (records({"event": "run_end"}), 1)]:
        append_bytes(file, extra)
        data += extra
        reads.clear()
        parsed.clear()
        assert progress._scan(file) is scan
        assert sum(count for _, count in reads) == len(data)
        assert len(parsed) == parse_count
        assert scan.offset == len(data) and scan.digest == hashlib.sha256(data).digest()
    assert scan.count == 3 and scan.last_obs_state == "合" and scan.pending == b""
    assert all(reader.closed for reader in opened)
    monkeypatch.undo()  # Evidence verification is not part of the instrumented reader call.
    assert file.read_bytes() == data


def test_rewrite_cost_is_prefix_validation_then_one_full_parse(tmp_path, monkeypatch):
    original = run_bytes()
    file = make_file(tmp_path, original)
    progress._scan(file)
    replacement = run_bytes("NEW", "failed") + records({"event": "observe"})
    file.write_bytes(replacement)
    parsed = []
    original_load = progress._load_record

    def load(raw):
        parsed.append(raw)
        return original_load(raw)

    monkeypatch.setattr(progress, "_load_record", load)
    reads, _ = observe_reads(monkeypatch, file)
    scan = progress._scan(file)
    assert sum(count for _, count in reads) == len(original) + len(replacement)
    assert len(parsed) == scan.count == 4
    assert scan.digest == hashlib.sha256(replacement).digest()


def test_replacement_before_open_uses_same_descriptor_identity_and_bytes(tmp_path, monkeypatch):
    file = make_file(tmp_path, run_bytes())
    old = progress._scan(file)
    replacement = file.with_name("replacement")
    data = run_bytes("NEW", "failed") + records({"event": "observe"})
    replacement.write_bytes(data)
    original_open, original_stat, original_fstat = Path.open, Path.stat, os.fstat
    calls, descriptors = [], []

    def open_file(self, *args, **kwargs):
        if self == file:
            calls.append("open")
            replacement.replace(file)
            stream = original_open(self, *args, **kwargs)
            descriptors.append(stream)
            return stream
        return original_open(self, *args, **kwargs)

    def fstat(fd):
        calls.append("fstat")
        assert fd == descriptors[0].fileno()
        return original_fstat(fd)

    def path_stat(self, *args, **kwargs):
        if self == file:
            calls.append("stat")
        return original_stat(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_file)
        patch.setattr(Path, "stat", path_stat)
        patch.setattr(os, "fstat", fstat)
        scan = progress._scan(file)
    assert calls == ["stat", "open", "fstat", "stat", "fstat", "stat"]
    assert scan is not old and scan.head["case"] == "NEW" and scan.last_outcome == "failed"
    assert scan.identity == (file.stat().st_dev, file.stat().st_ino)
    assert descriptors[0].closed and file.read_bytes() == data


def test_recycled_identity_still_checks_content(tmp_path, monkeypatch):
    file = make_file(tmp_path, run_bytes())
    old = progress._scan(file)
    replacement = file.with_name("replacement")
    data = run_bytes("NEW", "failed") + records({"event": "observe"})
    replacement.write_bytes(data)
    replacement.replace(file)
    original_fstat, original_stat = os.fstat, Path.stat
    changes = {"st_dev": old.identity[0], "st_ino": old.identity[1]}
    monkeypatch.setattr(os, "fstat", lambda fd: stat_with(original_fstat(fd), **changes))

    def recycled_stat(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        return stat_with(info, **changes) if self == file else info

    monkeypatch.setattr(Path, "stat", recycled_stat)
    result, scan = assert_summary_matches_fresh(file)
    assert scan is not old and result["title"] == "NEW" and result["verdict"] == "failed"


def test_path_replacement_after_open_rejects_candidate(tmp_path, monkeypatch):
    file = make_file(tmp_path, run_bytes())
    old = progress._scan(file)
    before = copy.deepcopy(old.__dict__)
    other = file.with_name("replacement")
    other.write_bytes(run_bytes("NEW", "failed"))
    other_stat = other.stat()
    original_stat = Path.stat

    def replaced_path(self, *args, **kwargs):
        # Portable model of a pathname now resolving to a different opened generation.
        return other_stat if self == file else original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", replaced_path)
    with pytest.raises(OSError, match="events changed"):
        progress._scan(file)
    assert progress._SCANS[key(file)] is old and old.__dict__ == before


@pytest.mark.parametrize("failure", ["read", "short_read", "append", "fstat", "path_stat"])
def test_failed_candidate_preserves_verified_cache_and_incremental_recovery(tmp_path, monkeypatch, failure):
    data = records({"event": "run_start", "case": "SAME"}, {"event": "case_done", "verdict": "passed"})
    data += b'{"event":"observe","state":"'
    file = make_file(tmp_path, data)
    old = progress._scan(file)
    before = copy.deepcopy(old.__dict__)
    suffix = b'NEW"}\n' + records({"event": "case_done", "verdict": "failed"},
                                {"event": "run_end", "verdict": "failed"}) + b"[]\n" + b" " * 200
    append_bytes(file, suffix)
    initial_size = len(data) + len(suffix)
    original_feed, original_fstat, original_stat = progress._Scan.feed, os.fstat, Path.stat
    parsed, failed = [], False

    def feed(candidate, event):
        assert candidate is not old
        assert old.__dict__ == before  # Never mutate the published aggregate during parsing.
        assert candidate.tail is not old.tail and candidate.verdicts is not old.verdicts
        assert candidate.ended is not old.ended
        parsed.append(event)
        return original_feed(candidate, event)

    def after_read(reader, chunk):
        nonlocal failed
        if reader.tell() > old.offset + 150 and not failed:
            failed = True
            if failure == "read":
                raise OSError("synthetic read failure")
            if failure == "short_read":
                return b""
            if failure == "append":
                append_bytes(file, records({"event": "observe", "state": "late"}))
        return chunk

    fstat_calls = 0

    def fstat(fd):
        nonlocal fstat_calls
        fstat_calls += 1
        if failure == "fstat" and fstat_calls == 2:
            raise OSError("synthetic fstat failure")
        return original_fstat(fd)

    path_stat_calls = 0

    def path_stat(self, *args, **kwargs):
        nonlocal path_stat_calls
        if self == file:
            path_stat_calls += 1
            if failure == "path_stat" and path_stat_calls > 2:
                raise PermissionError("synthetic path stat failure")
        return original_stat(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(progress, "_READ_CHUNK", 64)
        patch.setattr(progress._Scan, "feed", feed)
        patch.setattr(os, "fstat", fstat)
        patch.setattr(Path, "stat", path_stat)
        reads, opened = observe_reads(patch, file, after_read)
        result = progress.summarize(file.parent)
        assert result["state"] == "unknown" and result["progress_reliable"] is False
        assert {"source": "events.jsonl", "reason": "unreadable"} in result["read_errors"]
        assert result["verdict"] is None and result["events"] == 0
        assert len(opened) == 1 and opened[0].closed  # No automatic retry or persistent handle.
        assert sum(count for _, count in reads) <= initial_size
    assert parsed and progress._SCANS[key(file)] is old and old.__dict__ == before
    current = file.read_bytes()
    parsed.clear()
    original_load = progress._load_record

    def load(raw):
        parsed.append(raw)
        return original_load(raw)

    monkeypatch.setattr(progress, "_load_record", load)
    recovered = progress._scan(file)
    assert recovered is old
    assert len(parsed) == 4 + (failure == "append")  # Three new events, one invalid record, optional late event.
    assert recovered.count == 5 + (failure == "append")
    assert recovered.verdicts == {"passed": 1, "failed": 1}
    assert recovered.invalid_lines == 1 and recovered.ended["run_end"]["verdict"] == "failed"
    assert recovered.digest == hashlib.sha256(current).digest()
    assert file.read_bytes() == current


def test_initial_scan_concurrent_append_is_bounded_and_not_published(tmp_path, monkeypatch):
    original = run_bytes()
    file = make_file(tmp_path, original)
    extra = records({"event": "observe", "state": "late"})

    def keep_appending(reader, data):
        append_bytes(file, extra)
        return data

    with monkeypatch.context() as patch:
        patch.setattr(progress, "_READ_CHUNK", 10)
        reads, opened = observe_reads(patch, file, keep_appending)
        result = progress.summarize(file.parent)
    assert result["state"] == "unknown" and key(file) not in progress._SCANS
    assert sum(count for _, count in reads) == len(original)
    assert len(opened) == 1 and opened[0].closed
    current = file.read_bytes()
    assert current == original + extra * len(reads)
    assert progress.summarize(file.parent)["events"] == 3 + len(reads)
    assert file.read_bytes() == current


def test_visible_truncation_during_read_retains_old_cache(tmp_path, monkeypatch):
    original = run_bytes()
    file = make_file(tmp_path, original)
    old = progress._scan(file)
    before = copy.deepcopy(old.__dict__)
    truncated = False

    def truncate(reader, data):
        nonlocal truncated
        if not truncated:
            truncated = True
            file.write_bytes(b"")
        return data

    with monkeypatch.context() as patch:
        patch.setattr(progress, "_READ_CHUNK", 16)
        observe_reads(patch, file, truncate)
        with pytest.raises(OSError):
            progress._scan(file)
    assert progress._SCANS[key(file)] is old and old.__dict__ == before
    assert file.read_bytes() == b""
    recovered = progress._scan(file)
    assert recovered is not old and recovered.count == 0 and recovered.ended == {}
    assert recovered.digest == hashlib.sha256(b"").digest()


def test_atime_changes_do_not_invalidate_read(tmp_path, monkeypatch):
    file = make_file(tmp_path, run_bytes())
    original_fstat, original_stat = os.fstat, Path.stat
    calls = 0

    def fstat(fd):
        nonlocal calls
        calls += 1
        return stat_with(original_fstat(fd), st_atime_ns=calls, st_atime=float(calls))

    def path_stat(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        return stat_with(info, st_atime_ns=999, st_atime=999.0) if self == file else info

    monkeypatch.setattr(os, "fstat", fstat)
    monkeypatch.setattr(Path, "stat", path_stat)
    scan = progress._scan(file)
    assert scan.count == 3 and progress._scan(file) is scan
    assert calls == 4


@pytest.mark.parametrize("failure", ["open", "initial_fstat", "prefix_read", "path_missing"])
def test_early_io_failure_never_serves_or_discards_verified_cache(tmp_path, monkeypatch, failure):
    data = run_bytes()
    file = make_file(tmp_path, data)
    old = progress._scan(file)
    before = copy.deepcopy(old.__dict__)
    original_open, original_stat = Path.open, Path.stat

    def unavailable_open(self, *args, **kwargs):
        if self == file:
            raise PermissionError("synthetic open denied")
        return original_open(self, *args, **kwargs)

    def unavailable_fstat(fd):
        raise OSError("synthetic initial fstat failure")

    def unavailable_read(reader, chunk):
        raise OSError("synthetic prefix read failure")

    path_stat_calls = 0

    def missing_path(self, *args, **kwargs):
        nonlocal path_stat_calls
        if self == file:
            path_stat_calls += 1
            if path_stat_calls > 1:
                raise FileNotFoundError("synthetic disappearance after open")
        return original_stat(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        if failure == "open":
            patch.setattr(Path, "open", unavailable_open)
        elif failure == "initial_fstat":
            patch.setattr(os, "fstat", unavailable_fstat)
        elif failure == "prefix_read":
            observe_reads(patch, file, unavailable_read)
        else:
            patch.setattr(Path, "stat", missing_path)
        result = progress.summarize(file.parent)
    assert result["state"] == "unknown" and result["verdict"] is None
    assert result["read_errors"] == [{"source": "events.jsonl", "reason": "unreadable"}]
    assert progress._SCANS[key(file)] is old and old.__dict__ == before
    assert progress._scan(file) is old and old.__dict__ == before
    assert file.read_bytes() == data


def test_failed_rebuild_does_not_discard_verified_cache(tmp_path, monkeypatch):
    file = make_file(tmp_path, run_bytes())
    old = progress._scan(file)
    before = copy.deepcopy(old.__dict__)
    replacement = run_bytes("NEW", "failed") + records({"event": "observe", "state": "new"})
    file.write_bytes(replacement)
    original_feed = progress._Scan.feed
    parsed = []

    def fail_during_rebuild(candidate, event):
        assert candidate is not old and old.__dict__ == before
        original_feed(candidate, event)
        parsed.append(event)
        raise OSError("synthetic rebuild failure")

    with monkeypatch.context() as patch:
        patch.setattr(progress._Scan, "feed", fail_during_rebuild)
        with pytest.raises(OSError):
            progress._scan(file)
    assert parsed and progress._SCANS[key(file)] is old and old.__dict__ == before
    rebuilt = progress._scan(file)
    assert rebuilt is not old and rebuilt.head["case"] == "NEW" and rebuilt.last_outcome == "failed"
    assert old.__dict__ == before and file.read_bytes() == replacement


def test_visible_equal_length_rewrite_during_initial_read_is_unknown(tmp_path, monkeypatch):
    file = make_file(tmp_path, run_bytes())
    original_mtime = file.stat().st_mtime_ns
    original_load = progress._load_record
    replacement = run_bytes("NEW", "failed")
    changed = False

    def rewrite(raw):
        nonlocal changed
        if not changed:
            changed = True
            file.write_bytes(replacement)
            os.utime(file, ns=(original_mtime + 1_000_000_000, original_mtime + 1_000_000_000))
        return original_load(raw)

    with monkeypatch.context() as patch:
        patch.setattr(progress, "_load_record", rewrite)
        result = progress.summarize(file.parent)
    assert result["state"] == "unknown" and key(file) not in progress._SCANS
    assert result["verdict"] is None and result["events"] == 0
    recovered = progress.summarize(file.parent)
    assert recovered["title"] == "NEW" and recovered["verdict"] == "failed"
    assert "read_errors" not in recovered and file.read_bytes() == replacement


@pytest.mark.parametrize("mode", [stat.S_IFIFO, stat.S_IFDIR, stat.S_IFCHR, stat.S_IFSOCK])
def test_non_regular_path_is_rejected_before_potentially_blocking_open(tmp_path, monkeypatch, mode):
    file = make_file(tmp_path, run_bytes())
    old = progress._scan(file)
    before = copy.deepcopy(old.__dict__)
    original_stat, original_open = Path.stat, Path.open

    def special_stat(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        return stat_with(info, st_mode=mode) if self == file else info

    def no_open(self, *args, **kwargs):
        assert self != file, "Special files must be rejected before open can block."
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", special_stat)
    monkeypatch.setattr(Path, "open", no_open)
    result = progress.summarize(file.parent)
    assert result["state"] == "unknown" and result["verdict"] is None
    assert result["read_errors"] == [{"source": "events.jsonl", "reason": "unreadable"}]
    assert progress._SCANS[key(file)] is old and old.__dict__ == before


def test_opened_descriptor_type_is_rechecked(tmp_path, monkeypatch):
    file = make_file(tmp_path, run_bytes())
    original_fstat = os.fstat
    monkeypatch.setattr(os, "fstat", lambda fd: stat_with(original_fstat(fd), st_mode=stat.S_IFCHR))
    reads, opened = observe_reads(monkeypatch, file)
    with pytest.raises(OSError, match="regular file"):
        progress._scan(file)
    assert not reads and len(opened) == 1 and opened[0].closed
    assert key(file) not in progress._SCANS


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO smoke test")
def test_real_fifo_is_rejected_without_opening_it(tmp_path, monkeypatch):
    directory = tmp_path / "run-fifo"
    directory.mkdir()
    file = directory / "events.jsonl"
    os.mkfifo(file)
    original_open = Path.open

    def no_open(self, *args, **kwargs):
        assert self != file, "Opening this FIFO without a writer would block."
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_open)
    result = progress.summarize(directory)
    assert result["state"] == "unknown" and result["events"] == 0
    assert result["read_errors"] == [{"source": "events.jsonl", "reason": "unreadable"}]


@pytest.mark.parametrize("ctime_difference", [100, 2_000_000_100])
def test_stable_path_and_fd_ctime_need_not_match(tmp_path, monkeypatch, ctime_difference):
    # CPython 3.12 Windows path stat returns CreationTime, fd fstat ChangeTime.
    # Their fractions or whole seconds may differ without any concurrent write.
    data = run_bytes()
    file = make_file(tmp_path, data)
    original_fstat, original_stat = os.fstat, Path.stat
    birthtime = original_stat(file).st_ctime_ns
    parsed, original_load = [], progress._load_record

    def path_stat(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        return stat_with(info, st_ctime_ns=birthtime) if self == file else info

    def fstat(fd):
        return stat_with(original_fstat(fd), st_ctime_ns=birthtime + ctime_difference)

    def load(raw):
        parsed.append(raw)
        return original_load(raw)

    monkeypatch.setattr(Path, "stat", path_stat)
    monkeypatch.setattr(os, "fstat", fstat)
    monkeypatch.setattr(progress, "_load_record", load)
    scan = progress._scan(file)
    assert scan.count == 3 and scan.last_outcome == "passed"
    parsed.clear()
    assert progress._scan(file) is scan and not parsed
    extra = records({"event": "observe", "state": "new"})
    append_bytes(file, extra)
    assert progress._scan(file) is scan and scan.count == 4 and len(parsed) == 1
    assert scan.digest == hashlib.sha256(data + extra).digest()
    assert file.read_bytes() == data + extra


@pytest.mark.parametrize("source", ["path", "fd"])
@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"])
def test_each_stat_api_still_rejects_its_own_metadata_change(tmp_path, monkeypatch, source, field):
    file = make_file(tmp_path, run_bytes())
    old = progress._scan(file)
    before = copy.deepcopy(old.__dict__)
    original_fstat, original_stat = os.fstat, Path.stat
    path_calls = fd_calls = 0
    birthtime = file.stat().st_ctime_ns

    def path_stat(self, *args, **kwargs):
        nonlocal path_calls
        info = original_stat(self, *args, **kwargs)
        if self != file:
            return info
        path_calls += 1
        info = stat_with(info, st_ctime_ns=birthtime)
        if source == "path" and path_calls == 3:  # Precheck, opened-path baseline, final path.
            info = stat_with(info, **{field: getattr(info, field) + 1})
        return info

    def fstat(fd):
        nonlocal fd_calls
        fd_calls += 1
        info = stat_with(original_fstat(fd), st_ctime_ns=birthtime + 100)
        if source == "fd" and fd_calls == 2:
            info = stat_with(info, **{field: getattr(info, field) + 1})
        return info

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", path_stat)
        patch.setattr(os, "fstat", fstat)
        with pytest.raises(OSError, match="events changed"):
            progress._scan(file)
    assert progress._SCANS[key(file)] is old and old.__dict__ == before
    assert progress._scan(file) is old


def test_real_stat_apis_accept_stable_files_after_writes(tmp_path):
    # Runs on the actual Windows/Linux CI hosts. Failure includes raw metadata,
    # rather than hiding a runtime-specific mismatch behind a generic OSError.
    file = make_file(tmp_path, run_bytes())
    for index in range(4):
        if index:
            append_bytes(file, records({"event": "observe", "state": str(index)}))
        os.utime(file, ns=(1_700_000_000_000_000_000, 1_700_000_000_123_456_700 + index * 100))
        before = file.stat()
        with file.open("rb") as stream:
            opened = os.fstat(stream.fileno())
        diagnostic = {"platform": os.name,
                      "path": progress._signature(before), "fd": progress._signature(opened),
                      "path_birthtime": getattr(before, "st_birthtime_ns", None),
                      "fd_birthtime": getattr(opened, "st_birthtime_ns", None)}
        assert os.path.samestat(before, opened), diagnostic
        try:
            scan = progress._scan(file)
        except OSError as exc:
            pytest.fail(f"Stable file was rejected: {exc}; metadata={diagnostic}")
        assert scan.count == 3 + index and scan.last_outcome == "passed", diagnostic
        assert scan.digest == hashlib.sha256(file.read_bytes()).digest(), diagnostic
