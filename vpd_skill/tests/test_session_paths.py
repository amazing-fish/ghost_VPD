"""Session names are directory identities, not caller-controlled paths. No devices."""
import json
import os
import subprocess

import pytest

from test_goal_loop import _lab_world
from vpdtest import commands
from vpdtest.cli import main
from vpdtest.workspace import Workspace


def command(ws, capsys, *args):
    code = main(["--workspace", str(ws.root), *args])
    return code, json.loads(capsys.readouterr().out)


def entrypoint(kind, name, spec="replay:unused"):
    if kind == "session":
        return ["session", name]
    if kind == "from-session":
        return ["case", "from-session", "--session", name, "--new-id", "path-candidate", "--app", "motor_hm", "--goal", "synthetic"]
    args = (["observe"] if kind == "observe" else ["act", "launch"] if kind == "act" else
            ["act", "swipe", "--within", "target=unregistered", "--start", "0.5,0.8", "--end", "0.5,0.2"])
    return args + ["--session", name, "--device", spec]


KINDS = ["observe", "act", "relative-swipe", "session", "from-session"]
BAD_NAMES = ["", "foo/../../escape", "alias/../other", "a/b", "a\\b", "/absolute", "C:drive",
             "bad\nname", "bad\x7fname", "bad\x85name", "trailing.", "trailing ", 'bad"name',
             "bad*name", "bad?name", "bad<name", "bad>name", "bad|name"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", BAD_NAMES)
def test_invalid_names_rejected_before_session_io_or_device(ws, capsys, monkeypatch, kind, name):
    def forbidden(*args, **kwargs):
        pytest.fail("session read, lock, or device reached before name validation")

    monkeypatch.setattr(commands, "trail", forbidden)
    monkeypatch.setattr(commands, "session_lock", forbidden)
    monkeypatch.setattr(commands, "from_session", forbidden)
    monkeypatch.setattr(Workspace, "device", forbidden)
    code, out = command(ws, capsys, *entrypoint(kind, name))
    assert code == 3 and out["error"] == "ValueError"
    assert "session" in out["message"]
    assert not ws.runs_dir.exists() or not list(ws.runs_dir.iterdir())
    assert not ws.case_path("path-candidate").exists()


@pytest.mark.parametrize("name", ["regular-1", "会话测试_α", "internal space"])
def test_normal_names_support_observe_act_read_and_extract(ws, tmp_path, capsys, name):
    spec = _lab_world(tmp_path)
    for kind in ["observe", "act", "session", "from-session"]:
        code, out = command(ws, capsys, *entrypoint(kind, name, spec))
        assert code == 0, out
    assert (ws.runs_dir / f"session-{name}" / "events.jsonl").is_file()
    assert ws.case_path("path-candidate").is_file()


def symlink(source, target):
    try:
        source.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("target_kind", ["inside", "outside", "dangling"])
def test_session_directory_symlinks_rejected_without_read_or_write(
        ws, tmp_path, capsys, monkeypatch, kind, target_kind):
    ws.runs_dir.mkdir(exist_ok=True)
    target = (ws.runs_dir / "session-existing" if target_kind == "inside" else tmp_path / "outside")
    if target_kind != "dangling":
        target.mkdir()
        (target / "events.jsonl").write_bytes(b"synthetic untouched evidence")
    alias = ws.runs_dir / "session-alias"
    symlink(alias, target)

    def forbidden(*args, **kwargs):
        pytest.fail("alias followed before session directory validation")

    monkeypatch.setattr(commands, "trail", forbidden)
    monkeypatch.setattr(commands, "session_lock", forbidden)
    monkeypatch.setattr(Workspace, "device", forbidden)
    code, out = command(ws, capsys, *entrypoint(kind, "alias"))
    assert code == 3 and out["error"] == "ValueError"
    assert alias.is_symlink()
    if target_kind == "dangling":
        assert not target.exists()
    else:
        assert list(target.iterdir()) == [target / "events.jsonl"]
        assert (target / "events.jsonl").read_bytes() == b"synthetic untouched evidence"
    assert not ws.case_path("path-candidate").exists()


def test_configured_runs_root_may_be_a_symlink(ws, tmp_path, capsys):
    target = tmp_path / "mapped-runs"
    target.mkdir()
    symlink(ws.runs_dir, target)
    code, out = command(ws, capsys, *entrypoint("observe", "normal", _lab_world(tmp_path)))
    assert code == 0, out
    assert (target / "session-normal" / "events.jsonl").is_file()


@pytest.mark.parametrize("kind", KINDS)
def test_existing_regular_file_is_not_a_session_directory(ws, capsys, kind):
    ws.runs_dir.mkdir(exist_ok=True)
    path = ws.runs_dir / "session-file"
    path.write_bytes(b"not a directory")
    code, out = command(ws, capsys, *entrypoint(kind, "file"))
    assert code == 3 and out["error"] == "ValueError"
    assert path.read_bytes() == b"not a directory"


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction")
def test_windows_junction_alias_is_rejected_by_all_session_entries(ws, tmp_path, capsys):
    ws.runs_dir.mkdir(exist_ok=True)
    target = tmp_path / "junction-target"
    target.mkdir()
    marker = target / "events.jsonl"
    marker.write_bytes(b"synthetic untouched evidence")
    alias = ws.runs_dir / "session-junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)], capture_output=True)
    if result.returncode:
        pytest.skip(f"junction creation unavailable: exit {result.returncode}")
    try:
        assert alias.is_junction()
        for kind in KINDS:
            code, out = command(ws, capsys, *entrypoint(kind, "junction"))
            assert code == 3 and out["error"] == "ValueError"
        assert list(target.iterdir()) == [marker]
        assert marker.read_bytes() == b"synthetic untouched evidence"
        assert not ws.case_path("path-candidate").exists()
    finally:
        alias.rmdir()  # Remove only the synthetic junction, never its target contents.
