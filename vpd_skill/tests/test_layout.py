"""屏幕形态自适应：按 vp 宽度分档、分栏识别、可见区域定位、容器内滑动；用例里不出现像素坐标。

每个形态都用同一份 TC-MOTOR-HM-001 / targets.json / states.json 运行，只换回放 world。
反例测试说明：如果按"整屏比例滑动 / bounds 中心点击 / 整窗匹配状态"，这些形态会出错。
"""
import pytest

from vpdtest import layout as L
from vpdtest.cases import lint
from vpdtest.devices.harmony_hdc import infer_device_form, parse_display
from vpdtest.devices.replay import ReplayDevice, expand, load_world
from vpdtest.execute import Executor
from vpdtest.experience import Overlay
from vpdtest.knowledge import KnowledgeStore
from vpdtest.locate import resolve, template_scales
from vpdtest.observe import RunStore
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.workspace import RunContext

from conftest import WORLDS, confirm, world

BASE, COMPACT, FOLD = (world(n) for n in ("motor_hm_base.json", "motor_hm_compact.json", "motor_hm_fold.json"))
CFG = L.config(None)

# 2026-09-29 真机 SGT-AL10 输出的 Screen/Display 段（照录）；进程列表段替换为虚构内容，用来确认解析器不读取它
DISPLAY_DUMP = """-------------------------------[ability]-------------------------------
----------------------------------DisplayManagerService----------------------------------
-------------- DMS FREEZED PID LIST  --------------
[PID: 1111]:  [com.example.private]
---------------- Screen ID: 0 ----------------
PhysicalFoldStatus:           UNKNOWN
FoldLockStatus:               UNLOCKED
DisplayFoldStatus:            UNKNOWN
[SCREEN SESSION]
Name:                         UNKNOWN
DisplayId:                    0
activeModes<id, W, H, RS>:    768, 1320, 2848, 60
Rotation:                     0
[SCREEN INFO]
VirtualWidth:                 391
VirtualHeight:                843
VirtualPixelRatio:            3.375
Rotation:                     0
[SCREEN PROPERTY]
Rotation:                     0
Density:                      3.375
DPI<X, Y>:                    478.971, 463.712
Bounds<L,T,W,H>:              0, 0, 1320, 2848,
DefaultDeviceRotationOffset   0
---------------- Display ID: 0 ----------------
[DISPLAY INFO]
Width:                        1320
Height:                       2848
------------- Client Screen Infos -------------
Density:                      9.9
-----------------------------------------------
"""


def _tree(world_file: str, screen: str) -> UiTree:
    w = load_world(WORLDS / world_file)
    return UiTree({"attributes": {"type": "orgRoot", "bounds": "[0,0][%d,%d]" % tuple(w["screen"])},
                   "children": [expand(w["screens"][screen]["root"], w["bundle"])]})


def _profile(world_file: str, screen: str) -> tuple:
    dev = ReplayDevice(WORLDS / world_file)
    tree = _tree(world_file, screen)
    return tree, L.profile(tree, dev.display_info(), CFG)


def test_transport_error_is_environment_not_missing_target(ws):
    """2026-09-29 真机运行中 hdc 通道重建（E000004），前台查询失败曾被当成"不在 APP 内"，
    结论记成"工具：目标找不到"。现在应记为观测失败、环境问题，且不下发点击。"""
    from vpdtest.devices.harmony_hdc import classify_output
    msg = "[Fail][E000004]:The communication channel is being established.\nPlease wait for several seconds and try again."
    assert classify_output(msg) == "transport_unavailable"
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir, "session-tr", kind="session")
    dev = ws.device(BASE, state_path=store.dir / "replay_state.json")
    dev.launch(app.bundle, app.ability)
    dev.foreground = lambda: None
    dev.last_query_error = "transport_unavailable"
    ctx = RunContext(ws, app, dev, store, Overlay([]), mode="regression")
    res = Executor(ctx).run_step({"id": "S", "action": "tap", "target": "tab_my", "expect_state": "my_tab",
                                  "retry": 1}, "c")
    assert res["outcome"] == "error" and res["attribution"] == "environment"
    assert [a["status"] for a in res["attempts"]] == ["observation_failed", "observation_failed"]
    assert not any("dispatch" in a for a in res["attempts"])


def test_parse_real_display_dump_reads_only_screen_sections():
    d = parse_display(DISPLAY_DUMP)
    assert (d["width_px"], d["height_px"], d["density"]) == (1320, 2848, 3.375)
    assert d["fold_status"] == "unknown" and d["screens"] == 1
    assert "com.example.private" not in str(d)  # 进程列表段不读取
    assert infer_device_form(None, d["fold_status"])[0] == "bar"
    assert infer_device_form("1,0,0,0", "expanded")[0] == "foldable"


