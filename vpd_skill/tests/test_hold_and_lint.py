"""未确认停止的执行暂停、完整步骤来源的 lint；仅合成设备与临时资产。"""
import copy
from pathlib import Path

import pytest

from test_motion_safety import lab, _case, _run, _variant
from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world
from vpdtest.cases import lint
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import read_json, read_jsonl, write_json


@pytest.mark.parametrize("confirmed", [False, True])
def test_cleanup_waits_for_confirmed_stop(lab, tmp_path, confirmed):
    device = _variant(tmp_path, "hold_stuck", parking_in={"after_obs": None})
    if not confirmed:
        path = lab.root / "apps" / "motor_hm" / "targets.json"
        data = read_json(path)
        data["targets"]["lab_pause"]["strategies"][0]["value"] = "fixture_missing_pause"
        write_json(path, data)
    safety = {"safe_stop": [{"action": "tap", "target": "lab_pause", "expect_state": "lab_paused", "timeout_s": 0}],
              "max_motion_s": 60, "stopped_states": ["lab_paused", "lab_done", "lab_vpd_home"]}
    case = _case([{"id": "S2", "action": "tap", "target": "lab_start", "expect_state": "lab_parking"}],
                 safety=safety, cleanup=[{"id": "C1", "action": "back"}, {"id": "C2", "action": "stop_app"}])
    run = _run(lab, case, dev=device)
    assert run["cleanup"] and run["safety_stops"]
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    dispatches = [e for e in events if e["event"] == "dispatch" and e.get("step") in ("C1", "C2")]
    if confirmed:
        assert all(c["outcome"] == "passed" for c in run["cleanup"])
        assert len(dispatches) == 2 and all(e["sent"] for e in dispatches)
    else:
        assert all(c["outcome"] == "not_run" and c["final_status"] == "safety_hold" for c in run["cleanup"])
        assert len(dispatches) == 2 and all(not e["sent"] and e["error_class"] == "safety_hold" for e in dispatches)
        assert run["verdict"]["verdict"] == "blocked" and run["verdict"]["action_required"]
        assert {x["id"] for x in run["verdict"]["not_verified"]} >= {"C1", "C2"}
        assert "安全停止未确认，清理暂停，需人工接管" in (Path(run["dir"]) / "report.md").read_text("utf-8")
        assert not any(f["kind"] == "cleanup" and f["result"] == "blocked" for f in run["verdict"]["findings"])


def test_hold_blocks_all_normal_dispatch_and_allows_safety(ws, tmp_path):
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.safety_hold = {"trigger": "fixture", "ts": "fixture"}
    for op, args in [("key", ("back",)), ("wake", ()), ("swipe", (10, 20, 30, 40))]:
        result = ctx.dispatcher.send(op, *args, preconditions={})
        assert not result.sent and result.error_class == "safety_hold"
    result = ctx.dispatcher.send("key", "back", preconditions=ctx.dispatcher.app_conditions("back"),
                                 att={"action": "back", "_safety": True})
    assert result.sent and result.ok
    assert ctx.safety_hold["trigger"] == "fixture"


@pytest.mark.parametrize("source,action", [("steps", "input"), ("cleanup", "tap"), ("safe_stop", "tap"), ("flow", "input")])
def test_lint_checks_registered_targets_in_every_source(ws, source, action):
    case = copy.deepcopy(ws.load_case("TC-MOTOR-HM-001"))
    app = ws.app(case["app"])
    step = {"id": "X1", "action": action, "target": "fixture_missing", "text": "fixture"}
    if source == "safe_stop":
        case["safety"] = {"safe_stop": [step]}
    elif source == "flow":
        case["steps"] = [{"id": "F1", "action": "flow", "flow": "fixture_flow"}]
        app.flows["fixture_flow"] = {"steps": [step]}
    else:
        case[source] = [step]
    issues = lint(case, app, KnowledgeStore(ws.root), ws.policy())
    assert any("fixture_missing" in i["problem"] and i["field"].startswith(
        "steps.F1" if source == "flow" else f"{source}.X1") for i in issues)


def test_lint_checks_safe_stop_pixels(ws):
    case = copy.deepcopy(ws.load_case("TC-MOTOR-HM-001"))
    case["safety"] = {"safe_stop": [{"id": "X1", "action": "tap", "point": [10, 20]}]}
    issues = lint(case, ws.app(case["app"]), KnowledgeStore(ws.root), ws.policy())
    assert any(i["field"] == "safe_stop.X1" and "像素坐标" in i["problem"] for i in issues)


def test_lint_reports_recursive_flow_without_losing_other_children(ws):
    case = copy.deepcopy(ws.load_case("TC-MOTOR-HM-001"))
    app = ws.app(case["app"])
    app.flows["fixture_cycle"] = {"steps": [
        {"id": "LOOP", "action": "flow", "flow": "fixture_cycle"},
        {"id": "BAD", "action": "input", "target": "fixture_missing", "text": "fixture"},
    ]}
    case["cleanup"] = [{"id": "C1", "action": "flow", "flow": "fixture_cycle"}]
    issues = lint(case, app, KnowledgeStore(ws.root), ws.policy())
    assert any("递归引用" in i["problem"] and i["field"].startswith("cleanup.C1") for i in issues)
    assert any("fixture_missing" in i["problem"] and i["field"].endswith(".BAD") for i in issues)


def test_later_confirmed_stop_does_not_release_run_hold(ws, tmp_path):
    from vpdtest.execute import Executor
    from vpdtest.safety import safe_stop

    ctx = _ctx(ws, _lab_world(tmp_path))
    hold = {"trigger": "earlier_stop_unconfirmed", "ts": "fixture"}
    ctx.safety_hold = hold
    stopped = safe_stop(Executor(ctx), "fixture_later_confirmed")
    assert stopped["confirmed_stopped"]
    assert ctx.safety_hold is hold
    rejected = ctx.dispatcher.send("key", "back", preconditions={})
    assert not rejected.sent and rejected.error_class == "safety_hold"
