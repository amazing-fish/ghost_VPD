"""Independent reproduction of PR60 review findings using synthetic Runner contracts."""
from copy import deepcopy
from pathlib import Path

import pytest
from test_condition_runner import APP, condition_rig

from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json

__all__ = ("condition_rig",)


@pytest.mark.parametrize("action", ["flow", "ensure_state"])
def test_parent_guard_expires_before_second_physical_child(condition_rig, action):
    ws, case, world = condition_rig
    children = [{"action": "tap", "target": "advance", "expect_state": "details", "retry": 0},
                {"action": "back", "expect_state": "dashboard", "retry": 0}]
    app = ws.app(APP)
    if action == "flow":
        write_json(app.dir / "flows.json", {"flows": {"guarded_path": {"steps": children}}})
        step = {"id": "S2", "action": "flow", "flow": "guarded_path"}
    else:
        data = read_json(app.dir / "states.json")
        data["recipes"]["details"] = children
        write_json(app.dir / "states.json", data)
        step = {"id": "S2", "action": "ensure_state", "state": "details"}
    step["when"] = {"condition": "fault", "equals": True, "at": "S1"}
    case["steps"].append(step)
    run = run_case(ws, case=case, device_spec=world(), propose=False)
    result = run["steps"][-1]
    assert result["outcome"] == "pending", result
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert not any(e["event"] == "dispatch" and e.get("step") == "S2.2" and e.get("sent") is True for e in events)


def test_read_cannot_consume_guard_invalidated_by_locator_scroll(condition_rig):
    ws, case, world = condition_rig
    app = ws.app(APP)
    targets = read_json(app.dir / "targets.json")
    targets["targets"]["advance"]["scroll"] = {"direction": "up", "max": 1}
    write_json(app.dir / "targets.json", targets)
    spec = world()
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    data["screens"]["scrolled"] = deepcopy(data["screens"]["dashboard"])
    dashboard = data["screens"]["dashboard"]
    dashboard["root"]["children"] = [n for n in dashboard["root"]["children"] if n.get("text") != "合成下一页"]
    dashboard["on_swipe_up"] = "scrolled"
    write_json(path, data)
    case["steps"].append({"id": "S2", "action": "read", "target": "advance", "retry": 0,
                           "when": {"condition": "fault", "equals": True, "at": "S1"}})
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    result = run["steps"][-1]
    assert result["outcome"] == "pending", result
    assert not result.get("measurement", {}).get("value")


def test_parent_guard_does_not_replace_child_condition(condition_rig):
    ws, case, world = condition_rig
    write_json(ws.app(APP).dir / "flows.json", {"flows": {"path": {"steps": [
        {"action": "tap", "target": "advance", "retry": 0,
         "when": {"condition": "fault", "equals": False}}]}}})
    case["steps"].append({"id": "S2", "action": "flow", "flow": "path",
                           "when": {"condition": "fault", "equals": True}})
    run = run_case(ws, case=case, device_spec=world(), propose=False)
    assert run["steps"][-1]["outcome"] == "pending"
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert not any(e["event"] == "dispatch" and e.get("step") == "S2.1" and e.get("sent") is True for e in events)


@pytest.mark.parametrize("after", ["独立健康标签", "独立证据未知"])
def test_later_false_condition_cannot_clear_unrecovered_command_error(condition_rig, monkeypatch, after):
    ws, case, world = condition_rig
    calls = []
    def tap(self, x, y):
        calls.append((x, y))
        for node in self.world["screens"]["dashboard"]["root"]["children"]:
            if node.get("text") == "独立故障标签":
                node["text"] = after
        return DeviceResult("tap", False, False, "command_failed", "synthetic first command error", raw="original failure receipt")
    monkeypatch.setattr(ReplayDevice, "tap", tap)
    case["steps"].append({"id": "S2", "action": "tap", "target": "advance", "retry": 1,
                           "when": {"condition": "fault", "equals": True}})
    run = run_case(ws, case=case, device_spec=world(), propose=False)
    assert len(calls) == 1
    assert run["steps"][-1]["outcome"] == "error"
    assert run["verdict"]["verdict"] == "error"
    assert run["verdict"]["execution"]["errors"][0]["error_class"] == "command_failed"
    assert "original failure receipt" in str(run["verdict"]["execution"]["errors"])
