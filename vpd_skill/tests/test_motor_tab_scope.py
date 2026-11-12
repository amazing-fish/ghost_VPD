"""#236 synthetic tree regressions; these are not foldable-device acceptance evidence."""
import pytest

from vpdtest.locate import resolve
from vpdtest.tree import UiTree
from vpdtest.verdict import _check


def node(kind, bounds, children=(), **attrs):
    return {"attributes": {"type": kind, "bounds": bounds, **attrs}, "children": list(children)}


def navigation_tree(label, wide=False, container=True, navigation=True, selected=True):
    # Both labels are intentionally identical; only the navigation ancestor is selected.
    title = node("Text", "[300,20][600,80]", text=label, id="text_nav_title")
    children = [title]
    if navigation:
        bounds = "[0,200][150,300]" if wide else "[300,920][600,980]"
        tab = node("Column", bounds, [node("Text", bounds, text=label, selected=False)],
                   clickable=True, selected=selected)
        children.append(node("TabBar" if container else "Row", bounds, [tab]))
    return UiTree(node("Root", "[0,0][1200,1000]", children))


@pytest.mark.parametrize("target,label", [("tab_my", "我的"), ("tab_car", "爱车"),
    ("tab_discover", "发现"), ("tab_featured", "精选"), ("tab_buy", "购车"), ("tab_service", "服务")])
@pytest.mark.parametrize("wide", [False, True])
def test_navigation_scope_beats_duplicate_title_without_adaptation(ws, target, label, wide):
    app = ws.app("motor_hm")
    tree = navigation_tree(label, wide)
    result = resolve(target, app.targets[target], tree, None, app.dir, layout={"wide": wide})
    assert result["status"] == "found"
    assert result["strategy_index"] == 0
    assert result["adapted"] is False
    holds, evidence = _check({"type": "node_selected", "target": target, "expected": True},
                              {"obs_id": "synthetic", "layout": {"wide": wide}}, tree, app)
    assert holds is True  # Text.selected=false, clickable Column.selected=true


@pytest.mark.parametrize("target,label", [("tab_my", "我的"), ("tab_car", "爱车")])
@pytest.mark.parametrize("wide", [False, True, None])
def test_title_only_is_missing_navigation_not_product_failure(ws, target, label, wide):
    app = ws.app("motor_hm")
    tree = navigation_tree(label, navigation=False)
    layout = {} if wide is None else {"wide": wide}
    result = resolve(target, app.targets[target], tree, None, app.dir, layout=layout)
    assert result["status"] == "not_found"
    holds, evidence = _check({"type": "node_selected", "target": target, "expected": True},
                              {"obs_id": "synthetic", "layout": layout}, tree, app)
    assert holds is None


@pytest.mark.parametrize("target,label", [("tab_my", "我的"), ("tab_car", "爱车")])
@pytest.mark.parametrize("wide", [False, True, None])
def test_containerless_fallback_requires_known_narrow_layout(ws, target, label, wide):
    app = ws.app("motor_hm")
    tree = navigation_tree(label, wide=bool(wide), container=False)
    result = resolve(target, app.targets[target], tree, None, app.dir,
                     layout={} if wide is None else {"wide": wide})
    assert result["status"] == ("found" if wide is False else "not_found")
    if wide is False:
        assert result["strategy_index"] == 1
        assert result["adapted"] is True


def test_duplicate_title_replay_no_longer_creates_locator_experience(ws):
    from conftest import confirm, widen_scope, world
    from vpdtest.runner import run_case

    for kid in ("K-MOT-001", "K-MOT-002"):
        widen_scope(ws, kid, app_version=["3.0.3.200", "3.0.4.100"])
    confirm(ws, "K-MOT-001", "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_drift.json"))
    assert run["verdict"]["verdict"] == "passed"
    assert run["verdict"]["adapted"] is False
    assert run["experience_candidates"] == []
