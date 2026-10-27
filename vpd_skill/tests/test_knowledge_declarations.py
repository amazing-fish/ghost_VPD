"""Issue #45: author-declared applicability is metadata, never an Oracle decision."""
from copy import deepcopy

import pytest
from conftest import confirm, world
from verdict_fixtures import NAV_CASE, NAV_RULE, SETTINGS_RULE

from vpdtest import cases as C
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import write_json


def _case(ws, value="unknown"):
    case = ws.load_case(NAV_CASE)
    case["knowledge_used"] = [{"id": NAV_RULE, "use": "synthetic navigation contract",
                               "applicability": value, "rationale": "Isolated author declaration"}]
    write_json(ws.case_path(case["id"]), case)
    return case


def _lint(ws, case):
    return C.lint(case, ws.app(case["app"]), KnowledgeStore(ws.root), ws.policy(), runs_dir=ws.runs_dir)


@pytest.mark.parametrize("value", ["confirmed", "unconfirmed", "calibrated", "not_applicable", "适用",
                                   "", " applies", "unknown ", None, False, 1, [], {"status": "applies"}])
def test_invalid_declared_applicability_has_actionable_lint(verdict_ws, value):
    case = _case(verdict_ws, value)
    issues = _lint(verdict_ws, case)
    found = [i for i in issues if "knowledge_used" in i["field"] and "applicability" in i["field"]]
    assert len(found) == 1, issues
    assert all(v in found[0]["problem"] for v in ("applies", "unknown", "out_of_scope"))


@pytest.mark.parametrize("value", ["applies", "unknown", "out_of_scope"])
def test_canonical_declaration_can_be_reviewed_and_promoted_without_confirming_knowledge(verdict_ws, value):
    ws = verdict_ws
    case = _case(ws, value)
    kn = KnowledgeStore(ws.root)
    before = kn.path.read_bytes()
    assert _lint(ws, case) == []
    approved = C.approve(ws, case["id"], kn, "pytest-synthetic", "Only this isolated metadata fixture")
    assert approved["status"] == "draft"
    assert approved["knowledge_used"] == case["knowledge_used"]
    assert C.review_status(ws, approved)["valid"]
    assert C.promote(ws, case["id"], "candidate", kn)["status"] == "candidate"
    assert kn.path.read_bytes() == before


@pytest.mark.parametrize("entry", [{"id": NAV_RULE, "rationale": "No applicability declaration"},
                                   {"id": None, "note": "No usable knowledge"}])
def test_omitted_applicability_remains_optional(verdict_ws, entry):
    ws = verdict_ws
    case = _case(ws)
    case["knowledge_used"] = [deepcopy(entry)]
    write_json(ws.case_path(case["id"]), case)
    assert _lint(ws, case) == []
    approved = C.approve(ws, case["id"], KnowledgeStore(ws.root), "pytest-synthetic", "Optional declaration")
    assert approved["knowledge_used"] == [entry]


@pytest.mark.parametrize("operation", ["approve", "promote"])
@pytest.mark.parametrize("value", ["confirmed", None, {"status": "applies"}])
def test_invalid_declaration_cannot_be_signed_or_promoted_and_never_writes(verdict_ws, operation, value):
    ws = verdict_ws
    case = _case(ws, value)
    kn = KnowledgeStore(ws.root)
    before_case, before_knowledge = ws.case_path(case["id"]).read_bytes(), kn.path.read_bytes()
    with pytest.raises(PermissionError, match="applicability"):
        if operation == "approve":
            C.approve(ws, case["id"], kn, "pytest-synthetic", "Cannot validate invalid metadata")
        else:
            C.promote(ws, case["id"], "candidate", kn)
    assert ws.case_path(case["id"]).read_bytes() == before_case
    assert kn.path.read_bytes() == before_knowledge


@pytest.mark.parametrize("value", ["applies", "unknown", "out_of_scope"])
def test_declared_applicability_does_not_override_measured_oracle_scope(verdict_ws, value):
    ws = verdict_ws
    case = _case(ws, value)
    confirm(ws, NAV_RULE, SETTINGS_RULE)  # Only independent synthetic rules in this temporary workspace.
    run = run_case(ws, case["id"], device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "passed"
    assert run["knowledge_used"][0]["applicability"] == value
    decisive = [a for a in run["assertions"] if not a["advisory"]]
    assert decisive and all(a["oracle"]["status"] == "confirmed" for a in decisive)


def test_declaring_applies_never_confirms_an_unconfirmed_oracle(verdict_ws):
    ws = verdict_ws
    case = _case(ws, "applies")
    kn = KnowledgeStore(ws.root)
    before = kn.path.read_bytes()
    run = run_case(ws, case["id"], device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "pending"
    decisive = [a for a in run["assertions"] if not a["advisory"]]
    assert decisive and all(a["oracle"]["status"] == "unconfirmed" and a["result"] == "pending"
                           for a in decisive)
    assert kn.path.read_bytes() == before
