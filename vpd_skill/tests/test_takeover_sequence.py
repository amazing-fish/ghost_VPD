"""Unresolved physical uncertainty stops business sequences, not compensation."""
import pytest

from conftest import world
from verdict_fixtures import NAV_CASE
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json


def run_scenario(ws, monkeypatch, *, sent=None, mode="steps", known_failure=False, cleanup=False):
    case = ws.load_case(NAV_CASE)
    launch = {"id": "START", "action": "launch", "retry": 0}
    uncertain = {"id": "UNKNOWN", "action": "back", "critical": False, "retry": 0}
    after = {"id": "AFTER", "action": "home", "retry": 0}
    if known_failure:
        uncertain.update(expect_text="SYNTHETIC_ABSENT_TEXT", timeout_s=0)
    case.update(steps=[launch, uncertain, after], cleanup=[], assertions=[])
    if mode == "flow":
        write_json(ws.app("motor_hm").dir / "flows.json", {"flows": {
            "inner": {"steps": [uncertain, after]},
            "outer": {"steps": [{"action": "flow", "flow": "inner", "critical": False},
                                 {"action": "launch", "retry": 0}]},
        }})
        case["steps"] = [launch, {"id": "UNKNOWN", "action": "flow", "flow": "outer", "critical": False}, after]
    elif mode == "recipe":
        path = ws.app("motor_hm").dir / "states.json"
        states = read_json(path)
        states["recipes"]["my_tab"] = [uncertain, {
            "action": "tap", "target": "tab_my", "expect_state": "my_tab", "retry": 0}]
        write_json(path, states)
        case["steps"] = [launch, {"id": "UNKNOWN", "action": "ensure_state", "state": "my_tab"}, after]
    if cleanup:
        case["cleanup"] = [{"id": "CLEANUP", "action": "stop_app", "retry": 0}]
    calls = []
    original_key, original_tap = ReplayDevice.key, ReplayDevice.tap
    original_launch, original_stop = ReplayDevice.launch, ReplayDevice.stop

    def key(device, name):
        calls.append(name)
        if name == "back" and not known_failure:
            return DeviceResult("key", sent, False, "timeout", "synthetic acknowledgement")
        return original_key(device, name)

    def tap(device, *args):
        calls.append("tap")
        return original_tap(device, *args)

    def start(device, *args):
        calls.append("launch")
        return original_launch(device, *args)

    def stop(device, *args):
        calls.append("stop_app")
        return original_stop(device, *args)

    monkeypatch.setattr(ReplayDevice, "key", key)
    monkeypatch.setattr(ReplayDevice, "tap", tap)
    monkeypatch.setattr(ReplayDevice, "launch", start)
    monkeypatch.setattr(ReplayDevice, "stop", stop)
    return run_case(ws, case=case, device_spec=world("motor_hm_base.json"), propose=False), calls


@pytest.mark.parametrize("sent", [None, True])
@pytest.mark.parametrize("mode", ["steps", "flow", "recipe"])
def test_unknown_receipt_halts_actual_business_sequence(verdict_ws, monkeypatch, sent, mode):
    run, calls = run_scenario(verdict_ws, monkeypatch, sent=sent, mode=mode)
    assert calls == ["launch", "back"]
    failed, after = run["steps"][1:]
    assert failed["outcome"] == "error"
    assert failed["effect_unknown"] and failed["action_required"]
    assert after["outcome"] == "not_run" and after["blocked_by"] == "UNKNOWN"
    assert run["verdict"]["action_required"]
    if mode == "flow":
        inner = failed["sub_steps"][0]
        assert len(failed["sub_steps"]) == len(inner["sub_steps"]) == 1
        assert inner["action_required"] and inner["effect_unknown"]
        assert failed["failure_source"] == "UNKNOWN.1.1"
    elif mode == "recipe":
        assert len(failed["sub_steps"]) == 1
        assert failed["failure_source"] == "UNKNOWN.1"


@pytest.mark.parametrize("mode", ["steps", "flow", "recipe"])
def test_reliably_unsent_noncritical_failure_still_continues(verdict_ws, monkeypatch, mode):
    run, calls = run_scenario(verdict_ws, monkeypatch, sent=False, mode=mode)
    assert "home" in calls
    assert run["steps"][-1]["outcome"].startswith("passed")


def test_known_business_failure_does_not_become_unknown_takeover(verdict_ws, monkeypatch):
    run, calls = run_scenario(verdict_ws, monkeypatch, known_failure=True)
    failed = run["steps"][1]
    assert failed["action_required"] and not failed.get("effect_unknown")
    assert "home" in calls and run["steps"][-1]["outcome"].startswith("passed")


