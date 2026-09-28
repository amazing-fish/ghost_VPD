"""恢复：不擦除首次异常；未评审的中断不自动关闭；绝不点击「更新」。"""
from vpdtest.util import read_json, read_jsonl
from vpdtest.runner import run_case

from conftest import confirm, review_interrupt, world


def _taps(run):
    out = []
    for s in run["steps"]:
        for sub in s.get("sub_steps", [s]):
            for a in sub.get("attempts", []):
                if a.get("locate"):
                    out.append(a["locate"]["target"])
            for r in sub.get("recoveries", []):
                if r.get("attempt", {}).get("locate"):
                    out.append(r["attempt"]["locate"]["target"])
    return out


def test_unreviewed_interrupt_blocks_instead_of_being_dismissed(ws):
    confirm(ws, "K-MOT-001", "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_popup.json"))
    v = run["verdict"]
    assert v["verdict"] == "blocked" and v["attribution"] == "requirement_unclear"
    assert "update_later" not in _taps(run) and "update_now" not in _taps(run)
    assert v["first_anomaly"]["status"] == "interrupted"


def test_recovery_keeps_first_failure_and_original_evidence(ws):
    confirm(ws, "K-MOT-001", "K-MOT-002")
    review_interrupt(ws, "update_dialog")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_popup.json"))
    v = run["verdict"]
    assert v["verdict"] == "passed" and v["recovered"]
    fa = v["first_anomaly"]
    assert fa["status"] == "interrupted" and fa["detail"]["post"]["interrupts"][0]["id"] == "update_dialog"
    # 首次失败时的观测仍在，内容就是弹窗画面
    obs = read_json(f"{run['dir']}/obs/{fa['obs_after']}/observation.json")
    assert "下次再说" in obs["texts"] and obs["page"]["interrupts"]
    events = read_jsonl(f"{run['dir']}/events.jsonl")
    kinds = [(e["event"], e.get("status") or e.get("action")) for e in events]
    assert ("attempt", "interrupted") in kinds and ("recovery", "dismiss_interrupt") in kinds
    assert kinds.index(("attempt", "interrupted")) < kinds.index(("recovery", "dismiss_interrupt"))
    assert "update_later" in _taps(run) and "update_now" not in _taps(run)
    assert "经恢复" in open(f"{run['dir']}/report.md", encoding="utf-8").read()
