"""PR #59：视觉建议仍须保留其所需截图的真实采集错误。"""
import json
from pathlib import Path

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.verdict import case_verdict, evaluate_assertion


@pytest.mark.parametrize("advisory,product", [(True, "passed"), (False, "pending")])
def test_visual_screenshot_timeout_is_execution_error_without_invalidating_text(verdict_ws, monkeypatch,
                                                                              advisory, product):
    # COPY 的文字断言使用有效 UI 树；视觉检查必须独立报告其截图失败，不能吞成普通建议。
    confirm(verdict_ws, COPY_RULE)
    case = verdict_ws.load_case(COPY_CASE)
    case["assertions"][1]["advisory"] = advisory
    raw = "synthetic screenshot output " * 40 + "SCREENSHOT_RAW_TAIL"

    def screenshot_timeout(self, local_path):
        return DeviceResult("screenshot", True, False, "timeout", "synthetic screenshot collection timeout", raw=raw)

    monkeypatch.setattr(ReplayDevice, "screenshot", screenshot_timeout)
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    text, visual = run["assertions"]
    assert text["result"] == "pass"
    assert visual["type"] == "visual_review" and visual["advisory"] is advisory
    assert visual["result"] == "error", visual
    assert visual["error_class"] == "timeout" and visual["stage"]
    verdict = run["verdict"]
    assert verdict["verdict"] == "error" and verdict["product_verdict"] == product
    assert verdict["product_failed"] is False
    assert verdict["execution"]["status"] == "error"
    errors = verdict["execution"]["errors"]
    assert len(errors) == 1 and errors[0]["id"] == visual["id"]
    serialized = json.dumps(errors, ensure_ascii=False)
    assert "synthetic screenshot collection timeout" in serialized
    assert raw in serialized
    assert run["steps"][0]["final_obs"] in serialized
    report = (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")
    assert "synthetic screenshot collection timeout" in report and "SCREENSHOT_RAW_TAIL" in report


@pytest.mark.parametrize("screenshot", [None, {"status": "skipped"}, {"status": "withheld"}],
                         ids=["missing", "skipped", "withheld"])
def test_unavailable_visual_input_without_collection_failure_stays_pending(verdict_ws, screenshot):
    # 未请求/策略扣留/缺少截图没有执行器故障证据，保留原有视觉建议口径。
    obs = {"obs_id": "obs-visual", "fresh": True, "tree": {"status": "ok"},
           "page": {"state": "my_tab"}}
    if screenshot is not None:
        obs["screenshot"] = screenshot
    knowledge = KnowledgeStore(verdict_ws.root)
    assertion = {"id": "A2", "at": "S1", "type": "visual_review", "advisory": True,
                 "prompt": "synthetic visual review"}
    visual = evaluate_assertion(assertion, {"S1": {"outcome": "passed", "final_obs": "obs-visual"}},
                                lambda _: (obs, None), knowledge, verdict_ws.app("motor_hm"))
    assert visual["result"] == "pending" and "error_class" not in visual
    text = {"id": "A1", "at": "S1", "type": "text_present", "advisory": False, "result": "pass"}
    verdict = case_verdict({"ok": True}, {"ok": True}, [], [text, visual], knowledge)
    assert verdict["verdict"] == verdict["product_verdict"] == "passed"
    assert verdict["execution"]["status"] == "completed" and verdict["execution"]["errors"] == []
