"""PR #59 审查反例：父流程通过和 advisory 不能吞掉执行错误。"""
import json
from pathlib import Path

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.observe import Observer
from vpdtest.runner import run_case
from vpdtest.util import write_json
from vpdtest.verdict import case_verdict


def _case(ws):
    confirm(ws, COPY_RULE)
    case = ws.load_case(COPY_CASE)
    case["steps"] = [{"id": "S1", "action": "ensure_state", "state": "my_tab"}]
    case["assertions"] = [{"id": "A1", "at": "S1", "type": "text_present", "text": "注册/登录",
                           "oracle_refs": [COPY_RULE]}]
    return case


@pytest.mark.parametrize("cleanup_required", [None, False, True])
def test_noncritical_error_in_nested_flow_cannot_be_hidden_by_parent_pass(verdict_ws, monkeypatch, cleanup_required):
    # inner 的 launch 失败后继续 wait；inner/outer 都完成且产品文字可见，错误仍须单独汇总。
    case = _case(verdict_ws)
    write_json(verdict_ws.root / "apps" / "motor_hm" / "flows.json", {"flows": {
        "synthetic_inner": {"steps": [
            {"action": "launch", "critical": False, "retry": 0},
            {"action": "wait", "duration_s": 0}]},
        "synthetic_outer": {"steps": [
            {"action": "flow", "flow": "synthetic_inner"},
            {"action": "wait", "duration_s": 0}]}}})
    if cleanup_required is None:
        case["steps"].append({"id": "S2", "action": "flow", "flow": "synthetic_outer"})
        case["assertions"][0]["at"] = "S2"
    else:
        case["cleanup"] = [{"id": "C1", "action": "flow", "flow": "synthetic_outer", "required": cleanup_required}]
    original_launch = ReplayDevice.launch
    calls = []

    def launch_with_optional_failure(self, bundle, ability):
        calls.append((bundle, ability))
        if len(calls) == 2:
            return DeviceResult("launch", False, False, "timeout", "synthetic nested launch timeout",
                                raw="NESTED_DISPATCH_RAW_EVIDENCE")
        return original_launch(self, bundle, ability)

    monkeypatch.setattr(ReplayDevice, "launch", launch_with_optional_failure)
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    outer = run["steps"][1] if cleanup_required is None else run["cleanup"][0]
    inner, after_inner = outer["sub_steps"]
    child, after_error = inner["sub_steps"]
    assert len(calls) == 2
    assert child["outcome"] == "error" and child["critical"] is False
    assert after_error["outcome"] == after_inner["outcome"] == "passed"
    assert outer["outcome"] == inner["outcome"] == "passed"
    assert run["assertions"][0]["result"] == "pass"
    verdict = run["verdict"]
    assert verdict["verdict"] == "error", verdict
    assert verdict["product_verdict"] == "passed"
    assert verdict["execution"]["status"] == "error"
    errors = verdict["execution"]["errors"]
    assert len(errors) == 1 and errors[0]["stage"] == "dispatch"
    assert errors[0]["evidence"]["parent_step"] == inner["step_id"]
    if cleanup_required is not None:
        assert errors[0]["cleanup_required"] is cleanup_required
        report = (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")
        assert "子步骤执行错误" in report and "全部完成" not in report
    assert errors[0]["error_class"] == "timeout"
    serialized = json.dumps(errors, ensure_ascii=False)
    for evidence in (child["step_id"], child["final_obs"], "synthetic nested launch timeout",
                     "NESTED_DISPATCH_RAW_EVIDENCE"):
        assert evidence in serialized
    assert "NESTED_DISPATCH_RAW_EVIDENCE" in (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")


def _with_advisory(case, text="注册/登录"):
    case["steps"].append({"id": "S2", "action": "wait", "duration_s": 0, "retry": 0})
    case["assertions"].append({"id": "A2", "at": "S2", "type": "text_present", "text": text,
                               "advisory": True, "oracle_refs": [COPY_RULE]})
    return case


def test_advisory_collection_error_remains_execution_error_with_required_product_pass(verdict_ws, monkeypatch):
    # 仅 advisory 所用的 S2 终态树采集失败；required 的 S1 保留独立有效证据。
    case = _with_advisory(_case(verdict_ws))
    original_observe, original_dump = Observer.observe, ReplayDevice.dump_tree

    def dump_with_error(self, bundle, path):
        if getattr(self, "synthetic_collection_error", False):
            return DeviceResult("dump_tree", True, False, "timeout", "synthetic advisory tree timeout",
                                raw="ADVISORY_COLLECTION_RAW_EVIDENCE")
        return original_dump(self, bundle, path)

    def observe_with_error(self, label="", screenshot=True):
        self.device.synthetic_collection_error = label == "S2#1:after"
        try:
            return original_observe(self, label, screenshot)
        finally:
            self.device.synthetic_collection_error = False

    monkeypatch.setattr(ReplayDevice, "dump_tree", dump_with_error)
    monkeypatch.setattr(Observer, "observe", observe_with_error)
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert run["assertions"][0]["result"] == "pass"
    assert run["assertions"][1]["advisory"] is True
    assert run["assertions"][1]["result"] == "error"
    verdict = run["verdict"]
    assert verdict["verdict"] == "error", verdict
    assert verdict["product_verdict"] == "passed" and verdict["product_failed"] is False
    assert verdict["execution"]["status"] == "error"
    errors = verdict["execution"]["errors"]
    assert len(errors) == 1 and errors[0]["id"] == "A2"
    assert errors[0]["stage"] == "observation" and errors[0]["error_class"] == "timeout"
    serialized = json.dumps(errors, ensure_ascii=False)
    assert "synthetic advisory tree timeout" in serialized
    assert "ADVISORY_COLLECTION_RAW_EVIDENCE" in serialized
    assert run["steps"][1]["final_obs"] in serialized


def test_advisory_product_failure_stays_suggestion_and_does_not_fail_case(verdict_ws):
    case = _with_advisory(_case(verdict_ws), text="synthetic missing product copy")
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert run["assertions"][0]["result"] == "pass"
    assert run["assertions"][1]["result"] == "fail" and run["assertions"][1]["advisory"] is True
    verdict = run["verdict"]
    assert verdict["verdict"] == verdict["product_verdict"] == "passed"
    assert verdict["execution"]["status"] == "completed" and verdict["execution"]["errors"] == []
    assert [item["id"] for item in verdict["suggestions"]] == ["A2"]


def test_successful_cleanup_does_not_request_manual_data_recovery(verdict_ws):
    run = run_case(verdict_ws, case=_case(verdict_ws), device_spec=world("motor_hm_base.json"), propose=False)
    assert all(step["outcome"].startswith("passed") for step in run["cleanup"])
    report = (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")
    cleanup = report.split("## 清理", 1)[1].split("## ", 1)[0]
    assert "全部完成" in cleanup
    assert "未恢复" not in cleanup and "人工核对" not in cleanup


@pytest.mark.parametrize("parent_outcome", ["passed", "error"])
def test_sibling_errors_are_distinct_and_parent_copy_is_not_counted_again(parent_outcome):
    children = [{"step_id": f"F.{n}", "outcome": "error", "attribution": "tool", "reason": "same error",
                 "attempts": [{"n": 1, "status": "device_error", "error_class": "timeout", "obs_after": "same-obs"}],
                 "final_status": "device_error"} for n in (1, 2)]
    parent = {"step_id": "F", "outcome": parent_outcome, "sub_steps": children, "attempts": []}
    assertion = {"id": "A", "at": "F", "type": "state_is", "result": "pass", "advisory": False}
    verdict = case_verdict({"ok": True}, {"ok": True}, [parent], [assertion], None)
    assert [error["step"] for error in verdict["execution"]["errors"]] == ["F.1", "F.2"]
    assert verdict["product_verdict"] == "passed"


def test_recovered_child_error_is_not_reintroduced_by_recursive_projection():
    child = {"step_id": "F.1", "outcome": "passed_after_recovery", "execution_error": {"error_class": "timeout"},
             "attempts": [{"n": 1, "status": "device_error"}], "recoveries": [{"status": "ok"}]}
    parent = {"step_id": "F", "outcome": "passed_after_recovery", "sub_steps": [child], "attempts": []}
    assertion = {"id": "A", "at": "F", "type": "state_is", "result": "pass", "advisory": False}
    verdict = case_verdict({"ok": True}, {"ok": True}, [parent], [assertion], None)
    assert verdict["verdict"] == "passed" and verdict["execution"] == {"status": "completed", "errors": []}
