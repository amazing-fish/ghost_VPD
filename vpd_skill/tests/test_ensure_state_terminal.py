"""ensure_state owns its terminal guarantee; recipes only describe the route.

These are isolated synthetic Replay runs, not real-device login evidence.
"""
from pathlib import Path

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE, SETTINGS_RULE
from vpdtest import cases
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.observe import Observer
from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json


def _case(ws, children, *, target="settings", confirmed=False):
    confirm(ws, COPY_RULE)
    if confirmed:
        confirm(ws, SETTINGS_RULE)
    path = ws.app("motor_hm").dir / "states.json"
    states = read_json(path)
    states["recipes"][target] = children
    write_json(path, states)
    case = ws.load_case(COPY_CASE)
    case["assertions"] = case["assertions"][:1]
    case["cleanup"] = []
    case["steps"] += [{"id": "S2", "action": "ensure_state", "state": target,
                       "oracle_refs": [SETTINGS_RULE]},
                      {"id": "S3", "action": "wait", "duration_s": 0}]
    return case


def _run(ws, case):
    return run_case(ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)


@pytest.mark.parametrize("confirmed,expected", [(False, "pending"), (True, "failed")])
def test_successful_recipe_cannot_claim_a_state_it_did_not_reach(verdict_ws, confirmed, expected):
    case = _case(verdict_ws, [{"action": "wait", "duration_s": 0}], confirmed=confirmed)
    run = _run(verdict_ws, case)
    parent = run["steps"][1]
    assert parent["outcome"] == "fail_candidate"
    assert parent["final_status"] == "postcondition_not_met"
    assert run["steps"][2]["outcome"] == "not_run"
    assert run["verdict"]["verdict"] == expected
    child = parent["sub_steps"][-1]
    finding = next(f for f in run["verdict"]["findings"] if f.get("step") == child["step_id"])
    assert finding["evidence"]["obs_after"] == parent["final_obs"]
    assert finding["evidence"]["oracle_source"] == "S2"
    assert finding["oracle"]["items"][0]["id"] == SETTINGS_RULE


def test_reaching_the_state_needs_no_manual_wait_in_the_recipe(verdict_ws):
    case = _case(verdict_ws, [{"action": "tap", "target": "settings_entry", "retry": 0}])
    run = _run(verdict_ws, case)
    parent = run["steps"][1]
    assert parent["outcome"] == run["steps"][2]["outcome"] == "passed"
    final = parent["sub_steps"][-1]
    assert final["attempts"][-1]["post"]["expected"] == "settings"
    assert final["attempts"][-1]["post"]["met"] is True
    assert parent["final_obs"] == final["final_obs"]
    assert run["verdict"]["verdict"] == "passed"


def test_already_reached_state_does_not_run_recipe_or_extra_verification(verdict_ws):
    case = _case(verdict_ws, [{"action": "tap", "target": "settings_entry"}])
    case["steps"][1]["state"] = "my_tab"
    run = _run(verdict_ws, case)
    parent = run["steps"][1]
    assert parent["outcome"] == "passed" and not parent["attempts"]
    assert not parent.get("sub_steps")
    observations = [read_json(p) for p in (Path(run["dir"]) / "obs").glob("*/observation.json")]
    assert [o["label"] for o in observations if o["label"].startswith("S2")] == ["S2:check"]


@pytest.mark.parametrize("failure", ["blocked", "pending", "error"])
def test_incomplete_required_recipe_stops_before_terminal_check(verdict_ws, monkeypatch, failure):
    child = {"action": "read", "target": {"strategies": [{"kind": "text", "value": "absent"}]}, "retry": 0}
    if failure == "pending":
        child = {"action": "wait", "duration_s": 0, "critical": False,
                 "when": {"condition": "unknown", "equals": True}}
    elif failure == "error":
        child = {"action": "tap", "target": "settings_entry", "retry": 0}
        original = ReplayDevice.tap
        def tap(device, x, y):
            if device.current_screen() == "my_tab":
                return DeviceResult("tap", False, False, "timeout", "synthetic receipt error")
            return original(device, x, y)
        monkeypatch.setattr(ReplayDevice, "tap", tap)
    case = _case(verdict_ws, [child])
    case["conditions"] = {"unknown": {"kind": "page",
        "true": {"type": "text_present", "text": "unknown positive"},
        "false": {"type": "text_present", "text": "unknown negative"}}}
    run = _run(verdict_ws, case)
    parent = run["steps"][1]
    assert parent["outcome"] == failure
    assert len(parent["sub_steps"]) == 1
    assert run["steps"][2]["outcome"] == "not_run"


