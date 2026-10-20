"""Real Executor/Observer/Dispatcher operation selection, using synthetic contracts."""

import pytest
from conftest import world
from test_dispatch_regression import _ctx

from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.util import read_json


def _executor(ws):
    ctx = _ctx(ws, world("motor_hm_base.json"))
    ctx.facts = {"app": "motor_hm", "app_version": "3.0.3.200-replay"}
    ctx.conditions = {"car_visible": {"kind": "page", "true": {"type": "text_present", "text": "爱车"},
                                      "false": {"type": "text_present", "text": "SYNTHETIC_COUNTER_PROOF"}}}
    return Executor(ctx)


@pytest.mark.parametrize("equals,expected", [(True, "passed"), (False, "skipped")])
def test_when_uses_current_positive_evidence_without_counting_skip_as_execution(verdict_ws, equals, expected):
    executor = _executor(verdict_ws)
    result = executor.run_step({"id": "S1", "action": "wait", "duration_s": 0,
                                "when": {"condition": "car_visible", "equals": equals}}, "synthetic")
    assert result["outcome"] == expected
    assert result["condition"]["status"] == ("true" if equals else "false")
    assert bool(result["attempts"]) is equals
    assert executor.ctx.actions == 0
    stored = read_json(executor.ctx.store.dir / "obs" / result["condition"]["obs"] / "observation.json")
    assert stored["action_epoch"] == 0 and stored["clock_s"] > 0
    assert stored["scope"] == {"app": "motor_hm", "app_version": "3.0.3.200-replay"}


def test_unknown_when_does_not_dispatch_and_collection_error_is_separate(verdict_ws):
    executor = _executor(verdict_ws)
    executor.ctx.conditions["car_visible"]["true"]["text"] = "UNOBSERVED"
    step = {"id": "S1", "action": "back", "when": {"condition": "car_visible", "equals": True}}
    unknown = executor.run_step(step, "synthetic")
    assert unknown["outcome"] == "pending" and executor.ctx.actions == 0
    executor.ctx.device.dump_tree = lambda *a: DeviceResult("dump_tree", False, False, error_class="command_failed", error="raw failure")
    failed = executor.run_step({**step, "id": "S2"}, "synthetic")
    assert failed["outcome"] == "error" and failed["execution_error"]["error"] == "raw failure"
    assert executor.ctx.actions == 0


def test_historical_when_is_invalidated_by_dispatched_action(verdict_ws):
    executor = _executor(verdict_ws)
    first = executor.run_step({"id": "S1", "action": "wait", "duration_s": 0}, "synthetic")
    assert first["outcome"] == "passed"
    action = executor.run_step({"id": "S2", "action": "back"}, "synthetic")
    assert action["outcome"] == "passed" and executor.ctx.actions == 1
    result = executor.run_step({"id": "S3", "action": "back", "when": {
        "condition": "car_visible", "equals": True, "at": "S1"}}, "synthetic")
    assert result["outcome"] == "pending"
    assert "失效" in result["reason"] and executor.ctx.actions == 1


@pytest.mark.parametrize("kind", ["flow", "ensure_state"])
def test_required_skipped_child_stops_parent_before_the_next_child(verdict_ws, kind):
    executor = _executor(verdict_ws)
    child = {"action": "back", "when": {"condition": "car_visible", "equals": False}}
    if kind == "flow":
        executor.ctx.app.flows["synthetic"] = {"steps": [child, child]}
        step = {"id": "F1", "action": "flow", "flow": "synthetic"}
    else:
        executor.ctx.app.states.recipes["synthetic"] = [child, child]
        step = {"id": "F1", "action": "ensure_state", "state": "synthetic"}
    result = executor.run_step(step, "synthetic")
    assert result["outcome"] == "pending"
    assert len(result["sub_steps"]) == 1 and result["sub_steps"][0]["outcome"] == "skipped"
    assert executor.ctx.actions == 0


