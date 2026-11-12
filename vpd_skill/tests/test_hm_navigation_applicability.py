"""#245 migration proof only: isolated synthetic app, never a deployable HM asset.

The condition selects an observed navigation variant, not account identity.
Selection uses tab presence; product checks use selected state/transition, not
that same presence as a circular success proof. No production data are changed.
Cancelled S3 stays not_run, while a separate current observation may prove
its explicitly matching assertion inapplicable. This is not product coverage.
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
    ("buy", "not_applicable", ["not_applicable", "not_applicable"], 0),
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
        assert last["outcome"] == "not_run"
        assert last["applicability_evidence"]["cancelled_obs"] != last["applicability_evidence"]["source_obs"]
        assert run["verdict"]["coverage"]["verified"] == []
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


@pytest.mark.parametrize("mismatch", ["missing", "condition", "equals", "at", "operation"])
def test_cancelled_guard_must_match_explicit_selection(navigation, mismatch):
    ws, case, _ = navigation
    guard = case["assertions"][1]["guard"] = deepcopy(case["assertions"][1]["guard"])
    if mismatch == "missing": case["assertions"][1].pop("guard")
    elif mismatch == "equals": guard["equals"] = False
    elif mismatch == "at": guard["at"] = "S1"
    elif mismatch == "operation": case["steps"][-1].pop("when")
    else:
        case["conditions"]["other"] = deepcopy(case["conditions"]["car_navigation_variant"])
        guard["condition"] = "other"
    run = execute(navigation, variant="buy")
    assert run["verdict"]["verdict"] == "pending"
    assert run["assertions"][1]["result"] == "not_run"
    assert not taps(run)
    assert "applicability_evidence" not in run["steps"][-1]


@pytest.mark.parametrize("change", ["expired", "action", "scope"])
def test_cancellation_rechecks_age_epoch_and_scope(navigation, monkeypatch, change):
    from vpdtest.workspace import RunContext
    ws, case, _ = navigation
    case["conditions"]["car_navigation_variant"]["max_age_s"] = 1
    original = RunContext.observe
    def observe(self, label="", *args, **kwargs):
        if label.endswith(":cancelled-applicability"):
            if change == "expired": self.sleep(2)
            elif change == "scope": self.facts["app_version"] = "changed-build"
            else: self.dispatcher.send("tap", 390, 100, preconditions=self.dispatcher.app_conditions("tap"))
        return original(self, label, *args, **kwargs)
    monkeypatch.setattr(RunContext, "observe", observe)
    run = execute(navigation, variant="buy")
    assert run["assertions"][1]["result"] == "not_run"
    assert run["verdict"]["verdict"] == "pending"


def test_cleanup_does_not_rewrite_precleanup_applicability(navigation):
    navigation[1]["cleanup"] = [{"id": "C1", "action": "stop_app"}]
    run = execute(navigation, variant="buy")
    assert run["verdict"]["verdict"] == "not_applicable"
    assert run["cleanup"][0]["outcome"] == "passed"
    assert all(not a["verified"] for a in run["assertions"])
    assert run["steps"][-1]["outcome"] == "not_run"


def test_unconditional_required_successor_is_not_exempted(navigation):
    navigation[1]["steps"].append({"id": "S4", "action": "wait", "duration_s": 0})
    run = execute(navigation, variant="buy")
    assert run["verdict"]["verdict"] == "pending"
    assert run["steps"][-1]["final_status"] == "required_step_skipped"
    assert "applicability_evidence" not in run["steps"][-1]


def test_one_unguarded_assertion_keeps_cancelled_step_pending(navigation):
    a = deepcopy(navigation[1]["assertions"][1]); a["id"] = "A3"; a.pop("guard")
    navigation[1]["assertions"].append(a)
    run = execute(navigation, variant="buy")
    assert [a["result"] for a in run["assertions"]] == ["not_applicable", "not_applicable", "not_run"]
    assert run["verdict"]["verdict"] == "pending"


def test_cancellation_observation_error_is_retained(navigation, monkeypatch):
    from vpdtest.workspace import RunContext
    original = RunContext.observe
    def observe(self, label="", *args, **kwargs):
        if label.endswith(":cancelled-applicability"):
            monkeypatch.setattr(self.device, "dump_tree", lambda *a: DeviceResult(
                "dump_tree", True, False, "timeout", "cancelled observation failed"))
        return original(self, label, *args, **kwargs)
    monkeypatch.setattr(RunContext, "observe", observe)
    run = execute(navigation, variant="buy")
    assert run["assertions"][1]["result"] == "error"
    assert run["verdict"]["verdict"] == "error"
    assert not taps(run)


def test_cancellation_observation_interrupt_is_not_swallowed(navigation, monkeypatch):
    from vpdtest.workspace import RunContext
    original = RunContext.observe
    def observe(self, label="", *args, **kwargs):
        if label.endswith(":cancelled-applicability"): raise KeyboardInterrupt("synthetic cancelled observation")
        return original(self, label, *args, **kwargs)
    monkeypatch.setattr(RunContext, "observe", observe)
    with pytest.raises(KeyboardInterrupt): execute(navigation, variant="buy")



def test_account_identity_change_between_false_and_cancellation_is_not_reused(navigation, monkeypatch):
    from vpdtest.workspace import RunContext
    from vpdtest.util import read_json
    ws, case, build = navigation
    path = ws.app(APP).dir / "targets.json"
    targets = read_json(path)
    targets["targets"]["identity"] = {"strategies": [{"kind": "text", "value": "user-", "exact": False}]}
    write_json(path, targets)
    case["conditions"]["car_navigation_variant"].update(kind="account", scope={"account": "identity"})
    spec = build("buy")
    world_path = Path(spec.split(":", 1)[1]); world = read_json(world_path)
    for screen in world["screens"].values():
        screen["root"]["children"].append({"t": "Text", "text": "user-A", "b": [0, 150, 200, 190]})
    write_json(world_path, world)
    original = RunContext.observe
    def observe(self, label="", *args, **kwargs):
        if label.endswith(":cancelled-applicability"):
            self.device.world["screens"]["my"]["root"]["children"][-1]["text"] = "user-B"
        return original(self, label, *args, **kwargs)
    monkeypatch.setattr(RunContext, "observe", observe)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["assertions"][0]["result"] == "not_applicable"
    assert run["assertions"][1]["result"] == "not_run"
    assert run["verdict"]["verdict"] == "pending"


@pytest.mark.parametrize("reason", ["attempted", "safety_hold"])
def test_only_entry_false_without_safety_hold_can_be_transferred(navigation, monkeypatch, reason):
    from vpdtest.execute import Executor
    original = Executor.run_step
    def step(self, operation, case_id):
        result = original(self, operation, case_id)
        if operation["id"] == "S2":
            if reason == "attempted": result["attempts"] = [{"dispatch": {"sent": True}}]
            else: self.ctx.safety_hold = {"reason": "synthetic safety hold"}
        return result
    monkeypatch.setattr(Executor, "run_step", step)
    run = execute(navigation, variant="buy")
    assert run["assertions"][1]["result"] == "not_run"
    assert "applicability_evidence" not in run["steps"][-1]
    assert run["verdict"]["verdict"] != "not_applicable"
