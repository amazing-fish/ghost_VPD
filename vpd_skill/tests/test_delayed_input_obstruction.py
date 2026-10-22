"""Synthetic menu timing through Runner, Dispatcher and production login assets."""
from copy import deepcopy
from pathlib import Path

import pytest
from test_issue64_login_paths import (
    _password_flow,
    _password_world,
    _run_world,
    _sent,
    login_rig,
)

from vpdtest import cases
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

__all__ = ("login_rig",)


def delayed_world(rig, **kwargs):
    spec = _password_world(rig, **kwargs)
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    for name in ("phone", "password"):
        data["screens"]["autofill_" + name]["root"]["children"][0]["go"] = "delay_" + name
        # Manual-button postcheck and input's condition sampling see no menu.
        # The input attempt's own observation then sees the delayed overlay.
        data["screens"]["delay_" + name] = {
            "kind": "popup", "root": {"t": "Column", "b": [0, 0, 1, 1]},
            "after_obs": {"n": 2, "go": "selection_" + name}}
    write_json(path, data)
    return spec


def recovery_inputs(run):
    return [s for s in _password_flow(run)["sub_steps"] if s["action"] == "input"]


def test_delayed_menu_is_recovered_once_before_each_secret_input(login_rig):
    run = _run_world(login_rig, delayed_world(login_rig))
    assert all(s["outcome"].startswith("passed") for s in run["steps"]), run["steps"]
    inputs = recovery_inputs(run)
    assert len(inputs) == 2
    for step in inputs:
        assert step["outcome"] == "passed_after_recovery", step
        assert [a["status"] for a in step["attempts"]] == ["target_obscured", "ok"]
        assert step["attempts"][0]["dispatch"]["sent"] is False
        assert step["first_failure"]["status"] == "target_obscured"
        assert len(step["recoveries"]) == 1
        rec = step["recoveries"][0]
        assert rec["status"] == "dismissed" and rec["condition_after"]["status"] == "true"
        assert rec["attempt"]["action"] == "back"
        assert step["attempts"][0]["recovery_dispatches"][0]["verified"]
    assert len(_sent(run, "input_text")) == 2
    assert len([e for e in _sent(run, "key") if ".dismiss_obstruction" in e.get("step", "")]) == 2
    assert run["verdict"]["verdict"] == "pending" and run["informal"]


def test_same_delayed_trace_without_explicit_opt_in_still_blocks(login_rig):
    ws, _ = login_rig
    path = ws.app("qiankun_hm").dir / "flows.json"
    data = read_json(path)
    for step in data["flows"]["login_password_prod"]["steps"]:
        step.pop("dismiss_obstruction_when", None)
    write_json(path, data)
    run = _run_world(login_rig, delayed_world(login_rig))
    step = recovery_inputs(run)[0]
    assert step["final_status"] == "target_obscured"
    assert not step["recoveries"] and not _sent(run, "input_text")
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])


@pytest.mark.parametrize("mode", ["unknown", "unchanged", "other_cover"])
def test_delayed_unknown_or_unclosed_menu_never_retries_input(login_rig, mode):
    spec = delayed_world(login_rig, known_menu=mode != "unknown", menu_back="ignore" if mode == "unchanged" else "dismiss")
    if mode == "other_cover":
        path = Path(spec.split(":", 1)[1])
        data = read_json(path)
        for name in ("phone", "password"):
            data["screens"]["selection_" + name]["root"]["children"].append({
                "t": "UnknownOverlay", "click": True, "b": [0, 0, 400, 700],
                "children": [{"t": "Text", "text": "未知覆盖物", "b": [0, 0, 100, 30]}]})
        write_json(path, data)
    run = _run_world(login_rig, spec)
    step = recovery_inputs(run)[0]
    assert not step["outcome"].startswith("passed") and len(step["attempts"]) == 1
    assert not _sent(run, "input_text")
    backs = [e for e in _sent(run, "key") if ".dismiss_obstruction" in e.get("step", "")]
    assert len(backs) == (1 if mode == "unchanged" else 0)
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])


@pytest.mark.parametrize("value", [None, [], "login_system_selection", {},
    {"condition": [], "equals": True}, {"condition": {}, "equals": True},
    {"condition": "login_system_selection", "equals": False},
    {"condition": "login_system_selection", "equals": True, "at": "S1"},
    {"condition": "missing", "equals": True}])
def test_invalid_recovery_definition_rejected_before_device(ws, monkeypatch, value):
    case = deepcopy(ws.load_case("TC-QK-223"))
    case["steps"][0]["dismiss_obstruction_when"] = value
    monkeypatch.setattr(ws, "device", lambda *a, **k: pytest.fail("must reject before opening device"))
    assert any("dismiss_obstruction_when" in i["field"] for i in cases.lint(case, ws.app(case["app"]), KnowledgeStore(ws.root)))
    with pytest.raises(ValueError, match="条件定义无效"):
        run_case(ws, case=case, device_spec="not-opened")


