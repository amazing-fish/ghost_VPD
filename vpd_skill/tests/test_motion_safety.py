"""控车/运动：有人监护时允许，但必须能观察过程（monitor 时间线、TOR 提示、后台/返回），
并且一旦运动中出现首次失败、超时或用例结束仍未停，立刻安全停止且不掩盖首次失败。

使用合成世界 motor_hm_motion.json 与临时注入的 lab_* 状态/目标：文案是占位，只验证执行器与判定逻辑。
"""
import json

import pytest

from conftest import WORLDS, world
from vpdtest.devices.replay import load_world
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

LAB_STATES = {
    "lab_vpd_home": {"desc": "占位：泊车首页", "require_text": ["代客泊车（占位）"]},
    "lab_parking": {"desc": "占位：泊入中", "require_text": ["正在泊入（占位）"]},
    "lab_tor": {"desc": "占位：接管提示", "require_text": ["前方有障碍，请立即接管车辆（占位）"]},
    "lab_paused": {"desc": "占位：已暂停", "require_text": ["泊车已暂停（占位）"]},
    "lab_done": {"desc": "占位：泊车完成", "require_text": ["泊车完成（占位）"]},
}
LAB_TARGETS = {
    "lab_vpd_entry": {"side_effect": "vpd_entry", "strategies": [{"kind": "text", "value": "泊车代驾"}]},
    "lab_start": {"side_effect": "vehicle_motion", "strategies": [{"kind": "text", "value": "开始泊车"}]},
    "lab_continue": {"side_effect": "vehicle_motion", "strategies": [{"kind": "text", "value": "继续"}]},
    "lab_pause": {"side_effect": "safe_stop", "strategies": [{"kind": "text", "value": "暂停"}]},
    "lab_exit": {"side_effect": "safe_stop", "strategies": [{"kind": "text", "value": "退出泊车"}]},
}
SAFETY = {"safe_stop": [{"action": "tap", "target": "lab_pause", "expect_state": "lab_paused"},
                        {"action": "tap", "target": "lab_exit", "expect_state": "lab_vpd_home"}],
          "max_motion_s": 60, "stopped_states": ["lab_paused", "lab_done", "lab_vpd_home"]}
WATCH = {"tor": "接管", "moving": "车速", "done": "泊车完成"}


@pytest.fixture
def lab(ws):
    app_dir = ws.root / "apps" / "motor_hm"
    st = read_json(app_dir / "states.json")
    st["states"].update(LAB_STATES)
    st["recipes"]["lab_vpd_home"] = st["recipes"]["car_tab"] + [
        {"action": "tap", "target": "lab_vpd_entry", "expect_state": "lab_vpd_home", "timeout_s": 8}]
    write_json(app_dir / "states.json", st)
    tg = read_json(app_dir / "targets.json")
    tg["targets"].update(LAB_TARGETS)
    write_json(app_dir / "targets.json", tg)
    return ws


def _case(steps, **kw):
    base = {"id": "TC-LAB", "version": 1, "status": "draft", "app": "motor_hm", "title": "lab",
            "purpose": "验证运动安全机制", "preconditions": ["占位"], "test_data_none": True, "test_data": {},
            "authorizations": ["vpd_entry", "vehicle_motion"], "safety": SAFETY,
            "steps": [{"id": "S1", "action": "ensure_state", "state": "lab_vpd_home"}] + steps,
            "assertions": [], "cleanup": [], "sources": [{"type": "test"}]}
    base.update(kw)
    return base


def _run(ws, case, dev="motor_hm_motion.json", supervisor="现场监护人A"):
    return run_case(ws, case=case, device_spec=world(dev) if not dev.startswith("replay:") else dev, propose=False,
                    authorize=["vpd_entry", "vehicle_motion"], supervisor=supervisor)


