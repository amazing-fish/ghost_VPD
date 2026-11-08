"""Replay chronology: execution intervals, not delayed ingestion, prove recovery.

All assets and knowledge are synthetic. The CLI schedule only pauses AFTER
run_case returns; it does not edit run evidence or replace runtime results.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from itertools import permutations
from pathlib import Path

import pytest
from verdict_fixtures import NAV_CASE, NAV_RULE

from vpdtest import cases as C
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import read_json, write_json

SKILL = Path(__file__).resolve().parents[1]
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def instant(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def asset():
    return {"app": "synthetic", "steps": [], "assertions": []}


def replay(case, rid, start=None, finish=None, **changes):
    row = {"run_id": rid, "hash": C.content_hash(case), "deps": "current", "verdict": "passed",
           "ts": instant(100), "alerts": 0, "validation_level": "offline_replay"}
    if start is not None:
        row["started"] = instant(start)
    if finish is not None:
        row["finished"] = instant(finish)
    return {**row, **changes}


@pytest.mark.parametrize("bad_first", [True, False])
@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_whole_run_recovery_is_invariant_under_ingestion_order(bad_first, order):
    case = asset()
    rows = [replay(case, "bad", 0 if bad_first else 8, 1 if bad_first else 9, verdict="blocked"),
            replay(case, "pass-1", 2, 3), replay(case, "pass-2", 4, 5)]
    case["replays"] = [rows[i] for i in order]
    original = deepcopy(case)
    result = C.replay_basis(case, "current")
    assert result["ok"] is bad_first
    assert case == original
    if bad_first:
        assert set(result["replays"]) == {"pass-1", "pass-2"}
    else:
        assert "只有 0 次" in result["why"]


@pytest.mark.parametrize("verdict,execution", [
    ("blocked", {}), ("pending", {}), ("not_applicable", {}), ("error", {}),
    ("failed", {"status": "error", "errors": [{"stage": "cleanup"}]}),
])
def test_all_uncertain_results_reset_and_one_new_run_is_insufficient(verdict, execution):
    case = asset()
    case["replays"] = [replay(case, "bad", 2, 3, verdict=verdict, execution=execution),
                       replay(case, "old", 0, 1), replay(case, "new", 4, 5)]
    assert "只有 1 次" in C.replay_basis(case, "current")["why"]
    case["replays"].append(replay(case, "new-2", 6, 7))
    assert C.replay_basis(case, "current")["ok"]


@pytest.mark.parametrize("start,accepted", [(0, False), (1, False), (2, False), (2.001, True)])
def test_overlap_or_same_millisecond_does_not_prove_subsequent_run(start, accepted):
    case = asset()
    case["replays"] = [replay(case, "bad", 0, 2, verdict="pending"),
                       replay(case, "a", start, 3), replay(case, "b", 4, 5)]
    result = C.replay_basis(case, "current")
    assert result["ok"] is accepted
    if not accepted:
        assert result["timing"]["not_after"] == ["a"]


def test_offset_instants_not_text_sorting_determine_order():
    case = asset()
    bad = replay(case, "bad", 0, 1, verdict="blocked")
    bad["finished"] = "2026-01-01T09:00:01.000+09:00"
    good = replay(case, "a", 2, 3)
    good["started"] = "2025-12-31T19:00:02.000-05:00"
    case["replays"] = [good, replay(case, "b", 4, 5), bad]
    assert C.replay_basis(case, "current")["ok"]
    good["started"] = "2025-12-31T19:00:01.000-05:00"
    assert not C.replay_basis(case, "current")["ok"]


@pytest.mark.parametrize("changes", [
    {"started": None}, {"started": "malformed"}, {"started": 1},
    {"started": "2026-01-01T00:00:00"}, {"finished": "2026-01-01"},
    {"started": instant(5), "finished": instant(4)},
])
def test_explicit_invalid_intervals_fail_closed_even_without_reset(changes):
    case = asset()
    bad = replay(case, "invalid", 0, 1, **changes)
    case["replays"] = [bad, replay(case, "good", 2, 3)]
    before = deepcopy(case)
    result = C.replay_basis(case, "current")
    assert not result["ok"] and result["timing_issues"][0]["run_id"] == "invalid"
    assert case == before


@pytest.mark.parametrize("field", ["started", "finished"])
def test_partial_interval_cannot_fall_back_to_ingestion_ts(field):
    case = asset()
    bad = replay(case, "partial", 0, 1, verdict="blocked")
    del bad[field]
    case["replays"] = [bad, replay(case, "a", 200, 201), replay(case, "b", 202, 203)]
    assert C.replay_basis(case, "current")["timing_issues"]


def test_legacy_decisive_evidence_without_resets_stays_usable_and_unchanged():
    case = asset()
    case["replays"] = [replay(case, "legacy", ts="old-unusable-ts"), replay(case, "new", 2, 3)]
    before = deepcopy(case)
    assert C.replay_basis(case, "current")["ok"] and case == before
    case["replays"][1] = replay(case, "legacy-2", ts=None)
    assert C.replay_basis(case, "current")["ok"]


def test_legacy_reset_uses_ts_only_as_completion_upper_bound():
    case = asset()
    case["replays"] = [replay(case, "legacy-bad", ts=instant(3), verdict="blocked"),
                       replay(case, "a", 2, 4), replay(case, "b", 5, 6)]
    assert not C.replay_basis(case, "current")["ok"]
    case["replays"][1] = replay(case, "a", 4, 5)
    before = deepcopy(case)
    result = C.replay_basis(case, "current")
    assert result["ok"] and result["timing"]["legacy_upper_bounds"] == ["legacy-bad"]
    assert case == before and "finished" not in case["replays"][0]


@pytest.mark.parametrize("ts", [None, "invalid", "2026-01-01T00:00:01", 1])
def test_unknown_reset_bound_is_not_hidden_by_other_known_resets(ts):
    case = asset()
    rows = [replay(case, "unknown", ts=ts, verdict="blocked"),
            replay(case, "known", 0, 1, verdict="pending"),
            replay(case, "a", 200, 201), replay(case, "b", 202, 203)]
    for history in (rows, list(reversed(rows))):
        case["replays"] = history
        result = C.replay_basis(case, "current")
        assert not result["ok"] and result["timing"]["unbounded"] == ["unknown"]
        assert "缺少有效完成上界" in result["why"]


def test_legacy_passes_cannot_claim_order_from_ts_or_array_position():
    case = asset()
    case["replays"] = [replay(case, "bad", 0, 1, verdict="blocked"),
                       replay(case, "legacy-1"), replay(case, "legacy-2")]
    result = C.replay_basis(case, "current")
    assert not result["ok"] and result["timing"]["missing_intervals"] == ["legacy-1", "legacy-2"]


def coverage(result):
    return {"assertions": [{"id": "A", "result": result, "verified": result in ("pass", "fail"),
                            "applicable": result != "not_applicable"}]}


@pytest.mark.parametrize("bad_first", [True, False])
@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_assertion_reset_uses_same_chronology_even_when_aggregate_is_decisive(bad_first, order):
    case = {**asset(), "assertions": [{"id": "A"}]}
    rows = [replay(case, "unknown-A", 0 if bad_first else 8, 1 if bad_first else 9,
                   coverage=coverage("not_run")),
            replay(case, "a", 2, 3, coverage=coverage("pass")),
            replay(case, "b", 4, 5, coverage=coverage("pass"))]
    case["replays"] = [rows[i] for i in order]
    result = C.replay_basis(case, "current")
    assert result["ok"] is bad_first
    assert result["coverage"]["A"]["count"] == (2 if bad_first else 0)


def test_assertion_reset_outside_global_eligible_rows_still_constrains_coverage():
    case = {**asset(), "assertions": [{"id": "A"}]}
    case["replays"] = [replay(case, "whole-reset", 1, 2, verdict="blocked"),
                       replay(case, "spanning-A-reset", 0, 10, coverage=coverage("not_run")),
                       replay(case, "a", 3, 4, coverage=coverage("pass")),
                       replay(case, "b", 5, 6, coverage=coverage("pass"))]
    result = C.replay_basis(case, "current")
    assert not result["ok"] and result["replays"] == ["a", "b"]
    assert result["coverage"]["A"]["count"] == 0
    assert "spanning-A-reset" in result["coverage"]["A"]["timing"]["resets"]
    case["replays"].extend([replay(case, "c", 11, 12, coverage=coverage("pass")),
                            replay(case, "d", 13, 14, coverage=coverage("pass"))])
    assert C.replay_basis(case, "current")["coverage"]["A"]["runs"] == ["c", "d"]
    assert C.replay_basis(case, "current")["ok"]


def test_assertion_not_applicable_is_not_a_reset_and_legacy_passes_remain_valid():
    case = {**asset(), "assertions": [{"id": "A"}]}
    case["replays"] = [replay(case, "a", coverage=coverage("pass")),
                       replay(case, "na", coverage=coverage("not_applicable")),
                       replay(case, "b", coverage=coverage("pass"))]
    assert C.replay_basis(case, "current")["ok"]
    case["replays"][1]["coverage"] = coverage("not_run")
    result = C.replay_basis(case, "current")
    assert not result["ok"] and result["coverage"]["A"]["count"] == 0


def source_run(case, **changes):
    return {"run_id": "source", "case": {"hash": C.content_hash(case), "deps": "current"},
            "verdict": {"verdict": "passed"}, "validation_level": "offline_replay",
            "device_spec": "synthetic", "started": instant(0), "finished": instant(1), **changes}


def test_record_keeps_source_interval_separate_and_legacy_retry_does_not_enrich(verdict_ws):
    ws = verdict_ws
    run = source_run(ws.load_case(NAV_CASE))
    original = deepcopy(run)
    saved = C.record_replay(ws, NAV_CASE, run)["replays"][-1]
    assert saved["started"] == run["started"] and saved["finished"] == run["finished"]
    assert saved["ts"] != saved["finished"] and run == original
    path = ws.case_path(NAV_CASE)
    before = path.read_bytes()
    C.record_replay(ws, NAV_CASE, run)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="finished"):
        C.record_replay(ws, NAV_CASE, {**run, "finished": instant(2)})
    assert path.read_bytes() == before
    case = ws.load_case(NAV_CASE)
    for key in ("started", "finished"):
        del case["replays"][0][key]
    write_json(path, case)
    before = path.read_bytes()
    C.record_replay(ws, NAV_CASE, run)
    assert path.read_bytes() == before and run == original


def test_legacy_same_id_does_not_hide_two_contradictory_timed_summaries(verdict_ws):
    ws = verdict_ws
    run = source_run(ws.load_case(NAV_CASE))
    C.record_replay(ws, NAV_CASE, run)
    case = ws.load_case(NAV_CASE)
    timed = case["replays"][0]
    legacy = {k: v for k, v in timed.items() if k not in ("started", "finished")}
    case["replays"] = [legacy, timed, {**timed, "finished": instant(2)}]
    write_json(ws.case_path(NAV_CASE), case)
    before = ws.case_path(NAV_CASE).read_bytes()
    assert not C.replay_basis(case, "current")["ok"]
    with pytest.raises(ValueError, match="finished"):
        C.record_replay(ws, NAV_CASE, run)
    assert ws.case_path(NAV_CASE).read_bytes() == before


CLI_DRIVER = r'''
import json
from pathlib import Path
import sys
import time
from vpdtest.cli import main
root, fixture, env, marker, release = sys.argv[1:]
def trace(frame, event, result):
    if event == "return" and frame.f_code.co_name == "run_case" and frame.f_globals.get("__name__") == "vpdtest.runner":
        Path(marker).write_text(json.dumps(result), encoding="utf-8")
        deadline = time.monotonic() + 45
        while not Path(release).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Scheduling barrier timeout")
            time.sleep(.01)
    return trace
sys.settrace(trace)
raise SystemExit(main(["--workspace", root, "run", "TC-TEST-VERDICT-NAV", "--device", fixture,
                      "--env", "audit_state=" + env, "--record-replay"]))
'''


@pytest.mark.parametrize("branch_reset", [False, True], ids=["whole", "assertion"])
@pytest.mark.parametrize("bad_first,delayed", [(False, False), (True, False), (False, True), (True, True)],
                         ids=["serial_old_pass", "serial_old_reset", "late_old_pass", "late_old_reset"])
def test_real_cli_delayed_ingestion_and_sequential_controls(verdict_ws, tmp_path, bad_first, delayed, branch_reset):
    ws = verdict_ws
    kn = KnowledgeStore(ws.root)
    items = kn.items()
    next(item for item in items if item["id"] == NAV_RULE)["status"] = "confirmed"
    write_json(kn.path, {"items": items})
    case = ws.load_case(NAV_CASE)
    case.update(status="candidate", env_requirements={"audit_state": "ready"},
                steps=[{"id": "S1", "action": "ensure_state", "state": "my_tab", "oracle_refs": [NAV_RULE]}],
                assertions=[{"id": "A1", "at": "S1", "type": "text_present", "text": "注册/登录",
                             "oracle_refs": [NAV_RULE]}])
    if branch_reset:
        # A stable A1 product failure keeps the whole run decisive even when the
        # independent A2 branch's evidence is unknown. Real verdict aggregation,
        # not a hand-edited summary, exercises the assertion-only reset path.
        case["env_requirements"] = {}
        case["conditions"] = {"branch": {"kind": "page", "true": {"type": "text_present", "text": "注册/登录"},
                                         "false": {"type": "text_present", "text": "synthetic counter evidence"}}}
        case["assertions"] = [
            {"id": "A1", "at": "S1", "type": "text_present", "text": "synthetic expected missing text",
             "oracle_refs": [NAV_RULE]},
            {"id": "A2", "at": "S1", "type": "text_present", "text": "注册/登录", "oracle_refs": [NAV_RULE],
             "guard": {"condition": "branch", "equals": True}},
        ]
    write_json(ws.case_path(NAV_CASE), case)
    assert not C.lint(case, ws.app(case["app"]), KnowledgeStore(ws.root), ws.policy(), runs_dir=ws.runs_dir)
    driver, release = tmp_path / "schedule.py", tmp_path / "release"
    driver.write_text(CLI_DRIVER, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("VPD_SECRET_")}
    env.update(PYTHONPATH=os.pathsep.join([str(SKILL), *sys.path]), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    fixture = "replay:" + str(SKILL / "fixtures/worlds/motor_hm_base.json")
    unknown_world = tmp_path / "branch-unknown.json"
    unknown_world.write_text((SKILL / "fixtures/worlds/motor_hm_base.json").read_text(encoding="utf-8").replace(
        "注册/登录", "synthetic branch unknown"), encoding="utf-8")

    def device(good):
        return "replay:" + str(unknown_world) if branch_reset and not good else fixture

    def cli(args):
        process = subprocess.run([sys.executable, "-m", "vpdtest", "--workspace", str(ws.root), *args],
                                 cwd=SKILL, env=env, capture_output=True, text=True, encoding="utf-8", timeout=45,
                                 check=False)
        index = len(list(tmp_path.glob("cli-*.stdout.json")))
        (tmp_path / f"cli-{index}.stdout.json").write_text(process.stdout, encoding="utf-8")
        (tmp_path / f"cli-{index}.stderr.txt").write_text(process.stderr, encoding="utf-8")
        assert process.returncode == 0, (process.stdout, process.stderr)
        return json.loads(process.stdout)

    def run(good):
        result = cli(["run", NAV_CASE, "--device", device(good), "--env", "audit_state=" + ("ready" if good else "blocked"),
                      "--record-replay"])
        original = read_json(Path(result["report"]).with_name("run.json"))
        assert original["verdict"]["verdict"] == ("failed" if branch_reset else "passed" if good else "blocked")
        if branch_reset:
            assert original["assertions"][1]["result"] == ("pass" if good else "pending")
        return original

    earlier = [False] if bad_first else [True, True]
    later = [True, True] if bad_first else [False]
    processes, markers, originals = [], [], {}
    try:
        for index, good in enumerate(earlier):
            if delayed:
                marker = tmp_path / f"paused-{index}.json"
                markers.append(marker)
                processes.append(subprocess.Popen([sys.executable, str(driver), str(ws.root), device(good),
                                                   "ready" if good else "blocked", str(marker), str(release)],
                                                  cwd=SKILL, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                  text=True, encoding="utf-8"))
            else:
                run(good)
        if delayed:
            deadline = time.monotonic() + 45
            while not all(marker.exists() for marker in markers):
                assert all(process.poll() is None for process in processes)
                assert time.monotonic() < deadline
                time.sleep(.01)
            assert not ws.load_case(NAV_CASE).get("replays")
        originals = {path: path.read_bytes() for path in ws.runs_dir.glob("*/run.json")}
        time.sleep(.01)  # Distinct real milliseconds; no time mocking.
        later_runs = [run(good) for good in later]
        originals.update({path: path.read_bytes() for path in ws.runs_dir.glob("*/run.json")})
        if delayed:
            assert max(datetime.fromisoformat(read_json(marker)["finished"]) for marker in markers) < min(
                datetime.fromisoformat(row["started"]) for row in later_runs)
    finally:
        release.write_text("Release only the scheduling barrier", encoding="utf-8")
        for index, process in enumerate(processes):
            stdout, stderr = process.communicate(timeout=45)
            (tmp_path / f"scheduled-{index}.stdout.json").write_text(stdout, encoding="utf-8")
            (tmp_path / f"scheduled-{index}.stderr.txt").write_text(stderr, encoding="utf-8")
            assert process.returncode == 0, (stdout, stderr)
    assert all(path.read_bytes() == data for path, data in originals.items())
    summaries = ws.load_case(NAV_CASE)["replays"]
    assert len({row["run_id"] for row in summaries}) == 3
    assert len({(row["hash"], row["deps"]) for row in summaries}) == 1
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in ws.root.rglob("*") if path.is_file()}
    row = cli(["case", "readiness", NAV_CASE, "--known-issue", "synthetic-only"])["results"][0]
    assert row["review"]["state"] == "not_required"
    assert row["replay"]["ok"] is bad_first
    assert row["promotion"]["status"] == ("eligible" if bad_first else "blocked")
    if bad_first:
        preview = C.promote(ws, NAV_CASE, "replay_verified", KnowledgeStore(ws.root),
                            known_issue="synthetic-only", dry_run=True)
        assert preview["replays"] == row["replay"]["replays"]
    else:
        with pytest.raises(PermissionError) as error:
            C.promote(ws, NAV_CASE, "replay_verified", KnowledgeStore(ws.root),
                      known_issue="synthetic-only", dry_run=True)
        assert str(error.value) == row["replay"]["why"]
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before} == before
    for summary in summaries:
        original = read_json(ws.runs_dir / summary["run_id"] / "run.json")
        assert all(summary[key] == original[key] for key in ("started", "finished"))
