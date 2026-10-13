"""Issue #42: auxiliary transport evidence and recovery preserve motion failures."""

from test_retry_dispatch_safety import _context, _counts, _step

from vpdtest.execute import Executor
from vpdtest.safety import MotionGuard
from vpdtest.session import dispatched, effect_unknown


def test_failed_issued_locator_scroll_is_not_reissued_or_reported_unsent(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    # Locating the intended tap is allowed one scroll. A lost acknowledgement
    # may already have moved the page, so retrying the logical tap must not
    # silently reissue this unresolved auxiliary gesture.
    for _ in range(3):
        ctx.device.fail_next("swipe", sent=True, error_class="timeout")
    step = _step(target={
        "label": "missing target", "side_effect": "navigation",
        "strategies": [{"kind": "id", "value": "missing"}],
        "scroll": {"direction": "up", "max": 1},
    })
    result = Executor(ctx).run_step(step, "auxiliary-retry-fixture")
    checks = {
        "one physical scroll": _counts(ctx.device)["swipe"] == 1,
        "one logical attempt": len(result["attempts"]) == 1,
        "session retains dispatch": dispatched(result) is True,
        "session retains unknown effect": effect_unknown(result) is True,
        "handover is visible": bool(result.get("action_required")),
    }
    assert all(checks.values()), checks
    assert _counts(ctx.device)["tap"] == 0
    assert not result["outcome"].startswith("passed")
    assert result["first_failure"]["status"] == result["attempts"][0]["status"]


def test_late_stop_observed_during_recovery_cannot_become_a_pass(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    clock = [0.0]
    ctx.motion = MotionGuard({
        "max_motion_s": 1,
        "stopped_states": ["retry_fixture"],
        "moving_states": ["moving_fixture"],
    }, lambda: clock[0], ctx.store)
    ctx.motion.arm("S1", "fixture motion", "moving_fixture")
    executor = Executor(ctx)
    step = _step(effect="vehicle_motion", expect_state="retry_fixture")
    # The original attempt's mark predates cover recovery. The fresh stopped
    # observation is taken while recovery is completing, before its final
    # postcondition check. Use the real Observer -> MotionGuard path rather
    # than fabricating a breach counter or replacing _post.
    original_mark = ctx.motion.breaches
    attempt = {
        "n": 1, "action": "tap", "status": "unobservable", "step_id": "S1",
        "state_before": "moving_fixture", "motion_mark": original_mark,
        "dispatch": {"op": "tap", "sent": True, "executor_ok": True},
    }
    clock[0] = 2.0
    stopped, _ = ctx.observe("recovery:stopped-confirmation", screenshot=False)
    assert stopped["page"]["state"] == "retry_fixture"
    assert ctx.motion.armed is None
    assert ctx.motion.breaches == original_mark + 1
    assert ctx.motion.timeout["how"] == "late_stop"
    assert ctx.motion.locked()

    recovery = executor._after_recovery(step, attempt, {
        "action": "dismiss_system_dialog", "performed": True,
        "reason": "reviewed cover has been removed",
    }, "auxiliary-retry-fixture")
    result = executor._summarize(step, [attempt], [recovery])
    checks = {
        "post retains motion timeout": recovery["post"]["status"] == "motion_timeout",
        "recovery cannot pass": not result["outcome"].startswith("passed"),
        "handover is visible": bool(result.get("action_required")),
        "first error retained": result["first_failure"]["status"] == "unobservable",
        "timeout lock retained": ctx.motion.locked(),
    }
    assert all(checks.values()), checks
