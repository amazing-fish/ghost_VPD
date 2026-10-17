"""Issue #53: unsupported swipe options must never become ordinary scrolling.

Only replay fixtures are used. These checks establish rejection and preservation
of historical intent; they do not claim fixed-point or timed swipe support.
"""
import pytest
from test_dispatch_regression import _ctx
from test_goal_loop import BUNDLE, _lab_world, _start, cli
from vpdtest import cases
from vpdtest.devices.replay import ReplayDevice
from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.session import from_session, trail
from vpdtest.util import append_jsonl, read_jsonl

UNSUPPORTED = [
    {"point": [640, 1660]},
    {"point": []},
    {"point": ""},
    {"basis": "sheet handle visible in screenshot"},
    {"basis": ""},
    {"point_window": [1320, 2848]},
    {"point_basis": "historic visual proposal"},
    {"duration": 2},
    {"duration": 0},
    {"duration_s": 2},
    {"duration_s": 0},
]


@pytest.mark.parametrize("options", [
    ["--point", "640,1660", "--basis", "sheet handle"],
    ["--point", ""],
    ["--basis", "sheet handle"],
    ["--basis", ""],
    ["--duration", "2"],
    ["--duration", "0"],
])
def test_cli_rejects_unsupported_swipe_without_observing_or_dispatching(ws, tmp_path, monkeypatch, options):
    spec = _lab_world(tmp_path)
    _start(ws, spec, "swipe-options")
    directory = ws.runs_dir / "session-swipe-options"
    before = read_jsonl(directory / "events.jsonl")
    obs_before = set((directory / "obs").iterdir())
    swipes = []
    real_swipe = ReplayDevice.swipe

    def swipe(self, *args):
        swipes.append(args)
        return real_swipe(self, *args)

    monkeypatch.setattr(ReplayDevice, "swipe", swipe)
    with pytest.raises(ValueError, match="swipe"):
        cli(ws, "act", "swipe", "--direction", "down", "--app", "motor_hm",
            "--session", "swipe-options", "--device", spec, *options)
    after = read_jsonl(directory / "events.jsonl")
    added = after[len(before):]
    assert swipes == []
    assert set((directory / "obs").iterdir()) == obs_before
    assert not any(e["event"] in ("dispatch", "authorization", "agent_proposal") for e in added)
    assert any(e["event"] == "act_rejected" for e in added)
    assert trail(directory)["actions"] == 1  # Only the initial launch consumed budget.


@pytest.mark.parametrize("extra", UNSUPPORTED)
@pytest.mark.parametrize("in_flow", [False, True], ids=["direct", "flow"])
def test_invalid_swipe_step_rejected_before_observe_unlock_and_budget(ws, tmp_path, monkeypatch, extra, in_flow):
    ctx = _ctx(ws, _lab_world(tmp_path))
    observed, unlocks, physical = [], [], []
    real_observe = ctx.observe

    def observe(*args, **kwargs):
        observed.append(args)
        return real_observe(*args, **kwargs)

    def unlock(*args, **kwargs):
        unlocks.append(True)
        return {"status": "missing_secret"}

    monkeypatch.setattr(ctx, "observe", observe)
    monkeypatch.setattr(ctx.device, "screen_locked", lambda: True)
    monkeypatch.setattr("vpdtest.unlock.unlock_device", unlock)
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: physical.append(args))
    invalid = {"id": "S1", "action": "swipe", "direction": "down", "retry": 0, **extra}
    if in_flow:
        ctx.app.flows["unsupported_swipe_fixture"] = {"params": {}, "steps": [invalid]}
        step = {"id": "F1", "action": "flow", "flow": "unsupported_swipe_fixture", "retry": 0}
    else:
        step = invalid
    result = Executor(ctx).run_step(step, "swipe-contract")
    assert result["outcome"] == "blocked" and result["attribution"] == "tool"
    assert result["attempts"][-1]["status"] == "invalid_step"
    assert observed == unlocks == physical == []
    assert ctx.actions == 0
    assert not any(e["event"] == "dispatch" for e in read_jsonl(ctx.store.events_path))


