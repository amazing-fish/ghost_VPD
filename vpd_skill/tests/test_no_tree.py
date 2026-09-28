"""无 UI 树页面：只能走确定性模板定位；模板多处匹配时拒绝点击；不允许盲点坐标。"""
from vpdtest.devices.replay import ReplayDevice
from vpdtest.execute import Executor
from vpdtest.experience import Overlay
from vpdtest.locate import resolve
from vpdtest.observe import RunStore
from vpdtest.templates import crop
from vpdtest.util import write_json
from vpdtest.workspace import RunContext

WORLD = {
    "bundle": "app.huawei.hmos.motor", "app_version": "t", "screen": [1320, 2848], "launch_screen": "map",
    "initial": {"foreground": "com.ohos.sceneboard"},
    "screens": {
        "map": {"tree": "unavailable", "root": {"t": "XComponent", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Image", "b": [100, 2400, 300, 2600], "icon": "full-view", "go": "map2"},
            {"t": "Image", "b": [900, 2400, 1100, 2600], "icon": "ego-view"}]}},
        "map2": {"tree": "unavailable", "root": {"t": "XComponent", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Image", "b": [100, 2400, 300, 2600], "icon": "full-view"},
            {"t": "Image", "b": [900, 2400, 1100, 2600], "icon": "full-view"}]}}}}


def _setup(ws, tmp_path):
    wp = tmp_path / "no_tree_world.json"
    write_json(wp, WORLD)
    dev = ReplayDevice(wp, state_path=tmp_path / "state.json")
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir, kind="t")
    ctx = RunContext(ws, app, dev, store, Overlay([]), mode="regression")
    dev.launch(app.bundle, app.ability)
    obs, tree = ctx.observe("seed")
    assert obs["tree"]["status"] == "unavailable" and obs["page"]["state"] == "undetermined"
    tmpl = crop(store.dir / obs["screenshot"]["path"], [100, 2400, 300, 2600], app.dir / "templates" / "full_view.png")
    target = {"side_effect": "navigation", "strategies": [
        {"kind": "text", "value": "全览"}, {"kind": "template", "file": "templates/full_view.png", "threshold": 0.9}]}
    return ctx, app, dev, obs, tree, target, tmpl


def test_template_path_when_tree_unavailable(ws, tmp_path):
    ctx, app, dev, obs, tree, target, _ = _setup(ws, tmp_path)
    loc = resolve("full_view", target, tree, ctx.observer.screenshot_abs(obs), app.dir)
    assert loc["status"] == "found" and loc["basis"] == "template" and not loc["adapted"]
    assert loc["attempts"][0]["status"] == "skipped_no_tree"
    assert 150 <= loc["point"][0] <= 250 and loc["score"] > 0.95
    assert ctx.policy.check_tap(None, loc["point"], "template", target)["allowed"]
    app.targets["full_view"] = target
    res = Executor(ctx).run_step({"id": "S1", "action": "tap", "target": "full_view", "retry": 0}, "t")
    # 页面仍无 UI 树：动作已下发且执行器成功，但业务状态不可判定 → 不能判通过
    att = res["attempts"][0]
    assert att["dispatch"]["sent"] and att["dispatch"]["executor_ok"]
    assert res["outcome"] == "passed"  # 未声明 expect_state：只证明动作完成，不产生任何业务结论
    assert dev.current_screen() == "map2"


def test_ambiguous_template_is_not_clicked(ws, tmp_path):
    ctx, app, dev, obs, tree, target, _ = _setup(ws, tmp_path)
    dev.tap(200, 2500)  # 进入 map2：两个相同图标
    obs2, tree2 = ctx.observe("two-icons")
    loc = resolve("full_view", target, tree2, ctx.observer.screenshot_abs(obs2), app.dir)
    assert loc["status"] == "ambiguous"
    app.targets["full_view"] = target
    res = Executor(ctx).run_step({"id": "S2", "action": "tap", "target": "full_view", "retry": 1,
                                  "expect_state": "car_tab"}, "t")
    assert res["outcome"] == "blocked" and res["attribution"] == "tool"
    assert all("dispatch" not in a for a in res["attempts"])


def test_expect_state_on_no_tree_page_is_unobservable(ws, tmp_path):
    ctx, app, dev, obs, tree, target, _ = _setup(ws, tmp_path)
    app.targets["full_view"] = target
    res = Executor(ctx).run_step({"id": "S1", "action": "tap", "target": "full_view", "retry": 0,
                                  "expect_state": "car_tab"}, "t")
    assert res["outcome"] == "blocked" and res["final_status"] == "unobservable"
