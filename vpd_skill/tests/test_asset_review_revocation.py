"""Synthetic approval withdrawal, without maturity or product verdict rewrites."""
from copy import deepcopy
from datetime import datetime

import pytest
from conftest import world
from verdict_fixtures import NAV_CASE

from vpdtest import cases as C
from vpdtest import commands
from vpdtest.cli import build_parser
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import write_json

REVIEWER = "pytest-synthetic-reviewer"
BASIS = "Synthetic approval withdrawal for source re-check; no production decision."


def approved(ws, exploration=False, status="candidate"):
    case = ws.load_case(NAV_CASE)
    case["status"] = status
    if exploration:
        case["origin"] = {"type": "session"}
    write_json(ws.case_path(NAV_CASE), case)
    return C.approve(ws, NAV_CASE, KnowledgeStore(ws.root), REVIEWER, "Synthetic approval only")


@pytest.mark.parametrize("exploration", [False, True])
def test_matching_binding_does_not_override_revocation(verdict_ws, exploration):
    ws = verdict_ws
    case = approved(ws, exploration)
    case["asset_review"]["revocation"] = {
        "reviewer": REVIEWER, "basis": BASIS, "ts": "2026-11-07T00:00:00+00:00"}
    write_json(ws.case_path(NAV_CASE), case)
    info = C.review_status(ws, case)
    assert info["required"] and not info["valid"] and info["state"] == "revoked"
    with pytest.raises(PermissionError, match="撤销"):
        C.require_review(ws, case)
    with pytest.raises(PermissionError, match="撤销"):
        C.promote(ws, NAV_CASE, "replay_verified", KnowledgeStore(ws.root), dry_run=True)


@pytest.mark.parametrize("value", [None, False, "", [], {}, {"basis": "incomplete"}])
def test_damaged_revocation_marker_fails_closed(verdict_ws, value):
    case = approved(verdict_ws)
    case["asset_review"]["revocation"] = value
    info = C.review_status(verdict_ws, case)
    assert info["required"] and not info["valid"] and info["state"] == "revoked"


def test_revoke_preserves_all_previous_evidence(verdict_ws):
    ws = verdict_ws
    case = approved(ws, status="regression")
    case["replays"] = [{"run_id": "historical-fixture", "verdict": "pending"}]
    case["product_status"] = {"status": "known_failure", "issue": "SYNTHETIC-1"}
    write_json(ws.case_path(NAV_CASE), case)
    before = deepcopy(case)
    knowledge = (ws.root / "knowledge/items.json").read_bytes()
    result = C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    for key in ("status", "replays", "product_status"):
        assert result[key] == before[key]
    assert {k: v for k, v in result["asset_review"].items() if k != "revocation"} == before["asset_review"]
    assert result["history"][:-1] == before["history"]
    event = result["history"][-1]
    assert event["event"] == "asset_review_revoked" and event["reviewer"] == REVIEWER
    assert event["basis"] == BASIS and event["binding"] == before["asset_review"]["binding"]
    assert datetime.fromisoformat(event["ts"]).utcoffset() is not None
    assert result["asset_review"]["revocation"] == {k: event[k] for k in ("reviewer", "basis", "ts")}
    assert C.content_hash(result) == C.content_hash(before)
    assert (ws.root / "knowledge/items.json").read_bytes() == knowledge


def test_repeat_does_not_overwrite_withdrawal(verdict_ws):
    ws = verdict_ws
    approved(ws)
    C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    before = ws.case_path(NAV_CASE).read_bytes()
    with pytest.raises(PermissionError, match="已撤销"):
        C.revoke_review(ws, NAV_CASE, "someone-else", "different basis")
    assert ws.case_path(NAV_CASE).read_bytes() == before


@pytest.mark.parametrize("status", ["draft", "candidate", "regression"])
def test_removing_record_or_relabelling_cannot_erase_history(verdict_ws, status):
    ws = verdict_ws
    approved(ws)
    case = C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    case.pop("asset_review")
    case["status"] = status
    write_json(ws.case_path(NAV_CASE), case)
    info = C.review_status(ws, case)
    assert info["required"] and not info["valid"]
    with pytest.raises(PermissionError, match="审核"):
        C.require_review(ws, case)


