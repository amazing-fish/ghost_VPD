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


def test_reviewed_benign_popup_is_dismissed_inline_with_evidence(ws):
    """已评审的良性弹窗在动作前/后置轮询里直接关闭：不计为异常，但关闭记录和弹窗画面的观测都保留。"""
    confirm(ws, "K-MOT-001", "K-MOT-002")
    review_interrupt(ws, "update_dialog")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_popup.json"))
    v = run["verdict"]
    assert v["verdict"] == "passed"
    assert "update_now" not in _taps(run)
    dis = [d for s in run["steps"] for x in s.get("sub_steps", [s]) for d in x.get("dismissed", [])]
    assert dis and dis[0]["interrupt"] == "update_dialog" and dis[0]["ok"]
    obs = read_json(f"{run['dir']}/obs/{dis[0]['obs']}/observation.json")
    assert "下次再说" in obs["texts"] and obs["page"]["interrupts"]
    ev = [e for e in read_jsonl(f"{run['dir']}/events.jsonl") if e["event"] == "interrupt_dismissed"]
    assert ev and ev[0]["interrupt"] == "update_dialog"