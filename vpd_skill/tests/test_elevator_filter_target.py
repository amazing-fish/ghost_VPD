"""#80: observed elevator-chip navigation, not TC-QK-235 product validation.

Source-tree projection keeps its parent chain; replay transitions are synthetic.
No knowledge, production case or Policy grant is changed by this target package.
"""
from copy import deepcopy
from pathlib import Path

import pytest
from test_goal_loop import _lab_world
from vpdtest.execute import Executor
from vpdtest.experience import ExperienceStore
from vpdtest.locate import resolve
from vpdtest.observe import RunStore
from vpdtest.tree import UiTree, parse_bounds
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import RunContext

TARGET = "qk_tag_elevator"
FIXTURE = read_json(Path(__file__).parent / "fixtures" / "qiankun_elevator_s120.json")


def _walk(raw, parents=()):
    yield raw, parents
    for child in raw.get("children", []):
        yield from _walk(child, (*parents, raw))


def _find(raw, key, value):
    return next(node for node, _ in _walk(raw) if node.get(key) == value)


def _compact(raw):
    attrs = raw["attributes"]
    return {"t": attrs.get("type") or "Column", "b": list(parse_bounds(attrs["bounds"])),
            "id": attrs.get("id", ""), "text": attrs.get("text", ""), "desc": attrs.get("description", ""),
            "click": attrs.get("clickable") == "true",
            "attrs": {k: v for k, v in attrs.items() if k not in {"type", "bounds", "id", "text", "description"}},
            "children": [_compact(c) for c in raw.get("children", [])]}


def _parts(root):
    page = _find(root, "id", "SearchPoiPage")
    field = _find(page, "id", "__SearchField__search_input")
    filters = _find(page, "id", "my_location_and_filter_view")
    scroll = filters["children"][0]
    group = scroll["children"][0]
    elevator = group["children"][0]["children"][0]
    peer = group["children"][1]["children"][0]
    return page, field, filters, scroll, group, elevator, peer


def _change(root, change):
    page, field, filters, scroll, group, elevator, peer = _parts(root)
    if change == "dirty-query":
        field["text"] = "B1"
    elif change == "query-placeholder-text":
        field["text"] = "输入目的地或车位号"
    elif change == "duplicate-query":
        duplicate = deepcopy(field)
        duplicate["text"] = "other"
        _find(page, "id", "search_input")["children"].append(duplicate)
    elif change == "duplicate-page":
        next(parents[-1] for node, parents in _walk(root) if node is page)["children"].append(deepcopy(page))
    elif change == "duplicate-filter":
        page["children"].append(deepcopy(filters))
    elif change == "duplicate-offscreen-group":
        duplicate = deepcopy(group)
        for node, _ in _walk(duplicate):
            node["b"] = [node["b"][0], node["b"][1] + 3000, node["b"][2], node["b"][3] + 3000]
        scroll["children"].append(duplicate)
    elif change == "duplicate-label":
        group["children"].append(deepcopy(group["children"][0]))
    elif change.startswith("hidden-"):
        node = {"hidden-field": field, "hidden-row": elevator, "hidden-title": elevator["children"][1],
                "hidden-group": group, "hidden-peer": peer}[change]
        node["attrs"]["visible"] = " FALSE "
    elif change == "mixed-label":
        elevator["desc"] = "支付"
    elif change == "nested-control":
        elevator["children"][1]["children"] = [{"t": "Switch", "b": [132, 310, 248, 355], "click": True}]
    elif change == "clickable-wrapper":
        group["children"][0]["attrs"]["clickable"] = "true"
    elif change == "unknown-group-clickability":
        group["attrs"]["clickable"] = ""
    elif change == "clickable-group":
        group["attrs"]["clickable"] = "true"
    elif change == "outside-filter":
        filters["id"] = "not-a-filter"
    elif change == "clipped-row":
        elevator["b"] = [44, 283, 270, 400]
    elif change in ("overlay", "same-label-overlay"):
        root["children"].append({"t": "Button", "b": [44, 283, 270, 382], "click": True,
                                 "text": "电梯口" if change == "same-label-overlay" else "遮挡"})
    elif change == "modal":
        root["children"].append({"t": "Dialog", "b": [0, 0, 1264, 1848], "click": True,
                                 "children": [{"t": "Text", "b": [30, 100, 400, 200], "text": "确认"}]})
    elif change != "two-panes":
        raise AssertionError(change)


