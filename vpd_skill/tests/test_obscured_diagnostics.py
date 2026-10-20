"""Issue #64: expose the final touch blocker without changing dispatch rules."""

import pytest
from test_goal_loop import _lab_world, _start, cli

from vpdtest.commands import diagnose
from vpdtest.util import read_json, write_json


@pytest.mark.parametrize("overlay_type", ["SelectOverlay", "Button"])
def test_act_reports_current_blocker_and_keeps_target_blocked(ws, tmp_path, overlay_type):
    device = _lab_world(tmp_path)
    path = device.split(":", 1)[1]
    data = read_json(path)
    data["screens"]["home"]["root"]["children"].append({
        "t": overlay_type, "text": "遮挡面板", "b": [0, 380, 1320, 540], "click": True,
    })
    write_json(path, data)
    _start(ws, device, "blocked-target")
    out = cli(ws, "act", "tap", "--select", "text=唯一入口", "--app", "motor_hm",
              "--session", "blocked-target", "--device", device)
    assert out["result"]["final_status"] == "target_obscured"
    diagnosis = out["diagnosis"]
    assert diagnosis["blocker"]["type"] == overlay_type
    assert diagnosis["blocker"]["text"] == "遮挡面板"
    assert diagnosis["blocker"]["bounds"] == [0, 380, 1320, 540]
    assert diagnosis["expected"]["type"] == "Text"
    assert diagnosis["expected"]["text"] == "唯一入口"
    assert diagnosis["obs_id"] == out["result"]["attempts"][-1]["resolved"]["obs"]
    assert "observe" in diagnosis["hint"]
    attempts = out["result"]["attempts"]
    assert all(a.get("dispatch", {}).get("sent") is not True for a in attempts)
    assert diagnosis["blocker"] == attempts[-1]["dispatch_recheck"]["checks"]["tap"]["blocker"]


def test_diagnosis_does_not_reuse_a_previous_attempt_blocker():
    result = {"outcome": "blocked", "final_status": "target_obscured", "final_obs": "obs-0002",
              "attempts": [
                  {"status": "target_obscured", "dispatch_recheck": {
                      "checks": {"tap": {"blocker": {"type": "OldOverlay"}}}}},
                  {"status": "target_obscured", "obs_before": "obs-0002"},
              ]}
    out = diagnose(result)
    assert out.get("blocker") is None
    assert out.get("expected") is None


def test_diagnosis_binds_blocker_to_the_refreshed_target_observation():
    result = {"outcome": "blocked", "final_status": "target_obscured", "final_obs": "obs-0001",
              "attempts": [{"status": "target_obscured", "obs_before": "obs-0001",
                            "resolved": {"obs": "obs-0002"},
                            "dispatch_recheck": {"checks": {"tap": {
                                "blocker": {"type": "CurrentOverlay"},
                                "expected": {"type": "TextInput"}}}}}]}
    out = diagnose(result)
    assert out["obs_id"] == "obs-0002"
    assert out["blocker"]["type"] == "CurrentOverlay"


def test_success_has_no_obstruction_diagnosis():
    assert diagnose({"outcome": "passed", "final_status": "ok", "attempts": []}) is None
