"""#111: current-run read values, synthetic only; no product rules/assets changed."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_condition_runner import APP, BUNDLE, COMMON_RULE, condition_rig
from vpdtest.cases import execution_structure_issues
from vpdtest.devices.replay import expand
from vpdtest.execute import Executor, _measure
from vpdtest.knowledge import KnowledgeStore
from vpdtest.locate import resolve
from vpdtest.params import Params
from vpdtest.readrefs import qualify, resolve_text
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.verdict import evaluate_assertion

__all__ = ("condition_rig",)


def _rig(rig, value="合成收藏42", result=None):
    ws, _, build = rig
    case = read_json(Path(__file__).parent / "fixtures" / "read-reference-case.json")
    directory = ws.root / "apps" / APP
    targets = read_json(directory / "targets.json")
    targets["targets"].update({
        "source": {"strategies": [{"kind": "id", "value": "source"}]},
        "query": {"side_effect": "navigation", "allow_input": True,
                  "strategies": [{"kind": "id", "value": "query"}]},
        "result": {"strategies": [{"kind": "id", "value": "result"}]},
    })
    write_json(directory / "targets.json", targets)
    path = Path(build().split(":", 1)[1])
    data = read_json(path)
    data["screens"]["dashboard"]["root"]["children"] += [
        {"t": "Text", "id": "source", "text": value, "b": [10, 440, 360, 480]},
        {"t": "SearchField", "id": "query", "bind": "query", "text_bound": "query", "b": [10, 500, 360, 550]},
        {"t": "Row", "id": "result", "b": [10, 600, 360, 680], "children": [
            {"t": "Text", "text": value if result is None else result, "b": [20, 610, 350, 650]}]},
    ]
    write_json(path, data)
    return ws, case, "replay:" + str(path)


def _dispatch(run, op="input_text"):
    return [e for e in read_jsonl(Path(run["dir"]) / "events.jsonl") if e["event"] == "dispatch" and e["op"] == op]


@pytest.mark.parametrize("value", ["合成收藏42", "第二账号B7", "字面${var:secret}-${read:OTHER}"])
def test_real_runner_binds_current_read_to_input_and_scoped_assertion(condition_rig, value):
    ws, case, spec = _rig(condition_rig, value)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["steps"][1]["read_binding"]["status"] == "available", run["steps"][1]
    assert run["steps"][2]["outcome"] == "passed", run["steps"][2]
    assert len(_dispatch(run)) == 1
    assertion = run["assertions"][0]
    assert assertion["result"] == "pass", assertion
    assert assertion["evidence"]["read_sources"][0]["step"] == "R1"
    assert assertion["evidence"]["read_sources"][0]["obs"] == run["steps"][1]["final_obs"]
    tree = read_json(Path(run["dir"]) / "obs" / run["steps"][2]["final_obs"] / "tree.json")
    assert any(n.text == value and n.attrs.get("id") == "query" for n in UiTree(tree).nodes)


def test_result_cannot_borrow_read_name_from_neighbour_row(condition_rig):
    ws, case, spec = _rig(condition_rig, result="wrong row")
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["assertions"][0]["result"] == "fail", run["assertions"]


def test_bound_text_keeps_unconfirmed_oracle_pending(condition_rig):
    ws, case, spec = _rig(condition_rig)
    path = ws.root / "knowledge" / "items.json"
    data = read_json(path)
    next(x for x in data["items"] if x["id"] == COMMON_RULE)["status"] = "unconfirmed"
    write_json(path, data)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["assertions"][0]["result"] == "pending"
    assert run["assertions"][0]["observed_holds"] is True


def _source(value="V", outcome="passed", **kwargs):
    return {"step_id": "R1", "action": "read", "outcome": outcome, "final_obs": "obs-1",
            "measurement": {"value": value}, "read_binding": {"status": "available", "obs": "obs-1", "value": value},
            **kwargs}


@pytest.mark.parametrize("source", [None, _source(None), _source(" "), _source(3), _source(outcome="skipped"),
                                    _source(outcome="pending"), _source(outcome="fail_candidate"),
                                    _source(read_binding={"status": "unavailable"}),
                                    _source(read_binding={"status": "available", "obs": "old", "value": "V"})])
def test_unavailable_current_source_never_observes_locates_clears_or_dispatches(source):
    step = {"id": "I1", "action": "input", "target": "query", "text": "${read:R1}"}
    app = SimpleNamespace(targets={"source": {"strategies": [{"kind": "id", "value": "source"}]}},
                          flows={}, states=SimpleNamespace(recipes={}))
    ctx = SimpleNamespace(app=app, safety_hold=None, read_case={"steps": [
        {"id": "R1", "action": "read", "read_scope": "self", "target": "source"}, step]},
        read_inputs={}, step_results={"R1": source} if source else {}, params=Params({"R1": "OLD"}, {}, "run"))
    ctx.observe = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not observe or prepare"))
    result = Executor(ctx).run_step(step, "case")
    assert result["outcome"] == "pending", result
    assert result["attempts"] == []


def test_failed_source_propagates_collection_error_before_any_device_action():
    source = _source(outcome="error", attribution="environment", execution_error={
        "stage": "observe_before", "error_class": "device_offline", "error": "synthetic offline"})
    binding = resolve_text("${read:R1}", {"R1": source})
    assert binding["status"] == "error"
    assert binding["execution_error"]["error_class"] == "device_offline"
    assert binding["execution_error"]["attribution"] == "environment"


@pytest.mark.parametrize("kind", ["text_present", "text_absent", "text_present_any"])
def test_bound_terminal_text_types_keep_same_source(kind, condition_rig):
    ws, case, spec = _rig(condition_rig)
    assertion = case["assertions"][0]
    assertion["type"] = kind
    if kind == "text_present_any":
        assertion["any"] = [assertion.pop("text"), "not present"]
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["assertions"][0]["result"] == ("fail" if kind == "text_absent" else "pass")


@pytest.mark.parametrize("change", ["forward", "nonread", "page", "malformed", "target", "within", "test_data", "cleanup", "flow"])
def test_invalid_refs_fail_runner_before_device_creation(condition_rig, monkeypatch, change):
    ws, case, spec = _rig(condition_rig)
    if change == "forward":
        case["steps"][1], case["steps"][2] = case["steps"][2], case["steps"][1]
    elif change == "nonread":
        case["steps"][1]["action"] = "wait"
    elif change == "page":
        case["steps"][1]["read_scope"] = "page"
    elif change == "malformed":
        case["steps"][2]["text"] = "${read:}"
    elif change == "target":
        case["steps"][2]["target"] = {"strategies": [{"kind": "xpath", "value": "//Text[@text='${read:R1}']"}]}
    elif change == "within":
        case["assertions"][0]["within"] = "${read:R1}"
    elif change == "test_data":
        case["test_data"]["sneak"] = "${read:R1}"
    elif change == "cleanup":
        case["cleanup"].append({"id": "C2", "action": "input", "text": "${read:R1}"})
    else:
        case["steps"][2] = {"id": "I1", "action": "flow", "flow": "sneak"}
        write_json(ws.root / "apps" / APP / "flows.json", {"flows": {"sneak": {"steps": [
            {"action": "input", "text": "${read:R1}", "target": "query"}]}}})
    monkeypatch.setattr(ws, "device", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must preflight first")))
    with pytest.raises(ValueError, match="read"):
        run_case(ws, case=case, device_spec=spec, propose=False)


def _qualified(*, text="one", pattern=None, hidden=False, clipped=False, scope="self"):
    child = {"t": "Text", "id": "read", "text": text, "b": [0, 0, 100, 80]}
    parent = {"t": "Column", "b": [0, 0, 100, 100], "children": [child]}
    if hidden:
        parent["attrs"] = {"visible": "false"}
    if clipped:
        parent["t"], parent["b"] = "Scroll", [0, 0, 100, 50]
    tree = UiTree(expand({"t": "Column", "b": [0, 0, 100, 100], "children": [parent]}, BUNDLE))
    loc = resolve("read", {"strategies": [{"kind": "id", "value": "read"}], "min_visible_fraction": 0.1}, tree, None, Path("."))
    step = {"id": "R1", "read_scope": scope, **({"pattern": pattern} if pattern is not None else {})}
    measurement = _measure(tree, loc, step)
    return qualify(tree, loc, step, {"obs_id": "o", "tree": {"status": "ok"}}, measurement)


@pytest.mark.parametrize("text,pattern,hidden,clipped,scope", [
    ("one one", "one", False, False, "self"), ("one", "X", False, False, "self"),
    ("one", "(?=o)", False, False, "self"), (" ", None, False, False, "self"),
    ("one", None, True, False, "self"), ("one", None, False, True, "self"),
    ("one", "one", False, False, "page"), ("one", None, False, False, "node"),
])
def test_nonunique_hidden_clipped_empty_or_page_read_is_not_binding_source(text, pattern, hidden, clipped, scope):
    assert _qualified(text=text, pattern=pattern, hidden=hidden, clipped=clipped, scope=scope)["status"] == "unavailable"


def test_read_data_is_not_recursively_interpreted_by_parameter_substitution():
    params = Params({"secret": "must-not-expand", "prefix": "P"}, {}, "run")
    raw = "${var:prefix}/${read:R1}"
    source = _source("${var:secret}/${read:OTHER}")
    result = resolve_text(raw, {"R1": source}, subst=params.subst)
    assert result["text"] == "P/${var:secret}/${read:OTHER}"


def test_parameter_cannot_introduce_a_read_reference():
    params = Params({"prefix": "${read:OTHER}"}, {}, "run")
    result = resolve_text("${var:prefix}/${read:R1}", {"R1": _source()}, subst=params.subst)
    assert result["status"] == "unknown"


@pytest.mark.parametrize("value,pattern", [(" ", None), ("one one", "one")])
def test_referenced_unusable_read_is_pending_and_stops_dependants(condition_rig, value, pattern):
    ws, case, spec = _rig(condition_rig, value)
    if pattern:
        case["steps"][1]["pattern"] = pattern
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    source = run["steps"][1]
    assert source["outcome"] == "pending", source
    assert source["final_status"] == "read_source_unknown"
    assert source["read_binding"]["reason"]
    assert run["steps"][2]["outcome"] == "not_run"
    assert _dispatch(run) == []
    assert run["verdict"]["product_verdict"] == "pending"


def test_missing_source_does_not_override_known_false_operation_branch(condition_rig):
    ws, case, spec = _rig(condition_rig)
    case["steps"][2]["when"] = {"condition": "healthy", "equals": True}
    case["conditions"] = {"healthy": {"kind": "page", "true": {"type": "text_present", "text": "never"},
                                      "false": {"type": "text_present", "text": "合成工作台"}}}
    case["steps"][1]["critical"] = False
    case["steps"][1]["pattern"] = "never"
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["steps"][2]["outcome"] == "skipped", run["steps"][2]
    assert _dispatch(run) == []


@pytest.mark.parametrize("outcome,expected", [("pending", "pending"), ("error", "error")])
def test_terminal_absence_cannot_pass_with_missing_or_error_read_source(condition_rig, outcome, expected):
    ws, case, _ = _rig(condition_rig)
    assertion = {**case["assertions"][0], "type": "text_absent"}
    source = _source(outcome=outcome, execution_error={"error_class": "device_offline", "stage": "observe_before"})
    steps = {"R1": source, "I1": {"outcome": "passed", "final_obs": "obs-2"}}
    obs = {"obs_id": "obs-2", "tree": {"status": "ok"}, "page": {"state": "dashboard"}}
    result = evaluate_assertion(assertion, steps, lambda _: (obs, UiTree(expand({"t": "Column", "b": [0, 0, 100, 100]}))),
                                KnowledgeStore(ws.root), ws.app(APP))
    assert result["result"] == expected, result
    assert result["verified"] is False


def test_direct_executor_cannot_trust_invalid_forward_declaration():
    step = {"id": "I1", "action": "input", "target": "query", "text": "${read:R1}"}
    app = SimpleNamespace(targets={"source": {"strategies": [{"kind": "id", "value": "source"}]}},
                          flows={}, states=SimpleNamespace(recipes={}))
    ctx = SimpleNamespace(app=app, safety_hold=None, read_case={"steps": [step,
        {"id": "R1", "action": "read", "read_scope": "self", "target": "source"}]},
        read_inputs={}, step_results={"R1": _source()}, params=None)
    ctx.observe = lambda *a, **k: (_ for _ in ()).throw(AssertionError("invalid before observe"))
    result = Executor(ctx).run_step(step, "case")
    assert result["final_status"] == "invalid_step", result


def test_same_source_cannot_be_its_own_assertion_time(condition_rig):
    ws, case, _ = _rig(condition_rig)
    case["assertions"][0]["at"] = "R1"
    assert any("更早" in p["problem"] for p in execution_structure_issues(case, ws.app(APP)))


def test_read_any_mixed_with_nontext_is_invalid_before_execution(condition_rig):
    ws, case, _ = _rig(condition_rig)
    case["assertions"][0].update(type="text_present_any", any=["${read:R1}", None])
    case["assertions"][0].pop("text")
    assert any("any" in p["field"] for p in execution_structure_issues(case, ws.app(APP)))


def test_new_read_contract_excludes_old_literal_template_replay_without_changing_plain_deps(condition_rig, monkeypatch):
    from vpdtest import readrefs
    from vpdtest.cases import content_hash, dependency_hash, replay_basis
    ws, case, _ = _rig(condition_rig)
    app, knowledge = ws.app(APP), KnowledgeStore(ws.root)
    current = dependency_hash(case, app, knowledge)
    plain = {**case, "steps": [{**s, "text": "static"} if s["action"] == "input" else s for s in case["steps"]],
             "assertions": [{**case["assertions"][0], "text": "static"}]}
    plain_current = dependency_hash(plain, app, knowledge)
    with monkeypatch.context() as patch:
        patch.setattr(readrefs, "has_read_ref", lambda _: False)
        old = dependency_hash(case, app, knowledge)
        assert dependency_hash(plain, app, knowledge) == plain_current
    assert old != current
    # Synthetic historical summaries deliberately model the old literal-template
    # contract; they are not new real-device or current-engine replay evidence.
    history = {**case, "replays": [{"run_id": f"old-{i}", "hash": content_hash(case), "deps": old,
                                    "verdict": "passed"} for i in range(2)]}
    assert replay_basis(history, old)["ok"]
    assert replay_basis(history, current)["count"] == 0
    assert not replay_basis(history, current)["ok"]


def test_selected_conditional_input_cannot_downgrade_missing_source_to_noncritical(condition_rig):
    ws, case, spec = _rig(condition_rig)
    case["conditions"] = {"selected": {"kind": "page", "true": {"type": "text_present", "text": "合成工作台"},
                                       "false": {"type": "text_present", "text": "独立未选中"}}}
    case["steps"][1].update(critical=False, pattern="never")
    case["steps"][2].update(critical=False, when={"condition": "selected", "equals": True})
    case["steps"].append({"id": "AFTER", "action": "tap", "target": "advance", "retry": 0})
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["steps"][2]["outcome"] == "pending", run["steps"][2]
    assert run["steps"][2]["critical"] is True
    assert run["steps"][3]["outcome"] == "not_run"
    assert _dispatch(run, "tap") == []
    assert _dispatch(run) == []


@pytest.mark.parametrize("change,expected", [("missing", "pending"), ("ambiguous", "pending"), ("bad_xpath", "error")])
def test_referenced_source_locator_unknown_and_error_remain_distinct(condition_rig, change, expected):
    ws, case, spec = _rig(condition_rig)
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    children = data["screens"]["dashboard"]["root"]["children"]
    if change == "missing":
        children[:] = [n for n in children if n.get("id") != "source"]
    elif change == "ambiguous":
        children.append({"t": "Text", "id": "source", "text": "second", "b": [10, 700, 360, 740]})
    else:
        target_path = ws.root / "apps" / APP / "targets.json"
        targets = read_json(target_path)
        targets["targets"]["source"]["strategies"] = [{"kind": "xpath", "value": "//*["}]
        write_json(target_path, targets)
    write_json(path, data)
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["steps"][1]["outcome"] == expected, run["steps"][1]
    assert run["steps"][2]["outcome"] == "not_run"
    assert _dispatch(run) == []
