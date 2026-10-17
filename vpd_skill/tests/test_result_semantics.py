"""#56 第一切片的合成结果契约；不修改或确认任何生产知识/资产。"""
import json
from pathlib import Path

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice, expand
from vpdtest.execute import classify
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.util import read_json
from vpdtest.verdict import case_verdict, evaluate_assertion


@pytest.mark.parametrize("attempt,attribution", [
    ({"status": "device_error", "error_class": "timeout"}, "tool"),
    ({"status": "device_error", "error_class": "device_offline"}, "environment"),
    ({"status": "observation_failed", "error_class": "transport_unavailable"}, "environment"),
    ({"status": "stop_not_confirmed"}, "tool"),
])
def test_execution_failures_are_errors(attempt, attribution):
    # 传输/执行错误与停止能力失败不能再混入一般 blocked 或产品失败。
    outcome, actual_attribution, reason = classify(attempt)
    assert (outcome, actual_attribution) == ("error", attribution)
    assert reason


@pytest.mark.parametrize("status", [
    "policy_denied", "budget_exhausted", "safety_hold", "interrupted",
    "target_disabled", "foreground_changed", "foreground_not_restored",
])
def test_normal_execution_gates_remain_blocked(status):
    # 策略、预算、现场交接和其它 APP 占屏不是命令/采集故障。
    assert classify({"status": status})[0] == "blocked"


def _knowledge(ws):
    confirm(ws, COPY_RULE)
    knowledge = KnowledgeStore(ws.root)
    knowledge.bind({"app": "motor_hm", "account_state": "logged_out"})
    return knowledge


def _step(outcome="passed", **extra):
    return {"step_id": "S1", "action": "tap", "outcome": outcome,
            "critical": True, "attempts": [], "recoveries": [],
            "oracle_refs": [COPY_RULE], "final_obs": "obs-1", **extra}


def _assertion(kind="text_present", **extra):
    return {"id": "A1", "at": "S1", "type": kind,
            "text": "注册/登录", "oracle_refs": [COPY_RULE], **extra}


def _observation(**extra):
    return {"obs_id": "obs-1", "fresh": True, "ts": "2026-10-17T00:00:00Z",
            "tree": {"status": "ok", "path": "obs/obs-1/tree.json"},
            "page": {"state": "my_tab", "active": ["my_tab"]},
            "screenshot": {"status": "ok", "path": "obs/obs-1/screen.png"},
            "texts": ["注册/登录"], **extra}


def _valid_tree(text="注册/登录"):
    return UiTree(expand({"t": "Column", "b": [0, 0, 400, 800], "children": [
        {"t": "Text", "b": [10, 10, 200, 60], "text": text}]}))


@pytest.mark.parametrize("kind,extra", [
    ("text_present", {}), ("state_is", {"state": "my_tab"}),
    ("text_seen", {"pattern": "注册/登录"}),
])
def test_required_tree_collection_failure_is_assertion_error(verdict_ws, kind, extra):
    # 显式的采集失败不能因未找到文字/状态而变成产品失败或仅待确认。
    obs = _observation(tree={"status": "error", "error_class": "timeout",
                             "error": "synthetic dumpLayout timeout"},
                       page={"state": "undetermined", "active": []}, texts=[])
    result = evaluate_assertion(_assertion(kind, **extra), {"S1": _step()},
                                lambda _: (obs, None), _knowledge(verdict_ws),
                                verdict_ws.app("motor_hm"))
    assert result["result"] == "error", result
    assert result["attribution"] == "tool"
    assert result["error_class"] == "timeout"
    assert result["stage"]
    assert "synthetic dumpLayout timeout" in json.dumps(result, ensure_ascii=False)
    assert "obs-1" in json.dumps(result["evidence"], ensure_ascii=False)


@pytest.mark.parametrize("tree_status,fresh", [("empty", True), ("stale", False), ("not_collected", True)])
def test_insufficient_observation_without_collection_error_stays_unknown(verdict_ws, tree_status, fresh):
    obs = _observation(tree={"status": tree_status}, fresh=fresh)
    result = evaluate_assertion(_assertion(), {"S1": _step()}, lambda _: (obs, None),
                                _knowledge(verdict_ws), verdict_ws.app("motor_hm"))
    assert result["result"] == "undeterminable", result


def test_screenshot_error_does_not_invalidate_usable_text_evidence(verdict_ws):
    obs = _observation(screenshot={"status": "error", "error_class": "timeout",
                                  "error": "synthetic screenshot timeout"})
    result = evaluate_assertion(_assertion(), {"S1": _step()}, lambda _: (obs, _valid_tree()),
                                _knowledge(verdict_ws), verdict_ws.app("motor_hm"))
    assert result["result"] == "pass", result


def test_product_error_copy_is_still_a_product_observation(verdict_ws):
    # 页面显示“加载失败”是被测行为，不能因异常字样认作工具 error。
    obs = _observation(texts=["加载失败，请重试"])
    assertion = evaluate_assertion(_assertion(), {"S1": _step()},
                                   lambda _: (obs, _valid_tree("加载失败，请重试")),
                                   _knowledge(verdict_ws), verdict_ws.app("motor_hm"))
    verdict = case_verdict({"ok": True}, {"ok": True}, [_step()], [assertion],
                           _knowledge(verdict_ws), step_ids={"S1"})
    assert assertion["result"] == "fail"
    assert verdict["verdict"] == "failed" and verdict["product_failed"] is True
    assert verdict["product_verdict"] == "failed"
    assert verdict["execution"]["status"] == "completed"


