"""Executable spot-name scope (#111 same-park favorite identity binding). Synthetic tests only."""
from copy import deepcopy

import pytest

from conftest import WORLDS
from test_condition_runner import COMMON_RULE
from test_search_name_row import _oracle
from vpdtest.cases import lint, promote
from vpdtest.devices.replay import load_world
from vpdtest.execute import _measure
from vpdtest.knowledge import KnowledgeStore
from vpdtest.locate import resolve
from vpdtest.readrefs import qualify
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.util import write_json
from vpdtest.verdict import case_verdict, evaluate_assertion

CASE = "TC-QK-230-SPOT-NAME"


def _vpd_home_screen(*, park="合成园区A", spot="7号车位"):
    return {
        "root": {
            "t": "Column", "b": [0, 0, 1320, 2848],
            "children": [{
                "t": "Stack", "id": "VPD_HOME_VIEW", "b": [0, 0, 1320, 2848],
                "children": [
                    {
                        "t": "Row", "b": [54, 1460, 1266, 1530],
                        "children": [
                            {"t": "Text", "text": park, "b": [54, 1463, 324, 1526]},
                            {"t": "Text", "text": "泊车代驾", "b": [337, 1464, 525, 1525]},
                        ],
                    },
                    {"t": "Row", "b": [100, 1620, 900, 1720], "click": True, "go": "qk_search",
                     "children": [{"t": "Text", "text": "搜索", "b": [216, 1639, 338, 1710]}]},
                    {
                        "t": "Grid", "b": [54, 2400, 1266, 2600],
                        "children": [{
                            "t": "GridItem", "b": [67, 2428, 660, 2549],
                            "children": [
                                {"t": "Row", "click": True, "b": [94, 2457, 579, 2521],
                                 "children": [{"t": "Text", "text": spot, "b": [148, 2457, 317, 2521]}]},
                                {"t": "Column", "click": True, "go": "qk_fav_panel", "b": [579, 2448, 660, 2529],
                                 "children": [{"t": "Text", "text": ":", "b": [612, 2453, 628, 2524]}]},
                            ],
                        }],
                    },
                ],
            }],
        }
    }


def _fav_panel_screen(*, spot="7号车位", floor="B1", code="B1-B17车位"):
    return {
        "root": {
            "t": "Column", "b": [0, 0, 1320, 2848],
            "children": [
                {
                    "t": "Row", "b": [54, 920, 1266, 1100],
                    "children": [
                        {
                            "t": "Column", "b": [114, 937, 600, 1080],
                            "children": [
                                {"t": "Text", "text": spot, "b": [114, 937, 307, 1008]},
                                {"t": "Row", "b": [114, 1020, 540, 1080], "children": [
                                    {"t": "Text", "text": floor, "b": [221, 1024, 283, 1078]},
                                    {"t": "Text", "text": code, "b": [296, 1024, 532, 1078]},
                                ]},
                            ],
                        },
                        {"t": "Column", "b": [1100, 1000, 1240, 1080], "children": [
                            {"t": "Text", "text": "编辑", "b": [1125, 1019, 1206, 1066]},
                        ]},
                    ],
                },
                {"t": "Text", "text": "添加标签", "b": [100, 1300, 300, 1360]},
            ],
        },
    }


