"""Issue #43 的真实离线路径回归；合成语义只在 pytest 临时 workspace 中登记。

不使用 HDC、不修改产品判据。失败断言同时检查 replay.tap 调用及 dispatch.sent，
避免仅验证 Policy 返回值而漏掉 Executor、act、explore 或重定位下发路径。
"""
from copy import deepcopy

import pytest
from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world, _start, cli

from vpdtest.devices.replay import ReplayDevice
from vpdtest.execute import Executor
from vpdtest.explore import explore
from vpdtest.session import parse_select
from vpdtest.util import read_json, read_jsonl, write_json


def _button(label="确定", *, go="detail", field="text", extra=()):
    children = [{"t": "Text", "id": "confirmation-label", field: label,
                 "b": [100, 520, 1220, 600]}]
    children.extend({"t": "Text", "text": text, "b": [100, 620, 1220, 680]}
                    for text in extra)
    return {"t": "Button", "id": "confirmation-button", "click": True, "go": go,
            "b": [80, 500, 1240, 700 if extra else 620], "children": children}


def _modal(title="普通提示", *, label="确定", go="detail", field="text", extra=()):
    return {"t": "Dialog", "b": [40, 280, 1280, 900], "children": [
        {"t": "Text", "text": title, "b": [60, 320, 1260, 400]},
        _button(label, go=go, field=field, extra=extra),
    ]}


def _home(*children):
    return {"root": {"t": "Column", "b": [0, 0, 1320, 2848],
                     "children": list(children)}}


def _world(tmp_path, home, *, extra=None, name="confirmation"):
    screens = {"home": home}
    screens.update(extra or {})
    return _lab_world(tmp_path, name, screens)


def _tap_spy(monkeypatch, device):
    assert device.kind == "replay"
    calls = []
    original = device.tap

    def tap(*point):
        calls.append(point)
        return original(*point)

    monkeypatch.setattr(device, "tap", tap)
    return calls


def _class_tap_spy(monkeypatch):
    calls = []
    original = ReplayDevice.tap

    def tap(device, *point):
        assert device.kind == "replay"
        calls.append(point)
        return original(device, *point)

    monkeypatch.setattr(ReplayDevice, "tap", tap)
    return calls


def _tap_events(ctx):
    return [event for event in read_jsonl(ctx.store.events_path)
            if event.get("event") == "dispatch" and event.get("op") == "tap"]


def _assert_unissued(ctx, result, calls):
    assert result["outcome"] == "blocked"
    assert result.get("final_status") == "policy_denied"
    assert calls == []
    assert all(event["sent"] is False for event in _tap_events(ctx))


def _register_planning(ws):
    path = ws.root / "apps" / "motor_hm" / "states.json"
    data = read_json(path)
    data["states"]["fixture_planning"] = {"require_text_pattern": ["路线规划中"]}
    data["recipes"]["fixture_planning"] = [
        {"action": "launch", "expect_state": "fixture_planning"},
    ]
    write_json(path, data)


def _register_interrupt(ws, review, *, registered_target=False):
    path = ws.root / "apps" / "motor_hm" / "states.json"
    data = read_json(path)
    interrupt = {"class": "benign", "review": review,
                 "require_text": ["离线提示"], "dismiss_text": "确定", "settle_s": 0}
    if registered_target:
        interrupt.pop("dismiss_text")
        interrupt["dismiss_target"] = "fixture_info_ok"
        target_path = ws.root / "apps" / "motor_hm" / "targets.json"
        targets = read_json(target_path)
        targets["targets"]["fixture_info_ok"] = {
            "side_effect": "dismiss_dialog",
            "strategies": [{"kind": "text", "value": "确定", "within": {"type": "Dialog"}}],
        }
        write_json(target_path, targets)
    data["interrupts"]["fixture_info"] = interrupt
    write_json(path, data)


@pytest.mark.parametrize("label,effect,field", [
    (" 确定 ", "navigation", "text"),
    ("确认", "read_only", "text"),
    ("OK", "dismiss_dialog", "desc"),
])
def test_unknown_confirmation_cannot_self_certify_with_target_metadata(
        ws, tmp_path, monkeypatch, label, effect, field):
    spec = _world(tmp_path, _home(_button(label, field=field)))
    ctx = _ctx(ws, spec)
    calls = _tap_spy(monkeypatch, ctx.device)
    target = parse_select("id=confirmation-button", side_effect=effect)
    target.update(review="reviewed", source="agent-asserted benign confirmation")
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "revalidate": True, "target": target}, "fixture")
    _assert_unissued(ctx, result, calls)


