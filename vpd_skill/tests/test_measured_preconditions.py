"""#34: case preconditions must use device measurements before any dispatch."""
from pathlib import Path

import pytest

from vpdtest.devices.replay import ReplayDevice
from vpdtest.runner import run_case, run_facts
from vpdtest.util import read_json, read_jsonl, write_json

from conftest import world


def _case(requirements, layout_requirements=None):
    return {
        "id": "TC-MEASURED-ENV", "version": 1, "status": "draft", "app": "motor_hm",
        "title": "Synthetic measured precondition", "purpose": "Check before launch",
        "env_requirements": requirements, "layout_requirements": layout_requirements or {},
        "steps": [{"id": "S1", "action": "launch"}],
        "assertions": [], "cleanup": [{"action": "stop_app"}],
    }


def _sent(run):
    return [e for e in read_jsonl(Path(run["dir"]) / "events.jsonl")
            if e["event"] == "dispatch" and e.get("sent")]


@pytest.mark.parametrize("key,required", [("app_version", "different-build"),
                                           ("device_form", "foldable"), ("app", "qiankun_hm")])
def test_declared_matching_value_cannot_hide_measured_mismatch(ws, key, required):
    run = run_case(ws, case=_case({key: required}), device_spec=world("motor_hm_base.json"),
                   env={key: required}, propose=False)
    assert run["verdict"]["verdict"] == "blocked"
    assert run["env"]["unmet"][0]["key"] == key
    assert run["env"]["unmet"][0]["actual"] == run["facts"][key] != required
    assert all(s["outcome"] == "not_run" for s in run["steps"])
    assert not run["cleanup"] and not _sent(run)


def test_matching_measurements_need_no_duplicate_env_declaration(ws):
    dev = ReplayDevice(world("motor_hm_base.json").split(":", 1)[1])
    required = {"app_version": dev.app_version(dev.bundle),
                "device_form": dev.display_info()["device_form"], "account_state": "logged_in"}
    run = run_case(ws, case=_case(required), device_spec=world("motor_hm_base.json"),
                   env={"account_state": "logged_in"}, propose=False)
    assert run["env"]["ok"]
    assert run["env"]["facts"] == run["facts"]
    assert run["steps"][0]["outcome"] == "passed"
    assert _sent(run)


@pytest.mark.parametrize("measured", [None, ""])
def test_unknown_device_form_cannot_be_replaced_by_declaration(ws, tmp_path, measured):
    data = read_json(world("motor_hm_base.json").split(":", 1)[1])
    data["display"]["device_form"] = measured
    path = tmp_path / "unknown-form.json"
    write_json(path, data)
    run = run_case(ws, case=_case({"device_form": "bar"}), device_spec=f"replay:{path}",
                   env={"device_form": "bar"}, propose=False)
    assert run["verdict"]["verdict"] == "blocked"
    assert run["env"]["unmet"] == [{"key": "device_form", "required": "bar", "actual": None}]
    assert run["facts"]["device_form"] is None and not _sent(run)


def test_missing_measured_build_remains_unknown_for_oracles(ws):
    facts = run_facts(ws.app("motor_hm"), {}, {"app_version": "declared-build", "account_state": "logged_in"})
    assert facts["app_version"] is None
    assert facts["account_state"] == "logged_in"


@pytest.mark.parametrize("initial", ["locked", "usb_dialog"])
@pytest.mark.parametrize("compatibility", ["build", "layout"])
def test_incompatible_case_blocks_preflight_recovery_dispatch(ws, tmp_path, initial, compatibility):
    name = "motor_hm_usb_dialog.json" if initial == "usb_dialog" else "motor_hm_base.json"
    from vpdtest.devices.replay import load_world
    data = load_world(world(name).split(":", 1)[1])
    if initial == "locked":
        data["initial"].update(locked=True, screen_on=False, lock_pin="246810")
    else:
        write_json(ws.root / "system_dialogs.json", {"dialogs": {"usb": {
            "class": "benign", "window_names": ["UsbModeDialog"],
            "match": {"require_any_text": ["USB连接方式", "USB 连接方式"]},
            "dismiss": {"prefer_text": ["取消"], "forbid_text": ["传输文件"]},
            "review": "reviewed", "review_basis": "synthetic test registration"}}})
    path = tmp_path / f"incompatible-{initial}.json"
    write_json(path, data)
    spec = f"replay:{path}"
    ws.config.update(secrets={"LOCK_PIN_TEST": "246810"}, unlock_secrets={spec: "LOCK_PIN_TEST"})
    case = _case({"app_version": "different-build"}) if compatibility == "build" else _case({}, {"wide": True})
    run = run_case(ws, case=case, device_spec=spec, env={"app_version": "different-build"}, propose=False)
    assert run["verdict"]["verdict"] == "blocked"
    assert not _sent(run), "incompatible case must not unlock or dismiss before being blocked"
    assert "unlock" not in run["preflight"] and "system_dialog" not in run["preflight"]