def _search_row(name, floor_text, code_text, *, poi_id="searchPoiSpot_1", y0=745):
    return {
        "t": "__Common__", "id": poi_id, "click": True, "b": [165, y0, 1266, y0 + 227],
        "children": [{
            "t": "Row", "b": [165, y0, 1266, y0 + 227],
            "children": [
                {"t": "Image", "b": [165, y0 + 50, 240, y0 + 130]},
                {
                    "t": "Column", "b": [266, y0 + 20, 1266, y0 + 210],
                    "children": [{
                        "t": "Stack", "b": [266, y0 + 20, 1266, y0 + 210],
                        "children": [{
                            "t": "Column", "b": [266, y0 + 20, 1266, y0 + 210],
                            "children": [{
                                "t": "Column", "b": [266, y0 + 20, 1266, y0 + 210],
                                "children": [{
                                    "t": "Row", "b": [266, y0 + 20, 1266, y0 + 210],
                                    "children": [
                                        {"t": "Column", "b": [266, y0 + 40, 900, y0 + 180], "children": [
                                            {"t": "__Common__", "b": [266, y0 + 40, 600, y0 + 110],
                                             "children": [{"t": "Text", "text": name, "b": [266, y0 + 54, 435, y0 + 109]}]},
                                            {"t": "Row", "b": [266, y0 + 115, 700, y0 + 175], "children": [
                                                {"t": "Text", "text": floor_text, "b": [386, y0 + 122, 476, y0 + 169]},
                                                {"t": "Text", "text": code_text, "b": [515, y0 + 122, 587, y0 + 169]},
                                            ]},
                                        ]},
                                        {"t": "Column", "click": True, "b": [1150, y0 + 120, 1266, y0 + 190], "children": [
                                            {"t": "Text", "text": "路线", "b": [1185, y0 + 130, 1266, y0 + 184]},
                                        ]},
                                    ],
                                }],
                            }],
                        }],
                    }],
                },
            ],
        }],
    }


def _search_screen(*, park="合成园区A", query_bind="search_q", name="7号车位", floor="B1层", code="B17",
                   extra_rows=(), include_route=True, include_current_park=True):
    row = _search_row(name, floor, code)
    if not include_route:
        row["children"][0]["children"][1]["children"][0]["children"][0]["children"][0]["children"][0]["children"].pop()
    header_col_children = [
        {"t": "__Common__", "b": [165, 520, 1151, 595], "children": [
            {"t": "Text", "text": park, "b": [165, 530, 500, 590]},
        ]},
    ]
    if include_current_park:
        header_col_children.append(
            {"t": "Row", "b": [165, 600, 339, 660], "children": [
                {"t": "Text", "text": "当前园区", "b": [165, 600, 339, 660]},
            ]}
        )
    group_y2 = max([972] + [r["b"][3] for r in extra_rows])
    outer_y2 = max(1800, group_y2)
    group = {
        "t": "Column", "b": [152, 509, 1266, group_y2],
        "children": [
            {"t": "Column", "b": [152, 509, 1266, 740], "children": [
                {"t": "Row", "b": [165, 520, 1266, 740], "children": [
                    {"t": "Column", "b": [165, 520, 1151, 740], "children": header_col_children},
                ]},
            ]},
            row,
            *extra_rows,
        ],
    }
    return {
        "root": {
            "t": "RelativeContainer", "id": "SearchPoiPage", "b": [0, 0, 1320, 2848],
            "children": [
                {"t": "Search", "id": "search_input", "b": [310, 158, 1037, 293], "children": [
                    {"t": "SearchField", "id": "__SearchField__search_input", "click": True,
                     "bind": query_bind, "text_bound": query_bind, "text": "", "b": [310, 158, 1037, 293]},
                ]},
                {"t": "Column", "id": "search_list", "b": [0, 509, 1320, 2000], "children": [{
                    "t": "List", "b": [0, 509, 1320, 2000], "children": [{
                        "t": "__Common__", "b": [0, 509, 1320, outer_y2], "children": [{
                            "t": "Row", "b": [0, 509, 1320, outer_y2], "children": [{
                                "t": "Stack", "b": [0, 509, 1320, outer_y2], "children": [group],
                            }],
                        }],
                    }],
                }]},
            ],
        }
    }


def _world(tmp_path, *, park_home="合成园区A", park_search="合成园区A", spot="7号车位",
           floor="B1", code_full="B1-B17车位", result_floor="B1层", result_code="B17",
           extra_rows=(), include_route=True, include_current_park=True):
    fixture = load_world(WORLDS / "qiankun_hm_ready.json")
    fixture.pop("_path", None)
    fixture["screens"]["qk_vpd_home"] = _vpd_home_screen(park=park_home, spot=spot)
    fixture["screens"]["qk_fav_panel"] = _fav_panel_screen(spot=spot, floor=floor, code=code_full)
    fixture["screens"]["qk_search"] = _search_screen(
        park=park_search, name=spot, floor=result_floor, code=result_code,
        extra_rows=extra_rows, include_route=include_route, include_current_park=include_current_park,
    )
    path = tmp_path / "spot-name-v2-world.json"
    write_json(path, fixture)
    return "replay:" + str(path)


