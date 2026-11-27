"""Cross-park regression tests for TC-QK-240 & TC-QK-244 (Issue #316). Synthetic replay only."""
from __future__ import annotations

import pytest

from vpdtest.cases import lint
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import write_json


def _k89_world(tmp_path, *, home_park: str, support_parks: list[str], app_version: str = "2.0.4.100"):
    car_tab = {
        "root": {
            "t": "Column", "b": [0, 0, 1320, 2848],
            "children": [
                {"t": "Image", "id": "vehicleListBgImg", "b": [0, 0, 1320, 160]},
                {"t": "Column", "b": [0, 160, 1320, 320], "children": [
                    {"t": "Row", "b": [0, 160, 1320, 260], "children": [
                        {"t": "Row", "b": [40, 180, 420, 260], "children": [
                            {"t": "Text", "text": "问界M9", "click": True, "go": "veh_list", "b": [40, 180, 420, 260]},
                        ]},
                    ]},
                ]},
                {"t": "Row", "b": [0, 2600, 1320, 2760], "children": [
                    {"t": "Text", "text": "爱车", "sel": True, "click": True, "b": [550, 2620, 720, 2720]},
                    {"t": "Text", "text": "ADS", "click": True, "go": "vpd_home", "b": [320, 2620, 460, 2720]},
                ]},
                {"t": "Text", "text": "车锁", "b": [100, 1500, 220, 1560]},
            ],
        }
    }
    veh_list = {
        "root": {
            "t": "Column", "b": [0, 0, 1320, 2848],
            "children": [
                {"t": "Text", "text": "我的车辆", "b": [40, 120, 320, 180]},
                {"t": "Row", "click": True, "go": "car_tab", "b": [40, 300, 1200, 420],
                 "children": [{"t": "Text", "text": "车架号 000228(后6位)", "click": True, "go": "car_tab", "b": [60, 320, 700, 390]}]},
            ],
        }
    }
    vpd_home = {
        "root": {
            "t": "Column", "b": [0, 0, 1320, 2848],
            "children": [{
                "t": "Stack", "id": "VPD_HOME_VIEW", "b": [0, 0, 1320, 2848],
                "children": [
                    {
                        "t": "Row", "b": [54, 1460, 1266, 1530],
                        "children": [
                            {"t": "Text", "text": home_park, "b": [54, 1463, 460, 1526]},
                            {"t": "Text", "text": "泊车代驾", "b": [480, 1464, 680, 1525]},
                        ],
                    },
                    {
                        "t": "Row", "id": "support_btn", "b": [960, 300, 1120, 420],
                        "children": [{
                            "t": "Column", "click": True, "go": "support_areas", "b": [960, 300, 1120, 420],
                            "children": [
                                {"t": "Text", "text": "支持", "b": [970, 310, 1080, 355]},
                                {"t": "Text", "text": "区域", "b": [970, 360, 1080, 405]},
                            ],
                        }],
                    },
                    {"t": "Text", "text": "搜索", "b": [216, 1639, 338, 1710]},
                ],
            }],
        }
    }
    support_children = [
        {"t": "Text", "text": "支持区域", "b": [40, 120, 320, 180]},
        {"t": "Text", "text": "上海", "b": [40, 220, 200, 280]},
        {"t": "Text", "text": "当前位置", "b": [800, 340, 1000, 400]},
    ]
    for i, park in enumerate(support_parks):
        support_children.append({"t": "Text", "text": park, "b": [40, 340 + i * 90, 620, 400 + i * 90]})
    support_areas = {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": support_children}}
    data = {
        "bundle": "com.yinwang.qiankunapp.hm",
        "app_version": app_version,
        "screen": [1320, 2848],
        "display": {"density": 3.375, "device_form": "bar", "fold_status": "unknown"},
        "initial": {"installed": True, "foreground": "com.ohos.sceneboard"},
        "launch_screen": "car_tab",
        "screens": {
            "car_tab": car_tab,
            "veh_list": veh_list,
            "vpd_home": vpd_home,
            "support_areas": support_areas,
        },
    }
    path = tmp_path / f"k89-{home_park}-{len(support_parks)}.json"
    write_json(path, data)
    return "replay:" + str(path)


@pytest.mark.parametrize("case_id", ["TC-QK-240", "TC-QK-244"])
def test_k89_240_244_lint_clean_without_hardcoded_park_test_data(ws, case_id):
    case = ws.load_case(case_id)
    assert case["version"] == 5 and case["test_data"] == {} and case["test_data_none"] is True
    assert lint(case, ws.app(case["app"]), KnowledgeStore(ws.root), ws.policy()) == []


@pytest.mark.parametrize("park", ["金桥出口加工区", "由度金闽园", "华为练秋湖研发中心"])
def test_tc_qk_240_passes_across_different_parks(ws, tmp_path, park):
    spec = _k89_world(tmp_path, home_park=park, support_parks=[park])
    run = run_case(ws, "TC-QK-240", device_spec=spec, authorize=["vehicle_selection"], propose=False)
    assert run["verdict"]["verdict"] == "passed", run
    assert all(a["result"] == "pass" for a in run["assertions"])


@pytest.mark.parametrize("park", ["金桥出口加工区", "由度金闽园"])
def test_tc_qk_244_binds_support_area_to_home_park_across_parks(ws, tmp_path, park):
    spec = _k89_world(tmp_path, home_park=park, support_parks=[park, "星光安朴悦庭公寓"])
    run = run_case(ws, "TC-QK-244", device_spec=spec, authorize=["vehicle_selection"], propose=False)
    assert run["verdict"]["verdict"] == "passed", run
    assert all(a["result"] == "pass" for a in run["assertions"])


def test_tc_qk_244_a3_rejects_missing_or_substring_park_name(ws, tmp_path):
    spec = _k89_world(tmp_path, home_park="金桥出口加工区", support_parks=["金桥出口加工区二期", "星光安朴悦庭公寓"])
    run = run_case(ws, "TC-QK-244", device_spec=spec, authorize=["vehicle_selection"], propose=False)
    by_id = {a["id"]: a for a in run["assertions"]}
    assert by_id["A3"]["result"] == "fail"
    assert run["verdict"]["verdict"] == "failed"
