"""Issue #32：覆盖物与窗口查询不妨碍取证，截图仍受 APP 白名单约束。"""
from types import SimpleNamespace

import pytest
from test_sysdialog import _covered_ctx, _register

from vpdtest.commands import cmd_sysdiag
from vpdtest.policy import Policy
from vpdtest.sysdialog import diagnose_cover, load_dialogs
from vpdtest.util import read_json

WINDOWS = [
    ["app0", "SCBScreenLock"], ["app0", "SCBBannerNotificatio"],
    ["app0", "SCBDropdownPanel"], ["app0", "SCBNegativeScreen"],
    ["app0", "SCBGlobalSearch"], ["app0", "softKeyboard1"],
    ["app0", "SCBKeyboardPanel"], None, [],
]


@pytest.mark.parametrize("windows", WINDOWS)
def test_observation_keeps_cover_tree_and_screenshot(ws, windows, monkeypatch):
    _register(ws)
    ctx, dev, store = _covered_ctx(ws)
    monkeypatch.setattr(dev, "shown_windows", lambda: windows)
    obs, tree = ctx.observe("cover-evidence")
    assert tree.status == "empty"
    assert obs["screen_cover"]["dump_ok"]
    assert obs["screen_cover"]["windows"] == windows
    assert (store.dir / obs["screen_cover"]["screen_tree"]).is_file()
    assert obs["screenshot"]["status"] == "ok"
    assert (store.dir / obs["screenshot"]["path"]).is_file()
    assert "private" not in obs["screen_cover"]


@pytest.mark.parametrize("windows", WINDOWS)
def test_sysdiag_keeps_cover_tree_and_screenshot(ws, windows, monkeypatch):
    _register(ws)
    _, dev, _ = _covered_ctx(ws)
    monkeypatch.setattr(dev, "shown_windows", lambda: windows)
    monkeypatch.setattr(ws, "device", lambda _: dev)
    out = cmd_sysdiag(ws, SimpleNamespace(app="motor_hm", device=dev.spec, dismiss=False))
    assert out["dump_ok"] and out["windows"] == windows
    d = ws.runs_dir / out["run"]
    assert (d / out["screen_tree"]).is_file()
    assert out["screenshot"]["status"] == "ok"
    assert (d / out["screenshot"]["path"]).is_file()


def test_diagnosis_without_window_query_still_collects(ws):
    _register(ws)
    _, dev, store = _covered_ctx(ws)
    without_query = SimpleNamespace(dump_screen=dev.dump_screen)
    diag = diagnose_cover(without_query, store, store.dir / "diag", load_dialogs(ws.root))
    assert diag["windows"] is None and diag["dump_ok"]
    assert diag["dump_error"] is None and diag["matched"] == "usb_mode"
    assert read_json(store.dir / diag["screen_tree"])


@pytest.mark.parametrize("foreground", [None, "com.tencent.mm"])
def test_sysdiag_keeps_tree_but_respects_screenshot_app_boundary(ws, foreground, monkeypatch):
    _, dev, _ = _covered_ctx(ws)
    monkeypatch.setattr(dev, "foreground", lambda: foreground)
    monkeypatch.setattr(ws, "device", lambda _: dev)
    out = cmd_sysdiag(ws, SimpleNamespace(app="motor_hm", device=dev.spec, dismiss=False))
    assert out["dump_ok"] and out["screen_tree"]
    assert out["screenshot"]["status"] == "withheld"
    assert not list((ws.runs_dir / out["run"]).rglob("screen.png"))


def test_legacy_privacy_config_cannot_bypass_app_whitelist():
    pol = Policy({"allowed_packages": ["test.app"], "privacy": {"screenshot_only_when_target_foreground": False}})
    assert pol.screenshot_allowed("test.app")
    assert not pol.screenshot_allowed("other.app")
    assert not pol.screenshot_allowed(None)
    assert not hasattr(pol, "privacy")


def test_changed_cover_gets_distinct_current_evidence(ws, monkeypatch):
    _register(ws)
    ctx, dev, store = _covered_ctx(ws)
    windows = {"now": ["app0", "UsbModeDialog"]}
    monkeypatch.setattr(dev, "shown_windows", lambda: windows["now"])
    first, _ = ctx.observe("first-cover", screenshot=False)
    windows["now"] = ["app0", "SCBScreenLock"]
    second, _ = ctx.observe("changed-cover", screenshot=False)
    assert first["screen_cover"]["windows"] != second["screen_cover"]["windows"]
    a, b = first["screen_cover"]["screen_tree"], second["screen_cover"]["screen_tree"]
    assert a != b and (store.dir / a).is_file() and (store.dir / b).is_file()
    assert first["screenshot"]["status"] == second["screenshot"]["status"] == "skipped"


@pytest.mark.parametrize("after", [None, "com.tencent.mm", "com.yinwang.qiankunapp.hm"])
def test_sysdiag_capture_switch_uses_app_whitelist(ws, after, monkeypatch):
    _, dev, _ = _covered_ctx(ws)
    real = dev.screenshot

    def capture(path):
        r = real(path)
        dev.state["foreground"] = after
        return r
    monkeypatch.setattr(dev, "screenshot", capture)
    monkeypatch.setattr(ws, "device", lambda _: dev)
    out = cmd_sysdiag(ws, SimpleNamespace(app="motor_hm", device=dev.spec, dismiss=False))
    assert out["foreground_after"] == after
    allowed = after == "com.yinwang.qiankunapp.hm"
    assert out["screenshot"]["status"] == ("ok" if allowed else "withheld")
    assert bool(list((ws.runs_dir / out["run"]).rglob("screen.png"))) is allowed


def test_device_error_keeps_original_value_in_events_run_and_report(ws, monkeypatch):
    from conftest import world
    from test_qiankun_cases import AUTH_101, PASSWORD, PHONE

    from vpdtest.devices import DeviceResult
    from vpdtest.devices.replay import ReplayDevice
    from vpdtest.runner import run_case
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", PHONE)
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", PASSWORD)
    error = f"fixture-input-failed:{PHONE}"
    monkeypatch.setattr(ReplayDevice, "input_text", lambda *a: DeviceResult("input_text", True, False, "command_failed", error))
    run = run_case(ws, "TC-QK-101", device_spec=world("qiankun_hm_base.json"), propose=False, authorize=AUTH_101)
    d = ws.runs_dir / run["run_id"]
    assert error in (d / "events.jsonl").read_text(encoding="utf-8")
    assert error in (d / "run.json").read_text(encoding="utf-8")
    assert error in (d / "report.md").read_text(encoding="utf-8")
    assert "clipboard_may_hold_secret" not in run and "system_overlays" not in run
