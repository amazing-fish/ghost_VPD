"""Reported List/ListItemGroup/ListItem/Column/Row/Button/Column hierarchy.

All trees, transitions and geometry below are synthetic, not exported phone data.
"""
from copy import deepcopy

import pytest

from test_dispatch_regression import _ctx
from test_relative_swipe import _world, _step
from vpdtest.execute import Executor
from vpdtest.util import read_json, write_json


KINDS = ["ListItemGroup", "ListItem", "Column", "Row"]


def hierarchy(tmp_path, hit_depth=0, mutate=None):
    spec = _world(tmp_path, kind="List")
    data = read_json(spec.split(":", 1)[1])
    data["screen"] = [2584, 1828]
    root = data["screens"]["home"]["root"]
    root["b"] = [0, 0, 2584, 1828]
    scope = root["children"][0]
    scope.update(b=[0, 261, 2584, 1597], click=False)
    nodes = []
    parent = scope
    for i, kind in enumerate(KINDS):
        node = {"t": kind, "id": f"layout_{i}", "click": False,
                "b": [44, 277, 2540, 696] if i <= hit_depth else [900, 600, 1100, 680]}
        parent["children"] = [node]
        nodes.append(node)
        parent = node
    button = {"t": "Button", "id": "favorite_action", "click": True, "b": [900, 600, 1100, 680],
              "children": [{"t": "Column", "b": [900, 600, 1100, 680], "children": [
                  {"t": "Text", "text": "收藏", "b": [949, 613, 1015, 652]}]}]}
    parent["children"] = [button]
    if mutate:
        mutate(root, scope, nodes, button)
    write_json(spec.split(":", 1)[1], data)
    return spec


def step():
    return _step(start=[0.5, 0.75], end=[0.5, 0.25], speed=2000, expect_text="RESULT_up")


@pytest.mark.parametrize("hit_depth", range(4))
def test_every_reported_passive_layout_level_has_the_same_scroll_semantics(ws, tmp_path, hit_depth):
    ctx = _ctx(ws, hierarchy(tmp_path, hit_depth))
    _, tree = ctx.observe("synthetic-complete-hierarchy")
    node = tree.node_at(1292, 640)
    assert node.id == f"layout_{hit_depth}"
    assert not ctx.policy.check_tap(tree, (1292, 640), "tree", node=node)["allowed"]
    result = Executor(ctx).run_step(step(), "full-layout-scroll")
    assert result["outcome"] == "passed", result
    assert ctx.actions == 1


@pytest.mark.parametrize("kind", ["GridItem", "Flex"])
def test_other_supported_layout_still_needs_complete_chain(ws, tmp_path, kind):
    ctx = _ctx(ws, hierarchy(tmp_path, 3, lambda root, scope, nodes, button: nodes[2].update(t=kind)))
    assert Executor(ctx).run_step(step(), "supported-chain")["outcome"] == "passed"


@pytest.mark.parametrize("variant", [
    "outside_click", "inside_click", "ancestor_text", "ancestor_description", "nested_scroll",
    "unknown_layout", "long_true", "long_unknown", "actual_button", "slider", "unknown_surface", "overlay",
])
def test_chain_does_not_erase_action_owners_or_uncertain_surfaces(ws, tmp_path, variant):
    def mutate(root, scope, nodes, button):
        if variant == "outside_click": root["click"] = True
        elif variant == "inside_click": nodes[1]["click"] = True
        elif variant == "ancestor_text": nodes[1]["text"] = "充电"
        elif variant == "ancestor_description": nodes[1]["desc"] = "支付"
        elif variant == "nested_scroll": nodes[1]["scroll"] = True
        elif variant == "unknown_layout": nodes[1]["t"] = "CustomLayout"
        elif variant.startswith("long_"):
            nodes[1]["attrs"] = {"longClickable": "true" if variant == "long_true" else "unknown"}
        else:
            extra = {"t": "Button", "text": "支付", "b": [1291, 620, 1294, 650], "click": True}
            if variant == "actual_button":
                nodes[-1]["children"].append(extra)
            elif variant == "slider":
                extra.update(t="Slider", text="", click=False)
                nodes[-1]["children"].append(extra)
                nodes[-1]["children"].append({"t": "Text", "text": "普通说明", "b": extra["b"]})
            elif variant == "unknown_surface":
                extra.update(t="XComponent", text="", click=False)
                nodes[-1]["children"].append(extra)
            else: root["children"].append(extra)
    ctx = _ctx(ws, hierarchy(tmp_path, 3, mutate))
    result = Executor(ctx).run_step(step(), "guarded-layout-scroll")
    assert result["outcome"] != "passed", result
    assert result["final_status"] in ("policy_denied", "target_unresolved", "target_obscured"), result
    assert ctx.actions == 0


