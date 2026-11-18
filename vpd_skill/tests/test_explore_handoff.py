"""Exercise takeover boundaries without a physical device, gesture or notification."""
import json
from types import SimpleNamespace

import pytest

from conftest import world
from vpdtest import explore as explore_module
from vpdtest import workspace as workspace_module
from vpdtest.cli import main
from vpdtest.execute import Executor
from vpdtest.util import read_json
from vpdtest.workspace import RunContext


INSTRUCTION = "请核对当前页面，保留原操作结果"


def _explore_cli(ws, capsys, monkeypatch, stop=None, *, unknown=False, has_post=False, skip_first=False):
    calls = []
    monkeypatch.setattr(workspace_module, "load_config", lambda: {})
    observe = RunContext.observe

    def observe_before_takeover(self, *args, **kwargs):
        assert stop not in calls, "exploration observed the device after the handoff"
        return observe(self, *args, **kwargs)

    monkeypatch.setattr(RunContext, "observe", observe_before_takeover)
    candidates = [{"label": name, "target": name, "selected": False, "policy": {"allowed": True}}
                  for name in ("alpha", "beta")]
    monkeypatch.setattr(explore_module, "_candidates", lambda *_: candidates)

    def execute(self, step, case_id):
        sid = step["id"]
        calls.append(sid)
        if sid == stop:
            attempt = {"dispatch": {"sent": True if has_post else None}, "status": "device_error"}
            if has_post:
                attempt["post"] = {"observed": "car_tab"}
            return {"step_id": sid, "outcome": "error", "action_required": INSTRUCTION,
                    "effect_unknown": unknown, "final_status": "device_error", "final_obs": "obs-takeover",
                    "attempts": [attempt]}
        if step["action"] == "ensure_state":
            return {"step_id": sid, "outcome": "passed"}
        if skip_first and sid == "E1":
            return {"step_id": sid, "outcome": "blocked", "attempts": [{"status": "target_unresolved"}]}
        return {"step_id": sid, "outcome": "passed_after_recovery", "effect_unknown": True,
                "attempts": [{"status": "target_stale", "effect_unknown": True, "action_required": True},
                             {"status": "ok", "dispatch": {"sent": True}, "post": {"observed": "car_tab"},
                              "obs_before": "obs-from", "obs_after": "obs-to"}]}

    monkeypatch.setattr(Executor, "run_step", execute)
    argv = ["--workspace", str(ws.root), "explore", "--app", "motor_hm", "--start", "car_tab",
            "--device", world("motor_hm_base.json"), "--budget", "10"]
    code = main(argv)
    brief = json.loads(capsys.readouterr().out)
    directory = ws.runs_dir / brief["run_id"]
    return code, brief, calls, directory


@pytest.mark.parametrize(("stop", "unknown", "has_post", "phase", "calls", "completed"), [
    ("R0", False, False, "enter_state", ["R0"], 0),
    ("R1", True, False, "reset_candidate", ["R0", "R1"], 0),
    ("E1", True, False, "tap_candidate", ["R0", "R1", "E1"], 0),
    ("E1", False, True, "tap_candidate", ["R0", "R1", "E1"], 0),
    ("E2", True, True, "tap_candidate", ["R0", "R1", "E1", "R2", "E2"], 1),
])
def test_takeover_stops_and_registers_once(ws, capsys, monkeypatch, stop, unknown, has_post, phase, calls, completed):
    code, brief, actual, directory = _explore_cli(ws, capsys, monkeypatch, stop, unknown=unknown, has_post=has_post)
    saved = read_json(directory / "exploration.json")
    assert actual == calls and code == 2
    assert saved["action_required"] == brief["action_required"] == INSTRUCTION
    assert saved["exit"] == brief["exit"] == {
        "reason": "human_handoff", "state": "car_tab", "phase": phase, "step": stop, "source_step": stop,
        "effect_unknown": unknown, "final_status": "device_error", "final_obs": "obs-takeover"}
    assert len(saved["transitions"]) == len(brief["transitions"]) == completed
    assert INSTRUCTION in (directory / "report.md").read_text(encoding="utf-8")
    event, = brief["human_handoffs"]
    assert event["source"] == {"record": saved["run_id"]}
    files = list((ws.runs_dir / ".human-handoffs").glob("*.json"))
    assert len(files) == 1 and read_json(files[0]) == event
    for claim in ("acquired", "already_claimed"):
        assert main(["--workspace", str(ws.root), "handoff", event["event_id"], "--claim"]) == 0
        assert json.loads(capsys.readouterr().out)["claim"] == claim


def test_unresolved_candidate_without_takeover_is_still_skippable(ws, capsys, monkeypatch):
    code, brief, calls, _ = _explore_cli(ws, capsys, monkeypatch, skip_first=True)
    assert calls == ["R0", "R1", "E1", "R2", "E2"] and code == 0
    assert brief["exit"]["reason"] == "frontier_exhausted" and brief["skipped"] == 1
    assert len(brief["transitions"]) == 1
    assert "action_required" not in brief and "human_handoffs" not in brief
    assert not (ws.runs_dir / ".human-handoffs").exists()


@pytest.mark.parametrize(("mode", "safety", "stopped"), [
    ("explore", False, True), ("regression", False, False), ("explore", True, False),
])
def test_optional_recipe_child_keeps_original_source_and_mode_boundary(monkeypatch, mode, safety, stopped):
    flow = {"steps": [{"action": "wait", "critical": False}, {"action": "wait"}]}
    executor = Executor(SimpleNamespace(mode=mode, params=None, app=SimpleNamespace(flows={"inner": flow})))
    calls = []

    def child(step, case_id):
        calls.append(step["id"])
        if step["action"] == "flow":
            return executor._flow(step, case_id)
        result = {"step_id": step["id"], "outcome": "passed", "critical": step["critical"],
                  "attempts": [], "recoveries": [], "adapted": False}
        if step["id"] == "R1.1.1":
            result.update(outcome="blocked", reason="input missing", action_required=INSTRUCTION,
                          final_status="input_required", effect_unknown=False, final_obs="obs-child")
        return result

    monkeypatch.setattr(executor, "run_step", child)
    source = {"id": "R1", "state": "car_tab", "_safety": safety}
    children = [{"action": "flow", "flow": "inner", "critical": False}, {"action": "wait"}]
    result = executor._run_children(source, "explore", children, {"step_id": "R1", "action": "ensure_state"})
    if stopped:
        assert calls == ["R1.1", "R1.1.1"] and result["failure_source"] == "R1.1.1"
        exp = {}
        assert explore_module._stop_for_handoff(exp, result, "car_tab", "reset_candidate")
        assert exp["action_required"] == INSTRUCTION and exp["exit"]["source_step"] == "R1.1.1"
        assert exp["exit"]["effect_unknown"] is False
    else:
        assert calls == ["R1.1", "R1.1.1", "R1.1.2", "R1.2"] and "action_required" not in result
