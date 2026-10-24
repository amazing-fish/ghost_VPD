"""#58: relative gestures use actual controlled dispatch and replay transitions.

All worlds, rules and window changes are synthetic. HDC checks capture command
arguments only; none of these checks claim real-device calibration.
"""
from copy import deepcopy

import pytest

from test_dispatch_regression import _ctx
from test_goal_loop import BUNDLE, _lab_world, _start, cli
from vpdtest import cases
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.devices.replay import ReplayDevice
from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.session import from_session, trail
from vpdtest.util import read_json, read_jsonl, write_json


def _world(tmp_path, name="relative", *, kind="Scroll", scroll=True, children=None):
    spec = _lab_world(tmp_path, name)
    data = read_json(spec.split(":", 1)[1])
    data["screen"] = [1000, 1000]
    scope = {"t": kind, "id": "gesture_scope", "b": [100, 200, 900, 800],
             "scroll": scroll, "click": True, "children": children or []}
    data["screens"] = {
        "home": {"root": {"t": "Column", "b": [0, 0, 1000, 1000], "children": [scope]},
                 **{"on_swipe_" + d: "done_" + d for d in ("up", "down", "left", "right")}},
        **{"done_" + d: {"root": {"t": "Column", "b": [0, 0, 1000, 1000], "children": [
            {"t": "Text", "text": "RESULT_" + d, "b": [100, 100, 700, 200]}]}}
           for d in ("up", "down", "left", "right")},
    }
    write_json(spec.split(":", 1)[1], data)
    return spec


def _step(**extra):
    return {"id": "S1", "action": "swipe", "start": [0.2, 0.5], "end": [0.8, 0.5],
            "speed": 800, "within": {"id": "gesture_scope"}, "retry": 0,
            "timeout_s": 0, **extra}


@pytest.mark.parametrize("start,end,direction,expected", [
    ([0.2, 0.5], [0.8, 0.5], "right", (260, 500, 739, 500, 800)),
    ([0.8, 0.5], [0.2, 0.5], "left", (739, 500, 260, 500, 800)),
    ([0.5, 0.8], [0.5, 0.2], "up", (500, 679, 500, 320, 800)),
    ([0.5, 0.2], [0.5, 0.8], "down", (500, 320, 500, 679, 800)),
    ([0.1, 0.2], [0.9, 0.8], "right", (180, 320, 819, 679, 800)),
])
def test_relative_swipe_reaches_replay_direction_using_requested_points(
        ws, tmp_path, monkeypatch, start, end, direction, expected):
    ctx = _ctx(ws, _world(tmp_path))
    real, calls = ctx.device.swipe, []

    def swipe(*args):
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(ctx.device, "swipe", swipe)
    result = Executor(ctx).run_step(_step(start=start, end=end, expect_text="RESULT_" + direction), "relative")
    assert result["outcome"] == "passed", result
    assert calls == [expected]
    assert ctx.device.current_screen() == "done_" + direction
    att = result["attempts"][-1]
    assert att["swipe"]["relative"] is True
    assert att["swipe"]["start"] == start and att["swipe"]["end"] == end
    assert att["swipe"]["speed"] == 800
    assert att["swipe"]["window_rect"] == [0, 0, 1000, 1000]
    assert att["swipe"]["obs"]
    assert ctx.actions == 1 and att["dispatch"]["sent"] is True


def test_cli_session_candidate_lint_and_runner_preserve_relative_intent(ws, tmp_path, monkeypatch):
    spec = _world(tmp_path)
    _start(ws, spec, "relative-cli")
    calls, original = [], ReplayDevice.swipe

    def swipe(self, *args):
        calls.append(args)
        return original(self, *args)

    monkeypatch.setattr(ReplayDevice, "swipe", swipe)
    out = cli(ws, "act", "swipe", "--app", "motor_hm", "--session", "relative-cli",
              "--device", spec, "--start", "0.2,0.5", "--end", "0.8,0.5", "--speed", "800",
              "--within", "id=gesture_scope", "--expect-text", "RESULT_right", "--timeout", "0")
    assert out["result"]["outcome"] == "passed", out
    directory = ws.runs_dir / "session-relative-cli"
    candidate = from_session(directory, "TC-TEST-RELATIVE", ws.app("motor_hm"), "synthetic relative swipe")
    gesture = candidate["steps"][1]
    assert {k: gesture[k] for k in ("start", "end", "speed", "within")} == {
        "start": [0.2, 0.5], "end": [0.8, 0.5], "speed": 800, "within": {"id": "gesture_scope"}}
    assert "point" not in gesture and "direction" not in gesture
    knowledge = KnowledgeStore(ws.root)
    rule = "K-TEST-RELATIVE-SWIPE"
    knowledge.add({"id": rule, "kind": "product_rule", "subject": "pytest.relative",
                   "statement": "Synthetic right swipe reveals its synthetic result", "expected": "RESULT_right",
                   "applies_to": {"app": "motor_hm"},
                   "source": {"type": "reviewer", "ref": "test_relative_swipe.py synthetic contract"}})
    knowledge.confirm(rule, reviewer="pytest-synthetic", basis="synthetic transport contract only")
    candidate.update(purpose="Synthetic relative gesture reproduction", preconditions=["synthetic world"],
                     cleanup=[{"action": "stop_app"}], knowledge_used=[{"id": rule, "rationale": "S2 result"}],
                     assertions=[{"id": "A1", "at": "S2", "type": "text_present", "text": "RESULT_right",
                                  "oracle_refs": [rule]}])
    assert cases.lint(candidate, ws.app("motor_hm"), knowledge, ws.policy(), ws.runs_dir) == []
    run = run_case(ws, case=candidate, device_spec=spec, propose=False)
    assert run["steps"][1]["outcome"] == "passed", run
    assert calls == [(260, 500, 739, 500, 800)] * 2
    assert trail(directory)["actions"] == 2  # Launch plus one physical swipe.


