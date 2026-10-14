"""Issue #42: recovery must not duplicate an issued logical operation.

The fake transport records physical calls and can apply an operation while losing
its acknowledgement. Observer, targeting, policy, Dispatcher and run_step remain
real; these are transport-boundary regressions, not copies of _recover.
"""
from collections import Counter, defaultdict, deque

import pytest

from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.execute import Executor
from vpdtest.experience import ExperienceStore
from vpdtest.observe import RunStore
from vpdtest.states import StateModel
from vpdtest.util import read_jsonl, write_json
from vpdtest.workspace import RunContext


class CountingTransport(ReplayDevice):
    """An issued operation can take effect despite an error acknowledgement."""

    def __init__(self, world_path):
        super().__init__(world_path)
        self.failures = defaultdict(deque)
        self.calls = []
        self.issued = []
        self.hide_input_after_delete = False
        self.fail_observation_after_input = False
        self._failed_foreground_queries = 0

    def fail_next(self, op, *, sent=True, error_class="timeout"):
        self.failures[op].append((sent, error_class))

    def _physical(self, op, args, perform):
        self.calls.append((op, args))
        sent, error = self.failures[op].popleft() if self.failures[op] else (True, None)
        # Unknown sent metadata is deliberately conservative: it may have reached
        # the device. A definite False is the only case which cannot take effect.
        if sent is not False:
            self.issued.append((op, args))
            result = perform()
        else:
            result = DeviceResult(op, False, False)
        if error:
            return DeviceResult(result.op, sent, False, error, "fixture acknowledgement lost")
        return result

    def tap(self, x, y):
        return self._physical("tap", (x, y), lambda: super(CountingTransport, self).tap(x, y))

    def key(self, name):
        result = self._physical(f"key:{name}", (name,), lambda: super(CountingTransport, self).key(name))
        if name == "delete" and self.hide_input_after_delete:
            self.state["flags"]["input_gone"] = True
        return result

    def swipe(self, x1, y1, x2, y2, speed=2000):
        return self._physical("swipe", (x1, y1, x2, y2),
                              lambda: super(CountingTransport, self).swipe(x1, y1, x2, y2, speed))

    def input_text(self, x, y, text):
        result = self._physical("input_text", (x, y, text),
                                lambda: super(CountingTransport, self).input_text(x, y, text))
        if self.fail_observation_after_input:
            # Observer reads foreground at collection start and end. Fail both
            # reads in one observation, then let a later observation recover.
            self._failed_foreground_queries = 2
        return result

    def foreground(self):
        if self._failed_foreground_queries:
            self._failed_foreground_queries -= 1
            self.last_query_error = "transport_unavailable"
            return None
        return super().foreground()


