"""Issue #64 login paths through the real Runner/Dispatcher and synthetic Replay UI.

These worlds model the reported interactions, not a portable copy of the missing
device trees. Production knowledge and asset approvals remain unchanged.
"""
from copy import deepcopy
from pathlib import Path

import pytest
from test_login_reuse import events, execute, login_rig

from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

__all__ = ("login_rig",)
ENV = {"network": "on", "vpd_task_state": "none", "parking_area": "华为练秋湖研发中心"}


def _run_world(rig, spec):
    ws, _ = rig
    return run_case(ws, "TC-QK-223", device_spec=spec, env=ENV,
                    authorize=["account_state"], propose=False)


def _password_world(rig, *, known_menu=True, menu_back="dismiss"):
    ws, build = rig
    ws.config["secrets"] = {"QIANKUN_PHONE": "synthetic-phone", "QIANKUN_PASSWORD": "synthetic-password"}
    spec = build("password")
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    data["popup_back"] = menu_back
    form = data["screens"]["password"]["root"]["children"]
    for name, hint in (("phone", "请输入手机号"), ("password", "请输入密码")):
        field = next(n for n in form if n.get("attrs", {}).get("hint") == hint)
        field["hide_if_flag"] = name + "_manual"
        field["go"] = "autofill_" + name
        ready = deepcopy(field)
        ready.pop("hide_if_flag")
        ready.pop("go")
        ready["show_if_flag"] = name + "_manual"
        form.append(ready)
        data["screens"]["autofill_" + name] = {
            "kind": "popup", "root": {"t": "Column", "b": [0, 700, 400, 900], "children": [
                {"t": "Button", "text": "手动输入", "id": "AutoFillDialog_auto_fill_self_input",
                 "b": [30, 760, 330, 810], "go": "selection_" + name,
                 "set_flag": {name + "_manual": True}}]}}
        data["screens"]["selection_" + name] = {
            "kind": "popup", "root": {"t": "SelectOverlay", "click": True,
                "b": [0, 0, 400, 700], "children": [
                    {"t": "SelectMenu", "b": [30, 400, 330, 490], "children": [
                        {"t": "SelectMenuButton", "text": "自动填充" if known_menu else "未知系统操作",
                         "b": [30, 400, 160, 440], "click": True},
                        {"t": "SelectMenuButton", "text": "拍摄输入" if known_menu else "未登记操作",
                         "b": [170, 400, 330, 440], "click": True}]}]}}
    write_json(path, data)
    return spec


def _sent(run, op=None):
    return [e for e in events(run) if e["event"] == "dispatch" and e.get("sent") is not False
            and (op is None or e.get("op") == op)]


def _password_flow(run):
    return next(s for s in run["steps"][1]["sub_steps"] if s.get("flow") == "login_password_prod")


def test_known_selection_menu_is_dismissed_before_both_secret_inputs(login_rig):
    run = _run_world(login_rig, _password_world(login_rig))
    assert all(s["outcome"].startswith("passed") for s in run["steps"]), run["steps"]
    flow = _password_flow(run)
    inputs = [s for s in flow["sub_steps"] if s["action"] == "input"]
    assert len(inputs) == 2 and all(s["outcome"] == "passed_after_recovery" for s in inputs)
    assert all(len(s["recoveries"]) == 1 and s["recoveries"][0]["status"] == "dismissed" for s in inputs)
    assert len(_sent(run, "input_text")) == 2
    assert run["verdict"]["verdict"] == "pending"  # No production Oracle confirmation.
    assert run["informal"] and not run["asset_review"]["valid"]


def test_password_form_without_selection_menu_does_not_gain_extra_back(login_rig):
    ws, _ = login_rig
    ws.config["secrets"] = {"QIANKUN_PHONE": "synthetic-phone", "QIANKUN_PASSWORD": "synthetic-password"}
    run = execute(login_rig, "password")
    assert all(s["outcome"].startswith("passed") for s in run["steps"])
    flow = _password_flow(run)
    backs = [s for s in flow["sub_steps"] if s["action"] == "back"]
    assert len(backs) == 1 and backs[0]["outcome"].startswith("passed")  # Existing secure-keyboard dismissal.


def test_unregistered_selection_overlay_is_unknown_and_never_backed_through(login_rig):
    run = _run_world(login_rig, _password_world(login_rig, known_menu=False))
    assert run["steps"][1]["outcome"] == "pending"
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])
    assert not _sent(run, "input_text")
    assert not _sent(run, "key")
    assert _password_flow(run)["sub_steps"][-1]["final_status"] == "condition_unknown"


def test_multiple_visible_selection_overlays_do_not_authorize_global_back(login_rig):
    spec = _password_world(login_rig)
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    for name in ("phone", "password"):
        data["screens"]["selection_" + name]["root"]["children"].append({
            "t": "SelectOverlay", "b": [0, 0, 400, 700], "click": True,
            "children": [{"t": "Text", "text": "未登记覆盖物", "b": [30, 100, 330, 135]}],
        })
    write_json(path, data)
    run = _run_world(login_rig, spec)
    assert run["steps"][1]["outcome"] == "pending"
    assert not _sent(run, "key") and not _sent(run, "input_text")
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])


def test_selection_menu_that_does_not_close_still_blocks_input(login_rig):
    run = _run_world(login_rig, _password_world(login_rig, menu_back="ignore"))
    flow = _password_flow(run)
    assert flow["outcome"] == "blocked" and flow["final_status"] == "target_obscured"
    assert not _sent(run, "input_text")
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])
    assert len([e for e in _sent(run, "key") if ".dismiss_obstruction" in e.get("step", "")]) == 1


@pytest.mark.parametrize("legacy_row_markers", [False, True])
def test_vehicle_secondary_page_without_current_tree_hands_over_before_wait_or_back(login_rig, legacy_row_markers):
    _, build = login_rig
    spec = build("my")
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    children = [{"t": "Text", "text": "我的车辆", "b": [30, 30, 330, 65]}]
    if legacy_row_markers:
        # The legacy catalogue does not establish a current production-page recipe.
        children += [{"t": "Text", "text": label, "b": [30, y, 330, y + 35]}
                     for label, y in (("车架号", 80), ("TEST0000000000001", 130), ("默认显示", 180))]
    data["launch_screen"] = "vehicle_secondary"
    data["screens"]["vehicle_secondary"] = {"root": {"t": "root", "b": [0, 0, 400, 900], "children": children}}
    write_json(path, data)
    run = _run_world(login_rig, spec)
    login = run["steps"][1]
    assert login["outcome"] == "pending"
    assert login["sub_steps"][0]["final_status"] == "condition_unknown"
    assert not any(s["action"] == "monitor" for s in login["sub_steps"])
    assert not _sent(run, "key") and not _sent(run, "input_text")
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])
    assert run["verdict"]["product_verdict"] != "failed"


def test_proven_my_root_reentry_still_confirms_account_without_credentials(login_rig):
    run = execute(login_rig, "my")
    assert all(s["outcome"].startswith("passed") for s in run["steps"])
    assert not _sent(run, "input_text")
    assert run["verdict"]["verdict"] == "pending"
