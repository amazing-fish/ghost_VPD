"""Issue #82: diagnose inferred clipping without declaring the clipped target visible.

The trees are synthetic reproductions of the reported 947/1264 contradiction,
not a replacement for a same-observation window/tree/screenshot capture.
"""
from pathlib import Path

import pytest

from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world
from vpdtest.commands import diagnose
from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.locate import resolve
from vpdtest.tree import UiTree
from vpdtest.util import read_json, read_jsonl, write_json


def _node(kind, box, text="", clickable=False, children=()):
    return {"attributes": {"type": kind, "bounds": box, "text": text,
                           "clickable": str(clickable).lower()}, "children": list(children)}


def _clipped_tree(root_width=1264, clip_type="Swiper", background="ADS"):
    return UiTree(_node("Root", f"[0,0][{root_width},1848]", children=[
        _node("Text", "[20,20][200,60]", background),
        _node(clip_type, "[0,107][947,1631]", children=[
            _node("Column", "[0,107][1264,1100]", children=[
                _node("Button", "[1120,870][1210,930]", clickable=True, children=[
                    _node("Text", "[1135,883][1196,918]", "全览")])])])]))


@pytest.mark.parametrize("root_width", [947, 1264])
def test_point_with_no_concrete_receiver_does_not_classify_background(ws, root_width):
    tree = _clipped_tree(root_width)
    point = (1165, 900)
    result = ws.policy().check_tap(tree, point, "point", {"side_effect": "navigation"})
    assert not result["allowed"]
    assert result["deny"]["class"] == "no_receiver_at_point"
    assert result["label"] is None
    evidence = result["point_evidence"]
    assert evidence["point"] == list(point)
    assert evidence["window_source"] == "ui_tree_bounds"
    assert evidence["window_rect"][2] == root_width
    button = next(c for c in evidence["clipped_candidates"] if c["node"]["type"] == "Button")
    assert button["rect"] is None
    assert any(a["type"] == "Swiper" for a in button["clip_ancestors"])
    assert tree.find_text("全览") == []  # No geometry fallback or visibility relaxation.


@pytest.mark.parametrize("background", ["ADS", "沿途找车位", "支付", "普通页面"])
def test_root_receiver_diagnosis_is_independent_of_unrelated_page_text(ws, background):
    tree = UiTree(_node("Root", "[0,0][1264,1848]", children=[
        _node("Text", "[20,20][400,60]", background)]))
    result = ws.policy().check_tap(tree, (1100, 800), "point", {"side_effect": "navigation"},
                                  authorized=ws.policy().authorizable, node=tree.nodes[0])
    assert not result["allowed"] and result["deny"]["class"] == "no_receiver_at_point"
    assert result["point_evidence"]["clipped_candidates"] == []


@pytest.mark.parametrize("clip_type", ["List", "Scroll", "Grid", "WaterFlow", "Swiper", "Refresh"])
def test_select_diagnosis_exposes_clipping_chain_without_changing_visibility(clip_type):
    tree = _clipped_tree(1264, clip_type)
    loc = resolve("overview", {"strategies": [{"kind": "text", "value": "全览"}]},
                  tree, None, Path("."))
    assert loc["status"] == "not_found"
    attempt = loc["attempts"][0]
    assert attempt["offscreen"] == 1
    clipped = attempt["clipped_candidates"][0]
    assert clipped["node"]["text"] == "全览" and clipped["fraction"] == 0
    assert clipped["window_source"] == "ui_tree_bounds"
    assert [n["type"] for n in clipped["clip_ancestors"]] == [clip_type]
    diagnosis = diagnose({"outcome": "blocked", "final_status": "target_unresolved",
                          "attempts": [{"locate": loc}]})
    assert diagnosis["clipped_candidates"] == [clipped]
    assert "裁剪" in diagnosis["hint"]


def test_actual_nonroot_receiver_still_uses_its_own_semantics(ws):
    tree = UiTree(_node("Root", "[0,0][1264,1848]", children=[
        _node("Text", "[10,10][300,60]", "支付"),
        _node("Button", "[1100,800][1250,950]", "全览", clickable=True)]))
    assert ws.policy().check_tap(tree, (1175, 875), "point", {"side_effect": "navigation"})["allowed"]


def test_empty_layout_below_clickable_root_is_not_a_specific_receiver(ws):
    tree = UiTree(_node("Root", "[0,0][1264,1848]", clickable=True, children=[
        _node("Column", "[0,0][1264,1800]", children=[
            _node("Text", "[20,20][400,60]", "ADS")])]))
    assert tree.node_at(1100, 800).type == "Column"
    result = ws.policy().check_tap(tree, (1100, 800), "point")
    assert not result["allowed"] and result["deny"]["class"] == "no_receiver_at_point"


