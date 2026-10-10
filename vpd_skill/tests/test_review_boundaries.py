"""自动能力与会话入口使用同一执行契约；仅合成回放。"""
import ast
from pathlib import Path

import pytest

from conftest import world
from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world, _start, cli
from vpdtest.commands import _run_brief
from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.explore import explore
from vpdtest.session import parse_select
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.verdict import case_verdict


@pytest.mark.parametrize("status", ["missing_secret", "pin_rejected", "indeterminate"])
def test_preflight_unlock_failure_exposes_intervention(status):
    pre = {"ok": False, "checks": [{"check": "unlock_capability", "ok": False, "error_class": status}],
           "unlock": {"status": status, "action_required": True, "reason": "fixture-reason"}}
    verdict = case_verdict(pre, {"ok": True}, [], [], None)
    assert verdict["action_required"]
    assert verdict["alerts"][0]["kind"] == "unlock"
    brief = _run_brief({"verdict": verdict, "run_id": "fixture", "case": {"id": "fixture"}, "dir": "fixture",
                        "experience_candidates": [], "validation_level": "replay"})
    assert brief["action_required"] == verdict["action_required"]
    if status == "indeterminate":
        assert "fixture-reason" in verdict["action_required"]


@pytest.mark.parametrize("text", ["详情页", "不会出现的文字"])
def test_monitor_text_expectation_executes_and_survives_conversion(ws, tmp_path, text):
    spec = _lab_world(tmp_path)
    _start(ws, spec)
    data = read_json(spec.split(":", 1)[1])
    data["screens"]["home"]["after_obs"] = {"n": 1, "go": "detail", "replace": True}
    write_json(spec.split(":", 1)[1], data)
    out = cli(ws, "act", "monitor", "--app", "motor_hm", "--device", spec, "--session", "s1",
              "--expect-text", text, "--duration", "2", "--interval", "0.5")
    att = out["result"]["attempts"][-1]
    if text == "详情页":
        assert att["monitor"]["stopped_by"] == "until_text:详情页"
        assert att["monitor"]["samples"] >= 2
        assert att["monitor"]["duration_s"] < 2
    else:
        assert att["status"] == "postcondition_not_met"
    cli(ws, "case", "from-session", "--session", "s1", "--new-id", "TC-TEXT-FIX", "--app", "motor_hm")
    step = ws.load_case("TC-TEXT-FIX")["steps"][1]
    assert step["until_text"] == text and "expect_text" not in step


@pytest.mark.parametrize("action", ["tap", "ensure_state"])
def test_remaining_budget_allows_one_dismissal_and_no_requested_action(ws, tmp_path, action):
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.policy.max_actions_per_run = 1
    ctx.actions = 0
    for iid in ("popup1", "popup2"):
        ctx.app.states.interrupts[iid] = {"dismiss_text": "唯一入口", "settle_s": 0}
    observe = ctx.observe
    def dialogs(*args, **kwargs):
        obs, tree = observe(*args, **kwargs)
        obs["page"]["interrupts"] = [{"id": iid, "class": "benign", "review": "reviewed"}
                                    for iid in ("popup1", "popup2")[ctx.actions:]]
        return obs, tree
    ctx.observe = dialogs
    taps = []
    ctx.device.tap = lambda *p: taps.append(p) or DeviceResult("tap", True, True)
    out = Executor(ctx).run_step({"id": "A1", "action": action, "target": parse_select("text=唯一入口"), "state": "home_tab",
                                  "retry": 0}, "fixture")
    assert len(taps) == ctx.actions == 1
    assert out["final_status"] == "budget_exhausted"
    assert out["dismissed"][-1]["gate"] == "budget_exhausted"
    events = [e for e in read_jsonl(ctx.store.events_path) if e["event"] == "dispatch"]
    assert any(e["error_class"] == "budget_exhausted" and not e["sent"] for e in events)


def test_safety_navigation_bypasses_exhausted_budget(ws, tmp_path):
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.policy.max_actions_per_run = ctx.actions = 0
    ctx.motion.max_s = 1
    ctx.motion.arm("fixture", "fixture")
    ctx._virtual_s += 2
    out = Executor(ctx).run_step({"id": "SAFE1", "action": "back", "_safety": True, "retry": 0}, "fixture")
    assert out["outcome"] == "passed" and ctx.actions == 1
    event = next(e for e in read_jsonl(ctx.store.events_path) if e["event"] == "dispatch")
    assert event["checks"]["budget"]["exempt"] is True
    assert event["checks"]["motion"]["exempt"] is True and event["checks"]["motion"]["over"] is True


