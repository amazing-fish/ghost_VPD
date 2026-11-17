"""#262: synthetic top-of-list Row versus blank bottom scope; no phone replay."""
import pytest

from test_dispatch_regression import _ctx
from test_relative_swipe import _world, _step
from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.util import read_json, write_json


def row_world(tmp_path, *, kind="Row", row_click=False, root_click=False,
              row_text="", row_desc="", child_bounds=None, child_type="Text",
              child_click=False, child_text="精选/充电/购车等", scope_kind="List"):
    child = {"t": child_type, "text": child_text,
             "b": child_bounds or [110, 210, 220, 260], "click": child_click}
    row = {"t": kind, "id": "order_row", "text": row_text, "desc": row_desc,
           "b": [110, 210, 890, 790], "click": row_click, "children": [child]}
    spec = _world(tmp_path, kind=scope_kind, children=[row])
    data = read_json(spec.split(":", 1)[1])
    root = data["screens"]["home"]["root"]
    root["click"] = root_click
    root["children"][0]["click"] = False
    write_json(spec.split(":", 1)[1], data)
    return spec


@pytest.mark.parametrize("scope_kind", ["List", "Scroll", "Grid"])
@pytest.mark.parametrize("label", ["精选/充电/购车等", "收藏", "支付", "确定"])
def test_passive_row_ignores_off_path_descendants_but_tap_stays_denied(
        ws, tmp_path, scope_kind, label):
    ctx = _ctx(ws, row_world(tmp_path, scope_kind=scope_kind, child_text=label))
    _, tree = ctx.observe("synthetic-top-row")
    row = tree.node_at(500, 500)
    assert row.id == "order_row" and not row.clickable
    assert not ctx.policy.check_tap(tree, (500, 500), "tree", node=row)["allowed"]
    result = Executor(ctx).run_step(_step(expect_text="RESULT_right"), "row-scroll")
    assert result["outcome"] == "passed", result
    assert result["attempts"][-1]["dispatch"]["sent"] is True
    assert ctx.actions == 1


@pytest.mark.parametrize("kwargs", [
    {"row_click": True},
    {"root_click": True},
    {"row_text": "充电"},
    {"row_desc": "充电"},
    {"child_bounds": [400, 450, 600, 550]},
    {"child_bounds": [400, 450, 600, 550], "child_click": True},
    {"child_bounds": [400, 450, 600, 550], "child_type": "Slider", "child_text": ""},
    {"child_bounds": [400, 450, 600, 550], "child_type": "Button", "child_text": "支付"},
    {"kind": "Button"},
    {"kind": "Text"},
])
def test_row_extension_does_not_authorize_controls_or_actual_path_text(ws, tmp_path, monkeypatch, kwargs):
    ctx = _ctx(ws, row_world(tmp_path, **kwargs))
    calls = []
    monkeypatch.setattr(ctx.device, "swipe", lambda *args: calls.append(args) or DeviceResult("swipe", True, True))
    result = Executor(ctx).run_step(_step(), "row-deny")
    assert result["final_status"] == "policy_denied", result
    assert calls == [] and ctx.actions == 0


@pytest.mark.parametrize("kind", ["XComponent", "SurfaceView", "WebView", "Canvas"])
def test_row_extension_keeps_unknown_surface_refusal(ws, tmp_path, kind):
    ctx = _ctx(ws, row_world(tmp_path, child_bounds=[417, 200, 418, 800],
                             child_type=kind, child_text=""))
    result = Executor(ctx).run_step(_step(), "row-unknown")
    assert result["final_status"] == "target_unresolved", result
    assert ctx.actions == 0


def test_page_rule_on_row_own_label_still_denied(ws, tmp_path, monkeypatch):
    ctx = _ctx(ws, row_world(tmp_path, row_text="订单布局"))
    monkeypatch.setattr(ctx.app.states, "deny_for_page", lambda page: ["订单布局"])
    result = Executor(ctx).run_step(_step(), "row-page-rule")
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0


def test_reported_wide_vertical_gesture_crosses_passive_row(ws, tmp_path):
    # Geometry from the Issue summary, but a fabricated UI tree and transition.
    # This is not the work-machine screenshot/tree or an actual S01 replay.
    spec = row_world(tmp_path)
    data = read_json(spec.split(":", 1)[1])
    data["screen"] = [2584, 1828]
    root = data["screens"]["home"]["root"]
    root["b"] = [0, 0, 2584, 1828]
    scope = root["children"][0]
    scope["b"] = [0, 261, 2584, 1597]
    row = scope["children"][0]
    row["b"] = [44, 728, 2540, 914]
    row["children"][0]["b"] = [100, 760, 900, 820]
    write_json(spec.split(":", 1)[1], data)
    ctx = _ctx(ws, spec)
    result = Executor(ctx).run_step(_step(start=[0.5, 0.75], end=[0.5, 0.25], speed=2000,
                                          expect_text="RESULT_up"), "wide-vertical-row")
    assert result["outcome"] == "passed", result
    assert ctx.actions == 1
    assert any(check["node"] == 2 for check in result["attempts"][-1]["swipe"]["path_checks"])
