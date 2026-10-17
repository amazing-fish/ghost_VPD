"""Synthetic #56 lifecycle evidence; no production Oracle or asset is confirmed."""
from copy import deepcopy

import pytest
from verdict_fixtures import COPY_RULE, NAV_CASE, NAV_RULE

from vpdtest import cases
from vpdtest.experience import ExperienceStore
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import sha256_obj, write_json


def _conditional(ws):
    case = ws.load_case(NAV_CASE)
    case["assertions"] = [
        {"id": "A1", "at": "S2", "type": "node_selected", "target": "tab_my", "oracle_refs": [NAV_RULE]},
        {"id": "A2", "at": "S2", "type": "text_absent", "text": "synthetic failure",
         "oracle_refs": [NAV_RULE], "guard": {"condition": "fault", "equals": True}},
    ]
    case["conditions"] = {"fault": {"kind": "page", "true": {"type": "text_present", "text": "FAULT"},
                                      "false": {"type": "text_present", "text": "HEALTHY"}}}
    return case


def _coverage(**results):
    assertions = [{"id": aid, "result": result,
                   "applicable": False if result == "not_applicable" else None if result in ("pending", "not_run") else True,
                   "verified": result in ("pass", "fail")}
                  for aid, result in results.items()]
    return {"assertions": assertions, "verified": [a["id"] for a in assertions if a["verified"]],
            "not_applicable": [a["id"] for a in assertions if a["applicable"] is False],
            "unverified": [a["id"] for a in assertions if not a["verified"] and a["applicable"] is not False]}


def _replays(case, *coverages):
    case = deepcopy(case)
    case["replays"] = [{"run_id": f"run-synthetic-{i}", "hash": cases.content_hash(case), "deps": "current",
                         "verdict": "passed", "coverage": coverage, "failure_signature": []}
                        for i, coverage in enumerate(coverages)]
    return case


def test_condition_content_is_optional_and_old_unconditional_hash_is_unchanged(verdict_ws):
    case = verdict_ws.load_case(NAV_CASE)
    body = {k: case.get(k) for k in cases.CONTENT_KEYS}
    body.update({k: case[k] for k in cases.OPTIONAL_CONTENT_KEYS if case.get(k)})
    assert cases.content_hash(case) == sha256_obj(body)[:12]
    new = _conditional(verdict_ws)
    changed = deepcopy(new)
    changed["conditions"]["fault"]["false"]["text"] = "OTHER_HEALTH"
    assert cases.content_hash(changed) != cases.content_hash(new)


def test_provider_targets_and_oracle_conditions_change_dependencies(verdict_ws):
    case, app = _conditional(verdict_ws), verdict_ws.app("motor_hm")
    app.targets["provider_indicator"] = deepcopy(app.targets["settings_entry"])
    case["conditions"]["fault"]["true"] = {"type": "target_present", "target": "provider_indicator"}
    kn = KnowledgeStore(verdict_ws.root)
    before = cases.dependency_hash(case, app, kn)
    app.targets["provider_indicator"]["strategies"][0]["value"] += "_CHANGED"
    assert cases.dependency_hash(case, app, kn) != before
    before = cases.dependency_hash(case, app, kn)
    items = {"items": kn.items()}
    next(i for i in items["items"] if i["id"] == NAV_RULE)["condition_requirements"] = {"fault": True}
    write_json(kn.path, items)
    assert cases.dependency_hash(case, app, KnowledgeStore(verdict_ws.root)) != before


def test_lint_checks_guard_provider_and_historical_step(verdict_ws):
    case = _conditional(verdict_ws)
    case["assertions"][1]["guard"] = {"condition": "missing", "equals": True, "at": "S404"}
    issues = cases.lint(case, verdict_ws.app("motor_hm"), KnowledgeStore(verdict_ws.root), verdict_ws.policy())
    text = str(issues)
    assert "missing" in text and "S404" in text


def test_lint_follows_conditional_substeps_in_existing_flow(verdict_ws):
    case, app = _conditional(verdict_ws), verdict_ws.app("motor_hm")
    app.flows["synthetic_branch"] = {"steps": [{"id": "F1", "action": "back",
        "when": {"condition": "missing_in_flow", "equals": True, "at": "S404"}}]}
    case["steps"].append({"id": "S5", "action": "flow", "flow": "synthetic_branch"})
    issues = cases.lint(case, app, KnowledgeStore(verdict_ws.root), verdict_ws.policy())
    assert any("synthetic_branch" in issue["field"] and "missing_in_flow" in issue["problem"] for issue in issues)


