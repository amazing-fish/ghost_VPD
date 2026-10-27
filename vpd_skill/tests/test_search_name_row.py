"""#111: static name-row relations, historical AA and synthetic Runner evidence.

The input field selects a row; the independently read value remains the expected
text. No query text is interpolated into XPath or confirmed as a product rule.
"""
from copy import deepcopy
from pathlib import Path

import pytest
from test_condition_runner import APP, COMMON_RULE, condition_rig
from vpdtest.execute import _measure
from vpdtest.knowledge import KnowledgeStore
from vpdtest.locate import resolve
from vpdtest.readrefs import qualify
from vpdtest.runner import run_case
from vpdtest.tree import UiTree, intersect, parse_bounds
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.verdict import evaluate_assertion

__all__ = ("condition_rig",)
ROW = "qk_search_name_result_row"
SKILL = Path(__file__).parent.parent
AA = read_json(Path(__file__).parent / "fixtures" / "qiankun_search_s293.json")
CASE = Path(__file__).parent / "fixtures" / "search-name-row-case.json"


def _tree(obs="obs-0046"):
    return UiTree(deepcopy(AA["observations"][obs]["root"]))


def _locate(app, tree, layout=None):
    return resolve(ROW, app.targets[ROW], tree, None, app.dir,
                   layout={"pane_count": 1} if layout is None else layout)


def _raw(tree, node):
    path = []
    while node.parent is not None:
        parent = tree.nodes[node.parent]
        path.append(parent.children.index(node.idx))
        node = parent
    raw = tree.raw
    for index in reversed(path):
        raw = raw["children"][index]
    return raw


def _node(kind, bounds="[0,0][300,100]", children=(), **attrs):
    return {"attributes": {"type": kind, "bounds": bounds, **attrs}, "children": list(children)}


def _shift_row(raw, dy):
    """Keep known AA nodes visible=true while moving their full geometry."""
    if "attributes" in raw:
        attrs = raw["attributes"]
        x1, y1, x2, y2 = parse_bounds(attrs["bounds"])
        attrs.update(bounds=f"[{x1},{y1 + dy}][{x2},{y2 + dy}]", visible="true")
    else:  # Replay's compact fixture format becomes the same AA attributes.
        x1, y1, x2, y2 = raw["b"]
        raw["b"] = [x1, y1 + dy, x2, y2 + dy]
    for child in raw.get("children", []):
        _shift_row(child, dy)


def _source(value):
    # A different observation, outside the search response, provides the value.
    tree = UiTree(_node("Column", children=[_node("Text", "[10,10][290,90]", text=value, id="saved")]))
    loc = resolve("saved", {"strategies": [{"kind": "id", "value": "saved"}]}, tree, None, Path("."))
    step = {"id": "R1", "read_scope": "self"}
    measurement = _measure(tree, loc, step)
    return {"action": "read", "outcome": "passed", "final_obs": "source-obs", "measurement": measurement,
            "read_binding": qualify(tree, loc, step, {"obs_id": "source-obs"}, measurement)}


def _oracle(tmp_path, confirmed=True):
    directory = "confirmed-oracle" if confirmed else "unconfirmed-oracle"
    knowledge = KnowledgeStore(tmp_path / directory, facts={"app": "qiankun_hm"})
    if knowledge.get(COMMON_RULE):
        return knowledge
    knowledge.add({"id": COMMON_RULE, "kind": "product_rule", "subject": "pytest.name_row",
                   "statement": "合成名称同行契约，非乾崑产品判据", "expected": "synthetic name consistency",
                   "applies_to": {"app": "qiankun_hm"},
                   "source": {"type": "reviewer", "ref": "tests/test_search_name_row.py synthetic only"}})
    if confirmed:
        knowledge.confirm(COMMON_RULE, reviewer="pytest-synthetic", basis="isolated synthetic contract")
    return knowledge


