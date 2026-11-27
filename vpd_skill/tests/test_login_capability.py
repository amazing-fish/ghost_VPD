"""Atomic fast login capability (`vpdtest login`) & `screen_and_foreground` batching tests (synthetic only)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from vpdtest import cli
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.devices.replay import ReplayDevice
from vpdtest.login import login_app
from vpdtest.util import write_json


def _node(text, y, *, t=None, go=None, click=False, **kw):
    n = {"t": t or ("Button" if go else "Text"), "text": text, "b": [30, y, 360, y + 40], **kw}
    if go:
        n["go"] = go
        n["click"] = True
    elif click:
        n["click"] = True
    return n


def _screen(children):
    return {"root": {"t": "root", "b": [0, 0, 400, 900], "children": children}}


def _build_login_world(tmp_path, *, start="logged_out", post_login="car",
                       with_vault=False, with_overlay=False, unchecked_protocol=True):
    pwd_base_children = [
        _node("欢迎登录华为乾崑", 30),
        _node("验证码登录", 80),
        _node("", 140, t="TextInput", click=True, bind="phone", text_bound="phone",
              go="pwd_vault" if with_vault else None, attrs={"hint": "请输入手机号"}),
        _node("", 210, t="TextInput", click=True, bind="pw", text_bound="pw",
              attrs={"hint": "请输入密码"}),
        _node("userProtocol", 280, t="Checkbox", click=True,
              go="pwd_checked" if unchecked_protocol else None,
              attrs={"checked": "false" if unchecked_protocol else "true"}),
        _node("我已阅读并同意用户协议", 330),
        _node("登录", 390, go=post_login),
    ]
    pwd_checked_children = [
        _node("欢迎登录华为乾崑", 30),
        _node("验证码登录", 80),
        _node("", 140, t="TextInput", click=True, bind="phone", text_bound="phone",
              attrs={"hint": "请输入手机号"}),
        _node("", 210, t="TextInput", click=True, bind="pw", text_bound="pw",
              attrs={"hint": "请输入密码"}),
        _node("userProtocol", 280, t="Checkbox", click=True, attrs={"checked": "true"}),
        _node("我已阅读并同意用户协议", 330),
        _node("登录", 390, go=post_login),
    ]
    pwd_vault_children = [
        _node("欢迎登录华为乾崑", 30),
        _node("验证码登录", 80),
        _node("", 140, t="TextInput", click=True, bind="phone", text_bound="phone",
              attrs={"hint": "请输入手机号"}),
        _node("", 210, t="TextInput", click=True, bind="pw", text_bound="pw",
              attrs={"hint": "请输入密码"}),
        _node("手动输入", 500, go="pwd_overlay" if with_overlay else "pwd_clean"),
    ]
    pwd_overlay_children = [
        _node("欢迎登录华为乾崑", 30),
        _node("验证码登录", 80),
        _node("", 140, t="TextInput", click=True, bind="phone", text_bound="phone",
              attrs={"hint": "请输入手机号"}),
        _node("", 210, t="TextInput", click=True, bind="pw", text_bound="pw",
              attrs={"hint": "请输入密码"}),
        _node("userProtocol", 280, t="Checkbox", click=True,
              go="pwd_checked" if unchecked_protocol else None,
              attrs={"checked": "false" if unchecked_protocol else "true"}),
        _node("登录", 390, go=post_login),
        {"t": "SelectOverlay", "b": [0, 0, 400, 900], "click": True, "children": [
            {"t": "SelectMenu", "b": [20, 120, 260, 200], "children": [
                _node("自动填充", 130, t="SelectMenuButton"),
                _node("拍摄输入", 160, t="SelectMenuButton"),
            ]}
        ]},
    ]
    pwd_clean_children = [
        _node("欢迎登录华为乾崑", 30),
        _node("验证码登录", 80),
        _node("", 140, t="TextInput", click=True, bind="phone", text_bound="phone",
              attrs={"hint": "请输入手机号"}),
        _node("", 210, t="TextInput", click=True, bind="pw", text_bound="pw",
              attrs={"hint": "请输入密码"}),
        _node("userProtocol", 280, t="Checkbox", click=True,
              go="pwd_checked" if unchecked_protocol else None,
              attrs={"checked": "false" if unchecked_protocol else "true"}),
        _node("我已阅读并同意用户协议", 330),
        _node("登录", 390, go=post_login),
    ]
    screens = {
        "logged_out": _screen([_node("我的车辆", 30), _node("登录/注册", 90, go="quick")]),
        "quick": _screen([_node("本机号码一键登录", 30), _node("其他登录方式", 90, go="method")]),
        "method": _screen([_node("欢迎登录华为乾崑", 30), _node("密码登录", 90, go="password"),
                           _node("", 140, t="TextInput", attrs={"hint": "请输入手机号"})]),
        "password": _screen(pwd_base_children),
        "pwd_vault": _screen(pwd_vault_children),
        "pwd_overlay": _screen(pwd_overlay_children),
        "pwd_clean": _screen(pwd_clean_children),
        "pwd_checked": _screen(pwd_checked_children),
        "car": _screen([_node("爱车", 30, sel=True), _node("ADS", 90), _node("我的", 150), _node("我的车辆", 210)]),
        "sms": _screen([_node("身份验证", 30), _node("验证码已发至151****0000", 90)]),
        "safe_code": _screen([_node("请输入安全码", 30)]),
    }
    data = {
        "bundle": "com.yinwang.qiankunapp.hm",
        "app_version": "2.0.4.100",
        "screen": [400, 900],
        "display": {"density": 1, "device_form": "bar", "fold_status": "unknown"},
        "initial": {"installed": True, "foreground": "com.ohos.sceneboard"},
        "launch_screen": start,
        "screens": screens,
    }
    path = tmp_path / f"login-{start}-{post_login}.json"
    write_json(path, data)
    return "replay:" + str(path)


def test_login_requires_account_state_authorization(ws, tmp_path):
    spec = _build_login_world(tmp_path, start="logged_out")
    dev = ws.device(spec)
    res = login_app(ws, ws.app("qiankun_hm"), dev, authorize=[])
    assert res["status"] == "authorization_required"
    assert res["actions"] == 0
    assert res["action_required"] is False


@pytest.mark.parametrize("kw", [
    {"account_secret": "13800138000"},
    {"password_secret": "myPass!123"},
])
def test_login_rejects_literal_secret_values(ws, tmp_path, kw):
    spec = _build_login_world(tmp_path, start="logged_out")
    dev = ws.device(spec)
    with pytest.raises(ValueError, match="键名"):
        login_app(ws, ws.app("qiankun_hm"), dev, authorize=["account_state"], **kw)


def test_login_already_logged_in_returns_fast_without_secrets(ws, tmp_path, monkeypatch):
    monkeypatch.delenv("VPD_SECRET_QIANKUN_PHONE", raising=False)
    monkeypatch.delenv("VPD_SECRET_QIANKUN_PASSWORD", raising=False)
    ws.config["secrets"] = {}
    spec = _build_login_world(tmp_path, start="car")
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    res = login_app(ws, app, dev, authorize=["account_state"])
    assert res["status"] == "logged_in"
    assert res["method"] == "already"
    assert res["actions"] == 0
    assert res["action_required"] is False


def test_login_missing_secrets_stops_before_form_input(ws, tmp_path, monkeypatch):
    monkeypatch.delenv("VPD_SECRET_QIANKUN_PHONE", raising=False)
    monkeypatch.delenv("VPD_SECRET_QIANKUN_PASSWORD", raising=False)
    ws.config["secrets"] = {}
    spec = _build_login_world(tmp_path, start="logged_out")
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    res = login_app(ws, app, dev, authorize=["account_state"])
    assert res["status"] == "missing_secret"
    assert res["action_required"] is True
    assert res["actions"] == 0


def test_login_full_password_flow_handles_vault_overlay_and_protocol(ws, tmp_path, monkeypatch):
    fake_phone = "13900009999"
    fake_pwd = "SyntheticPwd999"
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", fake_phone)
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", fake_pwd)

    orig_key = ReplayDevice.key
    def custom_key(dev, name):
        if name == "back":
            if dev.current_screen() == "pwd_overlay":
                dev.state["stack"][-1] = "pwd_clean"
                return dev._res("key_back", raw="dismissed SelectOverlay")
            if dev.current_screen() in ("pwd_clean", "password"):
                return dev._res("key_back", raw="dismissed IME")
        return orig_key(dev, name)
    monkeypatch.setattr(ReplayDevice, "key", custom_key)

    spec = _build_login_world(tmp_path, start="logged_out", post_login="car",
                              with_vault=True, with_overlay=True, unchecked_protocol=True)
    dev = ws.device(spec)
    res = login_app(ws, ws.app("qiankun_hm"), dev, authorize=["account_state"])
    assert res["status"] == "logged_in", res
    assert res["method"] == "password"
    assert res["action_required"] is False
    assert dev.bound.get("phone") == fake_phone
    assert dev.bound.get("pw") == fake_pwd

    # Strict secret hygiene: neither return payload nor events.jsonl may leak raw credentials.
    payload_str = json.dumps(res, ensure_ascii=False)
    events_str = (ws.runs_dir / res["run"] / "events.jsonl").read_text(encoding="utf-8")
    assert fake_phone not in payload_str and fake_pwd not in payload_str
    assert fake_phone not in events_str and fake_pwd not in events_str


def test_login_stops_on_sms_challenge_preserving_screen(ws, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900001111")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd111")
    orig_key = ReplayDevice.key
    monkeypatch.setattr(
        ReplayDevice, "key",
        lambda dev, name: dev._res("key_back") if name == "back" and dev.current_screen() == "password" else orig_key(dev, name),
    )
    spec = _build_login_world(tmp_path, start="password", post_login="sms", unchecked_protocol=False)
    rc = cli.main(["--workspace", str(ws.root), "login", "--app", "qiankun_hm", "--device", spec,
                   "--authorize", "account_state"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out["status"] == "sms_required"
    assert out["action_required"] is True
    assert out.get("human_handoffs")
    events_str = (ws.runs_dir / out["run"] / "events.jsonl").read_text(encoding="utf-8")
    assert '"op": "stop_app"' not in events_str


def test_login_stops_on_safe_code_preserving_screen(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900002222")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd222")
    spec = _build_login_world(tmp_path, start="safe_code")
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    res = login_app(ws, app, dev, authorize=["account_state"])
    assert res["status"] == "safe_code_required"
    assert res["action_required"] is True
    assert res["actions"] == 0


def test_hdc_screen_and_foreground_batches_into_one_shell_call(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        m = re.search(r"(__VPD_SF_[0-9a-f]+__)", cmd[-1])
        assert m, cmd
        sep = m.group(1)
        sections = [
            ("POWER", "Current State: AWAKE", 0),
            ("LOCK", "screenLocked: false", 0),
            ("AA", "Mission ID #1\n  bundle name [com.yinwang.qiankunapp.hm]\n  state #FOREGROUND", 0),
        ]
        body = "".join(f"{sep}:BEGIN:{k}\r\n{v}\r\n\r\n{sep}:END:{k}:{rc}\r\n" for k, v, rc in sections)
        return subprocess.CompletedProcess(cmd, 0, body.encode("utf-8"), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dev = HdcDevice("synthetic-hdc")
    locked, fg = dev.screen_and_foreground()
    assert len(calls) == 1
    assert locked is False
    assert fg == "com.yinwang.qiankunapp.hm"


def test_hdc_screen_and_foreground_refreshes_foreground_after_lock_retry(monkeypatch):
    calls = []
    phase = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        phase["n"] += 1
        cmd_str = cmd[-1]
        m = re.search(r"(__VPD_SF_[0-9a-f]+__)", cmd_str)
        if m:
            sep = m.group(1)
            sections = [
                ("POWER", "Current State: AWAKE", 0),
                ("LOCK", "screenLocked: unknown", 0),
                ("AA", "Mission ID #1\n  bundle name [com.example.old]\n  state #FOREGROUND", 0),
            ]
            body = "".join(f"{sep}:BEGIN:{k}\r\n{v}\r\n\r\n{sep}:END:{k}:{rc}\r\n" for k, v, rc in sections)
            return subprocess.CompletedProcess(cmd, 0, body.encode("utf-8"), b"")
        m_scr = re.search(r"(__VPD_LK_[0-9a-f]+__)", cmd_str)
        if m_scr:
            sep = m_scr.group(1)
            sections = [
                ("POWER", "Current State: AWAKE", 0),
                ("LOCK", "screenLocked: false", 0),
            ]
            body = "".join(f"{sep}:BEGIN:{k}\r\n{v}\r\n\r\n{sep}:END:{k}:{rc}\r\n" for k, v, rc in sections)
            return subprocess.CompletedProcess(cmd, 0, body.encode("utf-8"), b"")
        if "aa" in cmd and "dump" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0,
                b"Mission ID #2\n  bundle name [com.example.refreshed]\n  state #FOREGROUND\n", b"",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dev = HdcDevice("synthetic-hdc")
    locked, fg = dev.screen_and_foreground()
    assert locked is False
    assert fg == "com.example.refreshed"
    assert len(calls) == 3


@pytest.mark.parametrize("bad_timeout", [float("nan"), float("inf"), float("-inf"), 0, -1, 61])
def test_login_rejects_non_finite_and_out_of_range_timeout(ws, tmp_path, bad_timeout):
    spec = _build_login_world(tmp_path, start="logged_out")
    dev = ws.device(spec)
    with pytest.raises(ValueError, match="timeout_s"):
        login_app(ws, ws.app("qiankun_hm"), dev, authorize=["account_state"], timeout_s=bad_timeout)


def test_login_propagates_action_required_from_dispatcher_pin_rejected(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900003333")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd333")
    spec = _build_login_world(tmp_path, start="password", post_login="car", unchecked_protocol=False)
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)

    from vpdtest.devices import DeviceResult
    from vpdtest.dispatch import Dispatcher
    orig_send = Dispatcher.send

    def fake_send(self, op, *args, preconditions=None, att=None, tap=None, transient=None):
        if att and att.get("step_id") == "login_focus_phone":
            att.update(status="screen_locked", action_required=True)
            return DeviceResult(op, False, False, "screen_locked", "pin rejected")
        return orig_send(self, op, *args, preconditions=preconditions, att=att, tap=tap, transient=transient)

    monkeypatch.setattr(Dispatcher, "send", fake_send)
    res = login_app(ws, app, dev, authorize=["account_state"])
    assert res["status"] == "blocked"
    assert res["action_required"] is True
    assert any(s.get("step") == "focus_phone" and s.get("action_required") is True for s in res["steps"])


def test_login_clear_text_passes_observed_action_epoch_and_stops_on_mid_clear_lock(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900004444")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd444")
    spec = _build_login_world(tmp_path, start="password", post_login="car", unchecked_protocol=False)
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    dev.bound["phone"] = "13800000000"

    seen = {}
    from vpdtest.devices import DeviceResult
    from vpdtest.dispatch import Dispatcher
    orig_send = Dispatcher.send

    def spy_send(self, op, *args, preconditions=None, att=None, tap=None, transient=None):
        if op == "clear_text":
            seen["has_epoch"] = tap is not None and "observed_action_epoch" in tap
            att.update(status="screen_locked", action_required=True)
            return DeviceResult(op, True, False, "screen_locked", "locked mid-clear")
        return orig_send(self, op, *args, preconditions=preconditions, att=att, tap=tap, transient=transient)

    monkeypatch.setattr(Dispatcher, "send", spy_send)
    res = login_app(ws, app, dev, authorize=["account_state"])
    assert seen.get("has_epoch") is True
    assert res["status"] == "blocked"
    assert res["action_required"] is True


def test_login_post_submit_benign_dialog_respects_budget_and_poll_limit(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900005555")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd555")
    orig_key = ReplayDevice.key
    monkeypatch.setattr(
        ReplayDevice, "key",
        lambda dev, name: dev._res("key_back") if name == "back" and dev.current_screen() == "password" else orig_key(dev, name),
    )

    # Persistent benign dialog after submit: must stop at replay poll limit (no infinite loop).
    spec = _build_login_world(tmp_path, start="password", post_login="car", unchecked_protocol=False)
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)

    from vpdtest import login as login_mod
    orig_classify = login_mod._classify_screen
    submitted = {"done": False}

    def sticky_dialog_classify(a, obs, tree):
        if obs.get("label", "").startswith("login_poll_"):
            submitted["done"] = True
            return "benign_dialog", {"name": "sticky_notice", "dismiss_text": "我知道了"}
        return orig_classify(a, obs, tree)

    monkeypatch.setattr(login_mod, "_classify_screen", sticky_dialog_classify)
    res = login_app(ws, app, dev, authorize=["account_state"], timeout_s=5.0)
    assert submitted["done"] is True
    assert res["status"] == "timeout"
    assert res["action_required"] is True


def test_login_post_submit_benign_dialog_stops_on_budget_exhausted(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900006666")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd666")
    orig_key = ReplayDevice.key
    monkeypatch.setattr(
        ReplayDevice, "key",
        lambda dev, name: dev._res("key_back") if name == "back" and dev.current_screen() == "password" else orig_key(dev, name),
    )

    spec = _build_login_world(tmp_path, start="password", post_login="car", unchecked_protocol=False)
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)

    from vpdtest import login as login_mod
    from vpdtest.devices import DeviceResult
    from vpdtest.dispatch import Dispatcher

    orig_classify = login_mod._classify_screen
    orig_send = Dispatcher.send

    def sticky_dialog_classify(a, obs, tree):
        if obs.get("label", "").startswith("login_poll_"):
            return "benign_dialog", {"name": "sticky_notice", "dismiss_text": "爱车"}
        return orig_classify(a, obs, tree)

    def budget_exhausted_on_dismiss(self, op, *args, preconditions=None, att=None, tap=None, transient=None):
        if att and str(att.get("step_id", "")).startswith("login_post_dismiss_"):
            att["status"] = "budget_exhausted"
            return DeviceResult(op, False, False, "budget_exhausted", "budget exhausted")
        return orig_send(self, op, *args, preconditions=preconditions, att=att, tap=tap, transient=transient)

    monkeypatch.setattr(login_mod, "_classify_screen", sticky_dialog_classify)
    monkeypatch.setattr(Dispatcher, "send", budget_exhausted_on_dismiss)
    res = login_app(ws, app, dev, authorize=["account_state"], timeout_s=5.0)
    assert res["status"] == "blocked"
    assert res["action_required"] is True
    assert "budget_exhausted" in res["reason"]

