"""#60 review regressions: compound operation guards and locator-assisted read.

All product requirements, identities, APP pages and device responses are synthetic.
The real Runner/Observer/Executor/Dispatcher path must reject stale parent evidence.
"""
from copy import deepcopy
from pathlib import Path

import pytest
import test_condition_runner
from test_condition_runner import (
    APP,
    COMMON_RULE,
    FAULT,
    HEALTHY,
    _assertions,
    _coverage,
)

from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json

condition_rig = test_condition_runner.condition_rig

def _compound(rig, kind, *, change=None, child_guard=None, optional_unknown=False):
    ws, original, world = rig
    case = deepcopy(original)
    spec = world()
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    app_dir = ws.app(APP).dir
    targets = read_json(app_dir / "targets.json")
    targets["targets"]["finish"] = {"desc": "合成完成", "side_effect": "navigation", "strategies": [
        {"kind": "text", "value": "合成完成"}]}
    data["screens"]["details"]["root"]["children"].append({
        "t": "Button", "text": "合成完成", "b": [10, 650, 190, 710], "go": "final"})
    data["screens"]["final"] = deepcopy(data["screens"]["details"])
    data["screens"]["final"]["root"]["children"][0]["text"] = "合成终页"
    if change in ("false", "unknown"):
        for name in ("details", "final"):
            marker = next(node for node in data["screens"][name]["root"]["children"] if node.get("text") == FAULT)
            marker["text"] = HEALTHY if change == "false" else "独立现场证据尚未取得"
    if change in ("vehicle", "task"):
        keys = ["vehicle"] if change == "vehicle" else ["vehicle", "task"]
        case["conditions"]["fault"].update(kind=change, scope={key: "identity_" + key for key in keys})
        for key in keys:
            targets["targets"]["identity_" + key] = {"desc": "合成身份 " + key, "strategies": [
                {"kind": "id", "value": "synthetic_" + key}]}
        for name, screen in data["screens"].items():
            for i, key in enumerate(keys):
                # 子动作之后切换身份，不借被测按钮的结果推断场景。
                suffix = "B" if name != "dashboard" and key == change else "A"
                screen["root"]["children"].append({
                    "t": "Text", "id": "synthetic_" + key, "text": key + "-" + suffix,
                    "b": [10, 480 + i * 50, 350, 520 + i * 50]})
    if child_guard is not None or optional_unknown:
        case["conditions"]["child"] = {"kind": "page",
            "true": {"type": "text_present", "text": "独立子条件允许"},
            "false": {"type": "text_present", "text": "独立子条件拒绝"}}
        marker = "独立子条件允许" if child_guard is True else "独立子条件拒绝"
        if not optional_unknown:
            for screen in data["screens"].values():
                screen["root"]["children"].append({
                    "t": "Text", "text": marker, "b": [10, 580, 350, 620]})
    write_json(path, data)
    write_json(app_dir / "targets.json", targets)
    model = read_json(app_dir / "states.json")
    model["states"]["final"] = {"require_text": ["合成终页"]}
    children = [
        {"action": "tap", "target": "advance", "expect_state": "details", "retry": 0},
        {"action": "tap", "target": "finish", "expect_state": "final", "retry": 0},
    ]
    if child_guard is not None:
        children[1]["when"] = {"condition": "child", "equals": True}
    if optional_unknown:
        children[1].update(critical=False, when={"condition": "child", "equals": True})
        children.append({"action": "wait", "duration_s": 0, "expect_state": "details", "retry": 0})
    if kind == "flow":
        write_json(app_dir / "flows.json", {"flows": {"compound": {"steps": children}}})
        parent = {"id": "F1", "action": "flow", "flow": "compound"}
    else:
        model["recipes"]["final"] = children
        parent = {"id": "F1", "action": "ensure_state", "state": "final"}
    write_json(app_dir / "states.json", model)
    parent["when"] = {"condition": "fault", "equals": True}
    case["steps"].append(parent)
    case["assertions"] = [case["assertions"][0], {
        "id": "A-flow", "at": "F1", "type": "state_is", "state": "details" if optional_unknown else "final",
        "oracle_refs": [COMMON_RULE]}]
    return ws, case, spec


def _sent_taps(run):
    return [event for event in read_jsonl(Path(run["dir"]) / "events.jsonl")
            if event["event"] == "dispatch" and event["op"] == "tap" and event["sent"] is True]


@pytest.mark.parametrize("kind", ["flow", "ensure_state"])
@pytest.mark.parametrize("change", ["false", "unknown", "vehicle", "task"])
def test_parent_guard_cannot_authorize_later_children_after_counterproof_or_identity_change(condition_rig, kind, change):
    ws, case, spec = _compound(condition_rig, kind, change=change)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    parent = run["steps"][1]
    assert len(_sent_taps(run)) == 1, parent
    assert not parent["outcome"].startswith("passed"), parent
    if change in ("unknown", "vehicle", "task"):
        assert parent["outcome"] == "pending", parent
    assert _assertions(run)["A-flow"]["result"] not in ("pass", "not_applicable")
    assert run["verdict"]["verdict"] != "passed"
    _coverage(run, verified=("A-common",), unverified=("A-flow",))


