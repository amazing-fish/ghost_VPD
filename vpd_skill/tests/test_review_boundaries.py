"""自动能力与会话入口使用同一执行契约；仅合成回放。"""
import ast
from pathlib import Path

import pytest

from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world, _start, cli
from vpdtest.commands import _run_brief
from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
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
