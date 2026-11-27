"""Atomic navigation capability (`vpdtest switch-vehicle` & `vpdtest enter-vpd`) tests (synthetic only)."""
from __future__ import annotations

import json
import pytest

from vpdtest import cli
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.dispatch import Dispatcher
from vpdtest.util import write_json


def _nav_world(
    tmp_path,
    *,
    start: str = "car_tab",
    vin_on_page: str = "000228",
    scrolled_vin: bool = False,
    ambiguous_vin: bool = False,
    non_whitelist_home: bool = False,
    ads_target: str = "vpd_home",
    ads_scrolled: bool = False,
    with_post_dialog: bool = False,
):
    tab_children = [
        {"t": "Text", "text": "爱车", "sel": True, "click": True, "b": [550, 2620, 720, 2720]},
    ]
    if not ads_scrolled:
        tab_children.append({"t": "Text", "text": "ADS", "click": True, "go": ads_target, "b": [320, 2620, 460, 2720]})

    car_children = [
        {"t": "Image", "id": "vehicleListBgImg", "b": [0, 0, 1320, 160]},
        {"t": "Column", "b": [0, 160, 1320, 320], "children": [
            {"t": "Row", "b": [0, 160, 1320, 260], "children": [
                {"t": "Row", "b": [40, 180, 420, 260], "children": [
                    {"t": "Text", "text": "问界K89", "click": True, "go": "veh_list", "b": [40, 180, 420, 260]},
                ]},
            ]},
        ]},
        {"t": "Text", "text": "车锁", "b": [100, 1500, 220, 1560]},
        {"t": "Row", "b": [0, 2600, 1320, 2760], "children": tab_children},
    ]

    car_tab_ads = {
        "root": {
            "t": "Column", "b": [0, 0, 1320, 2848],
            "children": [
                {"t": "Image", "id": "vehicleListBgImg", "b": [0, 0, 1320, 160]},
                {"t": "Column", "b": [0, 160, 1320, 320], "children": [
                    {"t": "Row", "b": [0, 160, 1320, 260], "children": [
                        {"t": "Row", "b": [40, 180, 420, 260], "children": [
                            {"t": "Text", "text": "问界K89", "click": True, "go": "veh_list", "b": [40, 180, 420, 260]},
                        ]},
                    ]},
                ]},
                {"t": "Text", "text": "爱车", "sel": True, "click": True, "b": [550, 2620, 720, 2720]},
                {"t": "Text", "text": "ADS", "click": True, "go": ads_target, "b": [320, 2620, 460, 2720]},
            ],
        }
    }

    veh_rows = [
        {"t": "Text", "text": "我的车辆", "b": [40, 120, 320, 180]},
        {"t": "Text", "text": "车架号 999111(后6位)", "click": True, "go": "car_tab", "b": [60, 320, 700, 390]},
    ]
    if not scrolled_vin:
        veh_rows.append({"t": "Text", "text": f"车架号 {vin_on_page}(后6位)", "click": True, "go": "car_tab", "b": [60, 440, 700, 510]})
        if ambiguous_vin:
            veh_rows.append({"t": "Text", "text": f"车架号 {vin_on_page}(后六位)", "click": True, "go": "car_tab", "b": [60, 560, 700, 630]})

    veh_list_scrolled = {
        "root": {
            "t": "Column", "b": [0, 0, 1320, 2848],
            "children": [
                {"t": "Text", "text": "我的车辆", "b": [40, 120, 320, 180]},
                {"t": "Text", "text": f"车架号 {vin_on_page}(后6位)", "click": True, "go": "car_tab", "b": [60, 440, 700, 510]},
            ],
        }
    }

    if non_whitelist_home:
        vpd_home_children = [
            {"t": "Stack", "id": "VPD_HOME_VIEW", "b": [0, 0, 1320, 2848], "children": [
                {"t": "Text", "text": "伟业金锦苑", "b": [54, 1463, 460, 1526]},
                {"t": "Text", "text": "搜索", "b": [216, 1639, 338, 1710]},
                {"t": "Text", "text": "收藏及常去", "b": [216, 1739, 500, 1810]},
                {"t": "Text", "text": "停泊位置", "b": [216, 1839, 500, 1910]},
            ]},
        ]
    else:
        vpd_home_children = [
            {"t": "Stack", "id": "VPD_HOME_VIEW", "b": [0, 0, 1320, 2848], "children": [
                {"t": "Row", "b": [54, 1460, 1266, 1530], "children": [
                    {"t": "Text", "text": "金桥出口加工区", "b": [54, 1463, 460, 1526]},
                    {"t": "Text", "text": "泊车代驾", "b": [480, 1464, 680, 1525]},
                ]},
                {"t": "Text", "text": "搜索", "b": [216, 1639, 338, 1710]},
            ]},
        ]

    pwd_children = [
        {"t": "Text", "text": "欢迎登录华为乾崑", "b": [30, 30, 400, 80]},
        {"t": "TextInput", "text": "", "click": True, "bind": "phone", "text_bound": "phone",
         "attrs": {"hint": "请输入手机号"}, "b": [30, 140, 400, 190]},
        {"t": "TextInput", "text": "", "click": True, "bind": "pw", "text_bound": "pw",
         "attrs": {"hint": "请输入密码"}, "b": [30, 210, 400, 260]},
        {"t": "Checkbox", "text": "userProtocol", "click": True, "attrs": {"checked": "true"}, "b": [30, 280, 90, 320]},
        {"t": "Button", "text": "登录", "click": True, "go": "car_tab", "b": [30, 390, 400, 450]},
    ]

    screens = {
        "car_tab": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": car_children}},
        "other_tab_with_ads": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "充电服务", "b": [54, 1645, 270, 1716]},
            {"t": "Row", "b": [0, 2600, 1320, 2760], "children": [
                {"t": "Text", "text": "ADS", "click": True, "sel": True, "b": [320, 2620, 460, 2720]},
                {"t": "Text", "text": "爱车", "click": True, "go": "car_tab", "b": [550, 2620, 720, 2720]},
            ]},
        ]}},
        "car_tab_ads": car_tab_ads,
        "veh_list": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": veh_rows}},
        "veh_list_scrolled": veh_list_scrolled,
        "vpd_home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": vpd_home_children}},
        "vpd_dialog": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "请下载车辆资源包", "b": [100, 500, 600, 560]},
            {"t": "Button", "text": "下次再说", "click": True, "go": "vpd_home", "b": [200, 600, 500, 660]},
        ]}},
        "vpd_load_failed": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "停车场信息加载失败", "b": [100, 500, 600, 560]},
            {"t": "Text", "text": "搜索车位号设为目的地", "b": [100, 600, 600, 660]},
        ]}},
        "vpd_location_off": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Stack", "id": "VPD_HOME_VIEW", "b": [0, 132, 1320, 2570], "children": [
                {"t": "Text", "text": "定位权限未开启", "b": [165, 1440, 496, 1501]},
                {"t": "Text", "text": "充电服务", "b": [54, 1645, 270, 1716]},
            ]},
        ]}},
        "vpd_charging_only": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Stack", "id": "VPD_HOME_VIEW", "b": [0, 132, 1320, 2570], "children": [
                {"t": "Text", "text": "充电服务", "b": [54, 1645, 270, 1716]},
                {"t": "Text", "text": "附近充电", "b": [54, 1750, 270, 1810]},
            ]},
        ]}},
        "safe_code": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "请输入安全码", "b": [100, 300, 600, 360]},
        ]}},
        "logged_out": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "我的车辆", "b": [40, 100, 300, 160]},
            {"t": "Button", "text": "登录/注册", "click": True, "go": "password", "b": [40, 200, 300, 260]},
        ]}},
        "password": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": pwd_children}},
    }
    if with_post_dialog:
        screens["car_tab"]["root"]["children"][3] = {"t": "Text", "text": "ADS", "click": True, "go": "vpd_dialog", "b": [320, 2620, 460, 2720]}

    data = {
        "bundle": "com.yinwang.qiankunapp.hm",
        "app_version": "2.0.4.100",
        "screen": [1320, 2848],
        "display": {"density": 3.375, "device_form": "bar", "fold_status": "unknown"},
        "initial": {"installed": True, "foreground": "com.ohos.sceneboard"},
        "launch_screen": start,
        "screens": screens,
    }
    path = tmp_path / f"nav-{start}-{vin_on_page}-{scrolled_vin}-{ambiguous_vin}-{non_whitelist_home}.json"
    write_json(path, data)
    return "replay:" + str(path)


