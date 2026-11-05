"""Synthetic replay contracts for the ordinary swipe's explicit scope.

These tests establish dispatch containment, not a work-machine reproduction.
"""
import pytest

from test_dispatch_regression import _ctx
from test_goal_loop import _start, cli
from test_relative_swipe import _world
from vpdtest.devices.replay import ReplayDevice
from vpdtest.execute import Executor
from vpdtest.session import trail
from vpdtest.util import read_json, read_jsonl, write_json


def _scope_world(tmp_path, shape):
    spec = _world(tmp_path, kind="Grid", scroll=False)
    data = read_json(spec.split(":", 1)[1])
    root = data["screens"]["home"]["root"]
    scope = root["children"][0]
    child = {"t": "List", "id": "inner", "b": [200, 300, 800, 700], "scroll": True}
    if shape == "missing":
        root["children"] = []
    elif shape == "ancestor":
        root["scroll"] = True
    elif shape == "sibling":
        root["children"].append({**child, "b": [0, 0, 100, 200]})
    elif shape == "horizontal":
        scope["t"], scope["scroll"] = "Swiper", True
    elif shape == "clipped":
        scope["children"] = [{**child, "b": [200, 900, 800, 990]}]
    elif shape == "nested":
        scope["children"] = [child]
    write_json(spec.split(":", 1)[1], data)
    return spec


def _plain(**extra):
    return {"id": "S1", "action": "swipe", "direction": "up",
            "within": {"id": "gesture_scope"}, "retry": 0, "timeout_s": 0, **extra}


@pytest.mark.parametrize("shape", ["non_scrollable", "missing", "ancestor", "sibling", "horizontal", "clipped"])
def test_explicit_non_scrollable_scope_never_dispatches_window_swipe(ws, tmp_path, monkeypatch, shape):
    ctx = _ctx(ws, _scope_world(tmp_path, shape))
    calls, real = [], ctx.device.swipe

    def swipe(*args):
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(ctx.device, "swipe", swipe)
    result = Executor(ctx).run_step(_plain(), "explicit-scope")
    assert calls == [], result
    assert result["outcome"] == ("error" if shape == "missing" else "blocked")  # Preserve existing missing-scope attribution.
    assert ctx.actions == 0
    att = result["attempts"][-1]
    assert att["dispatch"]["sent"] is False
    assert att["dispatch"]["error_class"] == ("scroll_scope_missing" if shape == "missing" else "scroll_container_missing")
    assert "fallback" not in att["swipe"] and "from" not in att["swipe"]
    assert not any(e["event"] == "dispatch" for e in read_jsonl(ctx.store.events_path))


@pytest.mark.parametrize("direction,expected", [("up", (500, 600, 500, 400)), ("down", (500, 400, 500, 600))])
def test_scoped_nested_container_preserves_coordinates(ws, tmp_path, monkeypatch, direction, expected):
    ctx = _ctx(ws, _scope_world(tmp_path, "nested"))
    calls, real = [], ctx.device.swipe

    def swipe(*args):
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(ctx.device, "swipe", swipe)
    result = Executor(ctx).run_step(_plain(direction=direction), "explicit-scope")
    assert result["outcome"] == "passed", result
    assert calls == [expected] and ctx.actions == 1
    assert result["attempts"][-1]["swipe"]["container"]["id"] == "inner"


@pytest.mark.parametrize("scrollable,expected", [(False, (500, 750, 500, 250)), (True, (500, 650, 500, 350))])
def test_unscoped_swipe_preserves_container_and_window_fallback(ws, tmp_path, monkeypatch, scrollable, expected):
    ctx = _ctx(ws, _world(tmp_path, kind="Grid", scroll=scrollable))
    calls, real = [], ctx.device.swipe

    def swipe(*args):
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(ctx.device, "swipe", swipe)
    step = _plain()
    step.pop("within")
    result = Executor(ctx).run_step(step, "unscoped")
    assert result["outcome"] == "passed", result
    assert calls == [expected] and ctx.actions == 1
    assert result["attempts"][-1]["swipe"].get("fallback") == (None if scrollable else "window")


@pytest.mark.parametrize("tree_state", [None, "parse_error"])
@pytest.mark.parametrize("scoped", [False, True])
def test_missing_current_tree_only_allows_legacy_unscoped_last_window(ws, tmp_path, monkeypatch, tree_state, scoped):
    from types import SimpleNamespace

    ctx = _ctx(ws, _world(tmp_path))
    ctx.observer.last_window = (0, 0, 1000, 1000)
    calls, real = [], ctx.device.swipe

    def swipe(*args):
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(ctx.device, "swipe", swipe)
    result, info = Executor(ctx)._swipe("up", tree=SimpleNamespace(status=tree_state) if tree_state else None,
                                       within={"id": "gesture_scope"} if scoped else None)
    assert calls == ([] if scoped else [(500, 750, 500, 250)])
    assert result.sent is (not scoped) and ctx.actions == (0 if scoped else 1)
    if scoped:
        assert result.error_class == "scroll_scope_unavailable"
        assert "fallback" not in info
    else:
        assert info["fallback"] == "last_window"


def test_cli_type_scope_rejection_keeps_session_budget(ws, tmp_path, monkeypatch):
    spec = _scope_world(tmp_path, "non_scrollable")
    _start(ws, spec, "scoped-plain-cli")
    calls, real = [], ReplayDevice.swipe

    def swipe(self, *args):
        calls.append(args)
        return real(self, *args)

    monkeypatch.setattr(ReplayDevice, "swipe", swipe)
    out = cli(ws, "act", "swipe", "--app", "motor_hm", "--session", "scoped-plain-cli",
              "--device", spec, "--direction", "up", "--within", "type=Grid", "--timeout", "0")
    assert calls == [], out
    assert out["result"]["outcome"] == "blocked"
    assert out["result"]["final_status"] == "scroll_container_missing"
    assert trail(ws.runs_dir / "session-scoped-plain-cli")["actions"] == 1  # Initial launch only.


@pytest.mark.parametrize("shape", ["non_scrollable", "missing"])
def test_locator_scroll_respects_scope_without_swipe_or_tap(ws, tmp_path, monkeypatch, shape):
    ctx = _ctx(ws, _scope_world(tmp_path, shape))
    calls = []
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(("swipe", args)))
    monkeypatch.setattr(ctx.device, "tap", lambda *args: calls.append(("tap", args)))
    target = {"label": "synthetic missing", "side_effect": "navigation",
              "strategies": [{"kind": "id", "value": "absent"}],
              "scroll": {"direction": "up", "within": {"id": "gesture_scope"}, "max": 3}}
    result = Executor(ctx).run_step({"id": "S1", "action": "tap", "target": target,
                                     "retry": 0, "timeout_s": 0}, "scoped-locator")
    assert calls == [], result
    assert result["outcome"] == ("error" if shape == "missing" else "blocked") and ctx.actions == 0
    if shape != "missing":
        assert result["final_status"] == "scroll_container_missing"
    scroll = result["attempts"][-1]["locate"]["scroll"]
    assert len(scroll) == 1 and scroll[0]["sent"] is False
    assert scroll[0]["error_class"] == ("scroll_scope_missing" if shape == "missing" else "scroll_container_missing")
