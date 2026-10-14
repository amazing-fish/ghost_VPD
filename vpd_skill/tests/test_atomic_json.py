"""Transient Windows file locks must not abort otherwise valid evidence writes."""
import json
from pathlib import Path

import pytest

from vpdtest import util


def _denied(code):
    error = PermissionError("synthetic file lock")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
def test_transient_windows_lock_preserves_old_file_until_atomic_success(tmp_path, monkeypatch, code):
    target = tmp_path / "tree.json"
    target.write_text('{"old": true}', encoding="utf-8")
    replace = util.os.replace
    calls = []

    def locked(source, destination):
        calls.append((source, destination))
        if len(calls) < 3:
            assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
            raise _denied(code)
        return replace(source, destination)

    monkeypatch.setattr(util.os, "replace", locked)
    result = util.write_json(target, {"new": "现场"})
    assert result == target and json.loads(target.read_text(encoding="utf-8")) == {"new": "现场"}
    assert len(calls) == 3 and len({s for s, _ in calls}) == 1
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("code,attempts", [(5, 5), (87, 1), (None, 1)])
def test_persistent_or_other_permission_error_is_not_hidden(tmp_path, monkeypatch, code, attempts):
    target = tmp_path / "run.json"
    original = '{"original": true}'
    target.write_text(original, encoding="utf-8")
    error, calls = _denied(code), []

    def denied(source, destination):
        calls.append(source)
        raise error

    monkeypatch.setattr(util.os, "replace", denied)
    with pytest.raises(PermissionError) as caught:
        util.write_json(target, {"new": True})
    assert caught.value is error and len(calls) == attempts
    assert target.read_text(encoding="utf-8") == original
    assert json.loads(Path(calls[0]).read_text(encoding="utf-8")) == {"new": True}


def test_exclusive_evidence_still_refuses_overwrite_without_replace(tmp_path, monkeypatch):
    target = tmp_path / "observation.json"
    util.write_json(target, {"first": True}, exclusive=True)

    def forbidden(*args):
        pytest.fail("exclusive evidence must not use replacement or retries")

    monkeypatch.setattr(util.os, "replace", forbidden)
    with pytest.raises(FileExistsError):
        util.write_json(target, {"second": True}, exclusive=True)
    assert json.loads(target.read_text(encoding="utf-8")) == {"first": True}