@pytest.mark.parametrize("phase", ["after_clear", "input_unknown"])
def test_partial_clear_or_unknown_input_is_never_replayed(login_rig, monkeypatch, phase):
    spec = _password_world(login_rig)
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    for name in ("phone", "password"):
        data["screens"]["autofill_" + name]["root"]["children"][0]["go"] = "@dismiss"
    write_json(path, data)
    if phase == "after_clear":
        original = ReplayDevice.clear_text
        def clear(dev, *args, **kwargs):
            result = original(dev, *args, **kwargs)
            if result.ok:
                dev._go("selection_phone")
            return result
        monkeypatch.setattr(ReplayDevice, "clear_text", clear)
    else:
        original = ReplayDevice.input_text
        def enter(dev, *args, **kwargs):
            original(dev, *args, **kwargs)
            dev._go("selection_phone")
            return DeviceResult("input_text", sent=None, ok=False, error_class="timeout", error="synthetic uncertain input receipt")
        monkeypatch.setattr(ReplayDevice, "input_text", enter)
    run = _run_world(login_rig, spec)
    step = recovery_inputs(run)[0]
    assert not step["outcome"].startswith("passed") and len(step["attempts"]) == 1
    assert not [e for e in _sent(run, "key") if ".dismiss_obstruction" in e.get("step", "")]
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])
    assert len(events_for_input(run)) == (0 if phase == "after_clear" else 1)


def events_for_input(run):
    from test_login_reuse import events
    return [e for e in events(run) if e["event"] == "dispatch" and e.get("op") == "input_text" and e.get("sent") is not False]


@pytest.mark.parametrize("mode", ["page_changed", "back_unknown"])
def test_recovery_page_change_or_uncertain_back_stops_before_input(login_rig, monkeypatch, mode):
    original = ReplayDevice.key
    def back(dev, name):
        if name == "back" and dev.current_screen().startswith("selection_"):
            if mode == "back_unknown":
                return DeviceResult("key_back", sent=None, ok=False, error_class="timeout", error="synthetic uncertain back receipt")
            dev._go("my")
            return dev._res("key_back")
        return original(dev, name)
    monkeypatch.setattr(ReplayDevice, "key", back)
    run = _run_world(login_rig, delayed_world(login_rig))
    step = recovery_inputs(run)[0]
    assert not step["outcome"].startswith("passed") and len(step["attempts"]) == 1
    assert not events_for_input(run)
    assert len(step["recoveries"]) == 1 and step["effect_unknown"]
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])
    if mode == "back_unknown":
        assert step["outcome"] == "error"
        assert step["execution_error"]["error_class"] == "timeout"
        assert "timeout" in step["reason"] and "synthetic uncertain back receipt" in step["reason"]
        assert "target_obscured" not in step["reason"]


@pytest.mark.parametrize("positive", [[], "bad", None, {"type": "text_present", "text": "菜单"}])
def test_recovery_requires_a_container_provider_before_device(ws, monkeypatch, positive):
    case = deepcopy(ws.load_case("TC-QK-223"))
    case["conditions"] = {"menu": {"kind": "page", "true": positive,
                                    "false": {"type": "text_present", "text": "无菜单"}}}
    case["steps"] = [{"id": "S1", "action": "input", "target": "qk_number_input", "text": "x",
                      "dismiss_obstruction_when": {"condition": "menu", "equals": True}}]
    monkeypatch.setattr(ws, "device", lambda *a, **k: pytest.fail("must reject before opening device"))
    assert cases.lint(case, ws.app(case["app"]), KnowledgeStore(ws.root))
    with pytest.raises(ValueError, match="条件定义无效"):
        run_case(ws, case=case, device_spec="not-opened")


def test_recovery_only_condition_and_selector_are_bound_to_dependency(ws):
    case = deepcopy(ws.load_case("TC-QK-223"))
    case.update(steps=[{"id": "S1", "action": "input", "target": "qk_number_input", "text": "${var:account}",
                        "dismiss_obstruction_when": {"condition": "login_system_selection", "equals": True}}], cleanup=[], assertions=[])
    knowledge = KnowledgeStore(ws.root)
    original = cases.dependency_hash(case, ws.app(case["app"]), knowledge)
    path = ws.app(case["app"]).dir / "targets.json"
    data = read_json(path)
    data["targets"]["qk_login_system_selection_here"]["strategies"][0]["value"] += "[true()]"
    write_json(path, data)
    assert cases.dependency_hash(case, ws.app(case["app"]), knowledge) != original
    original = cases.dependency_hash(case, ws.app(case["app"]), knowledge)
    path = ws.app(case["app"]).dir / "app.json"
    data = read_json(path)
    data["conditions"]["login_system_selection"]["max_age_s"] = 2
    write_json(path, data)
    assert cases.dependency_hash(case, ws.app(case["app"]), knowledge) != original
