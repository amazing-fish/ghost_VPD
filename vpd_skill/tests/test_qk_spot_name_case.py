"""Executable spot-name scope. All replay data and requirement confirmations here are synthetic."""
from copy import deepcopy

import pytest

from conftest import WORLDS, world
from test_condition_runner import COMMON_RULE
from test_search_name_row import _locate, _oracle, _raw, _tree
from vpdtest.cases import content_hash, lint, replay_basis
from vpdtest.devices.replay import load_world
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.runtime_data import bind_assertion, prepare
from vpdtest.tree import UiTree
from vpdtest.util import write_json
from vpdtest.verdict import case_verdict, evaluate_assertion

CASE = "TC-QK-230-SPOT-NAME"


def test_independent_draft_uses_one_dynamic_name_and_an_explicit_manual_identity(ws):
    case, parent = ws.load_case(CASE), ws.load_case("TC-QK-230")
    assert case["status"] == "draft" and case["version"] == 1 and not case.get("replays")
    assert parent["test_data"].keys() == {"fav_spot", "fav_waypoint", "fav_spot_number", "fav_waypoint_number"}
    assert case["test_data"] == {"fav_spot": {"runtime": True}}
    app = ws.app(case["app"])
    assert lint(case, app, KnowledgeStore(ws.root), ws.policy()) == []
    inputs = [s for s in case["steps"] if s["action"] == "input"]
    assert len(inputs) == 1
    assert inputs[0]["text"] == "${var:fav_spot}" and inputs[0]["target"] == "qk_search_input"
    assert inputs[0]["clear"] is inputs[0]["require_empty_after_clear"] is True
    assert [a["id"] for a in case["assertions"] if not a.get("advisory")] == ["favorite-identity"]
    assert prepare(case, {"fav_spot": "新设的本轮名称"}) == {"fav_spot": "新设的本轮名称"}


@pytest.mark.parametrize("values", [{}, {"fav_spot": ""}, {"fav_spot": "名称", "fav_waypoint": "路点"}])
def test_unusable_or_out_of_scope_inputs_stop_before_device_creation(ws, monkeypatch, values):
    monkeypatch.setattr(ws, "device", lambda *a, **kw: pytest.fail("unexpected device request"))
    with pytest.raises(ValueError, match="运行参数"):
        run_case(ws, CASE, runtime_values=values)


def _world_with_named_result(ws, tmp_path, name):
    """Add a renamed historical row to the stock replay world; retain the real app/runner/targets."""
    fixture = load_world(WORLDS / "qiankun_hm_ready.json")
    fixture.pop("_path", None)
    page = fixture["screens"]["qk_search"]["root"]
    search = page["children"][0]["children"][0]
    assert search["t"] == "Search"
    tree = _tree("obs-0046")
    row = tree.nodes[_locate(ws.app("qiankun_hm"), tree)["attempts"][-1]["node"]["idx"]]

    def replay_node(node):
        return {"t": node.type, "b": list(node.bounds), "text": name if node.text == "1号车位" else node.text,
                "children": [replay_node(tree.nodes[child]) for child in node.children]}

    result = {**replay_node(row), "show_if_bound": "search_q"}
    page["children"] = [search, {"t": "Column", "id": "search_list", "b": [0, 300, 1320, 2000],
                                 "children": [result]}]
    path = tmp_path / "spot-name-world.json"
    write_json(path, fixture)
    return "replay:" + str(path)


