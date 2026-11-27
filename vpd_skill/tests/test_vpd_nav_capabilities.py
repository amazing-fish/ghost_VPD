"""Atomic navigation capability (`vpdtest switch-vehicle` & `vpdtest enter-vpd`) tests (synthetic only)."""
from __future__ import annotations

import json
import time
import pytest

from vpdtest import cli
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.dispatch import Dispatcher
from vpdtest.util import write_json


def _car_screen(vehicle_name: str, *, ads_target: str = "vpd_home", ads_scrolled: bool = False):
    tab_children = [
        {"t": "Text", "text": "爱车", "sel": True, "click": True, "b": [550, 2620, 720, 2720]},
    ]
    if not ads_scrolled:
        tab_children.append({"t": "Text", "text": "ADS", "click": True, "go": ads_target, "b": [320, 2620, 460, 2720]})
    return {
        "root": {
            "t": "Column", "b": [0, 0, 1320, 2848],
            "children": [
                {"t": "Image", "id": "vehicleListBgImg", "b": [0, 0, 1320, 160]},
                {"t": "Column", "b": [0, 160, 1320, 320], "children": [
                    {"t": "Row", "b": [0, 160, 1320, 260], "children": [
                        {"t": "Row", "b": [40, 180, 420, 260], "children": [
                            {"t": "Text", "text": vehicle_name, "click": True, "go": "veh_list", "b": [40, 180, 420, 260]},
                        ]},
                    ]},
                ]},
                {"t": "Text", "text": "车锁", "b": [100, 1500, 220, 1560]},
                {"t": "Row", "b": [0, 2600, 1320, 2760], "children": tab_children},
            ],
        }
    }


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
    post_vin_screen: str = "car_tab_k89",
):
    veh_rows = [
        {"t": "Text", "text": "我的车辆", "b": [40, 120, 320, 180]},
        {"t": "Row", "click": True, "go": "car_tab", "b": [40, 300, 1200, 400], "children": [
            {"t": "Text", "text": "问界M9", "b": [60, 305, 400, 340]},
            {"t": "Text", "text": "车架号 999111(后6位)", "b": [60, 345, 700, 390]},
        ]},
    ]
    if not scrolled_vin:
        veh_rows.append({"t": "Row", "click": True, "go": post_vin_screen, "b": [40, 420, 1200, 520], "children": [
            {"t": "Text", "text": "问界K89", "b": [60, 425, 400, 460]},
            {"t": "Text", "text": f"车架号 {vin_on_page}(后6位)", "b": [60, 465, 700, 510]},
        ]})
        if ambiguous_vin:
            veh_rows.append({"t": "Row", "click": True, "go": post_vin_screen, "b": [40, 540, 1200, 640], "children": [
                {"t": "Text", "text": "赛力斯M9", "b": [60, 545, 400, 580]},
                {"t": "Text", "text": f"车架号 {vin_on_page}(后六位)", "b": [60, 585, 700, 630]},
            ]})

    veh_list_scrolled = {
        "root": {
            "t": "Column", "b": [0, 0, 1320, 2848],
            "children": [
                {"t": "Text", "text": "我的车辆", "b": [40, 120, 320, 180]},
                {"t": "Row", "click": True, "go": post_vin_screen, "b": [40, 420, 1200, 520], "children": [
                    {"t": "Text", "text": "问界K89", "b": [60, 425, 400, 460]},
                    {"t": "Text", "text": f"车架号 {vin_on_page}(后6位)", "b": [60, 465, 700, 510]},
                ]},
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
        "car_tab": _car_screen("问界M9", ads_target=ads_target, ads_scrolled=ads_scrolled),
        "car_tab_k89": _car_screen("问界K89", ads_target=ads_target, ads_scrolled=False),
        "other_tab_with_ads": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "充电服务", "b": [54, 1645, 270, 1716]},
            {"t": "Row", "b": [0, 2600, 1320, 2760], "children": [
                {"t": "Text", "text": "ADS", "click": True, "sel": True, "b": [320, 2620, 460, 2720]},
                {"t": "Text", "text": "爱车", "click": True, "go": "car_tab", "b": [550, 2620, 720, 2720]},
            ]},
        ]}},
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
        "sms_verify": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "身份验证", "b": [100, 300, 600, 360]},
            {"t": "Text", "text": "验证码已发至151****0000", "b": [100, 400, 700, 460]},
        ]}},
        "logged_out_dialog": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "请下载车辆资源包", "b": [100, 500, 600, 560]},
            {"t": "Button", "text": "下次再说", "click": True, "go": "logged_out", "b": [200, 600, 500, 660]},
        ]}},
        "logged_out": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "我的车辆", "b": [40, 100, 300, 160]},
            {"t": "Button", "text": "登录/注册", "click": True, "go": "password", "b": [40, 200, 300, 260]},
        ]}},
        "password": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": pwd_children}},
        "unclassified": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "未知占位页", "b": [100, 100, 500, 160]},
        ]}},
    }

    data = {
        "bundle": "com.yinwang.qiankunapp.hm",
        "app_version": "2.0.4.100",
        "screen": [1320, 2848],
        "display": {"density": 3.375, "device_form": "bar", "fold_status": "unknown"},
        "initial": {"installed": True, "foreground": "com.ohos.sceneboard"},
        "launch_screen": start,
        "screens": screens,
    }
    path = tmp_path / f"nav-{start}-{vin_on_page}-{scrolled_vin}-{ambiguous_vin}-{non_whitelist_home}-{post_vin_screen}.json"
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


