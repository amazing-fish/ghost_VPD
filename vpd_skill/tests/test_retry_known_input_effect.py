"""A confirmed clear/input with missing business postcondition is not unknown."""

import pytest
from test_goal_loop import _input_world, _start, cli
from test_retry_dispatch_safety import _context, _counts, _step

from vpdtest import cases
from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.session import dispatched, effect_unknown, from_session, trail


@pytest.mark.parametrize("retry", [0, 2])
def test_confirmed_clear_and_input_keeps_known_business_failure(ws, tmp_path, retry):
    ctx = _context(ws, tmp_path)
    result = Executor(ctx).run_step(_step("input", clear=True, retry=retry,
                                         expect_text="从未出现的业务后置"), "known-input-fixture")
    attempt = result["attempts"][0]
    assert attempt["clear"]["sent"] is True and attempt["clear"]["ok"] is True
    assert attempt["dispatch"]["sent"] is True and attempt["dispatch"]["executor_ok"] is True
    assert attempt["status"] == "postcondition_not_met"
    assert len(result["attempts"]) == 1
    assert _counts(ctx.device)["tap"] == _counts(ctx.device)["input_text"] == 1
    assert _counts(ctx.device)["key:delete"] == 4
    assert result["first_failure"]["status"] == "postcondition_not_met"
    assert not result["outcome"].startswith("passed")
    assert dispatched(result) is True
    assert effect_unknown(result) is False
    assert not result.get("effect_unknown", False)


def test_known_input_failure_does_not_require_effect_review_after_session_conversion(ws, tmp_path):
    spec = _input_world(tmp_path)
    _start(ws, spec, "known-input")
    output = cli(ws, "act", "input", "--select", "xpath=//TextInput", "--allow-input", "--clear",
                 "--text", "新值", "--expect-text", "从未出现的业务后置", "--timeout", "0",
                 "--app", "motor_hm", "--session", "known-input", "--device", spec)
    result = output["result"]
    assert result["attempts"][0]["status"] == "postcondition_not_met"
    assert result["attempts"][0]["clear"]["ok"] is True
    assert result["attempts"][0]["dispatch"]["executor_ok"] is True
    sdir = ws.runs_dir / "session-known-input"
    candidate = from_session(sdir, "TC-KNOWN-INPUT", ws.app("motor_hm"), None, ["A1=启动不属于输入验证"])
    issues = cases.lint(candidate, ws.app("motor_hm"), KnowledgeStore(ws.root))
    unknown_review_issues = [issue for issue in issues if "已下发但结果未确认" in issue["problem"]]
    assert unknown_review_issues == []
    kept = candidate["steps"][0]
    assert kept["from_session"]["effect_unknown"] is False
    assert kept["from_session"]["outcome"] == "fail_candidate"
    assert not kept.get("effect_reviewed")
    session_item = next(item for item in trail(sdir)["items"] if item.get("step_id") == "A2")
    assert session_item["dispatched"] is True and session_item["effect_unknown"] is False


@pytest.mark.parametrize("failure", ["clear_error", "input_error", "input_unsent"])
def test_incomplete_clear_input_still_has_unknown_effect(ws, tmp_path, failure):
    ctx = _context(ws, tmp_path)
    if failure == "clear_error":
        ctx.device.fail_next("key:move_end", sent=True)
    else:
        ctx.device.fail_next("input_text", sent=failure == "input_error")
    result = Executor(ctx).run_step(_step("input", clear=True, retry=0), "known-input-fixture")
    assert len(result["attempts"]) == 1
    assert result["first_failure"]
    assert not result["outcome"].startswith("passed")
    assert _counts(ctx.device)["tap"] == 1
    assert dispatched(result) is True
    assert effect_unknown(result) is True
    assert result["action_required"]
