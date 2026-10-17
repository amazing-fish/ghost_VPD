"""#56：PIN 键盘明确采集错误与有效树上无法识别键盘分开，全部使用合成设备。"""
import json

import pytest
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.unlock import wake_unlock

NO_PIN_TREE = json.dumps({"attributes": {"type": "Column", "bounds": "[0,0][1320,2848]"}, "children": []})
ERRORS = [
    ("dump_timeout", "timeout", "PIN_DUMP_TIMEOUT_RAW"),
    ("cat_failure", "command_failed", "[Fail] cat lock dump denied PIN_CAT_RAW"),
    ("invalid_json", "tree_parse_error", '{"attributes": PIN_INVALID_JSON_RAW'),
]


def _device(monkeypatch, failure):
    device = HdcDevice("synthetic-device")
    calls = []

    def shell(*args, op, **kwargs):
        calls.append(op)
        if op == "lock_dump" and failure == "dump_timeout":
            return DeviceResult(op, True, False, "timeout", "synthetic lock dump timeout", ERRORS[0][2])
        if op == "lock_dump_read":
            if failure == "cat_failure":
                return DeviceResult(op, True, False, "command_failed", "synthetic lock dump cat failure", ERRORS[1][2])
            return DeviceResult(op, True, True, raw=ERRORS[2][2] if failure == "invalid_json" else NO_PIN_TREE)
        return DeviceResult(op, True, True)

    monkeypatch.setattr(device, "_shell", shell)
    monkeypatch.setattr(device, "screen_locked", lambda: True)
    monkeypatch.setattr(device, "display_info", lambda: {"width_px": 1320, "height_px": 2848})
    return device, calls


@pytest.mark.parametrize("failure,error_class,raw", ERRORS)
def test_pinpad_collection_failure_keeps_original_query_error(monkeypatch, failure, error_class, raw):
    device, calls = _device(monkeypatch, failure)
    assert device.lock_pin_pad() is None
    error = getattr(device, "pin_pad_query_error", None)
    assert error and error["error_class"] == error_class
    assert error["error"] and error["raw"] == raw
    assert "cleanup" in calls


@pytest.mark.parametrize("failure,error_class,raw", ERRORS)
def test_unlock_pinpad_collection_failure_uses_execution_error_without_entering_pin(monkeypatch, failure,
                                                                                error_class, raw):
    device, calls = _device(monkeypatch, failure)
    result = wake_unlock(device, "2468", sleep=lambda _: None)
    assert result["status"] == "indeterminate" and result["entered_digits"] == 0
    error = result.get("execution_error")
    assert error and error["error_class"] == error_class and error["stage"]
    assert error["attribution"] == "tool"
    assert json.dumps(raw, ensure_ascii=False)[1:-1] in json.dumps(error, ensure_ascii=False)
    assert "tap" not in calls


def test_valid_tree_without_pinpad_is_normal_indeterminate_handoff(monkeypatch):
    device, calls = _device(monkeypatch, None)
    result = wake_unlock(device, "2468", sleep=lambda _: None)
    assert result["status"] == "indeterminate" and result["entered_digits"] == 0
    assert not result.get("execution_error")
    assert not getattr(device, "pin_pad_query_error", None)
    assert "tap" not in calls


def test_successful_pinpad_query_clears_previous_collection_error(monkeypatch):
    device, _ = _device(monkeypatch, None)
    device.pin_pad_query_error = {"error_class": "timeout", "error": "previous synthetic failure", "raw": "OLD_PIN_RAW"}
    assert device.lock_pin_pad() is None  # 合法树没有数字键盘，不得保留上次故障。
    assert device.pin_pad_query_error is None
