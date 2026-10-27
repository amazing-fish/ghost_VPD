"""#111/#118: current input structure, not query placeholder text, identifies search.

The compact AA inputs are projections of historical workmachine exports. Device
bridges in this module are fakes: these checks do not certify live input effects.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from vpdtest.cases import dependency_hash, hypothesis_targets
from vpdtest.devices import DeviceResult
from vpdtest.dispatch import Dispatcher
from vpdtest.knowledge import KnowledgeStore
from vpdtest.locate import resolve
from vpdtest.states import StateModel
from vpdtest.tree import UiTree
from vpdtest.util import read_json

FIXTURE = read_json(Path(__file__).parent / "fixtures" / "qiankun_search_s293.json")
SEARCH = ("obs-0039", "obs-0046", "obs-0051", "obs-0056", "obs-0061")
INPUTS = ("qk_search_input", "qk_spot_search_input")


def _observed(obs):
    return UiTree(deepcopy(FIXTURE["observations"][obs]["root"]))


def _node(kind, bounds="[0,0][400,400]", children=(), **attrs):
    return {"attributes": {"type": kind, "bounds": bounds, **attrs}, "children": list(children)}


def _search(query="A138"):
    return _node("RelativeContainer", children=[_node("Search", "[10,10][390,100]", [
        _node("SearchField", "[20,20][380,90]", text=query, id="__SearchField__search_input")
    ], id="search_input")], id="SearchPoiPage")


def _tree(*children):
    return UiTree(_node("Column", children=children))


@pytest.mark.parametrize("obs", SEARCH)
def test_empty_and_nonempty_exported_search_pages_have_one_actual_input(ws, obs):
    app, tree = ws.app("qiankun_hm"), _observed(obs)
    assert app.states.identify(tree, app.bundle, True)["state"] == "qk_spot_search"
    for ref in INPUTS:
        loc = resolve(ref, app.targets[ref], tree, None, app.dir)
        assert loc["status"] == "found", loc
        hit = loc["attempts"][-1]["node"]
        assert hit["type"] == "SearchField" and hit["id"] == "__SearchField__search_input"
        assert app.targets[ref]["status"] == "unverified"


@pytest.mark.parametrize("obs", ["obs-0015", "obs-0028", "obs-0035"])
def test_entry_and_favorites_exports_are_not_search_pages(ws, obs):
    app, tree = ws.app("qiankun_hm"), _observed(obs)
    assert not app.states.is_state(app.states.identify(tree, app.bundle, True), "qk_spot_search")
    for ref in INPUTS:
        assert resolve(ref, app.targets[ref], tree, None, app.dir)["status"] == "not_found"


@pytest.mark.parametrize("bad", ["missing-page", "wrong-parent", "duplicate", "hidden-ancestor", "clipped"])
def test_bad_structure_never_becomes_search_or_navigation_source(ws, bad):
    app = ws.app("qiankun_hm")
    search = _search()
    children = [search]
    if bad == "missing-page":
        search["attributes"]["id"] = "other-page"
    elif bad == "wrong-parent":
        search["children"][0]["attributes"]["type"] = "Column"
    elif bad == "duplicate":
        children.append(deepcopy(search))
    elif bad == "hidden-ancestor":
        children = [_node("Column", children=children, visible="false")]
    elif bad == "clipped":
        # A 1px clip can round the locator fraction to 1.0, but is not a full anchor.
        field = search["children"][0]["children"][0]
        field["attributes"]["bounds"] = "[20,20][380,4000]"
        children = [_node("List", "[0,0][400,3999]", children=children)]
        tree = UiTree(_node("Column", "[0,0][400,4000]", children=children))
    if bad != "clipped":
        tree = _tree(*children)
    page = app.states.identify(tree, app.bundle, True)
    assert page["state"] == "unknown"
    forged = {"state": "qk_spot_search", "active": ["qk_spot_search"], "candidates": ["qk_spot_search"]}
    sources = app.states.confirmation_sources(tree, page=forged)
    assert not any(s["kind"] == "state_navigation_context" for s in sources)
    assert all(not c["rules"] for s in sources for c in s.get("candidates", []))


def test_structural_signature_does_not_require_a_charging_tag_or_placeholder(ws):
    app = ws.app("qiankun_hm")
    assert app.states.identify(_tree(_search("query")), app.bundle, True)["state"] == "qk_spot_search"
    assert app.states.identify(_tree(_node("Text", text="输入目的地或车位号")), app.bundle, True)["state"] == "unknown"


def test_structural_anchor_cannot_borrow_required_text_from_another_pane(ws):
    target = ws.app("qiankun_hm").targets["qk_search_input"]
    model = StateModel({"states": {"search": {"require_text": ["LEFT"], "require_target": "input"}}},
                       "bundle", {"input": target})
    tree = _tree(_node("Column", "[0,0][200,400]", [_node("Text", "[0,0][200,100]", text="LEFT")]),
                 _node("Column", "[200,0][400,400]", [_search()]))
    layout = {"panes": [{"index": i, "node_idx": n.idx}
                        for i, n in enumerate(n for n in tree.nodes if n.parent == 0)]}
    page = model.identify(tree, "bundle", True, layout)
    assert page["state"] == "unknown" and all(not p["candidates"] for p in page["panes"])


@pytest.mark.parametrize("ref", [None, "", "missing", [], True])
def test_invalid_or_unknown_required_anchor_is_a_configuration_error(ref):
    with pytest.raises(ValueError, match="require_target"):
        StateModel({"states": {"search": {"require_target": ref}}}, "bundle", {})


@pytest.mark.parametrize("strategy", [{"kind": "point", "value": [1, 2]},
                                      {"kind": "template", "file": "x.png"},
                                      {"kind": "id", "value": ""}])
def test_required_anchor_cannot_fall_back_to_non_tree_or_empty_strategy(strategy):
    with pytest.raises(ValueError, match="require_target"):
        StateModel({"states": {"search": {"require_target": "input"}}}, "bundle",
                   {"input": {"strategies": [strategy]}})


def test_root_is_not_a_positive_state_anchor():
    model = StateModel({"states": {"search": {"require_target": "root"}}}, "bundle",
                       {"root": {"strategies": [{"kind": "id", "value": "root"}]}})
    tree = UiTree(_node("Column", children=[_node("Text", text="content")], id="root"))
    assert model.identify(tree, "bundle", True)["state"] == "unknown"


@pytest.mark.parametrize("field", ["require_target", "navigation_context_target"])
def test_state_anchor_definition_is_bound_to_dependency_and_hypothesis_gate(ws, field):
    app = ws.app("qiankun_hm")
    app.targets["fixture-input"] = {"strategies": [{"kind": "id", "value": "original"}]}
    app.states.states = {"search": {field: "fixture-input"}}
    case = {"steps": [{"id": "S1", "action": "wait"}], "assertions": [], "cleanup": []}
    knowledge = KnowledgeStore(ws.root)
    original = dependency_hash(case, app, knowledge)
    app.targets["fixture-input"]["strategies"][0]["value"] = "changed"
    changed = dependency_hash(case, app, knowledge)
    assert changed != original
    app.targets["unrelated"] = {"strategies": [{"kind": "id", "value": "unused"}]}
    assert dependency_hash(case, app, knowledge) == changed
    app.targets["fixture-input"]["status"] = "hypothesis"
    assert "fixture-input" in hypothesis_targets(case, app)


class _Bridge:
    """Only counts calls; does not certify the live operation or clear postcondition."""
    def __init__(self, bundle):
        self.bundle, self.calls = bundle, []

    def screen_locked(self):
        return False

    def foreground(self):
        return self.bundle

    def input_text(self, *args):
        self.calls.append("input_text")
        return DeviceResult("input_text", True, True)

    def clear_text(self, *args, **kwargs):
        self.calls.append("clear_text")
        return DeviceResult("clear_text", True, True)


@pytest.mark.parametrize("obs", ["obs-0039", "obs-0046"])
@pytest.mark.parametrize("ref", INPUTS)
@pytest.mark.parametrize("op", ["input_text", "clear_text"])
def test_actual_dispatcher_allows_query_input_without_misclassifying_route_planning(ws, obs, ref, op):
    app, tree = ws.app("qiankun_hm"), _observed(obs)
    bridge = _Bridge(app.bundle)
    ctx = SimpleNamespace(app=app, policy=ws.policy(), authorized=set(), actions=0, safety_hold=None,
                          motion=SimpleNamespace(over=lambda: False, locked=lambda: False))
    dispatcher = Dispatcher(bridge, ctx=ctx)
    loc = resolve(ref, app.targets[ref], tree, None, app.dir)
    assert loc["status"] == "found"
    page = app.states.identify(tree, app.bundle, True)
    att = {"action": "input"}
    result = dispatcher.send(op, *loc["point"], "query" if op == "input_text" else 3,
        preconditions={"screen_locked": False, "foreground": app.bundle}, att=att,
        tap={"tree": tree, "loc": loc, "target": app.targets[ref], "page": page,
             "state_deny": app.states.deny_for_page(page)})
    assert result.sent is True and result.ok is True, att
    assert bridge.calls == [op]


@pytest.mark.parametrize("hidden", [False, " FALSE ", "0", " 0 ", 0])
@pytest.mark.parametrize("ref", INPUTS)
def test_all_supported_hidden_ancestor_flags_reject_real_dispatch(ws, hidden, ref):
    app = ws.app("qiankun_hm")
    tree = _tree(_node("Column", children=[_search()], visible=hidden))
    loc = resolve(ref, app.targets[ref], tree, None, app.dir)
    assert loc["status"] == "not_found"
    bridge = _Bridge(app.bundle)
    ctx = SimpleNamespace(app=app, policy=ws.policy(), authorized=set(), actions=0, safety_hold=None,
                          motion=SimpleNamespace(over=lambda: False, locked=lambda: False))
    att = {"action": "input"}
    result = Dispatcher(bridge, ctx=ctx).send("input_text", 0, 0, "query",
        preconditions={"screen_locked": False, "foreground": app.bundle}, att=att,
        tap={"tree": tree, "loc": loc, "target": app.targets[ref],
             "page": app.states.identify(tree, app.bundle, True)})
    assert result.sent is False and bridge.calls == []
