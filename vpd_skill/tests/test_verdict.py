"""合成判定契约：知识与用例独立于生产语料，APP/策略/回放路径保持真实集成。"""
import pytest
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.verdict import evaluate_assertion
from vpdtest.util import read_json, write_json

from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE, NAV_CASE, NAV_RULE, SETTINGS_RULE


def test_pass_when_expectation_met_with_confirmed_oracle(verdict_ws):
    confirm(verdict_ws, NAV_RULE, SETTINGS_RULE)
    run = run_case(verdict_ws, NAV_CASE, device_spec=world("motor_hm_base.json"))
    v = run["verdict"]
    assert v["verdict"] == "passed", v["findings"]
    assert all(a["result"] == "pass" for a in run["assertions"] if not a["advisory"])
    assert v["suggestions"] and v["suggestions"][0]["id"] == "A4"  # 主观项只进入建议
    assert run["validation_level"] == "offline_replay"


def test_fail_when_confirmed_copy_rule_violated(verdict_ws):
    confirm(verdict_ws, COPY_RULE)
    run = run_case(verdict_ws, COPY_CASE, device_spec=world("motor_hm_typo.json"))
    v = run["verdict"]
    assert v["verdict"] == "failed" and v["attribution"] == "product"
    a1 = next(a for a in run["assertions"] if a["id"] == "A1")
    assert a1["result"] == "fail" and a1["evidence"]["obs"]


def test_unconfirmed_oracle_is_pending_not_pass(verdict_ws):
    run = run_case(verdict_ws, NAV_CASE, device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "pending"
    assert all(a["result"] == "pending" for a in run["assertions"])


def test_unconfirmed_oracle_with_violation_is_pending_not_fail(verdict_ws):
    run = run_case(verdict_ws, COPY_CASE, device_spec=world("motor_hm_typo.json"))
    a1 = next(a for a in run["assertions"] if a["id"] == "A1")
    assert a1["result"] == "pending" and a1["observed_holds"] is False
    assert run["verdict"]["verdict"] == "pending"


def test_locked_screen_blocks_as_environment(verdict_ws):
    confirm(verdict_ws, NAV_RULE, SETTINGS_RULE)
    run = run_case(verdict_ws, NAV_CASE, device_spec=world("motor_hm_locked.json"))
    v = run["verdict"]
    assert v["verdict"] == "blocked" and v["attribution"] == "environment"
    assert {c["check"] for c in run["preflight"]["checks"] if not c["ok"]} >= {"screen_unlocked"}
    assert all(s["outcome"] == "not_run" for s in run["steps"])


def test_unmet_env_requirement_blocks(verdict_ws):
    confirm(verdict_ws, COPY_RULE)
    run = run_case(verdict_ws, COPY_CASE, device_spec=world("motor_hm_base.json"), env={"account_state": "logged_in"})
    assert run["verdict"]["verdict"] == "blocked" and run["verdict"]["attribution"] == "environment"


def test_crash_fails_via_generic_invariant_even_if_business_oracle_unconfirmed(verdict_ws):
    run = run_case(verdict_ws, NAV_CASE, device_spec=world("motor_hm_crash.json"))
    v = run["verdict"]
    assert v["verdict"] == "failed" and v["attribution"] == "product"
    s3 = next(s for s in run["steps"] if s["step_id"] == "S3")
    assert s3["final_status"] == "anomaly" and len(s3["attempts"]) == 1  # 崩溃不重试、不恢复
    assert next(s for s in run["steps"] if s["step_id"] == "S4")["outcome"] == "not_run"


def test_absence_on_empty_tree_is_undeterminable(verdict_ws, tmp_path):
    obs = {"obs_id": "obs-x", "page": {"state": "undetermined"}, "tree": {"status": "empty"}, "screenshot": {}}
    from vpdtest.devices.replay import placeholder_tree
    from vpdtest.tree import UiTree
    a = {"id": "A", "at": "S1", "type": "text_absent", "text": "网络异常", "oracle_refs": ["K-GEN-001"]}
    r = evaluate_assertion(a, {"S1": {"outcome": "passed", "final_obs": "obs-x"}},
                           lambda _: (obs, UiTree(placeholder_tree())), KnowledgeStore(verdict_ws.root), verdict_ws.app("motor_hm"))
    assert r["result"] == "undeterminable"


def test_missing_oracle_refs_never_pass(verdict_ws):
    case = verdict_ws.load_case(NAV_CASE)
    for a in case["assertions"]:
        a["oracle_refs"] = []
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "pending"
    assert all(a["oracle"]["status"] == "missing" for a in run["assertions"] if not a["advisory"])


@pytest.mark.parametrize("world_name,holds", [("motor_hm_base.json", True), ("motor_hm_typo.json", False)])
@pytest.mark.parametrize("eligibility,oracle_status", [
    ("confirmed", "confirmed"), ("unconfirmed", "unconfirmed"), ("deprecated", "missing"),
    ("invalid", "invalid"), ("operation", "not_oracle"), ("out_of_scope", "out_of_scope"),
    ("unknown_scope", "applicability_unknown"), ("missing", "missing"),
])
def test_oracle_eligibility_controls_observed_result(verdict_ws, world_name, holds, eligibility, oracle_status):
    # 只改变合成判据，实际步骤/观测/断言/KnowledgeStore/最终判定均由真实链路执行。
    if eligibility != "unconfirmed":
        confirm(verdict_ws, COPY_RULE)
    path = verdict_ws.root / "knowledge" / "items.json"
    data = read_json(path)
    rule = next(item for item in data["items"] if item["id"] == COPY_RULE)
    if eligibility in ("deprecated", "invalid"):
        rule["status"] = eligibility
    elif eligibility == "operation":
        rule["kind"] = "operation"
    elif eligibility == "out_of_scope":
        rule["applies_to"]["app_version"] = "9.9.9"
    elif eligibility == "unknown_scope":
        rule["applies_to"]["pytest_missing_fact"] = "required"
    elif eligibility == "missing":
        data["items"].remove(rule)
    write_json(path, data)

    run = run_case(verdict_ws, COPY_CASE, device_spec=world(world_name))
    assertion = next(a for a in run["assertions"] if a["id"] == "A1")
    assert assertion["observed_holds"] is holds
    assert assertion["oracle"]["status"] == oracle_status
    expected = ("pass" if holds else "fail") if oracle_status == "confirmed" else "pending"
    assert assertion["result"] == expected
    assert run["verdict"]["verdict"] == {"pass": "passed", "fail": "failed", "pending": "pending"}[expected]
