"""#56：前台窗口兜底查询失败必须与成功但未知的窗口证据分开。"""
import json
from types import MethodType

import pytest
from conftest import world
from verdict_fixtures import COPY_CASE
from vpdtest.devices import LAUNCHER, DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.runner import run_case

BACKGROUND = "Mission ID #1\nbundle name [synthetic.background]\nstate #BACKGROUND\n"
FOREGROUND = "Mission ID #1\nbundle name [synthetic.foreground]\nstate #FOREGROUND\n"
SCB_WINDOWS = ("WindowName DisplayId Pid WinId Type Mode Flag ZOrd Orientation [ x y w h ]\n"
               "SCBStatusBar 0 1 2 2108 1 0 2202 0 [ 0 0 1320 131 ]\n")
RAW_TIMEOUT = "partial window list output " * 30 + "WINDOW_FALLBACK_TIMEOUT_RAW_TAIL"


def _window_timeout():
    return DeviceResult("window_list", True, False, "timeout", "synthetic foreground window list timeout", RAW_TIMEOUT)


def test_window_fallback_failure_retains_actual_foreground_query_error(monkeypatch):
    device = HdcDevice("synthetic-device")
    monkeypatch.setattr(device, "_shell", lambda *args, op: (
        DeviceResult(op, True, True, raw=BACKGROUND) if op == "aa_dump" else _window_timeout()))
    assert device.foreground() is None
    assert device.last_query_error == "timeout"
    detail = json.dumps(device.last_query_error_detail, ensure_ascii=False)
    assert "synthetic foreground window list timeout" in detail and RAW_TIMEOUT in detail


def test_explicit_aa_foreground_does_not_depend_on_window_fallback(monkeypatch):
    device = HdcDevice("synthetic-device")
    monkeypatch.setattr(device, "_shell", lambda *args, op: (
        DeviceResult(op, True, True, raw=FOREGROUND) if op == "aa_dump" else _window_timeout()))
    assert device.foreground() == "synthetic.foreground"
    assert device.last_query_error is None


def test_scb_window_fallback_still_identifies_launcher(monkeypatch):
    device = HdcDevice("synthetic-device")
    monkeypatch.setattr(device, "_shell", lambda *args, op:
                        DeviceResult(op, True, True, raw=BACKGROUND if op == "aa_dump" else SCB_WINDOWS))
    assert device.foreground() == LAUNCHER and device.last_query_error is None


@pytest.mark.parametrize("window_output", ["unrecognized synthetic successful window response", "WindowName\n"])
def test_successful_but_unknown_window_evidence_does_not_create_query_error(monkeypatch, window_output):
    device = HdcDevice("synthetic-device")
    monkeypatch.setattr(device, "_shell", lambda *args, op:
                        DeviceResult(op, True, True, raw=BACKGROUND if op == "aa_dump" else window_output))
    assert device.foreground() is None and device.last_query_error is None


def test_successful_window_fallback_clears_previous_query_failure(monkeypatch):
    device = HdcDevice("synthetic-device")
    calls = []

    def shell(*args, op):
        if op == "aa_dump":
            return DeviceResult(op, True, True, raw=BACKGROUND)
        calls.append(op)
        return _window_timeout() if len(calls) == 1 else DeviceResult(op, True, True, raw=SCB_WINDOWS)

    monkeypatch.setattr(device, "_shell", shell)
    assert device.foreground() is None and device.last_query_error == "timeout"
    assert device.foreground() == LAUNCHER and device.last_query_error is None
    assert device.last_query_error_detail is None


def test_run_preserves_window_fallback_error_as_collection_error(verdict_ws, monkeypatch):
    # 只将真实 HDC 前台查询绑定 Replay；观测、执行器、判定、报告仍走原始闭环。
    original_device = verdict_ws.device

    def device(*args, **kwargs):
        replay = original_device(*args, **kwargs)
        replay.foreground = MethodType(HdcDevice.foreground, replay)
        replay.shown_windows = MethodType(HdcDevice.shown_windows, replay)
        replay._shell = lambda *args, op: (
            DeviceResult(op, True, True, raw=BACKGROUND) if op == "aa_dump" else _window_timeout())
        return replay

    monkeypatch.setattr(verdict_ws, "device", device)
    case = verdict_ws.load_case(COPY_CASE)
    case.update(steps=[{"id": "S1", "action": "wait", "duration_s": 0, "retry": 0}],
                assertions=[{"id": "A1", "at": "S1", "type": "text_present", "text": "synthetic copy",
                             "oracle_refs": []}], cleanup=[])
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert run["verdict"]["verdict"] == "error"
    assert run["verdict"]["execution"]["status"] == "error"
    errors = json.dumps(run["verdict"]["execution"]["errors"], ensure_ascii=False)
    assert "timeout" in errors and "synthetic foreground window list timeout" in errors
    assert RAW_TIMEOUT in errors
