"""#142 synthetic diagnostics only: no production modal signature or device claim."""
import json

import pytest
from test_confirmation_semantics import _check, _node, _tree
from test_confirmation_dispatch_context import _home, _modal, _tap_spy, _world
from test_dispatch_regression import _ctx

from vpdtest.execute import Executor
from vpdtest.locate import resolve
from vpdtest.session import parse_select
from vpdtest.tree import UiTree
from vpdtest.util import read_jsonl


SECRET = "PRIVATE-INPUT-VALUE-142"


def _diagnostic(result):
    assert result["allowed"] is False
    assert result["deny"]["class"] == "confirmation_semantics_unknown"
    return result["confirmation_diagnostic"]


def test_unknown_title_diagnostic_references_current_tree_without_copying_values(ws):
    tree, node = _tree("确认", title="Synthetic unregistered title " + SECRET)
    # Text mirroring an input value and arbitrary selector/source metadata must stay out too.
    node.id = SECRET
    d = _diagnostic(ws.policy().check_tap(
        tree, node.center, "tree", {"side_effect": "user_data", "source": SECRET,
        "strategies": [{"kind": "text", "value": SECRET}]}, authorized={"user_data"}, node=node))
    modal = next(n for n in tree.nodes if n.type == "Dialog")
    title = next(n for n in tree.nodes if n.text.startswith("Synthetic"))
    assert d["version"] == 1 and d["evidence"] == "current_ui_tree"
    assert d["target"]["node_idx"] == node.idx
    assert d["target"]["container_idx"] == node.idx
    assert d["modal"]["nearest_idx"] == modal.idx
    assert d["title"]["selected_idx"] == title.idx
    assert any(c["node_idx"] == title.idx for c in d["title"]["candidates"])
    assert "title_context_not_registered" in d["gaps"]
    assert SECRET not in json.dumps(d, ensure_ascii=False)


def test_no_modal_is_reported_without_borrowing_background_title(ws):
    tree, node = _tree("确认")
    d = _diagnostic(_check(ws, tree, node))
    assert d["modal"]["nearest_idx"] is None
    assert d["title"]["selected_idx"] is None
    assert "nearest_modal_not_identified" in d["gaps"]


@pytest.mark.parametrize("target", [
    {"label": "确认"},
    {"strategies": [{"kind": "text", "value": "确", "exact": False}]},
    {"strategies": [{"kind": "xpath", "value": "//*[@text='确认']"}]},
])
def test_no_tree_unknown_confirmation_has_safe_missing_evidence_diagnostic(ws, target):
    d = _diagnostic(ws.policy().check_tap(None, (10, 10), "template", target))
    assert d["evidence"] == "ui_tree_unavailable"
    assert d["target"]["node_idx"] is None
    assert d["title"]["candidates"] == []
    assert "ui_tree_unavailable" in d["gaps"]


def test_source_gaps_report_counts_not_untrusted_source_values(ws):
    tree, node = _tree("确认", title="Synthetic unknown")
    sources = [{"kind": "state_confirmation_candidates", "scope_idx": 999,
                "candidates": [{"state": SECRET, "rules": []}]},
               {"kind": "reviewed_benign_interrupt", "modal_idx": 999, "source": SECRET}]
    d = _diagnostic(_check(ws, tree, node, sources=sources))
    assert d["sources"]["supplied_page"] == 1 and d["sources"]["accepted_page"] == 0
    assert d["sources"]["supplied_benign"] == 1 and d["sources"]["accepted_benign"] == 0
    assert "page_confirmation_not_accepted" in d["gaps"]
    assert "bound_benign_source_not_accepted" in d["gaps"]
    assert SECRET not in json.dumps(d)


def test_editable_descendants_are_not_selected_as_title_and_payload_is_bounded(ws):
    children = [_node("TextInput", "[10,10][190,60]", SECRET, children=[
        _node("Text", "[10,10][180,40]", SECRET)])]
    children += [_node("Text", "[10,60][190,80]", f"{SECRET}-{i}") for i in range(20)]
    children += [_node("Button", "[10,100][190,150]", "确认", clickable="true", id="confirm")]
    tree = UiTree(_node("orgRoot", "[0,0][200,200]", children=[
        _node("Dialog", "[0,0][200,200]", children=children)]))
    node = next(n for n in tree.nodes if n.id == "confirm")
    d = _diagnostic(_check(ws, tree, node))
    input_child = next(n for n in tree.nodes if n.type == "Text")
    assert d["title"]["selected_idx"] != input_child.idx
    assert d["title"]["candidate_count"] > 8
    assert len(d["title"]["candidates"]) == 8 and d["title"]["truncated"] is True
    assert SECRET not in json.dumps(d)


@pytest.mark.parametrize("path", ["executor", "dispatcher"])
def test_diagnostic_reaches_actual_denial_and_no_tap_is_sent(ws, tmp_path, monkeypatch, path):
    # This layout is intentionally synthetic, not the missing #142 field tree.
    spec = _world(tmp_path, _home(_modal("Synthetic unregistered", label="确认")))
    ctx = _ctx(ws, spec)
    ctx.authorized = {"user_data"}
    calls = _tap_spy(monkeypatch, ctx.device)
    target = parse_select("text=确认", side_effect="user_data")
    if path == "executor":
        result = Executor(ctx).run_step({"id": "D142", "action": "tap", "retry": 0,
                                        "target": target}, "synthetic")
        assert result["outcome"] == "blocked" and result["final_status"] == "policy_denied"
        policy = result["attempts"][0]["policy"]
    else:
        obs, tree = ctx.observe("diagnostic-test")
        loc = resolve("synthetic-confirm", target, tree, ctx.observer.screenshot_abs(obs), ctx.app.dir)
        assert loc["status"] == "found"
        att = {"action": "tap", "step_id": "D142"}
        result = ctx.dispatcher.send("tap", *loc["point"],
            preconditions=ctx.dispatcher.app_conditions("tap"), att=att,
            tap={"tree": tree, "loc": loc, "target": target, "target_ref": "synthetic-confirm",
                 "page": obs["page"], "state_deny": ctx.app.states.deny_for_page(obs["page"])})
        assert result.sent is False
        events = [e for e in read_jsonl(ctx.store.events_path) if e.get("event") == "dispatch"]
        assert events and all(e["sent"] is False for e in events)
        policy = events[-1]["checks"]["tap"]["policy"]
    assert calls == []
    assert _diagnostic(policy)["modal"]["nearest_idx"] is not None


@pytest.mark.parametrize("title", ["重命名", "修改名称"])
def test_registered_context_allowed_result_remains_unchanged(ws, title):
    tree, node = _tree("确认", title=title)
    result = _check(ws, tree, node, "user_data", granted=["user_data"])
    assert result["allowed"] is True and "confirmation_diagnostic" not in result


def test_diagnostic_failure_preserves_refusal_without_exception_values(ws, monkeypatch):
    policy = ws.policy()
    def fail(*args, **kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(policy, "_confirmation_diagnostic", fail)
    tree, node = _tree("确认")
    result = policy.check_tap(tree, node.center, "tree", node=node)
    assert _diagnostic(result)["gaps"] == ["diagnostic_collection_failed"]
    assert SECRET not in json.dumps(result)


def test_no_tree_diagnostic_does_not_copy_non_numeric_point_data(ws):
    result = ws.policy().check_tap(None, (SECRET, 10), "template", {"label": "确认"})
    assert _diagnostic(result)["target"]["point"] is None
    assert SECRET not in json.dumps(result)