def _evaluate(app, tree, knowledge, expected="1号车位", layout=None, fresh=True, assertions=None):
    layout = {"pane_count": 1} if layout is None else layout
    obs = {"obs_id": "result-obs", "fresh": fresh, "tree": {"status": "ok"}, "layout": layout,
           "page": app.states.identify(tree, app.bundle, True, layout)}
    steps = {"R1": _source(expected), "S3": {"action": "input", "outcome": "passed", "final_obs": "result-obs"}}
    definitions = assertions if assertions is not None else read_json(CASE)["assertions"]
    return [evaluate_assertion(a, steps, lambda _: (obs, tree), knowledge, app) for a in definitions]


@pytest.mark.parametrize("obs,name", [("obs-0046", "1号车位"), ("obs-0056", "1号停车点"), ("obs-0061", "333")])
def test_exported_name_results_resolve_one_real_entity_row(ws, tmp_path, obs, name):
    app, tree = ws.app("qiankun_hm"), _tree(obs)
    loc = _locate(app, tree)
    assert loc["status"] == "found", loc
    node = tree.nodes[loc["attempts"][-1]["node"]["idx"]]
    assert node.type == "Row" and tree.subtree_texts(node)[0] == name
    assert tree.subtree_texts(node)[-1] == "路线"
    result = _evaluate(app, tree, _oracle(tmp_path), name)
    assert [r["result"] for r in result] == ["pass", "pass", "pass"], result
    assert result[1]["evidence"]["read_sources"][0]["obs"] == "source-obs"
    assert result[1]["evidence"]["obs"] == result[2]["evidence"]["obs"] == "result-obs"
    assert result[2]["evidence"]["within"]["node"]["idx"] == node.idx
    assert app.targets[ROW]["status"] == "unverified"


@pytest.mark.parametrize("obs", ["obs-0039", "obs-0035", "obs-0051"])
def test_empty_home_and_number_query_do_not_become_name_row(ws, obs):
    # I1 is present as metadata in obs0051, but the title is 1号车位:
    # identifier queries do not silently become the name-search dimension.
    assert _locate(ws.app("qiankun_hm"), _tree(obs))["status"] == "not_found"


@pytest.mark.parametrize("change", ["duplicate-name", "nested-entity", "hidden-row", "hidden-title",
                                    "hidden-query", "duplicate-query", "duplicate-page", "empty-query",
                                    "duplicate-title", "borrow-parent-title"])
def test_uncertain_structure_never_supplies_a_positive_row_scope(ws, tmp_path, change):
    app, tree = ws.app("qiankun_hm"), _tree()
    row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
    title = tree.find_text("1号车位")[-1]
    query = tree.find_id("__SearchField__search_input")[0]
    if change == "duplicate-name":
        _raw(tree, tree.nodes[row.parent])["children"].append(deepcopy(_raw(tree, row)))
    elif change == "nested-entity":
        _raw(tree, row)["children"].append(deepcopy(_raw(tree, row)))
    elif change in ("hidden-row", "hidden-title", "hidden-query"):
        node = {"hidden-row": row, "hidden-title": title, "hidden-query": query}[change]
        _raw(tree, node)["attributes"]["visible"] = " FALSE "
    elif change == "duplicate-query":
        _raw(tree, tree.nodes[query.parent])["children"].append(deepcopy(_raw(tree, query)))
    elif change == "duplicate-page":
        page = tree.find_id("SearchPoiPage")[0]
        _raw(tree, tree.nodes[page.parent])["children"].append(deepcopy(_raw(tree, page)))
    elif change == "empty-query":
        _raw(tree, query)["attributes"]["text"] = " \t "
    elif change == "duplicate-title":
        _raw(tree, tree.nodes[title.parent])["children"].append(deepcopy(_raw(tree, title)))
    else:
        # An ancestor's matching name cannot substitute for the row title.
        _raw(tree, title)["attributes"]["text"] = "other"
        _raw(tree, tree.nodes[row.parent])["children"].append(
            _node("Text", "[170,790][400,810]", text="1号车位"))
    tree = UiTree(tree.raw)
    assert _locate(app, tree)["status"] in ("not_found", "ambiguous")
    result = _evaluate(app, tree, _oracle(tmp_path))[-1]
    assert result["result"] == "undeterminable", result


