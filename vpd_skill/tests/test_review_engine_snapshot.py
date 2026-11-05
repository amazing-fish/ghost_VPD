"""Engine change explanations use synthetic reviews, never production approval."""
from copy import deepcopy
import hashlib

import pytest
from verdict_fixtures import NAV_CASE

from vpdtest import cases as C
from vpdtest.cli import build_parser
from vpdtest.commands import cmd_case
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import write_json


def _asset(ws):
    case = ws.load_case(NAV_CASE)
    case["origin"] = {"type": "session"}
    write_json(ws.case_path(NAV_CASE), case)
    return case


def _approve(ws):
    return C.approve(ws, NAV_CASE, KnowledgeStore(ws.root),
                     "pytest-synthetic", "synthetic engine diagnostic contract only")


def test_future_approval_keeps_engine_files_bound_to_existing_hash(verdict_ws):
    _asset(verdict_ws)
    approved = _approve(verdict_ws)
    record = approved["asset_review"]
    snapshot = record["engine_snapshot"]
    assert snapshot["schema"] == 1
    assert C.sha256_obj(snapshot["files"]) == record["binding"]["engine_hash"]
    assert C.review_status(verdict_ws, approved)["valid"] is True


def test_legacy_review_remains_valid_without_inventing_file_changes(verdict_ws):
    _asset(verdict_ws)
    approved = _approve(verdict_ws)
    approved["asset_review"].pop("engine_snapshot", None)
    info = C.review_status(verdict_ws, approved)
    assert info["valid"] is True and info["state"] == "approved"
    assert info["engine_changes"]["available"] is False
    assert info["engine_changes"]["reason"] == "snapshot_missing"
    assert info["engine_changes"]["modified"] is None


def test_one_capture_binds_new_review_and_preserves_provider_copy(verdict_ws, monkeypatch):
    before = _asset(verdict_ws)
    old = {"execute.py": "a" * 64, "same.py": "b" * 64}
    calls = []

    def capture(directory):
        calls.append(directory)
        return old

    monkeypatch.setattr(C.review_snapshot, "engine_files", capture)
    kn_before = (verdict_ws.root / "knowledge/items.json").read_bytes()
    approved = _approve(verdict_ws)
    record = approved["asset_review"]
    assert len(calls) == 1
    assert record["engine_snapshot"]["files"] == old
    assert record["binding"]["engine_hash"] == C.sha256_obj(old)
    assert approved["history"][-1]["engine_snapshot"] == record["engine_snapshot"]
    assert C.content_hash(before) == C.content_hash(approved)
    assert before["status"] == approved["status"] == "draft"
    assert (verdict_ws.root / "knowledge/items.json").read_bytes() == kn_before
    old["later.py"] = "c" * 64
    assert "later.py" not in record["engine_snapshot"]["files"]


def test_added_removed_modified_engine_files_explain_stale_without_weakening_gate(verdict_ws, monkeypatch):
    _asset(verdict_ws)
    old = {"z.py": "a" * 64, "sub/removed.py": "b" * 64, "same.py": "c" * 64}
    monkeypatch.setattr(C.review_snapshot, "engine_files", lambda _: old)
    approved = _approve(verdict_ws)
    current = {"z.py": "d" * 64, "new.py": "e" * 64, "same.py": "c" * 64}
    monkeypatch.setattr(C.review_snapshot, "engine_files", lambda _: current)
    info = C.review_status(verdict_ws, approved)
    assert info["state"] == "stale" and info["valid"] is False
    assert info["engine_changes"] == {"available": True, "reason": None, "added": ["new.py"],
                                      "removed": ["sub/removed.py"], "modified": ["z.py"], "unchanged_count": 1}
    assert "engine_snapshot" not in info["record"]
    before = verdict_ws.case_path(NAV_CASE).read_bytes()
    with pytest.raises(PermissionError, match="审核失效"):
        C.require_review(verdict_ws, approved)
    with pytest.raises(PermissionError, match="审核失效"):
        C.promote(verdict_ws, NAV_CASE, "candidate", KnowledgeStore(verdict_ws.root), dry_run=True)
    assert verdict_ws.case_path(NAV_CASE).read_bytes() == before


def test_unchanged_engine_is_explained_without_invalidating_review(verdict_ws):
    _asset(verdict_ws)
    approved = _approve(verdict_ws)
    info = C.review_status(verdict_ws, approved)
    assert info["valid"] is True
    changes = info["engine_changes"]
    assert changes["available"] is True and changes["reason"] is None
    assert changes["added"] == changes["removed"] == changes["modified"] == []
    assert changes["unchanged_count"] == len(approved["asset_review"]["engine_snapshot"]["files"])


