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
def test_all_skipped_children_never_become_passed_parent(verdict_ws, kind):
    executor = _executor(verdict_ws)
    child = {"action": "back", "when": {"condition": "car_visible", "equals": False}}
    if kind == "flow":
        executor.ctx.app.flows["synthetic"] = {"steps": [child, child]}
        step = {"id": "F1", "action": "flow", "flow": "synthetic"}
    else:
        executor.ctx.app.states.recipes["synthetic"] = [child, child]
        step = {"id": "F1", "action": "ensure_state", "state": "synthetic"}
    result = executor.run_step(step, "synthetic")
    assert result["outcome"] == "skipped"
    assert len(result["sub_steps"]) == 2 and all(s["outcome"] == "skipped" for s in result["sub_steps"])
    assert executor.ctx.actions == 0


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
