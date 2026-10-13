"""Issue #35：按可点击容器复现误报；合成层级不代表已取得真机原树。"""
import pytest
from conftest import confirm
from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world

from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.session import parse_select
from vpdtest.tree import UiTree
from vpdtest.util import read_jsonl
from vpdtest.verdict import evaluate_assertion

LABELS = ["被授权的车辆", "附近充电站"]


def _node(kind, bounds, text="", description="", clickable=False, children=()):
    return {"attributes": {"type": kind, "bounds": bounds, "text": text,
                            "description": description, "clickable": str(clickable).lower()},
            "children": list(children)}


def _holder(label, extra=(), field="text", outside=None):
    leaf = _node("Text", "[20,20][400,60]", **{field: label})
    children = [leaf] + [_node("Text", f"[20,{80+i*30}][450,{105+i*30}]", text=t) for i, t in enumerate(extra)]
    root = [_node("Row", "[0,0][500,200]", clickable=True, children=children)]
    if outside:
        root.append(_node("Button", "[0,220][500,300]", text=outside, clickable=True))
    tree = UiTree(_node("orgRoot", "[0,0][500,400]", children=root))
    return tree, tree.nodes[2]


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("field", ["text", "description"])
@pytest.mark.parametrize("basis", ["tree", "point"])
@pytest.mark.parametrize("spacing", ["{}", " {} ", "\n{}\n"])
def test_known_navigation_label_is_allowed(ws, label, field, basis, spacing):
    tree, node = _holder(spacing.format(label), field=field)
    r = ws.policy().check_tap(tree, node.center, basis, {"side_effect": "navigation"})
    assert r["allowed"] and r["deny"] is None


@pytest.mark.parametrize("label,cls", [
    ("授权", "account_state"), ("授权的车辆", "account_state"),
    ("取消授权", "account_state"), ("撤销被授权的车辆", "account_state"),
    ("被授权的车辆授权管理", "account_state"), ("被授权的车辆\n授权", "account_state"),
    ("充电", "vehicle_control"), ("启动充电桩", "vehicle_control"),
    ("充电站启动", "vehicle_control"), ("附近充电站启动充电", "vehicle_control"),
    ("附近充电站\n启动充电", "vehicle_control"), ("充电费", "vehicle_control"),
    ("电源管理", "vehicle_control"),
])
def test_unreviewed_labels_and_combined_directives_still_require_authorization(ws, label, cls):
    tree, node = _holder(label)
    r = ws.policy().check_tap(tree, node.center, "tree", {"side_effect": "navigation"})
    assert not r["allowed"] and r["deny"]["class"] == cls


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("danger,cls", [("授权", "account_state"), ("解锁", "vehicle_control"),
                                        ("启动充电", "vehicle_control"), ("支付充电费", "payment")])
def test_other_text_in_same_clickable_container_still_blocks(ws, label, danger, cls):
    tree, node = _holder(label, extra=[danger])
    r = ws.policy().check_tap(tree, node.center, "tree", {"side_effect": "navigation"})
    assert not r["allowed"] and r["deny"]["class"] == cls


@pytest.mark.parametrize("label", LABELS)
def test_separate_dangerous_sibling_is_not_part_of_navigation(ws, label):
    tree, node = _holder(label, outside="支付充电费")
    pol = ws.policy()
    assert pol.check_tap(tree, node.center, "tree", {"side_effect": "navigation"})["allowed"]
    dangerous = tree.find_text("支付充电费")[0]
    r = pol.check_tap(tree, dangerous.center, "tree", {"side_effect": "navigation"})
    assert not r["allowed"] and r["deny"]["class"] == "payment"


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("effect", ["account_state", "vehicle_selection", "vehicle_control", "vehicle_motion"])
def test_exact_label_cannot_override_explicit_target_effect(ws, label, effect):
    tree, node = _holder(label)
    r = ws.policy().check_tap(tree, node.center, "tree", {"side_effect": effect})
    assert not r["allowed"] and r["deny"]["class"] == effect


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("extra", ["string", "effect"])
def test_page_rules_for_same_label_are_not_excluded(ws, label, extra):
    tree, node = _holder(label)
    rules = [label] if extra == "string" else [{"pattern": label, "class": "vehicle_control"}]
    r = ws.policy().check_tap(tree, node.center, "tree", {"side_effect": "navigation"}, rules)
    assert not r["allowed"] and r["deny"]["class"] == ("state_deny" if extra == "string" else "vehicle_control")


