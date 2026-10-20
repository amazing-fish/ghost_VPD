"""Issue #55: expose existing session accounting without changing its gates.

All worlds and credentials in this file are synthetic. These are offline CLI
and Dispatcher checks, not evidence of Windows or real-device behavior.
"""
import json

import pytest
from test_goal_loop import _lab_world, _locked_world, cli

from vpdtest import commands
from vpdtest import workspace as workspace_mod
from vpdtest.cli import main
from vpdtest.session import trail
from vpdtest.util import read_json, read_jsonl, write_json


def _limit(ws, maximum):
    path = ws.root / "policy.json"
    policy = read_json(path)
    policy["max_actions_per_run"] = maximum
    write_json(path, policy)


def _act(ws, device, session="budget", action="launch", *args):
    return cli(ws, "act", action, "--app", "motor_hm", "--device", device,
               "--session", session, *args)


def test_success_and_rejected_target_report_session_budget(ws, tmp_path):
    device = _lab_world(tmp_path)
    first = _act(ws, device)
    assert first["result"]["outcome"] == "passed"
    assert first["budget"] == {"used": 1, "max": 60, "remaining": 59}
    assert "budget_warning" not in first

    blocked = _act(ws, device, "budget", "tap", "--select", "text=设置")
    assert blocked["result"]["final_status"] == "target_ambiguous"
    assert blocked["budget"] == first["budget"]
    assert "budget_warning" not in blocked


def test_warning_boundary_and_exhaustion_keep_last_success(ws, tmp_path):
    _limit(ws, 5)
    device = _lab_world(tmp_path)
    for used in range(1, 6):
        out = _act(ws, device)
        assert out["result"]["outcome"] == "passed"
        assert out["budget"] == {"used": used, "max": 5, "remaining": 5 - used}
        assert ("budget_warning" in out) is (used >= 4)
        if used == 4:
            assert "80%" in out["budget_warning"]
            assert "hint" not in out

    assert "已用尽" in out["budget_warning"]
    assert "--session" in out["hint"]
    assert "policy" in out["hint"]
    before = read_jsonl(ws.runs_dir / "session-budget" / "events.jsonl")
    blocked = _act(ws, device)
    assert blocked["result"]["final_status"] == "budget_exhausted"
    assert blocked["budget"] == {"used": 5, "max": 5, "remaining": 0}
    assert "已用尽" in blocked["reason"]
    assert "--session" in blocked["hint"]
    new = read_jsonl(ws.runs_dir / "session-budget" / "events.jsonl")[len(before):]
    assert not any(e["event"] == "dispatch" and e.get("sent") is not False for e in new)


def test_policy_rejection_reports_budget_without_charging_a_tap(ws, tmp_path):
    device = _lab_world(tmp_path)
    path = device.split(":", 1)[1]
    world = read_json(path)
    world["screens"]["home"]["root"]["children"].append(
        {"t": "Button", "text": "立即更新", "b": [30, 600, 600, 680], "click": True})
    write_json(path, world)
    first = _act(ws, device)
    blocked = _act(ws, device, "budget", "tap", "--select", "text=立即更新")
    assert blocked["result"]["final_status"] == "policy_denied"
    assert blocked["budget"] == first["budget"]


def test_observe_and_readonly_wait_do_not_consume_budget(ws, tmp_path):
    _limit(ws, 2)
    device = _lab_world(tmp_path)
    _act(ws, device)
    cli(ws, "observe", "--app", "motor_hm", "--device", device, "--session", "budget")
    waited = _act(ws, device, "budget", "wait", "--duration", "0")
    assert waited["budget"] == {"used": 1, "max": 2, "remaining": 1}
    assert trail(ws.runs_dir / "session-budget")["actions"] == 1
    next_action = _act(ws, device)
    assert next_action["budget"]["used"] == 2
    assert _act(ws, device)["result"]["final_status"] == "budget_exhausted"
    cli(ws, "observe", "--app", "motor_hm", "--device", device, "--session", "budget")
    assert trail(ws.runs_dir / "session-budget")["actions"] == 2