def test_switch_vehicle_changes_from_m9_to_k89_and_scrolls_when_needed(ws, tmp_path, monkeypatch):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="car_tab", scrolled_vin=True, post_vin_screen="car_tab_k89")
    dev = ws.device(spec)
    orig_swipe = ReplayDevice.swipe

    def swipe_to_scrolled(d, x1, y1, x2, y2, speed=2000):
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


def test_switch_vehicle_rejects_unchanged_vehicle_readback_as_unconfirmed(ws, tmp_path):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="car_tab", post_vin_screen="car_tab")
    res = switch_vehicle_app(ws, ws.app("qiankun_hm"), ws.device(spec), vin_tail="000228",
                             authorize=["vehicle_selection"])
    assert res["status"] == "unconfirmed", res
    assert res["action_required"] is True
    assert res["current_vehicle"] == "问界M9"


def test_switch_vehicle_stops_on_ambiguous_vin_match(ws, tmp_path):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="veh_list", ambiguous_vin=True)
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    res = switch_vehicle_app(ws, app, dev, vin_tail="000228", authorize=["vehicle_selection"])
    assert res["status"] == "ambiguous"
    assert res["action_required"] is False


def test_switch_vehicle_scans_full_list_detects_cross_page_duplicate_and_inconclusive(ws, tmp_path, monkeypatch):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="veh_list", scrolled_vin=False)
    app = ws.app("qiankun_hm")
    from vpdtest.util import read_json
    p = spec.split("replay:", 1)[1]
    w = read_json(p)
    w["screens"]["veh_list_p2"] = {
        "root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "我的车辆", "b": [40, 120, 320, 180]},
            {"t": "Row", "click": True, "go": "car_tab_k89", "b": [40, 420, 1200, 520], "children": [
                {"t": "Text", "text": "赛力斯M9", "b": [60, 425, 400, 460]},
                {"t": "Text", "text": "车架号 000228(后六位)", "b": [60, 465, 700, 510]},
            ]},
        ]}
    }
    write_json(p, w)

    orig_swipe = ReplayDevice.swipe
    def swipe_to_p2(d, x1, y1, x2, y2, speed=2000):
        if d.current_screen() == "veh_list" and y1 > y2:
            d.state["stack"][-1] = "veh_list_p2"
            return d._res("swipe", raw="page 2")
        return orig_swipe(d, x1, y1, x2, y2, speed=speed)

    monkeypatch.setattr(ReplayDevice, "swipe", swipe_to_p2)
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    res_dup = switch_vehicle_app(ws, app, dev, vin_tail="000228", authorize=["vehicle_selection"])
    assert res_dup["status"] == "ambiguous", res_dup

    # Non-terminating scroll list (never reaches bottom within max_scrolls) -> inconclusive
    counter = {"n": 0}
    w["screens"]["veh_list_p2"]["root"]["children"] = [
        {"t": "Text", "text": "我的车辆", "b": [40, 120, 320, 180]},
    ]
    write_json(p, w)

    def swipe_infinite(d, x1, y1, x2, y2, speed=2000):
        counter["n"] += 1
        sid = f"dyn_{counter['n']}"
        d.world["screens"][sid] = {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": f"车架号 X{counter['n']:05d}(后六位)", "b": [60, 300, 700, 360]},
        ]}}
        d.state["stack"][-1] = sid
        return d._res("swipe", raw=sid)

    monkeypatch.setattr(ReplayDevice, "swipe", swipe_infinite)
    dev2 = ws.device(spec)
    dev2.launch(app.bundle, app.ability)
    res_inc = switch_vehicle_app(ws, app, dev2, vin_tail="000228", authorize=["vehicle_selection"])
    assert res_inc["status"] == "inconclusive", res_inc
    assert res_inc["action_required"] is True