@pytest.mark.parametrize("speed", [200, 40000])
def test_relative_dispatch_reaches_hdc_with_speed_in_pixels_per_second(ws, tmp_path, monkeypatch, speed):
    ctx = _ctx(ws, _world(tmp_path))
    hdc, commands = object.__new__(HdcDevice), []

    def shell(*args, **kwargs):
        commands.append((args, kwargs))
        return DeviceResult("swipe", True, True)

    monkeypatch.setattr(hdc, "_shell", shell)
    monkeypatch.setattr(ctx.device, "swipe", hdc.swipe)
    result = Executor(ctx).run_step(_step(start=[0, 0], end=[1, 1], speed=speed), "relative-hdc")
    assert result["outcome"] == "passed", result
    assert commands == [(("uitest", "uiInput", "swipe", 100, 200, 899, 799, speed), {"op": "swipe"})]
    assert ctx.actions == 1


@pytest.mark.parametrize("extra", [
    {"start": None}, {"end": None}, {"within": None},
    {"start": [0.1]}, {"end": [0.1, 0.2, 0.3]}, {"start": [True, 0.2]},
    {"start": [-0.1, 0.5]}, {"end": [1.1, 0.5]}, {"end": [float("nan"), 0.5]},
    {"start": [float("inf"), 0.5]}, {"speed": 199}, {"speed": 40001},
    {"speed": True}, {"speed": 800.5}, {"speed": "800"},
    {"direction": "up"}, {"random_point": True}, {"point": [100, 200]},
    {"point_basis": "visual intent"}, {"duration_s": 2},
])
def test_invalid_relative_definition_stops_before_observation_or_unlock(ws, tmp_path, monkeypatch, extra):
    ctx = _ctx(ws, _world(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail("malformed relative gestures must fail before device observation or action")

    monkeypatch.setattr(ctx, "observe", forbidden)
    monkeypatch.setattr(ctx.device, "swipe", forbidden)
    monkeypatch.setattr("vpdtest.unlock.unlock_device", forbidden)
    result = Executor(ctx).run_step(_step(**extra), "invalid-relative")
    assert result["final_status"] == "invalid_step", result
    assert ctx.actions == 0
    assert not any(e["event"] == "dispatch" for e in read_jsonl(ctx.store.events_path))


@pytest.mark.parametrize("options", [
    ["--start", "0.2,0.5", "--end", "0.8,0.5", "--speed", "199"],
    ["--start", "0.2", "--end", "0.8,0.5", "--speed", "800"],
    ["--start", "nan,0.5", "--end", "0.8,0.5", "--speed", "800"],
    ["--start", "0.2,0.5", "--end", "0.8,0.5", "--speed", "800", "--direction", "up"],
])
def test_cli_rejects_invalid_relative_arguments_before_opening_device(ws, monkeypatch, options):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid CLI gesture must not open a device")

    monkeypatch.setattr(ws, "device", forbidden)
    with pytest.raises((ValueError, SystemExit)):
        cli(ws, "act", "swipe", "--app", "motor_hm", "--session", "invalid-relative-cli",
            "--device", "hdc:never-contact", "--within", "id=gesture_scope", *options)
    assert not (ws.runs_dir / "session-invalid-relative-cli").exists()


@pytest.mark.parametrize("shape", ["ambiguous", "non_scrollable", "unknown_surface", "missing", "overlay", "controlled"])
def test_relative_gesture_requires_unique_current_scroll_scope_and_legal_path(ws, tmp_path, monkeypatch, shape):
    spec = _world(tmp_path)
    data = read_json(spec.split(":", 1)[1])
    nodes = data["screens"]["home"]["root"]["children"]
    if shape == "ambiguous":
        nodes.append(deepcopy(nodes[0]))
    elif shape == "non_scrollable":
        nodes[0]["scroll"] = False
    elif shape == "unknown_surface":
        nodes[0]["t"] = "XComponent"
    elif shape == "missing":
        nodes[0]["id"] = "another_scope"
    elif shape == "overlay":
        nodes.append({"t": "Button", "id": "foreign_overlay", "text": "面板", "click": True,
                      "b": [100, 200, 900, 800]})
    else:
        nodes[0]["children"].append({"t": "Text", "text": "支付", "b": [120, 400, 700, 600]})
    write_json(spec.split(":", 1)[1], data)
    ctx, calls = _ctx(ws, spec), []
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(), "scope-relative")
    assert result["outcome"] != "passed", result
    assert calls == [] and ctx.actions == 0


def test_relative_points_collapsing_to_same_pixel_do_not_send(ws, tmp_path, monkeypatch):
    ctx = _ctx(ws, _world(tmp_path))
    calls = []
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(start=[0.5, 0.5], end=[0.50001, 0.50001]), "tiny-relative")
    assert result["outcome"] != "passed", result
    assert calls == [] and ctx.actions == 0