@pytest.mark.parametrize("geometry,dy", [("offscreen", 3000), ("fully-clipped", 1100)])
def test_known_same_name_row_is_not_discarded_before_uniqueness(ws, tmp_path, geometry, dy):
    app, tree = ws.app("qiankun_hm"), _tree()
    row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
    duplicate = deepcopy(_raw(tree, row))
    _shift_row(duplicate, dy)
    _raw(tree, tree.nodes[row.parent])["children"].append(duplicate)
    tree = UiTree(tree.raw)
    shadow = next(n for n in tree.nodes if n.type == "Row" and n.bounds == parse_bounds(duplicate["attributes"]["bounds"]))
    assert shadow.visible and tree.visible_lineage(shadow) and not tree.shown(shadow)
    assert (intersect(shadow.bounds, tree.window_rect()) is None) == (geometry == "offscreen")
    assert _locate(app, tree)["status"] == "not_found"
    result = _evaluate(app, tree, _oracle(tmp_path))
    assert [r["result"] for r in result] == ["pass", "pass", "undeterminable"], result
    assert not all(r["verified"] for r in result)


@pytest.mark.parametrize("hidden", ["row", "title", "ancestor"])
def test_explicitly_hidden_shadow_is_not_an_eligible_duplicate(ws, tmp_path, hidden):
    app, tree = ws.app("qiankun_hm"), _tree()
    row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
    duplicate = deepcopy(_raw(tree, row))
    if hidden == "row":
        duplicate["attributes"]["visible"] = " FALSE "
    elif hidden == "title":
        shadow_tree = UiTree(duplicate)
        title = next(n for n in shadow_tree.nodes if n.text == "1号车位")
        _raw(shadow_tree, title)["attributes"]["visible"] = "0"
    else:
        duplicate = _node("__Common__", children=[duplicate], visible="false")
    _raw(tree, tree.nodes[row.parent])["children"].append(duplicate)
    tree = UiTree(tree.raw)
    assert _locate(app, tree)["status"] == "found"
    assert [r["result"] for r in _evaluate(app, tree, _oracle(tmp_path))] == ["pass"] * 3


@pytest.mark.parametrize("layout", [{"pane_count": 2}, {}, {"pane_count": None}])
def test_multiple_or_unknown_panes_cannot_borrow_a_query(ws, tmp_path, layout):
    app, tree = ws.app("qiankun_hm"), _tree()
    assert _locate(app, tree, layout)["status"] == "not_found"
    result = _evaluate(app, tree, _oracle(tmp_path), layout=layout)[-1]
    assert result["result"] == "undeterminable"


def test_partial_row_and_stale_observation_do_not_prove_presence_or_absence(ws, tmp_path):
    app, tree = ws.app("qiankun_hm"), _tree()
    row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
    # Override the threshold in the test to exercise within's exact rectangle
    # check too, rather than relying only on the target's 1.0 requirement.
    app.targets[ROW]["min_visible_fraction"] = 0.1
    _raw(tree, row)["attributes"]["bounds"] = "[165,818][1266,1900]"
    tree = UiTree(tree.raw)
    assertion = {"id": "A", "at": "S3", "type": "text_absent", "text": "missing", "within": ROW,
                 "oracle_refs": [COMMON_RULE]}
    assert _evaluate(app, tree, _oracle(tmp_path), assertions=[assertion])[0]["result"] == "undeterminable"
    assert _evaluate(app, _tree(), _oracle(tmp_path), fresh=False)[-1]["result"] == "undeterminable"


def test_current_query_is_an_identity_filter_not_an_oracle(ws, tmp_path):
    app, tree = ws.app("qiankun_hm"), _tree()
    for node in tree.find_text("1号车位"):
        _raw(tree, node)["attributes"]["text"] = "other entity"
    tree = UiTree(tree.raw)
    assert _locate(app, tree)["status"] == "found"
    result = _evaluate(app, tree, _oracle(tmp_path), expected="1号车位")
    assert [r["result"] for r in result] == ["pass", "fail", "fail"], result


