"""Explicit transport errors survive preflight without triggering PIN recovery."""
import subprocess

import pytest
from conftest import world
from verdict_fixtures import COPY_CASE
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.devices.replay import ReplayDevice
from vpdtest.runner import run_case


@pytest.mark.parametrize("operation", ["power_state", "lock_state", "bm_dump"])
def test_preflight_preserves_actual_query_failure(operation, monkeypatch):
    device = HdcDevice("synthetic-device")
    values = {"power_state": "Current State: AWAKE", "lock_state": "screenLocked false",
              "bm_dump": '"versionName": "1.0.0"'}

    def shell(*args, op):
        if op == operation:
            return DeviceResult(op, True, False, "transport_unavailable", "synthetic raw transport failure", "raw evidence")
        return DeviceResult(op, True, True, raw=values[op])

    monkeypatch.setattr(device, "_shell", shell)
    monkeypatch.setattr(device, "info", lambda: {"serial": device.serial})
    monkeypatch.setattr(device, "display_info", dict)
    pre = device.preflight("synthetic.app")
    assert not pre["ok"]
    errors = [c["execution_error"] for c in pre["checks"] if c.get("execution_error")]
    assert errors == [{"error_class": "transport_unavailable", "error": "synthetic raw transport failure", "raw": "raw evidence"}]


def test_query_failure_does_not_enter_unlock_recovery(verdict_ws, monkeypatch):
    original = ReplayDevice.preflight

    def preflight(device, bundle):
        pre = original(device, bundle)
        pre["ok"] = False
        pre["checks"] = [{"check": "screen_unlocked", "ok": False, "error_class": "screen_locked",
                          "execution_error": {"error_class": "timeout", "error": "synthetic lock query timeout"}}]
        return pre

    monkeypatch.setattr(ReplayDevice, "preflight", preflight)
    monkeypatch.setattr("vpdtest.unlock.unlock_device", lambda *a, **k: pytest.fail("must not unlock after query failure"))
    run = run_case(verdict_ws, COPY_CASE, device_spec=world("motor_hm_base.json"), propose=False)
    assert run["verdict"]["verdict"] == "error"
    error = run["verdict"]["execution"]["errors"][0]
    assert error["stage"] == "preflight" and error["error"] == "synthetic lock query timeout"
    assert all(s["outcome"] == "not_run" for s in run["steps"])


def test_os_error_returns_unknown_dispatch_certainty(monkeypatch):
    def fail(*a, **k):
        raise PermissionError("synthetic process start denied")

    monkeypatch.setattr("subprocess.run", fail)
    result = HdcDevice("synthetic-device")._run(["shell", "test"], "synthetic-op")
    assert result.ok is False and result.sent is None
    assert result.error_class == "tool_execution_error"
    assert result.error == "synthetic process start denied"


def test_timeout_keeps_partial_stdout_and_stderr(monkeypatch):
    def fail(*a, **k):
        raise subprocess.TimeoutExpired("synthetic", 1, output=b"partial stdout", stderr=b"partial stderr")

    monkeypatch.setattr("subprocess.run", fail)
    result = HdcDevice("synthetic-device")._run(["shell", "test"], "synthetic-op")
    assert result.error_class == "timeout" and result.sent is True
    assert result.raw == "partial stdoutpartial stderr"


def test_unlock_wake_execution_error_is_not_normal_input_handoff(verdict_ws, monkeypatch):
    monkeypatch.setattr(ReplayDevice, "wake", lambda *a: DeviceResult(
        "wake", True, False, "timeout", "synthetic native wake failure", "FULL_WAKE_ERROR"))
    case = verdict_ws.load_case(COPY_CASE)
    case["env_requirements"] = {}
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_locked.json"), propose=False)
    assert run["verdict"]["verdict"] == "error"
    assert run["verdict"]["execution"]["errors"]
    assert "FULL_WAKE_ERROR" in str(run["verdict"]["execution"]["errors"])
    assert run["verdict"]["action_required"]


def test_unlock_final_query_failure_survives_nested_dispatch(verdict_ws, monkeypatch):
    original = ReplayDevice.screen_locked
    calls = []

    def query(device):
        calls.append(True)
        if len(calls) == 3:
            device.screen_query_error = {"error_class": "timeout", "error": "last unlock query failed", "raw": "UNLOCK_QUERY_RAW"}
            return None
        device.screen_query_error = None
        return original(device)

    monkeypatch.setattr(ReplayDevice, "screen_locked", query)
    case = verdict_ws.load_case(COPY_CASE)
    case["env_requirements"] = {}
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_locked.json"), propose=False)
    assert run["verdict"]["verdict"] == "error"
    assert "UNLOCK_QUERY_RAW" in str(run["verdict"]["execution"]["errors"])
    assert run["actions"] == 0
