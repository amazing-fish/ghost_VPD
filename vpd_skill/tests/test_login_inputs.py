"""Conditional login inputs and strict selected capabilities; synthetic devices only."""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import world
from test_dispatch_regression import _ctx
from test_condition_runner import condition_rig

from vpdtest.execute import Executor
from vpdtest.params import Params
from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json

__all__ = ("condition_rig",)


@pytest.mark.parametrize("container", ["direct", "flow", "recipe"])
@pytest.mark.parametrize("conditional", [False, True])
def test_only_conditionally_used_inputs_defer_missing_secret(ws, container, conditional):
    app = ws.app("qiankun_hm")
    child = {"action": "input", "target": "qk_pwd_input", "text": "${var:pw}"}
    step = child
    if container == "flow":
        child["text"] = "${param:password}"
        app.flows["test"] = {"steps": [child]}
        step = {"action": "flow", "flow": "test", "params": {"password": "${var:pw}"}}
    if container == "recipe":
        app.states.recipes["test"] = [child]
        step = {"action": "ensure_state", "state": "test"}
    if conditional:
        step["when"] = {"condition": "synthetic", "equals": True}
    case = {"test_data": {"pw": {"secret": "MISSING_TEST_ONLY"}}, "steps": [step]}
    params = Params(case["test_data"], {}, "r")
    assert params.is_secret_ref("${var:pw}")
    assert params.preflight_missing(case, app) == ([] if conditional else ["MISSING_TEST_ONLY"])
    case["steps"].append({"action": "input", "text": "${var:pw}"})
    assert params.preflight_missing(case, app) == ["MISSING_TEST_ONLY"]


@pytest.mark.parametrize("data,raw,local", [
    ({}, "${var:unknown}", None), ({}, "${param:unknown}", {}),
    ({"x": None}, "${var:x}", None), ({}, "${param:x}", {"x": None}),
    ({"x": ""}, "${var:x}", None), ({}, "", None), ({}, "  ", None),
])
def test_missing_input_stops_before_locator_scroll_clear_or_dispatch(ws, monkeypatch, data, raw, local):
    ctx = _ctx(ws, world("motor_hm_base.json"))
    ctx.params = Params(data, {}, "r")
    ex = Executor(ctx)
    def forbidden(*args, **kwargs):
        pytest.fail("missing input reached locator/scroll/clear/input")
    monkeypatch.setattr(ex, "_locate", forbidden)
    result = ex.run_step({"id": "I", "action": "input", "text": raw, "clear": True,
                          "target": {"allow_input": True, "strategies": [{"kind": "text", "value": "absent"}],
                                     "scroll": {"direction": "up", "max": 2}}, "_flow_params": local}, "test")
    assert result["outcome"] == "blocked" and result["final_status"] == "input_required"
    assert "重新" in result["action_required"] and ctx.actions == 0
    assert not result.get("effect_unknown")


@pytest.mark.parametrize("kind", ["flow", "ensure_state"])
@pytest.mark.parametrize("failure", ["missing", "skipped", "terminal", "unknown"])
def test_selected_optional_capability_propagates_required_failure(condition_rig, kind, failure):
    ws, template, make_world = condition_rig
    case = deepcopy(template)
    app = ws.app(case["app"])
    case["cleanup"] = []
    if failure == "missing":
        case["test_data"] = {"pw": {"secret": "MISSING_TEST_ONLY"}}
        children = [{"action": "input", "text": "${var:pw}", "clear": True,
                     "target": {"allow_input": True, "secret": True, "strategies": [{"kind": "text", "value": "absent"}],
                                "scroll": {"direction": "up", "max": 2}}}]
    elif failure in ("skipped", "unknown"):
        children = [{"action": "wait", "duration_s": 0,
                     "when": {"condition": "fault", "equals": False}}]
        if failure == "unknown":
            case["conditions"]["unseen"] = {"kind": "page", "true": {"type": "text_present", "text": "UNSEEN"},
                                               "false": {"type": "text_present", "text": "ALSO_UNSEEN"}}
            children[0]["when"]["condition"] = "unseen"
    else:
        children = [{"action": "wait", "duration_s": 0, "expect_state": "details", "timeout_s": 0, "retry": 0}]
    if kind == "flow":
        write_json(app.dir / "flows.json", {"flows": {"branch": {"steps": children}}})
        parent = {"flow": "branch"}
    else:
        states = read_json(app.dir / "states.json")
        states["recipes"]["details"] = children
        write_json(app.dir / "states.json", states)
        parent = {"state": "details"}
    case["steps"] += [{"id": "S2", "action": kind, "critical": False,
                       "when": {"condition": "fault", "equals": True}, **parent},
                      {"id": "S3", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0}]
    run = run_case(ws, case=case, device_spec=make_world(), propose=False)
    assert run["steps"][1]["critical"] is True
    assert run["steps"][2]["outcome"] == "not_run"
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert not [e for e in events if e["event"] == "dispatch" and e.get("step") == "S3" and e.get("sent") is not False]
    if failure == "missing":
        assert run["verdict"]["action_required"]
        assert not [e for e in events if e["event"] == "dispatch" and e.get("op") in ("input_text", "clear_text", "swipe")]
