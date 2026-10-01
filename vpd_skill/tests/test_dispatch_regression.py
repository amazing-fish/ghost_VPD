"""执行正确性回归；合成数据不修改设备或业务判据。"""
import json
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_goal_loop import BUNDLE, _lab_world, _pad, _start, cli
from vpdtest.cases import content_hash
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.execute import Executor
from vpdtest.experience import ExperienceStore
from vpdtest.observe import RunStore
from vpdtest.runner import run_case
from vpdtest.session import parse_select
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import RunContext


def _ctx(ws, spec):
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir)
    dev = ws.device(spec, state_path=store.dir / "replay_state.json")
    dev.launch(BUNDLE, app.ability)
    return RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")


@pytest.mark.parametrize("dismiss", [False, True])
def test_topmost_overlay_blocks_normal_and_dismiss_tap(ws, tmp_path, dismiss):
    text = "下次再说" if dismiss else "唯一入口"
    spec = _lab_world(tmp_path)
    data = read_json(spec.split(":", 1)[1])
    kids = data["screens"]["home"]["root"]["children"]
    kids[2]["children"][0]["text"] = text
    kids.append({"t": "Button", "b": [0, 380, 1320, 540], "click": True,
                 "children": [{"t": "Text", "text": "遮挡面板", "b": [20, 420, 1300, 500]}]})
    write_json(spec.split(":", 1)[1], data)
    ctx = _ctx(ws, spec)
    taps = []
    ctx.device.tap = lambda *p: taps.append(p) or DeviceResult("tap", True, True)
    if dismiss:
        obs, tree = ctx.observe("before")
        obs["page"]["interrupts"] = [{"id": "update_dialog", "class": "benign", "review": "reviewed"}]
        ctx.app.states.interrupts["update_dialog"].update(dismiss_text=text)
        ctx.app.states.interrupts["update_dialog"].pop("dismiss_target", None)
        Executor(ctx)._clear_interrupts(obs, tree, "test")
    else:
        res = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                      "target": parse_select(f"text={text}")}, "session")
        assert res["final_status"] == "target_obscured"
    assert taps == []


@pytest.mark.parametrize("changed", [False, None])
def test_pin_stops_before_next_digit_when_lock_changes(monkeypatch, changed):
    dev = object.__new__(HdcDevice)
    dev.spec = "hdc:fixture"
    queries = iter([True, True, True, True, True, True, changed] + [False] * 20)
    monkeypatch.setattr(dev, "screen_locked", lambda: next(queries))
    monkeypatch.setattr(dev, "display_info", lambda: {"width_px": 1320, "height_px": 2848})
    monkeypatch.setattr(dev, "_shell", lambda *a, **kw: DeviceResult(kw.get("op", "x"), True, True,
                                                                raw=json.dumps(_pad())))
    monkeypatch.setattr("vpdtest.devices.harmony_hdc.time.sleep", lambda _: None)
    taps = []
    monkeypatch.setattr(dev, "tap", lambda *p: taps.append(p) or DeviceResult("tap", True, True))
    res = dev.wake_unlock("246810")
    assert len(taps) == 1
    assert res["status"] == "indeterminate" and res["entered_digits"] == 1