def test_false_long_click_flag_does_not_become_python_truthiness(ws, tmp_path):
    ctx = _ctx(ws, hierarchy(tmp_path, 3, lambda root, scope, nodes, button:
                             nodes[1].update(attrs={"longClickable": "false"})))
    assert Executor(ctx).run_step(step(), "false-flag-chain")["outcome"] == "passed"


def test_scope_from_other_tree_cannot_authorize_layout_chain(ws, tmp_path):
    ctx = _ctx(ws, hierarchy(tmp_path, 3))
    _, tree = ctx.observe("synthetic-same-tree")
    scope = next(n for n in tree.nodes if n.id == "gesture_scope")
    node = tree.node_at(1292, 640)
    assert ctx.policy._scroll_layout_texts(tree, node, deepcopy(scope)) is None
    assert ctx.policy._scroll_layout_texts(tree, node, tree.nodes[0]) is None


@pytest.mark.parametrize("attribute", ["clickable", "scrollable"])
@pytest.mark.parametrize("value", ["unknown", "", None])
def test_normalization_default_is_not_proof_of_passive_chain(ws, tmp_path, attribute, value):
    def mutate(root, scope, nodes, button):
        nodes[1]["attrs"] = {attribute: value}
    ctx = _ctx(ws, hierarchy(tmp_path, 3, mutate))
    result = Executor(ctx).run_step(step(), "unknown-raw-flag")
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0


def test_unknown_clickable_above_scope_does_not_hide_an_owner(ws, tmp_path):
    ctx = _ctx(ws, hierarchy(tmp_path, 3, lambda root, scope, nodes, button:
                             root.update(attrs={"clickable": "unknown"})))
    result = Executor(ctx).run_step(step(), "unknown-outer-owner")
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0


@pytest.mark.parametrize("value", ["true", "unknown"])
def test_long_click_owner_above_scope_keeps_full_semantics(ws, tmp_path, value):
    ctx = _ctx(ws, hierarchy(tmp_path, 3, lambda root, scope, nodes, button:
                             root.update(attrs={"longClickable": value})))
    result = Executor(ctx).run_step(step(), "outer-long-owner")
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0


def test_document_wrapper_exception_cannot_hide_real_interaction_metadata(ws, tmp_path):
    ctx = _ctx(ws, hierarchy(tmp_path, 3))
    _, tree = ctx.observe("synthetic-document-wrapper")
    scope = next(n for n in tree.nodes if n.id == "gesture_scope")
    node = tree.node_at(1292, 640)
    root = tree.nodes[0]
    assert root.type == "orgRoot" and root.parent is None
    assert ctx.policy._scroll_layout_texts(tree, node, scope) == []
    root.attrs["clickable"] = "unknown"
    assert ctx.policy._scroll_layout_texts(tree, node, scope) is None
    del root.attrs["clickable"]
    root.attrs["longClickable"] = "true"
    assert ctx.policy._scroll_layout_texts(tree, node, scope) is None