def test_auto_unlock_physical_actions_remain_charged(ws, tmp_path):
    _limit(ws, 20)
    device = _locked_world(tmp_path, "budget-unlock")
    ws.config["secrets"] = {"LOCK_PIN_TEST": "246810"}
    ws.config["unlock_secrets"] = {device: "LOCK_PIN_TEST"}
    out = _act(ws, device)
    assert out["result"]["outcome"] == "passed"
    events = read_jsonl(ws.runs_dir / "session-budget" / "events.jsonl")
    sent = [e for e in events if e["event"] == "dispatch" and e.get("sent") is not False
            and e.get("op") != "clear_text"]
    assert [e["action"] for e in sent].count("pin_digit") == 6
    assert len(sent) == 9  # wake + swipe + six PIN taps + requested launch
    assert out["budget"] == {"used": 9, "max": 20, "remaining": 11}
    assert _act(ws, device)["budget"]["used"] == 10


def test_unlock_exhausting_tail_budget_keeps_partial_execution_and_handoff(ws, tmp_path):
    _limit(ws, 5)
    device = _locked_world(tmp_path, "partial-unlock")
    ws.config["secrets"] = {"LOCK_PIN_TEST": "246810"}
    ws.config["unlock_secrets"] = {device: "LOCK_PIN_TEST"}
    out = _act(ws, device)
    assert out["result"]["final_status"] == "budget_exhausted"
    assert out["action_required"]
    assert out["budget"] == {"used": 5, "max": 5, "remaining": 0}
    assert "此前已执行部分" in out["reason"]
    events = read_jsonl(ws.runs_dir / "session-budget" / "events.jsonl")
    sent = [e for e in events if e["event"] == "dispatch" and e.get("sent") is not False]
    assert len(sent) == 5 and [e["action"] for e in sent].count("pin_digit") == 3
    assert not any(e.get("op") == "launch" for e in sent)


def test_budget_hint_cannot_bypass_existing_safety_hold(ws, tmp_path, monkeypatch):
    _limit(ws, 1)
    device = _lab_world(tmp_path)
    _act(ws, device)
    original = commands._open_session

    def held(*args):
        app, store, dev, ctx = original(*args)
        ctx.safety_hold = {"reason": "synthetic unconfirmed stop"}
        return app, store, dev, ctx

    monkeypatch.setattr(commands, "_open_session", held)
    before = read_jsonl(ws.runs_dir / "session-budget" / "events.jsonl")
    out = _act(ws, device)
    assert out["result"]["final_status"] == "safety_hold"
    assert out["action_required"]
    assert out["budget"] == {"used": 1, "max": 1, "remaining": 0}
    assert "安全暂停" in out["hint"]
    assert "reason" not in out  # the budget must not relabel a safety rejection
    new = read_jsonl(ws.runs_dir / "session-budget" / "events.jsonl")[len(before):]
    assert not any(e["event"] == "dispatch" and e.get("sent") is not False for e in new)


def test_real_cli_json_surfaces_budget_without_changing_exit_contract(ws, tmp_path, monkeypatch, capsys):
    _limit(ws, 1)
    device = _lab_world(tmp_path)
    monkeypatch.setattr(workspace_mod, "load_config", lambda: {"unlock_secrets": {}})
    argv = ["--workspace", str(ws.root), "act", "launch", "--app", "motor_hm",
            "--device", device, "--session", "budget-cli"]
    assert main(argv) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["result"]["outcome"] == "passed"
    assert completed["budget"] == {"used": 1, "max": 1, "remaining": 0}
    assert main(argv) == 0  # normal blocked action keeps the existing CLI contract
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["result"]["final_status"] == "budget_exhausted"
    assert blocked["budget"] == completed["budget"]
    assert blocked["reason"] and blocked["hint"]


@pytest.mark.parametrize("used", [0, 47, 48, 60, 61])
def test_existing_event_count_is_reported_conservatively(ws, tmp_path, used):
    from vpdtest.util import append_jsonl

    device = _lab_world(tmp_path)
    cli(ws, "observe", "--app", "motor_hm", "--device", device, "--session", "budget")
    path = ws.runs_dir / "session-budget" / "events.jsonl"
    for _ in range(used):
        # A historical unknown sent result already counts under trail(); output
        # must not reduce it to a new logical-action or successful-action count.
        append_jsonl(path, {"event": "dispatch", "op": "tap", "sent": None})
    out = _act(ws, device, "budget", "wait", "--duration", "0")
    assert out["budget"] == {"used": used, "max": 60, "remaining": max(0, 60 - used)}
    assert ("budget_warning" in out) is (used >= 48)
