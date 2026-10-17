"""#56：pidof 的错误输出不是 PID；正常无进程退出码保持兼容。"""
import json
import subprocess
from types import MethodType

import pytest
from conftest import world
from verdict_fixtures import COPY_CASE
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.runner import run_case


@pytest.mark.parametrize("response,expected,error_class,raw", [
    ("explicit_failure", None, "command_failed", "[Fail] pidof permission denied RAW_PIDOF_DENIED"),
    ("no_process", False, None, ""),
    ("numeric_pid", True, None, "1234\n"),
    ("timeout", None, "timeout", "PARTIAL_PIDOF_TIMEOUT_RAW"),
    ("transport", None, "transport_unavailable", "[Fail] communication channel is being established E000004"),
])
def test_process_query_interprets_only_success_or_empty_no_pid(monkeypatch, response, expected, error_class, raw):
    calls = []

    def subprocess_result(cmd, **kwargs):
        calls.append(cmd)
        assert cmd == ["hdc", "-t", "synthetic-device", "shell", "pidof", "synthetic.app"]
        if response == "timeout":
            raise subprocess.TimeoutExpired(cmd, 1, output=raw.encode())
        return subprocess.CompletedProcess(cmd, 1 if response in ("explicit_failure", "no_process") else 0,
                                           stdout=raw.encode(), stderr=b"")

    monkeypatch.setattr("subprocess.run", subprocess_result)
    device = HdcDevice("synthetic-device")
    assert device.process_alive("synthetic.app") is expected
    assert len(calls) == 1
    if error_class:
        assert device.process_query_error["error_class"] == error_class
        assert device.process_query_error["error"]
        assert device.process_query_error["raw"] == raw
    else:
        assert device.process_query_error is None


def test_stop_process_command_failure_retains_query_error_in_run(verdict_ws, monkeypatch):
    # 真实 HDC process_alive 绑定回放设备；实际动作仍全部走受控 Replay/Dispatcher。
    original_device = verdict_ws.device
    marker = "[Fail] pidof permission denied RAW_STOP_PIDOF_DENIED"

    def device(*args, **kwargs):
        replay = original_device(*args, **kwargs)
        replay.process_alive = MethodType(HdcDevice.process_alive, replay)
        replay._shell = lambda *args, **kwargs: DeviceResult("pidof", True, False, "command_failed", marker, marker)
        return replay

    monkeypatch.setattr(verdict_ws, "device", device)
    case = verdict_ws.load_case(COPY_CASE)
    case.update(steps=[{"id": "S1", "action": "stop_app", "retry": 0, "timeout_s": 0}],
                assertions=[], cleanup=[])
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    verdict = run["verdict"]
    assert verdict["verdict"] == "error" and verdict["product_failed"] is False
    assert verdict["execution"]["status"] == "error"
    errors = json.dumps(verdict["execution"]["errors"], ensure_ascii=False)
    assert "command_failed" in errors and marker in errors
