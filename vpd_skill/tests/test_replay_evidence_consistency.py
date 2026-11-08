"""Synthetic evidence only: duplicate ingestion must never erase conflicting proof."""
import hashlib
from copy import deepcopy

import pytest
from verdict_fixtures import NAV_CASE

from vpdtest import cases as C
from vpdtest.cli import build_parser
from vpdtest.commands import cmd_case
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import write_json


def asset():
    return {"app": "synthetic", "steps": [], "assertions": []}


def replay(case, rid, **changes):
    return {"run_id": rid, "hash": C.content_hash(case), "deps": "current",
            "verdict": "passed", "alerts": 0, "validation_level": "real_device",
            "device": "synthetic", "ts": "first", **changes}


@pytest.mark.parametrize("changes,fields", [
    ({"alerts": 1}, ["alerts"]),
    ({"execution": {"status": "error", "errors": ["synthetic"]}}, ["execution"]),
    ({"verdict": "failed"}, ["verdict"]),
    ({"coverage": {"assertions": []}}, ["coverage"]),
    ({"validation_level": "offline"}, ["validation_level"]),
    ({"device": "different-source"}, ["device"]),
    ({"facts": {"ts": "sensitive-time"}}, ["facts"]),
    ({"hash": "old"}, ["hash"]),
    ({"deps": "old"}, ["deps"]),
    ({"alerts": False}, ["alerts"]),
    ({"alerts": 0.0}, ["alerts"]),
])
def test_conflicting_id_blocks_before_version_filter_or_last_write(changes, fields):
    case = asset()
    case["replays"] = [replay(case, "r1", **changes), replay(case, "r2"), replay(case, "r1")]
    before = deepcopy(case)
    basis = C.replay_basis(case, "current")
    assert basis["ok"] is False
    assert basis["conflicts"] == [{"run_id": "r1", "fields": fields}]
    assert "r1" in basis["why"] and "sensitive-time" not in basis["why"]
    assert "rows" not in basis and case == before


@pytest.mark.parametrize("left,right", [
    ({}, {"facts": None}), ({"facts": {}}, {"facts": {"verified": False}}),
    ({"facts": {"ts": "a"}}, {"facts": {"ts": "b"}}),
    ({"facts": {"verified": False}}, {"facts": {"verified": 0}}),
])
def test_missing_and_nested_type_and_time_differences_are_preserved(left, right):
    assert C._replay_difference_fields(left, right) == ["facts"]


def test_exact_and_timestamp_duplicates_count_once_without_moving_first_position():
    case = asset()
    first = replay(case, "r1")
    case["replays"] = [first, deepcopy(first), {**first, "ts": "later"}]
    basis = C.replay_basis(case, "current")
    assert not basis["ok"] and basis["count"] == 1
    case["replays"] = [first, replay(case, "blocked", verdict="blocked"),
                       replay(case, "r2"), {**first, "ts": "later"}]
    basis = C.replay_basis(case, "current")
    assert not basis["ok"] and "只有 1 次" in basis["why"]
    case["replays"].append(replay(case, "r3"))
    basis = C.replay_basis(case, "current")
    assert basis["ok"] and basis["replays"] == ["r2", "r3"]
    assert [r["run_id"] for r in basis["ignored"]] == ["r1", "blocked"]


@pytest.mark.parametrize("old", [{"hash": "old"}, {"deps": "old"}])
def test_unrelated_old_id_conflicts_do_not_block_current_runs(old):
    case = asset()
    case["replays"] = [replay(case, "old", **old), replay(case, "old", alerts=1, **old),
                       replay(case, "r1"), replay(case, "r2")]
    basis = C.replay_basis(case, "current")
    assert basis["ok"] and basis["replays"] == ["r1", "r2"]