def test_switch_vehicle_requires_vehicle_selection_authorization(ws, tmp_path):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path)
    dev = ws.device(spec)
    res = switch_vehicle_app(ws, ws.app("qiankun_hm"), dev, vin_tail="000228", authorize=[])
    assert res["status"] == "authorization_required"
    assert res["actions"] == 0
    assert res["action_required"] is False


@pytest.mark.parametrize("bad_vin", ["", "   ", "000-228", "A" * 18])
def test_switch_vehicle_rejects_invalid_vin_tail(ws, tmp_path, bad_vin):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path)
    dev = ws.device(spec)
    with pytest.raises(ValueError, match="vin-tail"):
        switch_vehicle_app(ws, ws.app("qiankun_hm"), dev, vin_tail=bad_vin, authorize=["vehicle_selection"])


@pytest.mark.parametrize("bad_timeout", [float("nan"), float("inf"), 0, -2, 61])
def test_switch_vehicle_rejects_non_finite_timeout(ws, tmp_path, bad_timeout):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path)
    dev = ws.device(spec)
    with pytest.raises(ValueError, match="timeout_s"):
        switch_vehicle_app(ws, ws.app("qiankun_hm"), dev, vin_tail="000228",
                           authorize=["vehicle_selection"], timeout_s=bad_timeout)