def test_renewal_keeps_history_and_requires_future_review(verdict_ws):
    ws = verdict_ws
    approved(ws)
    C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    case = C.approve(ws, NAV_CASE, KnowledgeStore(ws.root), REVIEWER, "Fresh independent review")
    info = C.require_review(ws, case)
    assert info["required"] and info["valid"] and info["state"] == "approved"
    assert case["status"] == "candidate" and "revocation" not in case["asset_review"]
    assert any(h.get("event") == "asset_review_revoked" for h in case["history"])
    case["purpose"] += " changed"
    with pytest.raises(PermissionError, match="审核失效"):
        C.require_review(ws, case)


def test_unreviewed_asset_cannot_have_an_approval_revoked(verdict_ws):
    before = verdict_ws.case_path(NAV_CASE).read_bytes()
    with pytest.raises(PermissionError, match="没有.*审核"):
        C.revoke_review(verdict_ws, NAV_CASE, REVIEWER, BASIS)
    assert verdict_ws.case_path(NAV_CASE).read_bytes() == before


@pytest.mark.parametrize("reviewer,basis", [("", BASIS), (None, BASIS), (7, BASIS),
                                          (REVIEWER, ""), (REVIEWER, None), (REVIEWER, {})])
def test_invalid_input_never_writes(verdict_ws, reviewer, basis):
    approved(verdict_ws)
    before = verdict_ws.case_path(NAV_CASE).read_bytes()
    with pytest.raises(ValueError):
        C.revoke_review(verdict_ws, NAV_CASE, reviewer, basis)
    assert verdict_ws.case_path(NAV_CASE).read_bytes() == before


@pytest.mark.parametrize("cid", ["../policy", "..\\policy", "/absolute", "C:other"])
def test_path_is_not_a_case_id(verdict_ws, cid, monkeypatch):
    monkeypatch.setattr(verdict_ws, "load_case", lambda _: pytest.fail("reject path first"))
    with pytest.raises(ValueError, match="ID"):
        C.revoke_review(verdict_ws, cid, REVIEWER, BASIS)


def test_cli_withdrawal_does_not_need_working_dependencies(verdict_ws, monkeypatch):
    ws = verdict_ws
    case = approved(ws)
    case["app"] = "missing-app-after-source-change"
    write_json(ws.case_path(NAV_CASE), case)
    monkeypatch.setattr(commands, "KnowledgeStore", lambda _: pytest.fail("must not load knowledge"))
    monkeypatch.setattr(ws, "app", lambda _: pytest.fail("must not load app"))
    args = build_parser().parse_args(["case", "revoke-review", NAV_CASE, "--reviewer", REVIEWER, "--basis", BASIS])
    result = commands.cmd_case(ws, args)
    assert result["case"] == NAV_CASE and result["status"] == "candidate"
    assert result["asset_review"]["state"] == "revoked" and not result["asset_review"]["valid"]


@pytest.mark.parametrize("entry", ["suite", "runner"])
def test_batch_blocks_before_device_or_run_creation(verdict_ws, monkeypatch, entry):
    ws = verdict_ws
    approved(ws)
    C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    monkeypatch.setattr(ws, "device", lambda *a, **k: pytest.fail("must not create device"))
    with pytest.raises(PermissionError, match="撤销"):
        if entry == "runner":
            run_case(ws, NAV_CASE, device_spec=world("motor_hm_base.json"), require_asset_review=True)
        else:
            write_json(ws.root / "suites/revocation-test.json", {"cases": [NAV_CASE]})
            args = build_parser().parse_args(["suite", "revocation-test", "--device", world("motor_hm_base.json")])
            commands.cmd_suite(ws, args)
    assert not ws.runs_dir.exists()


def test_single_diagnostic_is_informal_and_does_not_renew(verdict_ws):
    ws = verdict_ws
    approved(ws, status="regression")
    C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    before = ws.case_path(NAV_CASE).read_bytes()
    result = run_case(ws, NAV_CASE, device_spec=world("motor_hm_base.json"), propose=False)
    assert result["informal"] and result["asset_review"]["state"] == "revoked"
    assert not result["asset_review"]["valid"]
    assert ws.case_path(NAV_CASE).read_bytes() == before
    report = (ws.runs_dir / result["run_id"] / "report.md").read_text(encoding="utf-8")
    assert "审核已撤销" in report and BASIS in report
    assert "原批准记录" in report


