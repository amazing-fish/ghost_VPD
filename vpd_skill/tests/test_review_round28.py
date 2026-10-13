"""第 28 轮 review 的三条修复：输入框过授权门槛、safe_stop 无树不盲点、重放依赖指纹。"""
from types import SimpleNamespace

import pytest

from vpdtest import cases as C
from vpdtest import safety as safety_mod
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

from conftest import confirm, world

BASE = world("motor_hm_base.json")


# ---- 1. 输入与点击共用副作用门槛 ----

def test_check_effect_gates_by_authorization(ws):
    pol = ws.policy()
    assert pol.check_effect({"side_effect": "navigation"}) is None
    assert pol.check_effect(None) is None  # 未声明按 navigation
    for effect in ("user_data", "account_state"):
        denied = pol.check_effect({"side_effect": effect}, set())
        assert denied and not denied["allowed"] and denied["deny"]["gated"], effect
        assert pol.check_effect({"side_effect": effect}, None)["allowed"] is False
    assert pol.check_effect({"side_effect": "user_data"}, {"user_data"}) is None
    # 授权别的类别不顶用
    assert pol.check_effect({"side_effect": "user_data"}, {"account_state"})["allowed"] is False
    # 监护类授权后放行，但拒绝信息要求监护人
    assert pol.check_effect({"side_effect": "vehicle_control"}, set())["deny"]["supervised"] is True
    assert pol.check_effect({"side_effect": "vehicle_control"}, {"vehicle_control"}) is None
    # 硬禁止类任何授权下都拒绝
    assert pol.check_effect({"side_effect": "payment"}, {"payment"})["allowed"] is False


def test_check_tap_still_uses_the_same_gate(ws):
    pol = ws.policy()
    t = {"side_effect": "user_data"}
    assert pol.check_tap(None, (0, 0), "template", t, authorized=set())["allowed"] is False
    assert pol.check_tap(None, (0, 0), "template", t, authorized={"user_data"})["allowed"] is True


# ---- 2. safe_stop 无 UI 树时不盲点 ----

@pytest.mark.parametrize("basis", ["template", "point"])
def test_safe_stop_not_clicked_without_tree(ws, basis):
    pol = ws.policy()
    r = pol.check_tap(None, (10, 10), basis, {"side_effect": "safe_stop"})
    assert r["allowed"] is False and r["deny"]["class"] == "safe_stop_unconfirmed"
    # 普通导航目标的模板匹配不受影响
    assert pol.check_tap(None, (10, 10), "template", {"side_effect": "navigation"})["allowed"] is True


def test_safe_stop_target_with_template_strategy_is_a_lint_problem(ws):
    ok = {"side_effect": "safe_stop", "strategies": [{"kind": "text", "value": "暂停"}, {"kind": "xpath", "value": "//x"}]}
    bad = {"side_effect": "safe_stop", "strategies": [{"kind": "text", "value": "暂停"}, {"kind": "template", "value": "p.png"}]}
    assert safety_mod.untreed_strategies(ok) == []
    assert safety_mod.untreed_strategies(bad) == ["template"]
    app = SimpleNamespace(targets={"stop_ok": ok, "stop_bad": bad})
    pol = ws.policy()
    msgs = lambda tid: [p["problem"] for p in safety_mod.problems(
        {"safe_stop": [{"action": "tap", "target": tid}], "max_motion_s": 60}, app, pol) if "非 UI 树策略" in p["problem"]]
    assert msgs("stop_ok") == []
    assert len(msgs("stop_bad")) == 1


# ---- 3. 重放绑定依赖指纹 ----

def _ready_case(ws):
    """复用探索晋级测试的用例：两步导航 + 一条已确认判据的断言，状态为 candidate。"""
    from test_explore_promote import _explored_case
    _, case = _explored_case(ws)
    confirm(ws, "K-MOT-002")
    case.update(purpose="p", test_data_none=True, cleanup=[{"action": "stop_app"}], status="candidate",
                assertions=[{"id": "A1", "at": "S3", "type": "target_present", "target": "logout_entry",
                             "desc": "d", "oracle_refs": ["K-MOT-002"]}])
    case["steps"][1]["target"], case["steps"][2]["target"] = "tab_my", "settings_entry"
    write_json(ws.case_path("TC-EXP-001"), case)
    kn = KnowledgeStore(ws.root)
    C.approve(ws, "TC-EXP-001", kn, "pytest-human-reviewer", "isolated dependency-replay fixture")
    return kn


def _replay_twice(ws):
    for _ in range(2):
        C.record_replay(ws, "TC-EXP-001", run_case(ws, "TC-EXP-001", device_spec=BASE))


def test_replays_carry_dependency_hash_and_promote_when_unchanged(ws):
    kn = _ready_case(ws)
    _replay_twice(ws)
    reps = ws.load_case("TC-EXP-001")["replays"]
    assert all(r.get("deps") for r in reps) and len({r["deps"] for r in reps}) == 1
    assert C.promote(ws, "TC-EXP-001", "replay_verified", kn)["status"] == "replay_verified"


def test_changing_a_referenced_target_invalidates_replays(ws):
    kn = _ready_case(ws)
    _replay_twice(ws)
    p = ws.root / "apps" / "motor_hm" / "targets.json"
    data = read_json(p)
    data["targets"]["settings_entry"]["strategies"][0]["value"] += "[1]"  # 用例文件没变，目标选择器变了
    write_json(p, data)
    C.approve(ws, "TC-EXP-001", kn, "pytest-human-reviewer", "isolated changed selector fixture")
    with pytest.raises(PermissionError, match="重放不足"):
        C.promote(ws, "TC-EXP-001", "replay_verified", kn)


def test_changing_oracle_statement_invalidates_but_confirming_does_not(ws):
    kn = _ready_case(ws)
    app = ws.app("motor_hm")
    case = ws.load_case("TC-EXP-001")
    before = C.dependency_hash(case, app, kn)
    ks = ws.root / "knowledge" / "items.json"
    data = read_json(ks)
    item = next(i for i in data["items"] if i["id"] == "K-MOT-002")
    item["status"] = "unconfirmed"  # 确认状态不进指纹
    write_json(ks, data)
    assert C.dependency_hash(case, app, KnowledgeStore(ws.root)) == before
    item["statement"] += "（改）"
    write_json(ks, data)
    assert C.dependency_hash(case, app, KnowledgeStore(ws.root)) != before


def test_replays_without_dependency_hash_do_not_count(ws):
    kn = _ready_case(ws)
    _replay_twice(ws)
    case = ws.load_case("TC-EXP-001")
    for r in case["replays"]:
        r.pop("deps")  # 旧记录没有依赖指纹，无法证明测的是同一份定义
    write_json(ws.case_path("TC-EXP-001"), case)
    with pytest.raises(PermissionError, match="依赖指纹|定义已变"):
        C.promote(ws, "TC-EXP-001", "replay_verified", kn)
