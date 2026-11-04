"""知识状态完整性与 Issue #33：退役不可复活，旧判据不得支撑结论或晋级。"""
import copy

import pytest

from verdict_fixtures import NAV_CASE

from vpdtest import cases as C
from vpdtest.knowledge import KnowledgeStore
from vpdtest.search import search
from vpdtest.util import read_json, write_json
from vpdtest.verdict import evaluate_assertion


def _rule(kn, kid="K-RETIRE-TEST"):
    kn.add({"id": kid, "kind": "product_rule", "subject": kid, "statement": "独立测试规则",
            "expected": {"present": True}, "source": {"type": "reviewer", "ref": "pytest"}})
    return kid


def _state(kn, kid, state, missing=False):
    data = read_json(kn.path)
    item = next(it for it in data["items"] if it["id"] == kid)
    if missing:
        item.pop("status")
    else:
        item["status"] = state
    write_json(kn.path, data)


def _case(ws, kid):
    case = ws.load_case(NAV_CASE)
    case.update(id="TC-RETIRE-TEST", status="candidate")
    for assertion in case["assertions"]:
        if not assertion.get("advisory"):
            assertion["oracle_refs"] = [kid]
    return case


@pytest.mark.parametrize("state", ["superseded", "", None, 7, [], {}])
def test_unknown_knowledge_status_never_confirms(verdict_ws, state):
    kn = KnowledgeStore(verdict_ws.root)
    kid, good = _rule(kn), _rule(kn, "K-RETIRE-GOOD")
    kn.confirm(good, reviewer="pytest")
    assert kn.oracle_status([good])["status"] == "confirmed"
    _state(kn, kid, state)
    for refs in ([kid], [good, kid], [kid, good]):
        oracle = kn.oracle_status(refs)
        assert oracle["status"] == "invalid"
        assert "状态" in oracle["reason"]
        bad = next(it for it in oracle["items"] if it["id"] == kid)
        assert bad["status"] == "invalid" and bad["stored_status"] == state


def test_missing_knowledge_status_is_invalid(verdict_ws):
    kn = KnowledgeStore(verdict_ws.root)
    kid = _rule(kn)
    _state(kn, kid, None, missing=True)
    assert kn.oracle_status([kid])["status"] == "invalid"


@pytest.mark.parametrize("state", ["deprecated", "superseded", None, [], {}])
def test_confirm_refuses_retired_or_invalid_knowledge_without_writing(verdict_ws, state):
    kn = KnowledgeStore(verdict_ws.root)
    kid = _rule(kn)
    _state(kn, kid, state)
    before = kn.path.read_bytes()
    with pytest.raises(PermissionError, match="状态"):
        kn.confirm(kid, reviewer="pytest", basis="不可作为复活依据")
    assert kn.path.read_bytes() == before


@pytest.mark.parametrize("state", ["deprecated", "superseded", None])
def test_inactive_knowledge_is_searchable_only_as_history(verdict_ws, state):
    kn = KnowledgeStore(verdict_ws.root)
    kid = _rule(kn)
    _state(kn, kid, state)
    result = search(verdict_ws, kid)
    row = next(r for rows in result["results"].values() for r in rows if r["ref"] == kid)
    assert row["role"] == "background" and row["callable"] is None
    assert row["can_be_oracle"] is False and row["note"]
    assert kid not in result["usable_oracles"]


@pytest.mark.parametrize("state", ["deprecated", "superseded"])
def test_inactive_oracle_cannot_turn_observed_success_into_pass(verdict_ws, state):
    kn = KnowledgeStore(verdict_ws.root)
    kid = _rule(kn)
    _state(kn, kid, state)
    assertion = {"id": "A1", "type": "value_present", "at": "S1", "oracle_refs": [kid]}
    result = evaluate_assertion(assertion, {"S1": {"outcome": "passed", "measurement": {"value": 1}}},
                                lambda _: None, kn, verdict_ws.app("motor_hm"))
    assert result["observed_holds"] is True and result["result"] == "pending"
    assert result["oracle"]["status"] == ("missing" if state == "deprecated" else "invalid")


def test_confirmation_and_history_do_not_invalidate_active_dependencies(verdict_ws):
    kn = KnowledgeStore(verdict_ws.root)
    kid = _rule(kn)
    case, app = _case(verdict_ws, kid), verdict_ws.app("motor_hm")
    before = C.dependency_hash(case, app, kn)
    kn.confirm(kid, reviewer="pytest")
    assert C.dependency_hash(case, app, kn) == before
    kn.confirm(kid, reviewer="pytest", basis="重复确认同一判据")
    assert C.dependency_hash(case, app, kn) == before


@pytest.mark.parametrize("state", ["deprecated", "superseded"])
def test_retirement_invalidates_replays_and_refuses_promotion(verdict_ws, state):
    kn = KnowledgeStore(verdict_ws.root)
    kid = _rule(kn)
    kn.confirm(kid, reviewer="pytest")
    case, app = _case(verdict_ws, kid), verdict_ws.app("motor_hm")
    assert not C.lint(case, app, kn, verdict_ws.policy(), runs_dir=verdict_ws.runs_dir)
    deps = C.dependency_hash(case, app, kn)
    case["replays"] = [{"run_id": f"run-retirement-{n}", "verdict": "passed", "hash": C.content_hash(case),
                        "deps": deps, "validation_level": "offline_replay"} for n in (1, 2)]
    assert C.replay_basis(case, deps)["ok"] is True
    _state(kn, kid, state)
    current = C.dependency_hash(case, app, kn)
    assert current != deps
    assert C.replay_basis(case, current)["ok"] is False
    issues = C.lint(case, app, kn)
    assert any(kid in issue["problem"] and "状态" in issue["problem"] for issue in issues)
    write_json(verdict_ws.case_path(case["id"]), case)
    with pytest.raises(PermissionError, match="用例不完整"):
        C.promote(verdict_ws, case["id"], "replay_verified", kn)
    assert verdict_ws.load_case(case["id"])["status"] == "candidate"


def test_issue33_retirement_keeps_history_and_debug_scope(ws):
    kn = KnowledgeStore(ws.root)
    old, kept = kn.get("K-QK-001"), kn.get("K-AV-001")
    assert old["status"] == "deprecated" and old["superseded_by"] == "K-AV-001"
    assert old["history"][-1]["to"] == "deprecated" and old["history"][-1]["basis"].endswith("/issues/33")
    assert old["applies_to"] == {"app": "qiankun_hm"}
    assert kept["status"] == "unconfirmed"
    assert kept["applies_to"] == {"app": "qiankun_hm", "app_version": "内部debug版 phone_fc78649"}
    assert "qiankun_hm.vpd.entry_success" not in kn.conflicts()
    assert kn.oracle_status([old["id"]])["status"] == "missing"
    case = ws.load_case("TC-QK-201")
    entry = next(row for row in case["knowledge_used"] if row["id"] == "K-AV-001")
    assert entry["applicability"] == "not_applicable" and "2026-10-13" in entry["rationale"]
    historical = copy.deepcopy(case)
    prior = next(row for row in historical["knowledge_used"] if row["id"] == "K-AV-001")
    prior.update(id="K-QK-001 / K-AV-001", rationale="历史冲突说明")
    app = ws.app(case["app"])
    assert C.content_hash(historical) == C.content_hash(case)
    assert C.dependency_hash(historical, app, kn) == C.dependency_hash(case, app, kn)
