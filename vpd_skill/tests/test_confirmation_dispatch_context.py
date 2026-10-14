"""Issue #43 的真实离线路径回归；合成语义只在 pytest 临时 workspace 中登记。

不使用 HDC、不修改产品判据。失败断言同时检查 replay.tap 调用及 dispatch.sent，
避免仅验证 Policy 返回值而漏掉 Executor、act、explore 或重定位下发路径。
"""
from copy import deepcopy

import pytest
from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world, _start, cli
from test_no_tree import _setup as _no_tree_setup

from vpdtest.devices.replay import ReplayDevice
from vpdtest.execute import Executor
from vpdtest.explore import explore
from vpdtest.locate import resolve
from vpdtest.session import parse_select
from vpdtest.templates import crop
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


@pytest.mark.parametrize("path", ["executor", "dispatcher"])
@pytest.mark.parametrize("second_effects,allowed", [
    ({}, False),
    ({"^确定$": "vehicle_motion"}, True),
    ({"^确定$": "user_data"}, False),
    ({"^取消$": "vehicle_motion"}, False),
], ids=["unmapped-candidate", "matching-consensus", "conflicting-effects", "unmatched-rule"])
def test_same_scope_confirmation_needs_every_candidate_to_agree(
        ws, tmp_path, monkeypatch, path, second_effects, allowed):
    spec = _world(tmp_path, _home(
        {"t": "Text", "text": "CANDIDATE A", "b": [40, 100, 1280, 180]},
        {"t": "Text", "text": "CANDIDATE B", "b": [40, 200, 1280, 280]},
        _button(),
    ), name=f"confirmation-consensus-{path}")
    ctx = _ctx(ws, spec)
    ctx.app.states.states.clear()
    ctx.app.states.states.update({
        "fixture_motion": {"require_text": ["CANDIDATE A"],
                           "effect_text": {"^确定$": "vehicle_motion"}},
        "fixture_other": {"require_text": ["CANDIDATE B"],
                          "effect_text": second_effects},
    })
    # 两种授权都有，避免把语义一致性测试误写成缺少 grant 的测试。
    ctx.authorized = {"vehicle_motion", "user_data"}
    calls = _tap_spy(monkeypatch, ctx.device)
    initial_obs, _ = ctx.observe("confirmation-candidates-before")
    target = parse_select("text=确定", side_effect="vehicle_motion")
    if path == "executor":
        result = Executor(ctx).run_step({"id": "C1", "action": "tap", "retry": 0,
                                         "target": target}, "fixture")
        if allowed:
            assert result["outcome"] == "passed"
        else:
            _assert_unissued(ctx, result, calls)
    else:
        obs, tree = ctx.observe("confirmation-consensus")
        loc = resolve("fixture-confirmation", target, tree, None, ctx.app.dir,
                      layout=obs.get("layout"))
        assert loc["status"] == "found"
        att = {"action": "tap", "step_id": "C1"}
        result = ctx.dispatcher.send(
            "tap", *loc["point"], preconditions=ctx.dispatcher.app_conditions("tap"), att=att,
            tap={"tree": tree, "loc": loc, "target": target,
                 "target_ref": "fixture-confirmation", "page": obs["page"],
                 "state_deny": ctx.app.states.deny_for_page(obs["page"])})
        assert result.ok is allowed
        assert result.sent is allowed
        if not allowed:
            assert att["status"] == "policy_denied"
            assert calls == []
            assert _tap_events(ctx)[-1]["sent"] is False
    page = initial_obs["page"]
    assert page["state"] == "ambiguous" and page["active"] == []
    assert page["candidates"] == ["fixture_motion", "fixture_other"]
    if allowed:
        assert len(calls) == 1
        assert _tap_events(ctx)[-1]["sent"] is True


@pytest.mark.parametrize("xpath,allowed", [
    ("//*[@text=concat('确','定')]", False),
    ("//*[@description=concat('O','K')]", False),
    ("//*[normalize-space(@text)='下一页']", False),
    ("//*[@text=$confirmation]", False),
    ("//*[attribute::text='下一页']", False),
    ("//*[@*[name()='text']=concat('确','定')]", False),
    ("//*[@id=concat('full','-view')]", False),
    ("//*[@id='full-view']", True),
    ("//*[@id='full-view(@text)']", True),
], ids=["concat-confirm", "concat-ok-description", "text-function", "variable", "text-attribute-axis",
        "dynamic-attribute", "id-function", "literal-id-navigation", "literal-id-punctuation"])