def test_spot_scope_uses_read_bound_identity_and_passes_lint(ws):
    case = ws.load_case(CASE)
    assert case["version"] == 2 and case["status"] == "candidate"
    assert case["test_data"] == {} and case["test_data_none"] is True
    app, knowledge = ws.app(case["app"]), KnowledgeStore(ws.root)
    assert lint(case, app, knowledge, ws.policy()) == []
    steps = {s["id"]: s for s in case["steps"]}
    for rid in ("R_PARK", "R_NAME", "R_FLOOR", "R_CODE"):
        assert steps[rid]["action"] == "read" and steps[rid]["read_scope"] == "self"
    assert steps["S3"]["action"] == "input" and steps["S3"]["text"] == "${read:R_NAME}"
    assert steps["S3"]["clear"] is steps["S3"]["require_empty_after_clear"] is True
    assert all(not a.get("advisory") and a["oracle_refs"] == ["K-QK-TC-059"] for a in case["assertions"])
    assert len(case["assertions"]) == 14
    with pytest.raises(PermissionError, match="重放不足"):
        promote(ws, CASE, "replay_verified", knowledge, dry_run=True)


def test_normal_runner_binds_home_panel_reads_to_same_park_search_row(ws, tmp_path):
    spec = _world(tmp_path)
    run = run_case(ws, CASE, device_spec=spec, propose=False)
    assert all(s["outcome"] == "passed" for s in run["steps"]), run["steps"]
    assert all(s["outcome"] == "passed" for s in run["cleanup"]), run["cleanup"]
    assert run["verdict"]["verdict"] == "passed"
    assert run["verdict"]["product_verdict"] == "passed"
    assert all(a["result"] == "pass" and a["verified"] for a in run["assertions"])


def test_cross_park_mismatch_fails(ws, tmp_path):
    spec = _world(tmp_path, park_home="合成园区A", park_search="合成园区B")
    run = run_case(ws, CASE, device_spec=spec, propose=False)
    by_id = {a["id"]: a for a in run["assertions"]}
    assert by_id["first-park"]["result"] == "fail"
    assert by_id["second-park"]["result"] == "fail"
    assert run["verdict"]["verdict"] == "failed"


@pytest.mark.parametrize("include_current_park", [True, False])
def test_cross_park_collision_when_spot_equals_home_park_fails(ws, tmp_path, include_current_park):
    spec = _world(
        tmp_path, park_home="合成园区A", park_search="合成园区B",
        spot="合成园区A", include_current_park=include_current_park,
    )
    run = run_case(ws, CASE, device_spec=spec, propose=False)
    by_id = {a["id"]: a for a in run["assertions"]}
    assert by_id["first-park"]["result"] == "fail"
    assert by_id["second-park"]["result"] == "fail"
    assert run["verdict"]["verdict"] == "failed"


def test_partially_visible_group_with_visible_header_and_row_passes(ws, tmp_path):
    overflow = _search_row("另一车位", "B1层", "B17", poi_id="searchPoiSpot_2", y0=2750)
    spec = _world(tmp_path, extra_rows=(overflow,))
    run = run_case(ws, CASE, device_spec=spec, propose=False)
    by_id = {a["id"]: a for a in run["assertions"]}
    assert by_id["first-park"]["result"] == "pass"
    assert by_id["second-park"]["result"] == "pass"
    assert run["verdict"]["verdict"] == "passed"
    assert run["verdict"]["product_verdict"] == "passed"


