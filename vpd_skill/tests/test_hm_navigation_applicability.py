"""#245 migration proof only: isolated synthetic app, never a deployable HM asset.

The condition selects an observed navigation variant, not account identity.
Selection uses tab presence; product checks use selected state/transition, not
that same presence as a circular success proof. No production data are changed.
Current limitation: skipped required S2 halts S3, so its A2 cannot be selected
without an observation. The buy variant remains pending, NOT fully migrated.
"""
from copy import deepcopy
from pathlib import Path
import pytest
from test_condition_runner import condition_rig, APP, BUNDLE
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.runner import run_case
from vpdtest.knowledge import KnowledgeStore

NAV_RULE = "K-PYTEST-NAVIGATION-245"
from vpdtest.util import write_json, read_jsonl


@pytest.fixture
def navigation(condition_rig, tmp_path):
    ws, base, _ = condition_rig
    app = ws.app(APP).dir
    knowledge = KnowledgeStore(ws.root)
    knowledge.add({"id": NAV_RULE, "kind": "product_rule", "subject": "pytest.navigation.245",
                   "statement": "Synthetic only: selecting a tab changes page and selected tab",
                   "expected": "selected page follows the clicked synthetic tab",
                   "applies_to": {"app": APP, "app_version": "1.0.0"},
                   "source": {"type": "reviewer", "ref": "test_hm_navigation_applicability.py synthetic contract"}})
    knowledge.confirm(NAV_RULE, reviewer="pytest-only", basis="Synthetic engine contract, not product approval")
    targets = {name: {"side_effect": "navigation", "strategies": [
        {"kind": "text", "value": label, "within": {"type": "TabBar"}}]}
        for name, label in (("tab_my", "我的"), ("tab_car", "爱车"), ("tab_buy", "购车"))}
    write_json(app / "targets.json", {"targets": targets})
    write_json(app / "states.json", {"states": {
        "my": {"require_text": ["我的页面"]}, "car": {"require_text": ["爱车页面"]}},
        "interrupts": {}, "recipes": {}})
    guard = {"condition": "car_navigation_variant", "equals": True}
    case = deepcopy(base)
    case["conditions"] = {"car_navigation_variant": {"kind": "page",
        "true": {"type": "target_present", "target": "tab_car"},
        "false": {"type": "target_present", "target": "tab_buy"}}}
    case["steps"] = [
        {"id": "OPEN", "action": "launch", "expect_state": "my", "retry": 0},
        {"id": "S1", "action": "ensure_state", "state": "my"},
        {"id": "S2", "action": "tap", "target": "tab_car", "expect_state": "car",
         "when": guard, "timeout_s": 0, "retry": 0, "oracle_refs": [NAV_RULE]},
        {"id": "S3", "action": "tap", "target": "tab_my", "expect_state": "my",
         "when": guard, "timeout_s": 0, "retry": 0, "oracle_refs": [NAV_RULE]}]
    case["assertions"] = [
        {"id": "A1", "at": "S2", "type": "node_selected", "target": "tab_my", "expected": False,
         "guard": guard, "oracle_refs": [NAV_RULE]},
        {"id": "A2", "at": "S3", "type": "node_selected", "target": "tab_my", "expected": True,
         "guard": guard, "oracle_refs": [NAV_RULE]}]
    case["cleanup"] = []

    def build(variant="car", *, broken=False, after=None):
        def screen(page, markers):
            tabs = []
            for name in ("my", *(["car"] if markers in ("car", "both") else []),
                         *(["buy"] if markers in ("buy", "both") else [])):
                i = len(tabs)
                label = {"my": "我的", "car": "爱车", "buy": "购车"}[name]
                go = "my" if name == "my" else ("my" if broken else "car")
                tabs.append({"t": "Column", "b": [i*120, 700, i*120+110, 780],
                    "click": True, "go": go, "attrs": {"selected": str(name == page).lower()},
                    "children": [{"t": "Text", "text": label, "b": [i*120+10, 720, i*120+100, 750],
                                  "attrs": {"selected": "false"}}]})
            return {"root": {"t": "Column", "b": [0, 0, 400, 800], "children": [
                {"t": "Text", "text": "我的页面" if page == "my" else "爱车页面", "b": [0, 20, 350, 70]},
                {"t": "TabBar", "b": [0, 700, 400, 800], "children": tabs}]}}
        path = tmp_path / "navigation-world.json"
        write_json(path, {"bundle": BUNDLE, "app_version": "1.0.0", "screen": [400, 800],
            "display": {"density": 1, "device_form": "bar"}, "initial": {"installed": True},
            "device": {"model": "SYNTHETIC-ONLY", "os": "Replay"}, "launch_screen": "my",
            "screens": {"my": screen("my", variant), "car": screen("car", after or variant)}})
        return "replay:" + str(path)
    return ws, case, build


def execute(navigation, **world):
    ws, case, build = navigation
    return run_case(ws, case=case, device_spec=build(**world), propose=False)


def taps(run):
    return [e for e in read_jsonl(Path(run["dir"]) / "events.jsonl")
            if e.get("event") == "dispatch" and e.get("action") == "tap"]


@pytest.mark.parametrize("variant,verdict,assertions,tap_count", [
    ("car", "passed", ["pass", "pass"], 2),
    ("buy", "pending", ["not_applicable", "not_run"], 0),
    ("neither", "pending", ["pending", "not_run"], 0),
    ("both", "pending", ["pending", "not_run"], 0)])
def test_observed_variant_selects_without_inventing_account_fact(navigation, variant, verdict, assertions, tap_count):
    run = execute(navigation, variant=variant)
    assert run["verdict"]["verdict"] == verdict, run["verdict"]
    assert [a["result"] for a in run["assertions"]] == assertions
    if variant == "buy":
        # Do not weaken S2 criticality or fabricate S3 observations to turn green.
        last = next(s for s in run["steps"] if s["step_id"] == "S3")
        assert last["final_status"] == "required_step_skipped"
        assert not last.get("final_obs")
        assert run["verdict"]["attribution"] == "requirement_unclear"
    assert len(taps(run)) == tap_count
    assert next(s for s in run["steps"] if s["step_id"] == "S1")["attempts"] == []
    assert "account_state" not in run["declarations"]["values"]


def test_present_car_tab_does_not_make_broken_transition_pass(navigation):
    run = execute(navigation, broken=True)
    assert run["verdict"]["product_failed"] is True, run["verdict"]
    assert run["verdict"]["product_verdict"] == "failed"
    assert run["verdict"]["verdict"] == "failed"
    assert len(taps(run)) == 1


def test_condition_is_reobserved_after_navigation_not_carried_across_page(navigation):
    run = execute(navigation, after="neither")
    assert run["verdict"]["verdict"] == "pending", run["verdict"]
    assert len(taps(run)) == 1
    assert not any(a["result"] in ("pass", "not_applicable") for a in run["assertions"])
    assert next(s for s in run["steps"] if s["step_id"] == "S3")["outcome"] == "pending"


def test_tree_collection_failure_is_not_inapplicable(navigation, monkeypatch):
    original = ReplayDevice.dump_tree
    def fail(self, bundle, path):
        if self._in_app():
            return DeviceResult("dump_tree", True, False, "timeout", "synthetic navigation tree failure")
        return original(self, bundle, path)
    monkeypatch.setattr(ReplayDevice, "dump_tree", fail)
    run = execute(navigation)
    assert run["verdict"]["verdict"] == "error"
    assert not taps(run)
    assert not any(a["result"] in ("pass", "not_applicable") for a in run["assertions"])