def test_changed_case_does_not_reuse_known_failure(ws):
    from conftest import world
    case = ws.load_case("TC-HM-S01")
    case["product_status"] = {"status": "known_failure", "since_hash": content_hash(case),
                              "issue": "fixture-issue", "signature": ["fixture"]}
    case["steps"][0]["timeout_s"] = 7
    run = run_case(ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert "known_issue" not in run["verdict"]
    assert "用例内容已变更，需要重新晋级" in (Path(run["dir"]) / "report.md").read_text("utf-8")


def test_monitor_session_conversion_uses_until_state(ws, tmp_path):
    spec = _lab_world(tmp_path)
    _start(ws, spec)
    cli(ws, "act", "monitor", "--app", "motor_hm", "--device", spec, "--session", "s1",
        "--expect", "home_tab", "--duration", "0.01", "--interval", "0.01", "--watch", "speed=速度")
    cli(ws, "case", "from-session", "--session", "s1", "--new-id", "TC-MON-FIX", "--app", "motor_hm")
    step = ws.load_case("TC-MON-FIX")["steps"][1]
    assert step["until_state"] == "home_tab" and "expect_state" not in step
    assert step["interval_s"] == 0.01 and step["watch"] == {"speed": "速度"}


def test_two_process_acts_serialize_sequence_and_observations(ws, tmp_path):
    spec = _lab_world(tmp_path)
    _start(ws, spec)
    ready = tmp_path / "go"
    code = '''import sys,time
from pathlib import Path
from vpdtest.workspace import Workspace
from vpdtest.cli import build_parser
from vpdtest import commands
w=Workspace(sys.argv[1]); w.config={}
old=commands.next_seq
def delayed(store):
    n=old(store); time.sleep(0.3); return n
commands.next_seq=delayed
while not Path(sys.argv[3]).exists(): time.sleep(0.01)
a=build_parser().parse_args(['act','wait','--duration','0.01','--app','motor_hm','--session','s1','--device',sys.argv[2]])
print(commands.cmd_act(w,a)['seq'])
'''
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    procs = [subprocess.Popen([sys.executable, "-c", code, str(ws.root), spec, str(ready)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
             for _ in range(2)]
    ready.touch()
    results = [p.communicate(timeout=20) for p in procs]
    assert all(p.returncode == 0 for p in procs), results
    assert sorted(int(out.strip()) for out, _ in results) == [2, 3]
    events = read_jsonl(ws.runs_dir / "session-s1" / "events.jsonl")
    requests = [e["seq"] for e in events if e["event"] == "act_request"]
    assert requests == [1, 2, 3]


def test_device_primitives_have_one_call_site_module():
    root = Path(__file__).resolve().parents[1] / "vpdtest"
    forbidden = []
    for path in root.rglob("*.py"):
        if path.name == "dispatch.py":
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func
                if attr.attr in ("tap", "swipe", "input_text") or (
                        attr.attr == "key" and not (isinstance(attr.value, ast.Name) and attr.value.id == "layout_mod")):
                    forbidden.append(f"{path.relative_to(root)}:{node.lineno}")
    assert forbidden == []


@pytest.mark.parametrize("initial", ["off", "locked", "unlocked", "other_app", "desktop"])
def test_preflight_unlock_replays_initial_states(ws, tmp_path, initial):
    from conftest import world
    data = read_json(world("motor_hm_base.json").split(":", 1)[1])
    data["initial"].update(locked=initial in ("off", "locked"), screen_on=initial != "off", lock_pin="246810")
    if initial == "other_app":
        data["initial"]["foreground"] = "com.example.other"
    elif initial == "desktop":
        data["initial"]["foreground"] = "com.ohos.sceneboard"
    p = tmp_path / f"{initial}.json"
    write_json(p, data)
    spec = f"replay:{p}"
    ws.config["secrets"] = {"LOCK_PIN_TEST": "246810", "LOCK_PIN": "111111"}
    ws.config["unlock_secrets"] = {spec: "LOCK_PIN_TEST"}
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=spec, propose=False)
    assert run["preflight"]["ok"]
    assert all(s["outcome"].startswith("passed") for s in run["steps"])
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    calls = [e for e in events if e["event"] == "capability_result" and e.get("capability") == "unlock"]
    assert bool(calls) == (initial in ("off", "locked"))
    if calls:
        assert calls[0]["status"] == "unlocked"
        digit_events = [e for e in events if e["event"] == "dispatch" and e.get("step") == "unlock"]
        assert len(digit_events) == 6 and all(e["checks"]["screen_locked"]["actual"] is True for e in digit_events)


def test_action_recovers_lock_and_observes_again(ws, tmp_path):
    spec = _lab_world(tmp_path)
    ctx = _ctx(ws, spec)
    ctx.device.world["initial"]["lock_pin"] = "246810"
    ctx.device.state["locked"] = True
    ws.config["unlock_secrets"] = {spec: "LOCK_PIN_TEST"}
    ws.config["secrets"] = {"LOCK_PIN_TEST": "246810"}
    out = Executor(ctx).run_step({"id": "A1", "action": "tap", "target": parse_select("text=唯一入口"),
                                  "expect_text": "详情页", "retry": 0}, "session")
    assert out["outcome"] == "passed"
    events = read_jsonl(ctx.store.events_path)
    unlocked = next(i for i, e in enumerate(events) if e["event"] == "capability_result")
    observes = [i for i, e in enumerate(events) if e["event"] == "observe"]
    assert observes and min(observes) > unlocked


def test_gate_checks_declared_foreground_without_app_context():
    from types import SimpleNamespace
    from vpdtest.dispatch import Dispatcher
    taps = []
    device = SimpleNamespace(screen_locked=lambda: False, foreground=lambda: "com.example.other",
                             tap=lambda *p: taps.append(p) or DeviceResult("tap", True, True))
    res = Dispatcher(device).send("tap", 10, 20,
                                  preconditions={"screen_locked": False, "foreground": BUNDLE})
    assert not res.sent and res.error_class == "foreground_changed" and taps == []


def test_unlocked_does_not_require_valid_pin_config(ws, tmp_path):
    from test_goal_loop import _locked_world
    spec = _locked_world(tmp_path, "already", locked=False)
    ws.config["unlock_secrets"] = {spec: "LOCK_PIN_TEST"}
    ws.config["secrets"] = {"LOCK_PIN_TEST": "invalid-fixture"}
    assert cli(ws, "unlock", "--device", spec)["status"] == "unlocked"