def test_clear_text_counts_individual_keys_and_stops_before_budget_overrun(ws, tmp_path):
    from vpdtest.dispatch import clear_text
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.policy.max_actions_per_run = 3
    result = clear_text(ctx.device, 50, 230, 5, ctx.dispatcher, ctx.dispatcher.app_conditions("input"))
    assert result.sent and not result.ok and result.error_class == "budget_exhausted"
    assert ctx.actions == 3
    events = [e for e in read_jsonl(ctx.store.events_path) if e["event"] == "dispatch"]
    assert len([e for e in events if e["sent"]]) == 3
    assert not events[-1]["sent"]


def test_motion_timeout_is_checked_after_relocation(ws, tmp_path):
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.motion.max_s = 1
    ctx.motion.arm("fixture", "fixture")
    taps = []
    ctx.device.tap = lambda *p: taps.append(p) or DeviceResult("tap", True, True)
    dispatcher = ctx.dispatcher
    dispatcher._tap_check = lambda tap: {"status": "target_obscured" if not tap.get("refreshed") else "ok"}
    def refresh():
        ctx._virtual_s += 2
        return {"refreshed": True, "loc": {"point": [10, 20]}}
    result = dispatcher.send("tap", 10, 20, preconditions={}, tap={"loc": {}}, refresh=refresh)
    assert not result.sent and result.error_class == "motion_timeout" and taps == []


def test_action_count_has_one_owner():
    root = Path(__file__).resolve().parents[1] / "vpdtest"
    writes = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Attribute) and node.target.attr == "actions":
                writes.append(path.name)
    assert writes == ["dispatch.py"]


def test_failed_but_sent_action_consumes_budget(ws, tmp_path):
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.policy.max_actions_per_run = 1
    ctx.device.key = lambda name: DeviceResult("key", True, False, "timeout")
    first = ctx.dispatcher.send("key", "back", preconditions={})
    second = ctx.dispatcher.send("key", "back", preconditions={})
    assert first.sent and not first.ok and ctx.actions == 1
    assert not second.sent and second.error_class == "budget_exhausted"


def test_budget_exhaustion_during_automatic_unlock_rejects_original_action(ws, tmp_path):
    spec = _lab_world(tmp_path)
    ctx = _ctx(ws, spec)
    ctx.device.state["locked"] = True
    ctx.device.world["initial"]["lock_pin"] = "246810"
    ctx.policy.max_actions_per_run = 1
    ws.config["unlock_secrets"] = {spec: "LOCK_PIN_TEST"}
    ws.config["secrets"] = {"LOCK_PIN_TEST": "246810"}
    att = {"action": "back"}
    result = ctx.dispatcher.send("key", "back", preconditions=ctx.dispatcher.app_conditions("back"), att=att)
    assert ctx.actions == 1 and not result.sent and result.error_class == "budget_exhausted"
    events = [e for e in read_jsonl(ctx.store.events_path) if e["event"] == "dispatch"]
    assert [e["op"] for e in events if e["sent"]] == ["wake"]


# ---- 运动超时不止在 monitor 里检查：wait 和后置条件等待期间同样要拦 ----

def test_wait_during_motion_ends_as_motion_timeout(ws, tmp_path):
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.motion.max_s = 1
    ctx.motion.arm("fixture", "fixture")
    out = Executor(ctx).run_step({"id": "W1", "action": "wait", "duration_s": 5, "retry": 0}, "fixture")
    assert out["final_status"] == "motion_timeout" and out["outcome"] != "passed"
    assert out["attempts"][-1]["waited_s"] < 5