def test_width_class_uses_vp_not_pixels():
    # 同样是"窄屏"：真机 1320 px @3.375 = 391 vp，合成小直板 1080 px @3.0 = 360 vp
    _, real = _profile("motor_hm_base.json", "my_tab")
    _, small = _profile("motor_hm_compact.json", "my_tab")
    _, fold = _profile("motor_hm_fold.json", "my_tab")
    assert (real["width_vp"], real["width_class"], real["wide"]) == (391.1, "sm", False)
    assert (small["width_vp"], small["width_class"], small["wide"]) == (360.0, "sm", False)
    assert (fold["width_vp"], fold["width_class"], fold["wide"]) == (733.3, "md", True)
    assert L.key(real) == L.key(small) == "sm/portrait" and L.key(fold) == "md/portrait"
    # 阈值是配置，不是代码：把宽屏起点调到 640 vp 后同一台折叠屏仍判为宽屏，391 vp 仍是窄屏
    cfg = L.config({"breakpoints_vp": [["narrow", 0], ["wide", 640]], "wide_classes": ["wide"]})
    assert L.classify_width(733.3, cfg) == "wide" and L.classify_width(391.1, cfg) == "narrow"
    assert L.profile(None, {"width_px": 1320, "height_px": 2848}, CFG)["status"] == "no_density"


def test_panes_come_from_tree_structure():
    _, bar = _profile("motor_hm_base.json", "my_tab")
    assert bar["panes"] == [] and bar["pane_count"] == 1  # 底部导航、单列页面不算分栏
    _, car = _profile("motor_hm_fold.json", "car_tab")
    assert car["panes"] == []  # 宽屏但这一页不分栏：侧边导航栏太窄，不算一栏
    _, my = _profile("motor_hm_fold.json", "my_tab")
    assert [p["type"] for p in my["panes"]] == ["NavBar", "NavDestination"] and my["pane_count"] == 2


def test_when_condition_skips_instead_of_guessing(ws):
    app = ws.app("motor_hm")
    tree, fold = _profile("motor_hm_fold.json", "my_tab")
    t = {"strategies": [{"kind": "text", "value": "我的", "region": [0, 0.85, 1, 1], "when": {"wide": False}}]}
    r = resolve("x", t, tree, None, app.dir, layout=fold)
    assert r["status"] == "not_found" and r["attempts"][0]["status"] == "skipped_layout"
    r = resolve("x", t, tree, None, app.dir, layout=None)
    assert r["attempts"][0]["status"] == "skipped_layout_unknown" and "窗口形态" in r["reason"]
    # 窄屏底部区域的后备策略在宽屏侧边导航上会失效，所以真实目标文件里它带 when；within TabBar 两种形态都适用
    loc = resolve("tab_my", app.targets["tab_my"], tree, None, app.dir, overlay_pref=1, layout=fold)
    assert loc["status"] == "found" and loc["strategy_index"] == 1 and loc["point"] == [120, 1155]


def test_compact_partially_hidden_row_is_not_tapped_at_bounds_center(ws):
    app = ws.app("motor_hm")
    tree, lay = _profile("motor_hm_compact.json", "my_tab")
    loc = resolve("settings_entry", app.targets["settings_entry"], tree, None, app.dir, layout=lay)
    assert loc["status"] == "partially_visible"  # 只露出上沿，不点边缘
    node = tree.find_text("设置")[0]
    raw_center = node.center
    hit = tree.node_at(*raw_center)
    # 原始 bounds 中心实际落在底部导航上（列表把这一行裁掉了）：按 bounds 点击会点到「发现」
    assert hit is not None and tree.is_within(hit, next(n for n in tree.nodes if n.type == "TabBar"))
    dev = ReplayDevice(WORLDS / "motor_hm_compact.json")
    dev.launch(dev.bundle, "EntryAbility")
    dev.state["stack"] = ["my_tab"]
    dev.tap(*raw_center)
    assert dev.state["stack"] == ["discover_tab"]


def test_same_case_passes_on_bar_compact_and_foldable(ws):
    confirm(ws, "K-MOT-001", "K-MOT-002")
    for spec, swipes in ((BASE, None), (COMPACT, 1), (FOLD, 1)):
        run = run_case(ws, "TC-MOTOR-HM-001", device_spec=spec, propose=False)
        assert run["verdict"]["verdict"] == "passed", (spec, run["verdict"])
        s3 = next(s for s in run["steps"] if s["step_id"] == "S3")
        assert s3["attempts"][0]["locate"].get("scroll_swipes") == swipes
        assert run["layout"]["profile"]["width_class"] in ("sm", "md")


