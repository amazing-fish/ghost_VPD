"""#107: exact observed home labels share one locator ambiguity boundary.

All trees and Replay worlds are synthetic; they do not confirm product rules or
replace current-device calibration.
"""
import pytest

from vpdtest.locate import resolve
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.util import write_json

HOME_LABELS = ("搜索车位号设为目的地", "搜索车位号设为起点")
ENTRY_TARGETS = ("qk_search_entry", "qk_spot_input")


def _tree(*texts, hidden=False, offscreen=False):
    return UiTree({"attributes": {"type": "Column", "bounds": "[0,0][1320,2848]"}, "children": [
        {"attributes": {"type": "Text", "text": text, "clickable": "true",
                        "visible": "false" if hidden else "true",
                        "bounds": ("[20,2900][1300,3000]" if offscreen else
                                   f"[20,{100 + i * 120}][1300,{180 + i * 120}]")}}
        for i, text in enumerate(texts)]})


@pytest.mark.parametrize("label", HOME_LABELS)
@pytest.mark.parametrize("target_id", ENTRY_TARGETS)
def test_known_home_label_is_identified_and_located(ws, label, target_id):
    app = ws.app("qiankun_hm")
    tree = _tree(label)
    page = app.states.identify(tree, app.bundle, True)
    assert page["state"] == "qk_vpd_home"
    found = resolve(target_id, app.targets[target_id], tree, None, app.dir)
    assert found["status"] == "found"
    assert found["attempts"][-1]["node"]["text"] == label


@pytest.mark.parametrize("target_id", ENTRY_TARGETS)
@pytest.mark.parametrize("labels", [HOME_LABELS, (HOME_LABELS[0], HOME_LABELS[0])])
def test_known_labels_in_distinct_nodes_remain_ambiguous(ws, target_id, labels):
    app = ws.app("qiankun_hm")
    found = resolve(target_id, app.targets[target_id], _tree(*labels), None, app.dir)
    assert found["status"] == "ambiguous"


@pytest.mark.parametrize("target_id", ENTRY_TARGETS)
@pytest.mark.parametrize("label", HOME_LABELS)
def test_existing_exact_accessibility_description_matching_is_preserved(ws, target_id, label):
    app = ws.app("qiankun_hm")
    tree = UiTree({"attributes": {"type": "Column", "bounds": "[0,0][1320,2848]"}, "children": [
        {"attributes": {"type": "Button", "description": label,
                        "bounds": "[20,100][1300,180]", "clickable": "true"}}]})
    assert resolve(target_id, app.targets[target_id], tree, None, app.dir)["status"] == "found"


def test_current_and_legacy_labels_do_not_hide_ambiguity(ws):
    app = ws.app("qiankun_hm")
    tree = _tree(HOME_LABELS[0], "输入车位号设为目的地")
    assert resolve("qk_spot_input", app.targets["qk_spot_input"], tree, None, app.dir)["status"] == "ambiguous"


@pytest.mark.parametrize("label", ["搜索车位号设为", "搜索车位号设为终点", "请搜索车位号设为起点",
                                  "搜索车位号设为起点（说明）"])
def test_prefix_or_unknown_label_does_not_identify_or_locate_home(ws, label):
    app = ws.app("qiankun_hm")
    tree = _tree(label)
    assert app.states.identify(tree, app.bundle, True)["state"] == "unknown"
    for target_id in ENTRY_TARGETS:
        assert resolve(target_id, app.targets[target_id], tree, None, app.dir)["status"] == "not_found"


@pytest.mark.parametrize("label", HOME_LABELS)
@pytest.mark.parametrize("extra", ["停车场信息加载失败，请重试", "路线规划中，请稍后", "开始", "暂停", "继续"])
def test_known_failure_planning_and_motion_pages_do_not_become_home(ws, label, extra):
    app = ws.app("qiankun_hm")
    page = app.states.identify(_tree(label, extra), app.bundle, True)
    assert not app.states.is_state(page, "qk_vpd_home")


@pytest.mark.parametrize("visibility", [{"hidden": True}, {"offscreen": True}])
@pytest.mark.parametrize("label", HOME_LABELS)
def test_hidden_or_outside_window_labels_are_not_actionable(ws, label, visibility):
    app = ws.app("qiankun_hm")
    tree = _tree(label, **visibility)
    assert not app.states.is_state(app.states.identify(tree, app.bundle, True), "qk_vpd_home")
    for target_id in ENTRY_TARGETS:
        assert resolve(target_id, app.targets[target_id], tree, None, app.dir)["status"] == "not_found"


def test_search_and_loading_pages_do_not_silently_count_as_home(ws):
    app = ws.app("qiankun_hm")
    # A placeholder alone is no longer a positive search signature.
    assert app.states.identify(_tree("输入目的地或车位号"), app.bundle, True)["state"] == "unknown"
    page = app.states.identify(_tree(HOME_LABELS[1], "正在加载..."), app.bundle, True)
    assert not app.states.is_state(page, "qk_vpd_home")


def test_legacy_input_label_remains_supported_only_by_legacy_entry(ws):
    app = ws.app("qiankun_hm")
    tree = _tree("输入车位号设为目的地")
    assert resolve("qk_spot_input", app.targets["qk_spot_input"], tree, None, app.dir)["status"] == "found"
    assert resolve("qk_search_entry", app.targets["qk_search_entry"], tree, None, app.dir)["status"] == "not_found"


@pytest.mark.parametrize("label", HOME_LABELS)
@pytest.mark.parametrize("target_id", ENTRY_TARGETS)
def test_home_postcondition_then_search_navigation_uses_real_executor(ws, tmp_path, label, target_id):
    app = ws.app("qiankun_hm")
    world = {"bundle": app.bundle, "app_version": "1.0.26.100", "screen": [1320, 2848],
             "initial": {"foreground": app.bundle, "installed": True}, "launch_screen": "home",
             "display": {"density": 3.375, "device_form": "bar"}, "screens": {
                 "home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
                     {"t": "Text", "text": label, "click": True, "b": [20, 100, 1300, 180], "go": "search"}]}},
                 "search": {"root": {"t": "RelativeContainer", "id": "SearchPoiPage", "b": [0, 0, 1320, 2848], "children": [
                     {"t": "Search", "id": "search_input", "b": [20, 100, 1300, 180], "children": [
                         {"t": "SearchField", "id": "__SearchField__search_input", "text": "输入目的地或车位号",
                          "b": [20, 100, 1300, 180]}]}]}}}}
    path = tmp_path / "home-variant.json"
    write_json(path, world)
    case = {"id": "TC-TEST-HOME-VARIANT", "version": 1, "status": "draft", "app": app.id,
            "title": "Synthetic known home label navigation", "env_requirements": {},
            "steps": [{"id": "S1", "action": "launch", "expect_state": "qk_vpd_home", "timeout_s": 0},
                      {"id": "S2", "action": "tap", "target": target_id,
                       "expect_state": "qk_spot_search", "timeout_s": 0, "retry": 0}],
            "assertions": [], "cleanup": [{"id": "C1", "action": "stop_app"}]}
    run = run_case(ws, case=case, device_spec=f"replay:{path}", propose=False)
    assert [(step["step_id"], step["outcome"]) for step in run["steps"]] == [("S1", "passed"), ("S2", "passed")]