def test_no_tree_xpath_cannot_hide_semantics_behind_real_template_dispatch(
        ws, tmp_path, monkeypatch, xpath, allowed):
    ctx, app, dev, obs, tree, target, _ = _no_tree_setup(ws, tmp_path)
    assert tree is None or tree.status != "ok"
    target["strategies"][0] = {"kind": "xpath", "value": xpath}
    calls = _tap_spy(monkeypatch, dev)
    # 使用实际截图裁剪模板并真实匹配，证明后备定位已成功，拒绝来自 Dispatcher policy gate。
    loc = resolve("fixture-full-view", target, tree, ctx.observer.screenshot_abs(obs), app.dir)
    assert loc["status"] == "found" and loc["basis"] == "template"
    assert loc["attempts"][0]["status"] == "skipped_no_tree"
    assert loc["score"] > 0.95
    att = {"action": "tap", "step_id": "X1"}
    result = ctx.dispatcher.send(
        "tap", *loc["point"], preconditions=ctx.dispatcher.app_conditions("tap"), att=att,
        tap={"tree": tree, "loc": loc, "target": target,
             "target_ref": "fixture-full-view", "page": obs["page"],
             "state_deny": ctx.app.states.deny_for_page(obs["page"])})
    assert result.ok is allowed and result.sent is allowed
    event = _tap_events(ctx)[-1]
    assert event["sent"] is allowed
    if allowed:
        assert len(calls) == 1
        assert event["checks"]["tap"]["policy"]["allowed"]
        assert dev.current_screen() == "map2"
    else:
        assert calls == [] and att["status"] == "policy_denied"
        policy = event["checks"]["tap"]["policy"]
        assert not policy["allowed"]
        assert policy["deny"]["class"] == "confirmation_semantics_unknown"
        assert dev.current_screen() == "map"


@pytest.mark.parametrize("selector,allowed", [
    ({"kind": "text", "value": "确", "exact": False}, False),
    ({"kind": "text", "value": "O", "exact": False}, False),
    ({"kind": "text", "value": "", "exact": False}, False),
    ({"kind": "text", "value": "全", "exact": False}, False),
    ({"kind": "text", "value": "全览", "exact": True}, True),
    ({"kind": "text", "value": "全览"}, True),
], ids=["partial-confirm", "partial-ok", "empty-partial", "partial-navigation",
        "exact-navigation", "default-exact-navigation"])
def test_no_tree_partial_text_cannot_authorize_real_template_dispatch(
        ws, tmp_path, monkeypatch, selector, allowed):
    ctx, app, dev, obs, tree, target, _ = _no_tree_setup(ws, tmp_path)
    assert tree is None or tree.status != "ok"
    target["strategies"][0] = selector
    calls = _tap_spy(monkeypatch, dev)
    loc = resolve("fixture-full-view", target, tree, ctx.observer.screenshot_abs(obs), app.dir)
    assert loc["status"] == "found" and loc["basis"] == "template"
    assert loc["attempts"][0]["status"] == "skipped_no_tree" and loc["score"] > 0.95
    att = {"action": "tap", "step_id": "P1"}
    result = ctx.dispatcher.send(
        "tap", *loc["point"], preconditions=ctx.dispatcher.app_conditions("tap"), att=att,
        tap={"tree": tree, "loc": loc, "target": target,
             "target_ref": "fixture-full-view", "page": obs["page"],
             "state_deny": ctx.app.states.deny_for_page(obs["page"])})
    assert result.ok is allowed and result.sent is allowed
    event = _tap_events(ctx)[-1]
    assert event["sent"] is allowed
    if allowed:
        assert len(calls) == 1 and dev.current_screen() == "map2"
        assert event["checks"]["tap"]["policy"]["allowed"]
    else:
        assert calls == [] and att["status"] == "policy_denied"
        assert dev.current_screen() == "map"
        policy = event["checks"]["tap"]["policy"]
        assert not policy["allowed"] and policy["deny"]["class"] == "confirmation_semantics_unknown"


def test_tree_backed_partial_navigation_still_uses_observed_full_label(ws, tmp_path, monkeypatch):
    spec = _world(tmp_path, _home(_button("查看详情")), name="partial-navigation-with-tree")
    ctx = _ctx(ws, spec)
    calls = _tap_spy(monkeypatch, ctx.device)
    target = {"side_effect": "navigation", "strategies": [
        {"kind": "text", "value": "查看", "exact": False},
    ]}
    result = Executor(ctx).run_step({"id": "P2", "action": "tap", "retry": 0,
                                     "target": target}, "fixture")
    assert result["outcome"] == "passed" and len(calls) == 1
    event = _tap_events(ctx)[-1]
    assert event["sent"] is True and event["checks"]["tap"]["policy"]["allowed"]
    assert ctx.device.state["stack"][-1] == "detail"