def test_lint_requires_providers_for_oracle_business_conditions(verdict_ws):
    case, kn = _conditional(verdict_ws), KnowledgeStore(verdict_ws.root)
    items = {"items": kn.items()}
    next(i for i in items["items"] if i["id"] == NAV_RULE)["condition_requirements"] = {"not_provided": True}
    write_json(kn.path, items)
    issues = cases.lint(case, verdict_ws.app("motor_hm"), KnowledgeStore(verdict_ws.root), verdict_ws.policy())
    assert any("not_provided" in issue["problem"] and "provider" in issue["problem"] for issue in issues)


def test_replays_record_branch_evidence_without_rewriting_run(verdict_ws):
    case = _conditional(verdict_ws)
    write_json(verdict_ws.case_path(case["id"]), case)
    coverage = _coverage(A1="pass", A2="not_applicable")
    run = {"run_id": "run-synthetic-one", "case": {"id": case["id"], "hash": cases.content_hash(case), "deps": "current"},
           "verdict": {"verdict": "passed", "coverage": coverage}, "validation_level": "offline_replay",
           "device_spec": "replay:synthetic"}
    original = deepcopy(run)
    saved = cases.record_replay(verdict_ws, case["id"], run)["replays"][-1]
    assert saved["coverage"] == coverage and run == original


def test_two_common_passes_do_not_verify_an_untriggered_conditional_branch(verdict_ws):
    case = _replays(_conditional(verdict_ws), _coverage(A1="pass", A2="not_applicable"),
                    _coverage(A1="pass", A2="not_applicable"))
    basis = cases.replay_basis(case, "current")
    assert basis["ok"] is False
    assert "A2" in basis["why"]


def test_each_conditional_assertion_requires_two_verified_consistent_runs(verdict_ws):
    case = _replays(_conditional(verdict_ws), _coverage(A1="pass", A2="not_applicable"),
                    _coverage(A1="pass", A2="pass"), _coverage(A1="pass", A2="pass"))
    basis = cases.replay_basis(case, "current")
    assert basis["ok"] is True
    assert basis["coverage"]["A2"]["count"] == 2
    case["replays"][-1]["coverage"] = _coverage(A1="pass", A2="not_run")
    assert cases.replay_basis(case, "current")["ok"] is False


def test_unchanged_aggregate_failure_signature_does_not_hide_an_unstable_assertion(verdict_ws):
    case = _replays(_conditional(verdict_ws), _coverage(A1="fail", A2="pass"), _coverage(A1="fail", A2="fail"))
    for row in case["replays"]:
        row.update(verdict="failed", failure_signature=["assertion:A1:synthetic"])
    basis = cases.replay_basis(case, "current")
    assert basis["ok"] is False and "A2" in basis["why"] and "不一致" in basis["why"]


def test_all_not_applicable_replays_cannot_mature_an_asset(verdict_ws):
    case = _replays(_conditional(verdict_ws), _coverage(A1="not_applicable", A2="not_applicable"),
                    _coverage(A1="not_applicable", A2="not_applicable"))
    for row in case["replays"]:
        row["verdict"] = "not_applicable"
    assert cases.replay_basis(case, "current")["ok"] is False


def test_conditional_asset_cannot_fall_back_to_coverage_free_aggregate_pass(verdict_ws):
    case = _replays(_conditional(verdict_ws), _coverage(A1="pass", A2="pass"), _coverage(A1="pass", A2="pass"))
    for replay in case["replays"]:
        replay.pop("coverage")
    assert cases.replay_basis(case, "current")["ok"] is False


def test_unconditional_historical_summaries_remain_compatible_with_matching_deps(verdict_ws):
    case = verdict_ws.load_case(NAV_CASE)
    case = _replays(case, {}, {})
    for replay in case["replays"]:
        replay.pop("coverage")
    assert cases.replay_basis(case, "current")["ok"] is True
    assert cases.replay_basis(case, "new-contract")["ok"] is False


def _promotable(ws, *, assertion_facts=True):
    case, kn = _conditional(ws), KnowledgeStore(ws.root)
    case["status"] = "replay_verified"
    case["assertions"][1]["oracle_refs"] = [COPY_RULE]
    for kid in (NAV_RULE, COPY_RULE):
        kn.confirm(kid, reviewer="pytest-synthetic", basis="isolated lifecycle contract only")
    write_json(ws.case_path(case["id"]), case)
    measured = {"app": "motor_hm", "app_version": "3.0.3.200"}
    for i in range(2):
        snapshots = [{"id": "A1", "result": "pass", "oracle": {"status": "confirmed"}, "facts": measured},
                     {"id": "A2", "result": "pass", "oracle": {"status": "confirmed"}}]
        if assertion_facts:
            snapshots[1]["facts"] = {"app": "motor_hm", "account_state": "logged_out"}
        run = {"run_id": f"run-synthetic-promote-{i}",
               "case": {"id": case["id"], "hash": cases.content_hash(case),
                        "deps": cases.dependency_hash(case, ws.app("motor_hm"), kn)},
               "verdict": {"verdict": "passed", "coverage": _coverage(A1="pass", A2="pass")},
               "validation_level": "offline_replay", "device_spec": "replay:synthetic",
               "facts": {**measured, "account_state": "logged_out"}, "oracle_facts": measured,
               "assertions": snapshots}
        cases.record_replay(ws, case["id"], run)
    return case, kn