def test_neighbour_row_borrowing_code_fails(ws, tmp_path):
    other = _search_row("另一车位", "B1层", "B17", poi_id="searchPoiSpot_2", y0=980)
    spec = _world(tmp_path, result_code="B99", extra_rows=(other,))
    run = run_case(ws, CASE, device_spec=spec, propose=False)
    by_id = {a["id"]: a for a in run["assertions"]}
    assert by_id["first-row-code"]["result"] == "fail"
    assert by_id["second-row-code"]["result"] == "fail"
    assert run["verdict"]["verdict"] == "failed"


@pytest.mark.parametrize("bad_floor,include_route,expected_id", [
    ("B2层", True, "first-row-floor"),
    ("B1层", False, "first-row-route"),
])
def test_floor_or_route_mismatch_fails(ws, tmp_path, bad_floor, include_route, expected_id):
    spec = _world(tmp_path, result_floor=bad_floor, include_route=include_route)
    run = run_case(ws, CASE, device_spec=spec, propose=False)
    by_id = {a["id"]: a for a in run["assertions"]}
    assert by_id[expected_id]["result"] == "fail"
    assert run["verdict"]["verdict"] == "failed"


def test_duplicate_named_row_is_undeterminable_and_unconfirmed_oracle_pending(ws, tmp_path):
    dup = _search_row("7号车位", "B1层", "B17", poi_id="searchPoiSpot_2", y0=980)
    spec = _world(tmp_path, extra_rows=(dup,))
    run = run_case(ws, CASE, device_spec=spec, propose=False)
    by_id = {a["id"]: a for a in run["assertions"]}
    assert by_id["first-row-name"]["result"] == "undeterminable"

    # Synthetic unconfirmed oracle check on the exact assertion definitions.
    app = ws.app("qiankun_hm")
    case = ws.load_case(CASE)
    home_tree = UiTree(load_world(WORLDS / "qiankun_hm_ready.json"))  # placeholder overridden below
    del home_tree
    search_raw = _search_screen(park="合成园区A", query_bind="search_q", name="7号车位", floor="B1层", code="B17")["root"]
    # Expand compact replay tree into full attributes.
    from vpdtest.devices.replay import expand
    expanded = expand(search_raw, app.bundle)
    expanded["children"][0]["children"][0]["attributes"]["text"] = "7号车位"
    tree = UiTree(expanded)

    def _read_step(sid, val):
        t = UiTree({"attributes": {"type": "Column", "bounds": "[0,0][300,100]"}, "children": [
            {"attributes": {"type": "Text", "id": "s", "text": val, "bounds": "[10,10][290,90]"}, "children": []}]})
        loc = resolve("s", {"strategies": [{"kind": "id", "value": "s"}]}, t, None, app.dir)
        m = _measure(t, loc, {"id": sid, "read_scope": "self"})
        return {"step_id": sid, "action": "read", "outcome": "passed", "final_obs": "src", "measurement": m,
                "read_binding": qualify(t, loc, {"id": sid, "read_scope": "self"}, {"obs_id": "src"}, m)}

    steps = {
        "R_PARK": _read_step("R_PARK", "合成园区A"), "R_NAME": _read_step("R_NAME", "7号车位"),
        "R_FLOOR": _read_step("R_FLOOR", "B1"), "R_CODE": _read_step("R_CODE", "B17"),
        "S4": {"step_id": "S4", "action": "wait", "outcome": "passed", "final_obs": "res"},
        "S5": {"step_id": "S5", "action": "wait", "outcome": "passed", "final_obs": "res"},
    }
    obs = {"obs_id": "res", "fresh": True, "tree": {"status": "ok"}, "layout": {"pane_count": 1},
           "page": app.states.identify(tree, app.bundle, True, {"pane_count": 1})}
    oracle = _oracle(tmp_path, confirmed=False)
    results = [evaluate_assertion({**a, "oracle_refs": [COMMON_RULE]}, steps, lambda _: (obs, tree), oracle, app)
               for a in case["assertions"]]
    assert all(r["result"] == "pending" and not r["verified"] for r in results)
    assert case_verdict({"ok": True}, {"ok": True}, list(steps.values()), results, oracle)["verdict"] == "pending"
