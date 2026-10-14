"""Issue #45: a real suite stops at handover and preserves its unexecuted plan.

Synthetic replay worlds exercise the real CLI, run_case, unlock capability and
Observer. They provide offline protocol evidence, not real-device acceptance.
"""
import json

import pytest

from vpdtest import workspace as workspace_mod
from vpdtest.cli import main
from vpdtest.util import SKILL_ROOT, read_json, read_jsonl, write_json

CASES = ["TC-MOTOR-HM-001", "TC-MOTOR-HM-002"]
SUITE = "suite-handover-contract"


def _world(tmp_path, name, *, locked=False):
    path = tmp_path / f"{name}.json"
    data = {
        "extends": str(SKILL_ROOT / "fixtures" / "worlds" / "motor_hm_base.json"),
        "device": {"model": name, "os": "offline-suite-contract"},
    }
    if locked:
        data["initial"] = {
            "locked": True,
            "screen_on": False,
            "foreground": "com.ohos.sceneboard",
            "installed": True,
        }
    write_json(path, data)
    return f"replay:{path}"


def _suite_cli(ws, capsys, monkeypatch, devices, *, fail_on=None):
    # main constructs a fresh Workspace: isolate it from local PIN mappings and
    # defaults while leaving the real runner, device, Dispatcher and Observer intact.
    monkeypatch.setattr(workspace_mod, "load_config", lambda: {"unlock_secrets": {}})
    write_json(ws.root / "suites" / f"{SUITE}.json", {"id": SUITE, "cases": CASES})
    argv = ["--workspace", str(ws.root), "suite", SUITE]
    for device in devices:
        argv.extend(["--device", device])
    if fail_on:
        argv.extend(["--fail-on", fail_on])
    code = main(argv)
    output = json.loads(capsys.readouterr().out)
    events = read_jsonl(ws.runs_dir / output["suite_run"] / "events.jsonl")
    return code, output, events


def _assert_real_run_evidence(ws, output, plan):
    paths = list(ws.runs_dir.glob("run-*/run.json"))
    assert len(paths) == len(output["results"]), "unexecuted work must not create phantom runs"
    for row, planned in zip(output["results"], plan):
        run_dir = ws.runs_dir / row["run_id"]
        run = read_json(run_dir / "run.json")
        assert run["case"]["id"] == planned["case"]
        assert run["device_spec"] == planned["device"]
        assert run["validation_level"] == "offline_replay"
        assert list((run_dir / "obs").glob("*/observation.json")), "real Observer evidence is required"
    return paths


@pytest.mark.parametrize("locked_first", [True, False], ids=["first-device", "second-device"])
def test_suite_stops_at_real_unlock_handover_and_preserves_remaining_plan(
        ws, tmp_path, capsys, monkeypatch, locked_first):
    locked = _world(tmp_path, "pin-required", locked=True)
    healthy = _world(tmp_path, "healthy")
    devices = [locked, healthy] if locked_first else [healthy, locked]
    plan = [{"case": case, "device": device} for device in devices for case in CASES]
    completed_count = 1 if locked_first else 3

    # --fail-on blocked would ordinarily yield 1; handover must retain priority 2.
    code, output, events = _suite_cli(ws, capsys, monkeypatch, devices, fail_on="blocked")
    assert code == 2
    assert len(output["results"]) == completed_count, "no next case/device may run after handover"
    assert output["completed"] is False
    assert output["not_run"] == plan[completed_count:], "remaining entries retain device-major plan order"
    assert len(plan) == len(output["results"]) + len(output["not_run"])
    handover = output["results"][-1]
    assert handover["verdict"] == "blocked"
    assert handover["action_required"]
    assert output["action_required"] == handover["action_required"]

    _assert_real_run_evidence(ws, output, plan)
    trigger_dir = ws.runs_dir / handover["run_id"]
    trigger = read_json(trigger_dir / "run.json")
    assert trigger["preflight"]["unlock"]["status"] == "missing_secret"
    assert trigger["preflight"]["unlock"]["action_required"]
    assert trigger["preflight"]["unlock"]["entered_digits"] == 0
    assert all(step["outcome"] == "not_run" for step in trigger["steps"])
    trigger_events = read_jsonl(trigger_dir / "events.jsonl")
    assert any(event["event"] == "capability_result" and event.get("status") == "missing_secret"
               for event in trigger_events)
    assert not any(event["event"] == "step_start" for event in trigger_events)

    assert events[0]["event"] == "suite_start" and events[0]["total"] == len(plan)
    assert [event["case"] for event in events if event["event"] == "case_start"] == [
        entry["case"] for entry in plan[:completed_count]]
    assert len([event for event in events if event["event"] == "case_done"]) == completed_count
    assert events[-1]["event"] == "suite_end"
    assert events[-1]["completed"] is False
    assert events[-1]["not_run"] == output["not_run"]
    assert events[-1]["action_required"] == output["action_required"]


def test_suite_without_handover_executes_every_case_on_every_device(ws, tmp_path, capsys, monkeypatch):
    devices = [_world(tmp_path, "healthy-a"), _world(tmp_path, "healthy-b")]
    plan = [{"case": case, "device": device} for device in devices for case in CASES]
    code, output, events = _suite_cli(ws, capsys, monkeypatch, devices)
    assert code == 0
    assert len(output["results"]) == len(plan) == 4
    assert events[0]["event"] == "suite_start" and events[0]["total"] == len(plan)
    assert all(row["verdict"] == "passed" and not row.get("action_required") for row in output["results"])
    assert output["completed"] is True
    assert output["not_run"] == []
    assert not output.get("action_required")
    _assert_real_run_evidence(ws, output, plan)
    assert len([event for event in events if event["event"] == "case_start"]) == len(plan)
    assert len([event for event in events if event["event"] == "case_done"]) == len(plan)
    assert events[-1]["event"] == "suite_end"
    assert events[-1]["completed"] is True
    assert events[-1]["not_run"] == []
    assert not events[-1].get("action_required")