@pytest.mark.parametrize("kind", ["flow", "ensure_state"])
@pytest.mark.parametrize("scenario", ["read", "all_skipped", "error"])
def test_composite_results_preserve_evidence_and_stop_at_required_error(verdict_ws, monkeypatch, kind, scenario):
    executor = _executor(verdict_ws)
    children = [{"action": "read", "read_scope": "page", "measure": "navigation", "pattern": "爱车"},
                {"action": "wait", "duration_s": 0}]
    if scenario == "all_skipped":
        children = [{**child, "critical": False, "when": {"condition": "car_visible", "equals": False}}
                    for child in children]
    elif scenario == "error":
        children[0] = {"action": "tap", "target": "tab_my", "retry": 0}
        monkeypatch.setattr(executor.ctx.device, "tap", lambda *args: DeviceResult(
            "tap", False, False, "command_failed", "synthetic command failure", raw="original receipt"))
    if kind == "flow":
        executor.ctx.app.flows["synthetic"] = {"steps": children}
        step = {"id": "F1", "action": "flow", "flow": "synthetic"}
    else:
        # ensure_state promises an actual known state, not just child completion.
        if scenario == "read":
            children[-1] = {"action": "tap", "target": "tab_my", "retry": 0}
        executor.ctx.app.states.recipes["my_tab"] = children
        step = {"id": "F1", "action": "ensure_state", "state": "my_tab"}
    result = executor.run_step(step, "synthetic")
    subs = result["sub_steps"]
    expected = {"read": "passed", "all_skipped": "skipped", "error": "error"}[scenario]
    if kind == "ensure_state" and scenario == "all_skipped":
        expected = "fail_candidate"  # Route not selected; the target is still absent.
    assert result["outcome"] == expected
    assert result["attempts"] == [attempt for child in subs for attempt in child["attempts"]]
    assert result["final_obs"] == subs[-1]["final_obs"]
    assert result["adapted"] is False and result["recoveries"] == []
    assert ("measurement" in result) is (kind == "flow")
    if scenario == "read":
        assert subs[0]["measurement"]["value"] and len(subs) == (2 if kind == "flow" else 3)
        if kind == "flow":
            assert result["measurement"] == subs[0]["measurement"]
        assert result["first_failure"] is None
    elif scenario == "all_skipped":
        assert all(s["outcome"] == "skipped" and not s["attempts"] for s in subs[:2])
        assert len(subs) == (2 if kind == "flow" else 3)
        assert bool(result["attempts"]) is (kind == "ensure_state")
    else:
        assert len(subs) == 1 and result["first_failure"] == subs[0]["first_failure"]
        assert "original receipt" in str(result["first_failure"])
        if kind == "flow":
            assert result["measurement"] is None
    assert executor.ctx.actions == int(kind == "ensure_state" and scenario == "read")


def test_optional_unknown_child_remains_visible_in_flow(verdict_ws):
    executor = _executor(verdict_ws)
    executor.ctx.conditions["unknown"] = {"kind": "page", "true": {"type": "text_present", "text": "A"},
                                           "false": {"type": "text_present", "text": "B"}}
    executor.ctx.app.flows["synthetic"] = {"steps": [
        {"action": "back", "critical": False, "when": {"condition": "unknown", "equals": True}},
        {"action": "wait", "duration_s": 0}]}
    result = executor.run_step({"id": "F1", "action": "flow", "flow": "synthetic"}, "synthetic")
    assert result["outcome"] == "pending" and result["sub_steps"][0]["outcome"] == "pending"
    assert result["sub_steps"][0]["condition"]["status"] == "unknown"


def test_safety_hold_precedes_conditional_observation(verdict_ws):
    executor = _executor(verdict_ws)
    executor.ctx.safety_hold = {"trigger": "synthetic", "ts": "fixture"}
    result = executor.run_step({"id": "S1", "action": "back", "when": {
        "condition": "car_visible", "equals": False}}, "synthetic")
    assert result["outcome"] == "blocked" and result["final_status"] == "safety_hold"
    assert not list((executor.ctx.store.dir / "obs").iterdir())


def test_attempt_rechecks_historical_when_after_internal_action(verdict_ws, monkeypatch):
    executor = _executor(verdict_ws)
    executor.run_step({"id": "S1", "action": "wait", "duration_s": 0}, "synthetic")
    def internal_action(obs, tree, *args, **kwargs):
        result = executor.ctx.dispatcher.send("key", "back", preconditions=executor.ctx.dispatcher.app_conditions("back"),
                                             att={"action": "back"})
        assert result.sent
        current, current_tree = executor.ctx.observe("synthetic-internal-action")
        return current, current_tree, []
    monkeypatch.setattr(executor, "_clear_interrupts", internal_action)
    result = executor.run_step({"id": "S2", "action": "back", "when": {
        "condition": "car_visible", "equals": True, "at": "S1"}}, "synthetic")
    assert result["outcome"] == "pending" and executor.ctx.actions == 1
    assert result["condition"]["status"] == "unknown" and "失效" in result["reason"]
    assert not result.get("first_failure")
