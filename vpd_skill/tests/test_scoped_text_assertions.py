"""Synthetic row boundaries; no production calibration, oracle or asset changes."""
import pytest
from conftest import confirm
from verdict_fixtures import COPY_CASE, COPY_RULE

from vpdtest import cases
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.util import write_json
from vpdtest.verdict import evaluate_assertion


def _node(typ, bounds, children=(), **attrs):
    return {"attributes": {"type": typ, "bounds": bounds, **attrs}, "children": list(children)}


def _row(name, number, y=20, identity="favorite-row", **attrs):
    return _node("Row", f"[0,{y}][400,{y+80}]", [
        _node("Text", f"[10,{y+10}][190,{y+40}]", text=name),
        _node("Text", f"[210,{y+10}][390,{y+40}]", text=number),
    ], id=identity, **attrs)


def _tree(*rows):
    return UiTree(_node("Column", "[0,0][400,400]", rows))


@pytest.fixture
def rig(verdict_ws):
    app = verdict_ws.app("motor_hm")
    app.targets["favorite_row"] = {"strategies": [{"kind": "id", "value": "favorite-row"}]}
    knowledge = KnowledgeStore(verdict_ws.root)
    knowledge.bind({"app": app.id, "account_state": "logged_out"})
    return verdict_ws, app, knowledge


def _assertion(typ="text_present", **values):
    return {"id": "A1", "at": "S1", "type": typ, "within": "favorite_row",
            "text": "A138", "oracle_refs": [COPY_RULE], **values}


def _evaluate(rig, ui_tree, assertion=None, **observation):
    _, app, knowledge = rig
    obs = {"obs_id": "synthetic-obs", "fresh": True, "tree": {"status": "ok"},
           "page": {"state": "unknown"}, **observation}
    return evaluate_assertion(assertion or _assertion(), {"S1": {"outcome": "passed", "final_obs": obs["obs_id"]}},
                              lambda _: (obs, ui_tree), knowledge, app)


@pytest.mark.parametrize("matching", [True, False], ids=["same-row", "cross-row"])
@pytest.mark.parametrize("confirmed", [True, False], ids=["confirmed", "unconfirmed"])
def test_row_identity_does_not_borrow_a_number_from_another_row(rig, matching, confirmed):
    ws, app, knowledge = rig
    if confirmed:
        knowledge.confirm(COPY_RULE, reviewer="pytest-synthetic", basis="Synthetic scoped text contract")
    tree = _tree(_row("1号车位", "A138" if matching else "A999"),
                 _row("2号车位", "A999" if matching else "A138", 150, "other-row"))
    result = _evaluate(rig, tree)
    assert result["observed_holds"] is matching
    assert result["result"] == (("pass" if matching else "fail") if confirmed else "pending")
    assert result["evidence"]["within"]["node"]["id"] == "favorite-row"
    assert all(hit["text"] == "A138" for hit in result["evidence"].get("matches", []))
    # Omitting within retains the established global-text contract.
    unscoped = _assertion()
    unscoped.pop("within")
    assert _evaluate(rig, tree, unscoped)["observed_holds"] is True


@pytest.mark.parametrize("typ", ["text_present", "text_absent", "text_present_any"])
def test_all_supported_checks_apply_the_same_row_boundary(rig, typ):
    tree = _tree(_row("1号车位", "A999"), _row("2号车位", "A138", 150, "other-row"))
    assertion = _assertion(typ, any=["A138", "not-present"])
    result = _evaluate(rig, tree, assertion)
    assert result["observed_holds"] is (typ == "text_absent")


@pytest.mark.parametrize("state", ["missing", "duplicate-name", "hidden", "hidden-ancestor", "clipped",
                                  "rounded-clipping", "root", "stale"])
def test_untrusted_scope_never_proves_absence(rig, state):
    _, app, _ = rig
    rows = [_row("1号车位", "A999")]
    observation = {}
    if state == "missing":
        rows = [_row("other", "A138", identity="other-row")]
    elif state == "duplicate-name":
        rows += [_row("1号车位", "A138", 150, "other-row")]
        app.targets["favorite_row"]["strategies"] = [{"kind": "xpath", "value": "//Row[.//Text[@text='1号车位']]"}]
    elif state == "hidden":
        rows[0]["attributes"]["visible"] = "false"
    elif state == "hidden-ancestor":
        rows = [_node("Column", "[0,0][400,200]", rows, visible="false")]
    elif state == "clipped":
        rows = [_node("List", "[0,0][400,60]", rows)]
        app.targets["favorite_row"]["min_visible_fraction"] = 0
    elif state == "rounded-clipping":
        # 3999/4000 rounds to 1.0; exact rectangles must still reject the scope.
        rows = [_node("List", "[0,0][400,3999]", [
            _node("Row", "[0,0][400,4000]", [_node("Text", "[0,0][100,20]", text="A999")], id="favorite-row")])]
    elif state == "root":
        app.targets["favorite_row"]["strategies"] = [{"kind": "xpath", "value": "/Column"}]
    elif state == "stale":
        observation["fresh"] = False
    result = _evaluate(rig, _tree(*rows), _assertion("text_absent"), **observation)
    assert result["result"] == "undeterminable", result
    assert "observed_holds" not in result and result["verified"] is False