def test_switch_vehicle_switches_and_scrolls_when_needed(ws, tmp_path, monkeypatch):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="car_tab", scrolled_vin=True)
    dev = ws.device(spec)
    orig_swipe = ReplayDevice.swipe

    def swipe_to_scrolled(d, x1, y1, x2, y2, speed=None):
        if d.current_screen() == "veh_list":
            d.state["stack"][-1] = "veh_list_scrolled"
            return d._res("swipe", raw="scrolled vehicle list")
        return orig_swipe(d, x1, y1, x2, y2, speed=speed)

    monkeypatch.setattr(ReplayDevice, "swipe", swipe_to_scrolled)
    res = switch_vehicle_app(ws, ws.app("qiankun_hm"), dev, vin_tail="000228", authorize=["vehicle_selection"])
    assert res["status"] == "switched", res
    assert res["current_vehicle"] == "问界K89"
    assert res["action_required"] is False
    assert any(s.get("step", "").startswith("scroll_vin_") for s in res["steps"])


def test_switch_vehicle_stops_on_ambiguous_vin_match(ws, tmp_path):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="veh_list", ambiguous_vin=True)
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    res = switch_vehicle_app(ws, app, dev, vin_tail="000228", authorize=["vehicle_selection"])
    assert res["status"] == "ambiguous"
    assert res["action_required"] is False


def test_switch_vehicle_propagates_action_required_on_pin_rejected(ws, tmp_path, monkeypatch):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="car_tab")
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    orig_send = Dispatcher.send

    def fake_send(self, op, *args, preconditions=None, att=None, tap=None, transient=None):
        if att and att.get("step_id") == "sv_open_vehicle_list":
            att.update(status="screen_locked", action_required=True)
            return DeviceResult(op, False, False, "screen_locked", "pin rejected")
        return orig_send(self, op, *args, preconditions=preconditions, att=att, tap=tap, transient=transient)

    monkeypatch.setattr(Dispatcher, "send", fake_send)
    res = switch_vehicle_app(ws, app, dev, vin_tail="000228", authorize=["vehicle_selection"])
    assert res["status"] == "blocked"
    assert res["action_required"] is True


def test_enter_vpd_already_ready_zero_actions(ws, tmp_path):
    from vpdtest.vpd_nav import enter_vpd_app
    spec = _nav_world(tmp_path, start="vpd_home", non_whitelist_home=False)
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    res = enter_vpd_app(ws, app, dev)
    assert res["status"] == "ready"
    assert res["method"] == "already"
    assert res["park_name"] == "金桥出口加工区"
    assert res["actions"] == 0
    assert res["action_required"] is False


def test_enter_vpd_works_on_non_whitelist_three_anchor_home(ws, tmp_path):
    from vpdtest.vpd_nav import enter_vpd_app
    spec = _nav_world(tmp_path, start="car_tab", non_whitelist_home=True)
    res = enter_vpd_app(ws, ws.app("qiankun_hm"), ws.device(spec))
    assert res["status"] == "ready", res
    assert res["method"] == "entered"
    assert res["action_required"] is False


