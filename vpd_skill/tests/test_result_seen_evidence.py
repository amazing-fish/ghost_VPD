"""#56：时间窗口断言不能把缺少样本当作未出现；可选清理错误仍须留证。"""
import pytest
from conftest import confirm
from verdict_fixtures import COPY_RULE
from vpdtest.devices import LAUNCHER
from vpdtest.knowledge import KnowledgeStore
from vpdtest.verdict import case_verdict, evaluate_assertion


def _knowledge(ws):
    confirm(ws, COPY_RULE)
    knowledge = KnowledgeStore(ws.root)
    knowledge.bind({"app": "motor_hm", "account_state": "logged_out"})
    return knowledge


def _seen_result(ws, kind, tree_status, *, monitor, state="undetermined", expected=False):
    obs = {"obs_id": "obs-synthetic", "fresh": True, "tree": {"status": tree_status},
           "foreground": LAUNCHER if state == "out_of_app" else "app.huawei.hmos.motor",
           "process_alive": state != "out_of_app",
           "page": {"state": state, "active": [], "process_alive": state != "out_of_app"}, "texts": []}
    step = {"outcome": "passed", "final_obs": obs["obs_id"]}
    if monitor:
        step["monitor"] = {"timeline": [{"t": 0, "obs": obs["obs_id"], "state": state, "new_texts": []}]}
    assertion = {"id": "A1", "at": "S1", "type": kind, "expected": expected,
                 "pattern": "故障提示", "state": "out_of_app", "oracle_refs": [COPY_RULE]}
    return evaluate_assertion(assertion, {"S1": step}, lambda _: (obs, None),
                              _knowledge(ws), ws.app("motor_hm"))


@pytest.mark.parametrize("kind", ["text_seen", "state_seen"])
@pytest.mark.parametrize("monitor", [False, True], ids=["final_observation", "monitor_timeline"])
@pytest.mark.parametrize("tree_status", ["empty", "unavailable", "not_collected"])
def test_missing_temporal_evidence_cannot_prove_absence(verdict_ws, kind, monitor, tree_status):
    # 没有确定状态/可用文字样本时 expected=false 也只能未知；不能制造通过。
    result = _seen_result(verdict_ws, kind, tree_status, monitor=monitor)
    assert result["result"] == "undeterminable", result


@pytest.mark.parametrize("monitor", [False, True], ids=["final_observation", "monitor_timeline"])
def test_state_seen_can_use_independent_reliable_out_of_app_evidence(verdict_ws, monitor):
    # 目标不在前台时不采树是正常行为；可靠的桌面/进程证据仍能证明 out_of_app。
    result = _seen_result(verdict_ws, "state_seen", "not_collected", monitor=monitor,
                          state="out_of_app", expected=True)
    assert result["result"] == "pass", result


def _cleanup(outcome):
    return {"step_id": "C1", "action": "stop_app", "outcome": outcome,
            "required": False, "attribution": "tool", "reason": "synthetic optional cleanup result",
            "final_status": "device_error" if outcome == "error" else "policy_denied",
            "final_obs": "obs-cleanup", "attempts": [{"n": 1, "status": "device_error",
            "error_class": "timeout", "obs_after": "obs-cleanup", "dispatch": {
                "sent": True, "executor_ok": False, "error_class": "timeout",
                "error": "synthetic optional cleanup timeout"}}], "recoveries": []}


def _passed_assertion():
    return {"id": "A1", "at": "S1", "type": "text_present", "advisory": False,
            "result": "pass", "evidence": {"obs": "obs-business"}}


def test_optional_cleanup_error_preserves_product_pass_and_execution_failure(verdict_ws):
    verdict = case_verdict({"ok": True}, {"ok": True}, [], [_passed_assertion()],
                           _knowledge(verdict_ws), cleanup=[_cleanup("error")])
    assert verdict["verdict"] == "error"
    assert verdict["product_verdict"] == "passed" and verdict["product_failed"] is False
    assert verdict["execution"]["status"] == "error"
    errors = verdict["execution"]["errors"]
    assert len(errors) == 1 and errors[0]["kind"] == "cleanup"
    assert errors[0]["error_class"] == "timeout"
    assert errors[0]["error"] == "synthetic optional cleanup timeout"
    assert errors[0]["evidence"]["obs_after"] == "obs-cleanup"


def test_optional_cleanup_policy_gate_does_not_become_execution_error(verdict_ws):
    # 可选清理的正常拒绝保留旧兼容口径；区别于执行器实际超时。
    verdict = case_verdict({"ok": True}, {"ok": True}, [], [_passed_assertion()],
                           _knowledge(verdict_ws), cleanup=[_cleanup("blocked")])
    assert verdict["verdict"] == "passed"
