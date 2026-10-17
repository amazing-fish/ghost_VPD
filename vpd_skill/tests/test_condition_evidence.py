"""Independent condition evidence contract; no production requirement is confirmed here."""
from types import SimpleNamespace

import pytest

from vpdtest.tree import UiTree


def _app():
    targets = {k: {"strategies": [{"kind": "id", "value": k}]} for k in ("vehicle", "task", "account")}
    return SimpleNamespace(id="synthetic", meta={}, targets=targets, dir=None,
                           target=lambda ref: (ref, targets[ref]))


def _evidence(texts=("FAULT",), epoch=0, clock=10.0, identities=None):
    ids = identities if identities is not None else {"vehicle": "car-1", "task": "task-1", "account": "account-1"}
    children = [{"attributes": {"type": "Text", "text": text, "bounds": "[0,0][80,20]"}} for text in texts]
    children += [{"attributes": {"type": "Text", "id": key, "text": value, "bounds": "[0,20][80,40]"}}
                 for key, value in ids.items()]
    tree = UiTree({"attributes": {"type": "Column", "bounds": "[0,0][100,200]"}, "children": children})
    obs = {"obs_id": f"obs-{epoch}-{clock}", "ts": "2026-10-17T12:00:00+00:00", "source": "replay", "fresh": True,
           "tree": {"status": "ok"}, "page": {"state": "synthetic"}, "action_epoch": epoch, "clock_s": clock,
           "scope": {"app": "synthetic", "app_version": "1.0"}}
    return obs, tree


def _definition(**extra):
    return {"fault": {"kind": "vehicle", "true": {"type": "text_present", "text": "FAULT"},
                      "false": {"type": "text_present", "text": "HEALTHY"}, "scope": {"vehicle": "vehicle"}, **extra}}


@pytest.mark.parametrize("texts,expected", [(("FAULT",), "true"), (("HEALTHY",), "false"),
                                            (("UNRELATED",), "unknown"), (("FAULT", "HEALTHY"), "unknown")])
def test_positive_and_independent_counter_evidence(texts, expected):
    from vpdtest.conditions import evaluate_guard
    obs, tree = _evidence(texts)
    out = evaluate_guard({"condition": "fault", "equals": True}, _definition(), {}, None, _app(), obs, tree)
    assert out["status"] == expected
    assert out["obs"] == obs["obs_id"] and out["scope"]["vehicle"] == "car-1"


@pytest.mark.parametrize("change,expected", [("epoch", "unknown"), ("age", "unknown"), ("scope", "unknown"),
                                              ("build", "unknown"), ("error", "error"), ("stale", "unknown")])
def test_historical_evidence_never_survives_invalidations(change, expected):
    from vpdtest.conditions import evaluate_guard
    source, stree = _evidence()
    target, ttree = _evidence(clock=11)
    if change == "epoch": target["action_epoch"] = 1
    if change == "age": target["clock_s"] = 20
    if change == "scope": target, ttree = _evidence(clock=11, identities={"vehicle": "car-2"})
    if change == "build": target["scope"]["app_version"] = "2.0"
    if change == "error": source["tree"] = {"status": "error", "error_class": "command_failed", "error": "raw failure"}
    if change == "stale": source["fresh"] = False
    out = evaluate_guard({"condition": "fault", "equals": True, "at": "S1"}, _definition(max_age_s=3),
                         {"S1": {"outcome": "passed", "final_obs": source["obs_id"]}},
                         lambda _: (source, stree), _app(), target, ttree)
    assert out["status"] == expected
    if change == "error": assert out["execution_error"]["error"] == "raw failure"


def test_definition_validation_rejects_result_inference_and_missing_identity():
    from vpdtest.conditions import validate_definitions, validate_guard
    defs = _definition()
    defs["fault"]["true"] = {"type": "target_enabled", "target": "vehicle"}
    defs["fault"]["scope"] = {}
    problems = validate_definitions(defs, _app())
    assert any("true" in p["field"] for p in problems)
    assert any("scope" in p["field"] for p in problems)
    assert validate_guard({"condition": "missing", "equals": True}, defs)
    assert validate_guard({"condition": "fault", "equals": "true"}, defs)
    assert validate_guard({"condition": "fault", "equals": True, "at": "BAD"}, defs, {"S1"})


def test_declared_state_is_never_read_as_condition_evidence():
    from vpdtest.conditions import evaluate_guard
    obs, tree = _evidence(("UNRELATED",))
    obs["facts"] = {"fault": True, "vehicle_fault_state": "vpd_fault"}
    out = evaluate_guard({"condition": "fault", "equals": True}, _definition(), {}, None, _app(), obs, tree)
    assert out["status"] == "unknown"


def test_missing_provider_is_unknown_but_invalid_explicit_definition_is_error():
    from vpdtest.conditions import evaluate_guard
    obs, tree = _evidence()
    guard = {"condition": "fault", "equals": True}
    assert evaluate_guard(guard, {}, {}, None, _app(), obs, tree)["status"] == "unknown"
    defs = _definition(ttl=3)
    malformed = evaluate_guard(guard, defs, {}, None, _app(), obs, tree)
    assert malformed["status"] == "error" and malformed["error_class"] == "invalid_condition"


def test_definition_rejects_unknown_state_and_unknown_invalidation_fields():
    from vpdtest.conditions import validate_definitions
    defs = _definition(invalidated_by=["switch_vehicle"])
    defs["fault"]["true"] = {"type": "state_is", "state": "UNREGISTERED"}
    problems = validate_definitions(defs, _app())
    assert any("invalidated_by" in p["problem"] for p in problems)
    assert any("UNREGISTERED" in p["problem"] for p in problems)


def test_missing_and_parse_failed_historical_evidence_are_distinct():
    from json import JSONDecodeError

    from vpdtest.conditions import evaluate_guard
    obs, tree = _evidence()
    steps = {"S1": {"outcome": "passed", "final_obs": "missing"}}
    guard = {"condition": "fault", "equals": True, "at": "S1"}
    def missing(_): raise FileNotFoundError("missing")
    def corrupt(_): raise JSONDecodeError("broken", "{", 0)
    assert evaluate_guard(guard, _definition(), steps, missing, _app(), obs, tree)["status"] == "unknown"
    out = evaluate_guard(guard, _definition(), steps, corrupt, _app(), obs, tree)
    assert out["status"] == "error" and out["error_class"] == "condition_evidence_load_error"


@pytest.mark.parametrize("source_text,target_text", [(("FAULT",), ("HEALTHY",)), (("HEALTHY",), ("FAULT",)),
                                                    (("FAULT",), ("FAULT", "HEALTHY"))])
def test_new_counterproof_invalidates_same_epoch_historical_fact(source_text, target_text):
    from vpdtest.conditions import evaluate_guard
    source, stree = _evidence(source_text)
    target, ttree = _evidence(target_text, clock=11)
    record = evaluate_guard({"condition": "fault", "equals": True, "at": "S1"}, _definition(max_age_s=3),
                            {"S1": {"outcome": "passed", "final_obs": source["obs_id"]}},
                            lambda _: (source, stree), _app(), target, ttree)
    assert record["status"] == "unknown"
    assert record["current_checks"] and "矛盾" in record["reason"]