def _error_step(**extra):
    attempt = {"n": 1, "status": "device_error", "error_class": "timeout",
               "obs_before": "obs-1", "obs_after": "obs-2",
               "dispatch": {"op": "tap", "sent": True, "executor_ok": False,
                            "error_class": "timeout", "error": "synthetic dispatch timeout"}}
    return _step("error", step_id="S2", final_status="device_error", attribution="tool",
                 reason="synthetic dispatch timeout", attempts=[attempt], final_obs="obs-2", **extra)


def test_product_failure_and_tool_error_remain_independently_visible(verdict_ws):
    knowledge = _knowledge(verdict_ws)
    assertion = {**_assertion(), "advisory": False, "result": "fail",
                 "evidence": {"obs": "obs-1", "actual_text": "加载失败"}}
    verdict = case_verdict({"ok": True}, {"ok": True}, [_step(), _error_step()],
                           [assertion], knowledge, step_ids={"S1", "S2"})
    assert verdict["verdict"] == "error" and verdict["attribution"] == "tool"
    assert verdict["product_verdict"] == "failed" and verdict["product_failed"] is True
    assert verdict["failure_signature"] == ["assertion:A1:text_present=加载失败"]
    assert verdict["execution"]["status"] == "error"
    errors = verdict["execution"]["errors"]
    assert errors and errors[0]["stage"] and errors[0]["error_class"] == "timeout"
    assert "synthetic dispatch timeout" in json.dumps(errors, ensure_ascii=False)
    assert "obs-2" in json.dumps(errors, ensure_ascii=False)
    assert any(f["result"] == "failed" and f["attribution"] == "product" for f in verdict["findings"])


@pytest.mark.parametrize("attribution", ["tool", "environment"])
def test_historical_non_product_fail_candidate_does_not_become_product_failure(verdict_ws, attribution):
    # 旧摘要也可能包含 fail_candidate；已确认产品 Oracle 不能改写其工具/环境归因。
    step = _step("fail_candidate", attribution=attribution, final_status="stop_not_confirmed",
                 reason="synthetic capability failure")
    verdict = case_verdict({"ok": True}, {"ok": True}, [step], [],
                           _knowledge(verdict_ws), step_ids={"S1"})
    assert verdict["verdict"] not in ("passed", "failed")
    assert verdict["product_failed"] is False
    assert verdict["product_verdict"] == "pending"
    assert verdict["failure_signature"] == []


def test_run_case_dispatch_timeout_reports_error_with_original_evidence(verdict_ws, monkeypatch):
    # 真实 run -> Executor -> Dispatcher -> DeviceResult -> Verdict -> report 全链路；无真机调用。
    def timeout_launch(self, bundle, ability):
        return DeviceResult("launch", True, False, "timeout", "synthetic launch timeout raw evidence",
                            raw="prefix" * 100 + "FULL_RAW_TAIL")

    monkeypatch.setattr(ReplayDevice, "launch", timeout_launch)
    case = verdict_ws.load_case(COPY_CASE)
    case["steps"] = [{"id": "S1", "action": "launch", "expect_state": "@in_app", "retry": 0}]
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    step, verdict = run["steps"][0], run["verdict"]
    assert step["outcome"] == "error" and verdict["verdict"] == "error"
    assert step["attempts"][0]["dispatch"]["error"] == "synthetic launch timeout raw evidence"
    assert verdict["execution"]["status"] == "error"
    error = verdict["execution"]["errors"][0]
    assert error["stage"] and error["error_class"] == "timeout"
    serialized = json.dumps(error, ensure_ascii=False)
    assert "synthetic launch timeout raw evidence" in serialized
    assert step["final_obs"] in serialized
    assert "FULL_RAW_TAIL" in serialized
    assert "FULL_RAW_TAIL" in (Path(run["dir"]) / "events.jsonl").read_text(encoding="utf-8")
    assert run["validation_level"] == "offline_replay"
    assert read_json(Path(run["dir"]) / "run.json")["verdict"]["verdict"] == "error"
    assert verdict["label"] in (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")


def test_known_product_failure_is_still_reproduced_with_cleanup_error(verdict_ws, monkeypatch):
    from vpdtest.cases import content_hash

    _knowledge(verdict_ws)
    case = verdict_ws.load_case(COPY_CASE)
    case["cleanup"] = [{"id": "C1", "action": "stop_app", "retry": 0}]
    case["steps"] = [{"id": "S0", "action": "launch", "expect_state": "car_tab", "retry": 0},
                     {"id": "S1", "action": "tap", "target": "tab_my", "expect_state": "my_tab", "retry": 0}]
    initial = run_case(verdict_ws, case=case, device_spec=world("motor_hm_typo.json"), propose=False)
    assert initial["verdict"]["product_failed"]
    case["product_status"] = {"status": "known_failure", "since_hash": content_hash(case),
                              "signature": initial["verdict"]["failure_signature"], "issue": "synthetic"}
    monkeypatch.setattr(ReplayDevice, "stop", lambda *a: DeviceResult("stop_app", True, False, "timeout", "cleanup timeout"))
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_typo.json"), propose=False)
    assert run["verdict"]["verdict"] == "error"
    assert run["verdict"]["known_issue"]["reproduced"] is True
    assert run["verdict"]["product_verdict"] == "failed"
