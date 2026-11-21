"""#256: synthetic path-policy regression, not work-machine acceptance."""
import pytest

from test_relative_swipe import _world, _step
from test_dispatch_regression import _ctx
from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.util import read_json, write_json


def setup(ws, tmp_path, *, child_text="收藏", child_bounds=None, clickable=False,
          scope_text="", child_click=False, kind="List"):
    spec = _world(tmp_path, kind=kind, children=[
        {"t": "Text", "text": child_text, "b": child_bounds or [110, 210, 220, 260],
         "click": child_click},
    ])
    data = read_json(spec.split(":", 1)[1])
    scope = data["screens"]["home"]["root"]["children"][0]
    scope.update(click=clickable, text=scope_text)
    write_json(spec.split(":", 1)[1], data)
    return _ctx(ws, spec)


@pytest.mark.parametrize("kind", ["List", "Scroll", "Grid"])
@pytest.mark.parametrize("label", ["收藏", "支付", "确定"])
def test_off_path_descendant_does_not_describe_blank_scroll_scope(ws, tmp_path, kind, label):
    ctx = setup(ws, tmp_path, kind=kind, child_text=label)
    _, tree = ctx.observe("synthetic-scope")
    scope = next(n for n in tree.nodes if n.id == "gesture_scope")
    assert not ctx.policy.check_tap(tree, (500, 500), "tree", node=scope)["allowed"]
    result = Executor(ctx).run_step(_step(expect_text="RESULT_right"), "scope-scroll")
    assert result["outcome"] == "passed", result
    assert result["attempts"][-1]["dispatch"]["sent"] is True
    assert ctx.actions == 1


@pytest.mark.parametrize("kwargs", [
    {"child_bounds": [400, 450, 600, 550]},
    {"child_bounds": [400, 450, 600, 550], "child_click": True},
    {"clickable": True},
    {"scope_text": "收藏"},
    {"scope_text": "支付"},
    {"scope_text": "确定"},
])
def test_scoping_does_not_remove_actual_receiver_or_own_semantics(ws, tmp_path, monkeypatch, kwargs):
    ctx = setup(ws, tmp_path, **kwargs)
    calls = []
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(), "unsafe-scope-scroll")
    assert result["final_status"] == "policy_denied", result
    assert calls == [] and ctx.actions == 0


def test_page_rule_on_scope_label_is_not_bypassed(ws, tmp_path, monkeypatch):
    ctx = setup(ws, tmp_path, scope_text="普通列表")
    monkeypatch.setattr(ctx.app.states, "deny_for_page", lambda page: ["普通列表"])
    result = Executor(ctx).run_step(_step(), "page-rule-scroll")
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0


@pytest.mark.parametrize("variant", [
    "description",
    "clickable_ancestor",
    "clickable_nested_container",
    "long_clickable_nested_container",
    "nested_scroll_container",
])
def test_scope_narrowing_keeps_other_semantic_owners(ws, tmp_path, variant):
    spec = _world(tmp_path, kind="List", children=[
        {"t": "Text", "text": "收藏", "b": [110, 210, 220, 260]}])
    data = read_json(spec.split(":", 1)[1])
    root = data["screens"]["home"]["root"]
    scope = root["children"][0]
    scope["click"] = False
    if variant == "description":
        scope["desc"] = "支付"
    elif variant == "clickable_ancestor":
        root["click"] = True
    elif variant == "clickable_nested_container":
        scope["children"] = [{"t": "Column", "b": [100, 200, 900, 800], "click": True,
                              "children": scope["children"]}]
    elif variant == "long_clickable_nested_container":
        scope["children"] = [{"t": "Row", "b": [100, 200, 900, 800], "attrs": {"longClickable": "true"},
                              "children": scope["children"]}]
    else:
        scope["children"] = [{"t": "Scroll", "b": [100, 200, 900, 800], "scroll": True,
                              "children": scope["children"]}]
    write_json(spec.split(":", 1)[1], data)
    ctx = _ctx(ws, spec)
    result = Executor(ctx).run_step(_step(), "semantic-owner-scroll")
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0


@pytest.mark.parametrize("wrapper_type", [
    "Row", "Column", "ListItem", "ListItemGroup", "GridItem", "Flex", "Stack", "RelativeContainer", "__Common__",
])
@pytest.mark.parametrize("label", ["收藏", "精选/充电/购车等", "支付", "确定"])
def test_passive_layout_wrappers_inside_scroll_scope_do_not_inherit_off_path_labels(
    ws, tmp_path, wrapper_type, label
):
    """#262: dragging blank area of a passive layout row/group inside List ignores off-path sibling labels."""
    spec = _world(tmp_path, kind="List", children=[
        {"t": wrapper_type, "b": [100, 200, 900, 800], "click": False, "children": [
            {"t": "Text", "text": label, "b": [110, 210, 220, 260], "click": False},
        ]},
    ])
    data = read_json(spec.split(":", 1)[1])
    data["screens"]["home"]["root"]["children"][0]["click"] = False
    write_json(spec.split(":", 1)[1], data)
    ctx = _ctx(ws, spec)
    result = Executor(ctx).run_step(_step(expect_text="RESULT_right"), "passive-wrapper-scroll")
    assert result["outcome"] == "passed", result
    assert result["attempts"][-1]["dispatch"]["sent"] is True
    assert ctx.actions == 1