def test_cli_act_default_navigation_cannot_tap_planning_confirmation(ws, tmp_path, monkeypatch):
    _register_planning(ws)
    spec = _world(tmp_path, _home(
        {"t": "Text", "text": "路线规划中，请稍后…", "b": [40, 100, 1280, 180]},
        _button(),
    ))
    calls = _class_tap_spy(monkeypatch)
    _start(ws, spec, "confirm-planning")
    output = cli(ws, "act", "tap", "--select", "text=确定", "--app", "motor_hm",
                 "--session", "confirm-planning", "--device", spec)
    result = output["result"]
    assert result["outcome"] == "blocked" and result["final_status"] == "policy_denied"
    assert result["attempts"][0]["state_before"] == "fixture_planning"
    assert calls == []
    events = read_jsonl(ws.runs_dir / "session-confirm-planning" / "events.jsonl")
    assert not any(event.get("event") == "dispatch" and event.get("op") == "tap"
                   and event.get("sent") is not False for event in events)


def test_explore_skips_planning_confirmation_without_recording_a_transition(ws, tmp_path, monkeypatch):
    _register_planning(ws)
    spec = _world(tmp_path, _home(
        {"t": "Text", "text": "路线规划中，请稍后…", "b": [40, 100, 1280, 180]},
        _button(),
    ))
    calls = _class_tap_spy(monkeypatch)
    output = explore(ws, "motor_hm", "fixture_planning", spec, budget=5)
    assert calls == [] and output["transitions"] == []
    assert any(item["label"] == "确定" and item["reason"] for item in output["skipped"])
    assert output["exit"]["reason"] == "frontier_exhausted"


def test_reviewed_benign_registered_dismiss_target_closes_the_same_modal(ws, tmp_path, monkeypatch):
    _register_interrupt(ws, "reviewed", registered_target=True)
    base = _home({"t": "Text", "text": "普通内容", "b": [40, 100, 1280, 180]})
    spec = _world(tmp_path, _home(_modal("离线提示", go="base")), extra={"base": base})
    ctx = _ctx(ws, spec)
    calls = _tap_spy(monkeypatch, ctx.device)
    result = Executor(ctx).run_step({"id": "R1", "action": "read", "read_scope": "page",
                                     "pattern": "普通内容", "retry": 0}, "fixture")
    assert result["outcome"] == "passed" and len(calls) == 1
    dismissed = result["attempts"][0]["dismissed"]
    assert dismissed[0]["interrupt"] == "fixture_info" and dismissed[0]["sent"] is True
    assert _tap_events(ctx)[0]["checks"]["tap"]["policy"]["allowed"]
    assert ctx.device.state["stack"][-1] == "base"


def test_pending_benign_interrupt_does_not_supply_confirmation_semantics(ws, tmp_path, monkeypatch):
    _register_interrupt(ws, "pending")
    spec = _world(tmp_path, _home(_modal("离线提示")))
    ctx = _ctx(ws, spec)
    calls = _tap_spy(monkeypatch, ctx.device)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": parse_select("text=确定", side_effect="dismiss_dialog")}, "fixture")
    _assert_unissued(ctx, result, calls)


def test_reviewed_interrupt_in_another_modal_cannot_authorize_the_confirmation(ws, tmp_path, monkeypatch):
    _register_interrupt(ws, "reviewed")
    lower = {"t": "Dialog", "b": [40, 80, 1280, 240], "children": [
        {"t": "Text", "text": "离线提示", "b": [60, 100, 1260, 200]},
    ]}
    spec = _world(tmp_path, _home(lower, _modal("普通提示")))
    ctx = _ctx(ws, spec)
    calls = _tap_spy(monkeypatch, ctx.device)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": parse_select("text=确定", side_effect="dismiss_dialog")}, "fixture")
    _assert_unissued(ctx, result, calls)


@pytest.mark.parametrize("granted", [False, True])
def test_current_rename_modal_semantics_still_require_matching_userdata_grant(ws, tmp_path, monkeypatch, granted):
    spec = _world(tmp_path, _home(_modal("修改名称")))
    ctx = _ctx(ws, spec)
    ctx.authorized = {"user_data"} if granted else set()
    calls = _tap_spy(monkeypatch, ctx.device)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": parse_select("text=确定", side_effect="user_data")}, "fixture")
    if not granted:
        _assert_unissued(ctx, result, calls)
        return
    assert result["outcome"] == "passed" and len(calls) == 1
    event = _tap_events(ctx)[0]
    assert event["sent"] is True and event["checks"]["tap"]["policy"]["allowed"]
    assert ctx.device.state["stack"][-1] == "detail"


