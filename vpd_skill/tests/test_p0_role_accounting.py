"""P0 asset role accounting, goal-driven suites, and readiness breakdown tests."""
import json
from pathlib import Path

from verdict_fixtures import NAV_CASE

from vpdtest import cases as C
from vpdtest.case_readiness import readiness
from vpdtest.cli import build_parser
from vpdtest.commands import cmd_case
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import write_json
from vpdtest.workspace import Workspace

PROD_DATA = Path(__file__).resolve().parents[1] / "data"


def test_production_case_roles_cover_all_cases_and_preserve_honest_counts():
    ws = Workspace(str(PROD_DATA), load_local_config=False)
    roles_cfg = json.loads((PROD_DATA / "case_roles.json").read_text(encoding="utf-8"))
    disk_ids = sorted(p.stem for p in (PROD_DATA / "cases").glob("*.json"))
    declared_ids = sorted(roles_cfg["cases"].keys())
    assert declared_ids == disk_ids

    out = readiness(ws)
    summary = out["summary"]
    assert summary["requested"] == 43
    assert summary["analysis_errors"] == 0
    assert summary["inspection_errors"] == 0
    assert summary["review"]["approved"] == 2
    assert summary["maturity"]["regression"] == 2

    by_role = summary["by_role"]
    assert by_role["framework_smoke"]["total"] == 10
    assert by_role["framework_smoke"]["business_coverage"] is False
    assert by_role["framework_smoke"]["approved_regression"] == 2

    assert by_role["capability_precondition"]["total"] == 10
    assert by_role["capability_precondition"]["business_coverage"] is False
    assert by_role["capability_precondition"]["approved_regression"] == 0

    assert by_role["business_vpd_core"]["total"] == 13
    assert by_role["business_vpd_core"]["business_coverage"] is True
    assert by_role["business_vpd_core"]["approved_regression"] == 0

    assert by_role["business_k89_map"]["total"] == 10
    assert by_role["business_k89_map"]["business_coverage"] is True
    assert by_role["business_k89_map"]["approved_regression"] == 0

    assert summary["business_regression"] == {
        "total_business_cases": 23,
        "approved_regression": 0,
    }


def test_readiness_cli_role_filter_and_suites_match_existing_cases():
    ws = Workspace(str(PROD_DATA), load_local_config=False)
    res = cmd_case(ws, build_parser().parse_args(["case", "readiness", "--role", "business_vpd_core"]))
    assert res["scope"]["selection"] == "role_filter"
    assert res["scope"]["role"] == "business_vpd_core"
    assert res["summary"]["requested"] == 13
    assert all(r["asset_role"] == "business_vpd_core" for r in res["results"])
    assert any(r["case"] == "TC-QK-230-SPOT-NAME" and r.get("baseline_refs") == ["OCT-RT-02"] for r in res["results"])

    disk_ids = {p.stem for p in (PROD_DATA / "cases").glob("*.json")}
    for suite_file in sorted((PROD_DATA / "suites").glob("*.json")):
        suite = ws.load_suite(suite_file.stem)
        suite_ids = [c["id"] if isinstance(c, dict) else c for c in suite["cases"]]
        assert suite_ids, f"empty suite: {suite_file.name}"
        assert set(suite_ids) <= disk_ids, f"unknown case in suite {suite_file.name}: {set(suite_ids) - disk_ids}"


def test_synthetic_business_regression_counts_only_valid_business_role(verdict_ws):
    ws = verdict_ws
    base_out = readiness(ws)
    assert all(r["asset_role"] == "unclassified" for r in base_out["results"] if r["source"] == "present")
    assert base_out["summary"]["business_regression"] == {"total_business_cases": 0, "approved_regression": 0}

    case = ws.load_case(NAV_CASE)
    case["status"] = "regression"
    case["origin"] = {"type": "session"}
    write_json(ws.case_path(NAV_CASE), case)
    write_json(ws.root / "case_roles.json", {
        "schema": 1,
        "roles": {
            "business_vpd_core": {"track": "business", "business_coverage": True}
        },
        "cases": {
            NAV_CASE: {"role": "business_vpd_core"}
        },
    })
    kn = KnowledgeStore(ws.root)
    C.approve(ws, NAV_CASE, kn, "pytest", "synthetic business approval")
    updated = readiness(ws)
    nav_row = next(r for r in updated["results"] if r["case"] == NAV_CASE)
    assert nav_row["asset_role"] == "business_vpd_core"
    assert updated["summary"]["business_regression"] == {
        "total_business_cases": 1,
        "approved_regression": 1,
    }
