"""Composite execution must preserve the same verdict as its actual child results.

All rules, assets and runs below belong to isolated synthetic Replay workspaces.
"""
from pathlib import Path

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE, SETTINGS_RULE
from vpdtest.commands import _run_brief
from vpdtest.devices import LAUNCHER, DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json
from vpdtest.verdict import case_verdict


def _run(ws, *, child_confirmed=True, parent_refs=(), child_refs=None,
         critical=False, depth=1, blocked=False, cleanup=None, guard=False, after=None,
         unknown=False, container="flow"):
    confirm(ws, COPY_RULE)
    if child_confirmed:
        confirm(ws, SETTINGS_RULE)
    child = {"action": "wait", "duration_s": 0, "expect_state": "settings",
             "timeout_s": 0, "retry": 0, "critical": critical,
             "oracle_refs": [SETTINGS_RULE] if child_refs is None else child_refs}
    if blocked:
        child = {"action": "read", "critical": critical, "retry": 0,
                 "target": {"strategies": [{"kind": "text", "value": "synthetic absent target"}]}}
    if unknown:
        child["when"] = {"condition": "unknown_page", "equals": True}
    flows = {"inner": {"steps": [child, after or {"action": "wait", "duration_s": 0, "retry": 0}]}}
    name = "inner"
    for level in range(1, depth):
        flows[f"outer{level}"] = {"steps": [{"action": "flow", "flow": name},
                                              {"action": "wait", "duration_s": 0, "retry": 0}]}
        name = f"outer{level}"
    write_json(ws.root / "apps" / "motor_hm" / "flows.json", {"flows": flows})
    case = ws.load_case(COPY_CASE)
    case["assertions"] = case["assertions"][:1]
    case["cleanup"] = []
    step = {"id": "S2", "action": "flow", "flow": name, "oracle_refs": list(parent_refs)}
    if container == "recipe":
        path = ws.root / "apps" / "motor_hm" / "states.json"
        data = read_json(path)
        data["states"]["audit_target"] = {"require_text": ["synthetic unreachable target state"]}
        data["recipes"]["audit_target"] = flows[name]["steps"]
        write_json(path, data)
        step = {"id": "S2", "action": "ensure_state", "state": "audit_target", "oracle_refs": list(parent_refs)}
    if cleanup is None:
        case["steps"].append(step)
    else:
        case["cleanup"] = [{**step, "id": "C1", "required": cleanup}]
    if unknown:
        case["conditions"] = {"unknown_page": {"kind": "page",
            "true": {"type": "text_present", "text": "synthetic unknown positive"},
            "false": {"type": "text_present", "text": "synthetic unknown negative"}}}
    if guard:
        case["conditions"] = {"is_settings": {"kind": "page",
            "true": {"type": "state_is", "state": "settings"},
            "false": {"type": "state_is", "state": "my_tab"}}}
        path = ws.root / "knowledge" / "items.json"
        data = read_json(path)
        next(item for item in data["items"] if item["id"] == SETTINGS_RULE)["condition_requirements"] = {"is_settings": True}
        write_json(path, data)
    run = run_case(ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    parent = run["steps"][1] if cleanup is None else run["cleanup"][0]
    leaf = parent
    ancestors = []
    while leaf.get("sub_steps"):
        ancestors.append(leaf["step_id"])
        leaf = leaf["sub_steps"][0]
    return case, run, parent, leaf, ancestors


@pytest.mark.parametrize("depth", [1, 3])
@pytest.mark.parametrize("confirmed,blocked,expected", [(True, False, "failed"),
    (False, False, "pending"), (False, True, "blocked")])
def test_noncritical_child_keeps_failure_without_stopping_sequence(verdict_ws, depth, confirmed, blocked, expected):
    _, run, parent, leaf, ancestors = _run(verdict_ws, depth=depth, child_confirmed=confirmed, blocked=blocked)
    assert parent["outcome"] == "passed"
    assert parent["sub_steps"][-1]["outcome"] == "passed"
    assert run["assertions"][0]["result"] == "pass"
    verdict = run["verdict"]
    assert verdict["verdict"] == expected
    findings = [f for f in verdict["findings"] if f.get("step") == leaf["step_id"]]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["parent_step"] == ancestors[-1] and finding["ancestors"] == ancestors
    assert finding["evidence"]["obs_after"] == leaf["final_obs"]
    assert _run_brief(run)["verdict"] == expected
    report = (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")
    assert leaf["step_id"] in report
    if not blocked:
        assert SETTINGS_RULE in {item["id"] for item in run["knowledge_snapshot"]}
    if expected == "failed":
        assert verdict["product_failed"]
        assert verdict["failure_signature"] == [f"step:{leaf['step_id']}:postcondition_not_met"]


@pytest.mark.parametrize("child_confirmed,parent_refs,expected", [
    (True, [], "failed"), (False, [COPY_RULE], "pending"), (True, [COPY_RULE], "failed")])
def test_critical_child_uses_own_oracle_and_deduplicates_parent(verdict_ws, child_confirmed, parent_refs, expected):
    _, run, parent, leaf, _ = _run(verdict_ws, critical=True, depth=2,
                                  child_confirmed=child_confirmed, parent_refs=parent_refs)
    assert parent["outcome"] == "fail_candidate" and len(parent["sub_steps"]) == 1
    findings = [f for f in run["verdict"]["findings"] if f["kind"] == "step"]
    assert run["verdict"]["verdict"] == expected
    assert len(findings) == 1 and findings[0]["step"] == leaf["step_id"]
    assert findings[0]["oracle"]["items"][0]["id"] == SETTINGS_RULE


@pytest.mark.parametrize("container", ["flow", "recipe"])
def test_nearest_parent_oracle_remains_compatible_when_child_has_no_refs(verdict_ws, container):
    _, run, _, leaf, _ = _run(verdict_ws, critical=True, child_refs=[], parent_refs=[COPY_RULE], depth=2, container=container)
    finding = next(f for f in run["verdict"]["findings"] if f.get("step") == leaf["step_id"])
    assert run["verdict"]["verdict"] == "failed"
    assert finding["oracle"]["items"][0]["id"] == COPY_RULE
    assert finding["evidence"]["oracle_source"] == "S2"


@pytest.mark.parametrize("inherit", [False, True])
def test_child_or_inherited_oracle_guard_uses_child_observation(verdict_ws, inherit):
    _, run, _, leaf, _ = _run(verdict_ws, guard=True, child_refs=[] if inherit else None,
                             parent_refs=[SETTINGS_RULE] if inherit else [], depth=2,
                             after={"action": "tap", "target": "settings_entry", "expect_state": "settings", "retry": 0})
    assert run["verdict"]["verdict"] == "pending"
    finding = next(f for f in run["verdict"]["findings"] if f.get("step") == leaf["step_id"])
    assert leaf["oracle_guard"]["status"] == "false"
    assert finding["evidence"]["guard"]["records"][0]["target_obs"] == leaf["final_obs"]
    assert run["steps"][1]["final_obs"] != leaf["final_obs"]
    if inherit:
        assert run["steps"][1]["oracle_guard"]["status"] == "true"
    assert not run["verdict"]["product_failed"]


def test_child_without_any_oracle_remains_pending(verdict_ws):
    _, run, _, leaf, _ = _run(verdict_ws, child_refs=[], depth=2)
    assert run["verdict"]["verdict"] == "pending"
    assert not run["verdict"]["product_failed"]
    assert any(f.get("step") == leaf["step_id"] for f in run["verdict"]["findings"])


def test_later_crash_does_not_authorize_earlier_unconfirmed_postcondition(verdict_ws, monkeypatch):
    original = ReplayDevice.launch
    calls = []
    def launch(self, bundle, ability):
        calls.append(bundle)
        if len(calls) == 2:
            self.state.update(alive=False, foreground=LAUNCHER)
            return DeviceResult("launch", True, True)
        return original(self, bundle, ability)
    monkeypatch.setattr(ReplayDevice, "launch", launch)
    _, run, parent, leaf, _ = _run(verdict_ws, child_refs=[], depth=2,
        after={"action": "launch", "retry": 0})
    assert parent["final_status"] == "anomaly"
    verdict = run["verdict"]
    assert verdict["verdict"] == "failed"
    earlier = next(f for f in verdict["findings"] if f.get("step") == leaf["step_id"])
    assert earlier["result"] == "pending" and earlier["oracle"]["status"] != "confirmed"
    failed = [f for f in verdict["findings"] if f["result"] == "failed"]
    assert len(failed) == 1 and failed[0]["mode"] == "anomaly"


def test_execution_error_does_not_erase_independent_child_product_failure(verdict_ws, monkeypatch):
    original = ReplayDevice.launch
    calls = []
    def launch(self, bundle, ability):
        calls.append(bundle)
        if len(calls) == 2:
            return DeviceResult("launch", False, False, "timeout", "synthetic second launch error")
        return original(self, bundle, ability)
    monkeypatch.setattr(ReplayDevice, "launch", launch)
    _, run, parent, leaf, _ = _run(verdict_ws, depth=2,
        after={"action": "launch", "critical": False, "retry": 0})
    assert parent["outcome"] == "passed"
    verdict = run["verdict"]
    assert verdict["verdict"] == "error" and verdict["product_verdict"] == "failed"
    assert len(verdict["execution"]["errors"]) == 1
    assert verdict["failure_signature"] == [f"step:{leaf['step_id']}:postcondition_not_met"]


def test_recovered_child_keeps_history_without_reopening_its_error(verdict_ws, monkeypatch):
    original = ReplayDevice.launch
    calls = []
    def launch(self, bundle, ability):
        calls.append(bundle)
        if len(calls) == 2:
            return DeviceResult("launch", False, False, "timeout", "synthetic recoverable launch error")
        return original(self, bundle, ability)
    monkeypatch.setattr(ReplayDevice, "launch", launch)
    _, run, parent, _, _ = _run(verdict_ws,
        after={"action": "launch", "critical": False, "retry": 1})
    recovered = parent["sub_steps"][-1]
    assert recovered["outcome"] == "passed_after_recovery" and recovered["first_failure"]
    assert run["verdict"]["verdict"] == "failed" and not run["verdict"]["execution"]["errors"]
    assert not any(f.get("step") == recovered["step_id"] for f in run["verdict"]["findings"])


@pytest.mark.parametrize("source", [None, "S2.1"])
def test_parent_independent_terminal_failure_is_not_deduplicated(verdict_ws, source):
    _, run, parent, leaf, _ = _run(verdict_ws, parent_refs=[COPY_RULE])
    # Model a separately checked composite terminal state after the children finished.
    # Its real final observation is different from the child's earlier failure.
    parent.update(outcome="fail_candidate", attribution="product", final_status="postcondition_not_met",
                  reason="independent parent terminal state absent", failure_source=source)
    assert parent["final_obs"] != leaf["final_obs"]
    verdict = case_verdict(run["preflight"], run["env"], run["steps"], run["assertions"],
                           KnowledgeStore(verdict_ws.root).bind(run["oracle_facts"]))
    failed = [f for f in verdict["findings"] if f["result"] == "failed"]
    assert {f["step"] for f in failed} == {parent["step_id"], leaf["step_id"]}


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("blocked", [False, True])
def test_nested_cleanup_failure_preserves_required_boundary(verdict_ws, required, blocked):
    _, run, parent, leaf, ancestors = _run(verdict_ws, cleanup=required, blocked=blocked, depth=2)
    assert parent["outcome"] == "passed" and leaf["outcome"] not in ("passed", "skipped")
    assert run["verdict"]["verdict"] == ("blocked" if required else "passed")
    findings = [f for f in run["verdict"]["findings"] if f.get("kind") == "cleanup"]
    assert len(findings) == int(required)
    if required:
        assert findings[0]["step"] == leaf["step_id"]
        assert findings[0]["ancestors"] == ancestors


@pytest.mark.parametrize("required", [False, True])
def test_nested_cleanup_unknown_remains_unknown_even_when_optional(verdict_ws, required):
    _, run, _, leaf, _ = _run(verdict_ws, cleanup=required, unknown=True, depth=2)
    assert leaf["outcome"] == "pending"
    assert run["verdict"]["verdict"] == ("blocked" if required else "pending")
    assert any(f.get("step") == leaf["step_id"] and f["result"] == "pending"
               for f in run["verdict"]["findings"])
