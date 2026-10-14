"""Codex P2 feedback: recovery has its own dispatch and business conditions."""

import pytest
from test_retry_dispatch_safety import _context, _counts, _step
from test_sysdialog import REGISTRY

from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.session import dispatched, effect_unknown
from vpdtest.util import read_json, read_jsonl


@pytest.mark.parametrize("idempotent", [True, False])
def test_benign_dismiss_ack_does_not_spend_safe_navigation_retry(ws, tmp_path, idempotent):
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

    def delayed_dismissal(x, y):
        result = tap(x, y)
        if y == 375:  # The original operation; first click only exposes a benign prompt.
            navigation_calls.append((x, y))
            ctx.device.state["flags"]["covered"] = len(navigation_calls) == 1
            ctx.device.state["flags"]["goal"] = len(navigation_calls) > 1
        elif y == 975:
            dismiss_calls.append((x, y))
            # The reviewed prompt outlives the three automatic cleanup samples.
            # It then disappears during the dedicated recovery attempt, while
            # the safely selectable close button remains on the page.
            if len(dismiss_calls) >= 4:
                ctx.device.state["flags"]["covered"] = False
        return result

    ctx.device.tap = delayed_dismissal
    result = Executor(ctx).run_step(_step(idempotent=idempotent, retry=1, expect_state="retry_goal"),
                                   "retry-feedback-fixture")
    assert len(navigation_calls) == (2 if idempotent else 1)
    assert len(dismiss_calls) == 5
    recovery = result["recoveries"][0]
    assert recovery["action"] == "dismiss_interrupt"
    assert recovery["attempt"]["dispatch"]["executor_ok"] is True
    assert recovery["attempt"]["post"]["status"] == "postcondition_not_met"
    assert result["first_failure"]["status"] == "interrupted"
    if idempotent:
        assert result["outcome"] == "passed_after_recovery"
        assert len(result["attempts"]) == 2
    else:
        assert not result["outcome"].startswith("passed")
        assert len(result["attempts"]) == 1
        assert result["action_required"]


def _cover(ctx):
    spec = read_json(ctx.ws.root.parent / "fixtures" / "worlds" / "motor_hm_usb_dialog.json")
    ctx.device.world["screens"]["usb_dialog"] = spec["patch_screens"]["usb_dialog"]
    ctx.observer.dialogs = REGISTRY["dialogs"]
    ctx.device.state["system_dialog"] = "usb_dialog"


@pytest.mark.parametrize("original", ["wait", "idempotent_tap"])
@pytest.mark.parametrize("failure", ["dismiss_failed", "indeterminate"])
def test_failed_system_recovery_retains_its_own_dispatch(ws, tmp_path, original, failure):
    ctx = _context(ws, tmp_path)
    _cover(ctx)
    if original == "idempotent_tap":
        ctx.device.state["system_dialog"] = None
    tap, dump_screen = ctx.device.tap, ctx.device.dump_screen
    original_calls, dismiss_calls = [], []
    fail_recheck = [False]

    def recovery_tap(x, y):
        if ctx.device.state["system_dialog"]:
            dismiss_calls.append((x, y))
            if failure == "dismiss_failed":
                ctx.device.fail_next("tap", sent=True, error_class="timeout")
            result = tap(x, y)
            fail_recheck[0] = failure == "indeterminate"
            return result
        original_calls.append((x, y))
        result = tap(x, y)
        ctx.device.state["system_dialog"] = "usb_dialog"
        return result

    def recheck_dump(path):
        if fail_recheck[0]:
            fail_recheck[0] = False
            return DeviceResult("dump_screen", True, False, "transport_unavailable", "fixture recheck lost")
        return dump_screen(path)

    ctx.device.tap, ctx.device.dump_screen = recovery_tap, recheck_dump
    step = _step("wait") if original == "wait" else _step(idempotent=True)
    result = Executor(ctx).run_step(step, "retry-feedback-fixture")
    capability = [e for e in read_jsonl(ctx.store.events_path)
                  if e["event"] == "capability_result" and e.get("capability") == "system_dialog"][-1]
    assert capability["status"] == failure
    assert capability["dismiss"]["sent"] is True
    assert capability["dismiss"]["ok"] is (failure == "indeterminate")
    assert len(dismiss_calls) == 1
    assert len(original_calls) == (0 if original == "wait" else 1)
    assert len(result["attempts"]) == 1
    assert dispatched(result) is True
    assert effect_unknown(result) is True
    assert result["action_required"]


@pytest.mark.parametrize("sent", [None, False])
def test_failed_system_recovery_distinguishes_unknown_from_unsent(ws, tmp_path, sent):
    ctx = _context(ws, tmp_path)
    _cover(ctx)
    ctx.device.fail_next("tap", sent=sent)
    result = Executor(ctx).run_step(_step("wait"), "retry-feedback-fixture")
    capability = [e for e in read_jsonl(ctx.store.events_path)
                  if e["event"] == "capability_result" and e.get("capability") == "system_dialog"][-1]
    assert capability["status"] == "dismiss_failed"
    assert _counts(ctx.device)["tap"] == 1
    if sent is False:
        assert dispatched(result) is False and effect_unknown(result) is False
        assert not result.get("action_required")
    else:
        assert dispatched(result) is True and effect_unknown(result) is True
        assert result["action_required"]
