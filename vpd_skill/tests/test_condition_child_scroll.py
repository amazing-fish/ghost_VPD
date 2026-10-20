"""Real Runner regressions for required child skips and repeated locator swipes."""
from copy import deepcopy
from pathlib import Path

import pytest
from test_condition_runner import APP, FAULT, HEALTHY, condition_rig

from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import RunContext

__all__ = ("condition_rig",)


def _events(run, operation):
    return [e for e in read_jsonl(Path(run["dir"]) / "events.jsonl") if e["event"] == "dispatch" and e["op"] == operation]


@pytest.mark.parametrize("kind", ["flow", "ensure"])
@pytest.mark.parametrize("critical", [None, True, False])
@pytest.mark.parametrize("parent_guard", [False, True])
def test_required_child_skip_halts_before_later_physical_child(condition_rig, kind, critical, parent_guard):
    ws, original, build = condition_rig
    case = deepcopy(original)
    case["cleanup"] = []
    case["assertions"] = case["assertions"][:1]
    first = {"action": "wait", "duration_s": 0, "when": {"condition": "fault", "equals": not parent_guard}}
    if critical is not None:
        first["critical"] = critical
    children = [first, {"action": "tap", "target": "advance", "expect_state": "details", "retry": 0}]
    directory = ws.root / "apps" / APP
    if kind == "flow":
        write_json(directory / "flows.json", {"flows": {"guarded": {"steps": children}}})
        second = {"id": "S2", "action": "flow", "flow": "guarded"}
    else:
        states = read_json(directory / "states.json")
        states["recipes"]["details"] = children
        write_json(directory / "states.json", states)
        second = {"id": "S2", "action": "ensure_state", "state": "details"}
    if parent_guard:
        case["conditions"]["scene_gate"] = {
            "kind": "page", "true": {"type": "text_present", "text": "合成工作台"},
            "false": {"type": "text_present", "text": "独立场景反证"}}
        second["when"] = {"condition": "scene_gate", "equals": True}
    case["steps"].append(second)
    run = run_case(ws, case=case, device_spec=build((FAULT,) if parent_guard else (HEALTHY,), False, True), propose=False)
    parent = run["steps"][1]
    sent = [e for e in _events(run, "tap") if e.get("sent") is not False]
    if critical is False:
        assert len(sent) == 1 and len(parent["sub_steps"]) == (3 if kind == "ensure" else 2)
        assert parent["sub_steps"][0]["outcome"] == "skipped"
        if kind == "ensure":
            terminal = parent["sub_steps"][-1]
            if parent_guard:
                # The source-page condition is unknown after leaving the dashboard.
                assert terminal["outcome"] == "pending" and not terminal["attempts"]
                assert terminal["condition"]["status"] == "unknown"
                assert run["verdict"]["verdict"] == "pending"
            else:
                assert terminal["attempts"][-1]["post"]["met"] is True
                assert terminal["attempts"][-1]["post"]["expected"] == "details"
        assert parent["outcome"] == ("pending" if kind == "ensure" and parent_guard else "passed")
    else:
        assert not sent, (parent, sent)
        assert parent["outcome"] == "pending" and len(parent["sub_steps"]) == 1
        assert parent["sub_steps"][0]["outcome"] == "skipped"
        assert run["verdict"]["verdict"] == "pending"


