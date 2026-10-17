"""Explicit errors from the queries needed for unlock retain their evidence."""
import json

import pytest
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.unlock import wake_unlock


def test_unlock_consumes_real_display_adapter_failure(monkeypatch):
    device = HdcDevice("synthetic-device")
    calls = []

    def shell(*args, op, **kwargs):
        calls.append(op)
        if op == "wakeup":
            return DeviceResult(op, True, True)
        assert op == "display_dump", "no input may follow failed required display evidence"
        return DeviceResult(op, True, False, "timeout", "synthetic display timeout", "COMBINED_DISPLAY_RAW")

    monkeypatch.setattr(device, "_shell", shell)
    monkeypatch.setattr(device, "screen_locked", lambda: True)
    result = wake_unlock(device, None, sleep=lambda _: None)
    assert result["status"] == "indeterminate" and result["entered_digits"] == 0
    assert result["execution_error"]["stage"] == "unlock_display"
    assert result["execution_error"]["attribution"] == "tool"
    assert "COMBINED_DISPLAY_RAW" in json.dumps(result["execution_error"])
    assert calls == ["wakeup", "display_dump"]


@pytest.mark.parametrize("recovered", [False, True])
def test_screen_query_retries_retain_error_until_reliable_state(monkeypatch, recovered):
    device = HdcDevice("synthetic-device")
    calls = []

    def shell(*args, op):
        if op == "lock_state":
            return DeviceResult(op, True, True, raw="screenLocked false")
        calls.append(op)
        if len(calls) == 1:
            return DeviceResult(op, True, False, "timeout", "synthetic power query timeout", "POWER_RETRY_RAW")
        return DeviceResult(op, True, True, raw="Current State: AWAKE" if recovered else "unrecognized state")

    monkeypatch.setattr(device, "_shell", shell)
    monkeypatch.setattr("vpdtest.devices.harmony_hdc.time.sleep", lambda _: None)
    assert device.screen_locked(tries=2) is (False if recovered else None)
    if recovered:
        assert device.screen_query_error is None
    else:
        assert device.screen_query_error["error_class"] == "timeout"
        assert device.screen_query_error["raw"] == "POWER_RETRY_RAW"

    # A separate query must not inherit an earlier call's failed attempt.
    monkeypatch.setattr(device, "_shell", lambda *args, op: DeviceResult(op, True, True, raw="unrecognized state"))
    assert device.screen_locked(tries=1) is None and device.screen_query_error is None


@pytest.mark.parametrize("mode", ["display_error", "unknown_size", "usable_size_with_metadata_error"])
def test_unlock_display_query_error_only_blocks_required_missing_dimensions(mode):
    error = {"error_class": "timeout", "error": "synthetic display query timeout", "raw": "DISPLAY_UNLOCK_RAW"}
    display = ({"density": None, "execution_error": error} if mode == "display_error" else
               {"width_px": 1320, "height_px": 2848, "execution_error": error}
               if mode == "usable_size_with_metadata_error" else {"density": None})
    actions = []

    class Device:
        kind = "replay"

        def screen_locked(self):
            return True

        def wake(self):
            actions.append("wake")
            return DeviceResult("wake", True, True)

        def display_info(self):
            return display

        def swipe(self, *args):
            actions.append("swipe")
            return DeviceResult("swipe", True, True)

    result = wake_unlock(Device(), None)
    assert result["entered_digits"] == 0
    if mode == "display_error":
        assert result["status"] == "indeterminate"
        assert result["execution_error"]["stage"] == "unlock_display"
        assert "DISPLAY_UNLOCK_RAW" in json.dumps(result["execution_error"], ensure_ascii=False)
    else:
        assert "execution_error" not in result
    assert actions == (["wake", "swipe"] if mode == "usable_size_with_metadata_error" else ["wake"])