def test_clipped_query_requires_the_paired_state_and_field_assertions(ws, tmp_path):
    app, tree = ws.app("qiankun_hm"), _tree()
    field = tree.find_id("__SearchField__search_input")[0]
    _raw(tree, field)["attributes"]["bounds"] = "[296,158][1037,1900]"
    result = _evaluate(app, UiTree(tree.raw), _oracle(tmp_path))
    # XPath is an identity relation, not a replacement for the input field's
    # current full-visibility/state evidence. The complete group cannot pass.
    assert result[0]["result"] != "pass" and result[1]["result"] == "undeterminable"
    assert not all(item["verified"] for item in result)


def test_cross_row_metadata_false_positive_is_rejected_by_the_selected_name_row(ws, tmp_path):
    app, tree = ws.app("qiankun_hm"), _tree()
    row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
    copy = deepcopy(_raw(tree, row))
    def replace(raw, mapping):
        a = raw["attributes"]
        a["text"] = mapping.get(a.get("text"), a.get("text", ""))
        for child in raw.get("children", []):
            replace(child, mapping)
    replace(_raw(tree, row), {"I1": "wrong-id"})
    replace(copy, {"1号车位": "different entity"})
    _raw(tree, tree.nodes[row.parent])["children"].append(copy)
    tree = UiTree(tree.raw)
    # I1 is only a synthetic expected marker here, not a new product field rule.
    a = {"id": "A", "at": "S3", "type": "text_present", "text": "I1", "oracle_refs": [COMMON_RULE]}
    legacy = _evaluate(app, tree, _oracle(tmp_path), assertions=[a])[0]
    scoped = _evaluate(app, tree, _oracle(tmp_path), assertions=[{**a, "within": ROW}])[0]
    assert legacy["result"] == "pass"  # Existing whole-tree behavior, the old false-positive control.
    assert scoped["result"] == "fail", scoped


def test_selector_errors_keep_tool_attribution_and_unconfirmed_oracle_stays_pending(ws, tmp_path):
    app, tree = ws.app("qiankun_hm"), _tree()
    pending = _evaluate(app, tree, _oracle(tmp_path, confirmed=False))
    assert [r["result"] for r in pending] == ["pending"] * 3
    app.targets[ROW]["strategies"][0]["value"] = "//*["
    error = _evaluate(app, tree, _oracle(tmp_path))[-1]
    assert error["result"] == "error" and error["attribution"] == "tool"
    assert error["stage"] == "assertion_scope"


