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


def test_trial_that_turns_product_failure_into_block_is_rejected(ws):
    _confirm(ws, "K-MOT-001", "K-MOT-002")
    xid = run_case(ws, "TC-MOTOR-HM-001", device_spec=DRIFT)["experience_candidates"][0]
    xs = ExperienceStore(ws.root, "motor_hm")

    def fake(cid, dev, overlay):  # 基线在 002 上判出产品失败，试验组改成了阻塞：缺陷被藏到工具问题后面
        v = ("blocked" if overlay.trial else "failed") if cid == "TC-MOTOR-HM-002" else "passed"
        return {"run_id": f"{cid}-{overlay.trial}", "verdict": {"verdict": v},
                "steps": [{"attempts": [{"locate": {"target": "tab_my", "layout_key": "sm/portrait",
                                                    "adapted": not overlay.trial}}]}]}
    res = xs.validate(xid, ["TC-MOTOR-HM-001", "TC-MOTOR-HM-002"], [DRIFT], fake)
    row = next(r for r in res["rows"] if r["case"] == "TC-MOTOR-HM-002")
    assert res["status"] == "rejected" and row["regression"]
    with pytest.raises(PermissionError):
        xs.adopt(xid)


def _wait_item(xs):
    xs.save_item({"id": "XP-wait", "kind": "wait_timeout", "change": {"key": "TC-MOTOR-HM-001:S3", "timeout_s": 9.0},
                  "status": "candidate", "requires_review": False, "origin_case": "TC-MOTOR-HM-001",
                  "evidence": [{"run_id": "r0", "case": "TC-MOTOR-HM-001", "step": "S3", "device": DRIFT}],
                  "validations": [], "history": []})


def _wait_runner(cid, dev, overlay):  # 试验组在该步骤上不再超时
    ok = bool(overlay.trial)
    return {"run_id": f"{cid}-{dev}-{overlay.trial}", "verdict": {"verdict": "passed" if ok else "failed"},
            "steps": [{"step_id": "S3", "attempts": [{"post": {"met": ok}}]}]}


def test_wait_timeout_needs_a_device_other_than_its_origin(ws):
    xs = ExperienceStore(ws.root, "motor_hm")
    _wait_item(xs)
    res = xs.validate("XP-wait", ["TC-MOTOR-HM-001"], [DRIFT], _wait_runner)
    assert res["status"] == "candidate" and "覆盖不足" in res["reason"]
    with pytest.raises(PermissionError):
        xs.adopt("XP-wait")
    # 其他用例不会走到这个等待键，不能充当独立覆盖
    res = xs.validate("XP-wait", ["TC-MOTOR-HM-001", "TC-MOTOR-HM-002"], [DRIFT], _wait_runner)
    assert res["status"] == "candidate" and res["exercised"] == [f"TC-MOTOR-HM-001@{DRIFT}"]
    res = xs.validate("XP-wait", ["TC-MOTOR-HM-001"], [DRIFT, BASE], _wait_runner)
    assert res["status"] == "validated" and "来源设备之外" in res["reason"]


def test_wait_timeout_improvement_must_appear_outside_its_origin_device(ws):
    xs = ExperienceStore(ws.root, "motor_hm")
    _wait_item(xs)

    def only_origin_improves(cid, dev, overlay):  # 来源设备：基线超时、试验通过；另一台设备走到该步骤但两边结果一样
        ok = bool(overlay.trial) or dev != DRIFT
        return {"run_id": f"{cid}-{dev}-{overlay.trial}", "verdict": {"verdict": "passed" if ok else "failed"},
                "steps": [{"step_id": "S3", "attempts": [{"post": {"met": ok}}]}]}
    res = xs.validate("XP-wait", ["TC-MOTOR-HM-001"], [DRIFT, BASE], only_origin_improves)
    assert res["status"] == "candidate" and "改进" in res["reason"] and "来源设备" in res["reason"]
    assert len(res["exercised"]) == 2
    with pytest.raises(PermissionError):
        xs.adopt("XP-wait")
    # 来源设备之外的设备上同样出现改进（基线超时、试验通过）才能晋级
    res = xs.validate("XP-wait", ["TC-MOTOR-HM-001"], [DRIFT, BASE], _wait_runner)
    assert res["status"] == "validated"


def test_review_required_kinds_need_reviewer(ws):
    xs = ExperienceStore(ws.root, "motor_hm")
    item = {"id": "XP-manual", "kind": "interrupt_rule", "change": {"interrupt": "update_dialog", "review": "reviewed"},
            "status": "validated", "requires_review": True, "origin_case": "-", "evidence": [], "validations": [],
            "history": []}
    xs.save_item(item)
    with pytest.raises(PermissionError):
        xs.adopt("XP-manual")
    assert xs.adopt("XP-manual", reviewer="pytest-reviewer")["version"] == 1
