"""#56 execution-edge examples; only synthetic devices and temporary runs are used."""
import json
from types import MethodType

import pytest
from conftest import world
from verdict_fixtures import COPY_CASE
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.dispatch import Dispatcher
from vpdtest.execute import Executor
from vpdtest.runner import run_case


def _case(ws, steps):
    case = ws.load_case(COPY_CASE)
    case.update(steps=steps, assertions=[], cleanup=[])
    return case


@pytest.mark.parametrize("query_error,expected", [
    ("transport_unavailable", "error"),
    (None, "blocked"),
])
def test_last_dispatch_foreground_check_distinguishes_query_error_from_unknown(
        verdict_ws, monkeypatch, query_error, expected):
    # Initial observation is healthy. Only the last, mandatory pre-dispatch
    # foreground check fails; a successful-but-unknown query is the control.
    original = Dispatcher._state
    marker = "RAW_LAST_FOREGROUND_CHECK_E000004"

    def check(self, conditions, attempt):
        if attempt["action"] == "launch":
            return original(self, conditions, attempt)
        with monkeypatch.context() as scoped:
            scoped.setattr(self.device, "foreground", lambda: None)
            scoped.setattr(self.device, "last_query_error", query_error)
            scoped.setattr(self.device, "last_query_error_detail",
                           {"error": "channel unavailable", "raw": marker}
                           if query_error else None, raising=False)
            return original(self, conditions, attempt)

    monkeypatch.setattr(Dispatcher, "_state", check)
    case = _case(verdict_ws, [{"id": "S1", "action": "launch", "retry": 0,
                              "expect_state": "@in_app"},
                             {"id": "S2", "action": "tap", "target": "tab_my", "retry": 0}])
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    result = run["verdict"]
    assert result["verdict"] == expected
    errors = result.get("execution", {}).get("errors", [])
    if query_error:
        assert result["attribution"] == "environment"
        assert errors and marker in json.dumps(errors, ensure_ascii=False)
        assert query_error in json.dumps(errors, ensure_ascii=False)
    else:
        assert not errors
    assert run["actions"] == 1  # launch is explicit; the foreground-bound tap is never sent.


def test_revalidation_tree_collection_failure_is_error_not_target_movement(verdict_ws, monkeypatch):
    # The first locate succeeds. The mandatory second tree collection then
    # returns an explicit parse error, rather than evidence of a changed node.
    original = Executor._revalidate
    marker = "synthetic predispatch tree JSON parse failure"

    def revalidate(self, *args, **kwargs):
        with monkeypatch.context() as scoped:
            scoped.setattr(self.ctx.device, "dump_tree", lambda *a, **k: DeviceResult(
                "dump_tree", True, False, "tree_parse_error", marker, "prefix" * 100 + "FULL_TREE_RAW_TAIL"))
            return original(self, *args, **kwargs)

    monkeypatch.setattr(Executor, "_revalidate", revalidate)
    case = _case(verdict_ws, [
        {"id": "S1", "action": "launch", "expect_state": "car_tab", "retry": 0},
        {"id": "S2", "action": "tap", "target": "tab_my", "expect_state": "my_tab",
         "revalidate": True, "retry": 0},
    ])
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert run["verdict"]["verdict"] == "error"
    assert run["verdict"]["attribution"] == "tool"
    errors = run["verdict"]["execution"]["errors"]
    assert marker in json.dumps(errors, ensure_ascii=False)
    assert "tree_parse_error" in json.dumps(errors, ensure_ascii=False)
    assert "FULL_TREE_RAW_TAIL" in json.dumps(errors, ensure_ascii=False)
    # The launch is the only physical action; no stale-coordinate tap is sent.
    assert run["actions"] == 1


def test_stop_process_query_failure_retains_original_hdc_output(verdict_ws, monkeypatch):
    # Exercise the real HDC process query adapter against a mocked command;
    # all physical actions and other state queries remain in the replay world.
    marker = "RAW_PIDOF_TRANSPORT_FAILURE_E000004"
    original = verdict_ws.device

    def device(*args, **kwargs):
        dev = original(*args, **kwargs)
        dev.process_alive = MethodType(HdcDevice.process_alive, dev)
        dev._shell = lambda *a, **k: DeviceResult(
            "pidof", True, False, "transport_unavailable", "pidof unavailable", marker)
        return dev

    monkeypatch.setattr(verdict_ws, "device", device)
    case = _case(verdict_ws, [{"id": "S1", "action": "stop_app", "retry": 0, "timeout_s": 0}])
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert run["verdict"]["verdict"] == "error"
    assert run["verdict"]["product_failed"] is False
    assert marker in json.dumps(run["verdict"]["execution"]["errors"], ensure_ascii=False)