@pytest.mark.parametrize("change,expected,window", [
    ("translation", (290, 550, 769, 550, 800), [30, 50, 1030, 1050]),
    ("fold_resize", (200, 500, 499, 500, 800), [0, 0, 700, 1000]),
])
def test_full_window_change_recomputes_relative_coordinates_from_current_scope(
        ws, tmp_path, monkeypatch, change, expected, window):
    ctx = _ctx(ws, _world(tmp_path))
    real, observed, calls = ctx.observe, [], []
    original_dump = ctx.device.dump_tree

    def dump_tree(bundle, local_path):
        result = original_dump(bundle, local_path)
        # Replay's outer orgRoot normally represents a fullscreen display. This
        # transport fixture models a translated app window with unchanged size.
        if change == "translation" and ctx.device.world["screens"]["home"]["root"]["b"][0] == 30:
            result.data["tree"]["attributes"]["bounds"] = "[30,50][1030,1050]"
            write_json(local_path, result.data["tree"])
        return result

    def observe(*args, **kwargs):
        out = real(*args, **kwargs)
        observed.append(out[0]["obs_id"])
        if len(observed) == 1:
            root = ctx.device.world["screens"]["home"]["root"]
            if change == "translation":
                root["b"] = [30, 50, 1030, 1050]
                root["children"][0]["b"] = [130, 250, 930, 850]
            else:
                root["b"] = [0, 0, 700, 1000]
                root["children"][0]["b"] = [100, 200, 600, 800]
                ctx.device.world["display"].update(device_form="foldable", fold_status="folded")
                ctx.device.world["screen"] = [700, 1000]
        return out

    monkeypatch.setattr(ctx, "observe", observe)
    monkeypatch.setattr(ctx.device, "dump_tree", dump_tree)
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(), "window-relative")
    assert len(observed) >= 2
    assert result["outcome"] == "passed", result
    assert calls == [expected] and ctx.actions == 1
    assert result["attempts"][-1]["swipe"]["window_rect"] == window
    assert result["attempts"][-1]["swipe"]["obs"] == observed[-2]


@pytest.mark.parametrize("gate", ["hold", "budget"])
def test_relative_swipe_keeps_existing_dispatch_gates(ws, tmp_path, monkeypatch, gate):
    ctx = _ctx(ws, _world(tmp_path))
    if gate == "hold":
        ctx.safety_hold = {"trigger": "synthetic stop not confirmed"}
    else:
        ctx.actions = ctx.policy.max_actions_per_run
    calls = []
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(), "gated-relative")
    assert result["final_status"] == ("safety_hold" if gate == "hold" else "budget_exhausted"), result
    assert calls == []


@pytest.mark.parametrize("sent", [True, None])
def test_relative_unknown_send_is_not_retried_even_if_idempotent_requested(ws, tmp_path, monkeypatch, sent):
    ctx = _ctx(ws, _world(tmp_path))
    calls = []

    def swipe(*args):
        calls.append(args)
        return DeviceResult("swipe", sent, False, "timeout", "synthetic acknowledgement lost")

    monkeypatch.setattr(ctx.device, "swipe", swipe)
    result = Executor(ctx).run_step(_step(retry=2, idempotent=True), "unknown-relative")
    assert calls == [(260, 500, 739, 500, 800)]
    assert len(result["attempts"]) == 1 and result["outcome"] != "passed"
    assert result["attempts"][0]["dispatch"]["sent"] is sent
    assert result.get("effect_unknown") or result.get("action_required")


