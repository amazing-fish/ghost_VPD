"""Issue #42: partial input execution must survive session/report consumption."""

import pytest
from test_retry_dispatch_safety import _context, _counts, _dispatch_events, _step

from vpdtest.execute import Executor
from vpdtest.session import dispatched, effect_unknown
from vpdtest.verdict import case_verdict


def _partial_input(ctx):
    ctx.device.fail_next("input_text", sent=False)
    return Executor(ctx).run_step(_step("input", clear=True), "retry-consumer-fixture")


def _recovery_alert(verdict):
    return [alert for alert in verdict["alerts"] if alert["kind"] == "step_recovery"]


def test_partial_clear_is_visible_to_session_and_step_verdict(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    result = _partial_input(ctx)
    assert _counts(ctx.device)["input_text"] == 1
    assert dispatched(result) is True
    assert effect_unknown(result) is True
    verdict = case_verdict({"ok": True}, {"ok": True}, [result], [], None)
    assert verdict["verdict"] == "error"
    assert verdict["action_required"]
    assert [alert["trigger"] for alert in _recovery_alert(verdict)] == ["S1"]


def test_clear_then_changed_input_policy_retains_partial_execution(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    key = ctx.device.key

    def changed_input(name):
        result = key(name)
        if name == "delete" and _counts(ctx.device)["key:delete"] == 4:
            # The input becomes a user-data editor after keyboard/clear layout
            # changes. Its newly visible label requires authorization.
            ctx.device.bound["name"] = "收藏"
        return result

    ctx.device.key = changed_input
    result = Executor(ctx).run_step(_step("input", clear=True), "retry-consumer-fixture")
    assert result["final_status"] == "policy_denied"
    assert _counts(ctx.device)["input_text"] == 0
    assert _counts(ctx.device)["key:delete"] == 4
    assert "dispatch" not in result["attempts"][0]
    assert any(event.get("aggregate") and event["sent"] for event in _dispatch_events(ctx))
    assert dispatched(result) is True
    assert effect_unknown(result) is True
    assert result["action_required"]


def test_clear_then_foreground_gate_retains_partial_execution(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    observe = ctx.observe

    def switch_after_snapshot(label="", screenshot=True, **kwargs):
        result = observe(label, screenshot, **kwargs)
        if label.endswith(":after-clear"):
            # User switches app after the clear observation, before input's
            # final dispatcher gate. The latest input location is still valid.
            ctx.device.state["foreground"] = "com.example.other"
        return result

    ctx.observe = switch_after_snapshot
    result = Executor(ctx).run_step(_step("input", clear=True), "retry-consumer-fixture")
    assert result["final_status"] == "foreground_changed"
    assert _counts(ctx.device)["input_text"] == 0
    assert _counts(ctx.device)["key:delete"] == 4
    assert "dispatch" not in result["attempts"][0]
    assert any(event["error_class"] == "foreground_changed" and event["sent"] is False
               for event in _dispatch_events(ctx))
    assert dispatched(result) is True
    assert effect_unknown(result) is True
    assert result["action_required"]


@pytest.mark.parametrize("retry", [0, 2])
def test_single_attempt_and_exhausted_budget_still_request_handover(ws, tmp_path, retry):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next("input_text")
    result = Executor(ctx).run_step(_step("input", retry=retry), "retry-consumer-fixture")
    assert _counts(ctx.device)["input_text"] == 1
    assert result["action_required"] and effect_unknown(result)
    assert case_verdict({"ok": True}, {"ok": True}, [result], [], None)["action_required"]


@pytest.mark.parametrize("retry", [0, 2])
def test_unknown_clear_send_cannot_be_overwritten_by_a_later_unsent_key(ws, tmp_path, retry):
    ctx = _context(ws, tmp_path)
    tap = ctx.device.tap

    def unknown_tap(*point):
        result = tap(*point)
        result.sent = None
        return result

    ctx.device.tap = unknown_tap
    ctx.device.fail_next("key:move_end", sent=False)
    result = Executor(ctx).run_step(_step("input", clear=True, retry=retry), "retry-consumer-fixture")
    assert _counts(ctx.device)["tap"] == 1
    assert result["attempts"][0]["clear"]["sent"] is None
    assert dispatched(result) and effect_unknown(result) and result["action_required"]


def test_clear_completed_without_input_is_still_an_incomplete_single_attempt(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    ctx.device.hide_input_after_delete = True
    result = Executor(ctx).run_step(_step("input", clear=True, retry=0), "retry-consumer-fixture")
    assert _counts(ctx.device)["input_text"] == 0
    assert result["attempts"][0]["clear"]["ok"] is True
    assert dispatched(result) and effect_unknown(result) and result["action_required"]


def test_uncertain_motion_send_keeps_the_watchdog_armed(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    ctx.authorized = {"vehicle_motion"}
    ctx.device.fail_next("tap", sent=None)
    result = Executor(ctx).run_step(_step(effect="vehicle_motion", retry=0), "retry-consumer-fixture")
    assert _counts(ctx.device)["tap"] == 1
    assert ctx.motion.armed and result["action_required"]


def test_required_cleanup_keeps_partial_input_handover_alert(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    result = _partial_input(ctx)
    result["required"] = True
    verdict = case_verdict({"ok": True}, {"ok": True}, [], [], None, cleanup=[result])
    assert verdict["verdict"] == "error"
    assert verdict["action_required"]
    assert [alert["trigger"] for alert in _recovery_alert(verdict)] == ["S1"]


def test_flow_substep_keeps_partial_input_handover_alert(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    ctx.app.flows["partial_input"] = {"steps": [_step("input", clear=True)]}
    ctx.device.fail_next("input_text", sent=False)
    result = Executor(ctx).run_step({"id": "F1", "action": "flow", "flow": "partial_input"},
                                   "retry-consumer-fixture")
    assert _counts(ctx.device)["input_text"] == 1
    assert dispatched(result) is True and effect_unknown(result) is True
    verdict = case_verdict({"ok": True}, {"ok": True}, [result], [], None)
    assert verdict["verdict"] == "error"
    assert verdict["action_required"]
    assert [alert["trigger"] for alert in _recovery_alert(verdict)] == ["F1.1"]
