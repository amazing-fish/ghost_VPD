"""Runner regressions: current operation guards precede each preparatory dismiss.

The app, rules and replay world are independent synthetic contracts. Observation
hooks model external UI changes between entry selection and primitive execution.
"""
from copy import deepcopy
from pathlib import Path

import pytest
from test_condition_runner import APP, FAULT, HEALTHY, condition_rig

from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import RunContext

__all__ = ("condition_rig",)


def _prepare(rig, kind, *, expiration=False, dialogs=1):
    ws, template, build = rig
    case = deepcopy(template)
    case["cleanup"] = []
    case["assertions"] = [case["assertions"][0]]
    guard = {"condition": "fault", "equals": True}
    if expiration:
        guard["at"] = "S1"
        case["conditions"]["fault"]["max_age_s"] = 5
    leaf = {"id": "S2", "action": "read", "target": "start", "read_scope": "self", "retry": 0}
    if "ensure" in kind:
        leaf = {"id": "S2", "action": "ensure_state", "state": "dashboard"}
    if kind.startswith("flow"):
        directory = ws.root / "apps" / APP
        write_json(directory / "flows.json", {"flows": {"guarded": {"steps": [leaf]}}})
        second = {"id": "S2", "action": "flow", "flow": "guarded", "when": guard}
        boundary = "S2.1:check" if "ensure" in kind else "S2.1#1:before"
    else:
        second = {**leaf, "when": guard}
        boundary = "S2:check" if "ensure" in kind else "S2#1:before"
    case["steps"].append(second)
    directory = ws.root / "apps" / APP
    state_path = directory / "states.json"
    states = read_json(state_path)
    targets = read_json(directory / "targets.json")
    for index in range(dialogs):
        ident = f"dismiss-{index}"
        targets["targets"][ident] = {"side_effect": "dismiss_dialog", "strategies": [{"kind": "id", "value": ident}]}
        states["interrupts"][ident] = {"class": "benign", "review": "reviewed", "settle_s": 0,
                                           "require_text": [f"合成提示-{index}"], "dismiss_target": ident}
    states["recipes"]["dashboard"] = [{"action": "wait", "duration_s": 0}]
    write_json(state_path, states)
    write_json(directory / "targets.json", targets)
    return ws, case, build((FAULT,), False, True), boundary


def _dialogs(count):
    return [{"t": "Dialog", "hide_if_flag": f"closed-{index}",
             "b": [0, 450 + index * 160, 400, 605 + index * 160], "children": [
                 {"t": "Text", "text": f"合成提示-{index}", "b": [10, 460 + index * 160, 380, 500 + index * 160]},
                 {"t": "Button", "id": f"dismiss-{index}", "text": f"关闭提示-{index}", "click": True,
                  "set_flag": {f"closed-{index}": True}, "b": [20, 520 + index * 160, 380, 585 + index * 160]}]}
            for index in range(count)]


def _hook(monkeypatch, boundary, change, *, count=1):
    original = RunContext.observe
    def observe(ctx, label="", screenshot=True, **kwargs):
        root = ctx.device.world["screens"]["dashboard"]["root"]
        if label == "S2:when":
            root["children"].extend(_dialogs(count))
        if label == boundary:
            if change == "expired":
                ctx.sleep(6)
            elif change in ("false", "unknown"):
                for child in root["children"]:
                    if child.get("text") == FAULT:
                        child["text"] = HEALTHY if change == "false" else "未提供任何独立条件证据"
        if change == "after_first_false" and label.endswith(":dismissed1"):
            for child in root["children"]:
                if child.get("text") == FAULT:
                    child["text"] = HEALTHY
        return original(ctx, label, screenshot, **kwargs)
    monkeypatch.setattr(RunContext, "observe", observe)


def _sent_taps(run):
    return [event for event in read_jsonl(Path(run["dir"]) / "events.jsonl")
            if event["event"] == "dispatch" and event["op"] == "tap" and event.get("sent") is not False]


@pytest.mark.parametrize("kind", ["primitive", "ensure", "flow_primitive", "flow_ensure"])
@pytest.mark.parametrize("change", ["false", "unknown", "expired", "true"])
def test_current_full_guard_chain_precedes_preparatory_dismiss(condition_rig, monkeypatch, kind, change):
    ws, case, device, boundary = _prepare(condition_rig, kind, expiration=change == "expired")
    _hook(monkeypatch, boundary, change)
    run = run_case(ws, case=case, device_spec=device, propose=False)
    second = run["steps"][1]
    if change == "true":
        assert len(_sent_taps(run)) == 1
        assert second["outcome"] == "passed"
    else:
        assert not _sent_taps(run), (second, _sent_taps(run))
        expected = "skipped" if change == "false" and not kind.startswith("flow_") else "pending"
        assert second["outcome"] == expected
        assert (second.get("condition") or {}).get("status") == ("false" if change == "false" else "unknown")


@pytest.mark.parametrize("kind", ["primitive", "ensure", "flow_primitive", "flow_ensure"])
@pytest.mark.parametrize("change", ["after_first_false", "after_first_epoch", "true"])
def test_each_dismiss_rechecks_guards_after_the_previous_auxiliary_action(condition_rig, monkeypatch, kind, change):
    ws, case, device, boundary = _prepare(condition_rig, kind, expiration=change == "after_first_epoch", dialogs=2)
    _hook(monkeypatch, boundary, change, count=2)
    run = run_case(ws, case=case, device_spec=device, propose=False)
    taps = _sent_taps(run)
    assert len(taps) == (2 if change == "true" else 1), (run["steps"][1], taps)
    second = run["steps"][1]
    assert second["outcome"] == ("passed" if change == "true" else "pending")


