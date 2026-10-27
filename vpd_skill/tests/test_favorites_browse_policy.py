"""#111: the observed 收藏夹 entry is browse navigation, not a data mutation.

The historical AA fixture is a projection, not source tree bytes. All devices in
these checks are fakes/replay; no destination is certified on a physical device.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_goal_loop import _lab_world
from vpdtest.devices import DeviceResult
from vpdtest.dispatch import Dispatcher
from vpdtest.execute import Executor
from vpdtest.experience import ExperienceStore
from vpdtest.locate import resolve
from vpdtest.observe import RunStore
from vpdtest.policy import Policy
from vpdtest.tree import UiTree
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import RunContext

BUNDLE = "com.yinwang.qiankunapp.hm"
FIXTURE = read_json(Path(__file__).parent / "fixtures" / "qiankun_search_s293.json")


def _node(kind, bounds="[0,0][500,500]", text="", children=(), **attrs):
    return {"attributes": {"type": kind, "bounds": bounds, "text": text, **attrs}, "children": list(children)}


def _raw():
    return _node("root", children=[_node("Stack", id="VPD_HOME_VIEW", children=[
        _node("Text", "[10,10][300,50]", "收藏及常去"),
        _node("Row", "[10,100][400,200]", clickable="true", children=[
            _node("Text", "[20,120][200,160]", "收藏夹")]),
    ])], bundleName=BUNDLE)


def _layout(tree):
    return {"status": "ok", "window_px": list(tree.screen_size()), "pane_count": 1, "panes": []}


def _check(ws, tree, *, target=None, layout=True, foreground=BUNDLE, rules=None, sources=None, basis="tree", policy=None):
    app = ws.app("qiankun_hm")
    target = target or app.targets["qk_favorites_entry"]
    node = next(n for n in tree.nodes if n.text == "收藏夹")
    page = app.states.identify(tree, app.bundle, True)
    return (policy or ws.policy()).check_tap(tree, node.center, basis, target,
        app.states.deny_for_page(page) + (rules or []), node=node,
        confirmation_sources=sources or app.states.confirmation_sources(tree, page=page),
        layout=_layout(tree) if layout is True else layout, foreground=foreground)


class _Bridge:
    def __init__(self, foreground=BUNDLE):
        self.calls, self.current = [], foreground

    def screen_locked(self):
        return False

    def foreground(self):
        return self.current

    def tap(self, *args):
        self.calls.append(args)
        return DeviceResult("tap", True, True)

    def input_text(self, *args):
        self.calls.append(args)
        return DeviceResult("input_text", True, True)


def _dispatch(ws, tree, foreground=BUNDLE, layout=None, op="tap"):
    app, policy, bridge = ws.app("qiankun_hm"), ws.policy(), _Bridge(foreground)
    target = app.targets["qk_favorites_entry"]
    loc = resolve("qk_favorites_entry", target, tree, None, app.dir)
    ctx = SimpleNamespace(app=app, policy=policy, authorized=policy.effective_grants(set()), actions=0,
                          safety_hold=None, motion=SimpleNamespace(over=lambda: False, locked=lambda: False))
    att = {"action": "tap"}
    args = [*(loc.get("point") or [0, 0]), *(["query"] if op == "input_text" else [])]
    result = Dispatcher(bridge, ctx=ctx).send(op, *args,
        preconditions={"screen_locked": False, "foreground": app.bundle}, att=att,
        tap={"tree": tree, "loc": loc, "target": target, "target_ref": "qk_favorites_entry",
             "page": app.states.identify(tree, app.bundle, True),
             "layout": _layout(tree) if layout is None else layout})
    return result, att, bridge


def test_historical_export_entry_passes_actual_dispatcher_with_fake_foreground(ws):
    tree = UiTree(deepcopy(FIXTURE["observations"]["obs-0035"]["root"]))
    app = ws.app("qiankun_hm")
    assert app.states.identify(tree, app.bundle, True)["state"] == "unknown"
    assert app.targets["qk_favorites_entry"]["status"] == "unverified"
    result, att, bridge = _dispatch(ws, tree)
    assert result.sent is True and result.ok is True, att
    assert bridge.calls == [(642, 1692)]
    source = att["policy"]["navigation_source"]
    assert source["kind"] == "favorites_browse_navigation"
    assert source["container_idx"] == 133 and source["home_idx"] != 0


def test_current_structure_proves_browse_without_promoting_page_state(ws):
    tree = UiTree(_raw())
    result = _check(ws, tree)
    assert result["allowed"], result
    assert result["navigation_source"]["foreground"] == BUNDLE
    assert ws.app("qiankun_hm").states.identify(tree, BUNDLE, True)["state"] == "unknown"


@pytest.mark.parametrize("change", [
    "editable-title", "setting-title", "clickable-title", "long-clickable-title", "conflicting-title-description",
    "conflicting-title-text", "button-title-ancestor", "editable-title-ancestor", "setting-title-ancestor",
    "disabled-title-ancestor", "disabled-row-ancestor", "row-outside-home", "row-one-pixel-outside-home",
    "title-outside-home", "clickable-text-title-ancestor", "clickable-row-title-ancestor", "editable-row-ancestor",
])
def test_title_and_receiver_need_static_enabled_in_home_evidence(ws, change):
    raw = _raw()
    home = raw["children"][0]
    header, row = home["children"]
    if change == "editable-title":
        header["attributes"]["type"] = "TextInput"
    elif change == "setting-title":
        header["attributes"]["type"] = "Switch"
    elif change == "clickable-title":
        header["attributes"]["clickable"] = "true"
    elif change == "long-clickable-title":
        header["attributes"]["longClickable"] = "true"
    elif change == "conflicting-title-description":
        header["attributes"]["description"] = "重命名"
    elif change == "conflicting-title-text":
        header["attributes"].update(text="用户输入值", description="收藏及常去")
    elif change in ("button-title-ancestor", "editable-title-ancestor", "setting-title-ancestor"):
        kind = {"button-title-ancestor": "Button", "editable-title-ancestor": "TextInput",
                "setting-title-ancestor": "Switch"}[change]
        home["children"][0] = _node(kind, "[10,10][300,50]", children=[header])
    elif change == "disabled-title-ancestor":
        home["children"][0] = _node("Column", "[10,10][300,50]", children=[header], enabled="false")
    elif change == "disabled-row-ancestor":
        home["children"][1] = _node("Column", "[10,100][400,200]", children=[row], enabled="false")
    elif change in ("clickable-text-title-ancestor", "clickable-row-title-ancestor"):
        home["children"][0] = _node("Text" if change == "clickable-text-title-ancestor" else "Row",
                                    "[10,10][300,50]", children=[header], clickable="true")
    elif change == "editable-row-ancestor":
        home["children"][1] = _node("TextInput", "[10,100][400,200]", children=[row])
    elif change == "row-outside-home":
        home["attributes"]["bounds"] = "[0,0][500,90]"
    elif change == "row-one-pixel-outside-home":
        home["attributes"]["bounds"] = "[0,0][500,199]"
    elif change == "title-outside-home":
        home["attributes"]["bounds"] = "[0,60][500,500]"
    result, att, bridge = _dispatch(ws, UiTree(raw))
    assert result.sent is False and bridge.calls == [], (change, att)
    assert att["status"] == "policy_denied"


def test_observed_column_title_gesture_is_not_an_editable_or_setting_control(ws):
    raw = _raw()
    home = raw["children"][0]
    home["children"][0] = _node("Column", "[10,10][300,50]", children=[home["children"][0]], clickable="true")
    result, att, bridge = _dispatch(ws, UiTree(raw))
    assert result.sent is True and len(bridge.calls) == 1, att


@pytest.mark.parametrize("change", [
    "missing-home", "wrong-home-type", "root-home", "missing-header", "wrong-parent", "duplicate-home",
    "duplicate-label", "duplicate-header", "hidden-home", "hidden-header", "hidden-ancestor",
    "clipped-home", "clipped-row", "disabled-row", "nested-receiver", "editable", "switch", "overlay", "modal",
    "mixed-rename", "mixed-delete", "mixed-control", "mixed-payment", "foreign-tree-bundle",
    "foreign-label-bundle", "foreign-header-bundle",
])
def test_invalid_context_or_mixed_receiver_remains_denied(ws, change):
    raw = _raw()
    home, header, row = raw["children"][0], raw["children"][0]["children"][0], raw["children"][0]["children"][1]
    if change == "missing-home":
        home["attributes"]["id"] = "other"
    elif change == "wrong-home-type":
        home["attributes"]["type"] = "Column"
    elif change == "root-home":
        raw = home
    elif change == "missing-header":
        header["attributes"]["text"] = "常去地点"
    elif change == "wrong-parent":
        row["children"] = [_node("Column", "[10,100][400,200]", children=row["children"])]
    elif change == "duplicate-home":
        raw["children"].append(deepcopy(home))
    elif change == "duplicate-label":
        row["children"].append(deepcopy(row["children"][0]))
    elif change == "duplicate-header":
        home["children"].append(deepcopy(header))
    elif change == "hidden-home":
        home["attributes"]["visible"] = " FALSE "
    elif change == "hidden-header":
        header["attributes"]["visible"] = False
    elif change == "hidden-ancestor":
        home["children"][1] = _node("Column", children=[row], visible="0")
    elif change == "clipped-home":
        raw["children"] = [_node("List", "[0,0][499,500]", children=[home])]
    elif change == "clipped-row":
        home["children"][1] = _node("List", "[0,0][500,199]", children=[row])
    elif change == "disabled-row":
        row["attributes"]["enabled"] = "false"
    elif change == "nested-receiver":
        row["children"].append(_node("Button", "[300,120][390,160]", clickable="true"))
    elif change in ("editable", "switch"):
        row["children"].append(_node("TextInput" if change == "editable" else "Switch", "[300,120][390,160]"))
    elif change in ("overlay", "modal"):
        raw["children"].append(_node("Button" if change == "overlay" else "Dialog", "[0,0][500,500]",
                                    "遮挡", clickable="true"))
    elif change.startswith("mixed-"):
        text = {"mixed-rename": "重命名", "mixed-delete": "删除", "mixed-control": "鸣笛",
                "mixed-payment": "支付"}[change]
        row["children"].append(_node("Text", "[240,120][390,160]", text))
    elif change == "foreign-tree-bundle":
        raw["attributes"]["bundleName"] = "app.huawei.hmos.motor"
    elif change == "foreign-label-bundle":
        row["children"][0]["attributes"]["bundleName"] = "other.app"
    elif change == "foreign-header-bundle":
        header["attributes"]["bundleName"] = "other.app"
    result = _check(ws, UiTree(raw))
    assert not result["allowed"], (change, result)


@pytest.mark.parametrize("layout", [None, {}, {"status": "error"},
    {"status": "ok", "window_px": [500,500], "pane_count": 2, "panes": [{"node_idx": 1}, {"node_idx": 2}]},
    {"status": "ok", "window_px": [999,500], "pane_count": 1, "panes": []}])
def test_missing_or_multiple_pane_layout_cannot_prove_browse(ws, layout):
    assert not _check(ws, UiTree(_raw()), layout=layout)["allowed"]


@pytest.mark.parametrize("foreground", [None, "", "app.huawei.hmos.motor", "other.app"])
def test_foreign_or_unknown_observed_foreground_does_not_classify_browse(ws, foreground):
    assert not _check(ws, UiTree(_raw()), foreground=foreground)["allowed"]


@pytest.mark.parametrize("change", ["read_only", "input", "point", "forged"])
def test_target_metadata_or_source_flags_do_not_grant_navigation(ws, change):
    raw, target, sources, basis = _raw(), {"side_effect": "navigation"}, [], "tree"
    if change == "read_only":
        target["side_effect"] = "read_only"
    elif change == "input":
        target["allow_input"] = True
    elif change == "point":
        basis = "point"
    else:
        raw["children"][0]["attributes"]["id"] = "other"
        target.update(review="reviewed", source="approved", app=BUNDLE, navigation_context_target="VPD_HOME_VIEW")
        sources = [{"kind": "favorites_browse_navigation", "foreground": BUNDLE, "home_idx": 1}]
    assert not _check(ws, UiTree(raw), target=target, sources=sources, basis=basis)["allowed"]


@pytest.mark.parametrize("rule", [
    {"class": "user_data", "pattern": "^收藏夹$", "reason": "specific restriction"},
    {"class": "vehicle_control", "pattern": "^收藏夹$", "reason": "specific control"},
    {"class": "user_data", "pattern": "收藏|重命名|修改名称|标签|^删除$", "reason": "second legacy-shaped rule"},
])
def test_only_one_legacy_global_hit_can_be_reclassified(ws, rule):
    data = deepcopy(ws.policy().data)
    data["deny_text"].append(rule)
    result = _check(ws, UiTree(_raw()), policy=Policy(data))
    assert not result["allowed"], result


@pytest.mark.parametrize("rule", ["收藏夹", {"class": "user_data", "pattern": "^收藏夹$"},
                                  {"class": "navigation", "pattern": "^收藏夹$"}])
def test_page_restrictions_remain_after_browse_classification(ws, rule):
    result = _check(ws, UiTree(_raw()), rules=[rule])
    assert not result["allowed"] and result["deny"]["source"] == "state"


@pytest.mark.parametrize("label", ["收藏", "取消收藏", "重命名", "修改名称", "标签", "删除"])
def test_data_mutation_labels_remain_controlled(ws, label):
    raw = _raw()
    raw["children"][0]["children"][1]["children"][0]["attributes"]["text"] = label
    tree = UiTree(raw)
    node = next(n for n in tree.nodes if n.text == label)
    result = ws.policy().check_tap(tree, node.center, "tree", {"side_effect": "navigation"}, node=node,
                                  layout=_layout(tree), foreground=BUNDLE)
    assert not result["allowed"] and result["deny"]["class"] == "user_data"


def test_dispatcher_rejects_changed_foreground_before_any_bridge_call(ws):
    result, att, bridge = _dispatch(ws, UiTree(_raw()), foreground="other.app")
    assert result.sent is False and bridge.calls == []
    assert att["status"] == "foreground_changed"


@pytest.mark.parametrize("op", ["input_text", "clear_text"])
def test_browse_proof_cannot_be_used_for_an_input_operation(ws, op):
    result, att, bridge = _dispatch(ws, UiTree(_raw()), op=op)
    assert result.sent is False and bridge.calls == []
    assert att["status"] == "policy_denied"


def _context(ws, tmp_path):
    spec = _lab_world(tmp_path, "favorites-browse", {
        "home": {"root": {"t": "Column", "b": [0,0,1320,2848], "children": [
            {"t": "Stack", "id": "VPD_HOME_VIEW", "b": [0,0,1320,2700], "children": [
                {"t": "Text", "text": "收藏及常去", "b": [40,80,600,160]},
                {"t": "Row", "b": [40,200,1000,360], "click": True, "go": "list", "children": [
                    {"t": "Text", "text": "收藏夹", "b": [60,220,600,320]}]},
            ]}]}},
        "list": {"root": {"t": "Column", "b": [0,0,1320,2848], "children": [
            {"t": "Text", "text": "收藏列表", "b": [40,80,600,160]}]}},
    })
    app = ws.app("qiankun_hm")
    world = read_json(spec.split(":", 1)[1])
    world["bundle"], world["initial"]["foreground"] = app.bundle, app.bundle
    write_json(spec.split(":", 1)[1], world)
    store, device = RunStore(ws.runs_dir), ws.device(spec)
    device.launch(app.bundle, app.ability)
    return RunContext(ws, app, device, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")


@pytest.mark.parametrize("header_type", ["TextInput", "Switch"])
def test_executor_rejects_interactive_header_before_dispatch(ws, tmp_path, header_type):
    ctx = _context(ws, tmp_path)
    ctx.device.world["screens"]["home"]["root"]["children"][0]["children"][0]["t"] = header_type
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
        "target": "qk_favorites_entry", "expect_text": "收藏列表"}, "fixture")
    events = [e for e in read_jsonl(ctx.store.events_path) if e.get("event") == "dispatch"]
    assert result["final_status"] == "policy_denied" and not any(e["sent"] for e in events)
    assert ctx.device.current_screen() == "home"


@pytest.mark.parametrize("context_changes", [False, True])
def test_executor_and_final_dispatcher_use_current_browse_context(ws, tmp_path, monkeypatch, context_changes):
    ctx = _context(ws, tmp_path)
    executor = Executor(ctx)
    original = executor._send_target

    def send(*args, **kwargs):
        if context_changes:
            ctx.device.world["screens"]["home"]["root"]["children"][0]["id"] = "other"
            obs, tree = ctx.observe("context-changed", screenshot=False)
            args = (args[0], args[1], obs, tree, *args[4:])
        return original(*args, **kwargs)

    monkeypatch.setattr(executor, "_send_target", send)
    result = executor.run_step({"id": "A1", "action": "tap", "retry": 0,
        "target": "qk_favorites_entry", "expect_text": "收藏列表"}, "fixture")
    events = [e for e in read_jsonl(ctx.store.events_path) if e.get("event") == "dispatch" and e.get("op") == "tap"]
    assert events
    if context_changes:
        assert result["final_status"] == "policy_denied" and events[-1]["sent"] is False
        assert ctx.device.current_screen() == "home"
    else:
        assert result["outcome"] == "passed", result
        assert events[-1]["sent"] is True and ctx.device.current_screen() == "list"
        assert events[-1]["checks"]["tap"]["policy"]["navigation_source"]["kind"] == "favorites_browse_navigation"
