"""Synthetic host continuation using real CLI/Executor; no automated identity inference."""
from copy import deepcopy
import pytest
from test_goal_loop import _lab_world, _start, cli
from vpdtest.util import read_json, read_jsonl, write_json


def rig(ws, tmp_path, label="新入口", duplicate=False, destination=True):
    dev = _lab_world(tmp_path, "renamed")
    path = dev.split(":", 1)[1]
    world = read_json(path)
    button = {"t": "Button", "id": "stable-entry", "b": [40, 400, 1280, 520], "click": True,
              "children": [{"t": "Text", "text": label, "b": [60, 420, 1260, 500]}]}
    if destination: button["go"] = "detail"
    children = [button]
    if duplicate:
        other = deepcopy(button)
        other["b"] = [40, 600, 1280, 720]
        other["children"][0]["b"] = [60, 620, 1260, 700]
        children.append(other)
    world["screens"]["home"]["root"] = {
        "t": "Column", "id": "home-panel", "b": [0, 0, 1320, 2848], "children": children}
    write_json(path, world)
    states_path = ws.app("motor_hm").dir / "states.json"
    states = read_json(states_path)
    states["states"]["independent_detail"] = {"require_text": ["详情页"]}
    write_json(states_path, states)
    _start(ws, dev, "drift")
    return ["--app", "motor_hm", "--session", "drift", "--device", dev]


def test_host_continues_renamed_navigation_preserving_failure(ws, tmp_path):
    args = rig(ws, tmp_path)
    failed = cli(ws, "act", "tap", "--select", "text=旧入口", *args)
    assert failed["diagnosis"]["status"] == "target_unresolved"
    assert not failed["dispatched"] and not failed["effect_unknown"]
    assert "asset-drift.md" in failed["diagnosis"]["hint"]
    observation = cli(ws, "observe", "--label", "原步骤改名核对", *args)
    assert any(x["id"] == "stable-entry" for x in observation["clickable"])
    result = cli(ws, "act", "tap", "--select", "id=stable-entry", "--within", "id=home-panel",
                 "--expect", "independent_detail", *args)
    assert result["result"]["outcome"] == "passed"
    assert result["result"]["attempts"][-1]["revalidate"]["result"] == "same"
    records = read_jsonl(ws.runs_dir / "session-drift" / "steps.jsonl")
    assert records[-2]["result"]["final_status"] == "target_unresolved"
    assert records[-1]["result"]["outcome"] == "passed"
    assert not list(ws.runs_dir.glob("run-*"))


@pytest.mark.parametrize("duplicate,label", [(True, "新入口"), (False, "开始泊车")])
def test_continuation_preserves_ambiguity_and_policy(ws, tmp_path, duplicate, label):
    args = rig(ws, tmp_path, label=label, duplicate=duplicate)
    result = cli(ws, "act", "tap", "--select", "id=stable-entry", "--within", "id=home-panel",
                 "--expect", "independent_detail", *args)
    assert result["result"]["outcome"] == "blocked"
    assert not result["dispatched"]
    assert result["diagnosis"]["status"] in ("target_ambiguous", "policy_denied")


def test_relocated_tap_without_postcondition_is_not_success(ws, tmp_path):
    args = rig(ws, tmp_path, destination=False)
    result = cli(ws, "act", "tap", "--select", "id=stable-entry", "--within", "id=home-panel",
                 "--expect", "independent_detail", "--timeout", "0", *args)
    assert result["dispatched"]
    assert result["result"]["final_status"] == "postcondition_not_met"
    assert result["result"]["outcome"] not in ("passed", "passed_after_adaptation", "passed_after_recovery")


def test_continuation_preserves_same_session_budget(ws, tmp_path):
    args = rig(ws, tmp_path)
    path = ws.root / "policy.json"
    policy = read_json(path)
    policy["max_actions_per_run"] = 1
    write_json(path, policy)
    result = cli(ws, "act", "tap", "--select", "id=stable-entry", "--within", "id=home-panel",
                 "--expect", "independent_detail", *args)
    assert result["result"]["final_status"] == "budget_exhausted"
    assert not result["dispatched"]


def test_adaptation_does_not_override_safety_hold(ws, tmp_path, monkeypatch):
    from vpdtest import commands
    args = rig(ws, tmp_path)
    original = commands._open_session
    def held(*a, **kw):
        app, store, dev, ctx = original(*a, **kw)
        ctx.safety_hold = {"reason": "synthetic unconfirmed stop"}
        return app, store, dev, ctx
    monkeypatch.setattr(commands, "_open_session", held)
    result = cli(ws, "act", "tap", "--select", "id=stable-entry", "--within", "id=home-panel",
                 "--expect", "independent_detail", *args)
    assert result["result"]["final_status"] == "safety_hold"
    assert not result["dispatched"] and result["action_required"]


