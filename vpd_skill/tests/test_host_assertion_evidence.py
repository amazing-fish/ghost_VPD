"""A completed action is not proof of a matching test fixture (synthetic UI)."""
from copy import deepcopy

import pytest

from test_goal_loop import _lab_world, cli
from vpdtest.commands import _host_followup
from vpdtest.util import read_json, write_json


@pytest.fixture
def evidence_run(ws, tmp_path):
    from vpdtest.runner import run_case
    device = _lab_world(tmp_path)
    steps = [{"id": "OPEN", "action": "launch", "retry": 0}]
    targets_file = ws.app("motor_hm").dir / "targets.json"
    targets = read_json(targets_file)
    targets["targets"]["absent_row"] = {"label": "合成行", "strategies": [{"kind": "text", "value": "不存在的行"}]}
    write_json(targets_file, targets)

    def rule(name, text, **extra):
        return {"id": name, "at": "OPEN", "type": "text_present",
                "text": text, "oracle_refs": [], **extra}
    case = {"id": "HOST-EVIDENCE", "app": "motor_hm", "version": 1, "status": "draft",
            "steps": steps, "cleanup": [], "assertions": [
                rule("WRONG", "未出现的编号"), rule("MATCH", "唯一入口"),
                rule("HINT", "未出现的编号", advisory=True),
                rule("SCOPE", "未出现的编号", within="absent_row")]}
    write_json(ws.case_path(case["id"]), case)
    return ws, case, device, run_case(ws, case=case, device_spec=device, propose=False)


def test_cli_routes_bound_assertion_evidence_without_changing_verdict(evidence_run):
    ws, case, device, run = evidence_run
    out = cli(ws, "run", case["id"], "--device", device)
    assert all(step["outcome"] == "passed" for step in run["steps"])
    assert out["verdict"] == run["verdict"]["verdict"] == "pending"
    offer = out["host_followup"]
    observed = {r["id"]: r for r in out["assertions"]}
    assert offer["next"] == "observe_only" and not offer["requires_human"]
    assert not offer["action_authorized"] and not offer["run_budget_inherited"]
    assert offer["steps"] == []
    assert offer["assertions"] == [
        {"id": aid, "step": "OPEN", "status": status, "final_obs": observed[aid]["evidence"]["obs"]}
        for aid, status in (("WRONG", "observation_mismatch"), ("SCOPE", "scope_undetermined"))]
    assert offer["notification_destination"] == "existing_authorized_task_channel"
    assert not offer.get("notification_authorized") and "notification_sent" not in offer


@pytest.mark.parametrize("change", ["ok", "wrong_observation", "advisory", "not_applicable",
                                    "confirmed_failure", "missing_id", "non_text", "not_run", "other_verdict", "guard_unknown",
                                    "evidence_missing", "execution_error"])
def test_assertion_route_does_not_guess(evidence_run, change):
    _, _, _, actual = evidence_run
    run = deepcopy(actual)
    a = run["assertions"][0]
    run["assertions"] = [a]
    if change == "ok": a["observed_holds"] = True
    elif change == "wrong_observation": a["evidence"]["obs"] = "obs-other"
    elif change == "advisory": a["advisory"] = True
    elif change == "not_applicable": a["applicable"] = False
    elif change == "confirmed_failure": a["result"] = "fail"
    elif change == "missing_id": a["id"] = ""
    elif change == "non_text": a["type"] = "visual_review"
    elif change == "other_verdict": run["verdict"]["verdict"] = "error"
    elif change == "evidence_missing": a["evidence"] = {}
    elif change == "execution_error": a["result"] = "error"
    elif change == "guard_unknown": a.pop("observed_holds")
    else: run["steps"][0]["outcome"] = "not_run"
    assert _host_followup(run) is None


def test_assertion_route_preserves_human_handoff(evidence_run):
    run = deepcopy(evidence_run[-1])
    run["cleanup"] = [{"effect_unknown": True}]
    offer = _host_followup(run)
    assert offer["next"] == "inspect" and offer["requires_human"]
    assert not offer["action_authorized"]


@pytest.mark.parametrize(("case", "routed"), [
    ("mismatch", True), ("empty", True), ("equal", False), ("missing-text", False),
    ("null-text", False), ("ambiguous", False), ("offscreen-duplicate", False),
    ("unpositioned-duplicate", False), ("clipped", False), ("stale", False),
    ("missing-frame", False), ("different-frame", False), ("unsuccessful-step", False),
    ("old-record", False),
])
def test_exact_target_text_handoff_uses_only_bound_readable_evidence(ws, tmp_path, case, routed):
    from test_search_name_row import _evaluate, _node, _oracle
    from vpdtest.tree import UiTree

    field = _node("SearchField", "[10,10][190,50]", id="__SearchField__search_input", text="A")
    fields = [field]
    if case == "equal":
        field["attributes"]["text"] = "B"
    elif case == "empty":
        field["attributes"]["text"] = ""
    elif case == "missing-text":
        del field["attributes"]["text"]
    elif case == "null-text":
        field["attributes"]["text"] = None
    elif case == "clipped":
        field["attributes"]["bounds"] = "[10,10][240,50]"
    elif case in ("ambiguous", "offscreen-duplicate", "unpositioned-duplicate"):
        other = deepcopy(field)
        if case == "offscreen-duplicate":
            other["attributes"]["bounds"] = "[10,210][190,260]"
        elif case == "unpositioned-duplicate":
            del other["attributes"]["bounds"]
        fields.append(other)
    search = _node("Search", "[0,0][200,100]", id="search_input", children=fields)
    tree = UiTree(_node("Column", "[0,0][200,100]", id="SearchPoiPage", children=[search]))
    definition = {"id": "QUERY", "at": "S3", "type": "target_text_equals",
                  "target": "qk_search_input", "expected": "B", "oracle_refs": []}
    assertion, = _evaluate(ws.app("qiankun_hm"), tree, _oracle(tmp_path, confirmed=False),
                           fresh=case != "stale", assertions=[definition])
    step = {"step_id": "S3", "outcome": "passed", "final_obs": "result-obs"}
    if case == "missing-frame":
        step.pop("final_obs")
    elif case == "different-frame":
        step["final_obs"] = "other-obs"
    elif case == "unsuccessful-step":
        step["outcome"] = "not_run"
    elif case == "old-record":
        assertion["evidence"].pop("unique_target_text", None)
    run = {"run_id": "test-run", "steps": [step], "assertions": [assertion],
           "verdict": {"verdict": "pending"}}
    original = deepcopy(run)
    offer = _host_followup(run)
    assert run == original
    if routed:
        assert assertion["result"] == "pending" and assertion["observed_holds"] is False
        assert offer["read_only"] and offer["next"] == "observe_only"
        assert not offer["action_authorized"] and "notification_sent" not in offer
        assert offer["assertions"] == [
            {"id": "QUERY", "step": "S3", "status": "observation_mismatch", "final_obs": "result-obs"}]
    else:
        assert offer is None