def test_enter_vpd_with_vin_tail_auth_gate_returns_zero_actions(ws, tmp_path, capsys):
    rc = cli.main(["--workspace", str(ws.root), "enter-vpd", "--app", "qiankun_hm",
                   "--device", _nav_world(tmp_path), "--vin-tail", "000228"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["status"] == "authorization_required"
    assert out["actions"] == 0


def test_enter_vpd_chains_auto_login_and_switch_vehicle_then_enters_vpd(ws, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900007777")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd777")
    orig_key = ReplayDevice.key
    monkeypatch.setattr(
        ReplayDevice, "key",
        lambda d, name: d._res("key_back") if name == "back" and d.current_screen() == "password" else orig_key(d, name),
    )
    spec = _nav_world(tmp_path, start="logged_out")
    rc = cli.main(["--workspace", str(ws.root), "enter-vpd", "--app", "qiankun_hm", "--device", spec,
                   "--vin-tail", "000228", "--authorize", "account_state", "vehicle_selection"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    assert out["status"] == "ready"
    assert out["method"] == "entered"
    assert out["park_name"] == "金桥出口加工区"
    assert out["actions"] > 0
    assert any(s["step"] == "login" and s["status"] == "logged_in" for s in out["steps"])
    assert any(s["step"] == "switch_vehicle" and s["status"] == "switched" for s in out["steps"])


def test_enter_vpd_stops_for_login_when_logged_out_without_account_state(ws, tmp_path, capsys):
    spec = _nav_world(tmp_path, start="logged_out")
    rc = cli.main(["--workspace", str(ws.root), "enter-vpd", "--app", "qiankun_hm", "--device", spec])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out["status"] == "login_required"
    assert out["action_required"] is True
    assert out.get("human_handoffs")


def test_enter_vpd_detects_safe_code_and_load_failed_and_dialog_dismissal(ws, tmp_path, monkeypatch):
    from vpdtest.vpd_nav import enter_vpd_app

    # 1) safe_code_required
    spec_sc = _nav_world(tmp_path, start="car_tab", ads_target="safe_code")
    res_sc = enter_vpd_app(ws, ws.app("qiankun_hm"), ws.device(spec_sc))
    assert res_sc["status"] == "safe_code_required" and res_sc["action_required"] is True

    # 2) load_failed
    spec_lf = _nav_world(tmp_path, start="car_tab", ads_target="vpd_load_failed")
    res_lf = enter_vpd_app(ws, ws.app("qiankun_hm"), ws.device(spec_lf))
    assert res_lf["status"] == "load_failed" and res_lf["action_required"] is False

    # 3) benign dialog dismissed after tap_ads -> enters vpd_home
    spec_dlg = _nav_world(tmp_path, start="car_tab", ads_target="vpd_dialog")
    res_dlg = enter_vpd_app(ws, ws.app("qiankun_hm"), ws.device(spec_dlg))
    assert res_dlg["status"] == "ready"
    assert any(s["step"].startswith("post_dismiss_") for s in res_dlg["steps"])


def test_enter_vpd_post_dialog_budget_exhausted_stops_cleanly(ws, tmp_path, monkeypatch):
    from vpdtest.vpd_nav import enter_vpd_app
    spec_dlg = _nav_world(tmp_path, start="car_tab", ads_target="vpd_dialog")
    orig_send = Dispatcher.send

    def fail_dismiss(self, op, *args, preconditions=None, att=None, tap=None, transient=None):
        if att and str(att.get("step_id", "")).startswith("ev_post_dismiss_"):
            att["status"] = "budget_exhausted"
            return DeviceResult(op, False, False, "budget_exhausted", "budget exhausted")
        return orig_send(self, op, *args, preconditions=preconditions, att=att, tap=tap, transient=transient)

    monkeypatch.setattr(Dispatcher, "send", fail_dismiss)
    res = enter_vpd_app(ws, ws.app("qiankun_hm"), ws.device(spec_dlg))
    assert res["status"] == "blocked"
    assert res["action_required"] is True
    assert "budget_exhausted" in res["reason"]


def test_switch_vehicle_navigates_to_car_tab_even_when_ads_tab_visible(ws, tmp_path):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="other_tab_with_ads")
    res = switch_vehicle_app(ws, ws.app("qiankun_hm"), ws.device(spec), vin_tail="000228",
                             authorize=["vehicle_selection"])
    assert res["status"] == "switched", res
    assert any(s.get("step", "").startswith("tap_car_tab_") for s in res["steps"])


def test_enter_vpd_fast_fails_on_location_permission_or_charging_only_view(ws, tmp_path):
    from vpdtest.vpd_nav import enter_vpd_app

    spec_loc = _nav_world(tmp_path, start="car_tab", ads_target="vpd_location_off")
    res_loc = enter_vpd_app(ws, ws.app("qiankun_hm"), ws.device(spec_loc))
    assert res_loc["status"] == "location_permission_required"
    assert res_loc["action_required"] is True

    spec_chg = _nav_world(tmp_path, start="car_tab", ads_target="vpd_charging_only")
    res_chg = enter_vpd_app(ws, ws.app("qiankun_hm"), ws.device(spec_chg))
    assert res_chg["status"] == "vpd_unavailable"
    assert res_chg["action_required"] is False
