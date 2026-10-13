"""Original business conditions remain mandatory after cover recovery."""

from test_retry_dispatch_safety import _context, _counts, _step
from test_sysdialog import REGISTRY

from vpdtest.execute import Executor
from vpdtest.util import read_json


def test_dismissing_cover_does_not_complete_or_reissue_original_input(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    cover = read_json(ws.root.parent / "fixtures" / "worlds" / "motor_hm_usb_dialog.json")
    ctx.device.world["screens"]["usb_dialog"] = cover["patch_screens"]["usb_dialog"]
    ctx.observer.dialogs = REGISTRY["dialogs"]
    input_text = ctx.device.input_text

    def input_then_cover(*args):
        result = input_text(*args)
        ctx.device.state["system_dialog"] = "usb_dialog"
        return result

    ctx.device.input_text = input_then_cover
    result = Executor(ctx).run_step(_step("input", expect_text="从未出现的原业务后置"), "retry-oracle-fixture")
    assert _counts(ctx.device)["input_text"] == 1
    assert _counts(ctx.device)["tap"] == 1  # only the reviewed USB cancel button
    assert ctx.device.state["system_dialog"] is None
    assert len(result["attempts"]) == 1
    assert not result["outcome"].startswith("passed")
    recovery = result["recoveries"][0]
    assert recovery["action"] == "dismiss_system_dialog" and recovery["performed"]
    assert recovery["post"]["observed"] == "retry_fixture"
    assert recovery["post"]["status"] == "postcondition_not_met"
    assert recovery["retry_original"] is False
    assert result["first_failure"]["status"] == "unobservable"
    assert result["action_required"]
