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