@pytest.mark.parametrize("kind", ["primitive", "ensure", "flow_primitive", "flow_ensure"])
@pytest.mark.parametrize("sent", [False, None, True])
def test_failed_preparation_keeps_receipt_and_uncertain_effect_handoff(condition_rig, monkeypatch, kind, sent):
    from vpdtest.devices import DeviceResult
    from vpdtest.devices.replay import ReplayDevice
    ws, case, device, boundary = _prepare(condition_rig, kind)
    _hook(monkeypatch, boundary, "true")
    original = ReplayDevice.tap
    def timeout(self, x, y):
        if sent is not False:
            original(self, x, y)
        return DeviceResult("tap", sent, False, "timeout", "preparation acknowledgement lost", raw="preparation raw receipt")
    monkeypatch.setattr(ReplayDevice, "tap", timeout)
    run = run_case(ws, case=case, device_spec=device, propose=False)
    second = run["steps"][1]
    leaf = second["sub_steps"][0] if kind.startswith("flow") else second
    assert second["outcome"] == "error" and leaf["final_status"] == "preparation_error"
    assert not leaf.get("measurement")
    assert leaf["execution_error"]["stage"] == "prepare_interrupt"
    assert "preparation raw receipt" in str(run["verdict"]["execution"]["errors"])
    assert bool(second.get("effect_unknown") or leaf.get("effect_unknown")) is (sent is not False)
    assert bool(run["verdict"].get("action_required")) is (sent is not False)


@pytest.mark.parametrize("kind", ["primitive", "ensure", "flow_primitive", "flow_ensure"])
def test_preparation_budget_refusal_stays_blocked(condition_rig, monkeypatch, kind):
    ws, case, device, boundary = _prepare(condition_rig, kind)
    _hook(monkeypatch, boundary, "true")
    policy_path = ws.root / "policy.json"
    policy = read_json(policy_path)
    policy["max_actions_per_run"] = 1  # S1 launch spends the budget; no dismiss is sent.
    write_json(policy_path, policy)
    run = run_case(ws, case=case, device_spec=device, propose=False)
    assert not _sent_taps(run)
    assert run["steps"][1]["outcome"] == "blocked"
    assert run["verdict"]["execution"]["status"] == "blocked"
    assert not run["verdict"]["execution"]["errors"]


@pytest.mark.parametrize("kind", ["primitive", "ensure", "flow_primitive", "flow_ensure"])
def test_preparation_policy_refusal_stays_blocked(condition_rig, monkeypatch, kind):
    ws, case, device, boundary = _prepare(condition_rig, kind)
    _hook(monkeypatch, boundary, "true")
    target_path = ws.root / "apps" / APP / "targets.json"
    targets = read_json(target_path)
    targets["targets"]["dismiss-0"].update(forbid_tap=True, forbid_reason="合成策略拒绝该准备动作")
    write_json(target_path, targets)
    run = run_case(ws, case=case, device_spec=device, propose=False)
    assert not _sent_taps(run)
    assert run["steps"][1]["outcome"] == "blocked"
    leaf = run["steps"][1]["sub_steps"][0] if kind.startswith("flow") else run["steps"][1]
    assert leaf["final_status"] == "policy_denied" and not leaf.get("measurement")
    assert run["verdict"]["execution"]["status"] == "blocked"
    assert not run["verdict"]["execution"]["errors"]


@pytest.mark.parametrize("kind", ["primitive", "ensure"])
def test_failed_unlock_inside_preparation_preserves_its_unknown_physical_effect(condition_rig, monkeypatch, kind):
    from vpdtest.devices import DeviceResult
    from vpdtest.devices.replay import ReplayDevice
    ws, case, device, boundary = _prepare(condition_rig, kind)
    _hook(monkeypatch, boundary, "true")
    original_locked = ReplayDevice.screen_locked
    original_observe = RunContext.observe
    def observe(ctx, label="", screenshot=True, **kwargs):
        result = original_observe(ctx, label, screenshot, **kwargs)
        if label == boundary:
            ctx.device._preparation_lock_pending = True
        return result
    def screen_locked(self):
        if getattr(self, "_preparation_lock_pending", False):
            self.state["locked"] = True
        return original_locked(self)
    def wake(self):
        return DeviceResult("wake", None, False, "timeout", "unlock may have been sent", raw="unlock raw uncertain receipt")
    monkeypatch.setattr(RunContext, "observe", observe)
    monkeypatch.setattr(ReplayDevice, "screen_locked", screen_locked)
    monkeypatch.setattr(ReplayDevice, "wake", wake)
    run = run_case(ws, case=case, device_spec=device, propose=False)
    step = run["steps"][1]
    assert step["outcome"] == "error"
    assert step.get("effect_unknown") and step.get("action_required")
    assert step["dismissed"][-1]["sent"] is None
    assert step["dismissed"][-1]["dismiss_sent"] is False
    assert step["dismissed"][-1]["unlock"]["status"] == "indeterminate"
    assert "unlock raw uncertain receipt" in str(run["verdict"]["execution"]["errors"])
    assert run["verdict"].get("action_required")