def _historical_session(ws, request):
    directory = ws.runs_dir / "session-historical-swipe"
    directory.mkdir(parents=True)
    identity = {"app": "motor_hm", "bundle": BUNDLE, "device": "replay:historic",
                "validation_level": "offline_replay"}
    events = [{"event": "session_open", "schema": 2, "identity": identity, "facts": identity}]
    for seq, req in ((1, {"action": "launch"}), (2, {"action": "swipe", "direction": "down", **request})):
        events.extend([
            {"event": "act_request", "seq": seq, "step_id": f"A{seq}", "request": req},
            {"event": "act_result", "seq": seq, "outcome": "passed", "dispatched": True},
        ])
    for event in events:
        append_jsonl(directory / "events.jsonl", {"ts": "2026-10-17T00:00:00Z", **event})
    return directory


@pytest.mark.parametrize("extra", UNSUPPORTED)
def test_historical_swipe_intent_cannot_be_silently_dropped_during_conversion(ws, extra):
    directory = _historical_session(ws, extra)
    with pytest.raises(ValueError) as error:
        from_session(directory, "TC-SWIPE-HIST", ws.app("motor_hm"), "historic swipe")
    message = str(error.value)
    assert "A2" in message and "--drop" in message and "swipe" in message
    with pytest.raises(ValueError, match="理由"):
        from_session(directory, "TC-SWIPE-HIST", ws.app("motor_hm"), "historic swipe", ["A2"])
    reason = "unsupported historical gesture; resample after capability implementation"
    candidate = from_session(directory, "TC-SWIPE-HIST", ws.app("motor_hm"),
                             "historic swipe", [f"A2={reason}"])
    assert [s["from_session"]["seq"] for s in candidate["steps"]] == [1]
    excluded = candidate["sources"][0]["excluded"]
    assert len(excluded) == 1 and excluded[0]["seq"] == 2 and excluded[0]["reason"] == reason


@pytest.mark.parametrize("extra", UNSUPPORTED)
@pytest.mark.parametrize("in_flow", [False, True], ids=["direct", "flow"])
def test_case_lint_rejects_unimplemented_swipe_contract_including_flow(ws, extra, in_flow):
    app = ws.app("motor_hm")
    step = {"id": "S1", "action": "swipe", "direction": "up", **extra}
    if in_flow:
        app.flows["unsupported_swipe_fixture"] = {"params": {}, "steps": [step]}
        step = {"id": "F1", "action": "flow", "flow": "unsupported_swipe_fixture"}
    candidate = ws.load_case("TC-MOTOR-HM-001")
    candidate["steps"] = [step]
    issues = cases.lint(candidate, app, KnowledgeStore(ws.root))
    assert any("swipe" in issue["problem"] for issue in issues)


@pytest.mark.parametrize("direction,expected", [
    ("up", {"from": [660, 1450], "to": [660, 1150]}),
    ("down", {"from": [660, 1150], "to": [660, 1450]}),
])
def test_plain_swipe_keeps_container_coordinates_and_one_action_budget(ws, tmp_path, direction, expected):
    ctx = _ctx(ws, _lab_world(tmp_path))
    result = Executor(ctx).run_step({"id": "S1", "action": "swipe", "direction": direction,
                                     "within": {"type": "List"}, "retry": 0}, "swipe-contract")
    assert result["outcome"] == "passed"
    attempt = result["attempts"][-1]
    assert {key: attempt["swipe"][key] for key in expected} == expected
    assert attempt["dispatch"]["sent"] is True and ctx.actions == 1


@pytest.mark.parametrize("source", ["steps", "cleanup", "flow", "safe_stop"])
def test_run_rejects_invalid_swipe_before_opening_device(ws, monkeypatch, source):
    candidate = ws.load_case("TC-MOTOR-HM-001")
    invalid = {"id": "BAD", "action": "swipe", "point": [640, 1660]}
    if source == "flow":
        app = ws.app("motor_hm")
        app.flows["invalid_swipe"] = {"params": {}, "steps": [invalid]}
        monkeypatch.setattr(ws, "app", lambda name: app)
        candidate["steps"] = [{"id": "F1", "action": "flow", "flow": "invalid_swipe"}]
    elif source == "safe_stop":
        candidate["safety"] = {"safe_stop": [invalid]}
    else:
        candidate[source] = [invalid]

    def open_device(*args, **kwargs):
        pytest.fail("invalid gesture definition must be rejected before device/preflight/unlock")

    monkeypatch.setattr(ws, "device", open_device)
    with pytest.raises(ValueError, match="swipe"):
        run_case(ws, case=candidate, propose=False)
    assert not list(ws.runs_dir.glob("run-*"))