def _scroll_case(rig, action, change, parent_guard):
    ws, original, build = rig
    case = deepcopy(original)
    case["cleanup"] = []
    case["assertions"] = case["assertions"][:1]
    guard = {"condition": "fault", "equals": True}
    if change == "epoch":
        guard["at"] = "S1"
    if change == "expired":
        case["conditions"]["fault"]["max_age_s"] = 5
    leaf = {"id": "S2", "action": action, "target": "advance", "retry": 0, "read_scope": "self", "text": "synthetic"}
    directory = ws.root / "apps" / APP
    targets = read_json(directory / "targets.json")
    targets["targets"]["advance"].update(strategies=[{"kind": "text", "value": "TARGET_NEVER_PRESENT"}],
                                           scroll={"direction": "up", "max": 3}, allow_input=True)
    write_json(directory / "targets.json", targets)
    if parent_guard:
        write_json(directory / "flows.json", {"flows": {"guarded": {"steps": [leaf]}}})
        case["steps"].append({"id": "S2", "action": "flow", "flow": "guarded", "when": guard})
        label = "S2.1:scroll1"
    else:
        case["steps"].append({**leaf, "when": guard})
        label = "S2:scroll1"
    spec = build((FAULT,), False, True)
    path = Path(spec.split(":", 1)[1])
    world = read_json(path)
    dashboard = world["screens"]["dashboard"]
    dashboard["on_swipe_up"] = "page1"
    for index in range(1, 4):
        page = deepcopy(dashboard)
        page["root"]["children"].append({"t": "Text", "text": f"页码-{index}", "b": [10, 690, 350, 735]})
        page["on_swipe_up"] = f"page{index + 1}" if index < 3 else None
        if index == 1 and change in ("false", "unknown"):
            for node in page["root"]["children"]:
                if node.get("text") == FAULT:
                    node["text"] = HEALTHY if change == "false" else "无独立前置证据"
        world["screens"][f"page{index}"] = page
    write_json(path, world)
    return ws, case, spec, label


@pytest.mark.parametrize("action", ["read", "tap", "input"])
@pytest.mark.parametrize("change", ["false", "unknown", "epoch", "expired", "true"])
@pytest.mark.parametrize("parent_guard", [False, True])
def test_each_locator_swipe_rechecks_complete_guard_chain(condition_rig, monkeypatch, action, change, parent_guard):
    ws, case, spec, label = _scroll_case(condition_rig, action, change, parent_guard)
    original = RunContext.observe
    def observe(ctx, name="", screenshot=True):
        result = original(ctx, name, screenshot)
        if change == "expired" and name == label:
            ctx.sleep(6)  # The observation is real but expires while preparing the next physical swipe.
        return result
    monkeypatch.setattr(RunContext, "observe", observe)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    sent = [e for e in _events(run, "swipe") if e.get("sent") is not False]
    second = run["steps"][1]
    leaf = second["sub_steps"][0] if parent_guard else second
    assert len(sent) == (3 if change == "true" else 1), (leaf, sent)
    assert not _events(run, "tap") and not _events(run, "input_text")
    assert not leaf.get("measurement")
    if change == "true":
        assert leaf["outcome"] == "blocked" and leaf["final_status"] == "target_unresolved"
    else:
        assert leaf["outcome"] == "pending"
        assert leaf["condition"]["status"] == ("false" if change == "false" else "unknown")
        assert not run["verdict"]["execution"]["errors"]


@pytest.mark.parametrize("action", ["read", "tap", "input"])
@pytest.mark.parametrize("sent", [False, None, True])
def test_locator_swipe_error_preserves_raw_and_unknown_effect(condition_rig, monkeypatch, action, sent):
    ws, case, spec, _ = _scroll_case(condition_rig, action, "true", True)
    original = ReplayDevice.swipe
    def swipe(self, *args):
        if sent is not False:
            original(self, *args)
        return DeviceResult("swipe", sent, False, "timeout", "locator acknowledgement lost", raw="locator original raw")
    monkeypatch.setattr(ReplayDevice, "swipe", swipe)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    leaf = run["steps"][1]["sub_steps"][0]
    assert leaf["outcome"] == "error"
    assert len(_events(run, "swipe")) == 1
    assert "locator original raw" in str(run["verdict"]["execution"]["errors"])
    assert bool(leaf.get("effect_unknown")) is (sent is not False)
    assert bool(run["verdict"].get("action_required")) is (sent is not False)


@pytest.mark.parametrize("action", ["read", "tap", "input"])
def test_locator_budget_refusal_after_one_swipe_stays_blocked(condition_rig, action):
    ws, case, spec, _ = _scroll_case(condition_rig, action, "true", True)
    policy_path = ws.root / "policy.json"
    policy = read_json(policy_path)
    policy["max_actions_per_run"] = 2
    write_json(policy_path, policy)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    leaf = run["steps"][1]["sub_steps"][0]
    assert len([e for e in _events(run, "swipe") if e.get("sent") is not False]) == 1
    assert leaf["outcome"] == "blocked" and leaf["final_status"] == "budget_exhausted"
    assert not run["verdict"]["execution"]["errors"]
