"""A K89 test must open the picker before K89 is the selected vehicle.

The header shape is derived from the real 2026-10-26 failed S4 observation;
these synthetic trees contain no account data and do not replace device runs.
"""
import pytest

from vpdtest.locate import resolve
from vpdtest.tree import UiTree


def _node(kind, children=(), **attributes):
    return {"attributes": {"type": kind, "bounds": "[0,0][1320,2848]", **attributes},
            "children": list(children)}


def _header(name="Different vehicle", *, clickable="true"):
    return [
        _node("Image", id="vehicleListBgImg"),
        _node("Column", [
            _node("Row", [
                _node("Row", [_node("Text", text=name, clickable=clickable,
                                    bounds="[54,186][494,265]"), _node("Image")]),
                _node("Row", [_node("Text", text="99+")]),
            ]),
            _node("Column", [_node("Text", text="问界K89", clickable="true")]),
        ]),
    ]


@pytest.mark.parametrize("cid", [f"TC-QK-{n}" for n in range(240, 250)])
@pytest.mark.parametrize("current_name", ["赛力斯M9 F2A", "问界K89"])
def test_k89_case_opens_header_without_assuming_target_is_selected(ws, cid, current_name):
    app = ws.app("qiankun_hm")
    case = ws.load_case(cid)
    step = next(s for s in case["steps"] if s["id"] == "S4")
    if step["action"] == "flow":
        step = next(s for s in app.flows[step["flow"]]["steps"] if s.get("target") == "qk_car_select")
    target = step["target"] if isinstance(step["target"], dict) else app.targets[step["target"]]
    tree = UiTree(_node("Stack", _header(current_name)))
    found = resolve("case-current-vehicle-entry", target, tree, None, app.dir)
    assert found["status"] == "found"
    assert found["attempts"][0]["node"]["text"] == current_name
    assert found["bounds"] == [54, 186, 494, 265]


def test_shared_picker_rejects_duplicate_header_candidates(ws):
    app = ws.app("qiankun_hm")
    tree = UiTree(_node("Stack", _header("Car A") + _header("Car B")))
    assert resolve("qk_car_select", app.targets["qk_car_select"], tree, None, app.dir)["status"] == "ambiguous"


def _legacy_page(with_current_column=False):
    header = _node("Row", [_node("Row", [_node("Text", text="Legacy vehicle", clickable="true")],
                                bounds="[54,186][494,265]")])
    children = [header] + (_header("Current vehicle") if with_current_column else [])
    tree = _node("Stack", children)
    for kind in reversed(["root", "Navigation", "NavigationContent", "NavDestination",
                          "NavDestinationContent", "Navigation", "NavBar", "NavBarContent",
                          "Tabs", "Swiper", "TabContent"]):
        tree = _node(kind, [tree])
    return UiTree(tree)


def test_existing_legacy_layout_still_opens_its_header(ws):
    app = ws.app("qiankun_hm")
    found = resolve("qk_car_select", app.targets["qk_car_select"], _legacy_page(), None, app.dir)
    assert found["status"] == "found"
    assert found["bounds"] == [54, 186, 494, 265]


def test_legacy_and_current_layouts_cannot_hide_each_others_ambiguity(ws):
    app = ws.app("qiankun_hm")
    found = resolve("qk_car_select", app.targets["qk_car_select"], _legacy_page(True), None, app.dir)
    assert found["status"] == "ambiguous"


@pytest.mark.parametrize("variant", ["not_clickable", "body_text_only", "missing_background"])
def test_shared_picker_does_not_use_unrelated_or_inactive_text(ws, variant):
    app = ws.app("qiankun_hm")
    nodes = _header(clickable="false" if variant == "not_clickable" else "true")
    if variant == "body_text_only":
        nodes[1]["children"][0]["children"][0]["children"] = [_node("Image")]
    elif variant == "missing_background":
        nodes = nodes[1:]
    tree = UiTree(_node("Stack", nodes))
    assert resolve("qk_car_select", app.targets["qk_car_select"], tree, None, app.dir)["status"] == "not_found"
