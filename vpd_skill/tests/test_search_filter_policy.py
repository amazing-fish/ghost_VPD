"""Issue84: synthetic search chips exercise the real state/policy/dispatch path.

The hierarchy is a contract fixture, not a captured 1395 tree or product result.
"""
import pytest

from test_goal_loop import _lab_world
from vpdtest.execute import Executor
from vpdtest.experience import ExperienceStore
from vpdtest.observe import RunStore
from vpdtest.session import parse_select
from vpdtest.tree import UiTree
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import RunContext


def _node(kind, bounds, text="", children=(), **attrs):
    return {"attributes": {"type": kind, "bounds": bounds, "text": text, **attrs}, "children": list(children)}


def _tree(label="充电桩", marker=True, input_type="SearchField", extra=(), modal=False,
          overlay=False, nested=False, root_receiver=False, peer=True, extra_receiver=False,
          same_label_overlay=False, peer_hidden=False, same_group_duplicate=False):
    children = []
    if marker:
        children += [_node(input_type, "[10,10][490,80]", "输入目的地或车位号", id="search_input")]
    leaves = [_node("Text", "[20,120][180,160]", label)]
    leaves += [_node("Text", f"[20,{170+i*20}][180,{185+i*20}]", text) for i, text in enumerate(extra)]
    if nested:
        leaves.append(_node("Button", "[170,120][190,160]", label, clickable="true"))
    chips = [_node("Button", "[10,100][200,240]", children=leaves,
                   clickable="false" if root_receiver else "true")]
    if peer:
        chips += [_node("Button", "[210,100][400,240]", clickable="true", children=[
            _node("Text", "[220,120][390,160]", "电梯口")], visible="false" if peer_hidden else "true")]
    if same_group_duplicate:
        chips += [_node("Button", "[10,100][200,240]", "充电桩", clickable="true")]
    if extra_receiver:
        chips += [_node("Button", "[410,100][490,240]", clickable="true")]
    children += [_node("Column", "[0,90][500,250]", children=chips)]
    if overlay:
        children.append(_node("Button", "[10,100][200,240]", "遮挡", clickable="true"))
    if same_label_overlay:
        children.append(_node("Button", "[10,100][200,240]", "充电桩", clickable="true"))
    if modal:
        children.append(_node("Dialog", "[0,0][500,500]", children=[
            _node("Text", "[20,300][480,350]", "确认充电")]))
    tree = UiTree(_node("orgRoot", "[0,0][500,500]", children=children,
                        clickable="true" if root_receiver else "false"))
    return tree, next(n for n in tree.nodes if n.text == label)


def _check(ws, tree, node, basis="tree", rules=None, page=None, target=None):
    model = ws.app("qiankun_hm").states
    page = model.identify(tree, model.bundle, True) if page is None else page
    return ws.policy().check_tap(tree, node.center, basis, target or {"side_effect": "navigation"},
        model.deny_for_page(page) + (rules or []), node=node,
        confirmation_sources=model.confirmation_sources(tree, page=page))


@pytest.mark.parametrize("basis", ["tree", "point"])
def test_search_charging_chip_has_current_navigation_proof(ws, basis):
    tree, node = _tree()
    result = _check(ws, tree, node, basis)
    assert result["allowed"], result
    assert result["navigation_source"]["state"] == "qk_spot_search"
    assert result["navigation_source"]["container_idx"] == tree.clickable_container(node).idx


@pytest.mark.parametrize("change", [
    {"marker": False}, {"input_type": "Text"}, {"modal": True}, {"overlay": True},
    {"root_receiver": True}, {"nested": True}, {"peer": False}, {"extra_receiver": True},
    {"peer_hidden": True}, {"same_group_duplicate": True},
    {"extra": ["启动充电"]},
    {"extra": ["充电设置"]}, {"extra": ["确认"]}, {"extra": ["支付"]},
])
def test_missing_context_or_impure_receiver_cannot_use_filter_classification(ws, change):
    tree, node = _tree(**change)
    result = _check(ws, tree, node)
    assert not result["allowed"]
    assert result["deny"]["class"] != "navigation"  # Preserve the real control/payment restriction.


@pytest.mark.parametrize("label", ["充电", "启动充电桩", "停止充电", "预约充电", "充电桩\n启动充电"])
def test_real_charging_labels_are_still_controlled_on_search_page(ws, label):
    tree, node = _tree(label)
    assert not _check(ws, tree, node)["allowed"]


@pytest.mark.parametrize("rules", [
    ["充电桩"], [{"pattern": "^充电桩$", "class": "vehicle_control"}],
    [{"pattern": "充电", "class": "vehicle_control"}],
    [{"pattern": "^充电桩$", "class": "navigation"}],
])
def test_page_restrictions_are_not_discarded_by_navigation_source(ws, rules):
    tree, node = _tree()
    result = _check(ws, tree, node, rules=rules)
    assert not result["allowed"]
    assert result["deny"]["source"] == "state"


