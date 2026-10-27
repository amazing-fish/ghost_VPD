"""Issue #110: distinguish a pixel proposal, policy refusal and a real click receipt.

The quick-login tree is a synthetic reproduction of the reported Checkbox bounds.
It does not certify the current phone or confirm any product requirement.
"""
from copy import deepcopy

import pytest
from test_goal_loop import _lab_world, _start, cli

from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.tree import UiTree
from vpdtest.util import read_json, read_jsonl, write_json


def quick_login(ws, tmp_path, *, change=None):
    checkbox = {"t": "Checkbox", "text": "", "b": [54, 2505, 101, 2552], "click": True,
                "attrs": {"checked": "false", "checkable": "true"}, "go": "checked"}
    children = [{"t": "Button", "text": "本机号码一键登录", "b": [66, 2282, 1254, 2417], "click": True},
                checkbox, {"t": "Text", "text": "欢迎登录", "b": [66, 200, 1254, 300]}]
    if change == "checked":
        checkbox["attrs"]["checked"] = "true"
    elif change == "identified":
        checkbox["id"] = "unregistered-setting"
    elif change == "not_login":
        children[0]["text"] = "普通页面"
    elif change == "duplicate":
        other = deepcopy(checkbox)
        other["b"] = [150, 2505, 197, 2552]
        children.append(other)
    elif change == "hidden":
        checkbox["attrs"]["visible"] = "false"
    elif change == "no_heading":
        children[-1]["text"] = "其他页面"
    elif change == "dialog_checkbox":
        children.remove(checkbox)
        children.append({"t": "Dialog", "b": [0, 2400, 1320, 2700], "children": [checkbox]})
    checked = deepcopy(children)
    if change != "dialog_checkbox":
        checked[1]["attrs"]["checked"] = "true"
    data = {"bundle": "com.yinwang.qiankunapp.hm", "app_version": "1.0.26.100", "screen": [1320, 2848],
            "display": {"density": 3.375, "device_form": "bar"},
            "initial": {"installed": True, "foreground": "com.ohos.sceneboard"}, "launch_screen": "quick",
            "screens": {"quick": {"root": {"t": "root", "b": [0, 0, 1320, 2848], "children": children}},
                        "checked": {"root": {"t": "root", "b": [0, 0, 1320, 2848], "children": checked}}}}
    file = tmp_path / "anonymous-protocol.json"
    write_json(file, data)
    device = f"replay:{file}"
    args = ["--app", "qiankun_hm", "--session", "point", "--device", device]
    cli(ws, "act", "launch", *args)
    cli(ws, "observe", *args)
    return args


def test_anonymous_point_is_explicitly_refused_without_dispatch(ws, tmp_path):
    args = quick_login(ws, tmp_path)
    out = cli(ws, "act", "tap", "--point", "77,2528", "--basis", "协议复选框", *args)
    assert out["action_status"] == "policy_denied"
    assert out["dispatched"] is False and out["effect_unknown"] is False
    assert out["result"]["outcome"] == "blocked"
    assert out["diagnosis"]["policy"]["deny"]["class"] == "setting_change"
    assert "策略拒发" in out["diagnosis"]["hint"]
    assert "dispatch_coordinates" not in out["result"]["attempts"][-1]


def test_labelled_point_sends_requested_pixels(ws, tmp_path):
    device = _lab_world(tmp_path)
    _start(ws, device, "labelled")
    args = ["--app", "motor_hm", "--session", "labelled", "--device", device]
    cli(ws, "observe", *args)
    out = cli(ws, "act", "tap", "--point", "660,460", "--basis", "唯一入口", "--expect-text", "详情页", *args)
    attempt = out["result"]["attempts"][-1]
    assert out["action_status"] == "ok" and out["dispatched"] is True
    assert attempt["dispatch_coordinates"] == {"system": "px", "point": [660, 460]}
    assert attempt["post"]["met"] is True


def test_registered_quick_login_checkbox_uses_current_pixel_center(ws, tmp_path):
    args = quick_login(ws, tmp_path)
    out = cli(ws, "act", "tap", "--target", "qk_protocol_unchecked", "--authorize", "account_state", *args)
    assert out["result"]["outcome"] in {"passed", "passed_after_adaptation"}
    assert out["action_status"] == "ok" and out["dispatched"] is True
    assert out["effect_unknown"] is False
    attempt = out["result"]["attempts"][-1]
    assert attempt["dispatch_coordinates"] == {"system": "px", "point": [77, 2528]}
    assert attempt["resolved"]["coordinate_system"] == "px"
    assert attempt["policy"]["allowed"]
    events = read_jsonl(ws.runs_dir / "session-point" / "events.jsonl")
    tap = [e for e in events if e["event"] == "dispatch" and e.get("op") == "tap"][-1]
    assert tap["sent"] is True and tap["coordinates"] == {"system": "px", "point": [77, 2528]}
    tree = read_json(ws.runs_dir / "session-point" / "obs" / out["result"]["final_obs"] / "tree.json")
    checkbox = [n for n in UiTree(tree).nodes if n.type == "Checkbox"]
    assert len(checkbox) == 1 and checkbox[0].attrs["checked"] == "true"


def test_registered_checkbox_still_needs_account_authority(ws, tmp_path):
    args = quick_login(ws, tmp_path)
    out = cli(ws, "act", "tap", "--target", "qk_protocol_unchecked", *args)
    assert out["action_status"] == "policy_denied" and out["dispatched"] is False
    assert out["diagnosis"]["policy"]["deny"]["class"] == "account_state"


@pytest.mark.parametrize("change", ["checked", "identified", "not_login", "duplicate", "hidden", "no_heading", "dialog_checkbox"])
def test_anonymous_selector_does_not_generalize_to_other_settings(ws, tmp_path, change):
    args = quick_login(ws, tmp_path, change=change)
    out = cli(ws, "act", "tap", "--target", "qk_protocol_unchecked", "--authorize", "account_state", *args)
    assert out["action_status"] == "target_unresolved" and out["dispatched"] is False


@pytest.mark.parametrize("point", ["77,2528,0", "-1,2528", "1.0,2", "77", "1320,2", "1,2848"])
def test_bad_pixel_proposals_are_rejected_before_execution(ws, tmp_path, point):
    args = quick_login(ws, tmp_path)
    before = read_jsonl(ws.runs_dir / "session-point" / "events.jsonl")
    with pytest.raises(ValueError, match="--point"):
        cli(ws, "act", "tap", f"--point={point}", "--basis", "协议复选框", *args)
    after = read_jsonl(ws.runs_dir / "session-point" / "events.jsonl")
    assert len([e for e in after if e["event"] == "dispatch"]) == len([e for e in before if e["event"] == "dispatch"])


def test_point_without_basis_is_a_usage_error_not_a_click(ws, tmp_path):
    args = quick_login(ws, tmp_path)
    with pytest.raises(ValueError, match="--basis"):
        cli(ws, "act", "tap", "--point", "77,2528", *args)


def test_hdc_tap_preserves_pixels_without_density_conversion(monkeypatch):
    received = []
    device = object.__new__(HdcDevice)
    monkeypatch.setattr(device, "_shell", lambda *args, **kwargs: received.append((args, kwargs)))
    device.tap(77, 2528)
    assert received == [(("uitest", "uiInput", "click", 77, 2528), {"op": "tap"})]