def test_switch_vehicle_scans_to_bottom_and_scrolls_back_to_unique_match(ws, tmp_path, monkeypatch):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="veh_list", scrolled_vin=False, post_vin_screen="car_tab_k89")
    app = ws.app("qiankun_hm")
    from vpdtest.util import read_json
    p = spec.split("replay:", 1)[1]
    w = read_json(p)
    w["screens"]["veh_list_p2"] = {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Text", "text": "我的车辆", "b": [40, 120, 320, 180]},
        {"t": "Text", "text": "车架号 777888(后六位)", "b": [60, 320, 700, 380]},
    ]}}
    write_json(p, w)

    orig_swipe = ReplayDevice.swipe
    def swipe_both(d, x1, y1, x2, y2, speed=2000):
        if y1 > y2 and d.current_screen() == "veh_list":
            d.state["stack"][-1] = "veh_list_p2"
        elif y1 < y2 and d.current_screen() == "veh_list_p2":
            d.state["stack"][-1] = "veh_list"
        return orig_swipe(d, x1, y1, x2, y2, speed=speed)

    monkeypatch.setattr(ReplayDevice, "swipe", swipe_both)
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    res = switch_vehicle_app(ws, app, dev, vin_tail="000228", authorize=["vehicle_selection"])
    assert res["status"] == "switched", res
    assert res["current_vehicle"] == "问界K89"
    assert any(s.get("step", "").startswith("scroll_back_") for s in res["steps"])


def test_switch_vehicle_open_list_or_vin_tap_sms_and_pin_rejected(ws, tmp_path, monkeypatch):
    from vpdtest.vpd_nav import switch_vehicle_app
    app = ws.app("qiankun_hm")

    # 1) open_vehicle_list lands on sms_verify -> sms_required
    spec_sms = _nav_world(tmp_path, start="car_tab")
    from vpdtest.util import read_json
    p = spec_sms.split("replay:", 1)[1]
    w = read_json(p)
    w["screens"]["car_tab"]["root"]["children"][1]["children"][0]["children"][0]["children"][0]["go"] = "sms_verify"
    write_json(p, w)
    res_sms = switch_vehicle_app(ws, app, ws.device(spec_sms), vin_tail="000228", authorize=["vehicle_selection"])
    assert res_sms["status"] == "sms_required" and res_sms["action_required"] is True

    # 2) pin_rejected propagation
    spec_pin = _nav_world(tmp_path, start="car_tab")
    dev = ws.device(spec_pin)
    dev.launch(app.bundle, app.ability)
    orig_send = Dispatcher.send

    def fake_send(self, op, *args, preconditions=None, att=None, tap=None, transient=None):
        if att and att.get("step_id") == "sv_open_vehicle_list":
            att.update(status="screen_locked", action_required=True)
            return DeviceResult(op, False, False, "screen_locked", "pin rejected")
        return orig_send(self, op, *args, preconditions=preconditions, att=att, tap=tap, transient=transient)

    monkeypatch.setattr(Dispatcher, "send", fake_send)
    res_pin = switch_vehicle_app(ws, app, dev, vin_tail="000228", authorize=["vehicle_selection"])
    assert res_pin["status"] == "blocked" and res_pin["action_required"] is True


