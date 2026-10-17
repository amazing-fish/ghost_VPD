"""Safe retry eligibility cannot replace handover after uncertain final dispatch."""

import pytest
from test_retry_dispatch_safety import _context, _counts, _step

from vpdtest.execute import Executor
from vpdtest.session import effect_unknown
from vpdtest.verdict import case_verdict


@pytest.mark.parametrize("retry", [0, 1, 2])
def test_exhausted_idempotent_navigation_requests_handover(ws, tmp_path, retry):
    ctx = _context(ws, tmp_path)
    for _ in range(retry + 2):
        ctx.device.fail_next("tap", sent=True, error_class="timeout")
    result = Executor(ctx).run_step(_step(idempotent=True, retry=retry), "exhausted-idempotent-fixture")
    assert _counts(ctx.device)["tap"] == retry + 1
    assert _counts(ctx.device, issued=True)["tap"] == retry + 1
    assert len(result["attempts"]) == retry + 1
    assert ctx.actions == retry + 1
    assert all(attempt["dispatch"]["sent"] is True and attempt["dispatch"]["executor_ok"] is False
               for attempt in result["attempts"])
    assert result["final_status"] == "device_error"
    assert result["outcome"] == "error" and result["attribution"] == "tool"
    assert result["first_failure"]["status"] == "device_error"
    assert effect_unknown(result) is True
    assert result.get("effect_unknown") is True
    assert result.get("action_required")
    verdict = case_verdict({"ok": True}, {"ok": True}, [result], [], None)
    assert verdict["verdict"] == "error" and verdict["attribution"] == "tool"
    assert verdict["action_required"]
    assert [alert["trigger"] for alert in verdict["alerts"] if alert["kind"] == "step_recovery"] == ["S1"]


def test_known_idempotent_business_failure_is_not_an_unknown_dispatch(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    result = Executor(ctx).run_step(_step(idempotent=True, retry=1, expect_text="从未出现的业务后置"),
                                   "exhausted-idempotent-fixture")
    assert _counts(ctx.device)["tap"] == len(result["attempts"]) == 2
    assert all(attempt["dispatch"]["sent"] is True and attempt["dispatch"]["executor_ok"] is True
               for attempt in result["attempts"])
    assert result["outcome"] == "fail_candidate" and result["attribution"] == "product"
    assert result["final_status"] == "postcondition_not_met"
    assert result["first_failure"]["status"] == "postcondition_not_met"
    assert effect_unknown(result) is False
    assert not result.get("effect_unknown", False)
    assert not result.get("action_required")
