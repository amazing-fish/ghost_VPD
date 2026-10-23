"""PIN transport receipts retain the per-device guard through real Dispatcher calls."""
import json

import pytest

from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.observe import RunStore
from vpdtest.unlock import unlock_device
from vpdtest.util import read_jsonl, sha256_obj, write_json


@pytest.mark.parametrize("accepted_before_failure,sent,entered,guarded", [
    (0, None, 0, True),
    (0, True, 1, True),
    (0, False, 0, False),
    (1, False, 1, True),
    (1, None, 1, True),
])
def test_pin_receipt_controls_reentry_without_repeating_possible_input(
        ws, tmp_path, monkeypatch, accepted_before_failure, sent, entered, guarded):
    pin = "246810"
    world = tmp_path / "pin-receipt-world.json"
    write_json(world, {
        "bundle": "com.example.synthetic", "screen": [1320, 2848],
        "initial": {"locked": True, "lock_pin": pin},
        "launch_screen": "home", "screens": {"home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848]}}},
    })
    device = ws.device("replay:" + str(world))
    ws.config.update(unlock_secrets={device.spec: "LOCK_PIN_TEST"}, secrets={"LOCK_PIN_TEST": pin})
    physical = []
    original_wake, original_swipe, original_tap = ReplayDevice.wake, ReplayDevice.swipe, ReplayDevice.tap

    def wake(dev):
        physical.append("wake")
        return original_wake(dev)

    def swipe(dev, *args):
        physical.append("swipe")
        return original_swipe(dev, *args)

    def tap(dev, *args):
        physical.append("pin_tap")
        if physical.count("pin_tap") == accepted_before_failure + 1:
            # A lost acknowledgement may still follow an applied digit. A
            # definite False is the only receipt which cannot reach the keypad.
            if sent is not False:
                original_tap(dev, *args)
            return DeviceResult("tap", sent, False, "timeout", "synthetic PIN acknowledgement failure")
        return original_tap(dev, *args)

    monkeypatch.setattr(ReplayDevice, "wake", wake)
    monkeypatch.setattr(ReplayDevice, "swipe", swipe)
    monkeypatch.setattr(ReplayDevice, "tap", tap)
    store = RunStore(ws.runs_dir, kind="unlock")
    first = unlock_device(ws, device, store)
    guard = ws.runs_dir / f"unlock-guard-{sha256_obj(device.spec)[:12]}.json"

    assert first["status"] == "indeterminate" and first["action_required"]
    assert first["entered_digits"] == entered
    assert bool(first.get("pin_entered")) is guarded
    assert first["execution_error"]["evidence"]["sent"] is sent
    assert guard.exists() is guarded
    assert physical == ["wake", "swipe"] + ["pin_tap"] * (accepted_before_failure + 1)
    before = list(physical)
    second = unlock_device(ws, device, store)
    if guarded:
        assert second["status"] == "pin_rejected" and second["action_required"]
        assert physical == before  # No wake, swipe or digit reissued after uncertainty.
        assert guard.exists()
    else:
        assert second["status"] == "unlocked" and second["entered_digits"] == len(pin)
        assert physical[len(before):] == ["wake", "swipe"] + ["pin_tap"] * len(pin)
        assert not guard.exists()

    events = read_jsonl(store.events_path)
    assert pin not in json.dumps([first, second, events], ensure_ascii=False)
    if guard.exists():
        assert pin not in guard.read_text(encoding="utf-8")