@pytest.mark.parametrize("snapshot,reason", [
    (None, "snapshot_invalid"),
    ([], "snapshot_invalid"),
    ({"schema": True, "files": {}}, "snapshot_schema_unsupported"),
    ({"schema": 2, "files": {}}, "snapshot_schema_unsupported"),
    ({"schema": 1, "files": []}, "snapshot_manifest_invalid"),
    ({"schema": 1, "files": {"valid.py": "bad-hash"}}, "snapshot_manifest_invalid"),
    ({"schema": 1, "files": {"valid.py": None}}, "snapshot_manifest_invalid"),
    ({"schema": 1, "files": {"valid.py": "f" * 64}}, "snapshot_binding_mismatch"),
])
def test_corrupt_diagnostic_is_unavailable_and_does_not_change_original_validity(verdict_ws, snapshot, reason):
    _asset(verdict_ws)
    approved = _approve(verdict_ws)
    approved["asset_review"]["engine_snapshot"] = deepcopy(snapshot)
    info = C.review_status(verdict_ws, approved)
    assert info["valid"] is True and info["state"] == "approved"
    assert info["engine_changes"]["available"] is False and info["engine_changes"]["reason"] == reason
    assert info["engine_changes"]["added"] is None
    assert info["engine_changes"]["removed"] is None
    assert info["engine_changes"]["modified"] is None
    assert C.require_review(verdict_ws, approved)["valid"] is True


@pytest.mark.parametrize("path", ["../outside.py", "/absolute.py", "C:/outside.py", "sub\\outside.py",
                                 "sub//bad.py", "sub/./bad.py", "sub/../bad.py", "bad\nname.py"])
def test_untrusted_snapshot_names_never_become_read_paths(monkeypatch, path):
    from pathlib import Path

    def forbidden(*args, **kwargs):
        pytest.fail("snapshot names must never cause filesystem reads")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    files = {path: "a" * 64}
    record = {"binding": {"engine_hash": C.sha256_obj(files)},
              "engine_snapshot": {"schema": 1, "files": files}}
    info = C.review_snapshot.engine_changes(record, {"safe.py": "b" * 64})
    assert info["available"] is False and info["reason"] == "snapshot_manifest_invalid"


def test_legacy_engine_hash_change_has_no_guessed_file_details(verdict_ws, monkeypatch):
    _asset(verdict_ws)
    monkeypatch.setattr(C.review_snapshot, "engine_files", lambda _: {"old.py": "a" * 64})
    approved = _approve(verdict_ws)
    approved["asset_review"].pop("engine_snapshot")
    monkeypatch.setattr(C.review_snapshot, "engine_files", lambda _: {"new.py": "b" * 64})
    info = C.review_status(verdict_ws, approved)
    assert info["state"] == "stale" and info["valid"] is False
    assert info["engine_changes"]["available"] is False
    assert info["engine_changes"]["reason"] == "snapshot_missing"
    assert info["engine_changes"]["added"] is None


@pytest.mark.parametrize("binding", [None, {}, {"engine_hash": None}])
def test_snapshot_without_old_binding_is_not_attributed_to_current_engine(binding):
    files = {"old.py": "a" * 64}
    info = C.review_snapshot.engine_changes({"binding": binding,
        "engine_snapshot": {"schema": 1, "files": files}}, {"new.py": "b" * 64})
    assert info["available"] is False and info["reason"] == "snapshot_binding_missing"
    assert info["modified"] is None


def test_other_review_changes_are_not_misattributed_to_engine_files(verdict_ws):
    _asset(verdict_ws)
    approved = _approve(verdict_ws)
    approved["purpose"] = "changed synthetic review purpose"
    info = C.review_status(verdict_ws, approved)
    assert info["state"] == "stale" and info["valid"] is False
    assert info["current"]["review_hash"] != approved["asset_review"]["binding"]["review_hash"]
    assert info["engine_changes"]["available"] is True
    assert info["engine_changes"]["modified"] == []


def test_readiness_exposes_compact_changes_without_any_approval_or_device_action(verdict_ws, monkeypatch):
    _asset(verdict_ws)
    monkeypatch.setattr(C.review_snapshot, "engine_files", lambda _: {"dispatch.py": "a" * 64})
    _approve(verdict_ws)
    monkeypatch.setattr(C.review_snapshot, "engine_files", lambda _: {"dispatch.py": "b" * 64})

    def forbidden(*args, **kwargs):
        pytest.fail("readiness must not approve or construct a device")

    monkeypatch.setattr(C, "approve", forbidden)
    monkeypatch.setattr(verdict_ws, "device", forbidden)
    def files():
        return {str(p.relative_to(verdict_ws.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in verdict_ws.root.rglob("*") if p.is_file()}
    before = files()
    result = cmd_case(verdict_ws, build_parser().parse_args(["case", "readiness", NAV_CASE]))
    row = result["results"][0]
    assert row["review"]["changed_binding_fields"] == ["engine_hash"]
    assert row["review"]["engine_changes"]["modified"] == ["dispatch.py"]
    assert "engine_snapshot" not in row["review"]
    assert row["promotion"]["status"] == "blocked"
    assert before == files()
