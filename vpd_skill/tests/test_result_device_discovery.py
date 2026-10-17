"""#56：设备发现的工具失败须保留，不能伪装成正常空设备列表。"""
import json
import subprocess

import pytest
from verdict_fixtures import COPY_CASE
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.verdict import case_verdict


@pytest.mark.parametrize("failure,error_class,evidence", [
    ("os_error", "tool_execution_error", "synthetic discovery process denied"),
    ("missing_tool", "tool_missing", "找不到"),
    ("timeout", "timeout", "DISCOVERY_TIMEOUT_RAW_TAIL"),
    ("nonzero", "command_failed", "DISCOVERY_NONZERO_RAW_TAIL"),
])
def test_target_discovery_failure_retains_tool_attribution_and_original_evidence(verdict_ws, monkeypatch,
                                                                              failure, error_class, evidence):
    calls = []

    def discover(cmd, **kwargs):
        calls.append(cmd)
        assert cmd == ["hdc", "list", "targets"]
        if failure == "os_error":
            raise PermissionError("synthetic discovery process denied")
        if failure == "missing_tool":
            raise FileNotFoundError("synthetic missing hdc executable")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 1, output=b"partial discovery output ",
                                            stderr=b"DISCOVERY_TIMEOUT_RAW_TAIL")
        return subprocess.CompletedProcess(cmd, 7, stdout=b"partial discovery output " * 40,
                                           stderr=b"DISCOVERY_NONZERO_RAW_TAIL")

    monkeypatch.setattr("subprocess.run", discover)
    device = HdcDevice()
    preflight = device.preflight("synthetic.app")
    assert len(calls) == 1 and device.serial is None and preflight["ok"] is False
    verdict = case_verdict(preflight, {"ok": True}, [], [], KnowledgeStore(verdict_ws.root))
    assert verdict["verdict"] == "error" and verdict["attribution"] == "tool", verdict
    assert verdict["execution"]["status"] == "error"
    error = verdict["execution"]["errors"][0]
    assert error["error_class"] == error_class and error["stage"]
    assert evidence in json.dumps(error, ensure_ascii=False)
    assert evidence in json.dumps(preflight, ensure_ascii=False)


def test_successful_empty_discovery_remains_device_environment_failure(verdict_ws, monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda cmd, **kwargs:
                        subprocess.CompletedProcess(cmd, 0, stdout=b"[Empty]\n", stderr=b""))
    device = HdcDevice()
    preflight = device.preflight("synthetic.app")
    assert device.serial is None and preflight["ok"] is False
    assert preflight["checks"][0]["error_class"] == "device_offline"
    assert not preflight["checks"][0].get("execution_error")
    verdict = case_verdict(preflight, {"ok": True}, [], [], KnowledgeStore(verdict_ws.root))
    assert verdict["verdict"] == "error" and verdict["attribution"] == "environment"


def test_explicit_serial_skips_discovery_and_preserves_normal_preflight(monkeypatch):
    monkeypatch.setattr(HdcDevice, "_first_target", lambda self: pytest.fail("explicit serial must skip discovery"))
    device = HdcDevice("synthetic-explicit-serial")
    values = {"power_state": "Current State: AWAKE", "lock_state": "screenLocked false",
              "bm_dump": '"versionName": "1.0.0"'}
    monkeypatch.setattr(device, "_shell", lambda *args, op:
                        DeviceResult(op, True, True, raw=values[op]))
    monkeypatch.setattr(device, "info", lambda: {"serial": device.serial})
    monkeypatch.setattr(device, "display_info", dict)
    preflight = device.preflight("synthetic.app")
    assert device.serial == "synthetic-explicit-serial" and preflight["ok"] is True
    assert not any(check.get("execution_error") for check in preflight["checks"])


@pytest.mark.parametrize("output,serial", [(b"[Empty]\n", None),
                                           (b"synthetic-recovered-device\n", "synthetic-recovered-device")])
def test_successful_discovery_does_not_reuse_previous_tool_failure(monkeypatch, output, serial):
    calls = []

    def discover(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise PermissionError("synthetic temporary discovery failure")
        return subprocess.CompletedProcess(cmd, 0, stdout=output, stderr=b"")

    monkeypatch.setattr("subprocess.run", discover)
    device = HdcDevice()
    device.serial = device._first_target()
    assert len(calls) == 2 and device.serial == serial
    values = {"power_state": "Current State: AWAKE", "lock_state": "screenLocked false",
              "bm_dump": '"versionName": "1.0.0"'}
    monkeypatch.setattr(device, "_shell", lambda *args, op:
                        DeviceResult(op, True, True, raw=values[op]))
    monkeypatch.setattr(device, "info", lambda: {"serial": device.serial})
    monkeypatch.setattr(device, "display_info", dict)
    preflight = device.preflight("synthetic.app")
    assert not any(check.get("execution_error") for check in preflight["checks"])
    assert preflight["ok"] is (serial is not None)
    assert "synthetic temporary discovery failure" not in json.dumps(preflight)


def test_invalid_swipe_is_rejected_before_automatic_device_discovery(verdict_ws, monkeypatch):
    case = verdict_ws.load_case(COPY_CASE)
    case["steps"] = [{"id": "S1", "action": "swipe", "point": [640, 1660]}]
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs:
                        pytest.fail("invalid swipe must be rejected before hdc list targets"))
    with pytest.raises(ValueError, match="swipe"):
        run_case(verdict_ws, case=case, device_spec="hdc:", propose=False)
    assert not list(verdict_ws.runs_dir.glob("run-*"))