def test_monitor_captures_tor_prompt_and_motion_ends_normally(lab):
    case = _case([
        {"id": "S2", "action": "tap", "target": "lab_start", "expect_state": "lab_parking"},
        {"id": "S3", "action": "monitor", "duration_s": 40, "interval_s": 2, "watch": WATCH, "until_state": "lab_done"},
    ], assertions=[
        {"id": "A1", "type": "text_seen", "at": "S3", "pattern": "接管", "oracle_refs": ["K-LAB-TOR"], "desc": "出现接管提示"},
        {"id": "A2", "type": "state_seen", "at": "S3", "state": "lab_tor", "oracle_refs": ["K-LAB-TOR"], "desc": "经过接管态"},
        {"id": "A3", "type": "text_seen", "at": "S3", "pattern": "闪退", "expected": False, "oracle_refs": ["K-LAB-TOR"],
         "desc": "全程不应出现闪退提示"}])
    run = _run(lab, case)
    s3 = next(s for s in run["steps"] if s["step_id"] == "S3")
    assert s3["outcome"] == "passed" and s3["monitor"]["stopped_by"] == "until_state:lab_done"
    assert s3["monitor"]["states"] == ["lab_parking", "lab_tor", "lab_done"]
    assert s3["monitor"]["hits"]["tor"]["count"] == 1 and s3["monitor"]["hits"]["moving"]["count"] == 1
    # 判据未确认 → pending，但观测结论是成立的
    assert all(a["result"] == "pending" and a["observed_holds"] for a in run["assertions"])
    assert [h["event"] for h in run["motion"]] == ["armed", "stopped_observed"]
    assert run["safety_stops"] == []  # 正常结束，不需要安全停止
    assert run["supervision"]["supervisor"] == "现场监护人A"
    report = (lab.runs_dir / run["run_id"] / "report.md").read_text("utf-8")
    assert "监视时间线：S3" in report and "请立即接管车辆" in report


def test_background_then_resume_then_continue_and_exit(lab):
    case = _case([
        {"id": "S2", "action": "tap", "target": "lab_start", "expect_state": "lab_parking"},
        {"id": "S3", "action": "home", "expect_state": "out_of_app"},
        {"id": "S4", "action": "wait", "duration_s": 10, "expect_state": "out_of_app"},
        {"id": "S5", "action": "resume", "expect_state": "lab_paused"},
        {"id": "S6", "action": "tap", "target": "lab_continue", "expect_state": "lab_parking"},
        {"id": "S7", "action": "tap", "target": "lab_pause", "expect_state": "lab_paused"},
        {"id": "S8", "action": "tap", "target": "lab_exit", "expect_state": "lab_vpd_home"},
    ])
    run = _run(lab, case)
    by = {s["step_id"]: s for s in run["steps"]}
    assert all(s["outcome"] == "passed" for s in run["steps"]), [(s["step_id"], s["outcome"], s.get("reason")) for s in run["steps"]]
    assert by["S5"]["resume"] == {"process_alive_before": True, "cold_start": False}
    assert [h["event"] for h in run["motion"]] == ["armed", "stopped_observed", "armed", "stopped_observed"]
    events = [json.loads(x)["event"] for x in (lab.runs_dir / run["run_id"] / "events.jsonl").read_text("utf-8").splitlines()]
    assert "motion_app_left" in events and run["safety_stops"] == []


def _variant(tmp_path, name, **screen_patch):
    w = load_world(WORLDS / "motor_hm_motion.json")
    for sid, patch in screen_patch.items():
        w["screens"][sid].update(patch)
        for k in [k for k, v in patch.items() if v is None]:
            w["screens"][sid].pop(k)
    w.pop("_path", None)
    p = tmp_path / f"{name}.json"
    write_json(p, w)
    return f"replay:{p}"


def test_motion_timeout_triggers_safe_stop_without_masking_first_failure(lab, tmp_path):
    # 卡住：一直"正在泊入"不结束（终点不对/被挡路）→ 超过 max_motion_s 后安全停止
    stuck = _variant(tmp_path, "motion_stuck", parking_in={"after_obs": None})
    safety = {**SAFETY, "max_motion_s": 5}
    case = _case([
        {"id": "S2", "action": "tap", "target": "lab_start", "expect_state": "lab_parking"},
        {"id": "S3", "action": "monitor", "duration_s": 60, "interval_s": 2, "watch": WATCH, "until_state": "lab_done"},
        {"id": "S4", "action": "wait", "duration_s": 1},
    ], safety=safety)
    run = _run(lab, case, dev=stuck)
    by = {s["step_id"]: s for s in run["steps"]}
    assert by["S3"]["outcome"] == "fail_candidate" and by["S3"]["final_status"] == "motion_timeout"
    assert by["S3"]["first_failure"]["status"] == "motion_timeout"  # 首次失败原样保留
    assert by["S4"]["outcome"] == "not_run"
    stop = run["safety_stops"][0]
    assert stop["trigger"] == "critical_step_failed" and stop["confirmed_stopped"]
    assert stop["steps"][0]["outcome"] == "passed"  # 点了暂停
    assert stop["steps"][1].get("skipped")          # 已观测到暂停，不再继续点退出
    assert run["verdict"]["first_anomaly"]["step"] == "S3"