def test_promote_reads_each_verified_assertions_own_oracle_context(verdict_ws):
    case, kn = _promotable(verdict_ws)
    result = cases.promote(verdict_ws, case["id"], "regression", kn, reviewer="pytest-synthetic", allow_offline=True)
    assert result["status"] == "regression"


def test_promote_never_restores_business_env_to_new_oracle_context(verdict_ws):
    case, kn = _promotable(verdict_ws, assertion_facts=False)
    with pytest.raises(PermissionError, match="A2.*判据未全部确认"):
        cases.promote(verdict_ws, case["id"], "regression", kn, reviewer="pytest-synthetic", allow_offline=True)


@pytest.mark.parametrize("earlier_contract", [1, 2, 3, None], ids=["early-1", "early-2", "early-3", "current"])
def test_promotion_requires_current_dependency_contract_despite_complete_branch_replays(
        verdict_ws, monkeypatch, earlier_contract):
    # Create two isolated historical summaries through the real recording path.
    # Only their engine-result contract differs; content, independent confirmed
    # rules, assertion facts and verified branch coverage remain complete.
    with monkeypatch.context() as historical_engine:
        if earlier_contract is not None:
            def historical_hash(payload):
                if isinstance(payload, dict) and "result_contract" in payload:
                    payload = {**payload, "result_contract": earlier_contract}
                return sha256_obj(payload)
            historical_engine.setattr(cases, "sha256_obj", historical_hash)
        case, kn = _promotable(verdict_ws)

    stored = verdict_ws.load_case(case["id"])
    assert len(stored["replays"]) == 2
    assert all(row["verdict"] == "passed" and row["coverage"]["verified"] == ["A1", "A2"]
               and all(a["verified"] for a in row["coverage"]["assertions"])
               for row in stored["replays"])
    original = verdict_ws.case_path(case["id"]).read_bytes()
    if earlier_contract is not None:
        with pytest.raises(PermissionError, match="依赖指纹|定义已变"):
            cases.promote(verdict_ws, case["id"], "regression", kn,
                          reviewer="pytest-synthetic", allow_offline=True)
        # Refusing stale evidence must preserve both historical summaries and
        # the asset's previous maturity instead of silently migrating them.
        assert verdict_ws.case_path(case["id"]).read_bytes() == original
    else:
        promoted = cases.promote(verdict_ws, case["id"], "regression", kn,
                                 reviewer="pytest-synthetic", allow_offline=True)
        assert promoted["status"] == "regression"
        assert promoted["history"][-1]["replays"] == [row["run_id"] for row in stored["replays"]]


def _experience(tmp_path):
    xs = ExperienceStore(tmp_path, "synthetic")
    xs.save_item({"id": "XP-condition", "kind": "locator_preference", "change": {"target": "target", "prefer_strategy_index": 1},
                  "status": "candidate", "requires_review": False, "origin_case": "origin", "evidence": [],
                  "validations": [], "history": []})
    return xs


@pytest.mark.parametrize("trial_result", ["not_applicable", "pending", "not_run", "error"])
def test_experience_cannot_improve_by_losing_a_verified_branch(tmp_path, trial_result):
    def run(cid, dev, overlay):
        trial = bool(overlay.trial)
        coverage = _coverage(A1="pass", A2=trial_result if trial else "pass")
        return {"run_id": f"{cid}-{trial}", "verdict": {"verdict": "passed", "coverage": coverage},
                "steps": [{"attempts": [{"locate": {"target": "target", "adapted": not trial}}]}]}
    result = _experience(tmp_path).validate("XP-condition", ["origin", "other"], ["synthetic"], run)
    assert result["status"] != "validated"
    assert not result["rows"][0].get("improved")
    assert result["rows"][0]["incomplete"] is True


def test_experience_comparable_verified_branches_can_validate(tmp_path):
    def run(cid, dev, overlay):
        return {"run_id": f"{cid}-{bool(overlay.trial)}", "verdict": {"verdict": "passed", "coverage": _coverage(A1="pass", A2="pass")},
                "steps": [{"attempts": [{"locate": {"target": "target", "adapted": not overlay.trial}}]}]}
    assert _experience(tmp_path).validate("XP-condition", ["origin", "other"], ["synthetic"], run)["status"] == "validated"
