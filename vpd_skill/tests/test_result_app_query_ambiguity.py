"""bm dump 的通用错误不是缺包证明；此文件锁定已有行为，无生产修复。

官方固定源：空 DumpInfos 结果共用 BM 通用文案；BMS 权限、缺包及 IPC 失败均可能无结果。
https://github.com/openharmony/bundlemanager_bundle_tool/blob/6a5fea99db7347b8d997fbac3c58f986a9ec8e52/frameworks/src/bundle_command.cpp#L1259
https://github.com/openharmony/bundlemanager_bundle_framework/blob/7865b761797896f384e9465298014cdb541cb73e/services/bundlemgr/src/bundle_mgr_host_impl.cpp#L2933
https://github.com/openharmony/bundlemanager_bundle_framework/blob/7865b761797896f384e9465298014cdb541cb73e/interfaces/inner_api/appexecfwk_core/src/bundlemgr/bundle_mgr_proxy.cpp#L2335
"""
import json

import pytest
from conftest import world
from verdict_fixtures import COPY_CASE
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.verdict import case_verdict


@pytest.mark.parametrize("raw", [
    "error: failed to get information and the parameters may be wrong.",
    "error: synthetic bundle manager permission denied",
    "error: synthetic unspecified bundle manager failure",
], ids=["ambiguous_generic_output", "permission_failure", "unknown_failure"])
def test_failed_version_query_does_not_claim_reliable_app_absence(verdict_ws, monkeypatch, raw):
    device = HdcDevice("synthetic-device")
    values = {"power_state": "Current State: AWAKE", "lock_state": "screenLocked false"}

    def shell(*args, op):
        if op == "bm_dump":
            return DeviceResult(op, True, False, "command_failed", raw, raw)
        return DeviceResult(op, True, True, raw=values[op])

    monkeypatch.setattr(device, "_shell", shell)
    monkeypatch.setattr(device, "info", lambda: {"serial": device.serial})
    monkeypatch.setattr(device, "display_info", dict)
    assert device.app_version("synthetic.app") is None
    assert device.version_query_error == {"error_class": "command_failed", "error": raw, "raw": raw}
    preflight = device.preflight("synthetic.app")
    installed = next(check for check in preflight["checks"] if check["check"] == "app_installed")
    assert installed["ok"] is False and installed["execution_error"]["raw"] == raw
    verdict = case_verdict(preflight, {"ok": True}, [], [], KnowledgeStore(verdict_ws.root))
    assert verdict["verdict"] == "error" and verdict["attribution"] == "tool"
    error = verdict["execution"]["errors"][0]
    assert error["error_class"] == "command_failed" and raw in json.dumps(error)


def test_explicit_replay_app_absence_remains_environment_blocked(verdict_ws, monkeypatch):
    original = ReplayDevice.preflight

    def absent_preflight(device, bundle):
        device.state["installed"] = False  # 回放明确事实，不从通用命令失败文案推断。
        return original(device, bundle)

    monkeypatch.setattr(ReplayDevice, "preflight", absent_preflight)
    run = run_case(verdict_ws, COPY_CASE, device_spec=world("motor_hm_base.json"), propose=False)
    verdict = run["verdict"]
    assert verdict["verdict"] == "blocked" and verdict["attribution"] == "environment"
    assert verdict["execution"]["status"] == "blocked" and verdict["execution"]["errors"] == []
    assert all(step["outcome"] == "not_run" for step in run["steps"])