def test_payment_remains_hard_denied_even_with_all_requested_grants(ws):
    tree, node = _holder("附近充电站", extra=["支付充电费"])
    pol = ws.policy()
    for effect in ("navigation", "payment"):
        r = pol.check_tap(tree, node.center, "tree", {"side_effect": effect}, authorized=pol.authorizable | {"payment"})
        assert not r["allowed"]
    assert not pol.check_authorizations(["payment"], {"payment"}, "pytest-supervisor")["ok"]


@pytest.mark.parametrize("shared", [True, False])
def test_vehicle_list_heading_preserves_vehicle_selection_authorization(ws, shared):
    heading = _node("Text", "[20,20][400,60]", text="被授权的车辆")
    vin = _node("Text", "[20,80][400,120]", text="车架号：FIXTURE-7885")
    row = _node("Row", "[0,0][500,180]", clickable=shared, children=[heading, vin])
    if not shared:
        row["children"][1] = _node("Row", "[0,70][500,150]", clickable=True, children=[vin])
    tree = UiTree(_node("orgRoot", "[0,0][500,400]", children=[row]))
    node = tree.find_text("车架号：FIXTURE-7885")[0]
    page_rule = [{"pattern": "车架号", "class": "vehicle_selection"}]
    pol = ws.policy()
    assert not pol.check_tap(tree, node.center, "tree", {"side_effect": "vehicle_selection"}, page_rule)["allowed"]
    r = pol.check_tap(tree, node.center, "tree", {"side_effect": "vehicle_selection"}, page_rule, {"vehicle_selection"})
    assert r["allowed"] and r["authorized_by"] == "vehicle_selection"
    assert not pol.check_tap(tree, node.center, "tree", {"side_effect": "navigation"}, page_rule, {"vehicle_selection"})["allowed"]


@pytest.mark.parametrize("label", LABELS)
def test_pure_text_assertion_never_requires_action_authorization(ws, label):
    confirm(ws, "K-MOT-001")
    tree, _ = _holder(label)
    obs = {"obs_id": "fixture", "fresh": True, "tree": {"status": "ok"}, "screenshot": {},
           "page": {"state": "fixture_home"}}
    kn = KnowledgeStore(ws.root)
    kn.bind({"app": "motor_hm", "app_version": "3.0.3.200"})
    a = {"id": "A1", "type": "text_present", "at": "S1", "text": label, "oracle_refs": ["K-MOT-001"]}
    r = evaluate_assertion(a, {"S1": {"outcome": "passed", "final_obs": "fixture"}}, lambda _: (obs, tree), kn, ws.app("motor_hm"))
    assert r["result"] == "pass"


@pytest.mark.parametrize("label", LABELS)
def test_executor_and_dispatch_recheck_allow_known_navigation(ws, tmp_path, monkeypatch, label):
    home = {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Button", "b": [40, 400, 1280, 520], "click": True, "go": "detail", "children": [
            {"t": "Text", "text": label, "b": [60, 420, 1260, 500]}]}]}}
    spec = _lab_world(tmp_path, "policy-nav", {"home": home})
    ctx = _ctx(ws, spec)
    taps = []
    real = ctx.device.tap

    def tap(*point):
        taps.append(point)
        return real(*point)
    monkeypatch.setattr(ctx.device, "tap", tap)
    r = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0, "target": parse_select(f"text={label}"),
                               "revalidate": True, "expect_text": "详情页"}, "fixture")
    assert r["outcome"] == "passed" and len(taps) == 1
    assert r["attempts"][0]["dispatch"]["sent"]
    events = [e for e in read_jsonl(ctx.store.events_path) if e.get("event") == "dispatch" and e.get("op") == "tap"]
    assert len(events) == 1 and events[0]["checks"]["tap"]["policy"]["allowed"]
    assert ctx.device.state["stack"][-1] == "detail"
