"""Public CLI continuation of synthetic sessions; never contact a physical device."""
import json

import pytest

from test_goal_loop import _lab_world, cli
from vpdtest.cli import main
from vpdtest.session import trail
from vpdtest.util import append_jsonl, read_jsonl
from vpdtest.workspace import Workspace


def command(ws, capsys, *args):
    code = main(["--workspace", str(ws.root), *args])
    return code, json.loads(capsys.readouterr().out)


def prepared_session(ws, tmp_path, *, incomplete=False):
    spec = _lab_world(tmp_path)
    cli(ws, "observe", "--session", "integrity", "--device", spec)
    session = ws.runs_dir / "session-integrity"
    if incomplete:
        append_jsonl(session / "events.jsonl", {
            "ts": "synthetic", "event": "act_request", "seq": 1,
            "step_id": "A1", "request": {"action": "tap"},
        })
    return spec, session


@pytest.mark.parametrize("filename", ["events.jsonl", "steps.jsonl"])
@pytest.mark.parametrize("tail", [b'{"seq": 1, "unfinished":', b'{"note":"\xe5\xb7'])
@pytest.mark.parametrize("action", ["observe", "act"])
@pytest.mark.parametrize("incomplete", [False, True])
def test_truncated_session_refuses_writes_before_device_and_stays_readable(
        ws, tmp_path, monkeypatch, capsys, filename, tail, action, incomplete):
    spec, session = prepared_session(ws, tmp_path, incomplete=incomplete)
    with (session / filename).open("ab") as stream:
        stream.write(tail)
    originals = {p.name: p.read_bytes() for p in session.iterdir() if p.is_file()}
    original_device = Workspace.device
    devices = []

    def device(self, *args, **kwargs):
        devices.append(args)
        return original_device(self, *args, **kwargs)

    monkeypatch.setattr(Workspace, "device", device)
    argv = [action] + (["launch"] if action == "act" else [])
    for _ in range(2):
        code, out = command(ws, capsys, *argv, "--session", "integrity", "--device", spec)
        assert code == 2 and out["status"] == "session_log_incomplete"
        assert out["action_required"] and out["dispatched"] is False
        assert out["files"] == [filename]
        assert out["unresolved_actions"] == ([{"seq": 1, "step": "A1"}] if incomplete else [])
        assert not devices
        assert {name: (session / name).read_bytes() for name in originals} == originals
    code, out = command(ws, capsys, "session", "integrity")
    assert code == 0 and out["truncated_tail"]
    assert out["write_blocker"]["status"] == "session_log_incomplete"
    tr = trail(session)
    assert tr["actions"] == (1 if incomplete else 0)
    if incomplete:
        assert any(x["kind"] == "incomplete" and x["effect_unknown"] for x in tr["items"])


@pytest.mark.parametrize("filename", ["events.jsonl", "steps.jsonl"])
@pytest.mark.parametrize("ending", [b"", b"\r", b"\r\n", b"\n"])
def test_complete_last_record_retains_boundary_and_normal_cli_continues(
        ws, tmp_path, capsys, filename, ending):
    spec, session = prepared_session(ws, tmp_path)
    cli(ws, "act", "launch", "--session", "integrity", "--device", spec)
    path = session / filename
    original = path.read_bytes().rstrip(b"\r\n") + ending
    path.write_bytes(original)
    assert "truncated_tail" not in trail(session)
    code, out = command(ws, capsys, "act", "back", "--session", "integrity", "--device", spec)
    assert code == 0 and out["result"]["outcome"].startswith("passed")
    assert path.read_bytes().startswith(original)
    assert len(read_jsonl(path)) >= 2
    code, out = command(ws, capsys, "session", "integrity")
    assert code == 0 and "write_blocker" not in out and "truncated_tail" not in out


@pytest.mark.parametrize("bad", [b'{"broken": tru\n', b'{"note":"\xe5\xb7"}\n'])
def test_corrupted_middle_record_still_errors_before_device(ws, tmp_path, monkeypatch, capsys, bad):
    spec, session = prepared_session(ws, tmp_path)
    path = session / "events.jsonl"
    data = path.read_bytes() + bad + b'{"ts":"t","event":"synthetic"}\n'
    path.write_bytes(data)
    calls = []
    original_device = Workspace.device

    def device(self, *args, **kwargs):
        calls.append(args)
        return original_device(self, *args, **kwargs)

    monkeypatch.setattr(Workspace, "device", device)
    code, out = command(ws, capsys, "observe", "--session", "integrity", "--device", spec)
    assert code == 3 and out["error"] in ("JSONDecodeError", "UnicodeDecodeError")
    assert not calls and path.read_bytes() == data


def test_append_preserves_non_ascii_record_without_final_newline(tmp_path):
    path = tmp_path / "records.jsonl"
    original = '{"text":"合成中文"}'.encode("utf-8")
    path.write_bytes(original)
    append_jsonl(path, {"text": "后继记录"})
    assert path.read_bytes().startswith(original)
    assert read_jsonl(path) == [{"text": "合成中文"}, {"text": "后继记录"}]