@pytest.mark.parametrize("omitted", [True, False])
def test_relative_speed_keeps_existing_2000_default(ws, tmp_path, monkeypatch, omitted):
    ctx, calls = _ctx(ws, _world(tmp_path)), []
    step = _step(speed=None)
    if omitted:
        step.pop("speed")
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(step, "default-relative")
    assert result["outcome"] == "passed", result
    assert calls == [(260, 500, 739, 500, 2000)]
    assert result["attempts"][-1]["swipe"]["speed"] == 2000


@pytest.mark.parametrize("changed", ["lock", "foreground"])
def test_late_device_state_change_is_rechecked_without_unlocking_or_swiping(ws, tmp_path, monkeypatch, changed):
    ctx, calls = _ctx(ws, _world(tmp_path)), []
    original = ctx.dispatcher.send

    def send(op, *args, **kwargs):
        if op == "swipe":
            if changed == "lock":
                ctx.device.state["locked"] = True
            else:
                ctx.device.state["foreground"] = "synthetic.other.app"
        return original(op, *args, **kwargs)

    def unlock(*args, **kwargs):
        pytest.fail("freshly locked device must invalidate the relative geometry, not unlock then reuse it")

    monkeypatch.setattr(ctx.dispatcher, "send", send)
    monkeypatch.setattr("vpdtest.unlock.unlock_device", unlock)
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(), "late-state-relative")
    assert result["final_status"] == ("screen_locked" if changed == "lock" else "foreground_changed"), result
    assert calls == [] and ctx.actions == 0
    event = [e for e in read_jsonl(ctx.store.events_path) if e["event"] == "dispatch"][-1]
    assert event["sent"] is False and event["op"] == "swipe"


def _condition_ctx(ws, tmp_path):
    spec = _world(tmp_path, children=[{"t": "Text", "text": "INDEPENDENT", "b": [120, 220, 700, 280]}])
    ctx = _ctx(ws, spec)
    ctx.facts = {"app": "motor_hm", "app_version": "3.0.3.200-replay"}
    ctx.conditions = {"gate": {"kind": "page", "max_age_s": 1,
                               "true": {"type": "text_present", "text": "INDEPENDENT"},
                               "false": {"type": "text_present", "text": "COUNTER"}}}
    return ctx


def test_relative_current_condition_counterproof_stops_before_dispatch(ws, tmp_path, monkeypatch):
    ctx, calls, observations = _condition_ctx(ws, tmp_path), [], []
    real = ctx.observe

    def observe(*args, **kwargs):
        out = real(*args, **kwargs)
        observations.append(out[0]["obs_id"])
        if len(observations) == 2:
            ctx.device.world["screens"]["home"]["root"]["children"][0]["children"][0]["text"] = "COUNTER"
        return out

    monkeypatch.setattr(ctx, "observe", observe)
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(when={"condition": "gate", "equals": True}), "counter-relative")
    assert result["outcome"] != "passed", result
    assert calls == [] and ctx.actions == 0
    assert any((a.get("condition") or {}).get("status") == "false" for a in result["attempts"]), result


def test_relative_condition_ttl_is_rechecked_at_actual_dispatch(ws, tmp_path, monkeypatch):
    ctx, calls = _condition_ctx(ws, tmp_path), []
    real = ctx.dispatcher.send

    def send(op, *args, **kwargs):
        if op == "swipe":
            ctx.sleep(2)
        return real(op, *args, **kwargs)

    monkeypatch.setattr(ctx.dispatcher, "send", send)
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(when={"condition": "gate", "equals": True}), "ttl-relative")
    assert result["outcome"] == "pending", result
    assert calls == [] and ctx.actions == 0
    assert result["attempts"][-1]["condition"]["status"] == "unknown"


def test_interrupt_arriving_during_current_swipe_revalidation_does_not_crash_or_send(ws, tmp_path, monkeypatch):
    ctx, calls, observed = _ctx(ws, _world(tmp_path)), [], []
    ctx.app.states.interrupts["synthetic_arriving"] = {
        "require_text": ["SYNTHETIC_INTERRUPT"], "class": "unknown", "review": "pending"}
    original = ctx.observe

    def observe(*args, **kwargs):
        if observed:
            children = ctx.device.world["screens"]["home"]["root"]["children"]
            if not any(n.get("id") == "arrival" for n in children):
                children.append({"t": "Button", "id": "arrival", "text": "SYNTHETIC_INTERRUPT",
                                 "b": [100, 200, 900, 800], "click": True})
        out = original(*args, **kwargs)
        observed.append(out[0]["obs_id"])
        return out

    monkeypatch.setattr(ctx, "observe", observe)
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(retry=1), "interrupt-relative")
    assert result["outcome"] != "passed", result
    assert calls == [] and ctx.actions == 0
    assert result["final_status"] == "target_obscured"
    assert "中断" in result["attempts"][-1]["reason"]
    assert result["attempts"][-1]["swipe"]["obs"] == observed[-1]