def test_enter_vpd_already_ready_zero_actions_and_replay_launch_cap(ws, tmp_path):
    from vpdtest.vpd_nav import enter_vpd_app
    spec = _nav_world(tmp_path, start="vpd_home", non_whitelist_home=False)
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    res = enter_vpd_app(ws, app, dev)
    assert res["status"] == "ready" and res["method"] == "already" and res["actions"] == 0
    assert res["park_name"] == "金桥出口加工区"

    # Replay launch polling on unclassified screen must stop quickly (not 8 real seconds)
    t0 = time.monotonic()
    spec_unk = _nav_world(tmp_path, start="unclassified")
    res_unk = enter_vpd_app(ws, app, ws.device(spec_unk))
    assert time.monotonic() - t0 < 2.0
    assert res_unk["action_required"] is True


def test_enter_vpd_works_on_non_whitelist_three_anchor_home(ws, tmp_path):
    from vpdtest.vpd_nav import enter_vpd_app
    spec = _nav_world(tmp_path, start="car_tab", non_whitelist_home=True)
    res = enter_vpd_app(ws, ws.app("qiankun_hm"), ws.device(spec))
    assert res["status"] == "ready", res
    assert res["method"] == "entered"


def test_enter_vpd_with_vin_tail_auth_gate_returns_zero_actions(ws, tmp_path, capsys):
    rc = cli.main(["--workspace", str(ws.root), "enter-vpd", "--app", "qiankun_hm",
                   "--device", _nav_world(tmp_path), "--vin-tail", "000228"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "authorization_required" and out["actions"] == 0


def test_enter_vpd_chains_auto_login_switch_vehicle_and_shares_budget(ws, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900007777")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd777")
    orig_key = ReplayDevice.key
    monkeypatch.setattr(
        ReplayDevice, "key",
        lambda d, name: d._res("key_back") if name == "back" and d.current_screen() == "password" else orig_key(d, name),
    )
    spec = _nav_world(tmp_path, start="logged_out_dialog", post_vin_screen="car_tab_k89")
    rc = cli.main(["--workspace", str(ws.root), "enter-vpd", "--app", "qiankun_hm", "--device", spec,
                   "--vin-tail", "000228", "--authorize", "account_state", "vehicle_selection"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    assert out["status"] == "ready"
    assert any(s["step"] == "pre_dismiss_qk_vehicle_resource_dialog" for s in out["steps"])
    assert any(s["step"] == "login" and s["status"] == "logged_in" for s in out["steps"])
    assert any(s["step"] == "switch_vehicle" and s["current_vehicle"] == "问界K89" for s in out["steps"])

    # Shared budget check: tight max_actions_per_run=5 stops inside child login/switch rather than resetting budget
    p_data = ws.root / "policy.json"
    from vpdtest.util import read_json
    pol = read_json(p_data)
    pol["max_actions_per_run"] = 5
    write_json(p_data, pol)
    rc2 = cli.main(["--workspace", str(ws.root), "enter-vpd", "--app", "qiankun_hm", "--device", spec,
                    "--vin-tail", "000228", "--authorize", "account_state", "vehicle_selection"])
    out2 = json.loads(capsys.readouterr().out)
    assert out2["actions"] <= 5
    assert out2["status"] != "ready"


def test_enter_vpd_stops_for_login_when_logged_out_without_account_state(ws, tmp_path, capsys):
    spec = _nav_world(tmp_path, start="logged_out")
    rc = cli.main(["--workspace", str(ws.root), "enter-vpd", "--app", "qiankun_hm", "--device", spec])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["status"] == "login_required" and out["action_required"] is True
    assert out.get("human_handoffs")


def test_enter_vpd_terminal_states_pre_and_post_tap(ws, tmp_path, monkeypatch):
    from vpdtest.vpd_nav import enter_vpd_app

    # 1) Already on ADS/VPD view with 定位权限未开启 -> immediately reports without switching away (0 actions)
    spec_pre = _nav_world(tmp_path, start="vpd_location_off")
    app = ws.app("qiankun_hm")
    dev_pre = ws.device(spec_pre)
    dev_pre.launch(app.bundle, app.ability)
    res_pre = enter_vpd_app(ws, app, dev_pre)
    assert res_pre["status"] == "location_permission_required" and res_pre["action_required"] is True
    assert res_pre["actions"] == 0

    # 2) Post-tap safe_code / load_failed / benign_dialog / location_off / charging_only
    for target_scr, expected_st, expected_req in [
        ("safe_code", "safe_code_required", True),
        ("vpd_load_failed", "load_failed", False),
        ("vpd_location_off", "location_permission_required", True),
        ("vpd_charging_only", "vpd_unavailable", False),
    ]:
        spec = _nav_world(tmp_path, start="car_tab", ads_target=target_scr)
        res = enter_vpd_app(ws, app, ws.device(spec))
        assert res["status"] == expected_st and res["action_required"] is expected_req

    spec_dlg = _nav_world(tmp_path, start="car_tab", ads_target="vpd_dialog")
    res_dlg = enter_vpd_app(ws, app, ws.device(spec_dlg))
    assert res_dlg["status"] == "ready"
    assert any(s["step"].startswith("post_dismiss_") for s in res_dlg["steps"])

    # 3) Budget exhausted while dismissing post-ADS dialog
    orig_send = Dispatcher.send
    def fail_dismiss(self, op, *args, preconditions=None, att=None, tap=None, transient=None):
        if att and str(att.get("step_id", "")).startswith("ev_post_dismiss_"):
            att["status"] = "budget_exhausted"
            return DeviceResult(op, False, False, "budget_exhausted", "budget exhausted")
        return orig_send(self, op, *args, preconditions=preconditions, att=att, tap=tap, transient=transient)

    monkeypatch.setattr(Dispatcher, "send", fail_dismiss)
    res_bud = enter_vpd_app(ws, app, ws.device(spec_dlg))
    assert res_bud["status"] == "blocked" and res_bud["action_required"] is True


def test_switch_vehicle_navigates_to_car_tab_even_when_ads_tab_visible_and_handles_car_tab_timeout(ws, tmp_path):
    from vpdtest.vpd_nav import switch_vehicle_app
    spec = _nav_world(tmp_path, start="other_tab_with_ads", post_vin_screen="car_tab_k89")
    res = switch_vehicle_app(ws, ws.app("qiankun_hm"), ws.device(spec), vin_tail="000228",
                             authorize=["vehicle_selection"])
    assert res["status"] == "switched", res
    assert res["current_vehicle"] == "问界K89"
    assert any(s.get("step", "").startswith("tap_car_tab_") for s in res["steps"])

    # If 爱车 tab never opens car_tab within timeout_s, _ensure_car_tab stops with terminal timeout
    from vpdtest.util import read_json
    p = spec.split("replay:", 1)[1]
    w = read_json(p)
    w["screens"]["other_tab_with_ads"]["root"]["children"][1]["children"][1]["go"] = "other_tab_with_ads"
    write_json(p, w)
    res_to = switch_vehicle_app(ws, ws.app("qiankun_hm"), ws.device(spec), vin_tail="000228",
                                authorize=["vehicle_selection"], timeout_s=0.1)
    assert res_to["status"] == "timeout" and res_to["action_required"] is True