def test_readiness_exposes_withdrawal_read_only(verdict_ws):
    ws = verdict_ws
    approved(ws)
    C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    before = ws.case_path(NAV_CASE).read_bytes()
    args = build_parser().parse_args(["case", "readiness", NAV_CASE])
    result = commands.cmd_case(ws, args)["results"][0]
    assert result["review"]["state"] == "revoked" and result["promotion"]["status"] == "blocked"
    assert result["review"]["revocation"]["basis"] == BASIS
    assert "撤销" in result["promotion"]["reason"]
    assert ws.case_path(NAV_CASE).read_bytes() == before


def test_restoring_old_approval_does_not_undo_later_withdrawal(verdict_ws):
    ws = verdict_ws
    old = approved(ws)
    case = C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    case["asset_review"] = old["asset_review"]
    info = C.review_status(ws, case)
    assert info["required"] and not info["valid"] and info["state"] == "revoked"
    assert info["revocation"]["basis"] == BASIS


@pytest.mark.parametrize("history", [None, {}, [None]])
def test_invalid_history_is_not_overwritten_on_withdrawal(verdict_ws, history):
    ws = verdict_ws
    case = approved(ws)
    case["history"] = history
    write_json(ws.case_path(NAV_CASE), case)
    before = ws.case_path(NAV_CASE).read_bytes()
    with pytest.raises(ValueError, match="history"):
        C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    assert ws.case_path(NAV_CASE).read_bytes() == before


def test_stale_approval_can_be_withdrawn_without_rebinding(verdict_ws):
    ws = verdict_ws
    case = approved(ws)
    case["asset_review"]["binding"]["engine_hash"] = "synthetic-old-engine"
    write_json(ws.case_path(NAV_CASE), case)
    revoked = C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    assert revoked["asset_review"]["binding"]["engine_hash"] == "synthetic-old-engine"
    assert C.review_status(ws, revoked)["state"] == "revoked"


@pytest.mark.parametrize("operation", ["promote", "record_replay", "approve"])
def test_inflight_update_cannot_overwrite_completed_withdrawal(verdict_ws, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout
    from threading import Event
    ws = verdict_ws
    approved(ws, status="draft")
    at_write, release, revoke_started = Event(), Event(), Event()
    original_write = C.write_json
    def paused_write(path, data):
        if not at_write.is_set():
            at_write.set()
            assert release.wait(5), "test did not release first writer"
        return original_write(path, data)
    monkeypatch.setattr(C, "write_json", paused_write)
    kn = KnowledgeStore(ws.root)
    run = {"run_id": "concurrent-fixture", "verdict": {"verdict": "pending"},
           "case": {"hash": "synthetic"}, "validation_level": "offline_replay", "device_spec": "replay:fixture"}
    def first():
        if operation == "promote":
            return C.promote(ws, NAV_CASE, "candidate", kn)
        if operation == "record_replay":
            return C.record_replay(ws, NAV_CASE, run)
        return C.approve(ws, NAV_CASE, kn, REVIEWER, "Concurrent synthetic review")
    def withdraw():
        revoke_started.set()
        return C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first)
        assert at_write.wait(5)
        withdrawal = pool.submit(withdraw)
        assert revoke_started.wait(5)
        try:
            with pytest.raises(FutureTimeout):
                withdrawal.result(timeout=0.15)
        finally:
            release.set()
        pending.result(timeout=5)
        withdrawal.result(timeout=5)
    case = ws.load_case(NAV_CASE)
    assert C.review_status(ws, case)["state"] == "revoked"
    assert any(h.get("event") == "asset_review_revoked" for h in case["history"])
    if operation == "record_replay":
        assert case["replays"][-1]["run_id"] == "concurrent-fixture"
    with pytest.raises(PermissionError, match="撤销"):
        C.preflight_batch(ws, [NAV_CASE])


def test_writable_workspace_does_not_require_writable_parent(verdict_ws, monkeypatch):
    from pathlib import Path
    ws = verdict_ws
    original_mkdir = Path.mkdir
    def constrained_mkdir(path, *args, **kwargs):
        if path == ws.root.parent / ".case-locks":
            raise PermissionError("synthetic parent is not writable")
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", constrained_mkdir)
    approved(ws)
    case = C.revoke_review(ws, NAV_CASE, REVIEWER, BASIS)
    assert C.review_status(ws, case)["state"] == "revoked"
    assert (ws.root / ".case-locks" / NAV_CASE / ".session.lock").is_file()
