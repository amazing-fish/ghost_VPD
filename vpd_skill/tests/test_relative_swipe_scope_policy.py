"""#256: synthetic path-policy regression, not work-machine acceptance."""
import pytest

from test_relative_swipe import _world, _step
from test_dispatch_regression import _ctx
from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.util import read_json, write_json


def setup(ws, tmp_path, *, child_text="收藏", child_bounds=None, clickable=False,
          scope_text="", child_click=False, kind="List"):
    spec = _world(tmp_path, kind=kind, children=[
        {"t": "Text", "text": child_text, "b": child_bounds or [110, 210, 220, 260],
         "click": child_click},
    ])
    data = read_json(spec.split(":", 1)[1])
    scope = data["screens"]["home"]["root"]["children"][0]
    scope.update(click=clickable, text=scope_text)
    write_json(spec.split(":", 1)[1], data)
    return _ctx(ws, spec)


@pytest.mark.parametrize("kind", ["List", "Scroll", "Grid"])
@pytest.mark.parametrize("label", ["收藏", "支付", "确定"])
def test_off_path_descendant_does_not_describe_blank_scroll_scope(ws, tmp_path, kind, label):
    ctx = setup(ws, tmp_path, kind=kind, child_text=label)
    _, tree = ctx.observe("synthetic-scope")
    scope = next(n for n in tree.nodes if n.id == "gesture_scope")
    assert not ctx.policy.check_tap(tree, (500, 500), "tree", node=scope)["allowed"]
    result = Executor(ctx).run_step(_step(expect_text="RESULT_right"), "scope-scroll")
    assert result["outcome"] == "passed", result
    assert result["attempts"][-1]["dispatch"]["sent"] is True
    assert ctx.actions == 1


@pytest.mark.parametrize("kwargs", [
    {"child_bounds": [400, 450, 600, 550]},
    {"child_bounds": [400, 450, 600, 550], "child_click": True},
    {"clickable": True},
    {"scope_text": "收藏"},
    {"scope_text": "支付"},
    {"scope_text": "确定"},
])
def test_scoping_does_not_remove_actual_receiver_or_own_semantics(ws, tmp_path, monkeypatch, kwargs):
    ctx = setup(ws, tmp_path, **kwargs)
    calls = []
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(), "unsafe-scope-scroll")
    assert result["final_status"] == "policy_denied", result
    assert calls == [] and ctx.actions == 0


def test_page_rule_on_scope_label_is_not_bypassed(ws, tmp_path, monkeypatch):
    ctx = setup(ws, tmp_path, scope_text="普通列表")
    monkeypatch.setattr(ctx.app.states, "deny_for_page", lambda page: ["普通列表"])
    result = Executor(ctx).run_step(_step(), "page-rule-scroll")
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0


@pytest.mark.parametrize("variant", ["description", "clickable_ancestor", "nested_container"])
def test_scope_narrowing_keeps_other_semantic_owners(ws, tmp_path, variant):
    spec = _world(tmp_path, kind="List", children=[
        {"t": "Text", "text": "收藏", "b": [110, 210, 220, 260]}])
    data = read_json(spec.split(":", 1)[1])
    root = data["screens"]["home"]["root"]
    scope = root["children"][0]
    scope["click"] = False
    if variant == "description":
        scope["desc"] = "支付"
    elif variant == "clickable_ancestor":
        root["click"] = True
    else:
        scope["children"] = [{"t": "Column", "b": [100, 200, 900, 800],
                              "children": scope["children"]}]
    write_json(spec.split(":", 1)[1], data)
    ctx = _ctx(ws, spec)
    result = Executor(ctx).run_step(_step(), "semantic-owner-scroll")
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0


def test_unrelated_scope_argument_cannot_narrow_a_tap(ws, tmp_path):
    ctx = setup(ws, tmp_path)
    _, tree = ctx.observe("synthetic-scope")
    scope = next(n for n in tree.nodes if n.id == "gesture_scope")
    result = ctx.policy.check_tap(tree, (500, 500), "tree", node=scope, scroll_scope=tree.nodes[0])
    assert not result["allowed"] and result["deny"]["class"] == "user_data"
