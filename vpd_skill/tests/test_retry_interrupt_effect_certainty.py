"""Registered blocking interruptions preserve observed execution certainty."""

import pytest
from test_retry_dispatch_safety import _context, _counts, _step

from vpdtest.execute import Executor
from vpdtest.session import dispatched, effect_unknown


@pytest.mark.parametrize("known_state", [True, False])
def test_confirmed_tap_with_registered_blocking_popup_preserves_state_certainty(ws, tmp_path, known_state):
    ctx = _context(ws, tmp_path)
    ctx.device.world["screens"]["fixture"]["root"]["children"].append({
        "t": "Dialog", "show_if_flag": "blocked_popup", "b": [100, 800, 1230, 1300],
        "children": [{"t": "Text", "text": "需人工处理", "b": [140, 850, 1190, 970]}],
    })
    ctx.app.states.interrupts["blocking_fixture"] = {
        "class": "blocking", "review": "reviewed", "require_text": ["需人工处理"],
    }
    if not known_state:
        ctx.app.states.states.clear()
    tap = ctx.device.tap

    def accepted_tap_then_blocking_popup(x, y):
        result = tap(x, y)
        ctx.device.state["flags"]["blocked_popup"] = True
        return result

    ctx.device.tap = accepted_tap_then_blocking_popup
    result = Executor(ctx).run_step(_step(retry=1), "interrupt-effect-certainty-fixture")
    attempt = result["attempts"][0]
    assert len(result["attempts"]) == 1 and _counts(ctx.device)["tap"] == 1
    assert attempt["dispatch"]["sent"] is True and attempt["dispatch"]["executor_ok"] is True
    assert result["final_status"] == result["first_failure"]["status"] == "interrupted"
    assert attempt["post"]["observed"] == ("retry_fixture" if known_state else "unknown")
    assert attempt["post"]["interrupts"] == [{
        "id": "blocking_fixture", "class": "blocking", "review": "reviewed",
    }]
    assert result["recoveries"][0]["performed"] is False
    assert not result["outcome"].startswith("passed")
    assert dispatched(result) is True and result["action_required"]
    expected_unknown = not known_state
    checks = {
        "step certainty retained": bool(result.get("effect_unknown")) is expected_unknown,
        "session certainty retained": effect_unknown(result) is expected_unknown,
    }
    assert all(checks.values()), checks