def test_stop_observed_after_timeout_does_not_hide_breach(ws, tmp_path):
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.motion.max_s, ctx.motion.stopped = 1, {"lab_done"}
    ctx.motion.arm("fixture", "fixture", "lab_parking")
    observe = ctx.observer.observe
    def late_stop(label="", screenshot=True):
        obs, tree = observe(label, screenshot)
        if label.endswith(":after"):  # 等后置条件时才第一次看到停止，此时已超过 max_motion_s
            ctx._virtual_s += 2
            obs["page"].update(state="lab_done", active=["lab_done"])
        return obs, tree
    ctx.observer.observe = late_stop
    out = Executor(ctx).run_step({"id": "W1", "action": "wait", "duration_s": 0.5, "retry": 0}, "fixture")
    assert ctx.motion.armed is None and not ctx.motion.over()  # 计时已解除
    assert ctx.motion.history[-1]["late"] is True
    assert out["final_status"] == "motion_timeout"


# ---- 监视期间进程被杀要识别为进程死亡（页面在 APP 内时也要记下存活状态）----

def test_monitor_detects_process_death_inside_app(ws, tmp_path):
    spec = _lab_world(tmp_path)
    _start(ws, spec)
    # after_obs 计数在会话里累计（launch 已经观测过），从当前计数往后数：先在 APP 内采样两次，第 3 次时进程已死
    seen = (read_json(ws.runs_dir / "session-s1" / "replay_state.json").get("tick") or [None, 0])[1]
    path = spec.split(":", 1)[1]
    data = read_json(path)
    data["screens"]["home"]["after_obs"] = {"n": seen + 3, "go": "@crash"}
    write_json(path, data)
    out = cli(ws, "act", "monitor", "--app", "motor_hm", "--device", spec, "--session", "s1",
              "--duration", "4", "--interval", "0.5")
    att = out["result"]["attempts"][-1]
    assert att["status"] == "anomaly" and att["monitor"]["stopped_by"] == "process_died"
    assert att["monitor"]["states"][0] != "out_of_app"  # 死之前确实在 APP 内采样过


# ---- 会话的动作预算跨 act 进程累计 ----

def test_session_action_budget_accumulates_across_acts(ws, tmp_path):
    pol = read_json(ws.root / "policy.json")
    pol["max_actions_per_run"] = 2
    write_json(ws.root / "policy.json", pol)
    spec = _lab_world(tmp_path)
    _start(ws, spec)  # launch：第 1 个动作
    first = cli(ws, "act", "tap", "--select", "text=唯一入口", "--app", "motor_hm", "--session", "s1", "--device", spec)
    assert first["result"]["attempts"][-1]["dispatch"]["sent"]
    second = cli(ws, "act", "back", "--app", "motor_hm", "--session", "s1", "--device", spec)
    assert second["result"]["final_status"] == "budget_exhausted"
    events = read_jsonl(ws.runs_dir / "session-s1" / "events.jsonl")
    assert len([e for e in events if e["event"] == "dispatch" and e["sent"]]) == 2


# ---- suite 与 run 同样传递授权 ----

def test_suite_passes_authorization_to_cases(ws):
    write_json(ws.root / "suites" / "qk_auth.json", {"id": "qk_auth", "desc": "fixture", "cases": ["TC-QK-102"]})
    dev = world("qiankun_hm_ready.json")
    bare = cli(ws, "suite", "qk_auth", "--device", dev)["results"][0]
    granted = cli(ws, "suite", "qk_auth", "--device", dev, "--authorize", "vpd_entry", "user_data", "route_planning")
    assert bare["verdict"] == "blocked"
    run = read_json(Path(granted["results"][0]["report"]).parent / "run.json")
    assert run["authorized"] == ["route_planning", "user_data", "vpd_entry"]
    assert granted["results"][0]["verdict"] != "blocked"


# ---- 探索：没下发或没有点击后观测的点击不记成迁移 ----

def test_explore_does_not_record_unsent_taps_as_transitions(ws, monkeypatch):
    locate = Executor._locate
    def unresolved(self, tid, target, obs, tree, step, scroll_ok=True):
        if step["id"].startswith("E"):
            return {"status": "not_found", "target": tid, "attempts": []}, obs, tree
        return locate(self, tid, target, obs, tree, step, scroll_ok)
    monkeypatch.setattr(Executor, "_locate", unresolved)
    exp = explore(ws, "motor_hm", "car_tab", world("motor_hm_base.json"), budget=60)
    assert exp["transitions"] == []
    assert any(s.get("reason") == "未下发或无点击后观测：target_unresolved" for s in exp["skipped"])