def test_foldable_split_view_state_is_identified_per_pane(ws):
    confirm(ws, "K-MOT-001", "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=FOLD, propose=False)
    s3 = next(s for s in run["steps"] if s["step_id"] == "S3")
    import json
    obs = json.loads((ws.runs_dir / run["run_id"] / "obs" / s3["final_obs"] / "observation.json").read_text("utf-8"))
    assert obs["page"]["state"] == "my_tab+settings" and obs["page"]["active"] == ["my_tab", "settings"]
    # 反例：不按栏匹配时，侧边导航的「爱车」让 settings 的 forbid_text 生效，设置页会被认成 my_tab
    app = ws.app("motor_hm")
    tree = UiTree(json.loads((ws.runs_dir / run["run_id"] / "obs" / s3["final_obs"] / "tree.json").read_text("utf-8")))
    naive = app.states.identify(tree, app.bundle, True, None)
    assert naive["state"] == "my_tab" and not app.states.is_state(naive, "settings")


def test_foldable_swipe_stays_inside_list_pane(ws):
    dev = ReplayDevice(WORLDS / "motor_hm_fold.json")
    dev.launch(dev.bundle, "EntryAbility")
    dev.state["stack"] = ["my_tab"]
    dev.swipe(1100, int(2480 * 0.7), 1100, int(2480 * 0.3))  # 旧做法：整屏中线 → 落在右栏，列表不动
    assert dev.state["stack"] == ["my_tab"]
    confirm(ws, "K-MOT-001", "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=FOLD, propose=False)
    trail = next(s for s in run["steps"] if s["step_id"] == "S3")["attempts"][0]["locate"]["scroll"]
    assert trail[0]["container"]["type"] == "List" and 240 <= trail[0]["from"][0] <= 1000
    assert trail[0]["content_changed"] is True


def test_layout_requirement_blocks_as_environment_not_failure(ws):
    case = ws.load_case("TC-MOTOR-HM-002")
    case.update(id="TC-LAYOUT-WIDE", layout_requirements={"wide": True})
    confirm(ws, "K-MOT-001")
    bar = run_case(ws, case=case, device_spec=BASE, propose=False)
    assert bar["verdict"]["verdict"] == "blocked" and bar["verdict"]["attribution"] == "environment"
    assert bar["env"]["unmet"] == [{"key": "layout.wide", "required": True, "actual": False}]
    assert all(s["outcome"] == "not_run" for s in bar["steps"])
    fold = run_case(ws, case=case, device_spec=FOLD, propose=False)
    assert fold["verdict"]["verdict"] == "passed"


def test_locator_preference_is_scoped_to_window_form():
    ov = Overlay([{"kind": "locator_preference", "change": {"target": "tab_my", "prefer_strategy_index": 1,
                                                            "layout_key": "sm/portrait"}}])
    assert ov.locator_pref("tab_my", "sm/portrait") == 1
    assert ov.locator_pref("tab_my", "md/portrait") is None  # 直板上学到的偏好不带到折叠屏展开态
    legacy = Overlay([{"kind": "locator_preference", "change": {"target": "tab_my", "prefer_strategy_index": 1}}])
    assert legacy.locator_pref("tab_my", "md/portrait") == 1


def test_template_scale_follows_density():
    assert template_scales({"density": 3.375}, {"density": 3.375}) == (0.8, 1.25, 15)
    lo, hi, _ = template_scales({"density": 3.375}, {"density": 3.0})
    assert lo < 3.0 / 3.375 < hi
    assert template_scales({}, {"density": 3.0}) == (0.5, 2.0, 31)  # 未记录采集密度时全范围搜索


def test_cases_must_not_contain_pixel_points(ws):
    app = ws.app("motor_hm")
    case = ws.load_case("TC-MOTOR-HM-001")
    case["steps"][1] = {**case["steps"][1], "point": [1100, 2700]}
    case["layout_requirements"] = {"panes": 2}
    problems = [i["problem"] for i in lint(case, app, KnowledgeStore(ws.root))]
    assert any("像素坐标" in p for p in problems)
    assert any("panes" in p and "启动前" in p for p in problems)


def test_agent_point_from_other_window_size_is_dropped(ws):
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir, "session-pt", kind="session")
    dev = ws.device(FOLD, state_path=store.dir / "replay_state.json")
    dev.launch(app.bundle, app.ability)
    ctx = RunContext(ws, app, dev, store, Overlay([]), mode="agent")
    step = {"id": "A1", "action": "tap", "retry": 0, "point": [1000, 2000], "point_window": [1320, 2848],
            "target": {"label": "agent:截图中的按钮", "strategies": [], "side_effect": "navigation"}}
    res = Executor(ctx).run_step(step, "session")
    loc = res["attempts"][0]["locate"]
    assert res["outcome"] == "blocked" and "已丢弃" in loc["point_rejected"]
    assert "dispatch" not in res["attempts"][0]


@pytest.mark.parametrize("name", ["motor_hm_compact.json", "motor_hm_fold.json"])
def test_variant_worlds_stay_inside_their_screen(name):
    w = load_world(WORLDS / name)
    sw, sh = w["screen"]
    from vpdtest.devices.replay import _walk
    for sid, scr in w["screens"].items():
        if sid in ("vpd_home",):
            continue
        for node, _ in _walk(scr["root"]):
            b = node.get("b")
            if b:
                assert 0 <= b[0] <= b[2] <= sw and b[1] <= b[3], (name, sid, node)
