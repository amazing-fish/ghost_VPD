"""Read-only visibility over existing maturity/review/promotion contracts; synthetic assets."""
from copy import deepcopy
import hashlib
import json

import pytest
from verdict_fixtures import NAV_CASE, NAV_RULE, SETTINGS_RULE

from vpdtest import cases as C
from vpdtest.cli import build_parser
from vpdtest.commands import cmd_case
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import write_json


def _query(ws, *args):
    return cmd_case(ws, build_parser().parse_args(["case", "readiness", *args]))


def _files(ws):
    return {str(p.relative_to(ws.root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in ws.root.rglob("*") if p.is_file()}


def test_cli_reports_requested_missing_source_without_inventing_a_maturity(verdict_ws):
    result = _query(verdict_ws, NAV_CASE, "TC-TEST-MISSING")
    assert [r["case"] for r in result["results"]] == [NAV_CASE, "TC-TEST-MISSING"]
    assert result["results"][1]["source"] == "missing"
    assert result["results"][1]["status"] is None
    assert result["summary"]["source"]["missing"] == 1
    assert result["scope"]["runtime_applicability"] == "not_checked"
    assert result["scope"]["coverage"] == "not_assessed"


def test_promotion_preview_is_read_only_and_matches_existing_candidate_gate(verdict_ws):
    ws = verdict_ws
    before = _files(ws)
    result = C.promote(ws, NAV_CASE, "candidate", KnowledgeStore(ws.root), dry_run=True)
    assert result["from"] == "draft" and result["to"] == "candidate"
    assert _files(ws) == before
    actual = C.promote(ws, NAV_CASE, "candidate", KnowledgeStore(ws.root))
    assert actual["status"] == result["to"]


def test_unexpected_inspection_fault_keeps_trace_later_rows_and_cli_error(verdict_ws, monkeypatch, capsys):
    from vpdtest.cli import main
    ws = verdict_ws
    case = ws.load_case(NAV_CASE)
    case["id"] = "TC-INSPECTION-FAULT"
    write_json(ws.case_path(case["id"]), case)
    original = C.review_status
    def inspect(workspace, asset, *args, **kwargs):
        if asset["id"] == case["id"]:
            raise RuntimeError("synthetic inspection fault")
        return original(workspace, asset, *args, **kwargs)
    monkeypatch.setattr(C, "review_status", inspect)
    rc = main(["--workspace", str(ws.root), "case", "readiness", case["id"], NAV_CASE])
    result = json.loads(capsys.readouterr().out)
    assert rc == 3 and result["summary"]["inspection_errors"] == 1
    assert result["results"][0]["error"]["kind"] == "inspection_error"
    assert "RuntimeError" in result["results"][0]["error"]["trace"]
    assert result["results"][1]["promotion"]["status"] == "eligible"


def test_default_scan_counts_actual_files_and_does_not_guess_missing_ids(verdict_ws):
    result = _query(verdict_ws)
    expected = sorted(p.stem for p in (verdict_ws.root / "cases").glob("*.json"))
    assert [r["case"] for r in result["results"]] == expected
    assert result["summary"]["requested"] == len(expected)
    assert result["summary"]["source"].get("missing", 0) == 0
    assert result["scope"]["selection"] == "directory"
    assert result["scope"]["missing_sources"] == "only_explicitly_requested_ids"


def test_review_pending_stale_and_binding_dimensions_remain_distinct(verdict_ws):
    ws = verdict_ws
    case = ws.load_case(NAV_CASE)
    case["origin"] = {"type": "session"}
    write_json(ws.case_path(NAV_CASE), case)
    pending = _query(ws, NAV_CASE)["results"][0]
    assert pending["review"]["state"] == "pending"
    assert pending["promotion"]["status"] == "blocked"
    assert "尚无有效人工审核" in pending["promotion"]["reason"]
    C.approve(ws, NAV_CASE, KnowledgeStore(ws.root), "pytest-synthetic", "Synthetic fixture only")
    assert _query(ws, NAV_CASE)["results"][0]["review"]["state"] == "approved"
    case = ws.load_case(NAV_CASE)
    case["asset_review"]["binding"]["engine_hash"] = "synthetic-old-engine"
    write_json(ws.case_path(NAV_CASE), case)
    stale = _query(ws, NAV_CASE)["results"][0]
    assert stale["review"]["state"] == "stale"
    assert stale["review"]["changed_binding_fields"] == ["engine_hash"]
    assert "审核失效" in stale["promotion"]["reason"]
    assert "policy.py" not in json.dumps(stale["review"])


@pytest.mark.parametrize("binding", [None, {"unexpected": "not a file-level snapshot"}])
def test_old_incomplete_binding_is_not_explained_as_a_specific_file_change(verdict_ws, binding):
    ws = verdict_ws
    case = ws.load_case(NAV_CASE)
    case["origin"] = {"type": "session"}
    write_json(ws.case_path(NAV_CASE), case)
    C.approve(ws, NAV_CASE, KnowledgeStore(ws.root), "pytest-synthetic", "Synthetic only")
    case = ws.load_case(NAV_CASE)
    case["asset_review"]["binding"] = binding
    write_json(ws.case_path(NAV_CASE), case)
    row = _query(ws, NAV_CASE)["results"][0]
    assert row["review"]["state"] == "stale"
    assert row["review"]["binding_available"] is isinstance(binding, dict)
    assert "file_changes" not in row["review"]


def test_runtime_oracle_scope_is_not_inferred_from_stored_confirmation(verdict_ws):
    row = _query(verdict_ws, NAV_CASE)["results"][0]
    assert row["oracle_references"]
    assert all(r["stored_status"] == "unconfirmed" for r in row["oracle_references"])
    # Candidate promotion currently permits active unconfirmed rules; do not invent a new gate.
    assert row["promotion"]["status"] == "eligible"
    assert row["scope"]["runtime_applicability"] == "not_checked"


def test_case_review_does_not_become_a_replay_or_product_conclusion(verdict_ws):
    ws = verdict_ws
    case = ws.load_case(NAV_CASE)
    case.update(status="candidate", product_status={"status": "known_failure", "issue": "synthetic"})
    case["replays"] = []
    write_json(ws.case_path(NAV_CASE), case)
    row = _query(ws, NAV_CASE)["results"][0]
    assert row["status"] == "candidate"
    assert row["recorded_product_status"]["status"] == "known_failure"
    assert row["replay"]["ok"] is False
    assert row["promotion"]["to"] == "replay_verified" and row["promotion"]["status"] == "blocked"
    assert "重放不足" in row["promotion"]["reason"]


def test_broken_json_and_unknown_app_are_reported_without_hiding_healthy_rows(verdict_ws):
    ws = verdict_ws
    ws.case_path("TC-BROKEN").write_text("{", encoding="utf-8")
    write_json(ws.case_path("TC-NO-APP"), {"id": "TC-NO-APP", "app": "synthetic_missing"})
    result = _query(ws, "TC-BROKEN", NAV_CASE, "TC-NO-APP")
    assert len(result["results"]) == 3 and result["incomplete"] is True
    assert result["results"][1]["promotion"]["status"] == "eligible"
    assert result["summary"]["analysis_errors"] == 2


def test_query_never_writes_or_connects_a_device(verdict_ws, monkeypatch):
    ws = verdict_ws
    before = _files(ws)
    def forbidden(*args, **kwargs):
        raise AssertionError("readiness must not write or connect")
    monkeypatch.setattr(ws, "device", forbidden)
    monkeypatch.setattr(C, "write_json", forbidden)
    monkeypatch.setattr(C, "approve", forbidden)
    result = _query(ws, NAV_CASE)
    assert result["results"][0]["promotion"]["status"] == "eligible"
    assert _files(ws) == before and not ws.runs_dir.exists()


def test_preview_does_not_mutate_a_cached_case_object(verdict_ws, monkeypatch):
    ws = verdict_ws
    cached = ws.load_case(NAV_CASE)
    before = deepcopy(cached)
    monkeypatch.setattr(ws, "load_case", lambda _: cached)
    C.promote(ws, NAV_CASE, "candidate", KnowledgeStore(ws.root), dry_run=True)
    assert cached == before


@pytest.mark.parametrize("cid", ["../policy", "..\\policy", "/absolute", "C:other"])
def test_query_rejects_paths_instead_of_case_ids(verdict_ws, cid):
    with pytest.raises(ValueError, match="ID"):
        _query(verdict_ws, cid)


def test_no_automatic_step_past_regression(verdict_ws):
    case = verdict_ws.load_case(NAV_CASE)
    case["status"] = "regression"
    write_json(verdict_ws.case_path(NAV_CASE), case)
    row = _query(verdict_ws, NAV_CASE)["results"][0]
    assert row["promotion"]["status"] == "no_next_stage"


def _replay_case(ws, to, verdict="passed", level="offline_replay", confirmed=True):
    kn = KnowledgeStore(ws.root)
    if confirmed:
        for kid in (NAV_RULE, SETTINGS_RULE):
            kn.confirm(kid, reviewer="pytest-synthetic", basis="Synthetic promotion-gate fixture only")
    case = ws.load_case(NAV_CASE)
    case["status"] = "candidate" if to == "replay_verified" else "replay_verified"
    h, deps = C.content_hash(case), C.dependency_hash(case, ws.app(case["app"]), kn)
    case["replays"] = [{"run_id": f"synthetic-replay-{i}", "verdict": verdict, "hash": h,
        "deps": deps, "validation_level": level, "ts": f"2026-01-0{i}T00:00:00+00:00",
        "facts": {"app": "motor_hm", "app_version": "3.0.3.200"},
        "failure_signature": ["A1"] if verdict == "failed" else []} for i in (1, 2)]
    write_json(ws.case_path(NAV_CASE), case)
    return kn


@pytest.mark.parametrize("to,verdict,level,confirmed,options,allowed", [
    ("replay_verified", "passed", "offline_replay", True, {}, True),
    ("replay_verified", "failed", "offline_replay", True, {}, False),
    ("replay_verified", "failed", "offline_replay", True, {"known_issue": "SYNTHETIC-1"}, True),
    ("replay_verified", "pending", "offline_replay", True, {}, False),
    ("regression", "passed", "offline_replay", True, {}, False),
    ("regression", "passed", "offline_replay", True, {"reviewer": "pytest-synthetic"}, False),
    ("regression", "passed", "offline_replay", True, {"reviewer": "pytest-synthetic", "allow_offline": True}, True),
    ("regression", "failed", "offline_replay", True, {"reviewer": "pytest-synthetic", "allow_offline": True}, False),
    ("regression", "failed", "offline_replay", True, {"reviewer": "pytest-synthetic", "allow_offline": True,
                                                   "known_issue": "SYNTHETIC-1"}, True),
    ("regression", "passed", "real_device", True, {"reviewer": "pytest-synthetic"}, True),
    ("regression", "passed", "real_device", False, {"reviewer": "pytest-synthetic"}, False),
])
def test_cli_preview_and_actual_promotion_share_every_gate(
        verdict_ws, to, verdict, level, confirmed, options, allowed):
    ws = verdict_ws
    kn = _replay_case(ws, to, verdict, level, confirmed)
    before = _files(ws)
    flags = [NAV_CASE, "--to", to]
    for key, value in options.items():
        flags += ["--" + key.replace("_", "-")] + ([] if value is True else [value])
    row = _query(ws, *flags)["results"][0]
    assert row["promotion"]["status"] == ("eligible" if allowed else "blocked"), row
    assert _files(ws) == before
    if allowed:
        actual = C.promote(ws, NAV_CASE, to, kn, **options)
        assert actual["status"] == to
        assert actual.get("product_status") == row["promotion"]["preview"].get("product_status")
    else:
        with pytest.raises(PermissionError) as exc:
            C.promote(ws, NAV_CASE, to, kn, **options)
        assert str(exc.value) == row["promotion"]["reason"]
        assert _files(ws) == before


def test_preview_does_not_set_product_status_on_cached_case(verdict_ws, monkeypatch):
    ws = verdict_ws
    kn = _replay_case(ws, "replay_verified")
    cached = ws.load_case(NAV_CASE)
    before = deepcopy(cached)
    monkeypatch.setattr(ws, "load_case", lambda _: cached)
    preview = C.promote(ws, NAV_CASE, "replay_verified", kn, dry_run=True)
    assert preview["product_status"]["status"] == "conforming"
    assert cached == before and "product_status" not in cached


def test_extra_binding_field_remains_stale_even_with_all_hashes_matching(verdict_ws):
    ws = verdict_ws
    case = ws.load_case(NAV_CASE)
    case["origin"] = {"type": "session"}
    write_json(ws.case_path(NAV_CASE), case)
    C.approve(ws, NAV_CASE, KnowledgeStore(ws.root), "pytest-synthetic", "Synthetic fixture only")
    case = ws.load_case(NAV_CASE)
    case["asset_review"]["binding"]["synthetic_extra"] = "unexpected"
    write_json(ws.case_path(NAV_CASE), case)
    row = _query(ws, NAV_CASE)["results"][0]
    assert row["review"]["state"] == "stale"
    assert row["review"]["changed_binding_fields"] == []
    assert row["review"]["unexpected_binding_field_count"] == 1
    assert row["promotion"]["status"] == "blocked"


@pytest.mark.parametrize("field,value", [
    ("assertions", [None]), ("steps", [None]), ("cleanup", [False]),
    ("replays", [None]), ("knowledge_used", ["bad"]), ("sources", [None]),
    ("history", [False]), ("test_data", []), ("safety", []),
])
def test_malformed_nested_source_does_not_suppress_later_healthy_assets(verdict_ws, field, value):
    ws = verdict_ws
    case = ws.load_case(NAV_CASE)
    case.update(id="TC-NESTED-BROKEN")
    case[field] = value
    write_json(ws.case_path(case["id"]), case)
    before = _files(ws)
    result = _query(ws, case["id"], NAV_CASE)
    assert result["incomplete"] is True and result["summary"]["analysis_errors"] == 1
    bad, good = result["results"]
    assert bad["error"]["stage"] == "source"
    assert field in bad["error"]["message"]
    assert good["promotion"]["status"] == "eligible"
    assert _files(ws) == before
