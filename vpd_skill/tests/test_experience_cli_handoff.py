"""Issue #45: experience validation must expose and stop at a real replay handoff.

The baseline handoff and normal control invoke a fresh CLI process.  The trial
handoff invokes cli.main, using the real runner and ReplayDevice throughout; the
world is locked after the completed baseline to model a device locking between
phases.  Only temporary workspaces/worlds are mutated.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from vpdtest import commands
from vpdtest.cli import main
from vpdtest.experience import ExperienceStore
from vpdtest.util import SKILL_ROOT, read_json, write_json
from vpdtest.workspace import Workspace

CASES = ["TC-MOTOR-HM-001", "TC-MOTOR-HM-002"]
XID = "XP-cli-handoff"


@pytest.fixture
def handoff_ws(tmp_path):
    shutil.copytree(SKILL_ROOT / "data", tmp_path / "data")
    shutil.copytree(SKILL_ROOT / "fixtures", tmp_path / "fixtures")
    ws = Workspace(str(tmp_path / "data"))
    ws.config.pop("device", None)
    ws.config.pop("secrets", None)
    ws.config["unlock_secrets"] = {}
    xs = ExperienceStore(ws.root, "motor_hm")
    write_json(xs.items_path, {"items": []})
    write_json(xs.active_path, {"version": 0, "items": []})
    xs.save_item({
        "id": XID, "kind": "wait_timeout",
        "change": {"key": "TC-MOTOR-HM-001:S2", "timeout_s": 8},
        "description": "isolated CLI handoff regression",
        "status": "candidate", "requires_review": False,
        "origin_case": "TC-MOTOR-HM-001", "evidence": [],
        "validations": [], "history": [],
    })
    return ws


def _device(ws, world):
    return "replay:" + str(ws.root.parent / "fixtures" / "worlds" / world)


def _argv(ws, devices, cases=CASES):
    argv = ["--workspace", str(ws.root), "experience", "validate", XID,
            "--app", "motor_hm", "--cases", *cases]
    for device in devices:
        argv += ["--device", device]
    return argv


def _cli_process(ws, devices, cases=CASES):
    env = {k: v for k, v in os.environ.items() if not k.startswith("VPD_SECRET_")}
    # Keep dependency paths inherited from the test launcher, while selecting
    # this checkout's CLI rather than an installed copy of vpdtest.
    env["PYTHONPATH"] = str(SKILL_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-m", "vpdtest", *_argv(ws, devices, cases)],
                          check=False, capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=60)
    assert proc.stdout.strip(), proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def _runs(ws):
    return {p.parent.name: read_json(p) for p in ws.runs_dir.glob("run-*/run.json")}


def _plan(devices, cases=CASES):
    return [{"case": case, "device": device, "phase": phase}
            for device in devices for case in cases for phase in ("baseline", "trial")]


def _assert_handoff(ws, out, *, phase, case, device, runs):
    assert out["status"] == "candidate", out
    assert out["completed"] is False, out
    assert out["action_required"], out
    source = out["handoff"]
    assert source["phase"] == phase
    assert source["case"] == case
    assert source["device"] == device
    assert source["action_required"] == out["action_required"]
    assert source["run_id"] in runs
    run = runs[source["run_id"]]
    assert run["verdict"]["action_required"] == out["action_required"]
    assert run["preflight"]["unlock"]["status"] == "missing_secret"
    report = Path(source["report"])
    assert report.is_file()
    assert report.resolve() == (ws.runs_dir / source["run_id"] / "report.md").resolve()
    assert out["action_required"] in report.read_text(encoding="utf-8")
    saved = ExperienceStore(ws.root, "motor_hm").get(XID)
    assert saved["status"] == "candidate"
    assert saved["validations"][-1]["completed"] is False
    assert saved["validations"][-1]["handoff"] == source
    # An interrupted comparison cannot become an adoptable result.
    with pytest.raises(PermissionError):
        ExperienceStore(ws.root, "motor_hm").adopt(XID)


def test_experience_cli_baseline_handoff_exits_2_and_stops_plan(handoff_ws):
    ws = handoff_ws
    devices = [_device(ws, "motor_hm_locked.json"), _device(ws, "motor_hm_base.json")]
    code, out = _cli_process(ws, devices)
    assert code == 2, out
    runs = _runs(ws)
    assert len(runs) == 1, "A baseline handoff must stop before its trial and all later work"
    _assert_handoff(ws, out, phase="baseline", case=CASES[0], device=devices[0], runs=runs)
    assert out["rows"] == []
    assert out["exercised"] == [] and out["exercised_cases"] == []
    assert out["not_run"] == _plan(devices)[1:]


def test_experience_cli_trial_handoff_uses_real_locked_world_and_stops(handoff_ws, monkeypatch):
    ws = handoff_ws
    devices = [_device(ws, "motor_hm_base.json"), _device(ws, "motor_hm_compact.json")]
    world = Path(devices[0].split(":", 1)[1])
    original_run = commands.run_case
    phases = []

    def run_and_lock_after_baseline(*args, **kwargs):
        overlay = kwargs["overlay"]
        phases.append("trial" if overlay.trial else "baseline")
        run = original_run(*args, **kwargs)
        if len(phases) == 1:
            assert not run["verdict"].get("action_required")
            spec = read_json(world)
            spec["initial"].update(locked=True, screen_on=False)
            write_json(world, spec)
        return run

    monkeypatch.setattr(commands, "run_case", run_and_lock_after_baseline)
    # The CLI still constructs its own Workspace; reuse the isolated config so
    # a developer's config.local.json cannot supply a PIN to this replay.
    monkeypatch.setattr("vpdtest.cli.Workspace", lambda root: ws)
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(_argv(ws, devices))
    out = json.loads(stdout.getvalue())
    assert code == 2, out
    assert phases == ["baseline", "trial"]
    runs = _runs(ws)
    assert len(runs) == 2
    _assert_handoff(ws, out, phase="trial", case=CASES[0], device=devices[0], runs=runs)
    source = out["handoff"]
    assert source["baseline_run"] in runs
    assert source["baseline_run"] != source["run_id"]
    assert not runs[source["baseline_run"]]["verdict"].get("action_required")
    baseline_report = Path(source["baseline_report"])
    assert baseline_report.is_file()
    assert baseline_report.resolve() == (ws.runs_dir / source["baseline_run"] / "report.md").resolve()
    assert out["rows"] == [], "The partial baseline/trial pair is not a completed comparison"
    assert out["exercised"] == [] and out["exercised_cases"] == []
    assert out["not_run"] == _plan(devices)[2:]


def test_experience_cli_normal_control_completes_every_pair(handoff_ws):
    ws = handoff_ws
    devices = [_device(ws, "motor_hm_base.json"), _device(ws, "motor_hm_compact.json")]
    code, out = _cli_process(ws, devices)
    assert code == 0, out
    assert out["completed"] is True
    assert out["not_run"] == []
    assert not out.get("action_required") and not out.get("handoff")
    assert len(out["rows"]) == len(CASES) * len(devices)
    runs = _runs(ws)
    assert len(runs) == 2 * len(out["rows"])
    assert {(row["case"], row["device"]) for row in out["rows"]} == {
        (case, device) for device in devices for case in CASES}
    for row in out["rows"]:
        assert row["baseline_run"] in runs and row["trial_run"] in runs
        assert not runs[row["baseline_run"]]["verdict"].get("action_required")
        assert not runs[row["trial_run"]]["verdict"].get("action_required")
    saved = ExperienceStore(ws.root, "motor_hm").get(XID)
    assert saved["validations"][-1]["completed"] is True
    assert saved["validations"][-1]["not_run"] == []


def test_experience_completed_regression_survives_later_handoff(handoff_ws):
    """A later intervention cannot erase a regression already observed in a complete pair."""
    ws = handoff_ws
    xs = ExperienceStore(ws.root, "motor_hm")
    cases = ["CASE-completed-regression", "CASE-handoff", "CASE-must-not-run"]
    devices = ["replay:offline-contract"]
    calls = []

    def one(case, device, overlay):
        phase = "trial" if overlay.trial else "baseline"
        calls.append((case, phase))
        rid = f"evidence-{len(calls)}"
        directory = ws.runs_dir / rid
        directory.mkdir(parents=True)
        (directory / "report.md").write_text("isolated contract evidence", encoding="utf-8")
        if case == cases[0]:
            verdict = {"verdict": "failed" if phase == "trial" else "passed"}
        else:
            assert case == cases[1] and phase == "baseline", "Validation continued after a handoff"
            verdict = {"verdict": "blocked", "action_required": "synthetic device requires intervention"}
        return {"run_id": rid, "dir": str(directory), "verdict": verdict, "steps": []}

    out = xs.validate(XID, cases, devices, one)
    assert calls == [(cases[0], "baseline"), (cases[0], "trial"), (cases[1], "baseline")]
    assert out["status"] == "rejected"
    assert xs.get(XID)["status"] == "rejected"
    assert out["completed"] is False and out["action_required"]
    assert len(out["rows"]) == 1 and out["rows"][0]["regression"] is True
    assert out["rows"][0]["baseline"] == "passed" and out["rows"][0]["trial"] == "failed"
    assert out["handoff"]["case"] == cases[1] and out["handoff"]["phase"] == "baseline"
    assert Path(out["handoff"]["report"]).is_file()
    assert out["not_run"] == _plan(devices, cases)[3:]
    with pytest.raises(PermissionError):
        xs.adopt(XID)