def test_dispatch_refresh_rechecks_semantics_when_confirmation_node_identity_is_unchanged(ws, tmp_path, monkeypatch):
    home = _home(_modal("修改名称"), {
        "t": "Button", "click": True, "b": [80, 500, 1240, 620],
        "children": [{"t": "Text", "text": "遮挡面板", "b": [100, 520, 1220, 600]}],
    })
    home["after_obs"] = {"n": 1, "go": "changed", "replace": True}
    changed = _home(_modal("普通提示"))
    spec = _world(tmp_path, home, extra={"changed": changed}, name="confirmation-refresh")
    ctx = _ctx(ws, spec)
    ctx.authorized = {"user_data"}
    calls = _tap_spy(monkeypatch, ctx.device)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": parse_select("text=确定", side_effect="user_data")}, "fixture")
    _assert_unissued(ctx, result, calls)
    attempt = result["attempts"][0]
    # 初检允许，遮挡触发 Dispatcher 重新观察；语义变了而目标节点文字/ID/位置完全不变。
    assert attempt["policy"]["allowed"]
    checks = attempt["dispatch_recheck"]["checks"]["tap_attempts"]
    assert [item["status"] for item in checks] == ["target_obscured", "policy_denied"]
    before = next(item["node"] for item in attempt["locate"]["attempts"] if item.get("status") == "found")
    after = ctx.last_tree.find_id("confirmation-label")[0].brief()
    assert all(before[key] == after[key] for key in ("type", "text", "id", "bounds"))
    assert not checks[-1]["policy"]["allowed"]


def test_safe_stop_cannot_mask_unknown_confirmation_in_the_same_click_container(ws, tmp_path, monkeypatch):
    spec = _world(tmp_path, _home(_button("返回", extra=("确定",))))
    ctx = _ctx(ws, spec)
    calls = _tap_spy(monkeypatch, ctx.device)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": parse_select("text=返回", side_effect="safe_stop")}, "fixture")
    _assert_unissued(ctx, result, calls)


@pytest.mark.parametrize("label,effect", [("暂停", "safe_stop"), ("查看详情", "navigation")])
def test_pure_safe_stop_and_ordinary_navigation_remain_available_on_unknown_pages(ws, tmp_path, monkeypatch, label, effect):
    spec = _world(tmp_path, _home(_button(label)))
    ctx = _ctx(ws, spec)
    calls = _tap_spy(monkeypatch, ctx.device)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": parse_select(f"text={label}", side_effect=effect)}, "fixture")
    assert result["outcome"] == "passed" and len(calls) == 1
    assert _tap_events(ctx)[0]["sent"] is True


def test_pane_only_ambiguous_candidates_keep_restrictive_effect_union(ws, tmp_path, monkeypatch):
    home = {"root": {"t": "Row", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Column", "b": [0, 0, 600, 2848], "children": [
            {"t": "Text", "text": "LEFT", "b": [40, 100, 550, 180]},
        ]},
        {"t": "Column", "b": [600, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "A", "b": [620, 100, 1300, 180]},
            {"t": "Text", "text": "B", "b": [620, 200, 1300, 280]},
            {"t": "Button", "click": True, "go": "detail", "b": [620, 500, 1300, 620],
             "children": [{"t": "Text", "text": "确定", "b": [640, 520, 1280, 600]}]},
        ]},
    ]}}
    spec = _world(tmp_path, home, name="confirmation-panes")
    world_path = spec.split(":", 1)[1]
    world = read_json(world_path)
    world["display"]["density"] = 1
    write_json(world_path, world)
    ctx = _ctx(ws, spec)
    ctx.app.states.states.update({
        "fixture_motion": {"require_text": ["A"], "forbid_text": ["LEFT"],
                           "effect_text": {"^确定$": "vehicle_motion"}},
        "fixture_data": {"require_text": ["B"], "forbid_text": ["LEFT"],
                         "effect_text": {"^确定$": "user_data"}},
    })
    ctx.authorized = {"vehicle_motion", "user_data"}
    calls = _tap_spy(monkeypatch, ctx.device)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": parse_select("text=确定")}, "fixture")
    _assert_unissued(ctx, result, calls)
    page = ctx.last_obs["page"]
    assert page["state"] == "ambiguous" and page["active"] == [] and page["candidates"] == []
    assert page["panes"][1]["candidates"] == ["fixture_motion", "fixture_data"]
    rules = deepcopy(ctx.app.states.deny_for_page(page))
    assert {rule["class"] for rule in rules if isinstance(rule, dict)} == {"vehicle_motion", "user_data"}
