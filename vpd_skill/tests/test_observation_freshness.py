"""观测末尾状态必须与本次树证据一致；只用临时回放设备。"""
import pytest

from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world, BUNDLE
from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.session import parse_select
from vpdtest.states import StateModel
from vpdtest.util import read_json, read_jsonl, write_json


def _context(ws, tmp_path, monkeypatch, stage):
    spec = _lab_world(tmp_path)
    path = spec.split(":", 1)[1]
    world = read_json(path)
    world["screens"]["home"]["root"]["children"].append(
        {"t": "Text", "text": "网络失败（占位）", "b": [40, 600, 1280, 700]})
    write_json(path, world)
    ctx = _ctx(ws, spec)
    ctx.app.states = ctx.observer.states = StateModel({"states": {
        "fixture_home": {"require_text": ["唯一入口"]}}}, BUNDLE)
    state = {"foreground": BUNDLE, "alive": True, "shots": 0, "queries": 0}

    def foreground():
        state["queries"] += 1
        value = state["foreground"]
        if stage == "switch_back" and state["queries"] == 2:
            state["foreground"] = BUNDLE
        return value

    monkeypatch.setattr(ctx.device, "foreground", foreground)
    monkeypatch.setattr(ctx.device, "process_alive", lambda _: state["alive"])
    monkeypatch.setattr(ctx.observer, "private_windows", lambda: [])
    dump = ctx.device.dump_tree

    def dump_then_switch(*args):
        result = dump(*args)
        if stage in ("after_tree", "without_screenshot", "switch_back"):
            state["foreground"] = "com.yinwang.qiankunapp.hm"
        return result

    monkeypatch.setattr(ctx.device, "dump_tree", dump_then_switch)
    shot = ctx.device.screenshot

    def capture(path):
        state["shots"] += 1
        if stage == "failed_capture":
            state["foreground"] = "com.ohos.sceneboard"
            return DeviceResult("screenshot", True, False, "capture_failed", "fixture")
        result = shot(path)
        if stage == "crash_during_capture":
            state.update(foreground="com.ohos.sceneboard", alive=False)
        if stage == "unknown_after_capture":
            state["foreground"] = None
        return result

    monkeypatch.setattr(ctx.device, "screenshot", capture)
    if stage == "display_query":
        display = ctx.device.display_info

        def display_then_switch():
            result = display()
            state["foreground"] = "com.ohos.sceneboard"
            return result

        monkeypatch.setattr(ctx.device, "display_info", display_then_switch)
    if stage == "withheld_capture":
        def windows_then_switch():
            state["foreground"] = "com.yinwang.qiankunapp.hm"
            return ["SCBDropdownPanel"]
        monkeypatch.setattr(ctx.observer, "private_windows", windows_then_switch)
    return ctx, state


@pytest.mark.parametrize("stage", ["crash_during_capture", "after_tree", "without_screenshot",
                                   "failed_capture", "unknown_after_capture", "withheld_capture", "display_query",
                                   "switch_back", "stable"])
def test_every_observation_branch_invalidates_old_tree(ws, tmp_path, monkeypatch, stage):
    ctx, state = _context(ws, tmp_path, monkeypatch, stage)
    obs, tree = ctx.observe("fixture", screenshot=stage != "without_screenshot")
    if stage == "stable":
        assert obs["page"]["state"] == "fixture_home" and obs["fresh"] is True
        assert tree is not None and obs["texts"]
        assert state["queries"] == 3  # 保留截图前复查；末查替换原截图后查询
        return
    assert obs["page"]["state"] == ("undetermined" if state["foreground"] in (None, BUNDLE) else "out_of_app")
    assert obs["fresh"] is False and obs["tree"]["status"] == "stale"
    assert tree is None and obs["texts"] == [] and not obs.get("ui_alerts")
    assert obs["foreground"] == state["foreground"] and obs["process_alive"] == state["alive"]
    assert (ctx.store.dir / obs["tree"]["path"]).exists()  # 旧树仅作为证据保留
    assert any(e["event"] == "observation_stale" for e in read_jsonl(ctx.store.events_path))
    assert not any(e["event"] == "ui_alert" for e in read_jsonl(ctx.store.events_path))
    if stage in ("after_tree", "withheld_capture", "without_screenshot", "switch_back"):
        assert state["shots"] == 0
    if stage in ("crash_during_capture", "unknown_after_capture"):
        assert obs["screenshot"]["status"] == "withheld"
        assert not (ctx.store.dir / "obs" / obs["obs_id"] / "screen.png").exists()


@pytest.mark.parametrize("stage", ["crash_during_capture", "unknown_after_capture", "switch_back"])
def test_postcondition_does_not_pass_after_capture_state_change(ws, tmp_path, monkeypatch, stage):
    ctx, _ = _context(ws, tmp_path, monkeypatch, stage)
    post = Executor(ctx)._post({"id": "P1", "action": "tap", "expect_state": "fixture_home",
                               "timeout_s": 0}, 1, "fixture")
    assert post["met"] is False
    assert post["suspected_crash"] is (stage == "crash_during_capture")


def test_no_screenshot_revalidation_relocates_before_dispatch(ws, tmp_path, monkeypatch):
    ctx, state = _context(ws, tmp_path, monkeypatch, "stable")
    dump = ctx.device.dump_tree
    calls, taps = [], []

    def temporarily_unknown(*args):
        result = dump(*args)
        calls.append(args)
        if len(calls) == 2:  # 第一次是 before；第二次是无截图的 predispatch
            state["foreground"] = None
        return result

    foreground = ctx.device.foreground

    def unknown_once():
        result = foreground()
        if result is None:
            state["foreground"] = BUNDLE
        return result

    monkeypatch.setattr(ctx.device, "dump_tree", temporarily_unknown)
    monkeypatch.setattr(ctx.device, "foreground", unknown_once)
    monkeypatch.setattr(ctx.device, "tap", lambda *p: taps.append(p) or DeviceResult("tap", True, True))
    result = Executor(ctx).run_step({"id": "P1", "action": "tap", "target": parse_select("text=唯一入口"),
                                     "revalidate": True, "retry": 1}, "fixture")
    assert len(taps) == 1 and result["outcome"] == "passed_after_recovery"
    assert len(result["attempts"]) == 2
    assert result["attempts"][0].get("dispatch", {}).get("sent", False) is False
    assert len(calls) >= 4  # 必须重新观察并定位，不能在过时的复查树上下发
