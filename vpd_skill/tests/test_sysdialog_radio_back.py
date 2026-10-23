"""OH7-style radio selection is synthetic; only a declared back closes the modal."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.cli import main
from vpdtest import workspace as workspace_mod
from vpdtest.experience import Overlay
from vpdtest.observe import RunStore
from vpdtest.runner import run_case
from vpdtest.sysdialog import resolve_screen_cover
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import RunContext

from conftest import confirm, world


def _radio(ws, tmp_path, monkeypatch):
    original = Path(world("motor_hm_usb_dialog.json").removeprefix("replay:"))
    data = read_json(original)
    data["extends"] = str(original.parent / "motor_hm_base.json")
    buttons = data["patch_screens"]["usb_dialog"]["root"]["children"]
    for button in buttons:
        button.pop("dismiss_system_dialog", None)
        for label in button.get("children", []):
            if label.get("text") == "取消":
                label["text"] = "确定"
    path = tmp_path / "radio-usb.json"
    write_json(path, data)
    spec = {"class": "benign", "review": "reviewed", "window_names": ["UsbModeDialog"],
            "match": {"require_any_text": ["USB连接方式"]},
            "dismiss": {"prefer_text": ["仅充电"], "forbid_text": ["确定", "传输文件"],
                        "back_if_still_present": True}}
    write_json(ws.root / "system_dialogs.json", {"dialogs": {"usb_mode": spec}})
    control = SimpleNamespace(tap_result=None, back_result=None, tap_closes=False, back_closes=True,
                              after_tap=None, windows={}, dump_fail_at=set(), title_change_at=set(),
                              lock_after_tap=False, calls=[], window_queries=0, dumps=0,
                              window_errors={}, clear_window_error_on_dump=False)
    real_tap, real_key = ReplayDevice.tap, ReplayDevice.key
    real_windows, real_dump, real_locked = ReplayDevice.shown_windows, ReplayDevice.dump_screen, ReplayDevice.screen_locked

    def tap(device, *point):
        if not device.state.get("system_dialog"):
            return real_tap(device, *point)
        control.calls.append("tap")
        result = real_tap(device, *point)
        if control.tap_closes:
            device.state["system_dialog"] = None
        if control.after_tap:
            control.after_tap(device)
        return control.tap_result or result

    def key(device, name):
        if name != "back" or not device.state.get("system_dialog"):
            return real_key(device, name)
        control.calls.append("back")
        if control.back_closes:
            device.state["system_dialog"] = None
        return control.back_result or device._res("key_back")

    def windows(device):
        control.window_queries += 1
        device.window_query_error = control.window_errors.get(control.window_queries)
        if device.window_query_error:
            return None
        return control.windows.get(control.window_queries, real_windows(device))

    def dump(device, destination):
        control.dumps += 1
        if control.clear_window_error_on_dump:
            device.window_query_error = None
        if control.dumps in control.dump_fail_at:
            return DeviceResult("dump_screen", True, False, "timeout", "synthetic dump failure", "DUMP_RAW")
        if control.dumps in control.title_change_at:
            for node in device.world["screens"]["usb_dialog"]["root"]["children"]:
                if "USB连接方式" in node.get("text", ""):
                    node["text"] = "其他弹窗"
        return real_dump(device, destination)

    def locked(device):
        if control.calls and control.lock_after_tap == "unknown":
            return None
        if control.calls and control.lock_after_tap is True:
            return True
        return real_locked(device)

    monkeypatch.setattr(ReplayDevice, "tap", tap)
    monkeypatch.setattr(ReplayDevice, "key", key)
    monkeypatch.setattr(ReplayDevice, "shown_windows", windows)
    monkeypatch.setattr(ReplayDevice, "dump_screen", dump)
    monkeypatch.setattr(ReplayDevice, "screen_locked", locked)
    device = ws.device("replay:" + str(path))
    store = RunStore(ws.runs_dir, kind="sysdiag")
    ctx = RunContext(ws, ws.app("motor_hm"), device, store, Overlay([]), mode="regression")
    return control, spec, device, store, ctx, "replay:" + str(path)


def _resolve(ws, setup):
    _, spec, device, store, ctx, _ = setup
    return resolve_screen_cover(ws, device, store, dispatcher=ctx.dispatcher, dialogs={"usb_mode": spec})


def test_radio_selection_then_one_back_closes_and_retains_both_receipts(ws, tmp_path, monkeypatch):
    setup = _radio(ws, tmp_path, monkeypatch)
    control, _, _, store, ctx, _ = setup
    result = _resolve(ws, setup)
    assert result["status"] == "dismissed", result
    assert control.calls == ["tap", "back"] and ctx.actions == 2
    assert result["dismiss"]["button"] == "仅充电"
    assert [d["op"] for d in result["dispatches"]] == ["tap", "key"]
    assert result["back_post"]["matched"] is None and "UsbModeDialog" not in result["back_post"]["windows"]
    events = [e for e in read_jsonl(store.events_path) if e["event"] == "dispatch"]
    assert [e["op"] for e in events] == ["tap", "key"]
    assert all(e["sent"] is True and e["executor_ok"] is True for e in events)


def test_old_layout_closes_on_tap_without_extra_back(ws, tmp_path, monkeypatch):
    setup = _radio(ws, tmp_path, monkeypatch)
    setup[0].tap_closes = True
    result = _resolve(ws, setup)
    assert result["status"] == "dismissed" and setup[0].calls == ["tap"]


@pytest.mark.parametrize("opt_in", [None, False, "true"])
def test_back_requires_explicit_boolean_opt_in(ws, tmp_path, monkeypatch, opt_in):
    setup = _radio(ws, tmp_path, monkeypatch)
    if opt_in is None:
        setup[1]["dismiss"].pop("back_if_still_present")
    else:
        setup[1]["dismiss"]["back_if_still_present"] = opt_in
    result = _resolve(ws, setup)
    assert result["status"] == "dismiss_failed" and setup[0].calls == ["tap"]


@pytest.mark.parametrize("windows", [None, ["app0"], ["app0", "UsbModeDialog2"],
                                    ["app0", "UsbModeDialog", "UsbModeDialog2"],
                                    ["app0", "UsbModeDialog", "UnknownOverlay"]])
@pytest.mark.parametrize("query", [3, 4])
def test_changed_unknown_or_ambiguous_window_never_backs(ws, tmp_path, monkeypatch, windows, query):
    setup = _radio(ws, tmp_path, monkeypatch)
    setup[0].windows[query] = windows
    result = _resolve(ws, setup)
    assert result["status"] != "dismissed" and setup[0].calls == ["tap"]
    if windows is None:
        assert "execution_error" not in result


def test_changed_dialog_before_back_never_uses_old_identity(ws, tmp_path, monkeypatch):
    setup = _radio(ws, tmp_path, monkeypatch)
    setup[0].title_change_at.add(4)
    result = _resolve(ws, setup)
    assert result["status"] == "dismiss_failed" and setup[0].calls == ["tap"]


@pytest.mark.parametrize("sent", [False, None, True])
def test_failed_tap_never_falls_back(ws, tmp_path, monkeypatch, sent):
    setup = _radio(ws, tmp_path, monkeypatch)
    setup[0].tap_result = DeviceResult("tap", sent, False, "timeout", "tap failed", "TAP_RAW")
    result = _resolve(ws, setup)
    assert result["status"] == "dismiss_failed" and setup[0].calls == ["tap"]
    assert result["dispatches"][0]["sent"] is sent
    assert result["execution_error"]["evidence"]["raw"] == "TAP_RAW"


@pytest.mark.parametrize("sent", [False, None, True])
def test_failed_back_retains_true_tap_and_tri_state_back(ws, tmp_path, monkeypatch, sent):
    setup = _radio(ws, tmp_path, monkeypatch)
    setup[0].back_result = DeviceResult("key", sent, False, "timeout", "back failed", "BACK_RAW")
    result = _resolve(ws, setup)
    assert result["status"] == "dismiss_failed" and setup[0].calls == ["tap", "back"]
    assert [d["sent"] for d in result["dispatches"]] == [True, sent]
    assert result["execution_error"]["stage"] == "system_dialog_key"
    assert result["execution_error"]["evidence"]["raw"] == "BACK_RAW"


@pytest.mark.parametrize("gate", ["budget", "hold", "locked", "unknown"])
def test_back_still_uses_dispatcher_limits_and_screen_state(ws, tmp_path, monkeypatch, gate):
    setup = _radio(ws, tmp_path, monkeypatch)
    control, _, _, _, ctx, _ = setup
    if gate == "budget":
        ctx.policy.max_actions_per_run = 1
    elif gate == "hold":
        control.after_tap = lambda dev: setattr(ctx, "safety_hold", {"reason": "synthetic hold"})
    else:
        control.lock_after_tap = True if gate == "locked" else "unknown"
    result = _resolve(ws, setup)
    assert result["status"] == "dismiss_failed" and control.calls == ["tap"]
    assert [d["sent"] for d in result["dispatches"]] == [True, False]
    assert ctx.actions == 1


@pytest.mark.parametrize("cause", ["dump_failure", "still_present", "window_unknown"])
def test_back_success_requires_independent_absence_proof(ws, tmp_path, monkeypatch, cause):
    setup = _radio(ws, tmp_path, monkeypatch)
    control = setup[0]
    if cause == "dump_failure":
        control.dump_fail_at.add(5)
    elif cause == "still_present":
        control.back_closes = False
    else:
        control.windows[5] = None
    result = _resolve(ws, setup)
    assert result["status"] != "dismissed" and control.calls == ["tap", "back"]
    if cause == "window_unknown":
        assert result["status"] == "indeterminate" and "execution_error" not in result


def test_unknown_preflight_back_stops_before_launch_and_preserves_raw_error(ws, tmp_path, monkeypatch):
    setup = _radio(ws, tmp_path, monkeypatch)
    control, _, _, _, _, device_spec = setup
    control.back_result = DeviceResult("key", None, False, "timeout", "lost back receipt", "BACK_UNKNOWN_RAW")
    launches = []
    real = ReplayDevice.launch
    monkeypatch.setattr(ReplayDevice, "launch", lambda dev, *args: (launches.append(args), real(dev, *args))[1])
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=device_spec)
    assert control.calls == ["tap", "back"] and not launches
    assert all(step["outcome"] == "not_run" and not step.get("attempts") for step in run["steps"])
    assert not run["preflight"]["ok"] and run["verdict"]["verdict"] == "error"
    assert run["verdict"]["action_required"]
    failure = run["preflight"]["checks"][-1]
    assert failure["execution_error"]["evidence"]["raw"] == "BACK_UNKNOWN_RAW"


@pytest.mark.parametrize("phase", ["preflight", "recovery"])
def test_back_post_dump_failure_is_tool_error_with_original_raw(ws, tmp_path, monkeypatch, phase):
    setup = _radio(ws, tmp_path, monkeypatch)
    control, _, _, _, _, device_spec = setup
    if phase == "recovery":
        control.windows[1] = ["app0"]
    control.after_tap = lambda dev: control.dump_fail_at.add(control.dumps + 3)
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=device_spec)
    assert control.calls == ["tap", "back"]
    assert run["verdict"]["verdict"] == "error" and run["verdict"]["action_required"]
    if phase == "preflight":
        error = run["preflight"]["checks"][-1]["execution_error"]
    else:
        error = next(step["execution_error"] for parent in run["steps"]
                     for step in parent.get("sub_steps", [parent]) if step.get("recoveries"))
    assert error["stage"] == "system_dialog_back_verification"
    assert error["error_class"] == "timeout" and error["attribution"] == "tool"
    assert error["evidence"]["query"] == "dump_screen" and error["evidence"]["raw"] == "DUMP_RAW"


@pytest.mark.parametrize("phase", ["preflight", "recovery"])
@pytest.mark.parametrize("moment,offset", [("before", 2), ("after", 3)])
def test_back_window_query_failure_is_tool_error_even_when_dump_succeeds(
        ws, tmp_path, monkeypatch, phase, moment, offset):
    setup = _radio(ws, tmp_path, monkeypatch)
    control, _, _, _, _, device_spec = setup
    if phase == "recovery":
        control.windows[1] = ["app0"]
    detail = {"op": "window_list", "error_class": "timeout", "error": "synthetic window query timeout",
              "raw": "WINDOW_TIMEOUT_RAW"}
    control.after_tap = lambda dev: control.window_errors.update({control.window_queries + offset: detail})
    control.clear_window_error_on_dump = True
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=device_spec)
    assert control.calls == (["tap"] if moment == "before" else ["tap", "back"])
    assert run["verdict"]["verdict"] == "error" and run["verdict"]["action_required"]
    if phase == "preflight":
        error = run["preflight"]["checks"][-1]["execution_error"]
    else:
        error = next(step["execution_error"] for parent in run["steps"]
                     for step in parent.get("sub_steps", [parent]) if step.get("recoveries"))
    assert error["stage"] == ("system_dialog_back_recheck" if moment == "before"
                              else "system_dialog_back_verification")
    assert error["error_class"] == "timeout" and error["attribution"] == "tool"
    assert error["error"] == detail["error"]
    assert error["evidence"]["query"] == "shown_windows" and error["evidence"]["raw"] == detail["raw"]


def test_unrecognized_window_output_does_not_reuse_old_query_failure(ws, tmp_path, monkeypatch):
    from vpdtest.sysdialog import diagnose_cover
    setup = _radio(ws, tmp_path, monkeypatch)
    _, spec, device, store, _, _ = setup
    device.window_query_error = {"error_class": "timeout", "error": "old timeout", "raw": "OLD_WINDOW_RAW"}
    monkeypatch.setattr(device, "shown_windows", lambda: None)
    result = diagnose_cover(device, store, store.dir / "unknown-window", {"usb_mode": spec})
    assert result["windows"] is None and result["dump_ok"]
    assert "execution_error" not in result


@pytest.mark.parametrize("query", [2, 3], ids=["tap-baseline", "after-tap"])
def test_known_window_failure_during_tap_prevents_back_and_preserves_error(ws, tmp_path, monkeypatch, query):
    setup = _radio(ws, tmp_path, monkeypatch)
    setup[0].window_errors[query] = {"error_class": "timeout", "error": "window query failed",
                                     "raw": "TAP_WINDOW_RAW"}
    setup[0].clear_window_error_on_dump = True
    result = _resolve(ws, setup)
    assert setup[0].calls == ["tap"] and result["action_required"]
    assert result["execution_error"]["error_class"] == "timeout"
    assert result["execution_error"]["evidence"]["query"] == "shown_windows"
    assert result["execution_error"]["evidence"]["raw"] == "TAP_WINDOW_RAW"


@pytest.mark.parametrize("command", ["sysdiag", "run", "suite"])
def test_unknown_back_reaches_real_cli_and_suite_handover(ws, tmp_path, monkeypatch, capsys, command):
    setup = _radio(ws, tmp_path, monkeypatch)
    control, _, _, _, _, device_spec = setup
    control.back_result = DeviceResult("key", None, False, "timeout", "lost receipt", "CLI_BACK_RAW")
    monkeypatch.setattr(workspace_mod, "load_config", lambda: {})
    argv = ["--workspace", str(ws.root), command]
    if command == "sysdiag":
        argv += ["--app", "motor_hm", "--dismiss"]
    elif command == "run":
        argv += ["TC-MOTOR-HM-001"]
    else:
        write_json(ws.root / "suites" / "usb-handover.json",
                   {"id": "usb-handover", "cases": ["TC-MOTOR-HM-001", "TC-MOTOR-HM-002"]})
        argv += ["usb-handover"]
    code = main(argv + ["--device", device_spec])
    output = json.loads(capsys.readouterr().out)
    assert code == 2 and output["action_required"]
    assert control.calls == ["tap", "back"]
    if command == "suite":
        assert len(output["results"]) == 1 and len(output["not_run"]) == 1


@pytest.mark.parametrize("back_sent", [True, None])
def test_unobservable_recovery_retains_all_actions_and_original_anomaly(ws, tmp_path, monkeypatch, back_sent):
    setup = _radio(ws, tmp_path, monkeypatch)
    control, _, _, _, _, device_spec = setup
    control.windows[1] = ["app0"]  # The preflight quick check misses a later visible modal.
    if back_sent is None:
        control.back_result = DeviceResult("key", None, False, "timeout", "lost back receipt", "RECOVERY_BACK_RAW")
    confirm(ws, "K-MOT-001", "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=device_spec)
    steps = [sub for step in run["steps"] for sub in step.get("sub_steps", [step])]
    recovered = next(step for step in steps if step.get("recoveries"))
    attempt = recovered["attempts"][0]
    assert attempt["status"] == "unobservable"
    assert [d["sent"] for d in attempt["recovery_dispatches"]] == [True, back_sent]
    assert control.calls == ["tap", "back"]
    if back_sent is None:
        assert attempt["effect_unknown"] and recovered["recoveries"][0]["retry_original"] is False
        assert recovered["execution_error"]["evidence"]["raw"] == "RECOVERY_BACK_RAW"
        assert len(recovered["attempts"]) == 1
    else:
        assert run["verdict"]["verdict"] == "passed"
