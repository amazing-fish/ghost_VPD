"""#56: explicit display queries cannot manufacture a device-form fact.

All commands and product contracts are synthetic; no physical device is used.
"""
from types import MethodType

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.runner import run_case

FOLD_PARAM = "const.window.foldscreen.type"
MODEL_PARAM = "const.product.model"
DISPLAY = """---------------- Screen ID: 0 ----------------
VirtualPixelRatio:            2
DisplayFoldStatus:            UNKNOWN
---------------- Display ID: 0 ----------------
Width:                        400
Height:                       800
"""


def _shell(*args, op, failed=None, fold="UNKNOWN", absent_ok=True, **kwargs):
    query = args[-1] if op == "param_get" else op
    if query == failed:
        return DeviceResult(op, True, False, "timeout", "synthetic query timeout", "DISPLAY_PARAM_FULL_RAW")
    if op == "param_get":
        return DeviceResult(op, True, absent_ok, None if absent_ok else "command_failed",
                            None if absent_ok else "parameter does not exist", f"Get parameter {args[-1]} fail!")
    values = {"display_dump": DISPLAY.replace("UNKNOWN", fold), "power_state": "Current State: AWAKE",
              "lock_state": "screenLocked false", "bm_dump": '"versionName": "3.0.3.200"'}
    return DeviceResult(op, True, True, raw=values[op])


def _hdc(monkeypatch, **options):
    device = HdcDevice("synthetic-device")
    monkeypatch.setattr(device, "_shell", lambda *a, **k: _shell(*a, **k, **options))
    return device


def test_failed_fold_property_query_does_not_mean_bar(monkeypatch):
    display = _hdc(monkeypatch, failed=FOLD_PARAM).display_info()
    assert display["device_form"] is None
    assert display["execution_error"]["error_class"] == "timeout"
    assert display["execution_error"]["raw"] == "DISPLAY_PARAM_FULL_RAW"


@pytest.mark.parametrize("absent_ok", [True, False], ids=["successful-absence", "known-parameter-missing-output"])
def test_genuinely_absent_fold_property_still_allows_bar(monkeypatch, absent_ok):
    display = _hdc(monkeypatch, absent_ok=absent_ok).display_info()
    assert display["device_form"] == "bar" and not display.get("execution_error")


@pytest.mark.parametrize("fold", ["FOLDED", "EXPAND"])
def test_independent_fold_state_survives_property_query_error(monkeypatch, fold):
    display = _hdc(monkeypatch, failed=FOLD_PARAM, fold=fold).display_info()
    assert display["device_form"] == "foldable"
    assert display["execution_error"]["raw"] == "DISPLAY_PARAM_FULL_RAW"


def test_display_dump_error_retains_raw_and_fails_declared_preflight(monkeypatch):
    device = _hdc(monkeypatch, failed="display_dump")
    pre = device.preflight("synthetic.app")
    assert pre["ok"] is False
    check = next(c for c in pre["checks"] if c["check"] == "display_info")
    assert check["ok"] is False and check["execution_error"]["raw"] == "DISPLAY_PARAM_FULL_RAW"


def test_optional_device_info_error_is_visible_without_failing_preflight(monkeypatch):
    pre = _hdc(monkeypatch, failed=MODEL_PARAM).preflight("synthetic.app")
    assert pre["ok"] is True
    assert pre["device"]["model"] is None
    assert pre["device"]["query_errors"][MODEL_PARAM]["raw"] == "DISPLAY_PARAM_FULL_RAW"
    assert not any(c.get("execution_error") for c in pre["checks"])


def test_successful_param_requery_clears_previous_error(monkeypatch):
    device = _hdc(monkeypatch, failed=FOLD_PARAM)
    assert device.display_info().get("execution_error")
    monkeypatch.setattr(device, "_shell", _shell)
    display = device.display_info()
    assert display["device_form"] == "bar" and not display.get("execution_error")


def test_unrecognized_display_output_does_not_manufacture_bar(monkeypatch):
    device = _hdc(monkeypatch)
    original = device._shell

    def shell(*args, op, **kwargs):
        if op == "display_dump":
            return DeviceResult(op, True, True, raw="successful command with unrecognized display format")
        return original(*args, op=op, **kwargs)

    monkeypatch.setattr(device, "_shell", shell)
    display = device.display_info()
    assert display["device_form"] is None
    assert not display.get("execution_error")  # 无明确执行错误，保留未知而非编造故障。


@pytest.mark.parametrize("failed,expected", [(FOLD_PARAM, "error"), (MODEL_PARAM, "passed")])
def test_runtime_does_not_pass_bar_scope_from_failed_measurement(verdict_ws, monkeypatch, failed, expected):
    confirm(verdict_ws, COPY_RULE)
    case = verdict_ws.load_case(COPY_CASE)
    case["env_requirements"]["device_form"] = "bar"
    original_device = verdict_ws.device

    def device(*args, **kwargs):
        replay = original_device(*args, **kwargs)
        for method in ("preflight", "app_version", "_param", "display_info", "info"):
            setattr(replay, method, MethodType(getattr(HdcDevice, method), replay))
        replay._shell = lambda *a, **k: _shell(*a, **k, failed=failed)
        return replay

    monkeypatch.setattr(verdict_ws, "device", device)
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert run["verdict"]["verdict"] == expected
    if failed == FOLD_PARAM:
        assert run["facts"]["device_form"] is None
        assert run["verdict"]["execution"]["status"] == "error"
        assert "DISPLAY_PARAM_FULL_RAW" in str(run["verdict"]["execution"]["errors"])
        assert all(step["outcome"] == "not_run" for step in run["steps"])
    else:
        assert run["facts"]["device_form"] == "bar"
        assert run["verdict"]["execution"]["status"] == "completed"
        assert run["device"]["query_errors"][MODEL_PARAM]["raw"] == "DISPLAY_PARAM_FULL_RAW"
