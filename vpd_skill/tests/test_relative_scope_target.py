"""Registered container references on synthetic dual Grid worlds; no phone fixtures."""
from copy import deepcopy
import shutil

import pytest

from test_dispatch_regression import _ctx
from test_goal_loop import _start, cli
from test_relative_swipe import _step, _world
from vpdtest import cases
from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.params import Params
from vpdtest.runner import run_case
from vpdtest.devices import DeviceResult
from vpdtest.session import from_session
from vpdtest.util import read_json, write_json


TARGET = "synthetic_lower_grid"


def _target(**extra):
    return {"label": "synthetic lower grid", "side_effect": "navigation",
            "strategies": [{"kind": "xpath", "value": "//Column[@id='lower']//Grid"}], **extra}


def _dual(tmp_path, ws):
    spec = _world(tmp_path, kind="Grid")
    data = read_json(spec.split(":", 1)[1])
    grid = data["screens"]["home"]["root"]["children"][0]
    grid.pop("id")
    grid["click"] = False
    lower = {"t": "Column", "id": "lower", "b": [100, 400, 900, 900], "children": [grid]}
    grid["b"] = [100, 400, 900, 900]
    upper = deepcopy(grid)
    upper["b"] = [100, 100, 900, 300]
    data["screens"]["home"]["root"]["children"] = [upper, lower]
    write_json(spec.split(":", 1)[1], data)
    p = ws.root / "apps" / "motor_hm" / "targets.json"
    targets = read_json(p)
    targets["targets"][TARGET] = _target()
    write_json(p, targets)
    return spec


