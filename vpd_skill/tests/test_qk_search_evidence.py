"""QK-230 offline response evidence. Historical exports and controlled mutations only."""
import json
import sys
from copy import deepcopy

import pytest
from test_search_name_row import _raw, _tree
from vpdtest.qk_search_evidence import inspect_name, main
from vpdtest.tree import UiTree
from vpdtest.util import write_json


def observation(app, tree, obs_id):
    layout = {"pane_count": 1}
    return {"obs_id": obs_id, "fresh": True, "source": "replay", "process_alive": True,
            "foreground": app.bundle, "foreground_after": app.bundle, "tree": {"status": "ok"},
            "layout": layout, "page": app.states.identify(tree, app.bundle, True, layout)}


@pytest.mark.parametrize("obs_id,name,texts", [
    ("obs-0046", "1号车位", ["1号车位", "L1层", "I1", "路线"]),
    ("obs-0056", "1号停车点", ["1号停车点", "下车点", "L1层", "路线"]),
])
def test_same_row_only_has_observational_weight(ws, obs_id, name, texts):
    app, tree = ws.app("qiankun_hm"), _tree(obs_id)
    report = inspect_name(app, obs_id, observation(app, tree, obs_id), tree, name)
    assert report["row_observation"] == "observed", report
    assert report["row_texts"] == texts
    assert report["favorite_identity"] == "undetermined"
    assert report["number_requirement"] == "pending"
    assert report["product_verdict"] == "not_evaluated"


@pytest.mark.parametrize("mutation", ["duplicate", "other_poi", "clipped_other_poi", "dirty_name",
                                     "stale", "wrong_obs", "another_app", "other_pane", "partial_title"])
def test_uncertain_name_or_scope_blocks_evidence(ws, mutation):
    app, obs_id, name = ws.app("qiankun_hm"), "obs-0056", "1号停车点"
    tree = _tree(obs_id)
    obs = observation(app, tree, obs_id)
    if mutation == "duplicate":
        row = tree.nodes[inspect_name(app, obs_id, obs, tree, name)["row_node"]["idx"]]
        _raw(tree, tree.nodes[row.parent])["children"].append(deepcopy(_raw(tree, row)))
    elif mutation in ("other_poi", "clipped_other_poi"):
        attrs = _raw(tree, tree.find_text("远望1号停车点")[-1])["attributes"]
        attrs["text"] = name
        if mutation == "clipped_other_poi":
            attrs["bounds"] = "[165,3050][520,3110]"
    elif mutation == "partial_title":
        title = next(n for n in tree.nodes if n.type == "Text" and n.text == name)
        _raw(tree, title)["attributes"]["bounds"] = "[164,872][482,927]"
    elif mutation == "dirty_name":
        _raw(tree, tree.find_id("__SearchField__search_input")[0])["attributes"]["text"] += "残留"
    elif mutation == "stale":
        obs["fresh"] = False
    elif mutation == "wrong_obs":
        obs["obs_id"] = "obs-0046"
    elif mutation == "another_app":
        obs["foreground_after"] = "other.bundle"
    else:
        obs["layout"]["pane_count"] = 2
    report = inspect_name(app, obs_id, obs, UiTree(tree.raw), name)
    assert report["row_observation"] == "blocked", report
    assert "row_texts" not in report
    assert report["favorite_identity"] == "undetermined" and report["number_requirement"] == "pending"


@pytest.mark.parametrize("name", ["I1", "L1层", "路线", "175米"])
def test_name_may_equal_a_known_field_or_action_without_becoming_ambiguous(ws, name):
    app, obs_id = ws.app("qiankun_hm"), "obs-0046"
    tree = _tree(obs_id)
    for n in tree.nodes:
        if n.text == "1号车位":
            _raw(tree, n)["attributes"]["text"] = name
    tree = UiTree(tree.raw)
    report = inspect_name(app, obs_id, observation(app, tree, obs_id), tree, name)
    assert report["row_observation"] == "observed", report
    assert report["favorite_identity"] == "undetermined" and report["number_requirement"] == "pending"


def test_name_in_an_unknown_neighbor_role_still_blocks(ws):
    app, obs_id, name = ws.app("qiankun_hm"), "obs-0056", "1号停车点"
    tree = _tree(obs_id)
    neighbor = tree.find_text("16.5公里")[-1]
    _raw(tree, neighbor)["attributes"].update(type="UnidentifiedLabel", text=name)
    tree = UiTree(tree.raw)
    report = inspect_name(app, obs_id, observation(app, tree, obs_id), tree, name)
    assert report["reason"] == "another_result_may_have_this_name" and "row_texts" not in report


def test_neighbor_number_cannot_supply_the_waypoint_field(ws):
    app, obs_id = ws.app("qiankun_hm"), "obs-0056"
    tree = _tree(obs_id)
    neighbor = tree.find_text("16.5公里")[-1]
    _raw(tree, neighbor)["attributes"]["text"] = "C63"
    tree = UiTree(tree.raw)
    report = inspect_name(app, obs_id, observation(app, tree, obs_id), tree, "1号停车点")
    assert report["row_observation"] == "observed" and tree.find_text("C63")
    assert "C63" not in report["row_texts"] and report["number_requirement"] == "pending"


def test_clipped_optional_field_is_omitted_without_blocking_the_name(ws):
    app, obs_id = ws.app("qiankun_hm"), "obs-0046"
    tree = _tree(obs_id)
    number = tree.find_text("I1")[-1]
    _raw(tree, number)["attributes"]["bounds"] = "[100,940][160,987]"
    tree = UiTree(tree.raw)
    report = inspect_name(app, obs_id, observation(app, tree, obs_id), tree, "1号车位")
    assert report["row_observation"] == "observed", report
    assert "I1" not in report["row_texts"] and report["omitted_text_nodes"] == 1
    assert report["number_requirement"] == "pending"


def test_cli_reads_the_collector_selected_tree_and_returns_no_product_verdict(ws, tmp_path, monkeypatch, capsys):
    app, obs_id = ws.app("qiankun_hm"), "obs-0056"
    tree = _tree(obs_id)
    record = observation(app, tree, obs_id)
    record["tree"]["path"] = f"obs/{obs_id}/tree_recheck.json"
    directory = tmp_path / "obs" / obs_id
    write_json(directory / "observation.json", record)
    write_json(directory / "tree.json", {})
    write_json(directory / "tree_recheck.json", tree.raw)
    monkeypatch.setenv("VPD_WORKSPACE", str(ws.root))
    monkeypatch.setattr(sys, "argv", ["inspect", "--evidence-dir", str(tmp_path), "--obs-id", obs_id,
                                     "--name", "1号停车点"])
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["row_observation"] == "observed" and report["product_verdict"] == "not_evaluated"