def _benign_confirmation_fixture(ws, tmp_path, *, two=True, registered=None, parent_label=False,
                                 decorated_other=False):
    close = {"t": "Button", "id": "fixture-close", "click": True, "go": "detail",
             "b": [80, 500, 1240, 620], "children": [
                 {"t": "Text", "id": "fixture-close-label", "text": "确定", "b": [100, 520, 1220, 600]},
             ]}
    if parent_label:
        close["text"] = "确定"
    children = [{"t": "Text", "text": "离线提示", "b": [60, 320, 1260, 400]}, close]
    if two:
        other = {"t": "Button", "id": "fixture-other", "click": True, "go": "detail",
                 "b": [80, 700, 1240, 850], "children": [
                     {"t": "Text", "id": "fixture-other-label", "text": "确定", "b": [100, 735, 940, 810]},
                 ]}
        if decorated_other:
            other["children"].append({"t": "Image", "icon": "ego-view", "b": [980, 705, 1220, 845]})
        children.append(other)
    modal = {"t": "Dialog", "b": [40, 280, 1280, 1100], "children": children}
    spec = _world(tmp_path, _home(modal), name="benign-confirmation-binding")
    ctx = _ctx(ws, spec)
    ctx.app.states.states.clear()
    ctx.app.states.interrupts.clear()
    interrupt = {"class": "benign", "review": "reviewed", "require_text": ["离线提示"], "settle_s": 0}
    if registered:
        interrupt["dismiss_target"] = "fixture_info_ok"
        ctx.app.targets["fixture_info_ok"] = {
            "side_effect": "dismiss_dialog", "strategies": [
                {"kind": registered, "value": "fixture-close" if registered == "id" else "确定"},
            ]}
    else:
        interrupt["dismiss_text"] = "确定"
    ctx.app.states.interrupts["fixture_info"] = interrupt
    return ctx


def _dispatch_benign_fixture(ctx, target, *, target_ref="inline:fixture", point=None):
    obs, tree = ctx.observe("benign-confirmation-binding")
    loc = resolve(target_ref, target, tree, ctx.observer.screenshot_abs(obs), ctx.app.dir,
                  point=point, layout=obs.get("layout"))
    assert loc["status"] == "found"
    att = {"action": "tap", "step_id": "B1"}
    result = ctx.dispatcher.send(
        "tap", *loc["point"], preconditions=ctx.dispatcher.app_conditions("tap"), att=att,
        tap={"tree": tree, "loc": loc, "target": target, "target_ref": target_ref,
             "page": obs["page"], "state_deny": ctx.app.states.deny_for_page(obs["page"])})
    return result, att, loc


def _assert_benign_dispatch(ctx, result, att, calls, allowed):
    assert result.ok is allowed and result.sent is allowed
    event = _tap_events(ctx)[-1]
    assert event["sent"] is allowed
    if allowed:
        assert len(calls) == 1
        assert event["checks"]["tap"]["policy"]["allowed"]
    else:
        assert calls == [] and att["status"] == "policy_denied"
        policy = event["checks"]["tap"]["policy"]
        assert not policy["allowed"] and policy["deny"]["class"] == "confirmation_semantics_unknown"


@pytest.mark.parametrize("button_id", ["fixture-close", "fixture-other"])
def test_label_only_benign_source_cannot_choose_between_two_independent_same_label_buttons(
        ws, tmp_path, monkeypatch, button_id):
    ctx = _benign_confirmation_fixture(ws, tmp_path)
    ctx.authorized = {"vehicle_motion"}
    calls = _tap_spy(monkeypatch, ctx.device)
    target = parse_select(f"id={button_id}", side_effect="dismiss_dialog")
    result, att, _ = _dispatch_benign_fixture(ctx, target)
    _assert_benign_dispatch(ctx, result, att, calls, False)


def test_benign_label_parent_and_child_in_one_click_container_remain_a_unique_close(
        ws, tmp_path, monkeypatch):
    ctx = _benign_confirmation_fixture(ws, tmp_path, two=False, parent_label=True)
    calls = _tap_spy(monkeypatch, ctx.device)
    target = parse_select("id=fixture-close", side_effect="dismiss_dialog")
    result, att, _ = _dispatch_benign_fixture(ctx, target)
    _assert_benign_dispatch(ctx, result, att, calls, True)