@pytest.mark.parametrize("has_row", [False, True], ids=["unrecognized-list", "named-row"])
def test_normal_runner_keeps_separate_result_observations_and_records_only_a_declaration(ws, tmp_path, has_row):
    name = "可替换的本轮合成车位"
    spec = _world_with_named_result(ws, tmp_path, name) if has_row else world("qiankun_hm_ready.json")
    run = run_case(ws, CASE, device_spec=spec, propose=False, runtime_values={"fav_spot": name})
    assert all(s["outcome"] == "passed" for s in run["steps"]), run["steps"]
    assert all(s["outcome"] == "passed" for s in run["cleanup"]), run["cleanup"]
    assert run["runtime_data"] == {"values": {"fav_spot": name}, "source": "run_arguments", "measured": False}
    steps = {s["step_id"]: s for s in run["steps"]}
    assert steps["S4"]["final_obs"] != steps["S5"]["final_obs"]
    evidence = {a["id"]: a for a in run["assertions"]}
    for step, tag in (("S4", "first"), ("S5", "second")):
        assert evidence[tag + "-query"]["evidence"]["obs"] == steps[step]["final_obs"]
        assert evidence[tag + "-query"]["evidence"]["actual_text"] == name
        row = evidence[tag + "-row"]
        assert row["evidence"]["obs"] == steps[step]["final_obs"]
        if has_row:
            assert row["observed_holds"] is True and row["verified"] is False
        else:
            assert row["result"] == "undeterminable"
    assert evidence["favorite-identity"]["evidence"]["obs"] == steps["S5"]["final_obs"]
    assert evidence["favorite-identity"]["result"] == "pending"
    assert run["verdict"]["product_verdict"] == "pending"
    assert not ws.load_case(CASE).get("replays")


@pytest.mark.parametrize("mode", ["matching", "wrong-query", "duplicate", "missing-screenshot", "capture-error"])
def test_even_a_confirmed_rule_cannot_turn_declared_input_into_verified_identity(ws, tmp_path, mode):
    case, app, tree = ws.load_case(CASE), ws.app("qiankun_hm"), _tree("obs-0046")
    if mode == "duplicate":
        row = tree.nodes[_locate(app, tree)["attempts"][-1]["node"]["idx"]]
        _raw(tree, tree.nodes[row.parent])["children"].append(deepcopy(_raw(tree, row)))
        tree = UiTree(tree.raw)
    name = "合成的另一名称" if mode == "wrong-query" else "1号车位"
    values = prepare(case, {"fav_spot": name})
    oracle = _oracle(tmp_path)
    layout = {"pane_count": 1}
    page = app.states.identify(tree, app.bundle, True, layout)
    shot = {"status": "ok", "path": "synthetic.png"}
    if mode == "missing-screenshot":
        shot = {"status": "unavailable"}
    elif mode == "capture-error":
        shot = {"status": "error", "error_class": "screenshot_capture_error"}

    def load(frame):
        return {"obs_id": frame, "fresh": True, "layout": layout, "page": page,
                "tree": {"status": "ok"}, "screenshot": shot}, tree

    steps = {step: {"step_id": step, "action": "wait", "outcome": "passed", "final_obs": step + "-frame"}
             for step in ("S4", "S5")}
    results = []
    for item in case["assertions"]:
        bound = bind_assertion({**item, "oracle_refs": [COMMON_RULE]}, values)
        results.append(evaluate_assertion(bound, steps, load, oracle, app))
    by_id = {a["id"]: a for a in results}
    identity = by_id["favorite-identity"]
    assert not identity["verified"] and not identity["advisory"]
    assert identity["result"] == ("error" if mode == "capture-error" else "pending")
    if mode == "matching":
        assert all(a["result"] == "pass" and not a["verified"] for a in results if a["advisory"])
    elif mode == "wrong-query":
        assert by_id["second-query"]["result"] == "fail"
    elif mode == "duplicate":
        assert by_id["second-row"]["result"] == "undeterminable"
    verdict = case_verdict({"ok": True}, {"ok": True}, list(steps.values()), results, oracle)
    assert verdict["product_verdict"] == "pending"
    assert verdict["verdict"] == ("error" if mode == "capture-error" else "pending")
    assert "favorite-identity" in verdict["coverage"]["unverified"]
    if mode == "matching":
        records = [{"run_id": run_id, "hash": content_hash(case), "verdict": verdict["verdict"]}
                   for run_id in ("synthetic-source-A", "synthetic-source-B")]
        assert not replay_basis({**case, "replays": records})["ok"]
