"""Opt-in scoped display evidence; historical text presence keeps its semantics."""
import pytest

from test_case_templates import APP, _scope, _spec, rig
from vpdtest import cases
from vpdtest.case_templates import build_case
from vpdtest.devices.replay import expand
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.verdict import evaluate_assertion

__all__ = ("rig",)


def _assertion(**overrides):
    return {"id": "A", "at": "S", "type": "text_present", "within": "parking_name",
            "strict_visible_text": True, "text": "expected", "oracle_refs": ["K-QK-TPL-240"], **overrides}


def _evaluate(rig, assertion, children):
    ws, knowledge, _ = rig
    knowledge.confirm("K-QK-TPL-240", reviewer="pytest-synthetic", basis="Synthetic strict display contract only")
    knowledge.bind({"app": APP})
    tree = UiTree(expand({"t": "Root", "b": [0, 0, 400, 800], "children": [
        {"t": "Column", "id": "parking_name", "b": [10, 60, 390, 200], "children": children}]}))
    observation = {"obs_id": "synthetic", "fresh": True, "tree": {"status": "ok"}, "page": {"state": "qk_vpd_home"}}
    steps = {"S": {"outcome": "passed", "final_obs": "synthetic", "measurement": {"value": "expected"}}}
    return evaluate_assertion(assertion, steps, lambda _: (observation, tree), knowledge, ws.app(APP))


@pytest.mark.parametrize("strict", [None, False, True])
def test_description_matching_remains_legacy_only(rig, strict):
    assertion = _assertion()
    if strict is None:
        assertion.pop("strict_visible_text")
    else:
        assertion["strict_visible_text"] = strict
    result = _evaluate(rig, assertion, [{"t": "Image", "desc": "expected", "b": [20, 70, 380, 100]}])
    assert result["result"] == ("fail" if strict is True else "pass")


@pytest.mark.parametrize("strict", [None, False, True])
def test_out_of_scope_geometry_is_rejected_only_by_explicit_strict_mode(rig, strict):
    assertion = _assertion()
    if strict is None:
        assertion.pop("strict_visible_text")
    else:
        assertion["strict_visible_text"] = strict
    result = _evaluate(rig, assertion, [{"t": "Text", "text": "expected", "b": [20, 790, 380, 830]}])
    assert result["result"] == ("undeterminable" if strict is True else "pass")


@pytest.mark.parametrize("typ", ["text_present", "text_present_any"])
def test_hidden_unexpected_text_makes_the_entire_scope_unverified(rig, typ):
    assertion = _assertion(type=typ, any=["expected"])
    result = _evaluate(rig, assertion, [
        {"t": "Text", "text": "expected", "b": [20, 70, 380, 100]},
        {"t": "Text", "text": "unexpected", "attrs": {"visible": " FALSE "}, "b": [20, 110, 380, 140]}])
    assert result["result"] == "undeterminable" and not result["verified"]
    assert "observed_holds" not in result


@pytest.mark.parametrize("change", ["integer", "string", "null", "no-within", "unknown-within", "inline-within",
                                    "absence", "target", "value", "seen", "transient", "visual"])
def test_invalid_strict_flags_fail_lint_runner_and_direct_verdict_before_actions(rig, monkeypatch, change):
    ws, knowledge, _ = rig
    case = build_case(ws.app(APP), _spec())
    assertion = _assertion(at="S_DISPLAY")
    if change in ("integer", "string", "null"):
        assertion["strict_visible_text"] = {"integer": 1, "string": "true", "null": None}[change]
    elif change == "no-within":
        assertion.pop("within")
    elif change == "unknown-within":
        assertion["within"] = "unregistered"
    elif change == "inline-within":
        assertion["within"] = _scope("parking_name")
    else:
        assertion["type"] = {"absence": "text_absent", "target": "target_present", "value": "value_present",
                             "seen": "text_seen", "transient": "transient_text_seen", "visual": "visual_review"}[change]
    case["assertions"] = [assertion]
    assert cases.assertion_scope_issues(case, ws.app(APP))
    def forbidden(*args, **kwargs):
        pytest.fail("invalid flag must fail before device, observation, or applicability")
    monkeypatch.setattr(ws, "device", forbidden)
    with pytest.raises(ValueError, match="执行结构无效"):
        run_case(ws, case=case, propose=False)
    assert not ws.runs_dir.exists()
    result = evaluate_assertion(assertion, {"S_DISPLAY": {"outcome": "passed", "final_obs": "synthetic",
                                                        "measurement": {"value": "expected"}}},
                                forbidden, knowledge, ws.app(APP))
    assert result["result"] == "error" and result["error_class"] == "invalid_assertion_scope"
    assert not result["verified"]


@pytest.mark.parametrize("strict", [None, False, True])
def test_any_matches_only_nodes_qualified_by_strict_visibility(rig, strict):
    assertion = _assertion(type="text_present_any", any=[" "])
    if strict is None:
        assertion.pop("strict_visible_text")
    else:
        assertion["strict_visible_text"] = strict
    result = _evaluate(rig, assertion, [
        {"t": "Text", "text": "normal", "b": [20, 70, 380, 100]},
        {"t": "Text", "text": " ", "b": [20, 790, 380, 830]}])
    assert result["result"] == ("fail" if strict is True else "pass")
