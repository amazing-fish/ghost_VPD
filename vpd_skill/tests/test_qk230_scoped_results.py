"""QK-230 production case wiring against a synthetic search tree, not device evidence."""
from copy import deepcopy

import pytest

from test_search_name_row import _evaluate, _locate, _oracle, _raw, _tree
from vpdtest.tree import UiTree


def _replace(raw, mapping):
    attrs = raw["attributes"]
    attrs["text"] = mapping.get(attrs.get("text"), attrs.get("text", ""))
    for child in raw.get("children", []):
        _replace(child, mapping)


FIXTURE = {"fav_spot": "1号车位", "fav_waypoint": "合成路点", "fav_spot_number": "I1", "fav_waypoint_number": "J9"}


def _assertions(case, which, rule):
    from vpdtest.runtime_data import bind_assertion
    case = {**case, "assertions": [bind_assertion(a, FIXTURE) for a in case["assertions"]]}
    ids = [f"A{which}-state", f"A{which}-query", f"A{which}"]
    return [{**next(a for a in case["assertions"] if a["id"] == aid),
             "at": "S3", "oracle_refs": [rule]} for aid in ids]


def test_both_production_queries_are_bound_to_their_search_observation(ws):
    case = ws.load_case("TC-QK-230")
    steps = {step["id"]: step for step in case["steps"]}
    for i, data, input_id, at in [(1, "fav_spot", "S6", "S7"), (2, "fav_waypoint", "S11", "S12")]:
        query = steps[input_id]
        assert query["action"] == "input" and query["target"] == "qk_search_input"
        assert query["text"] == "${var:" + data + "}"
        assert query["clear"] is True and query["require_empty_after_clear"] is True
        assertions = {a["id"]: a for a in case["assertions"]}
        assert assertions[f"A{i}-state"]["state"] == "qk_spot_search"
        assert assertions[f"A{i}-query"]["expected"] == "${var:" + data + "}"
        assert assertions[f"A{i}-query"]["target"] == "qk_search_input"
        assert assertions[f"A{i}"]["within"] == "qk_search_name_result_row"
        assert all(assertions[aid]["at"] == at for aid in (f"A{i}-state", f"A{i}-query", f"A{i}"))


@pytest.mark.parametrize("which", [1, 2])
def test_production_assertion_cannot_borrow_a_neighbouring_row_number(ws, tmp_path, which):
    from test_condition_runner import COMMON_RULE
    app, tree, case = ws.app("qiankun_hm"), _tree(), ws.load_case("TC-QK-230")
    assertions = _assertions(case, which, COMMON_RULE)
    query, number = assertions[1]["expected"], assertions[2]["text"]
    row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
    other = deepcopy(_raw(tree, row))
    _replace(_raw(tree, row), {"1号车位": query, "I1": "not-the-number"})
    _replace(other, {"1号车位": "different entity", "I1": number})
    _raw(tree, tree.nodes[row.parent])["children"].append(other)
    # The query and row name vary for the second case in the same snapshot.
    if query != "1号车位":
        field = next(n for n in tree.nodes if n.attrs.get("id") == "__SearchField__search_input")
        _raw(tree, field)["attributes"]["text"] = query
    tree = UiTree(tree.raw)
    oracle = _oracle(tmp_path)
    legacy = {k: v for k, v in assertions[2].items() if k != "within"}
    assert _evaluate(app, tree, oracle, assertions=[legacy])[0]["result"] == "pass"
    result = _evaluate(app, tree, oracle, assertions=assertions)
    assert [r["result"] for r in result] == ["pass", "pass", "fail"]


def test_unconfirmed_oracle_still_does_not_promote(ws, tmp_path):
    from test_condition_runner import COMMON_RULE
    app, tree, case = ws.app("qiankun_hm"), _tree(), ws.load_case("TC-QK-230")
    assertions = _assertions(case, 1, COMMON_RULE)
    assert all(r["result"] == "pending" and not r["verified"]
               for r in _evaluate(app, tree, _oracle(tmp_path, confirmed=False), assertions=assertions))


def test_correct_row_and_query_can_pass_the_synthetic_contract(ws, tmp_path):
    from test_condition_runner import COMMON_RULE
    app, tree, case = ws.app("qiankun_hm"), _tree(), ws.load_case("TC-QK-230")
    assertions = _assertions(case, 1, COMMON_RULE)
    row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
    _replace(_raw(tree, row), {"I1": assertions[2]["text"]})
    results = _evaluate(app, UiTree(tree.raw), _oracle(tmp_path), assertions=assertions)
    assert [r["result"] for r in results] == ["pass", "pass", "pass"]


def test_wrong_actual_query_cannot_satisfy_the_complete_group(ws, tmp_path):
    from test_condition_runner import COMMON_RULE
    app, tree, case = ws.app("qiankun_hm"), _tree(), ws.load_case("TC-QK-230")
    assertions = _assertions(case, 1, COMMON_RULE)
    row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
    _replace(_raw(tree, row), {"1号车位": "different query", "I1": assertions[2]["text"]})
    field = next(n for n in tree.nodes if n.attrs.get("id") == "__SearchField__search_input")
    _raw(tree, field)["attributes"]["text"] = "different query"
    results = _evaluate(app, UiTree(tree.raw), _oracle(tmp_path), assertions=assertions)
    assert results[1]["result"] == "fail"
    assert not all(r["result"] == "pass" for r in results)


@pytest.mark.parametrize("which", [1, 2])
@pytest.mark.parametrize("impostor", ["description", "child"])
def test_auxiliary_text_cannot_impersonate_actual_query(ws, tmp_path, which, impostor):
    from test_condition_runner import COMMON_RULE
    app, tree, case = ws.app("qiankun_hm"), _tree(), ws.load_case("TC-QK-230")
    assertions = _assertions(case, which, COMMON_RULE)
    row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
    _replace(_raw(tree, row), {"1号车位": "different query", "I1": assertions[2]["text"]})
    field = next(n for n in tree.nodes if n.attrs.get("id") == "__SearchField__search_input")
    attrs = _raw(tree, field)["attributes"]
    attrs.update(text="different query", description="")
    if impostor == "description":
        attrs["description"] = assertions[1]["expected"]
    else:
        _raw(tree, field).setdefault("children", []).append({"attributes": {
            "type": "Text", "text": assertions[1]["expected"], "bounds": attrs["bounds"]}, "children": []})
    results = _evaluate(app, UiTree(tree.raw), _oracle(tmp_path), assertions=assertions)
    assert results[1]["result"] != "pass", results
    assert not all(r["result"] == "pass" for r in results)

