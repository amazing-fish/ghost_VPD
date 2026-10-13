"""A verified cover recovery must preserve known complete input execution."""

from test_retry_dispatch_safety import _context, _counts, _step
from test_retry_recovery_feedback import _cover

from vpdtest.execute import Executor
from vpdtest.session import dispatched, effect_unknown


def test_confirmed_clear_input_after_usb_recovery_remains_known_business_failure(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    input_text = ctx.device.input_text

    def input_then_usb_cover(x, y, text):
        result = input_text(x, y, text)
        _cover(ctx)
        return result

    ctx.device.input_text = input_then_usb_cover
    result = Executor(ctx).run_step(_step(
        "input", clear=True, retry=1, expect_text="从未出现的业务后置",
    ), "recovered-known-input-fixture")
    attempt = result["attempts"][0]
    recovery = result["recoveries"][0]
    assert len(result["attempts"]) == 1
    assert _counts(ctx.device)["input_text"] == 1
    assert _counts(ctx.device)["key:delete"] == 4
    assert attempt["clear"]["sent"] is True and attempt["clear"]["ok"] is True
    assert attempt["dispatch"]["sent"] is True and attempt["dispatch"]["executor_ok"] is True
    assert attempt["status"] == "unobservable"
    assert result["first_failure"]["status"] == "unobservable"
    assert recovery["capability"]["status"] == "dismissed"
    assert recovery["post"]["status"] == "postcondition_not_met"
    assert recovery.get("effect_unknown") is False
    assert not result["outcome"].startswith("passed")
    assert dispatched(result) is True
    checks = {
        "step retains known effect": not result.get("effect_unknown", False),
        "session retains known effect": effect_unknown(result) is False,
    }
    assert all(checks.values()), checks
