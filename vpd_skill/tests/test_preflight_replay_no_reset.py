"""#235: rejected setup is retained but cannot erase verified product evidence."""
from copy import deepcopy
import pytest
from test_condition_runner import condition_rig, _run, CASE
from vpdtest import cases
from vpdtest.util import write_json


def recorded(rig):
    ws, case, _ = rig
    blocked_case = deepcopy(case)
    blocked_case["env_requirements"] = {"account_state": "ready"}
    # Requirement changes affect hash; use the same content for every run.
    write_json(ws.case_path(CASE), blocked_case)
    blocked_case["replays"] = []
    write_json(ws.case_path(CASE), blocked_case)
    for _ in range(2):
        cases.record_replay(ws, CASE, _run(rig, case=blocked_case, env={"account_state": "ready"}))
    run = _run(rig, case=blocked_case)
    return ws, run


def test_preflight_block_keeps_two_verified_replays(condition_rig):
    ws, run = recorded(condition_rig)
    case = cases.record_replay(ws, CASE, run)
    assert run["verdict"]["verdict"] == "blocked"
    basis = cases.replay_basis(case)
    assert basis["ok"], basis
    assert case["replays"][-1]["preflight_evidence"]["dispatch_count"] == 0
    assert len(basis["replays"]) == 2
    assert run["run_id"] not in basis["replays"]
    assert all(item["count"] == 2 for item in basis["coverage"].values())


@pytest.mark.parametrize("change", ["legacy", "dispatch", "observation", "action", "attempt", "error", "pending", "product_fail"])
def test_unknown_or_product_execution_still_resets(condition_rig, change):
    ws, run = recorded(condition_rig)
    if change == "legacy": run.pop("preflight_evidence")
    elif change == "dispatch": run["preflight_evidence"]["dispatch_count"] = 1
    elif change == "observation": run["preflight_evidence"]["product_observations"] = 1
    elif change == "action": run["actions"] = 1
    elif change == "attempt": run["steps"][0]["attempts"] = [{"sent": None}]
    elif change == "error": run["verdict"]["execution"]["status"] = "error"
    elif change == "pending": run["verdict"]["verdict"] = "pending"
    elif change == "product_fail": run["verdict"]["product_failed"] = True
    case = cases.record_replay(ws, CASE, run)
    assert not cases.replay_basis(case)["ok"]


def test_legacy_retry_does_not_rewrite_or_upgrade_evidence(condition_rig):
    ws, run = recorded(condition_rig)
    legacy = deepcopy(run)
    legacy.pop("preflight_evidence")
    original = cases.record_replay(ws, CASE, legacy)
    before = ws.case_path(CASE).read_bytes()
    assert cases.record_replay(ws, CASE, run) == original
    assert ws.case_path(CASE).read_bytes() == before
    assert not cases.replay_basis(original)["ok"]


@pytest.mark.parametrize("field", ["actions", "steps", "cleanup", "safety_hold", "safety_stops", "assertions"])
def test_missing_full_run_evidence_never_means_zero(condition_rig, field):
    ws, run = recorded(condition_rig)
    run.pop(field)
    case = cases.record_replay(ws, CASE, run)
    assert "preflight_evidence" not in case["replays"][-1]
    assert not cases.replay_basis(case)["ok"]


def test_only_preflight_blocks_cannot_qualify(condition_rig):
    ws, case, _ = condition_rig
    case["env_requirements"] = {"account_state": "ready"}
    write_json(ws.case_path(CASE), case)
    for _ in range(2):
        result = cases.record_replay(ws, CASE, _run(condition_rig, case=case))
    assert not cases.replay_basis(result)["ok"]


@pytest.mark.parametrize("value", [False, 0.0, None, "0"])
def test_malformed_dispatch_count_is_not_proof(condition_rig, value):
    ws, run = recorded(condition_rig)
    run["preflight_evidence"]["dispatch_count"] = value
    case = cases.record_replay(ws, CASE, run)
    assert "preflight_evidence" not in case["replays"][-1]
    assert not cases.replay_basis(case)["ok"]


def test_missing_event_log_is_not_zero_dispatch(condition_rig, monkeypatch):
    from vpdtest import runner
    ws, case, _ = condition_rig
    case["env_requirements"] = {"account_state": "ready"}
    write_json(ws.case_path(CASE), case)
    monkeypatch.setattr(runner, "read_jsonl", lambda path: [])
    run = _run(condition_rig, case=case)
    assert "preflight_evidence" not in run