def test_build_is_rechecked_after_unlock_before_business_dispatch(ws, tmp_path, monkeypatch):
    data = read_json(world("motor_hm_base.json").split(":", 1)[1])
    expected = data["app_version"]
    data["initial"].update(locked=True, screen_on=False, lock_pin="246810")
    path = tmp_path / "build-changes-after-unlock.json"
    write_json(path, data)
    spec = f"replay:{path}"
    ws.config.update(secrets={"LOCK_PIN_TEST": "246810"}, unlock_secrets={spec: "LOCK_PIN_TEST"})
    original = ReplayDevice.preflight
    calls = []

    def preflight(device, bundle):
        pre = original(device, bundle)
        calls.append(pre)
        if len(calls) > 1:
            pre["app_version"] = "changed-build"
        return pre

    monkeypatch.setattr(ReplayDevice, "preflight", preflight)
    run = run_case(ws, case=_case({"app_version": expected}), device_spec=spec, propose=False)
    assert len(calls) == 2 and run["preflight"]["unlock"]["status"] == "unlocked"
    assert run["verdict"]["verdict"] == "blocked"
    assert run["env"]["unmet"] == [{"key": "app_version", "required": expected, "actual": "changed-build"}]
    assert run["facts"]["app_version"] == "changed-build"
    assert all(s["outcome"] == "not_run" for s in run["steps"]) and not run["cleanup"]
    assert not any(e.get("op") in ("launch", "stop") for e in _sent(run))
    assert "system_dialog" not in run["preflight"]


def test_layout_is_rechecked_after_unlock_before_business_dispatch(ws, tmp_path, monkeypatch):
    data = read_json(world("motor_hm_base.json").split(":", 1)[1])
    data["initial"].update(locked=True, screen_on=False, lock_pin="246810")
    data["display"]["density"] = 1.5  # synthetic wide layout at the first measurement
    path = tmp_path / "layout-changes-after-unlock.json"
    write_json(path, data)
    spec = f"replay:{path}"
    ws.config.update(secrets={"LOCK_PIN_TEST": "246810"}, unlock_secrets={spec: "LOCK_PIN_TEST"})
    original = ReplayDevice.preflight
    calls = []

    def preflight(device, bundle):
        pre = original(device, bundle)
        calls.append(pre)
        if len(calls) > 1:
            pre["display"]["density"] = 3.375
        return pre

    monkeypatch.setattr(ReplayDevice, "preflight", preflight)
    run = run_case(ws, case=_case({}, {"wide": True}), device_spec=spec, propose=False)
    assert len(calls) == 2 and run["preflight"]["unlock"]["status"] == "unlocked"
    assert run["verdict"]["verdict"] == "blocked" and not run["env"]["ok"]
    assert run["env"]["layout"]["display_profile"]["wide"] is False
    assert all(s["outcome"] == "not_run" for s in run["steps"]) and not run["cleanup"]
    assert not any(e.get("op") in ("launch", "stop") for e in _sent(run))
    assert "system_dialog" not in run["preflight"]


def test_matching_layout_allows_preflight_recovery_and_keeps_business_env(ws, tmp_path):
    data = read_json(world("motor_hm_base.json").split(":", 1)[1])
    data["initial"].update(locked=True, screen_on=False, lock_pin="246810")
    path = tmp_path / "compatible-layout.json"
    write_json(path, data)
    spec = f"replay:{path}"
    ws.config.update(secrets={"LOCK_PIN_TEST": "246810"}, unlock_secrets={spec: "LOCK_PIN_TEST"})
    run = run_case(ws, case=_case({"account_state": "logged_in"}, {"wide": False}),
                   device_spec=spec, env={"account_state": "logged_in"}, propose=False)
    assert run["env"]["ok"] and run["preflight"]["unlock"]["status"] == "unlocked"
    assert run["steps"][0]["outcome"] == "passed"
    assert any(e.get("op") == "launch" for e in _sent(run))