def test_unconfirmed_safe_stop_asks_for_manual_takeover(lab, tmp_path):
    # 退后台时进程被回收：拉起是冷启动，回不到泊车页；安全停止找不到按钮，必须报告"需人工接管"
    killed = _variant(tmp_path, "motion_killed", parking_in={"kill_on_background": True})
    case = _case([
        {"id": "S2", "action": "tap", "target": "lab_start", "expect_state": "lab_parking"},
        {"id": "S3", "action": "home", "expect_state": "out_of_app"},
        {"id": "S4", "action": "resume", "expect_state": "lab_paused"},
    ])
    run = _run(lab, case, dev=killed)
    by = {s["step_id"]: s for s in run["steps"]}
    assert by["S4"]["outcome"] == "fail_candidate" and by["S4"]["resume"]["cold_start"] is True
    stop = run["safety_stops"][0]
    assert stop["confirmed_stopped"] is False and "人工接管" in stop["action_required"]
    assert any(f["kind"] == "safety_stop" for f in run["verdict"]["findings"])
    assert "未能确认已停止" in (lab.runs_dir / run["run_id"] / "report.md").read_text("utf-8")


def test_motion_needs_supervisor_and_safety_declaration(lab):
    case = _case([{"id": "S2", "action": "tap", "target": "lab_start", "expect_state": "lab_parking"}])
    run = _run(lab, case, supervisor=None)
    assert run["verdict"]["verdict"] == "blocked" and {u["key"] for u in run["env"]["unmet"]} == {"supervisor"}
    assert all(s["outcome"] == "not_run" for s in run["steps"])
    no_safety = _case([{"id": "S2", "action": "tap", "target": "lab_start"}], safety=None)
    keys = {u["key"] for u in _run(lab, no_safety)["env"]["unmet"]}
    assert "safety" in keys
    # 授权了运动但没带监护人参数的 act 单步：直接拒绝
    from vpdtest.cli import build_parser
    from vpdtest.commands import cmd_act
    a = build_parser().parse_args(["act", "tap", "--target", "lab_start", "--device", world("motor_hm_motion.json"),
                                   "--authorize", "vehicle_motion"])
    with pytest.raises(ValueError, match="监护人"):
        cmd_act(lab, a)


def test_explore_records_control_entries_without_tapping(lab):
    from vpdtest.explore import explore
    exp = explore(lab, "motor_hm", "car_tab", world("motor_hm_motion.json"), budget=10)
    sup = {s["label"]: s for s in exp["skipped"] if s.get("supervised")}
    assert {"解锁", "空调", "寻车"} <= set(sup) and sup["解锁"]["deny_class"] == "vehicle_control"
    assert set(exp["coverage"]["supervised_entries"]) >= {"解锁", "空调", "寻车"}
    assert all(t["label"] not in sup for t in exp["transitions"])


def test_lint_requires_authorization_safety_and_cleanup(lab):
    from vpdtest.cases import lint
    from vpdtest.knowledge import KnowledgeStore
    app, kn, pol = lab.app("motor_hm"), KnowledgeStore(lab.root), lab.policy()
    case = _case([{"id": "S2", "action": "tap", "target": "lab_start"},
                  {"id": "S3", "action": "monitor", "watch": {"tor": "("}, "stop_on": ["nope"]}],
                 authorizations=["vpd_entry", "user_data"], safety=None)
    probs = [i["problem"] for i in lint(case, app, kn, pol)]
    assert any("未申请 vehicle_motion" in p for p in probs)
    assert any("不是合法正则" in p for p in probs) and any("未定义的 watch 规则" in p for p in probs)
    assert any("清理步骤" in p for p in probs)
    ok = _case([{"id": "S2", "action": "tap", "target": "lab_start"}])
    assert not [i for i in lint(ok, app, kn, pol) if "safety" in i["field"] or "未申请" in i["problem"]]