@pytest.mark.parametrize("kind", ["flow", "ensure_state"])
def test_parent_condition_that_still_holds_allows_both_children(condition_rig, kind):
    ws, case, spec = _compound(condition_rig, kind)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert len(_sent_taps(run)) == 2
    assert run["steps"][1]["outcome"].startswith("passed")
    assert _assertions(run)["A-flow"]["result"] == "pass"
    assert run["verdict"]["verdict"] == "passed"
    _coverage(run, verified=("A-common", "A-flow"))


@pytest.mark.parametrize("kind", ["flow", "ensure_state"])
@pytest.mark.parametrize("change", ["false", "unknown"])
def test_child_when_does_not_replace_its_parent_condition(condition_rig, kind, change):
    ws, case, spec = _compound(condition_rig, kind, change=change, child_guard=True)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    parent = run["steps"][1]
    assert len(_sent_taps(run)) == 1, parent
    assert not parent["outcome"].startswith("passed"), parent
    if change == "unknown":
        assert parent["outcome"] == "pending"
    assert run["verdict"]["verdict"] != "passed"
    _coverage(run, verified=("A-common",), unverified=("A-flow",))


@pytest.mark.parametrize("kind", ["flow", "ensure_state"])
def test_parent_true_does_not_override_child_false(condition_rig, kind):
    ws, case, spec = _compound(condition_rig, kind, child_guard=False)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert len(_sent_taps(run)) == 1
    assert _assertions(run)["A-flow"]["result"] != "pass"
    assert run["verdict"]["verdict"] != "passed"
    _coverage(run, verified=("A-common",), unverified=("A-flow",))


def test_noncritical_unknown_child_cannot_make_parent_assertion_verified(condition_rig):
    ws, case, spec = _compound(condition_rig, "flow", optional_unknown=True)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    parent = run["steps"][1]
    assert len(_sent_taps(run)) == 1
    assert any(sub["outcome"] == "pending" for sub in parent["sub_steps"])
    assert parent["outcome"] == "pending", parent
    assert _assertions(run)["A-flow"]["result"] != "pass"
    assert run["verdict"]["verdict"] == "pending"
    _coverage(run, verified=("A-common",), unverified=("A-flow",))


@pytest.mark.parametrize("counterproof", [HEALTHY, "独立证据缺失"])
def test_conditional_read_cannot_measure_after_locator_scroll_invalidates_its_when(condition_rig, counterproof):
    ws, original, world = condition_rig
    case = deepcopy(original)
    app_dir = ws.app(APP).dir
    targets = read_json(app_dir / "targets.json")
    targets["targets"]["reading"] = {"desc": "合成读数", "strategies": [{"kind": "id", "value": "synthetic_reading"}],
                                      "scroll": {"direction": "up", "max": 1}}
    write_json(app_dir / "targets.json", targets)
    spec = world()
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    dashboard = data["screens"]["dashboard"]
    scrolled = deepcopy(dashboard)
    next(node for node in scrolled["root"]["children"] if node.get("text") == FAULT)["text"] = counterproof
    scrolled["root"]["children"].append({"t": "Text", "id": "synthetic_reading", "text": "合成读数42米",
                                         "b": [10, 600, 350, 650]})
    dashboard["on_swipe_up"] = "scrolled"
    data["screens"]["scrolled"] = scrolled
    write_json(path, data)
    case["steps"].append({"id": "R1", "action": "read", "target": "reading", "read_scope": "self",
                          "pattern": r"(\d+)米", "measure": "合成距离", "unit": "米", "retry": 0,
                          "when": {"condition": "fault", "equals": True}})
    case["assertions"] = [case["assertions"][0], {"id": "A-read", "at": "R1", "type": "value_present",
                                                "pattern": r"\d+", "oracle_refs": [COMMON_RULE]}]
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    step = run["steps"][1]
    assert step["attempts"][0]["locate"]["scroll"][0]["sent"] is True
    assert step["outcome"] in ("pending", "skipped"), step
    assert not step.get("measurement"), step
    assert not any(measurement.get("step") == "R1" for measurement in run["measurements"])
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert not any(event["event"] == "measure" and event.get("step") == "R1" for event in events)
    assert _assertions(run)["A-read"]["result"] not in ("pass", "not_applicable")
    assert run["verdict"]["verdict"] == "pending"
    _coverage(run, verified=("A-common",), unverified=("A-read",))
