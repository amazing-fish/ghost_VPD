"""Unknown physical sends keep their budget across completed CLI invocations."""
import pytest
from test_goal_loop import _lab_world, _start, cli

from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.session import trail
from vpdtest.util import read_json, write_json


@pytest.mark.parametrize("sent", [None, 0, 1, "false"])
def test_completed_unknown_send_is_charged_before_next_cli_action(ws, tmp_path, monkeypatch, sent):
    spec = _lab_world(tmp_path)
    policy_path = ws.root / "policy.json"
    policy = read_json(policy_path)
    policy["max_actions_per_run"] = 2  # One launch, then one possible tap.
    write_json(policy_path, policy)
    _start(ws, spec, session="budget-retry")
    calls = []

    def unknown_tap(device, *point):
        calls.append(point)
        return DeviceResult("tap", sent, False, "timeout", "fixture lost acknowledgement")

    monkeypatch.setattr(ReplayDevice, "tap", unknown_tap)
    first = cli(ws, "act", "tap", "--app", "motor_hm", "--session", "budget-retry",
                "--device", spec, "--select", "text=唯一入口")
    assert first["result"]["attempts"][0]["dispatch"]["sent"] == sent
    assert trail(ws.runs_dir / "session-budget-retry")["actions"] == 2
    second = cli(ws, "act", "tap", "--app", "motor_hm", "--session", "budget-retry",
                 "--device", spec, "--select", "text=唯一入口")
    assert len(calls) == 1
    assert second["result"]["final_status"] == "budget_exhausted"
    assert trail(ws.runs_dir / "session-budget-retry")["actions"] == 2


def test_completed_proven_unsent_action_does_not_spend_the_session_budget(ws, tmp_path, monkeypatch):
    spec = _lab_world(tmp_path)
    policy_path = ws.root / "policy.json"
    policy = read_json(policy_path)
    policy["max_actions_per_run"] = 2
    write_json(policy_path, policy)
    _start(ws, spec, session="budget-unsent")
    calls = []

    def unsent_tap(device, *point):
        calls.append(point)
        return DeviceResult("tap", False, False, "transport_unavailable", "fixture not dispatched")

    monkeypatch.setattr(ReplayDevice, "tap", unsent_tap)
    for _ in range(2):
        result = cli(ws, "act", "tap", "--app", "motor_hm", "--session", "budget-unsent",
                     "--device", spec, "--select", "text=唯一入口")
        assert result["result"]["final_status"] == "device_error"
    assert len(calls) == 2
    assert trail(ws.runs_dir / "session-budget-unsent")["actions"] == 1
