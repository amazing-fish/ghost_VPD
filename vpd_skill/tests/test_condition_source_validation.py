"""Runner validates public condition sources before constructing any device.

The current public ``at`` namespace is top-level case.steps IDs. A known source
that has not succeeded yet stays unknown at runtime; ordering is not a new lint rule.
"""
from copy import deepcopy

import pytest
from test_condition_runner import APP, condition_rig

from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

__all__ = ("condition_rig",)


def _case_with_source(rig, location, source):
    ws, original, world = rig
    case = deepcopy(original)
    guard = {"condition": "fault", "equals": True, "at": source}
    child = {"id": "LOCAL", "action": "wait", "duration_s": 0, "retry": 0, "when": guard}
    if location == "main":
        case["steps"].append({**child, "id": "S2"})
    elif location == "assertion":
        case["assertions"][1]["guard"] = guard
    elif location == "cleanup":
        case["cleanup"][0]["when"] = guard
    elif location == "safe_stop":
        case["safety"] = {"max_motion_s": 30, "stopped_states": ["dashboard"],
                          "safe_stop": [{**child, "id": "SAFE1"}]}
    elif location == "flow":
        write_json(ws.app(APP).dir / "flows.json", {"flows": {"with_source": {"steps": [child]}}})
        case["steps"].append({"id": "F1", "action": "flow", "flow": "with_source"})
    elif location == "recipe":
        path = ws.app(APP).dir / "states.json"
        model = read_json(path)
        model["recipes"]["details"] = [child, {
            "id": "GO", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0}]
        write_json(path, model)
        case["steps"].append({"id": "F1", "action": "ensure_state", "state": "details"})
    else:
        raise AssertionError(location)
    return ws, case, world()


@pytest.mark.parametrize("location", ["main", "assertion", "cleanup", "safe_stop", "flow", "recipe"])
@pytest.mark.parametrize("source", ["MISSING_SOURCE", "S1_TYPO"])
def test_invalid_source_is_rejected_before_device_construction(condition_rig, monkeypatch, location, source):
    ws, case, spec = _case_with_source(condition_rig, location, source)
    constructed = []

    def construct(*args, **kwargs):
        constructed.append(True)
        raise AssertionError("device construction reached before invalid condition source was rejected")

    monkeypatch.setattr(ws, "device", construct)
    with pytest.raises(ValueError, match=source):
        run_case(ws, case=case, device_spec=spec, propose=False)
    assert constructed == []
    assert not ws.runs_dir.exists()


@pytest.mark.parametrize("location,source", [
    ("main", "C1"), ("main", "SAFE1"), ("cleanup", "C1"), ("safe_stop", "SAFE1"),
    ("flow", "LOCAL"), ("flow", "F1.1"), ("recipe", "LOCAL"), ("recipe", "F1.1"),
])
def test_cleanup_safety_and_nested_local_ids_do_not_become_public_sources(condition_rig, monkeypatch, location, source):
    ws, case, spec = _case_with_source(condition_rig, location, source)
    if source == "SAFE1" and location == "main":
        case["safety"] = {"max_motion_s": 30, "stopped_states": ["dashboard"],
                          "safe_stop": [{"id": "SAFE1", "action": "wait", "duration_s": 0}]}
    attempted = []

    def construct(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("non-public source reached device construction")

    monkeypatch.setattr(ws, "device", construct)
    with pytest.raises(ValueError, match=source.replace(".", r"\.")):
        run_case(ws, case=case, device_spec=spec, propose=False)
    assert attempted == []
    assert not ws.runs_dir.exists()


@pytest.mark.parametrize("location", ["main", "assertion", "cleanup", "safe_stop", "flow", "recipe"])
def test_valid_prior_top_level_source_keeps_existing_runtime_behavior(condition_rig, location):
    ws, case, spec = _case_with_source(condition_rig, location, "S1")
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["steps"][0]["outcome"] == "passed"
    assert run["verdict"]["verdict"] == "passed", run["verdict"]
    assert run["verdict"]["coverage"]["unverified"] == []
    if location == "main":
        selected = run["steps"][1]["condition"]
        assert selected["status"] == "true"
    elif location == "cleanup":
        assert run["cleanup"][0]["outcome"] == "passed"
        assert run["cleanup"][0]["condition"]["status"] == "true"
    elif location in ("flow", "recipe"):
        parent = run["steps"][1]
        assert parent["outcome"].startswith("passed")
        assert parent["sub_steps"][0]["condition"]["status"] == "true"
    # safe_stop is validated here but not triggered; no motion is armed in this fixture.
    assert run["validation_level"] == "offline_replay"


@pytest.mark.parametrize("source", ["S2", "S3"])
def test_known_self_or_future_source_remains_runtime_pending(condition_rig, source):
    ws, case, spec = _case_with_source(condition_rig, "main", source)
    case["steps"].append({"id": "S3", "action": "wait", "duration_s": 0, "retry": 0})
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["steps"][0]["outcome"] == "passed"
    assert run["steps"][1]["outcome"] == "pending"
    assert run["steps"][1]["condition"]["status"] == "unknown"
    assert run["steps"][1]["attempts"] == []
    assert run["steps"][2]["outcome"] == "not_run"
    assert run["verdict"]["verdict"] == "pending"