@pytest.mark.parametrize("typ", ["text_present", "text_absent", "text_present_any"])
def test_hidden_nonclipping_descendants_are_excluded_only_from_scoped_checks(rig, typ):
    row = _row("1号车位", "A999")
    row["children"].append(_node("Column", "[0,20][400,100]", [
        _node("Text", "[10,30][190,60]", text="A138")], visible="false"))
    result = _evaluate(rig, _tree(row), _assertion(typ, any=["A138"]))
    assert result["observed_holds"] is (typ == "text_absent")


@pytest.mark.parametrize("failure", ["collection", "xpath"])
def test_scope_errors_remain_tool_errors_in_the_assertion_result(rig, failure):
    observation = {}
    if failure == "collection":
        observation["tree"] = {"status": "error", "error_class": "tree_parse_error", "error": "synthetic raw failure"}
    else:
        rig[1].targets["favorite_row"]["strategies"] = [{"kind": "xpath", "value": "//*["}]
    result = _evaluate(rig, _tree(_row("1号车位", "A999")), _assertion("text_absent"), **observation)
    assert result["result"] == "error" and result["attribution"] == "tool"
    assert result["stage"] == ("observation" if failure == "collection" else "assertion_scope")
    assert result["error_class"] == ("tree_parse_error" if failure == "collection" else "assertion_scope_error")
    assert "observed_holds" not in result
    if failure == "xpath":
        assert "容器定位错误" in result["reason"]
        assert "XPathEvalError" in str(result["evidence"])


@pytest.mark.parametrize("invalid", ["unknown", "empty", "dict", "unsupported", "template", "mixed", "bad-text", "bad-any",
                                   "bad-target", "bad-target-list", "missing-value", "nontext-value"])
def test_invalid_scope_definitions_fail_lint_and_runner_before_any_device_access(rig, monkeypatch, invalid):
    ws, app, knowledge = rig
    assertion = _assertion()
    if invalid in ("unknown", "empty", "dict"):
        assertion["within"] = {"unknown": "not-registered", "empty": "", "dict": {"id": "favorite-row"}}[invalid]
    elif invalid == "unsupported":
        assertion["type"] = "target_present"
        assertion["target"] = "favorite_row"
    elif invalid in ("template", "mixed"):
        template = {"kind": "template", "file": "nonexistent.png"}
        app.targets["favorite_row"]["strategies"] = ([template] if invalid == "template" else
                                                      [*app.targets["favorite_row"]["strategies"], template])
    elif invalid == "bad-text":
        assertion["text"] = []
    elif invalid == "bad-any":
        assertion.update(type="text_present_any", any="A138")
    elif invalid in ("bad-target", "bad-target-list"):
        app.targets["favorite_row"] = None if invalid == "bad-target" else ["not-a-target-object"]
    else:
        app.targets["favorite_row"]["strategies"] = [
            {"kind": "id", **({"value": []} if invalid == "nontext-value" else {})}]
    case = ws.load_case(COPY_CASE)
    case.update(steps=[{"id": "S1", "action": "launch"}], assertions=[assertion], cleanup=[])
    issues = cases.lint(case, app, knowledge)
    assert any(issue["field"] == "assertions.A1.within" for issue in issues)
    monkeypatch.setattr(ws, "app", lambda _: app)
    monkeypatch.setattr(ws, "device", lambda *a, **k: pytest.fail("invalid scope reached device creation"))
    with pytest.raises(ValueError, match="执行结构无效"):
        run_case(ws, case=case, propose=False)
    assert not ws.runs_dir.exists()


def test_scope_target_is_bound_to_dependencies_and_hypothesis_gate(rig):
    ws, app, knowledge = rig
    case = ws.load_case(COPY_CASE)
    case["assertions"] = [_assertion()]
    before = cases.dependency_hash(case, app, knowledge)
    app.targets["unrelated_row"] = {"strategies": [{"kind": "id", "value": "unrelated"}]}
    assert cases.dependency_hash(case, app, knowledge) == before
    app.targets["favorite_row"]["strategies"][0]["value"] = "changed-row"
    assert cases.dependency_hash(case, app, knowledge) != before
    app.targets["favorite_row"]["status"] = "hypothesis"
    assert cases.hypothesis_targets(case, app) == ["favorite_row"]
    assert any(issue["field"] == "targets" for issue in cases.lint(case, app, knowledge))


def test_scoped_result_flows_through_runner_and_keeps_raw_observation_identity(rig, tmp_path, monkeypatch):
    ws, app, _ = rig
    confirm(ws, COPY_RULE)
    root = _tree(_row("1号车位", "A999"), _row("2号车位", "A138", 150, "other-row")).raw
    def spec(node):
        return {"attrs": node["attributes"], "children": [spec(child) for child in node.get("children", [])]}
    path = tmp_path / "row-world.json"
    write_json(path, {"bundle": app.bundle, "app_version": "3.0.3.200", "screen": [400, 400],
                      "display": {"density": 1, "device_form": "bar"}, "env_facts": {"account_state": "logged_out"},
                      "initial": {"foreground": app.bundle, "installed": True}, "launch_screen": "rows",
                      "screens": {"rows": {"root": spec(root)}}})
    case = ws.load_case(COPY_CASE)
    case.update(steps=[{"id": "S1", "action": "launch"}], assertions=[_assertion()])
    monkeypatch.setattr(ws, "app", lambda _: app)
    run = run_case(ws, case=case, device_spec=f"replay:{path}", propose=False)
    result = run["assertions"][0]
    assert result["result"] == "fail" and run["verdict"]["verdict"] == "failed"
    assert result["evidence"]["obs"] == run["steps"][0]["final_obs"]
    assert result["evidence"]["within"]["node"]["id"] == "favorite-row"
