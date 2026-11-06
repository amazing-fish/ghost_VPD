"""Synthetic CLI-to-capture contracts; no phone observations or device metadata."""
import json
import pytest

from test_goal_loop import _lab_world, _start, cli
from test_transient_capture import install_frames
from vpdtest import transient
from vpdtest.session import from_session


@pytest.mark.parametrize("ready", [0.05, 1.5, 10])
def test_cli_ready_timeout_reaches_source_and_round_trips(verdict_ws, tmp_path, monkeypatch, ready):
    spec = _lab_world(tmp_path)
    _start(verdict_ws, spec, "ready-cli")
    install_frames(monkeypatch)
    actual_start = transient.TransientCapture.start
    received = []

    def start(capture):
        received.append(capture.ready_timeout_s)
        return actual_start(capture)

    monkeypatch.setattr(transient.TransientCapture, "start", start)
    out = cli(verdict_ws, "act", "tap", "--app", "motor_hm", "--session", "ready-cli",
              "--device", spec, "--select", "text=唯一入口", "--expect-text", "详情页",
              "--capture-transient", "0.05", "--capture-ready-timeout", str(ready), "--timeout", "0")
    assert out["dispatched"] is True and out["result"]["outcome"] == "passed"
    assert received == [ready]
    cap = out["result"]["attempts"][0]["transient"]
    assert cap["post_s"] == 0.05 and cap["trigger"]["sent"] is True
    candidate = from_session(verdict_ws.runs_dir / "session-ready-cli", "TC-READY-CLI",
                             verdict_ws.app("motor_hm"), "synthetic capture option transport")
    assert candidate["steps"][1]["capture_transient"] == {
        "post_s": 0.05, "ready_timeout_s": ready}


def test_omitted_ready_timeout_keeps_existing_default(verdict_ws, tmp_path, monkeypatch):
    spec = _lab_world(tmp_path)
    _start(verdict_ws, spec, "default-ready")
    install_frames(monkeypatch)
    received = []
    actual_start = transient.TransientCapture.start

    def start(capture):
        received.append(capture.ready_timeout_s)
        return actual_start(capture)

    monkeypatch.setattr(transient.TransientCapture, "start", start)
    cli(verdict_ws, "act", "tap", "--app", "motor_hm", "--session", "default-ready",
        "--device", spec, "--select", "text=唯一入口", "--capture-transient", "0.05", "--timeout", "0")
    assert received == [5]
    candidate = from_session(verdict_ws.runs_dir / "session-default-ready", "TC-READY-DEFAULT",
                             verdict_ws.app("motor_hm"), "synthetic default transport")
    assert candidate["steps"][1]["capture_transient"] == {"post_s": 0.05}


@pytest.mark.parametrize("options", [
    ["--capture-ready-timeout", "1"],
    ["--capture-transient", "0.05", "--capture-ready-timeout", "0"],
    ["--capture-transient", "0.05", "--capture-ready-timeout", "-1"],
    ["--capture-transient", "0.05", "--capture-ready-timeout", "10.01"],
    ["--capture-transient", "0.05", "--capture-ready-timeout", "nan"],
    ["--capture-transient", "0.05", "--capture-ready-timeout", "inf"],
])
def test_bad_ready_options_fail_before_device_or_session(verdict_ws, monkeypatch, options):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid capture options must not open a device")

    monkeypatch.setattr(verdict_ws, "device", forbidden)
    with pytest.raises(ValueError, match="capture|ready_timeout"):
        cli(verdict_ws, "act", "tap", "--app", "motor_hm", "--session", "invalid-ready",
            "--device", "hdc:never-contact", "--select", "text=synthetic", *options)
    assert not (verdict_ws.runs_dir / "session-invalid-ready").exists()


def test_non_tap_capture_options_fail_before_device(verdict_ws, monkeypatch):
    monkeypatch.setattr(verdict_ws, "device", lambda *a, **kw: pytest.fail("must not open device"))
    with pytest.raises(ValueError, match="tap"):
        cli(verdict_ws, "act", "back", "--app", "motor_hm", "--session", "invalid-action",
            "--device", "hdc:never-contact", "--capture-transient", "0.05",
            "--capture-ready-timeout", "1")


def test_explicit_ready_timeout_does_not_allow_tap_without_first_frame(verdict_ws, tmp_path, monkeypatch):
    spec = _lab_world(tmp_path)
    _start(verdict_ws, spec, "not-ready")
    install_frames(monkeypatch, initial=False)
    out = cli(verdict_ws, "act", "tap", "--app", "motor_hm", "--session", "not-ready",
              "--device", spec, "--select", "text=唯一入口", "--capture-transient", "0.05",
              "--capture-ready-timeout", "0.05", "--timeout", "0")
    assert out["dispatched"] is False and out["action_status"] == "transient_unavailable"
    assert len(out["result"]["attempts"]) == 1 and out["budget"]["used"] == 1
    assert out["result"]["attempts"][0]["transient"]["trigger"] is None


def test_invalid_ready_option_has_cli_error_exit_without_device(verdict_ws, monkeypatch, capsys):
    from vpdtest import cli as module

    monkeypatch.setattr(module, "Workspace", lambda root: verdict_ws)
    monkeypatch.setattr(verdict_ws, "device", lambda *a, **kw: pytest.fail("must not open device"))
    code = module.main(["act", "tap", "--select", "text=synthetic", "--device", "hdc:never-contact",
                        "--capture-transient", "0.05", "--capture-ready-timeout", "11"])
    assert code == 3
    assert json.loads(capsys.readouterr().out)["error"] == "ValueError"
