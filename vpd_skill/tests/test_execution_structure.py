"""One structural contract before a case opens its device or a selected flow runs."""
import pytest
from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world
from vpdtest import cases
from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_jsonl


def _cycle(app, kind):
    flow = {"action": "flow", "flow": "cycle"}
    recipe = {"action": "ensure_state", "state": "cycle"}
    if kind == "flow":
        app.flows["cycle"] = {"steps": [{"action": "launch"}, flow]}
        return {"id": "F", **flow}
    app.flows["cycle"] = {"steps": [{"action": "launch"}, recipe]}
    app.states.recipes["cycle"] = [flow]
    return {"id": "R", **recipe}


@pytest.mark.parametrize("shape", ["duplicate", "cleanup", "safe_stop", "flow", "recipe", "child_id", "terminal_id"])
def test_invalid_structure_rejected_before_device_creation(ws, monkeypatch, shape):
    case = ws.load_case("TC-MOTOR-HM-001")
    app = ws.app(case["app"])
    case.update(steps=[{"id": "S1", "action": "launch"}], assertions=[], cleanup=[])
    if shape == "duplicate":
        case["steps"].append({"id": "S1", "action": "wait"})
    elif shape == "cleanup":
        case["steps"][0]["id"] = "C1"
        case["cleanup"] = [{"action": "wait"}]
    elif shape == "safe_stop":
        case["steps"][0]["id"] = "SAFE1"
        case["safety"] = {"safe_stop": [{"action": "wait"}]}
    elif shape in ("child_id", "terminal_id"):
        app.flows["single"] = {"steps": [{"id": "LOCAL", "action": "wait", "duration_s": 0}]}
        app.states.recipes["single"] = app.flows["single"]["steps"]
        case["steps"] = [
            {"id": "S1", "action": "flow", "flow": "single"} if shape == "child_id" else
            {"id": "S1", "action": "ensure_state", "state": "single"},
            {"id": "S1.1" if shape == "child_id" else "S1.2", "action": "wait", "duration_s": 0},
        ]
    else:
        case["steps"].append(_cycle(app, shape))
    monkeypatch.setattr(ws, "app", lambda _: app)

    def unexpected(*args, **kwargs):
        pytest.fail("invalid structure must not create a device or run preflight/unlock")

    monkeypatch.setattr(ws, "device", unexpected)
    with pytest.raises(ValueError, match="重复|递归"):
        run_case(ws, case=case, propose=False, run_id="invalid-structure")
    assert not (ws.runs_dir / "invalid-structure").exists()
    issues = cases.lint(case, app, KnowledgeStore(ws.root))
    assert any("重复" in x["problem"] or "递归" in x["problem"] for x in issues)


@pytest.mark.parametrize("kind", ["flow", "recipe"])
def test_selected_recursive_composite_stops_before_first_child(ws, tmp_path, monkeypatch, kind):
    ctx = _ctx(ws, _lab_world(tmp_path))
    step = _cycle(ctx.app, kind)

    def unexpected(*args, **kwargs):
        pytest.fail("recursive definition must not observe or execute its valid first child")

    monkeypatch.setattr(ctx, "observe", unexpected)
    monkeypatch.setattr(ctx.device, "launch", unexpected)
    result = Executor(ctx).run_step(step, "structural-contract")
    assert result["outcome"] == "blocked" and result["attribution"] == "tool"
    assert result["final_status"] == "invalid_step" and "递归" in result["reason"]
    assert ctx.actions == 0
    assert not any(e["event"] == "dispatch" for e in read_jsonl(ctx.store.events_path))


def test_structure_failure_does_not_hide_safety_hold(ws, tmp_path, monkeypatch):
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.safety_hold = {"trigger": "previous_unconfirmed_stop"}
    step = _cycle(ctx.app, "flow")
    monkeypatch.setattr(ctx, "observe", lambda *a: pytest.fail("held action must not observe"))
    result = Executor(ctx).run_step(step, "held-structure")
    assert result["final_status"] == "safety_hold" and result["action_required"]
    assert ctx.actions == 0


def test_reused_flow_and_local_child_ids_remain_valid(ws, monkeypatch):
    case = ws.load_case("TC-MOTOR-HM-001")
    app = ws.app(case["app"])
    app.flows["reusable"] = {"steps": [
        {"id": "LOCAL", "action": "wait", "duration_s": 0},
        {"id": "LOCAL", "action": "wait", "duration_s": 0},
    ]}
    case.update(steps=[{"id": "F1", "action": "flow", "flow": "reusable"},
                       {"id": "F2", "action": "flow", "flow": "reusable"}], assertions=[], cleanup=[])
    monkeypatch.setattr(ws, "app", lambda _: app)
    run = run_case(ws, case=case, device_spec="replay:motor_hm_base.json", propose=False)
    assert [s["outcome"] for s in run["steps"]] == ["passed", "passed"]
    assert [[s["step_id"] for s in parent["sub_steps"]] for parent in run["steps"]] == [
        ["F1.1", "F1.2"], ["F2.1", "F2.2"]]
    assert run["informal"] is True


def test_already_reached_state_needs_no_recipe(ws, monkeypatch):
    case = ws.load_case("TC-MOTOR-HM-001")
    app = ws.app(case["app"])
    app.states.recipes.pop("car_tab", None)
    case.update(steps=[{"id": "S1", "action": "launch"},
                       {"id": "S2", "action": "ensure_state", "state": "car_tab"}], assertions=[], cleanup=[])
    monkeypatch.setattr(ws, "app", lambda _: app)
    run = run_case(ws, case=case, device_spec="replay:motor_hm_base.json", propose=False)
    assert run["steps"][1]["outcome"] == "passed"
    assert not run["steps"][1]["attempts"] and not run["steps"][1].get("sub_steps")