def test_hidden_swiper_page_and_real_overlay_remain_unavailable(ws):
    tree = _clipped_tree()
    tree.raw["children"].append(_node("Dialog", "[0,0][1264,1848]", children=[
        _node("Button", "[1100,800][1250,950]", "支付", clickable=True)]))
    tree = UiTree(tree.raw)
    assert tree.find_text("全览") == []
    result = ws.policy().check_tap(tree, (1165, 900), "point", {"side_effect": "navigation"})
    assert not result["allowed"] and result["deny"]["class"] == "payment"


def test_repeated_observations_do_not_reuse_old_clipped_candidates(ws):
    blocked = ws.policy().check_tap(_clipped_tree(), (1165, 900), "point")
    moved = ws.policy().check_tap(_clipped_tree(), (1100, 1200), "point")
    assert blocked["point_evidence"]["clipped_candidates"]
    assert moved["point_evidence"]["clipped_candidates"] == []


def test_executor_rejects_root_point_before_dispatch_with_evidence(ws, tmp_path):
    spec = _lab_world(tmp_path, "root-point")
    world = read_json(spec.split(":", 1)[1])
    world["screens"]["home"]["root"] = {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Text", "text": "ADS", "b": [20, 20, 300, 60]}]}
    write_json(spec.split(":", 1)[1], world)
    ctx = _ctx(ws, spec)
    taps = []
    ctx.device.tap = lambda *p: taps.append(p) or DeviceResult("tap", True, True)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": {"side_effect": "navigation", "strategies": []},
                                     "point": [1100, 900], "point_basis": "synthetic empty area"}, "session")
    assert taps == [] and result["final_status"] == "policy_denied"
    diagnosis = diagnose(result)
    assert diagnosis["policy"]["deny"]["class"] == "no_receiver_at_point"
    assert "接收" in diagnosis["hint"]
    assert not any(e.get("sent") is True for e in read_jsonl(ctx.store.events_path) if e["event"] == "dispatch")


def test_dispatch_refresh_diagnosis_reports_final_receiver_rejection(ws, tmp_path):
    spec = _lab_world(tmp_path, "point-refresh")
    world = read_json(spec.split(":", 1)[1])
    world["screens"]["home"] = {"after_obs": {"n": 1, "go": "empty", "replace": True}, "root": {
        "t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Button", "text": "全览", "click": True, "b": [1000, 800, 1250, 1000]},
            {"t": "Text", "text": "全览", "b": [1050, 850, 1200, 950]}]}}
    world["screens"]["empty"] = {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Text", "text": "ADS", "b": [20, 20, 300, 60]}]}}
    write_json(spec.split(":", 1)[1], world)
    ctx = _ctx(ws, spec)
    taps = []
    ctx.device.tap = lambda *p: taps.append(p) or DeviceResult("tap", True, True)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": {"side_effect": "navigation", "strategies": []},
                                     "point": [1100, 900], "point_basis": "synthetic overview"}, "session")
    assert taps == [] and result["final_status"] == "policy_denied"
    attempt = result["attempts"][0]
    assert attempt["policy"]["allowed"]  # Initial policy remains an independent audit record.
    final = attempt["dispatch_recheck"]["checks"]["tap"]
    assert final["policy"]["deny"]["class"] == "no_receiver_at_point"
    diagnosis = diagnose(result)
    assert diagnosis["policy"] == final["policy"] and "接收" in diagnosis["hint"]
    assert diagnosis["policy"]["point_evidence"]["clipped_candidates"] == []


def test_registered_unlabelled_canvas_tree_and_relative_point_remain_compatible(ws, tmp_path):
    spec = _lab_world(tmp_path, "registered-canvas")
    world = read_json(spec.split(":", 1)[1])
    world["screens"]["home"]["root"] = {"t": "Column", "click": True, "b": [0, 0, 1320, 2848],
        "children": [{"t": "XComponent", "id": "canvas", "b": [100, 400, 1200, 1600]}]}
    write_json(spec.split(":", 1)[1], world)
    ctx = _ctx(ws, spec)
    ctx.app.targets["geometry_canvas"] = {"side_effect": "navigation",
                                          "strategies": [{"kind": "id", "value": "canvas"}]}
    taps = []
    ctx.device.tap = lambda *p: taps.append(p) or DeviceResult("tap", True, True)
    result = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0,
                                     "target": "geometry_canvas", "at": [0.5, 0.5]}, "session")
    assert len(taps) == 1 and result["outcome"] == "passed"