def test_other_global_rule_with_same_class_and_label_is_preserved(ws):
    tree, node = _tree()
    policy = ws.policy()
    policy.data["deny_text"].append({"class": "vehicle_control", "pattern": "^充电桩$", "reason": "specific control"})
    from vpdtest.policy import Policy
    model = ws.app("qiankun_hm").states
    page = model.identify(tree, model.bundle, True)
    result = Policy(policy.data).check_tap(tree, node.center, "tree", {"side_effect": "navigation"},
        model.deny_for_page(page), node=node, confirmation_sources=model.confirmation_sources(tree, page=page))
    assert not result["allowed"] and result["deny"]["reason"] == "specific control"


@pytest.mark.parametrize("state", ["unknown", "ambiguous", "undetermined", "out_of_app"])
def test_unresolved_page_does_not_prove_filter_semantics(ws, state):
    tree, node = _tree()
    page = {"state": state, "candidates": ["qk_spot_search"], "active": [], "interrupts": []}
    assert not _check(ws, tree, node, page=page)["allowed"]


def test_forged_page_name_and_target_flags_do_not_prove_current_search_page(ws):
    tree, node = _tree(marker=False)
    page = {"state": "qk_spot_search", "candidates": ["qk_spot_search"], "active": ["qk_spot_search"]}
    target = {"side_effect": "navigation", "source": "reviewed", "navigation_context_target": "qk_search_input"}
    assert not _check(ws, tree, node, page=page, target=target)["allowed"]


def test_same_named_nonmodal_overlay_cannot_borrow_background_search_context(ws):
    tree, _ = _tree(same_label_overlay=True)
    overlay = [n for n in tree.nodes if n.text == "充电桩"][-1]
    assert tree.touch_target(*overlay.center).idx == overlay.idx
    assert not _check(ws, tree, overlay, basis="point")["allowed"]


def test_filter_pair_in_another_pane_does_not_prove_navigation(ws):
    tree, node = _tree()
    context = next(n for n in tree.nodes if n.id == "search_input")
    group = next(n for n in tree.ancestors(node) if n.type == "Column")
    page = {"state": "qk_spot_search", "candidates": [], "active": ["qk_spot_search"], "panes": [
        {"node_idx": context.idx, "candidates": ["qk_spot_search"]},
        {"node_idx": group.idx, "candidates": []}]}
    assert not _check(ws, tree, node, page=page)["allowed"]


def test_ambiguous_registered_search_input_does_not_prove_context(ws):
    tree, node = _tree()
    tree.raw["children"].append(_node("SearchField", "[10,300][490,370]", "输入目的地或车位号", id="search_input"))
    tree = UiTree(tree.raw)
    node = next(n for n in tree.nodes if n.text == "充电桩")
    assert not _check(ws, tree, node)["allowed"]


def _context(ws, tmp_path):
    spec = _lab_world(tmp_path, "search-chip", {"home": {"root": {"t": "Column", "b": [0,0,1320,2848], "children": [
        {"t": "SearchField", "id": "search_input", "text": "输入目的地或车位号", "b": [40,100,1200,200]},
        {"t": "Column", "b": [20,280,1000,460], "children": [
            {"t": "Button", "b": [40,300,400,440], "click": True, "go": "detail", "children": [
                {"t": "Text", "text": "充电桩", "b": [60,320,380,420]}]},
            {"t": "Button", "b": [480,300,820,440], "click": True, "children": [
                {"t": "Text", "text": "电梯口", "b": [500,320,800,420]}]}]},
    ]}}})
    app = ws.app("qiankun_hm")
    world = read_json(spec.split(":", 1)[1])
    world["bundle"] = app.bundle
    world["initial"]["foreground"] = app.bundle
    write_json(spec.split(":", 1)[1], world)
    store = RunStore(ws.runs_dir)
    device = ws.device(spec)
    device.launch(app.bundle, app.ability)
    return RunContext(ws, app, device, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")


@pytest.mark.parametrize("context_changes", [False, True])
def test_executor_and_dispatch_recheck_use_same_current_filter_semantics(ws, tmp_path, monkeypatch, context_changes):
    ctx = _context(ws, tmp_path)
    executor = Executor(ctx)
    original_send = executor._send_target

    def send(*args, **kwargs):
        if context_changes:
            ctx.device.world["screens"]["home"]["root"]["children"][0]["id"] = "unknown_input"
            # Supply a new current observation, as relocation/recovery does; Dispatcher must
            # derive its proof again, rather than reuse Executor's earlier positive result.
            obs, tree = ctx.observe("context-changed", screenshot=False)
            args = (args[0], args[1], obs, tree, *args[4:])
        return original_send(*args, **kwargs)

    monkeypatch.setattr(executor, "_send_target", send)
    result = executor.run_step({"id": "A1", "action": "tap", "retry": 0,
        "target": parse_select("text=充电桩"), "expect_text": "详情页"}, "fixture")
    events = [e for e in read_jsonl(ctx.store.events_path) if e.get("event") == "dispatch" and e.get("op") == "tap"]
    assert events
    if context_changes:
        assert result["final_status"] == "policy_denied"
        assert not events[-1]["sent"]
        assert ctx.device.current_screen() == "home"
    else:
        assert result["outcome"] == "passed", result
        assert events[-1]["sent"]
        assert events[-1]["checks"]["tap"]["policy"]["navigation_source"]["state"] == "qk_spot_search"
        assert ctx.device.current_screen() == "detail"
