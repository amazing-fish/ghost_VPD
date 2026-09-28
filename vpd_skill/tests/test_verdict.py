"""判定：满足预期判通过、违反预期判失败、环境不足/判据缺失不误判通过（离线回放验证）。"""
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.verdict import evaluate_assertion

from conftest import confirm, world


def test_pass_when_expectation_met_with_confirmed_oracle(ws):
    confirm(ws, "K-MOT-001", "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_base.json"))
    v = run["verdict"]
    assert v["verdict"] == "passed", v["findings"]
    assert all(a["result"] == "pass" for a in run["assertions"] if not a["advisory"])
    assert v["suggestions"] and v["suggestions"][0]["id"] == "A4"  # 主观项只进入建议
    assert run["validation_level"] == "offline_replay"


def test_fail_when_confirmed_copy_rule_violated(ws):
    confirm(ws, "K-MOT-003")
    run = run_case(ws, "TC-MOTOR-HM-003", device_spec=world("motor_hm_typo.json"))
    v = run["verdict"]
    assert v["verdict"] == "failed" and v["attribution"] == "product"
    a1 = next(a for a in run["assertions"] if a["id"] == "A1")
    assert a1["result"] == "fail" and a1["evidence"]["obs"]


def test_unconfirmed_oracle_is_pending_not_pass(ws):
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "pending"
    assert all(a["result"] == "pending" for a in run["assertions"])


def test_unconfirmed_oracle_with_violation_is_pending_not_fail(ws):
    run = run_case(ws, "TC-MOTOR-HM-003", device_spec=world("motor_hm_typo.json"))
    a1 = next(a for a in run["assertions"] if a["id"] == "A1")
    assert a1["result"] == "pending" and a1["observed_holds"] is False
    assert run["verdict"]["verdict"] == "pending"


def test_locked_screen_blocks_as_environment(ws):
    confirm(ws, "K-MOT-001", "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_locked.json"))
    v = run["verdict"]
    assert v["verdict"] == "blocked" and v["attribution"] == "environment"
    assert {c["check"] for c in run["preflight"]["checks"] if not c["ok"]} >= {"screen_unlocked"}
    assert all(s["outcome"] == "not_run" for s in run["steps"])


def test_unmet_env_requirement_blocks(ws):
    confirm(ws, "K-MOT-003")
    run = run_case(ws, "TC-MOTOR-HM-003", device_spec=world("motor_hm_base.json"), env={"account_state": "logged_in"})
    assert run["verdict"]["verdict"] == "blocked" and run["verdict"]["attribution"] == "environment"


def test_crash_fails_via_generic_invariant_even_if_business_oracle_unconfirmed(ws):
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_crash.json"))
    v = run["verdict"]
    assert v["verdict"] == "failed" and v["attribution"] == "product"
    s3 = next(s for s in run["steps"] if s["step_id"] == "S3")
    assert s3["final_status"] == "anomaly" and len(s3["attempts"]) == 1  # 崩溃不重试、不恢复
    assert next(s for s in run["steps"] if s["step_id"] == "S4")["outcome"] == "not_run"


def test_absence_on_empty_tree_is_undeterminable(ws, tmp_path):
    obs = {"obs_id": "obs-x", "page": {"state": "undetermined"}, "tree": {"status": "empty"}, "screenshot": {}}
    from vpdtest.devices.replay import placeholder_tree
    from vpdtest.tree import UiTree
    a = {"id": "A", "at": "S1", "type": "text_absent", "text": "网络异常", "oracle_refs": ["K-GEN-001"]}
    r = evaluate_assertion(a, {"S1": {"outcome": "passed", "final_obs": "obs-x"}},
                           lambda _: (obs, UiTree(placeholder_tree())), KnowledgeStore(ws.root), ws.app("motor_hm"))
    assert r["result"] == "undeterminable"


def test_missing_oracle_refs_never_pass(ws):
    case = ws.load_case("TC-MOTOR-HM-001")
    for a in case["assertions"]:
        a["oracle_refs"] = []
    run = run_case(ws, case=case, device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "pending"
    assert all(a["oracle"]["status"] == "missing" for a in run["assertions"] if not a["advisory"])