def test_uitest_empty_attr_outer_root_does_not_self_lock_passive_scroll_chain(ws):
    """#262: real HarmonyOS uitest 7.0.0.1 wraps trees in a 26-key empty-attr root (type='', clickable='')."""
    from vpdtest.tree import UiTree

    empty_root_attrs = {
        "accessibilityId": "", "bounds": "[0,0][2584,1828]", "checkable": "", "checked": "",
        "clickable": "", "description": "", "enabled": "", "focused": "", "hashcode": "",
        "hint": "", "hostWindowId": "", "id": "", "key": "", "longClickable": "",
        "origBounds": "", "scrollable": "", "selected": "", "text": "", "type": "",
        "visible": "", "bundleName": "", "pagePath": "", "abilityName": "", "zIndex": "",
        "opacity": "", "extra": "",
    }
    raw = {
        "attributes": empty_root_attrs,
        "children": [{
            "attributes": {"type": "root", "bounds": "[0,107][2584,1828]", "clickable": "false",
                           "scrollable": "false", "longClickable": "false", "visible": "true"},
            "children": [{
                "attributes": {"type": "List", "id": "mine_list", "bounds": "[0,261][2584,1597]",
                               "clickable": "false", "scrollable": "true", "longClickable": "false",
                               "visible": "true"},
                "children": [{
                    "attributes": {"type": "ListItemGroup", "bounds": "[44,277][2540,1200]",
                                   "clickable": "false", "scrollable": "false", "longClickable": "false",
                                   "visible": "true"},
                    "children": [{
                        "attributes": {"type": "ListItem", "bounds": "[44,277][2540,1200]",
                                       "clickable": "false", "scrollable": "false", "longClickable": "false",
                                       "visible": "true"},
                        "children": [{
                            "attributes": {"type": "Row", "bounds": "[44,728][2540,914]",
                                           "clickable": "false", "scrollable": "false",
                                           "longClickable": "false", "visible": "true"},
                            "children": [
                                {"attributes": {"type": "Text", "text": "精选/充电/购车等",
                                                "bounds": "[80,750][400,820]", "clickable": "false",
                                                "visible": "true"}, "children": []},
                                {"attributes": {"type": "Button", "bounds": "[900,750][1100,850]",
                                                "clickable": "true", "visible": "true"},
                                 "children": [{"attributes": {"type": "Text", "text": "收藏",
                                                              "bounds": "[920,770][1050,830]",
                                                              "clickable": "false", "visible": "true"},
                                               "children": []}]},
                            ],
                        }],
                    }],
                }],
            }],
        }],
    }
    tree = UiTree(raw)
    scope = next(n for n in tree.nodes if n.type == "List")
    row = next(n for n in tree.nodes if n.type == "Row")
    text_charge = next(n for n in tree.nodes if n.text == "精选/充电/购车等")
    fav_btn = next(n for n in tree.nodes if n.type == "Button")
    target = {"label": "relative_scroll", "side_effect": "navigation"}

    # Blank area of passive Row inside ListItemGroup > ListItem > List: allowed.
    ok = ws.policy().check_tap(tree, (1500, 820), "tree", target, node=row, scroll_scope=scope)
    assert ok["allowed"], ok
    # Direct hit on controlled Text or clickable Button: still denied.
    deny_text = ws.policy().check_tap(tree, (200, 780), "tree", target, node=text_charge, scroll_scope=scope)
    assert not deny_text["allowed"] and deny_text["deny"]["class"] == "vehicle_control"
    deny_btn = ws.policy().check_tap(tree, (980, 800), "tree", target, node=fav_btn, scroll_scope=scope)
    assert not deny_btn["allowed"] and deny_btn["deny"]["class"] == "user_data"


def test_passive_wrapper_own_label_and_clickable_parent_are_still_denied(ws, tmp_path):
    spec = _world(tmp_path, kind="List", children=[
        {"t": "ListItemGroup", "b": [100, 200, 900, 800], "click": False, "desc": "充电", "children": [
            {"t": "Row", "b": [100, 200, 900, 800], "click": False, "children": []},
        ]},
    ])
    data = read_json(spec.split(":", 1)[1])
    data["screens"]["home"]["root"]["children"][0]["click"] = False
    write_json(spec.split(":", 1)[1], data)
    ctx = _ctx(ws, spec)
    result = Executor(ctx).run_step(_step(), "passive-ancestor-desc-scroll")
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0


def test_unrelated_scope_argument_cannot_narrow_a_tap(ws, tmp_path):
    ctx = setup(ws, tmp_path)
    _, tree = ctx.observe("synthetic-scope")
    scope = next(n for n in tree.nodes if n.id == "gesture_scope")
    result = ctx.policy.check_tap(tree, (500, 500), "tree", node=scope, scroll_scope=tree.nodes[0])
    assert not result["allowed"] and result["deny"]["class"] == "user_data"
