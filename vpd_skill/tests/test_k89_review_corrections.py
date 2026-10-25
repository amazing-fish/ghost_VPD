"""Counterexamples for the two human-requested corrections; synthetic UI only."""
from copy import deepcopy

import pytest

from vpdtest.knowledge import KnowledgeStore
from vpdtest.tree import UiTree
from vpdtest.verdict import evaluate_assertion


def node(kind, text="", children=(), **attrs):
    return {"attributes": {"type": kind, "text": text, "bounds": "[0,0][1000,300]", **attrs},
            "children": list(children)}


def row(name, with_icon=True, *, icon_in_count=False):
    marker = node("Row", children=([node("Image")] if with_icon else []) + [node("Text", "当前位置")])
    return node("ListItem", children=[node("Row", id="area_name", children=[node("Text", name), marker]),
                                      node("Row", children=[node("Image")] if icon_in_count else [])])


@pytest.mark.parametrize("variant,expected", [("current", "found"), ("parking_p_only", "not_found"),
                                              ("other_park_only", "not_found"), ("duplicate", "ambiguous")])
def test_current_location_icon_must_belong_to_current_parking_name_row(ws, variant, expected):
    case = ws.load_case("TC-QK-248")
    assertion = next(a for a in case["assertions"] if a["id"] == "A_CURRENT_ICON")
    assert assertion["type"] == "target_present"
    rows = [row("由度金闽园地下停车场", variant in {"current", "duplicate"}, icon_in_count=True),
            row("华为练秋湖研发中心")]
    if variant == "duplicate":
        rows.append(deepcopy(rows[0]))
    tree = UiTree(node("Column", children=rows))
    app = ws.app(case["app"])
    observed = {"obs_id": "fixture", "fresh": True, "tree": {"status": "ok", "path": "synthetic.json"}}
    result = evaluate_assertion(assertion, {"S_CURRENT_ICON": {"outcome": "passed", "final_obs": "fixture"}},
                                lambda _: (observed, tree), KnowledgeStore(ws.root), app)
    # Use the actual assertion path; pre-substituting in the test would hide an
    # unsupported ${var:...} reference in an assertion's inline target.
    assert result["evidence"]["locate"]["status"] == expected


def test_search_tag_chain_cannot_pass_from_old_static_label_presence(ws):
    case = ws.load_case("TC-QK-242")
    assert not any(a.get("type") == "text_present" and a.get("text") in {"电梯口", "充电桩"}
                   for a in case["assertions"])
    knowledge = KnowledgeStore(ws.root)
    observed = {"obs_id": "fixture", "fresh": True, "tree": {"status": "ok"},
                "screenshot": {"status": "ok", "path": "synthetic.png"}}
    tree = UiTree(node("Column", children=[node("Text", "电梯口"), node("Text", "充电桩")]))
    for assertion in case["assertions"]:
        evaluated = evaluate_assertion(assertion, {}, lambda _: (observed, tree), knowledge, ws.app(case["app"]))
        assert evaluated["result"] == "not_run"
