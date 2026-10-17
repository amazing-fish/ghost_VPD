"""A previous recovery observation cannot replace a later transport failure."""

from test_retry_dispatch_safety import _context, _step

from vpdtest.execute import Executor


def test_prior_recovery_post_cannot_hide_the_final_attempt_transport_error(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    root = ctx.device.world["screens"]["fixture"]["root"]
    root["children"] += [
        {"t": "Text", "text": "良性覆盖", "show_if_flag": "covered", "b": [40, 800, 1280, 870]},
        {"t": "Button", "id": "dismiss", "text": "收起提示", "click": True, "b": [40, 900, 1280, 1050]},
        {"t": "Text", "text": "目标已达", "show_if_flag": "goal", "b": [40, 1100, 1280, 1200]},
    ]
    ctx.app.states.states["retry_fixture"]["forbid_text"] = ["目标已达"]
    ctx.app.states.states["retry_goal"] = {"require_text": ["目标已达"]}
    ctx.app.states.interrupts["benign_retry"] = {
        "class": "benign", "review": "reviewed", "require_text": ["良性覆盖"], "settle_s": 0,
        "dismiss_target": {"label": "fixture close", "side_effect": "dismiss_dialog",
                           "strategies": [{"kind": "id", "value": "dismiss"}]},
    }
    tap = ctx.device.tap
    navigation_calls, dismiss_calls = [], []

    def delayed_dismissal_then_retry_timeout(x, y):
        if y == 375 and len(navigation_calls) == 1:
            # This explicitly idempotent navigation is permitted a second
            # attempt; that attempt takes effect but loses its acknowledgement.
            ctx.device.fail_next("tap", sent=True, error_class="timeout")
        result = tap(x, y)
        if y == 375:
            navigation_calls.append((x, y))
            ctx.device.state["flags"]["covered"] = len(navigation_calls) == 1
            ctx.device.state["flags"]["goal"] = len(navigation_calls) > 1
        elif y == 975:
            dismiss_calls.append((x, y))
            if len(dismiss_calls) >= 4:
                ctx.device.state["flags"]["covered"] = False
        return result

    ctx.device.tap = delayed_dismissal_then_retry_timeout
    result = Executor(ctx).run_step(_step(
        idempotent=True, retry=1, expect_state="retry_goal",
    ), "recovery-attempt-binding-fixture")
    first, last = result["attempts"]
    recovery = result["recoveries"][0]
    assert len(navigation_calls) == 2
    assert first["status"] == "interrupted"
    assert last["status"] == "device_error" and last["dispatch"]["sent"] is True
    assert last["dispatch"]["executor_ok"] is False
    assert recovery["post"]["status"] == "postcondition_not_met"
    assert result["first_failure"]["status"] == "interrupted"
    assert recovery["post"]["obs_id"] != last["obs_after"]
    checks = {
        "final transport status retained": result["final_status"] == "device_error",
        "final tool attribution retained": result["outcome"] == "error" and result["attribution"] == "tool",
        "latest observation retained": result["final_obs"] == last["obs_after"],
    }
    assert all(checks.values()), checks