def _runner(rig, value):
    ws, _, build = rig
    case = read_json(CASE)
    directory = ws.root / "apps" / APP
    targets = read_json(directory / "targets.json")
    observed = read_json(SKILL / "data" / "apps" / "qiankun_hm" / "targets.json")["targets"]
    targets["targets"].update({key: observed[key] for key in (ROW, "qk_search_input")})
    targets["targets"].update({
        "fixture_saved_name": {"strategies": [{"kind": "id", "value": "saved-name"}]},
        "fixture_open_search": {"side_effect": "navigation", "strategies": [{"kind": "id", "value": "open-search"}]}})
    write_json(directory / "targets.json", targets)
    write_json(directory / "states.json", {"states": {
        "fixture_saved_places": {"require_text": ["合成收藏页"]},
        "qk_spot_search": {"require_target": "qk_search_input"}}})
    path = Path(build().split(":", 1)[1])
    data = read_json(path)
    data["launch_screen"] = "saved"
    data["screens"] = {
        "saved": {"root": {"t": "Column", "b": [0, 0, 400, 800], "children": [
            {"t": "Text", "text": "合成收藏页", "b": [10, 20, 390, 70]},
            {"t": "Text", "id": "saved-name", "text": value, "b": [10, 100, 390, 150]},
            {"t": "Button", "id": "open-search", "text": "进入搜索", "go": "search", "b": [10, 200, 390, 260]}]}},
        "search": {"root": {"t": "Column", "b": [0, 0, 400, 800], "children": [
            {"t": "RelativeContainer", "id": "SearchPoiPage", "b": [0, 0, 400, 800], "children": [
                {"t": "Search", "id": "search_input", "b": [10, 20, 390, 120], "children": [
                    {"t": "SearchField", "id": "__SearchField__search_input", "bind": "query", "text_bound": "query",
                     "b": [20, 30, 380, 110]}]},
                {"t": "Column", "id": "search_list", "b": [10, 180, 390, 650], "children": [
                    {"t": "Row", "b": [20, 220, 380, 400], "children": [
                        {"t": "Image", "b": [20, 240, 40, 270]},
                        {"t": "Column", "b": [50, 230, 290, 390], "children": [
                            {"t": "__Common__", "b": [50, 230, 290, 290], "children": [
                                {"t": "Text", "text": value, "b": [50, 240, 290, 280]}]},
                            {"t": "Row", "b": [50, 310, 290, 370], "children": [
                                {"t": "Text", "text": "合成元数据", "b": [50, 320, 290, 360]}]}]},
                        {"t": "Column", "b": [300, 230, 380, 390], "children": [
                            {"t": "Text", "text": "路线", "b": [300, 280, 380, 330]}]}]}]}]}]}}}
    write_json(path, data)
    return ws, case, "replay:" + str(path)


@pytest.mark.parametrize("name", ["合成收藏42", "第二账号B7", "a' or 1=1 or \"${var:x}-${read:OTHER}\""])
def test_actual_runner_keeps_independent_read_query_and_row_in_one_terminal_observation(condition_rig, name):
    ws, case, spec = _runner(condition_rig, name)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert [a["result"] for a in run["assertions"]] == ["pass", "pass", "pass"], run["steps"]
    result = run["assertions"]
    assert result[1]["evidence"]["obs"] == result[2]["evidence"]["obs"] == run["steps"][-1]["final_obs"]
    assert result[1]["evidence"]["read_sources"][0]["obs"] == run["steps"][1]["final_obs"]
    assert run["steps"][1]["final_obs"] != run["steps"][-1]["final_obs"]
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert len([e for e in events if e["event"] == "dispatch" and e.get("op") == "input_text"]) == 1
    tree = UiTree(read_json(Path(run["dir"]) / "obs" / run["steps"][-1]["final_obs"] / "tree.json"))
    assert tree.find_id("__SearchField__search_input")[0].text == name


@pytest.mark.parametrize("geometry", ["offscreen", "fully-clipped"])
def test_actual_runner_cannot_pass_with_a_known_but_unshown_same_name_row(condition_rig, geometry):
    ws, case, spec = _runner(condition_rig, "合成收藏42")
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    search_list = data["screens"]["search"]["root"]["children"][0]["children"][1]
    duplicate = deepcopy(search_list["children"][0])
    _shift_row(duplicate, 1500 if geometry == "offscreen" else 300)
    if geometry == "fully-clipped":
        duplicate = {"t": "List", "b": [10, 180, 390, 450], "children": [duplicate]}
    search_list["children"].append(duplicate)
    write_json(path, data)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert [a["result"] for a in run["assertions"]] == ["pass", "pass", "undeterminable"], run["assertions"]
    assert run["verdict"]["verdict"] != "passed" and run["verdict"]["product_verdict"] != "passed"
    tree = UiTree(read_json(Path(run["dir"]) / "obs" / run["steps"][-1]["final_obs"] / "tree.json"))
    rows = [n for n in tree.nodes if n.type == "Row" and tree.subtree_texts(n)[0] == "合成收藏42"]
    assert len(rows) == 2 and all(n.visible and tree.visible_lineage(n) for n in rows)
    assert len([n for n in rows if tree.shown(n)]) == 1
    shadow = next(n for n in rows if not tree.shown(n))
    assert (intersect(shadow.bounds, tree.window_rect()) is None) == (geometry == "offscreen")