def _context(ws, tmp_path):
    app = ws.app("motor_hm")
    app.states = StateModel({"states": {"retry_fixture": {"require_text": ["重试试验页"]}}}, app.bundle)
    world = {
        "bundle": app.bundle, "screen": [1320, 2848], "launch_screen": "fixture",
        "app_version": "0.0.0-retry-fixture", "initial": {"foreground": app.bundle},
        "display": {"density": 3.375, "device_form": "bar"},
        "screens": {"fixture": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "重试试验页", "b": [40, 100, 1280, 180]},
            {"t": "Button", "id": "operation", "text": "试验操作", "click": True, "b": [40, 300, 1280, 450]},
            {"t": "TextInput", "id": "name", "click": True, "bind": "name", "text_bound": "name",
             "hide_if_flag": "input_gone", "b": [40, 600, 1280, 750]},
        ]}}},
    }
    path = tmp_path / "retry_world.json"
    write_json(path, world)
    device = CountingTransport(path)
    device.launch(app.bundle, app.ability)
    device.bound["name"] = "旧值"
    store = RunStore(ws.runs_dir)
    ctx = RunContext(ws, app, device, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    return ctx


def _target(effect="navigation", *, input_box=False):
    target = {"label": "fixture", "strategies": [{"kind": "id", "value": "name" if input_box else "operation"}]}
    if effect is not None:
        target["side_effect"] = effect
    if input_box:
        target["allow_input"] = True
    return target


def _step(action="tap", effect="navigation", **extra):
    step = {"id": "S1", "action": action, "retry": 2, "timeout_s": 0, "expect_state": "@in_app"}
    if action in ("tap", "input"):
        step["target"] = _target(effect, input_box=action == "input")
    if action == "input":
        step["text"] = "新值"
    step.update(extra)
    return step


def _counts(device, *, issued=False):
    return Counter(op for op, _ in (device.issued if issued else device.calls))


def _dispatch_events(ctx):
    return [event for event in read_jsonl(ctx.store.events_path) if event["event"] == "dispatch"]


@pytest.mark.parametrize("error", ["timeout", "command_failed", "transport_unavailable"])
@pytest.mark.parametrize("action,op", [("input", "input_text"), ("back", "key:back"), ("swipe", "swipe")])
def test_issued_nonidempotent_step_has_one_physical_call(ws, tmp_path, action, op, error):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next(op, error_class=error)
    out = Executor(ctx).run_step(_step(action), "retry-fixture")
    assert _counts(ctx.device)[op] == 1
    assert _counts(ctx.device, issued=True)[op] == 1
    assert len(out["attempts"]) == 1
    assert not out["outcome"].startswith("passed")
    assert out["attempts"][0]["dispatch"]["sent"] is True
    assert out["first_failure"]["status"] == "device_error"


@pytest.mark.parametrize("effect", [None, "unknown_effect"])
@pytest.mark.parametrize("idempotent", [False, True])
def test_unknown_target_metadata_never_authorizes_reissue(ws, tmp_path, effect, idempotent):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next("tap")
    out = Executor(ctx).run_step(_step(effect=effect, idempotent=idempotent), "retry-fixture")
    assert _counts(ctx.device)["tap"] == 1
    assert len(out["attempts"]) == 1
    assert not out["outcome"].startswith("passed")


@pytest.mark.parametrize("sent", [None, 0, 1, "false"])
def test_unknown_sent_metadata_never_authorizes_reissue(ws, tmp_path, sent):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next("tap", sent=sent)
    out = Executor(ctx).run_step(_step(idempotent=True), "retry-fixture")
    assert _counts(ctx.device)["tap"] == 1
    assert len(out["attempts"]) == 1
    assert not out["outcome"].startswith("passed")


@pytest.mark.parametrize("idempotent", [None, "true", 1])
def test_navigation_needs_literal_true_idempotence(ws, tmp_path, idempotent):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next("tap")
    out = Executor(ctx).run_step(_step(idempotent=idempotent), "retry-fixture")
    assert _counts(ctx.device)["tap"] == 1
    assert len(out["attempts"]) == 1
    assert not out["outcome"].startswith("passed")


def test_random_navigation_point_does_not_gain_blind_retry(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next("tap")
    out = Executor(ctx).run_step(_step(idempotent=True, random_point=True), "retry-fixture")
    assert _counts(ctx.device)["tap"] == 1
    assert len(out["attempts"]) == 1
    assert not out["outcome"].startswith("passed")


@pytest.mark.parametrize("effect", ["vehicle_motion", "vehicle_control", "user_data"])
@pytest.mark.parametrize("failure", ["transport_error", "postcondition"])
def test_side_effectful_tap_cannot_claim_idempotence(ws, tmp_path, effect, failure):
    ctx = _context(ws, tmp_path)
    ctx.authorized = {effect}
    step = _step(effect=effect, idempotent=True)
    if failure == "transport_error":
        ctx.device.fail_next("tap")
    else:
        step["expect_text"] = "从未出现的后置条件"
    out = Executor(ctx).run_step(step, "retry-fixture")
    assert _counts(ctx.device)["tap"] == 1
    assert len(out["attempts"]) == 1
    assert not out["outcome"].startswith("passed")
    if effect == "vehicle_motion":
        assert ctx.motion.armed is not None


@pytest.mark.parametrize("error", ["timeout", "command_failed", "transport_unavailable"])
def test_definitely_unissued_operation_can_retry_once(ws, tmp_path, error):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next("input_text", sent=False, error_class=error)
    out = Executor(ctx).run_step(_step("input", retry=1), "retry-fixture")
    assert _counts(ctx.device)["input_text"] == 2
    assert _counts(ctx.device, issued=True)["input_text"] == 1
    assert ctx.actions == 1
    assert out["outcome"] == "passed_after_recovery"
    assert [a["dispatch"]["sent"] for a in out["attempts"]] == [False, True]
    assert out["first_failure"]["status"] == "device_error"


def test_definitely_unissued_retries_remain_bounded(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    for _ in range(5):
        ctx.device.fail_next("input_text", sent=False)
    out = Executor(ctx).run_step(_step("input", retry=99), "retry-fixture")
    assert _counts(ctx.device)["input_text"] == 3
    assert _counts(ctx.device, issued=True)["input_text"] == 0
    assert ctx.actions == 0
    assert len(out["attempts"]) == 3
    assert not out["outcome"].startswith("passed")


def test_definitely_unissued_clear_aggregate_can_retry(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next("tap", sent=False)
    out = Executor(ctx).run_step(_step("input", clear=True, retry=1), "retry-fixture")
    counts = _counts(ctx.device)
    assert counts["tap"] == 2
    assert _counts(ctx.device, issued=True)["tap"] == 1
    assert counts["key:move_end"] == counts["input_text"] == 1
    assert counts["key:delete"] == 4
    assert [a["dispatch"]["sent"] for a in out["attempts"]] == [False, True]
    assert out["outcome"] == "passed_after_recovery"


@pytest.mark.parametrize("failure", ["transport_error", "postcondition"])
def test_explicit_safe_navigation_idempotence_can_retry(ws, tmp_path, failure):
    ctx = _context(ws, tmp_path)
    step = _step(idempotent=True, retry=1)
    if failure == "transport_error":
        ctx.device.fail_next("tap")
    else:
        step["expect_text"] = "从未出现的后置条件"
    out = Executor(ctx).run_step(step, "retry-fixture")
    assert _counts(ctx.device)["tap"] == 2
    assert ctx.actions == 2
    assert len(out["attempts"]) == 2
    assert out["outcome"] == ("passed_after_recovery" if failure == "transport_error" else "fail_candidate")


@pytest.mark.parametrize("sent", [False, True])
def test_clear_aggregate_keeps_prior_tap_when_move_end_fails(ws, tmp_path, sent):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next("key:move_end", sent=sent)
    out = Executor(ctx).run_step(_step("input", clear=True, idempotent=True), "retry-fixture")
    counts = _counts(ctx.device)
    assert counts["tap"] == counts["key:move_end"] == 1
    assert counts["input_text"] == counts["key:delete"] == 0
    assert len(out["attempts"]) == 1
    assert out["attempts"][0]["dispatch"]["sent"] is True
    aggregate = [event for event in _dispatch_events(ctx) if event.get("aggregate")]
    assert len(aggregate) == 1 and aggregate[0]["sent"] is True


@pytest.mark.parametrize("sent", [False, True])
def test_clear_then_input_error_cannot_restart_the_whole_step(ws, tmp_path, sent):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_next("input_text", sent=sent)
    out = Executor(ctx).run_step(_step("input", clear=True, idempotent=True), "retry-fixture")
    counts = _counts(ctx.device)
    assert counts["tap"] == counts["key:move_end"] == counts["input_text"] == 1
    assert counts["key:delete"] == 4  # initial two characters plus the existing two-key margin
    assert len(out["attempts"]) == 1
    assert not out["outcome"].startswith("passed")
    aggregate = [event for event in _dispatch_events(ctx) if event.get("aggregate")]
    assert len(aggregate) == 1 and aggregate[0]["sent"] is True


def test_clear_then_relocation_failure_does_not_restart_the_whole_step(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    ctx.device.hide_input_after_delete = True
    out = Executor(ctx).run_step(_step("input", clear=True, idempotent=True), "retry-fixture")
    counts = _counts(ctx.device)
    assert counts["tap"] == counts["key:move_end"] == 1
    assert counts["key:delete"] == 4
    assert counts["input_text"] == 0
    assert len(out["attempts"]) == 1
    assert out["attempts"][0]["dispatch"]["sent"] is True


def test_input_post_observation_failure_does_not_reissue_accepted_text(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    ctx.device.fail_observation_after_input = True
    # Force business verification, so a failed tree read cannot mean completion.
    out = Executor(ctx).run_step(_step("input", expect_text="新值"), "retry-fixture")
    assert _counts(ctx.device)["input_text"] == 1
    assert len(out["attempts"]) == 1
    assert out["attempts"][0]["dispatch"]["sent"] is True
    assert out["first_failure"]["status"] == "observation_failed"