def test_skipped_optional_route_cannot_substitute_for_terminal_evidence(verdict_ws):
    case = _case(verdict_ws, [{"action": "tap", "target": "settings_entry", "critical": False,
                              "when": {"condition": "on_my", "equals": False}}])
    case["conditions"] = {"on_my": {"kind": "page", "true": {"type": "state_is", "state": "my_tab"},
                                    "false": {"type": "state_is", "state": "settings"}}}
    run = _run(verdict_ws, case)
    parent = run["steps"][1]
    assert parent["sub_steps"][0]["outcome"] == "skipped"
    assert not parent["sub_steps"][0]["attempts"]
    assert parent["outcome"] == "fail_candidate"
    assert run["verdict"]["verdict"] == "pending"


@pytest.mark.parametrize("failure,expected", [("collection", "error"), ("interrupt", "blocked")])
def test_terminal_check_preserves_observation_errors_and_unreviewed_interrupts(verdict_ws, monkeypatch, failure, expected):
    case = _case(verdict_ws, [{"action": "tap", "target": "settings_entry", "retry": 0}])
    path = verdict_ws.app("motor_hm").dir / "states.json"
    states = read_json(path)
    states["interrupts"]["terminal_gate"] = {"class": "blocking", "require_text": ["Synthetic final gate"]}
    write_json(path, states)
    observe, dump = Observer.observe, ReplayDevice.dump_tree
    def dump_tree(device, bundle, path):
        if getattr(device, "terminal_error", False):
            return DeviceResult("dump_tree", True, False, "timeout", "synthetic terminal observation error")
        return dump(device, bundle, path)
    def observe_terminal(observer, label="", screenshot=True):
        if label == "S2.2#1:after":
            if failure == "collection":
                observer.device.terminal_error = True
            else:
                observer.device.world["screens"]["settings"]["root"]["children"].append(
                    {"t": "Text", "text": "Synthetic final gate", "b": [20, 100, 300, 150]})
        return observe(observer, label, screenshot)
    monkeypatch.setattr(Observer, "observe", observe_terminal)
    monkeypatch.setattr(ReplayDevice, "dump_tree", dump_tree)
    run = _run(verdict_ws, case)
    parent = run["steps"][1]
    assert parent["sub_steps"][0]["outcome"] == "passed"
    assert parent["outcome"] == expected and run["verdict"]["verdict"] == expected
    assert run["steps"][2]["outcome"] == "not_run"
    if failure == "collection":
        assert "synthetic terminal observation error" in str(run["verdict"]["execution"]["errors"])


def test_parent_condition_is_still_required_at_terminal_check(verdict_ws):
    case = _case(verdict_ws, [{"action": "tap", "target": "settings_entry", "retry": 0}])
    case["conditions"] = {"on_my": {"kind": "page", "true": {"type": "state_is", "state": "my_tab"},
                                    "false": {"type": "state_is", "state": "settings"}}}
    case["steps"][1]["when"] = {"condition": "on_my", "equals": True}
    run = _run(verdict_ws, case)
    parent = run["steps"][1]
    assert parent["sub_steps"][0]["outcome"] == "passed"
    assert parent["outcome"] == "pending"
    assert parent["sub_steps"][-1]["condition"]["status"] == "false"
    assert run["steps"][2]["outcome"] == "not_run"
    taps = [e for e in read_jsonl(Path(run["dir"]) / "events.jsonl") if e["event"] == "dispatch" and e["op"] == "tap"]
    assert len(taps) == 2  # Enter my tab, then settings; no blind terminal action.


@pytest.mark.parametrize("old_contract", [5, 6])
def test_old_replays_cannot_validate_the_new_terminal_guarantee(verdict_ws, monkeypatch, old_contract):
    case = _case(verdict_ws, [{"action": "wait", "duration_s": 0}])
    run = _run(verdict_ws, case)
    assert run["verdict"]["verdict"] == "pending"
    knowledge, app = KnowledgeStore(verdict_ws.root), verdict_ws.app(case["app"])
    current = cases.dependency_hash(case, app, knowledge)
    original_hash = cases.sha256_obj
    def previous_contract(value):
        assert value["result_contract"] == 7
        return original_hash({**value, "result_contract": old_contract})
    monkeypatch.setattr(cases, "sha256_obj", previous_contract)
    old = cases.dependency_hash(case, app, knowledge)
    monkeypatch.setattr(cases, "sha256_obj", original_hash)
    assert old != current
    write_json(verdict_ws.case_path(case["id"]), case)
    for suffix in ("a", "b"):
        old_run = {**run, "run_id": "synthetic-old-" + suffix,
                   "case": {**run["case"], "deps": old},
                   "verdict": {**run["verdict"], "verdict": "passed", "findings": [],
                               "product_verdict": "passed", "product_failed": False}}
        cases.record_replay(verdict_ws, case["id"], old_run)
    saved = verdict_ws.load_case(case["id"])
    assert len(saved["replays"]) == 2
    assert not cases.replay_basis(saved, current)["ok"]