def test_unknown_business_action_keeps_independent_cleanup(verdict_ws, monkeypatch):
    run, calls = run_scenario(verdict_ws, monkeypatch, cleanup=True)
    assert calls == ["launch", "back", "stop_app"]
    assert run["steps"][-1]["outcome"] == "not_run"
    assert run["cleanup"][0]["outcome"].startswith("passed")


def test_recovered_history_is_not_a_new_handoff():
    from vpdtest.execute import unresolved_takeover
    result = {"outcome": "passed_after_recovery", "effect_unknown": True,
              "attempts": [{"effect_unknown": True, "action_required": True}]}
    assert not unresolved_takeover(result)
    assert not unresolved_takeover({"action_required": True})


@pytest.mark.parametrize("safety", [False, True])
def test_safety_child_sequence_preserves_best_effort(monkeypatch, safety):
    from vpdtest.execute import Executor
    executor = object.__new__(Executor)
    calls = []

    def execute(step, _case):
        calls.append(step["id"])
        result = {"step_id": step["id"], "critical": False, "attempts": [],
                  "recoveries": [], "adapted": False, "outcome": "passed"}
        if len(calls) == 1:
            result.update(outcome="error", effect_unknown=True, action_required="synthetic handoff")
        return result

    monkeypatch.setattr(executor, "run_step", execute)
    result = executor._run_children({"id": "F", "flow": "synthetic", "_safety": safety}, "test",
                                    [{"action": "back"}, {"action": "wait"}],
                                    {"action": "flow", "step_id": "F"})
    assert calls == (["F.1", "F.2"] if safety else ["F.1"])
    assert bool(result.get("action_required")) is (not safety)


def test_possible_write_keeps_owned_cleanup_after_business_handoff(verdict_ws, monkeypatch):
    from vpdtest.execute import Executor
    case = verdict_ws.load_case(NAV_CASE)
    target = {"label": "synthetic tag", "side_effect": "user_data",
              "strategies": [{"kind": "text", "value": "synthetic tag"}]}
    case.update(authorizations=["user_data"], assertions=[],
                steps=[{"id": "WRITE", "action": "tap", "target": target, "critical": False},
                       {"id": "AFTER", "action": "home"}],
                cleanup=[{"id": "RESTORE", "action": "tap", "target": target, "after_dispatch": "WRITE"}])
    calls = []

    def execute(_self, step, _case):
        calls.append(step["id"])
        result = {"step_id": step["id"], "action": step["action"], "critical": step.get("critical", True),
                  "outcome": "passed", "attempts": [], "recoveries": [], "adapted": False}
        if step["id"] == "WRITE":
            result.update(outcome="error", effect_unknown=True, action_required="synthetic handoff",
                          attempts=[{"dispatch": {"sent": None, "executor_ok": False}}])
        return result

    monkeypatch.setattr(Executor, "run_step", execute)
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False, authorize=["user_data"])
    assert calls == ["WRITE", "RESTORE"]
    assert run["steps"][1]["outcome"] == "not_run"
    assert run["cleanup"][0]["cleanup_guard"]["may_have_changed_user_data"]


def test_cleanup_flow_halts_its_sequence_but_keeps_independent_cleanup(verdict_ws, monkeypatch):
    case = verdict_ws.load_case(NAV_CASE)
    case.update(steps=[{"id": "START", "action": "launch", "retry": 0}], assertions=[],
                cleanup=[{"id": "C1", "action": "flow", "flow": "restore"},
                         {"id": "C2", "action": "stop_app", "retry": 0}])
    write_json(verdict_ws.app("motor_hm").dir / "flows.json", {"flows": {"restore": {"steps": [
        {"action": "back", "critical": False, "retry": 0}, {"action": "home", "retry": 0},
    ]}}})
    calls = []
    original_key, original_stop = ReplayDevice.key, ReplayDevice.stop

    def key(device, name):
        calls.append(name)
        result = original_key(device, name)
        return DeviceResult("key", None, False, "timeout", "synthetic lost receipt") if name == "back" else result

    def stop(device, *args):
        calls.append("stop_app")
        return original_stop(device, *args)

    monkeypatch.setattr(ReplayDevice, "key", key)
    monkeypatch.setattr(ReplayDevice, "stop", stop)
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert calls == ["back", "stop_app"]
    first, second = run["cleanup"]
    assert len(first["sub_steps"]) == 1 and first["effect_unknown"] and first["action_required"]
    assert second["outcome"] == "passed"
