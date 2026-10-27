"""Base already omitted condition-only hypothesis targets from asset eligibility.

Synthetic lint/promotion regressions; no production calibration or human approval.
"""
from copy import deepcopy

import pytest
from test_condition_runner import APP, COMMON_RULE, FAULT_RULE, condition_rig
from vpdtest import cases
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import read_json, write_json

__all__ = ("condition_rig",)


def _asset(rig, consumer, source):
    ws, case, _ = rig
    targets_path = ws.app(APP).dir / "targets.json"
    targets = read_json(targets_path)
    for ref in ("identity", "positive", "negative"):
        targets["targets"][ref] = {"status": "hypothesis" if ref == source else "calibrated_on_device",
                                    "strategies": [{"kind": "id", "value": ref}]}
    write_json(targets_path, targets)
    case["conditions"]["fault"] = {
        "kind": "vehicle" if source == "identity" else "page",
        "true": {"type": "target_present", "target": "positive"},
        "false": {"type": "target_present", "target": "negative"},
        **({"scope": {"vehicle": "identity"}} if source == "identity" else {})}
    case["assertions"][1].pop("guard")
    guard = {"condition": "fault", "equals": True}
    if consumer == "assertion_guard":
        case["assertions"][1]["guard"] = guard
    elif consumer == "step_when":
        case["steps"].append({"id": "S2", "action": "wait", "when": guard})
    elif consumer == "dismiss":
        case["steps"].append({"id": "S2", "action": "input", "target": "start", "text": "synthetic",
                               "dismiss_obstruction_when": guard})
    elif consumer in {"flow_when", "recipe_when"}:
        child = {"id": "child", "action": "wait", "when": guard}
        app_dir = targets_path.parent
        if consumer == "flow_when":
            write_json(app_dir / "flows.json", {"flows": {"synthetic": {"steps": [child]}}})
            case["steps"].append({"id": "S2", "action": "flow", "flow": "synthetic"})
        else:
            states = read_json(app_dir / "states.json")
            states["recipes"]["dashboard"] = [child]
            write_json(app_dir / "states.json", states)
            case["steps"].append({"id": "S2", "action": "ensure_state", "state": "dashboard"})
    elif consumer == "cleanup_when":
        case["cleanup"][0]["when"] = guard
    elif consumer == "safe_stop_when":
        case["safety"] = {"safe_stop": [{"id": "STOP", "action": "wait", "when": guard}]}
    elif consumer in {"oracle_assertion", "oracle_step"}:
        kn = KnowledgeStore(ws.root)
        items = {"items": kn.items()}
        next(item for item in items["items"] if item["id"] == FAULT_RULE)["condition_requirements"] = {"fault": True}
        write_json(kn.path, items)
        if consumer == "oracle_step":
            case["assertions"][1]["oracle_refs"] = [COMMON_RULE]
            case["steps"][0]["oracle_refs"] = [FAULT_RULE]
    else:
        raise AssertionError(consumer)
    write_json(ws.case_path(case["id"]), case)
    return ws, case, ws.app(APP), KnowledgeStore(ws.root)


@pytest.mark.parametrize("source", ["identity", "positive", "negative"])
@pytest.mark.parametrize("consumer", ["assertion_guard", "step_when", "dismiss", "flow_when", "recipe_when",
                                       "cleanup_when", "safe_stop_when", "oracle_assertion", "oracle_step"])
def test_lint_rejects_used_condition_hypothesis_targets(condition_rig, consumer, source):
    ws, case, app, kn = _asset(condition_rig, consumer, source)
    issues = cases.lint(case, app, kn, ws.policy())
    assert len(issues) == 1, issues
    assert issues[0]["field"] == "targets" and source in issues[0]["problem"]
    assert "status=hypothesis" in issues[0]["problem"]


@pytest.mark.parametrize("consumer", ["assertion_guard", "dismiss", "oracle_assertion", "oracle_step"])
def test_promotion_refuses_condition_only_hypothesis_without_writing(condition_rig, consumer):
    ws, case, app, kn = _asset(condition_rig, consumer, "identity")
    before = ws.case_path(case["id"]).read_bytes()
    with pytest.raises(PermissionError, match="status=hypothesis"):
        cases.promote(ws, case["id"], "candidate", kn)
    assert ws.case_path(case["id"]).read_bytes() == before
    # Actual calibration is external; only this isolated fixture flips the status.
    targets = read_json(app.dir / "targets.json")
    targets["targets"]["identity"]["status"] = "calibrated_on_device"
    write_json(app.dir / "targets.json", targets)
    assert cases.promote(ws, case["id"], "candidate", kn)["status"] == "candidate"


@pytest.mark.parametrize("source", ["identity", "positive", "negative"])
def test_registered_provider_uses_the_same_hypothesis_gate_and_old_api(condition_rig, source):
    ws, case, app, kn = _asset(condition_rig, "assertion_guard", source)
    metadata = read_json(app.dir / "app.json")
    metadata["conditions"] = case.pop("conditions")
    write_json(app.dir / "app.json", metadata)
    app = ws.app(APP)
    assert cases.hypothesis_targets(case, app) == [source]
    assert len(cases.lint(case, app, kn, ws.policy())) == 1


@pytest.mark.parametrize("consumer", ["assertion_guard", "oracle_assertion"])
def test_unused_hypothesis_provider_does_not_block_or_change_dependencies(condition_rig, consumer):
    ws, case, app, kn = _asset(condition_rig, consumer, "identity")
    if consumer == "assertion_guard":
        case["assertions"][1].pop("guard")
    else:
        case["assertions"][1]["oracle_refs"] = [COMMON_RULE]
    assert cases.hypothesis_targets(case, app, kn) == []
    assert cases.lint(case, app, kn, ws.policy()) == []
    before = cases.dependency_hash(case, app, kn)
    app.targets["identity"]["strategies"][0]["value"] = "unused-change"
    assert cases.dependency_hash(case, app, kn) == before
    plain = deepcopy(case)
    plain.pop("conditions")
    assert cases.dependency_hash(plain, app, kn) == before
    write_json(ws.case_path(case["id"]), case)
    assert cases.promote(ws, case["id"], "candidate", kn)["status"] == "candidate"