# #234: TC-HM-S01 runs bare (without --env) under empty HDC env_facts, guarding A1 via S1b account proof.
def _s01_world(tmp_path, name, *, my_marker, with_logout=True):
    data = read_json(world("motor_hm_base.json").split(":", 1)[1])
    data["app_version"] = "3.0.3.200"
    data["env_facts"] = {}
    def _walk(node):
        yield node
        for child in node.get("children", []):
            yield from _walk(child)
    for node in _walk(data["screens"]["my_tab"]["root"]):
        if node.get("text") == "注册/登录":
            node["text"] = my_marker
    if not with_logout:
        for node in _walk(data["screens"]["settings"]["root"]):
            if node.get("text") == "退出登录":
                node["text"] = "其他设置项"
    path = tmp_path / name
    write_json(path, data)
    return f"replay:{path}"


def test_s01_bare_run_passes_when_logged_in_and_stays_pending_when_logout_missing(ws, tmp_path):
    spec_ok = _s01_world(tmp_path, "s01-in.json", my_marker="我的车辆", with_logout=True)
    run_ok = run_case(ws, "TC-HM-S01", device_spec=spec_ok, propose=False)
    assert run_ok["env"]["ok"] and run_ok["actions"] == 5
    assert run_ok["verdict"]["verdict"] == "passed"
    assert [a["result"] for a in run_ok["assertions"]] == ["pass", "pass"]
    assert run_ok["assertions"][0]["guard"]["status"] == "true"

    # Shipped TC-HM-S01 S4 uses expect_state="settings" (which requires "退出登录" in motor_hm/states.json)
    # and intentionally has no oracle_refs (so an unguarded tap miss or logged-out Settings screen cannot false-fail).
    # Engine-level carried-guard product_failed when S4 lands cleanly is covered by test_account_condition_carry.py.
    spec_bad = _s01_world(tmp_path, "s01-in-no-logout.json", my_marker="我的车辆", with_logout=False)
    run_bad = run_case(ws, "TC-HM-S01", device_spec=spec_bad, propose=False)
    assert run_bad["env"]["ok"] and run_bad["actions"] == 4
    assert run_bad["verdict"]["verdict"] == "pending" and run_bad["verdict"]["product_failed"] is False
    assert run_bad["verdict"]["first_anomaly"]["step"] == "S4"
    assert run_bad["verdict"]["first_anomaly"]["status"] == "postcondition_not_met"
    assert [a["result"] for a in run_bad["assertions"]] == ["not_run", "not_run"]
    assert run_bad["verdict"]["coverage"]["unverified"] == ["A1", "A2"]


def test_s01_bare_run_logged_out_and_env_declaration_cannot_override_real_tree(ws, tmp_path):
    spec = _s01_world(tmp_path, "s01-out.json", my_marker="注册/登录", with_logout=True)
    run = run_case(ws, "TC-HM-S01", device_spec=spec, env={"account_state": "logged_in"}, propose=False)
    assert run["env"]["ok"] and run["actions"] == 5
    assert "account_state" not in run["oracle_facts"]
    assert run["verdict"]["product_failed"] is False
    a1, a2 = run["assertions"]
    assert a1["result"] == "not_applicable" and a1["guard"]["status"] == "false"
    assert a2["result"] == "pass"


def test_s01_bare_run_unknown_my_tab_keeps_a1_pending_without_product_fail(ws, tmp_path):
    spec = _s01_world(tmp_path, "s01-unknown.json", my_marker="无关文本", with_logout=True)
    run = run_case(ws, "TC-HM-S01", device_spec=spec, propose=False)
    assert run["env"]["ok"] and run["actions"] == 5
    assert run["verdict"]["verdict"] == "pending" and run["verdict"]["product_failed"] is False
    a1, a2 = run["assertions"]
    assert a1["result"] == "pending" and a1["guard"]["status"] == "unknown"
    assert a2["result"] == "pass"