@pytest.mark.parametrize("button_id,allowed", [("fixture-close", True), ("fixture-other", False)])
def test_registered_benign_id_close_binds_the_actual_button_even_when_reference_matches(
        ws, tmp_path, monkeypatch, button_id, allowed):
    ctx = _benign_confirmation_fixture(ws, tmp_path, registered="id")
    calls = _tap_spy(monkeypatch, ctx.device)
    target = parse_select(f"id={button_id}", side_effect="dismiss_dialog")
    result, att, _ = _dispatch_benign_fixture(ctx, target, target_ref="fixture_info_ok")
    _assert_benign_dispatch(ctx, result, att, calls, allowed)


def test_registered_duplicate_generic_text_cannot_authorize_agent_point_fallback(
        ws, tmp_path, monkeypatch):
    ctx = _benign_confirmation_fixture(ws, tmp_path, registered="text")
    calls = _tap_spy(monkeypatch, ctx.device)
    target = ctx.app.targets["fixture_info_ok"]
    result, att, loc = _dispatch_benign_fixture(ctx, target, target_ref="fixture_info_ok", point=[660, 775])
    assert loc["basis"] == "point" and loc["attempts"][0]["status"] == "ambiguous"
    _assert_benign_dispatch(ctx, result, att, calls, False)


def test_registered_duplicate_generic_text_cannot_authorize_real_template_fallback(
        ws, tmp_path, monkeypatch):
    ctx = _benign_confirmation_fixture(ws, tmp_path, registered="text", decorated_other=True)
    obs, _ = ctx.observe("benign-other-template")
    crop(ctx.observer.screenshot_abs(obs), [80, 700, 1240, 850],
         ctx.app.dir / "templates" / "fixture_other.png")
    target = ctx.app.targets["fixture_info_ok"]
    target["strategies"].append({"kind": "template", "file": "templates/fixture_other.png", "threshold": 0.95})
    calls = _tap_spy(monkeypatch, ctx.device)
    result, att, loc = _dispatch_benign_fixture(ctx, target, target_ref="fixture_info_ok")
    assert loc["basis"] == "template" and loc["attempts"][0]["status"] == "ambiguous"
    assert loc["score"] > 0.95
    _assert_benign_dispatch(ctx, result, att, calls, False)


def test_unique_benign_close_source_cannot_prove_an_authorized_controlled_effect(
        ws, tmp_path, monkeypatch):
    ctx = _benign_confirmation_fixture(ws, tmp_path, two=False)
    ctx.authorized = {"vehicle_motion"}
    calls = _tap_spy(monkeypatch, ctx.device)
    target = parse_select("id=fixture-close", side_effect="vehicle_motion")
    result, att, _ = _dispatch_benign_fixture(ctx, target)
    _assert_benign_dispatch(ctx, result, att, calls, False)


@pytest.mark.parametrize("point_mode", ["at", "random_point"])
def test_registered_benign_parent_cannot_authorize_a_generic_child_touch_receiver(
        ws, tmp_path, monkeypatch, point_mode):
    ctx = _benign_confirmation_fixture(ws, tmp_path, two=False, registered="id")
    # 内嵌的独立点击接收者覆盖父关闭容器；几何上仍在父节点范围内，但有自己的回调。
    parent = ctx.device.world["screens"]["home"]["root"]["children"][0]["children"][1]
    parent["children"].append({
        "t": "Button", "id": "fixture-independent-child", "click": True, "go": "detail",
        "b": list(parent["b"]), "children": [
            {"t": "Text", "text": "OK", "b": [100, 520, 1220, 600]},
        ],
    })
    calls = _tap_spy(monkeypatch, ctx.device)
    obs, tree = ctx.observe("benign-nested-touch-receiver")
    target = ctx.app.targets["fixture_info_ok"]
    loc = resolve("fixture_info_ok", target, tree, ctx.observer.screenshot_abs(obs), ctx.app.dir,
                  layout=obs.get("layout"))
    assert loc["status"] == "found" and loc["basis"] == "tree"
    step = {"id": "B2", "action": "tap", "retry": 0, "target": "fixture_info_ok"}
    step.update(at=[0.5, 0.5] if point_mode == "at" else None,
                random_point=point_mode == "random_point")
    executor = Executor(ctx)
    loc = {**loc, "point": executor._point_in(loc, step, 1, tree), "container_point": True}
    parent_node = tree.find_id("fixture-close")[0]
    receiver = tree.touch_target(*loc["point"])
    assert receiver.id == "fixture-independent-child" and receiver.idx != parent_node.idx
    assert tree.touch_check(*loc["point"], parent_node)["allowed"]
    att = {"action": "tap", "step_id": "B2"}
    result = executor._send_target("tap", loc, obs, tree, target, "fixture_info_ok", step, att)
    _assert_benign_dispatch(ctx, result, att, calls, False)
