"""#56: evidence qualification only; synthetic trees/Runner, no product confirmation."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_condition_runner import APP, FAULT, HEALTHY, condition_rig
from vpdtest.cases import dependency_hash, replay_basis
from vpdtest.conditions import evaluate_guard, validate_definitions
from vpdtest.devices.replay import expand
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.states import StateModel
from vpdtest.tree import UiTree
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.verdict import _check

__all__ = ("condition_rig",)


def _node(text="", ident="", box=(10, 10, 190, 50), **extra):
    return {"t": "Text", "text": text, "id": ident, "b": list(box), **extra}


def _tree(*children):
    return UiTree(expand({"t": "Column", "b": [0, 0, 400, 400], "children": list(children)}, "bundle"))


def _wrapped(node, mode):
    if mode == "hidden":
        return {"t": "Column", "b": [0, 0, 400, 200], "attrs": {"visible": "false"}, "children": [node]}
    if mode == "clipped":
        return {"t": "Scroll", "b": [0, 0, 400, 35], "children": [node]}
    return node


def _app(targets=None, states=None):
    targets = targets or {"identity": {"strategies": [{"kind": "id", "value": "identity"}]},
                          "proof": {"strategies": [{"kind": "id", "value": "proof"}]}}
    model = StateModel({"states": states or {}}, "bundle", targets)
    return SimpleNamespace(id="fixture", dir=Path("."), targets=targets, states=model,
                           target=lambda ref: (ref, targets[ref]))


def _obs(oid="current", page=None, layout=None):
    return {"obs_id": oid, "fresh": True, "tree": {"status": "ok"},
            "page": page or {"state": "page"}, "layout": layout or {},
            "scope": {"app": "fixture", "app_version": "1.0"}, "action_epoch": 0, "clock_s": 1.0}


def _defs(check=None, kind="page", negative=None):
    return {"ready": {"kind": kind,
                      "true": check or {"type": "text_present", "text": "READY"},
                      "false": negative or {"type": "text_present", "text": "NOT_READY"},
                      **({"scope": {"vehicle": "identity"}} if kind == "vehicle" else {})}}


def _guard(defs, tree, app=None, *, source=None):
    obs = _obs()
    if source is None:
        return evaluate_guard({"condition": "ready", "equals": True}, defs, {}, None, app or _app(), obs, tree)
    return evaluate_guard({"condition": "ready", "equals": True, "at": "S1"}, defs,
                          {"S1": {"outcome": "passed", "final_obs": "source"}},
                          lambda _: (_obs("source"), source), app or _app(), obs, tree)


@pytest.mark.parametrize("kind", ["text_present", "text_present_any", "target_present"])
@pytest.mark.parametrize("mode", ["hidden", "clipped"])
@pytest.mark.parametrize("side", ["true", "false"])
def test_hidden_or_clipped_text_is_neither_positive_nor_independent_counterproof(kind, mode, side):
    text = "READY" if side == "true" else "NOT_READY"
    check = {"type": kind, **({"target": "proof"} if kind == "target_present" else
                             {"any": [text]} if kind == "text_present_any" else {"text": text})}
    defs = _defs()
    defs["ready"][side] = check
    result = _guard(defs, _tree(_wrapped(_node(text, "proof"), mode)))
    assert result["status"] == "unknown", result
    assert result["value"] is None


@pytest.mark.parametrize("mode", ["hidden", "clipped", "missing", "duplicate", "root"])
@pytest.mark.parametrize("where", ["source", "current"])
def test_identity_requires_unique_nonroot_full_visible_source_and_current_nodes(mode, where):
    good = _tree(_node("CAR", "identity"), _node("READY", box=(10, 230, 190, 270)))
    children = [] if mode == "missing" else [_wrapped(_node("CAR", "identity"), mode)]
    if mode == "duplicate":
        children.append(_node("OTHER", "identity", box=(210, 10, 390, 50)))
    bad = _tree(*children, _node("READY", box=(10, 230, 190, 270)))
    if mode == "root":
        bad.raw["attributes"].update(id="identity", text="CAR")
        bad = UiTree(bad.raw)
        # Root is the only matching node.
        bad.raw["children"] = bad.raw["children"][-1:]
        bad = UiTree(bad.raw)
    result = _guard(_defs(kind="vehicle"), bad if where == "current" else good,
                    source=bad if where == "source" else good)
    assert result["status"] == "unknown", result


@pytest.mark.parametrize("which", ["identity", "proof"])
def test_xpath_parse_error_is_error_rather_than_missing_evidence(which):
    app = _app()
    app.targets[which]["strategies"] = [{"kind": "xpath", "value": "//*["}]
    defs = _defs(kind="vehicle") if which == "identity" else _defs({"type": "target_present", "target": "proof"})
    result = _guard(defs, _tree(_node("CAR", "identity"), _node("READY", "proof")), app)
    assert result["status"] == "error", result
    assert result["execution_error"]["attribution"] == "tool"
    assert result["execution_error"]["error_class"] == "condition_locator_error"
    assert result["execution_error"]["stage"] == ("condition_identity" if which == "identity" else "condition_evidence")


@pytest.mark.parametrize("which", ["identity", "proof"])
def test_current_frame_locator_error_cannot_be_hidden_by_valid_historical_proof(which, monkeypatch):
    app = _app()
    app.targets[which]["strategies"] = [{"kind": "xpath", "value": f"//*[@id='{which}']"}]
    source = _tree(_node("CAR", "identity"), _node("READY", "proof", box=(10, 230, 190, 270)))
    current = _tree(_node("CAR", "identity"), _node("READY", "proof", box=(10, 230, 190, 270)))
    def fail(_):
        raise ValueError("synthetic current-frame selector parse failure")
    monkeypatch.setattr(current, "xpath", fail)
    defs = _defs(kind="vehicle") if which == "identity" else _defs({"type": "target_present", "target": "proof"})
    result = _guard(defs, current, app, source=source)
    assert result["status"] == "error", result
    assert result["execution_error"]["error_class"] == "condition_locator_error"
    assert "synthetic current-frame" in str(result["execution_error"])


def test_identity_cannot_accept_one_pixel_clipping_even_if_locator_fraction_rounds_to_one():
    from vpdtest.locate import resolve
    raw = {"t": "Column", "b": [0, 0, 400, 4000], "children": [
        {"t": "Scroll", "b": [0, 0, 400, 3009], "children": [_node("CAR", "identity", box=(10, 10, 190, 3010))]},
        _node("READY", box=(10, 3500, 190, 3550))]}
    tree, app = UiTree(expand(raw, "bundle")), _app()
    located = resolve("identity", app.targets["identity"], tree, None, Path("."))
    assert located["status"] == "found" and located["visible_fraction"] == 1.0
    assert _guard(_defs(kind="vehicle"), tree, app)["status"] == "unknown"


@pytest.mark.parametrize("definition", ["scope", "target_present"])
@pytest.mark.parametrize("strategy", [{"kind": "point", "value": [1, 2]}, {"kind": "template", "file": "x.png"},
                                      {"kind": "id", "value": " "}])
def test_condition_targets_cannot_be_unverifiable_non_tree_sources(definition, strategy):
    app = _app()
    app.targets["identity"]["strategies"] = [strategy]
    defs = _defs(kind="vehicle") if definition == "scope" else _defs({"type": "target_present", "target": "identity"})
    assert validate_definitions(defs, app)


def test_condition_qualification_does_not_change_historical_whole_page_assertions():
    tree = _tree(_wrapped(_node("READY"), "hidden"))
    obs = _obs()
    assert _check({"type": "text_present", "text": "READY"}, obs, tree, _app())[0] is True
    assert _guard(_defs(), tree)["status"] == "unknown"


@pytest.mark.parametrize("signature", [{"require_text": ["READY"]}, {"require_any_text": ["READY"]},
                                       {"require_text_pattern": ["^READY$"]},
                                       {"require_selected_text": ["READY"]}])
def test_state_conditions_recheck_visible_positive_signature_without_changing_global_identification(signature):
    app = _app(states={"ready": signature, "not_ready": {"require_text": ["NOT_READY"]}})
    tree = _tree(_wrapped(_node("READY", attrs={"selected": "true"}), "hidden"))
    page = app.states.identify(tree, "bundle", True)
    assert page["state"] == "ready"  # Existing identification intentionally retains its old semantics.
    obs = _obs(page=page)
    defs = _defs({"type": "state_is", "state": "ready"}, negative={"type": "state_is", "state": "not_ready"})
    result = evaluate_guard({"condition": "ready", "equals": True}, defs, {}, None, app, obs, tree)
    assert result["status"] == "unknown", result


@pytest.mark.parametrize("state", ["ready", "unknown", "ambiguous"])
def test_state_proof_does_not_promote_uncertain_page_or_use_pure_negative_signature(state):
    app = _app(states={"ready": {"forbid_text": ["NOT_READY"]}, "not_ready": {"require_text": ["NOT_READY"]}})
    defs = _defs({"type": "state_is", "state": "ready"}, negative={"type": "state_is", "state": "not_ready"})
    result = evaluate_guard({"condition": "ready", "equals": True}, defs, {}, None, app,
                            _obs(page={"state": state}), _tree(_node("unrelated")))
    assert result["status"] == "unknown", result


def test_state_condition_cannot_join_signature_text_across_panes():
    app = _app(states={"ready": {"require_text": ["LEFT", "RIGHT"]},
                       "not_ready": {"require_text": ["NOT_READY"]}})
    tree = _tree({"t": "Column", "b": [0, 0, 200, 400], "children": [_node("LEFT")]},
                 {"t": "Column", "b": [200, 0, 400, 400], "children": [_node("RIGHT", box=(210, 10, 390, 50))]})
    panes = [{"index": 0, "node_idx": 1}, {"index": 1, "node_idx": 3}]
    page = {"state": "ready", "active": ["ready"], "panes": [dict(p, candidates=[]) for p in panes]}
    defs = _defs({"type": "state_is", "state": "ready"}, negative={"type": "state_is", "state": "not_ready"})
    result = evaluate_guard({"condition": "ready", "equals": True}, defs, {}, None, app,
                            _obs(page=page, layout={"panes": panes}), tree)
    assert result["status"] == "unknown", result


@pytest.mark.parametrize("reported", ["current", "stale", "duplicated"])
def test_state_condition_reuses_current_pane_ownership(reported):
    app = _app(states={"ready": {"require_text": ["READY"]}, "not_ready": {"require_text": ["NOT_READY"]}})
    tree = _tree({"t": "Column", "b": [0, 0, 200, 400], "children": [_node("READY")]},
                 {"t": "Column", "b": [200, 0, 400, 400], "children": [_node("OTHER", box=(210, 10, 390, 50))]})
    panes = [{"index": 0, "node_idx": 1}, {"index": 1, "node_idx": 3}]
    page = {"state": "ready", "active": ["ready"], "panes": [dict(p, candidates=["ready"] if i == 0 else [])
                                                             for i, p in enumerate(panes)]}
    if reported == "stale":
        page["panes"][0]["node_idx"] = 0
    elif reported == "duplicated":
        page["panes"].append(page["panes"][0])
    defs = _defs({"type": "state_is", "state": "ready"}, negative={"type": "state_is", "state": "not_ready"})
    result = evaluate_guard({"condition": "ready", "equals": True}, defs, {}, None, app,
                            _obs(page=page, layout={"panes": panes}), tree)
    assert result["status"] == ("true" if reported == "current" else "unknown"), result


def _runner_case(rig, mode):
    ws, template, build = rig
    case = deepcopy(template)
    case["steps"].append({"id": "S2", "action": "tap", "target": "advance", "retry": 0,
                          "when": {"condition": "fault", "equals": True}})
    spec = build(markers=(HEALTHY if mode == "counter_hidden" else FAULT,))
    path = Path(spec.split(":", 1)[1])
    world = read_json(path)
    children = world["screens"]["dashboard"]["root"]["children"]
    if mode in ("positive_hidden", "counter_hidden"):
        index = next(i for i, n in enumerate(children) if n.get("text") in (FAULT, HEALTHY))
        children[index] = {"t": "Column", "b": [0, 0, 400, 300], "attrs": {"visible": "false"},
                           "children": [children[index]]}
    else:
        targets_path = ws.root / "apps" / APP / "targets.json"
        targets = read_json(targets_path)
        targets["targets"]["identity"] = {"strategies": [{"kind": "id", "value": "identity"}]}
        case["conditions"]["fault"].update(kind="vehicle", scope={"vehicle": "identity"})
        children.append({"t": "Column", "b": [0, 440, 400, 500], "attrs": {"visible": "false"},
                         "children": [_node("CAR", "identity", box=(10, 450, 190, 490))]})
        if mode == "locator_error":
            targets["targets"]["identity"]["strategies"] = [{"kind": "xpath", "value": "//*["}]
        write_json(targets_path, targets)
    write_json(path, world)
    return ws, case, spec


@pytest.mark.parametrize("mode,expected", [("positive_hidden", "pending"), ("counter_hidden", "pending"),
                                           ("identity_hidden", "pending"), ("locator_error", "error")])
def test_actual_runner_never_dispatches_from_hidden_or_erroneous_condition_source(condition_rig, mode, expected):
    ws, case, spec = _runner_case(condition_rig, mode)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    result = run["steps"][1]
    assert result["outcome"] == expected, result
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert not [e for e in events if e["event"] == "dispatch" and e["op"] == "tap"]
    if mode in ("positive_hidden", "counter_hidden"):
        assert run["assertions"][1]["result"] == "pending"
        assert "A-fault" in run["verdict"]["coverage"]["unverified"]
    if expected == "error":
        assert result["execution_error"]["stage"] == "condition_identity"
        assert result["execution_error"]["attribution"] == "tool"


@pytest.mark.parametrize("consumer", ["assertion_guard", "step_when", "oracle_guard"])
def test_only_used_condition_contract_invalidates_old_replays(condition_rig, monkeypatch, consumer):
    from vpdtest import cases
    ws, case, _ = condition_rig
    app, kn = ws.app(APP), KnowledgeStore(ws.root)
    if consumer != "assertion_guard":
        case["assertions"][1].pop("guard")
        if consumer == "step_when":
            case["steps"].append({"id": "S2", "action": "wait", "when": {"condition": "fault", "equals": True}})
        else:
            items = {"items": kn.items()}
            rule = case["assertions"][1]["oracle_refs"][0]
            next(item for item in items["items"] if item["id"] == rule)["condition_requirements"] = {"fault": True}
            write_json(kn.path, items)
    current = dependency_hash(case, app, kn)
    plain = deepcopy(case)
    plain.pop("conditions")
    plain["steps"] = plain["steps"][:1]
    plain["assertions"] = [plain["assertions"][0]]
    plain_current = dependency_hash(plain, app, kn)
    original = cases.sha256_obj
    def without_new_contract(value):
        return original({k: v for k, v in value.items() if k != "condition_evidence_contract"})
    monkeypatch.setattr(cases, "sha256_obj", without_new_contract)
    old = dependency_hash(case, app, kn)
    assert old != current
    assert dependency_hash(plain, app, kn) == plain_current
    coverage = {"assertions": [{"id": a["id"], "result": "pass", "verified": True, "applicable": True}
                               for a in case["assertions"]]}
    history = {**case, "replays": [{"run_id": f"old-{i}", "hash": cases.content_hash(case), "deps": old,
                                    "verdict": "passed", "coverage": coverage} for i in range(2)]}
    assert replay_basis(history, old)["ok"]
    assert not replay_basis(history, current)["ok"]