@pytest.mark.parametrize("signature,expected", [(["A"], False), (["B"], True), ([], False), (None, False)])
def test_all_current_decisive_failure_signatures_must_match(signature, expected):
    case = asset()
    early = replay(case, "early", verdict="failed")
    if signature is not None:
        early["failure_signature"] = signature
    case["replays"] = [early, replay(case, "blocked", verdict="blocked"),
                       replay(case, "r1", verdict="failed", failure_signature=["B"]),
                       replay(case, "r2", verdict="failed", failure_signature=["B"])]
    basis = C.replay_basis(case, "current")
    assert basis["ok"] is expected
    if expected:
        assert basis["signature"] == ["B"] and basis["replays"] == ["r1", "r2"]
    else:
        assert "失败特征不一致" in basis["why"]
    early["hash"] = "old"
    assert C.replay_basis(case, "current")["ok"]


def recorded_run(case, **changes):
    return {"run_id": "synthetic-run", "case": {"hash": C.content_hash(case), "deps": "current"},
            "verdict": {"verdict": "passed"}, "device_spec": "synthetic",
            "validation_level": "real_device", **changes}


def test_record_replay_idempotence_conflict_and_source_preservation(verdict_ws, monkeypatch):
    ws = verdict_ws
    case = ws.load_case(NAV_CASE)
    run = recorded_run(case, facts={"ts": "source-time", "verified": False})
    source = deepcopy(run)
    monkeypatch.setattr(C, "now_iso", lambda: "first")
    C.record_replay(ws, NAV_CASE, run)
    path = ws.case_path(NAV_CASE)
    original = path.read_bytes()
    monkeypatch.setattr(C, "now_iso", lambda: "second")
    result = C.record_replay(ws, NAV_CASE, run)
    assert len(result["replays"]) == 1 and path.read_bytes() == original and run == source
    bad = deepcopy(run)
    bad["facts"]["verified"] = 0
    with pytest.raises(ValueError, match="synthetic-run.*facts"):
        C.record_replay(ws, NAV_CASE, bad)
    assert path.read_bytes() == original and run == source
    other = {**run, "run_id": "independent-run"}
    C.record_replay(ws, NAV_CASE, other)
    assert C.replay_basis(ws.load_case(NAV_CASE), "current")["ok"]


def test_record_replay_checks_every_prior_summary_before_any_mutation(verdict_ws):
    ws = verdict_ws
    run = recorded_run(ws.load_case(NAV_CASE))
    C.record_replay(ws, NAV_CASE, run)
    case = ws.load_case(NAV_CASE)
    case["replays"].insert(0, {**case["replays"][0], "hash": "old", "alerts": 1})
    write_json(ws.case_path(NAV_CASE), case)
    before = ws.case_path(NAV_CASE).read_bytes()
    with pytest.raises(ValueError, match="alerts, hash"):
        C.record_replay(ws, NAV_CASE, run)
    assert ws.case_path(NAV_CASE).read_bytes() == before


def snapshot(ws):
    return {p.relative_to(ws.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in ws.root.rglob("*") if p.is_file()}


def test_readiness_and_promotion_preview_share_conflict_and_never_write(verdict_ws):
    ws = verdict_ws
    case = ws.load_case(NAV_CASE)
    case["status"] = "candidate"
    kn = KnowledgeStore(ws.root)
    deps = C.dependency_hash(case, ws.app(case["app"]), kn)
    case["replays"] = [replay(case, "r1", deps=deps, alerts=1), replay(case, "r2", deps=deps),
                       replay(case, "r1", deps=deps)]
    write_json(ws.case_path(NAV_CASE), case)
    before = snapshot(ws)
    basis = C.replay_basis(case, deps)
    row = cmd_case(ws, build_parser().parse_args(["case", "readiness", NAV_CASE]))["results"][0]
    assert row["replay"]["conflicts"] == basis["conflicts"]
    assert row["promotion"]["status"] == "blocked"
    assert row["promotion"]["reason"] == basis["why"]
    with pytest.raises(PermissionError) as error:
        C.promote(ws, NAV_CASE, "replay_verified", kn, dry_run=True)
    assert str(error.value) == basis["why"]
    assert snapshot(ws) == before
