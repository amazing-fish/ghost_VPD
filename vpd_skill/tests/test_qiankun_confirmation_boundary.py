"""生产 TC-QK-103 的开始/确认执行边界，使用现有合成 Replay 世界。

全窗变体只在临时设备的内存世界中扩大 Dialog bounds；它验证未知确认的拒绝，
不证明真实 APP 使用全窗弹窗，也不确认知识或批准生产资产。
"""
import pytest

from vpdtest.execute import Executor
from vpdtest.experience import Overlay
from vpdtest.locate import resolve
from vpdtest.observe import RunStore
from vpdtest.safety import MotionGuard
from vpdtest.util import read_jsonl
from vpdtest.workspace import RunContext


@pytest.mark.parametrize("full_window", [False, True], ids=["existing-dialog", "full-window-unknown"])
def test_103_start_confirmation_requires_current_semantics(ws, monkeypatch, full_window):
    app = ws.app("qiankun_hm")
    case = ws.load_case("TC-QK-103")
    steps = {step["id"]: step for step in case["steps"]}
    store = RunStore(ws.runs_dir)
    world_path = ws.root.parent / "fixtures" / "worlds" / "qiankun_hm_ready.json"
    device = ws.device(f"replay:{world_path}", state_path=store.dir / "replay_state.json")
    assert device.kind == "replay"
    device.launch(app.bundle, app.ability)
    # 聚焦 S2 已到达算路结果之后的真实生产 S3/S4，不改用例或状态/目标定义。
    device.state["stack"] = ["qk_route_result"]
    if full_window:
        width, height = device.world["screen"]
        device.world["screens"]["qk_start_confirm"]["root"]["b"] = [0, 0, width, height]

    ctx = RunContext(ws, app, device, store, Overlay([]))
    ctx.authorized = set(case["authorizations"])
    ctx.supervisor = "离线回放监护"
    ctx.motion = MotionGuard(case["safety"], ctx.clock, store)
    assert ctx.policy.check_authorizations(
        case["authorizations"], ctx.authorized, ctx.supervisor, case["safety"])["ok"]
    calls = []
    original_tap = device.tap

    def tap(*point):
        calls.append(point)
        return original_tap(*point)

    monkeypatch.setattr(device, "tap", tap)
    executor = Executor(ctx)
    start = executor.run_step(steps["S3"], case["id"])
    assert start["outcome"] == "passed"
    assert start["attempts"][0]["dispatch"]["sent"] is True
    assert len(calls) == 1
    expected_page = "unknown" if full_window else "qk_route_result"
    assert start["attempts"][0]["post"]["observed"] == expected_page

    # Executor 的拒绝发生在下发前；独立核对 Dispatcher 在同一当前树上也使用相同语义。
    obs, tree = ctx.last_obs, ctx.last_tree
    target = app.targets["qk_start_confirm"]
    loc = resolve("qk_start_confirm", target, tree, ctx.observer.screenshot_abs(obs),
                  app.dir, None, None, obs.get("layout"))
    assert loc["status"] == "found"
    dispatch_check = ctx.dispatcher._tap_check({
        "tree": tree, "loc": loc, "target": target, "target_ref": "qk_start_confirm",
        "page": obs["page"], "state_deny": app.states.deny_for_page(obs["page"]),
    })
    confirm = executor.run_step(steps["S4"], case["id"])
    tap_events = [event for event in read_jsonl(store.events_path)
                  if event.get("event") == "dispatch" and event.get("op") == "tap"]
    start_events = [event for event in tap_events if event.get("step") == "S3"]
    confirm_events = [event for event in tap_events if event.get("step") == "S4"]
    assert len(start_events) == 1 and start_events[0]["sent"] is True
    if full_window:
        assert confirm["outcome"] == "blocked" and confirm["final_status"] == "policy_denied"
        assert confirm["attempts"][0]["state_before"] == "unknown"
        policy = confirm["attempts"][0]["policy"]
        assert policy["allowed"] is False
        assert policy["deny"]["class"] == "confirmation_semantics_unknown"
        assert dispatch_check["status"] == "policy_denied"
        assert dispatch_check["policy"]["deny"]["class"] == "confirmation_semantics_unknown"
        assert len(calls) == 1 and confirm_events == []
    else:
        assert confirm["outcome"] == "passed" and dispatch_check["status"] == "ok"
        assert len(calls) == 2
        assert len(confirm_events) == 1 and confirm_events[0]["sent"] is True
        source = confirm["attempts"][0]["policy"]["confirmation_source"]["page_sources"][0]
        assert source["class"] == "vehicle_motion"
        assert source["candidates"][0]["state"] == "qk_route_result"
