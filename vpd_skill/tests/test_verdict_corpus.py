"""生产资产集成检查：有意读取当前用例/判据，数据治理变化时应重新评估这些期望。"""
from conftest import unconfirm, world

from vpdtest.runner import run_case


def test_production_navigation_case_has_ready_oracles(ws):
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "passed", run["verdict"]["findings"]
    assertions = [a for a in run["assertions"] if not a["advisory"]]
    assert assertions and all(a["oracle"]["status"] == "confirmed" for a in assertions)
    assert {ref for a in assertions for ref in a["oracle_refs"]} == {"K-MOT-001", "K-MOT-002"}


def test_production_navigation_case_without_ready_oracles_is_pending(ws):
    unconfirm(ws, "K-MOT-001", "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "pending"
    assertions = [a for a in run["assertions"] if not a["advisory"]]
    assert assertions and all(a["oracle"]["status"] == "unconfirmed" for a in assertions)
    assert all(a["result"] == "pending" and a["observed_holds"] is True for a in assertions)
