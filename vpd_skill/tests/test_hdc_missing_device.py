"""Observed HDC E001005 transport absence, tested without an HDC process/device."""
from types import SimpleNamespace
import subprocess

import pytest

from vpdtest.commands import cmd_doctor
from vpdtest.devices.harmony_hdc import HdcDevice, classify_output

RAW = "[Fail][E001005] Device not found or connected\r\r\n"


@pytest.mark.parametrize("raw", [RAW, RAW.lower(), "[Fail] [E001005] Device not found or connected\n"])
def test_explicit_hdc_missing_device_is_offline(raw):
    assert classify_output(raw) == "device_offline"


@pytest.mark.parametrize("raw,expected", [
    ("[Fail]Connect failed", "command_failed"),
    ("[Fail][E001006] Device not found or connected", "command_failed"),
    ("Device not found or connected", None),
    ("E001005 appears in a normal application message", None),
    ("[Fail]Not match target founded, check connect-key please", "device_offline"),
    ("[Fail][E000004] Communication channel is being established", "transport_unavailable"),
    ("error: screen is locked", "screen_locked"),
    ("start ability successfully.", None),
])
def test_other_failures_and_success_keep_their_meaning(raw, expected):
    assert classify_output(raw) == expected


@pytest.mark.parametrize("returncode", [0, 1])
def test_doctor_keeps_failed_queries_and_does_not_claim_device_online(monkeypatch, tmp_path, returncode):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, RAW.encode(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    device = HdcDevice("synthetic-disconnected", hdc="never-executed-hdc")
    ws = SimpleNamespace(root=tmp_path / "data", runs_dir=tmp_path / "runs", config={},
                         app=lambda _: SimpleNamespace(bundle="synthetic.app"), device=lambda _: device)
    result = cmd_doctor(ws, SimpleNamespace(device="hdc:synthetic-disconnected", app="synthetic"))
    preflight = result["preflight"]
    checks = {check["check"]: check for check in preflight["checks"]}
    assert preflight["ok"] is False
    assert checks["device_online"]["ok"] is False
    assert checks["device_online"]["error_class"] == "device_offline"
    assert RAW.strip() in checks["device_online"]["detail"]
    for key in ("screen_awake", "screen_unlocked", "app_installed", "display_info"):
        assert checks[key]["ok"] is False
        assert checks[key]["execution_error"]["error_class"] == "device_offline"
        assert checks[key]["execution_error"]["raw"] == RAW
    assert preflight["app_version"] is None
    assert preflight["device"]["model"] is None
    assert checks["screen_awake"]["value"] is None
    assert checks["screen_unlocked"]["value"] is None
    assert calls and all(cmd[0] == "never-executed-hdc" for cmd in calls)
    assert all("shell" in cmd for cmd in calls)
