"""Recovered business failure uses fresh evidence while preserving first failure."""

import pytest
from test_retry_dispatch_safety import _context, _counts, _step
from test_retry_recovery_feedback import _cover

from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.session import effect_unknown
from vpdtest.util import read_json, write_json
from vpdtest.verdict import case_verdict


@pytest.mark.parametrize("clear", [False, True])
def test_recovered_input_business_failure_retains_product_verdict(ws, tmp_path, clear):
    ctx = _context(ws, tmp_path)
    input_text = ctx.device.input_text

    def input_then_usb_cover(x, y, text):
        result = input_text(x, y, text)
        _cover(ctx)
        return result

    ctx.device.input_text = input_then_usb_cover
    oracle_id = "K-RECOVERED-VERDICT-FIXTURE"
    items_path = ws.root / "knowledge" / "items.json"
    items = read_json(items_path)
    items["items"].append({
        "id": oracle_id, "kind": "requirement", "status": "confirmed",
        "subject": "motor_hm.retry_fixture.input", "statement": "合成测试要求输入后出现指定业务后置文本",
        "applies_to": {"app": "motor_hm"}, "expected": {"text": "从未出现的业务后置"},
        "source": {"type": "test", "ref": "pytest isolated synthetic oracle"},
    })
    write_json(items_path, items)
    knowledge = KnowledgeStore(ws.root)
    knowledge.bind({"app": "motor_hm", "app_version": "0.0.0-retry-fixture"})
    assert knowledge.oracle_status([oracle_id])["status"] == "confirmed"

    result = Executor(ctx).run_step(_step(
        "input", clear=clear, retry=1, expect_text="从未出现的业务后置", oracle_refs=[oracle_id],
    ), "recovered-verdict-fixture")
    attempt, recovery = result["attempts"][0], result["recoveries"][0]
    assert _counts(ctx.device)["input_text"] == 1
    assert _counts(ctx.device)["tap"] == (2 if clear else 1)
    assert len(result["attempts"]) == 1
    assert recovery["capability"]["status"] == "dismissed"
    assert recovery["post"]["status"] == "postcondition_not_met"
    assert recovery["retry_original"] is False
    assert attempt["status"] == "unobservable"
    assert result["first_failure"]["status"] == "unobservable"
    assert result["first_failure"]["obs_before"] == attempt["obs_before"]
    assert result["first_failure"]["obs_after"] == attempt["obs_after"]
    assert recovery["post"]["obs_id"] != attempt["obs_after"]
    assert effect_unknown(result) is False

    assert result["outcome"] == "fail_candidate"
    assert result["attribution"] == "product"
    assert result["final_status"] == "postcondition_not_met"
    assert result["final_obs"] == recovery["post"]["obs_id"]
    verdict = case_verdict({"ok": True}, {"ok": True}, [result], [], knowledge)
    assert verdict["verdict"] == "failed"
    assert verdict["attribution"] == "product"
    assert verdict["product_failed"] is True
    assert verdict["first_anomaly"]["status"] == "unobservable"
    assert all(alert["kind"] == "step_recovery" for alert in verdict["alerts"])