def test_registered_scope_selects_only_lower_anonymous_grid(ws, tmp_path, monkeypatch):
    ctx = _ctx(ws, _dual(tmp_path, ws))
    calls, original = [], ctx.device.swipe

    def swipe(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(ctx.device, "swipe", swipe)
    out = Executor(ctx).run_step(_step(within={"target": TARGET}), "synthetic")
    assert out["outcome"] == "passed", out
    assert calls == [(260, 650, 739, 650, 800)]
    att = out["attempts"][-1]
    assert att["swipe"]["scope_target"]["target"] == TARGET
    assert att["swipe"]["scope_target"]["basis"] == "tree"


def test_broad_type_scope_still_refuses_two_grids(ws, tmp_path, monkeypatch):
    ctx = _ctx(ws, _dual(tmp_path, ws))
    monkeypatch.setattr(ctx.device, "swipe", lambda *a: pytest.fail("ambiguous scope must not dispatch"))
    out = Executor(ctx).run_step(_step(within={"type": "Grid"}), "synthetic")
    assert out["final_status"] == "target_ambiguous", out
    assert ctx.actions == 0


@pytest.mark.parametrize("target", [None, {}, {"strategies": []},
    _target(strategies=[{"kind": "template", "file": "never.png"}]),
    _target(strategies=[{"kind": "point", "value": [1, 2]}]),
    _target(strategies=[{"kind": "xpath", "value": ""}]),
    _target(side_effect="vehicle_control")])
def test_bad_scope_definition_stops_before_device_open(ws, monkeypatch, target):
    app = ws.app("motor_hm")
    if target is not None:
        app.targets[TARGET] = target
    monkeypatch.setattr(ws, "app", lambda _: app)
    monkeypatch.setattr(ws, "device", lambda *a, **kw: pytest.fail("must validate before opening device"))
    case = {"id": "TC-SYNTHETIC-SCOPE", "app": "motor_hm", "steps": [_step(within={"target": TARGET})]}
    with pytest.raises(ValueError, match="within|容器|范围"):
        run_case(ws, case=case, device_spec="hdc:must-not-open", propose=False)


@pytest.mark.parametrize("strategy,status", [
    ({"kind": "xpath", "value": "//Grid"}, "target_ambiguous"),
    ({"kind": "id", "value": "absent"}, "target_unresolved"),
    ({"kind": "xpath", "value": "//Column[@id='lower']"}, "target_unresolved"),
])
def test_unresolved_or_non_scroll_scope_never_dispatches(ws, tmp_path, monkeypatch, strategy, status):
    ctx = _ctx(ws, _dual(tmp_path, ws))
    ctx.app.targets[TARGET] = _target(strategies=[strategy])
    monkeypatch.setattr(ctx.device, "swipe", lambda *a: pytest.fail("invalid scope must not dispatch"))
    out = Executor(ctx).run_step(_step(within={"target": TARGET}), "synthetic")
    assert out["final_status"] == status, out
    assert ctx.actions == 0


def test_scope_definition_and_hypothesis_are_case_dependencies(ws, tmp_path):
    _dual(tmp_path, ws)
    app = ws.app("motor_hm")
    case = {"id": "TC-SYNTHETIC-SCOPE", "app": "motor_hm", "steps": [_step(within={"target": TARGET})]}
    knowledge = KnowledgeStore(ws.root)
    before = cases.dependency_hash(case, app, knowledge)
    app.targets[TARGET]["strategies"][0]["value"] = "//Grid"
    assert cases.dependency_hash(case, app, knowledge) != before
    app.targets[TARGET]["status"] = "hypothesis"
    assert TARGET in cases.hypothesis_targets(case, app, knowledge)


def test_cli_and_session_keep_registered_scope(ws, tmp_path):
    spec = _dual(tmp_path, ws)
    _start(ws, spec, "registered-scope")
    out = cli(ws, "act", "swipe", "--app", "motor_hm", "--session", "registered-scope",
              "--device", spec, "--within", "target=" + TARGET, "--start", "0.2,0.5",
              "--end", "0.8,0.5", "--speed", "800", "--timeout", "0")
    assert out["result"]["outcome"] == "passed", out
    case = from_session(ws.runs_dir / "session-registered-scope", "TC-SYNTHETIC-SCOPE",
                        ws.app("motor_hm"), "synthetic scope retention")
    assert case["steps"][1]["within"] == {"target": TARGET}


def test_missing_registered_scope_cli_rejects_before_device_or_session(ws, monkeypatch):
    monkeypatch.setattr(ws, "device", lambda *a, **kw: pytest.fail("definition must precede device"))
    with pytest.raises(ValueError, match="within|容器|范围"):
        cli(ws, "act", "swipe", "--app", "motor_hm", "--session", "never-created",
            "--device", "hdc:never", "--within", "target=missing", "--start", "0.2,0.5", "--end", "0.8,0.5")
    assert not (ws.runs_dir / "session-never-created").exists()


@pytest.mark.parametrize("shape", ["disabled", "not_scrollable", "foreign_overlay", "payment", "slider", "canvas"])
def test_registered_scope_keeps_path_and_node_safety_checks(ws, tmp_path, monkeypatch, shape):
    spec = _dual(tmp_path, ws)
    data = read_json(spec.split(":", 1)[1])
    roots = data["screens"]["home"]["root"]["children"]
    grid = roots[1]["children"][0]
    if shape == "disabled":
        grid["attrs"] = {"enabled": "false"}
    elif shape == "not_scrollable":
        grid["scroll"] = False
    elif shape == "foreign_overlay":
        roots.append({"t": "Button", "b": [450, 400, 460, 900], "text": "overlay", "click": True})
    else:
        grid["children"] = [{"t": {"payment": "Text", "slider": "Slider", "canvas": "Canvas"}[shape],
                             "b": [450, 400, 460, 900], "text": "支付" if shape == "payment" else ""}]
    write_json(spec.split(":", 1)[1], data)
    ctx = _ctx(ws, spec)
    monkeypatch.setattr(ctx.device, "swipe", lambda *a: pytest.fail("unsafe path must not dispatch"))
    out = Executor(ctx).run_step(_step(within={"target": TARGET}), "synthetic")
    assert out["outcome"] != "passed", out
    assert ctx.actions == 0


@pytest.mark.parametrize("gate", ["hold", "budget"])
def test_registered_scope_does_not_bypass_dispatch_gate(ws, tmp_path, monkeypatch, gate):
    ctx = _ctx(ws, _dual(tmp_path, ws))
    if gate == "hold":
        ctx.safety_hold = {"trigger": "synthetic held state"}
    else:
        ctx.actions = ctx.policy.max_actions_per_run
    calls = []
    monkeypatch.setattr(ctx.device, "swipe", lambda *a: calls.append(a))
    out = Executor(ctx).run_step(_step(within={"target": TARGET}), "synthetic")
    assert out["final_status"] == ("safety_hold" if gate == "hold" else "budget_exhausted"), out
    assert calls == []


def test_registered_scope_uses_fresh_tree_geometry(ws, tmp_path, monkeypatch):
    ctx = _ctx(ws, _dual(tmp_path, ws))
    real, observations, calls = ctx.observe, [], []

    def observe(*args, **kwargs):
        result = real(*args, **kwargs)
        observations.append(result[0]["obs_id"])
        if len(observations) == 1:
            scope = ctx.device.world["screens"]["home"]["root"]["children"][1]
            scope["b"] = [200, 400, 800, 900]
            scope["children"][0]["b"] = [200, 400, 800, 900]
        return result

    monkeypatch.setattr(ctx, "observe", observe)
    monkeypatch.setattr(ctx.device, "swipe", lambda *a: calls.append(a) or DeviceResult("swipe", True, True))
    out = Executor(ctx).run_step(_step(within={"target": TARGET}), "synthetic")
    assert out["outcome"] == "passed", out
    assert calls == [(320, 650, 679, 650, 800)]
    assert out["attempts"][-1]["swipe"]["obs"] == observations[-2]


def test_registered_scope_hypothesis_in_expanded_flow_and_cleanup(ws, tmp_path):
    _dual(tmp_path, ws)
    app = ws.app("motor_hm")
    app.targets[TARGET]["status"] = "hypothesis"
    app.flows["synthetic_scope"] = {"steps": [_step(within={"target": TARGET})]}
    case = {"app": "motor_hm", "steps": [{"id": "S1", "action": "flow", "flow": "synthetic_scope"}],
            "cleanup": [{**_step(within="target=" + TARGET), "id": "C1"}]}
    assert TARGET in cases.hypothesis_targets(case, app)
    assert any("hypothesis" in issue["problem"] for issue in cases.lint(case, app, KnowledgeStore(ws.root)))


def test_registered_scope_not_available_to_ordinary_direction_swipe(ws, monkeypatch):
    monkeypatch.setattr(ws, "device", lambda *a, **kw: pytest.fail("must reject before device"))
    with pytest.raises(ValueError, match="相对"):
        cli(ws, "act", "swipe", "--app", "motor_hm", "--session", "ordinary-scope",
            "--device", "hdc:never", "--within", "target=" + TARGET, "--direction", "up")


@pytest.mark.parametrize("session_app", [None, "motor_hm", "synthetic_app"])
def test_registered_scope_cli_omitted_app_uses_session_or_default(ws, tmp_path, monkeypatch, session_app):
    spec = _dual(tmp_path, ws)
    if session_app == "synthetic_app":
        shutil.copytree(ws.root / "apps" / "motor_hm", ws.root / "apps" / session_app)
        path = ws.root / "apps" / "motor_hm" / "targets.json"
        targets = read_json(path)
        targets["targets"].pop(TARGET)
        write_json(path, targets)
    name = "omitted-app"
    if session_app is not None:
        cli(ws, "act", "launch", "--app", session_app, "--session", name, "--device", spec)
    else:
        # A fresh session can begin while the app is already running on a device.
        # Use only replay here, and preserve the established motor_hm default.
        ws.config["app"] = "qiankun_hm"
        original = ws.device

        def opened_device(*args, **kwargs):
            device = original(*args, **kwargs)
            app = ws.app("motor_hm")
            device.launch(app.bundle, app.ability)
            return device

        monkeypatch.setattr(ws, "device", opened_device)
    out = cli(ws, "act", "swipe", "--session", name, "--device", spec,
              "--within", "target=" + TARGET, "--start", "0.2,0.5", "--end", "0.8,0.5", "--timeout", "0")
    assert out["result"]["outcome"] == "passed", out
    from vpdtest.session import trail
    assert trail(ws.runs_dir / ("session-" + name))["identity"]["app"] == (session_app or "motor_hm")


@pytest.mark.parametrize("existing", [False, True])
def test_missing_registered_scope_without_app_preserves_session_before_device(ws, tmp_path, monkeypatch, existing):
    name = "omitted-app-invalid"
    directory = ws.runs_dir / ("session-" + name)
    if existing:
        _start(ws, _dual(tmp_path, ws), name)
    before = {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    monkeypatch.setattr(ws, "device", lambda *a, **kw: pytest.fail("definition must precede device"))
    with pytest.raises(ValueError, match="within|容器|范围"):
        cli(ws, "act", "swipe", "--session", name, "--device", "hdc:never",
            "--within", "target=missing", "--start", "0.2,0.5", "--end", "0.8,0.5")
    after = {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert after == before
    assert directory.exists() == existing


@pytest.mark.parametrize("ancestor", ["parent", "grandparent"])
def test_registered_scope_rejects_hidden_nonclipping_ancestor(ws, tmp_path, monkeypatch, ancestor):
    spec = _dual(tmp_path, ws)
    data = read_json(spec.split(":", 1)[1])
    roots = data["screens"]["home"]["root"]["children"]
    if ancestor == "grandparent":
        roots[1] = {"t": "Column", "b": [0, 0, 1000, 1000], "children": [roots[1]]}
    roots[1]["attrs"] = {"visible": "false"}
    write_json(spec.split(":", 1)[1], data)
    ctx = _ctx(ws, spec)
    calls = []
    monkeypatch.setattr(ctx.device, "swipe", lambda *a: calls.append(a) or DeviceResult("swipe", True, True))
    out = Executor(ctx).run_step(_step(within={"target": TARGET}), "synthetic")
    assert calls == [], out
    assert out["final_status"] == "target_unresolved", out
    assert ctx.actions == 0


@pytest.mark.parametrize("entry", ["executor", "runner", "cli"])
def test_registered_scope_read_reference_cannot_fall_back(ws, tmp_path, monkeypatch, entry):
    spec = _dual(tmp_path, ws)
    target = _target(strategies=[{"kind": "xpath", "value": "//Grid[@id='${read:missing}']"},
                                 *_target()["strategies"]])
    path = ws.root / "apps" / "motor_hm" / "targets.json"
    targets = read_json(path)
    targets["targets"][TARGET] = target
    write_json(path, targets)
    if entry == "executor":
        ctx = _ctx(ws, spec)
        monkeypatch.setattr(ctx, "observe", lambda *a, **kw: pytest.fail("invalid definition must precede observation"))
        out = Executor(ctx).run_step(_step(within={"target": TARGET}), "synthetic")
        assert out["final_status"] == "invalid_step", out
        assert "read" in out["reason"]
        assert ctx.actions == 0
    else:
        monkeypatch.setattr(ws, "device", lambda *a, **kw: pytest.fail("invalid definition must precede device"))
        with pytest.raises(ValueError, match="read"):
            if entry == "runner":
                run_case(ws, case={"id": "TC-SYNTHETIC-READ-SCOPE", "app": "motor_hm",
                                  "steps": [_step(within={"target": TARGET})]}, device_spec=spec, propose=False)
            else:
                cli(ws, "act", "swipe", "--app", "motor_hm", "--session", "read-scope", "--device", spec,
                    "--within", "target=" + TARGET, "--start", "0.2,0.5", "--end", "0.8,0.5")
        if entry == "cli":
            assert not (ws.runs_dir / "session-read-scope").exists()


@pytest.mark.parametrize("kind", ["var", "param"])
@pytest.mark.parametrize("value", ["", "gesture_scope"])
def test_registered_scope_validates_expanded_value_without_disabling_parameters(ws, tmp_path, monkeypatch, kind, value):
    ctx = _ctx(ws, _world(tmp_path, kind="Grid"))
    ctx.app.targets[TARGET] = _target(strategies=[{"kind": "id", "value": "${" + kind + ":scope_id}",
                                                 "within": {"type": "Grid"}}])
    ctx.params = Params({"scope_id": value} if kind == "var" else {}, {}, "synthetic")
    calls = []
    monkeypatch.setattr(ctx.device, "swipe", lambda *a: calls.append(a) or DeviceResult("swipe", True, True))
    step = _step(within={"target": TARGET}, _flow_params={"scope_id": value} if kind == "param" else {})
    out = Executor(ctx).run_step(step, "synthetic")
    if value:
        assert out["outcome"] == "passed", out
        assert calls == [(260, 500, 739, 500, 800)]
    else:
        assert calls == [], out
        assert out["final_status"] == "invalid_step", out
        assert ctx.actions == 0
