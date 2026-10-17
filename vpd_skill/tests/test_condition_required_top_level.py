"""Required top-level skips stop dependants without erasing independent results.

All rules and page responses are synthetic. The real Runner executes the case;
absence of later dispatches and assertion coverage are checked from its evidence.
"""
from copy import deepcopy
from pathlib import Path

import pytest
from test_condition_runner import COMMON_RULE, HEALTHY, condition_rig

from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.runner import run_case
from vpdtest.util import read_jsonl

__all__ = ("condition_rig",)


def _case(rig, *, critical="default", successors=True, future_assertions=True):
    _, template, _ = rig
    case = deepcopy(template)
    case["cleanup"] = []
    conditional = {
        "id": "S2", "action": "back", "retry": 0,
        "when": {"condition": "fault", "equals": True},
    }
    if critical != "default":
        conditional["critical"] = critical
    case["steps"].append(conditional)
    case["assertions"][1]["at"] = "S2"
    if successors:
        case["steps"] += [
            {"id": "S3", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0},
            {"id": "S4", "action": "back", "expect_state": "dashboard", "retry": 0},
        ]
        if future_assertions:
            case["assertions"] += [
                {"id": "A-future", "at": "S3", "type": "state_is", "state": "details",
                 "oracle_refs": [COMMON_RULE]},
                {"id": "A-last", "at": "S4", "type": "state_is", "state": "dashboard",
                 "oracle_refs": [COMMON_RULE]},
            ]
    return case


def _run(rig, case, *, common=True):
    ws, _, world = rig
    return run_case(ws, case=case, device_spec=world((HEALTHY,), False, common), propose=False)


def _assert_no_dependent_dispatches(run):
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert not [event for event in events if event["event"] == "dispatch"
                and event.get("step") in {"S3", "S4"} and event.get("sent") is not False]
    for step in run["steps"][2:]:
        assert step["outcome"] == "not_run", step
        assert step["final_status"] == "required_step_skipped", step
        assert step["blocked_by"] == "S2", step
        assert not step["attempts"]


@pytest.mark.parametrize("critical", ["default", True])
@pytest.mark.parametrize("future_assertions", [False, True])
def test_required_top_level_skip_stops_all_dependants_and_keeps_case_pending(
    condition_rig, critical, future_assertions,
):
    run = _run(condition_rig, _case(condition_rig, critical=critical, future_assertions=future_assertions))
    assert run["steps"][1]["outcome"] == "skipped"
    _assert_no_dependent_dispatches(run)
    assert run["verdict"]["verdict"] == "pending", run["verdict"]
    assertions = {item["id"]: item for item in run["assertions"]}
    assert assertions["A-common"]["result"] == "pass"
    assert assertions["A-fault"]["result"] == "not_applicable"
    coverage = run["verdict"]["coverage"]
    assert coverage["verified"] == ["A-common"]
    assert coverage["not_applicable"] == ["A-fault"]
    if future_assertions:
        assert assertions["A-future"]["result"] == "not_run"
        assert assertions["A-last"]["result"] == "not_run"
        assert set(coverage["unverified"]) == {"A-future", "A-last"}
    else:
        assert not coverage["unverified"]


def test_optional_top_level_skip_allows_later_independently_verified_actions(condition_rig):
    run = _run(condition_rig, _case(condition_rig, critical=False))
    assert [step["outcome"] for step in run["steps"]] == ["passed", "skipped", "passed", "passed"]
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert {event.get("step") for event in events if event["event"] == "dispatch"
            and event.get("step") in {"S3", "S4"} and event.get("sent") is True} == {"S3", "S4"}
    assert run["verdict"]["verdict"] == "passed"
    coverage = run["verdict"]["coverage"]
    assert set(coverage["verified"]) == {"A-common", "A-future", "A-last"}
    assert coverage["not_applicable"] == ["A-fault"]
    assert not coverage["unverified"]


@pytest.mark.parametrize("cleanup_error", [False, True])
def test_required_skip_preserves_prior_product_failure_and_independent_execution_error(
    condition_rig, monkeypatch, cleanup_error,
):
    case = _case(condition_rig)
    if cleanup_error:
        case["cleanup"] = [{"id": "C1", "action": "stop_app", "retry": 0}]

        def stop(self, bundle):
            return DeviceResult("stop", False, False, "command_failed", "synthetic cleanup error",
                                raw="required-skip independent cleanup receipt")

        monkeypatch.setattr(ReplayDevice, "stop", stop)
    run = _run(condition_rig, case, common=False)
    _assert_no_dependent_dispatches(run)
    verdict = run["verdict"]
    assert verdict["verdict"] == ("error" if cleanup_error else "failed"), verdict
    assert verdict["product_failed"] and verdict["product_verdict"] == "failed"
    assert any("A-common" in signature for signature in verdict["failure_signature"])
    assert run["assertions"][0]["result"] == "fail"
    assert verdict["coverage"]["verified"] == ["A-common"]
    assert set(verdict["coverage"]["unverified"]) == {"A-future", "A-last"}
    if cleanup_error:
        assert verdict["execution"]["status"] == "error"
        assert "required-skip independent cleanup receipt" in str(verdict["execution"]["errors"])


@pytest.mark.parametrize("all_conditional", [False, True])
def test_terminal_required_skip_does_not_invalidate_prior_independent_assertions(
    condition_rig, all_conditional,
):
    case = _case(condition_rig, successors=False)
    if all_conditional:
        case["assertions"] = [case["assertions"][1]]
    run = _run(condition_rig, case)
    assert run["steps"][-1]["outcome"] == "skipped"
    assert run["verdict"]["verdict"] == ("not_applicable" if all_conditional else "passed")
    coverage = run["verdict"]["coverage"]
    assert coverage["verified"] == ([] if all_conditional else ["A-common"])
    assert coverage["not_applicable"] == ["A-fault"]
    assert not coverage["unverified"]
