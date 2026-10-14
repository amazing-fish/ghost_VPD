"""Shared diagnosis consumes the final recovery result, not historical failure."""

import pytest
from test_retry_dispatch_safety import _context, _counts, _step
from test_retry_recovery_feedback import _cover

from vpdtest.commands import diagnose
from vpdtest.execute import Executor


def _input_with_cover(ctx, *, clear, expect_text):
    input_text = ctx.device.input_text

    def input_then_cover(x, y, text):
        result = input_text(x, y, text)
        _cover(ctx)
        return result

    ctx.device.input_text = input_then_cover
    return Executor(ctx).run_step(_step(
        "input", clear=clear, retry=1, expect_text=expect_text,
    ), "recovered-diagnosis-fixture")


@pytest.mark.parametrize("clear", [False, True])
def test_diagnosis_uses_current_recovered_business_failure(ws, tmp_path, clear):
    ctx = _context(ws, tmp_path)
    result = _input_with_cover(ctx, clear=clear, expect_text="从未出现的业务后置")
    attempt, recovery = result["attempts"][0], result["recoveries"][0]
    assert _counts(ctx.device)["input_text"] == 1
    assert _counts(ctx.device)["tap"] == (2 if clear else 1)
    assert result["outcome"] == "fail_candidate" and result["attribution"] == "product"
    assert result["final_status"] == "postcondition_not_met"
    assert result["final_obs"] == recovery["post"]["obs_id"]
    assert recovery["after_attempt"] == attempt["n"]
    assert attempt["status"] == result["first_failure"]["status"] == "unobservable"
    assert result["first_failure"]["obs_after"] == attempt["obs_after"]
    diagnosis = diagnose(result)
    assert diagnosis["status"] == result["final_status"]
    assert diagnosis["post"]["obs_id"] == result["final_obs"]
    assert diagnosis["post"]["status"] == "postcondition_not_met"


def test_successful_recovery_has_no_failure_diagnosis(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    result = _input_with_cover(ctx, clear=True, expect_text="新值")
    assert _counts(ctx.device)["input_text"] == 1
    assert result["outcome"] == "passed_after_recovery"
    assert result["first_failure"]["status"] == "unobservable"
    assert result["recoveries"][0]["post"]["status"] == "ok"
    assert result["recoveries"][0]["after_attempt"] == result["attempts"][0]["n"]
    assert result["final_obs"] == result["recoveries"][0]["post"]["obs_id"]
    assert diagnose(result) is None


@pytest.mark.parametrize("missing", ["observation", "action"])
def test_partial_legacy_recovery_metadata_does_not_crash_or_invent_observation(missing):
    result = {"outcome": "fail_candidate", "final_status": "postcondition_not_met",
              "attempts": [{"n": 1, "status": "unobservable"}],
              "recoveries": [{"after_attempt": 1, "post": {"status": "postcondition_not_met"}}]}
    if missing == "action":
        result["final_obs"] = result["recoveries"][0]["post"]["obs_id"] = "obs-recovered"
    diagnosis = diagnose(result)
    assert diagnosis["status"] == "postcondition_not_met"
    if missing == "observation":
        assert "recovery" not in diagnosis and "obs_id" not in diagnosis
    else:
        assert diagnosis["post"]["obs_id"] == diagnosis["obs_id"] == result["final_obs"]