def test_unknown_send_retains_handoff_not_a_successful_adaptation(ws, tmp_path, monkeypatch):
    from vpdtest.devices import DeviceResult
    from vpdtest.devices.replay import ReplayDevice
    args = rig(ws, tmp_path)
    monkeypatch.setattr(ReplayDevice, "tap", lambda *a: DeviceResult("tap", None, False, "timeout", "synthetic"))
    result = cli(ws, "act", "tap", "--select", "id=stable-entry", "--within", "id=home-panel",
                 "--expect", "independent_detail", *args)
    assert result["effect_unknown"] and result["action_required"]
    assert result["result"]["outcome"] != "passed"


@pytest.fixture
def failed_run(ws, tmp_path):
    from vpdtest.runner import run_case
    dev = _lab_world(tmp_path, "run-drift")
    case = {"id": "DRIFT-SYNTHETIC", "app": "motor_hm", "version": 1, "status": "draft",
            "steps": [{"id": "LAUNCH", "action": "launch", "retry": 0},
                      {"id": "MISSING", "action": "tap", "target": {
                          "label": "old", "side_effect": "navigation",
                          "strategies": [{"kind": "text", "value": "旧入口"}]}, "retry": 0}],
            "assertions": [], "cleanup": []}
    write_json(ws.case_path(case["id"]), case)
    return ws, case, dev, run_case(ws, case=case, device_spec=dev, propose=False)


def test_actual_run_cli_exposes_bound_readonly_host_route(failed_run):
    ws, case, dev, run = failed_run
    out = cli(ws, "run", case["id"], "--device", dev)
    followup = out["host_followup"]
    assert followup["run_id"] == out["run_id"]
    assert followup["steps"][0]["step"] == "MISSING"
    assert followup["steps"][0]["final_obs"]
    assert followup["next"] == "observe_only"
    assert followup["action_authorized"] is False
    assert followup["run_budget_inherited"] is False
    assert out["verdict"] == run["verdict"]["verdict"]


@pytest.mark.parametrize("change", ["passed", "not_run", "wrong_status", "wrong_obs", "recovered", "composite"])
def test_run_route_does_not_guess_recovered_or_unbound_evidence(failed_run, change):
    from vpdtest.commands import _run_brief
    run = deepcopy(failed_run[-1])
    step = run["steps"][-1]
    if change in ("passed", "not_run"): step["outcome"] = change
    elif change == "wrong_status": step["final_status"] = "policy_denied"
    elif change == "wrong_obs": step["final_obs"] = "obs-unrelated"
    elif change == "recovered": step["recoveries"] = [{"after_attempt": step["attempts"][-1]["n"]}]
    else: step["sub_steps"] = [deepcopy(step)]
    assert "host_followup" not in _run_brief(run)


@pytest.mark.parametrize("risk", ["handoff", "hold", "cleanup_unknown"])
def test_run_host_route_cannot_cover_handoff(failed_run, risk):
    from vpdtest.commands import _run_brief
    run = deepcopy(failed_run[-1])
    if risk == "handoff": run["verdict"]["action_required"] = "synthetic handoff"
    elif risk == "hold": run["safety_hold"] = {"reason": "synthetic"}
    else: run["cleanup"] = [{"effect_unknown": True}]
    out = _run_brief(run)
    assert out["host_followup"]["next"] == "inspect"
    assert out["host_followup"]["requires_human"] is True
    assert out["action_required"] == run["verdict"].get("action_required")


def test_actual_runner_postcondition_failure_is_routed(failed_run):
    from vpdtest.runner import run_case
    from vpdtest.commands import _run_brief
    ws, case, dev, _ = failed_run
    states_path = ws.app("motor_hm").dir / "states.json"
    states = read_json(states_path)
    states["states"]["unmet_independent_state"] = {"require_text": ["合成未达终态"]}
    write_json(states_path, states)
    case["steps"][-1] = {"id": "WAIT", "action": "wait", "expect_state": "unmet_independent_state",
                           "timeout_s": 0, "duration_s": 0, "retry": 0}
    run = run_case(ws, case=case, device_spec=dev, propose=False)
    out = _run_brief(run)
    assert out["host_followup"]["steps"][0]["status"] == "postcondition_not_met"
    assert out["host_followup"]["steps"][0]["final_obs"] == run["steps"][-1]["final_obs"]


def test_actual_sent_tap_handoff_still_reaches_host(failed_run):
    from vpdtest.runner import run_case
    from vpdtest.commands import _run_brief
    ws, case, dev, _ = failed_run
    states_path = ws.app("motor_hm").dir / "states.json"
    states = read_json(states_path)
    states["states"]["unmet"] = {"require_text": ["合成未达终态"]}
    write_json(states_path, states)
    step = case["steps"][-1]
    step["target"]["strategies"][0]["value"] = "唯一入口"
    step.update(expect_state="unmet", timeout_s=0, retry=1)
    run = run_case(ws, case=case, device_spec=dev, propose=False)
    out = _run_brief(run)
    assert run["steps"][-1]["recoveries"][-1]["performed"] is False
    assert out["host_followup"]["next"] == "inspect"
    assert out["host_followup"]["requires_human"] is True
    assert out["action_required"]
