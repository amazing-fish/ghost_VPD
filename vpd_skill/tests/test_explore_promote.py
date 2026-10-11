"""探索 → 探索记录 → 候选用例 → 重放验证 → 正式回归（各级门禁）。"""
import pytest

from vpdtest import cases as C
from vpdtest.explore import explore
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import write_json

from conftest import confirm, unconfirm, world

BASE = world("motor_hm_base.json")


def test_explore_respects_policy_and_reports_honest_coverage(ws):
    exp = explore(ws, "motor_hm", "car_tab", BASE, budget=60, goal="账号与导航")
    assert exp["exit"]["reason"] == "frontier_exhausted"
    cov = exp["coverage"]
    assert set(cov["visited_declared"]) == {"car_tab", "my_tab", "settings"}
    assert cov["unknown_states"] and "不代表 APP 的完整覆盖" in cov["disclaimer"]
    skipped = {s["label"] for s in exp["skipped"]}
    assert {"解锁", "空调", "泊车代驾", "退出登录", "注册/登录"} <= skipped
    assert all(t["to"] != "vpd_home" for t in exp["transitions"])
    labels = {(t["from"], t["label"], t["to"]) for t in exp["transitions"]}
    assert ("car_tab", "我的", "my_tab") in labels and ("my_tab", "设置", "settings") in labels


def _explored_case(ws):
    exp = explore(ws, "motor_hm", "car_tab", BASE, budget=60)
    t1 = next(t for t in exp["transitions"] if (t["from"], t["to"]) == ("car_tab", "my_tab"))
    t2 = next(t for t in exp["transitions"] if (t["from"], t["to"]) == ("my_tab", "settings"))
    case = C.from_exploration(exp, [t1["id"], t2["id"]], "TC-EXP-001", "motor_hm")
    write_json(ws.case_path("TC-EXP-001"), case)
    return exp, case


def test_exploration_record_is_traceable_and_not_promotable_until_complete(ws):
    exp, case = _explored_case(ws)
    kn = KnowledgeStore(ws.root)
    assert case["status"] == "exploration_record"
    src = case["sources"][0]
    assert src["run"] == exp["run_id"]
    for o in src["obs"]:
        assert (ws.runs_dir / exp["run_id"] / "obs" / o / "observation.json").exists()
    problems = {i["field"] for i in C.lint(case, ws.app("motor_hm"), kn)}
    assert {"purpose", "assertions", "cleanup"} <= problems
    with pytest.raises(PermissionError):
        C.promote(ws, "TC-EXP-001", "candidate", kn)


def test_candidate_replay_and_regression_gates(ws):
    unconfirm(ws, "K-MOT-002")
    _, case = _explored_case(ws)
    kn = KnowledgeStore(ws.root)
    case.update(purpose="从爱车页经我的页进入设置", test_data_none=True, cleanup=[{"action": "stop_app"}],
                assertions=[{"id": "A1", "at": "S3", "type": "target_present", "target": "logout_entry",
                             "desc": "设置页可见退出登录", "oracle_refs": ["K-MOT-002"]}])
    case["steps"][1]["target"], case["steps"][2]["target"] = "tab_my", "settings_entry"
    write_json(ws.case_path("TC-EXP-001"), case)
    assert C.promote(ws, "TC-EXP-001", "candidate", kn)["status"] == "candidate"
    with pytest.raises(PermissionError):
        C.promote(ws, "TC-EXP-001", "replay_verified", kn)  # 没有重放
    run = run_case(ws, "TC-EXP-001", device_spec=BASE)
    C.record_replay(ws, "TC-EXP-001", run)
    assert run["verdict"]["verdict"] == "pending"  # 判据未确认，重放不算通过
    with pytest.raises(PermissionError):
        C.promote(ws, "TC-EXP-001", "replay_verified", kn)
    confirm(ws, "K-MOT-002")
    for _ in range(2):
        C.record_replay(ws, "TC-EXP-001", run_case(ws, "TC-EXP-001", device_spec=BASE))
    assert C.promote(ws, "TC-EXP-001", "replay_verified", kn)["status"] == "replay_verified"
    with pytest.raises(PermissionError):
        C.promote(ws, "TC-EXP-001", "regression", kn)  # 无评审人
    with pytest.raises(PermissionError, match="真机"):
        C.promote(ws, "TC-EXP-001", "regression", kn, reviewer="pytest-reviewer")
    done = C.promote(ws, "TC-EXP-001", "regression", kn, reviewer="pytest-reviewer", allow_offline=True)
    assert done["status"] == "regression" and len(done["history"][-1]["replays"]) == 2


def test_editing_case_invalidates_old_replays(ws):
    _, case = _explored_case(ws)
    kn = KnowledgeStore(ws.root)
    confirm(ws, "K-MOT-002")
    case.update(purpose="p", test_data_none=True, cleanup=[{"action": "stop_app"}], status="candidate",
                assertions=[{"id": "A1", "at": "S3", "type": "target_present", "target": "logout_entry",
                             "desc": "d", "oracle_refs": ["K-MOT-002"]}])
    case["steps"][1]["target"], case["steps"][2]["target"] = "tab_my", "settings_entry"
    write_json(ws.case_path("TC-EXP-001"), case)
    for _ in range(2):
        C.record_replay(ws, "TC-EXP-001", run_case(ws, "TC-EXP-001", device_spec=BASE))
    edited = ws.load_case("TC-EXP-001")
    edited["assertions"][0]["type"] = "target_absent"  # 修改断言 → 旧重放不再作数
    write_json(ws.case_path("TC-EXP-001"), edited)
    with pytest.raises(PermissionError, match="重放不足"):
        C.promote(ws, "TC-EXP-001", "replay_verified", kn)
