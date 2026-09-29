"""RSI v0.1：候选可追溯来源；跨用例、跨世界与基线对比验证；采纳版本化；可回滚；回退则拒绝。"""
import pytest

from vpdtest.experience import ExperienceStore
from vpdtest.runner import run_case
from vpdtest.util import read_json

from conftest import confirm, widen_scope, world

BASE, DRIFT = world("motor_hm_base.json"), world("motor_hm_drift.json")


def _confirm(ws, *ids):
    # 漂移世界是 3.0.4.100：评审人确认这些判据对新版本仍适用后才能跨版本判定（applies_to 参与适用性）
    for k in ("K-MOT-001", "K-MOT-002"):
        if k in ids:
            widen_scope(ws, k, app_version=["3.0.3.200", "3.0.4.100"])
    confirm(ws, *ids)


def _runner(ws):
    return lambda cid, dev, overlay: run_case(ws, cid, device_spec=dev, overlay=overlay, propose=False)


def test_drift_adaptation_creates_traceable_candidate(ws):
    _confirm(ws, "K-MOT-001", "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=DRIFT)
    assert run["verdict"]["verdict"] == "passed" and run["verdict"]["adapted"]
    s2 = next(s for s in run["steps"] if s["step_id"] == "S2")
    assert s2["outcome"] == "passed_after_adaptation"
    loc = s2["attempts"][0]["locate"]
    assert [a["status"] for a in loc["attempts"]] == ["ambiguous", "found"]  # 首选策略多匹配，未盲点
    assert run["experience_candidates"]
    xs = ExperienceStore(ws.root, "motor_hm")
    item = xs.get(run["experience_candidates"][0])
    assert item["status"] == "candidate" and item["change"] == {"target": "tab_my", "prefer_strategy_index": 1, "layout_key": "sm/portrait",
                                                               "strategy_kind": "text"}
    ev = item["evidence"][0]
    assert ev["run_id"] == run["run_id"] and ev["step"] == "S2"
    assert (ws.runs_dir / ev["run_id"] / "obs" / ev["obs"] / "observation.json").exists()
    # 同样的经验再次出现只追加证据，不重复建候选
    run2 = run_case(ws, "TC-MOTOR-HM-001", device_spec=DRIFT)
    assert run2["experience_candidates"] == [] and len(xs.get(item["id"])["evidence"]) == 2


def test_validate_adopt_and_rollback(ws):
    _confirm(ws, "K-MOT-001", "K-MOT-002", "K-MOT-003")
    xid = run_case(ws, "TC-MOTOR-HM-001", device_spec=DRIFT)["experience_candidates"][0]
    xs = ExperienceStore(ws.root, "motor_hm")
    with pytest.raises(PermissionError):
        xs.adopt(xid)  # 未验证不能采纳
    res = xs.validate(xid, ["TC-MOTOR-HM-001", "TC-MOTOR-HM-002", "TC-MOTOR-HM-003"], [BASE, DRIFT], _runner(ws))
    assert res["status"] == "validated", res["reason"]
    assert not any(r.get("regression") for r in res["rows"])
    assert set(res["exercised_cases"]) - {"TC-MOTOR-HM-001"}  # 在来源用例之外得到验证
    drift_001 = next(r for r in res["rows"] if r["case"] == "TC-MOTOR-HM-001" and r["device"] == DRIFT)
    assert drift_001["trial_adaptations"] < drift_001["baseline_adaptations"]
    act = xs.adopt(xid)
    assert act["version"] == 1 and act["items"] == [xid]
    after = run_case(ws, "TC-MOTOR-HM-001", device_spec=DRIFT)
    assert after["overlay"]["version"] == 1 and not after["verdict"]["adapted"]
    back = xs.rollback(0, "pytest 回滚演练")
    assert back["items"] == [] and xs.get(xid)["status"] == "rolled_back"
    assert [e["action"] for e in xs.ledger()] == ["candidate_created", "validated", "adopted", "rolled_back"]
    assert read_json(xs.dir / "snapshots" / "active_v0.json")["items"] == []


def test_origin_case_only_is_not_enough(ws):
    _confirm(ws, "K-MOT-001", "K-MOT-002")
    xid = run_case(ws, "TC-MOTOR-HM-001", device_spec=DRIFT)["experience_candidates"][0]
    xs = ExperienceStore(ws.root, "motor_hm")
    res = xs.validate(xid, ["TC-MOTOR-HM-001"], [DRIFT], _runner(ws))
    assert res["status"] == "candidate" and "覆盖不足" in res["reason"]


def test_regression_rejects_candidate(ws):
    _confirm(ws, "K-MOT-001", "K-MOT-002")
    xid = run_case(ws, "TC-MOTOR-HM-001", device_spec=DRIFT)["experience_candidates"][0]
    xs = ExperienceStore(ws.root, "motor_hm")

    def fake(cid, dev, overlay):  # 试验组在某用例上变差 → 必须拒绝
        bad = overlay.trial and cid == "TC-MOTOR-HM-002"
        return {"run_id": f"{cid}-{overlay.trial}", "verdict": {"verdict": "failed" if bad else "passed"},
                "steps": [{"attempts": [{"locate": {"target": "tab_my", "adapted": not overlay.trial}}]}]}
    res = xs.validate(xid, ["TC-MOTOR-HM-001", "TC-MOTOR-HM-002"], [DRIFT], fake)
    assert res["status"] == "rejected"
    with pytest.raises(PermissionError):
        xs.adopt(xid)


def test_review_required_kinds_need_reviewer(ws):
    xs = ExperienceStore(ws.root, "motor_hm")
    item = {"id": "XP-manual", "kind": "interrupt_rule", "change": {"interrupt": "update_dialog", "review": "reviewed"},
            "status": "validated", "requires_review": True, "origin_case": "-", "evidence": [], "validations": [],
            "history": []}
    xs.save_item(item)
    with pytest.raises(PermissionError):
        xs.adopt("XP-manual")
    assert xs.adopt("XP-manual", reviewer="pytest-reviewer")["version"] == 1