def _context(ws, tmp_path, change=None):
    root = _compact(deepcopy(FIXTURE["root"]))
    _parts(root)[5]["go"] = "detail"
    if change:
        _change(root, change)
    spec = _lab_world(tmp_path, "elevator", {"home": {"root": root}, "detail": {"root": {
        "t": "Column", "b": [0, 0, 1264, 1848], "children": [
            {"t": "Text", "text": "合成电梯口后置", "b": [30, 100, 800, 200]}]}}})
    path = Path(spec.split(":", 1)[1])
    world = read_json(path)
    app = ws.app("qiankun_hm")
    world.update(bundle=app.bundle, screen=[1264, 1848])
    world["initial"]["foreground"] = app.bundle
    write_json(path, world)
    store = RunStore(ws.runs_dir)
    device = ws.device(spec)
    device.launch(app.bundle, app.ability)
    ctx = RunContext(ws, app, device, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    if change == "two-panes":
        observe = ctx.observe
        def two_panes(*args, **kwargs):
            obs, tree = observe(*args, **kwargs)
            obs["layout"]["pane_count"] = 2
            return obs, tree
        ctx.observe = two_panes
    return ctx


def _tap(ctx):
    return Executor(ctx).run_step({"id": "A1", "action": "tap", "target": TARGET, "retry": 0,
                                   "revalidate": True, "expect_text": "合成电梯口后置"}, "fixture")


def _events(ctx):
    return [e for e in read_jsonl(ctx.store.events_path) if e.get("event") == "dispatch" and e.get("op") == "tap"]


def test_original_source_projection_resolves_the_actual_elevator_receiver(ws):
    app, tree = ws.app("qiankun_hm"), UiTree(deepcopy(FIXTURE["root"]))
    loc = resolve(TARGET, app.targets[TARGET], tree, None, app.dir, layout={"pane_count": 1})
    assert loc["status"] == "found" and loc["point"] == [157, 332], loc
    node = tree.nodes[loc["attempts"][-1]["node"]["idx"]]
    assert node.idx == 49 and node.type == "Row" and tree.subtree_texts(node) == ["电梯口"]
    assert tree.touch_target(*loc["point"]).idx == node.idx
    page = app.states.identify(tree, app.bundle, True, {"pane_count": 1})
    assert page["state"] == "qk_spot_search"
    sources = app.states.confirmation_sources(tree, page=page)
    peer = tree.find_text("充电桩")[0]
    charging = ws.policy().check_tap(tree, peer.center, "tree", {"side_effect": "navigation"},
        app.states.deny_for_page(page), node=peer, confirmation_sources=sources)
    assert charging["allowed"] and charging["navigation_source"]["filter_receivers"] == [49, 53]
    assert "qk_tag_charging" not in app.targets


def test_registered_target_reaches_dispatcher_without_a_policy_exemption(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    result = _tap(ctx)
    assert result["outcome"] == "passed", result
    events = _events(ctx)
    assert len(events) == 1 and events[0]["sent"] is True
    policy = events[0]["checks"]["tap"]["policy"]
    assert policy["allowed"] and policy["label"] == "电梯口"
    assert "navigation_source" not in policy and "authorized_by" not in policy
    assert ctx.device.current_screen() == "detail"


@pytest.mark.parametrize("change", [
    "dirty-query", "query-placeholder-text", "duplicate-query", "duplicate-page", "duplicate-filter",
    "duplicate-offscreen-group", "duplicate-label", "hidden-field", "hidden-row", "hidden-title",
    "hidden-group", "hidden-peer", "mixed-label", "nested-control", "clickable-wrapper",
    "unknown-group-clickability", "clickable-group", "outside-filter", "clipped-row", "two-panes",
    "overlay", "same-label-overlay", "modal",
])
def test_uncertain_context_or_receiver_never_dispatches(ws, tmp_path, change):
    ctx = _context(ws, tmp_path, change)
    result = _tap(ctx)
    assert result["outcome"] == "blocked", result
    assert not any(event["sent"] for event in _events(ctx))
    assert ctx.device.current_screen() == "home"


@pytest.mark.parametrize("refresh,revoke", [("predispatch", "query"), ("dispatch-relocate", "query"),
                                            ("dispatch-relocate", "peer"),
                                            ("predispatch", "original-text"),
                                            ("dispatch-relocate", "original-text")])
def test_new_observation_retracts_filter_eligibility(ws, tmp_path, monkeypatch, refresh, revoke):
    ctx = _context(ws, tmp_path, "overlay" if refresh == "dispatch-relocate" else None)
    observe = ctx.observe
    seen = []
    def observe_changed(label="", screenshot=True, **kwargs):
        if refresh in label:
            root = ctx.device.world["screens"]["home"]["root"]
            if refresh == "dispatch-relocate":
                root["children"].pop()  # Remove the overlay: it cannot explain the later rejection.
            if revoke == "query":
                _parts(root)[1]["text"] = "B1"
            elif revoke == "original-text":
                _parts(root)[1]["attrs"]["originalText"] = "B1"
            else:
                _parts(root)[4]["children"].pop()  # The pure double-label group no longer exists.
            seen.append(label)
        return observe(label, screenshot, **kwargs)
    monkeypatch.setattr(ctx, "observe", observe_changed)
    result = _tap(ctx)
    assert seen and result["outcome"] == "blocked", result
    assert result["final_status"] == ("target_stale" if refresh == "predispatch" else "target_unresolved")
    assert not any(event["sent"] for event in _events(ctx))
    assert ctx.device.current_screen() == "home"


def test_base_missing_target_cannot_dispatch_the_new_registered_action(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    # The actual base has no qk_tag_elevator definition. Preserve that baseline
    # instead of claiming a former production selector performed this action.
    del ctx.app.targets[TARGET]
    with pytest.raises(ValueError, match="targets.json 中没有目标 qk_tag_elevator"):
        _tap(ctx)
    assert not _events(ctx) and ctx.device.current_screen() == "home"


def test_archived_text_only_proposal_would_accept_a_dirty_nonfilter_label(ws, tmp_path):
    ctx = _context(ws, tmp_path, "outside-filter")
    field = _find(ctx.device.world["screens"]["home"]["root"], "id", "__SearchField__search_input")
    field["text"] = "B1"
    # Control reproduces the archived local-only target proposal, not main code.
    ctx.app.targets[TARGET] = {"side_effect": "navigation", "strategies": [{"kind": "text", "value": "电梯口"}]}
    assert _tap(ctx)["outcome"] == "passed"
    assert any(event["sent"] for event in _events(ctx))


@pytest.mark.parametrize("value", [None, " ", "\t", "B1"])
def test_missing_or_nonempty_raw_field_text_is_not_an_actual_empty_query(ws, value):
    app, raw = ws.app("qiankun_hm"), deepcopy(FIXTURE["root"])
    field = next(node for node, _ in _walk(raw)
                 if node["attributes"].get("id") == "__SearchField__search_input")
    if value is None:
        field["attributes"].pop("text")
    else:
        field["attributes"]["text"] = value
    loc = resolve(TARGET, app.targets[TARGET], UiTree(raw), None, app.dir, layout={"pane_count": 1})
    assert loc["status"] == "not_found"


@pytest.mark.parametrize("value", ["B1", "123", " "])
def test_conflicting_original_text_does_not_authorize_an_empty_query_tap(ws, tmp_path, value):
    ctx = _context(ws, tmp_path)
    field = _parts(ctx.device.world["screens"]["home"]["root"])[1]
    assert field["text"] == ""
    field["attrs"]["originalText"] = value
    result = _tap(ctx)
    assert result["outcome"] == "blocked", result
    assert not any(event["sent"] for event in _events(ctx))
    assert ctx.device.current_screen() == "home"


@pytest.mark.parametrize("value", [None, ""])
def test_absent_or_empty_original_text_preserves_the_observed_empty_query(ws, tmp_path, value):
    ctx = _context(ws, tmp_path)
    field = _parts(ctx.device.world["screens"]["home"]["root"])[1]
    if value is None:
        field["attrs"].pop("originalText", None)
    else:
        field["attrs"]["originalText"] = value
    assert _tap(ctx)["outcome"] == "passed"
    assert len(_events(ctx)) == 1 and _events(ctx)[0]["sent"] is True


@pytest.mark.parametrize("layout", [{}, {"pane_count": None}, {"pane_count": 2}])
def test_unknown_or_multiple_panes_do_not_guess_a_filter_scope(ws, layout):
    app = ws.app("qiankun_hm")
    loc = resolve(TARGET, app.targets[TARGET], UiTree(deepcopy(FIXTURE["root"])), None, app.dir, layout=layout)
    assert loc["status"] == "not_found"
