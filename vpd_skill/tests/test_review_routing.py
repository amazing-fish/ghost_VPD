"""Synthetic local evidence only. Never approves production assets."""
from copy import deepcopy
import pytest
from test_asset_review import _asset, _approve, _review
from test_goal_loop import cli
from vpdtest import cases as C
from vpdtest.knowledge import KnowledgeStore
from vpdtest.review_routing import route
from vpdtest.util import read_json, write_json


def evidence(ws, case, outcome="pass"):
    kn = KnowledgeStore(ws.root)
    binding = C._review_binding(ws, case, ws.app(case["app"]), kn, ws.policy())
    run = {"run_id": "run-fixture-review", "validation_level": "real_device",
           "case": {"id": case["id"], "hash": binding["content_hash"], "deps": binding["dependency_hash"]},
           "asset_review": {"current": binding}, "verdict": {"verdict": "passed" if outcome == "pass" else "failed", "execution": {"status": "completed"}},
           "assertions": [{"id": a["id"], "result": outcome, "verified": True,
                           "evidence": {"obs": "obs-0001", "tree": "obs/obs-0001/tree.json", "screenshot": "obs/obs-0001/screen.png"},
                           "oracle_refs": a["oracle_refs"], "oracle": {"status": "confirmed"},
                           "facts": {"app": "motor_hm", "app_version": "3.0.3.200"}}
                          for a in case["assertions"] if not a.get("advisory")]}
    path = ws.runs_dir / run["run_id"] / "run.json"
    write_json(path, run)
    write_json(path.parent / "obs/obs-0001/observation.json", {
        "obs_id": "obs-0001", "source": "hdc",
        "tree": {"status": "ok", "path": "obs/obs-0001/tree.json"},
        "screenshot": {"status": "ok", "path": "obs/obs-0001/screen.png"}})
    write_json(path.parent / "obs/obs-0001/tree.json", {"fixture": True})
    (path.parent / "obs/obs-0001/screen.png").write_bytes(b"synthetic-image-fixture")
    (path.parent / "events.jsonl").write_text('{"fixture":true}\n', encoding="utf8")
    return run, path


def routing(ws, case, rid=None):
    return route(ws, case, KnowledgeStore(ws.root), ws.app(case["app"]), ws.policy(), rid)


def approve_agent(ws, case, rid="run-fixture-review"):
    return C.approve(ws, case["id"], KnowledgeStore(ws.root), "independent-test-reviewer", "fixture review",
                     review_kind="agent", evidence_run=rid, independent_ref="fixture-review-thread")


@pytest.mark.parametrize("outcome", ["pass", "fail"])
def test_clear_evidence_can_be_reviewed_without_product_pass(ws, outcome):
    case = _asset(ws)
    run, _ = evidence(ws, case, outcome)
    before = (ws.root / "knowledge/items.json").read_bytes()
    assert routing(ws, case, run["run_id"])["route"] == "independent_agent"
    approved = approve_agent(ws, case)
    assert approved["status"] == "exploration_record"
    assert approved["asset_review"]["review_kind"] == "agent"
    assert _review(ws)["valid"]
    assert (ws.root / "knowledge/items.json").read_bytes() == before
    assert not approved.get("replays")


def test_missing_evidence_is_agent_work_not_human_signature(ws):
    case = _asset(ws)
    assert routing(ws, case)["route"] == "evidence_needed"
    before = ws.case_path(case["id"]).read_bytes()
    with pytest.raises(PermissionError):
        approve_agent(ws, case)
    assert ws.case_path(case["id"]).read_bytes() == before


@pytest.mark.parametrize("change", ["offline", "old", "wrong_case", "pending", "unverified", "missing_assertion", "scope", "refs", "context", "execution", "alerts"])
def test_unusable_evidence_never_approves(ws, change):
    case = _asset(ws)
    run, path = evidence(ws, case)
    if change == "offline": run["validation_level"] = "offline_replay"
    if change == "old": run["case"]["deps"] = "old"
    if change == "wrong_case": run["case"]["id"] = "other"
    if change == "pending": run["verdict"]["verdict"] = "pending"
    if change == "unverified": run["assertions"][0]["verified"] = False
    if change == "missing_assertion": run["assertions"] = []
    if change == "scope": run["assertions"][0]["facts"]["app_version"] = "9.9"
    if change == "refs": run["assertions"][0]["oracle_refs"] = []
    if change == "execution": run["verdict"]["execution"]["status"] = "error"
    if change == "alerts": run["verdict"]["alerts"] = ["hold"]
    if change == "context": run["asset_review"]["current"]["context_hash"] = "old"
    write_json(path, run)
    assert routing(ws, case, run["run_id"])["route"] == "evidence_needed"
    with pytest.raises(PermissionError): approve_agent(ws, case)


@pytest.mark.parametrize("risk", ["unknown", "conflict", "side_effect", "nested_effect"])
def test_unclear_or_high_impact_routes_to_human(ws, risk):
    case = _asset(ws)
    if risk in {"unknown", "conflict"}:
        p = ws.root / "knowledge/items.json"
        data = read_json(p)
        item = next(i for i in data["items"] if i["id"] == "K-MOT-001")
        if risk == "unknown": item["status"] = "unconfirmed"
        else:
            rival = deepcopy(item); rival.update(id="K-RIVAL", expected={"different": True})
            data["items"].append(rival)
        write_json(p, data)
    elif risk == "side_effect": case["authorizations"] = ["vehicle_motion"]
    else:
        case["cleanup"] = [{"action": "tap", "target": {"side_effect": "user_data", "strategies": []}}]
    write_json(ws.case_path(case["id"]), case)
    assert routing(ws, case)["route"] == "human_required"


def test_agent_approval_remains_portable_when_evidence_is_modified_or_removed(ws):
    case = _asset(ws); run, path = evidence(ws, case)
    approve_agent(ws, case)
    run["extra"] = "changed"
    write_json(path, run)
    assert _review(ws)["valid"]
    diagnostic = routing(ws, ws.load_case(case["id"]), run["run_id"])
    assert diagnostic["recorded_evidence_match"] is False
    with pytest.raises(PermissionError, match="不一致"):
        approve_agent(ws, case)
    path.unlink()
    assert routing(ws, ws.load_case(case["id"]), run["run_id"])["route"] == "evidence_needed"
    assert _review(ws)["valid"]


def test_revocation_requires_human_and_old_human_approval_remains_valid(ws):
    case = _asset(ws); evidence(ws, case)
    _approve(ws)
    assert _review(ws)["valid"]
    C.revoke_review(ws, case["id"], "human", "withdrawn")
    assert routing(ws, ws.load_case(case["id"]), "run-fixture-review")["route"] == "human_required"
    with pytest.raises(PermissionError): approve_agent(ws, case)


def test_independent_source_required_and_path_rejected(ws):
    case = _asset(ws); evidence(ws, case)
    with pytest.raises(ValueError, match="独立"):
        C.approve(ws, case["id"], KnowledgeStore(ws.root), "name", "basis", review_kind="agent",
                  evidence_run="run-fixture-review")
    assert routing(ws, case, "../elsewhere")["route"] == "evidence_needed"


def test_cli_routes_and_records_explicit_review_kind(ws):
    case = _asset(ws); evidence(ws, case)
    assert cli(ws, "case", "review-route", case["id"])["route"] == "evidence_needed"
    out = cli(ws, "case", "approve", case["id"], "--reviewer", "independent-reviewer", "--basis", "review",
              "--review-kind", "agent", "--evidence-run", "run-fixture-review", "--independent-ref", "review-thread")
    assert out["asset_review"]["valid"]


@pytest.mark.parametrize("file", ["events.jsonl", "obs/obs-0001/observation.json", "obs/obs-0001/tree.json", "obs/obs-0001/screen.png"])
def test_missing_raw_source_never_approves(ws, file):
    case = _asset(ws); run, path = evidence(ws, case)
    (path.parent / file).unlink()
    assert routing(ws, case, run["run_id"])["route"] == "evidence_needed"


def test_hash_is_of_checked_bytes_not_concurrent_replacement(ws, monkeypatch):
    import json
    from pathlib import Path
    case = _asset(ws); run, path = evidence(ws, case)
    original = Path.read_bytes
    def replace_after_read(self):
        raw = original(self)
        if self == path.resolve():
            changed = deepcopy(run); changed["verdict"]["verdict"] = "pending"
            self.write_text(json.dumps(changed), encoding="utf8")
        return raw
    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    approved = approve_agent(ws, case)
    monkeypatch.setattr(Path, "read_bytes", original)
    assert approved["asset_review"]["review_kind"] == "agent"
    assert _review(ws)["valid"]


def test_unclassified_target_needs_agent_work_not_human_signature(ws):
    case = _asset(ws)
    case["steps"][1]["target"] = {"strategies": []}
    assert routing(ws, case)["route"] == "evidence_needed"


def test_real_runner_format_remains_offline_until_real_evidence(ws):
    from conftest import world, widen_scope
    from vpdtest.runner import run_case
    case = _asset(ws)
    for kid in ("K-MOT-001", "K-MOT-002"):
        widen_scope(ws, kid, app_version="3.0.3.200-replay")
    run = run_case(ws, case["id"], device_spec=world("motor_hm_base.json"), propose=False)
    assert run["validation_level"] == "offline_replay"
    assert routing(ws, case, run["run_id"])["route"] == "evidence_needed"
    # Exercise real Runner serialization with clearly synthetic device provenance.
    path = ws.runs_dir / run["run_id"] / "run.json"
    if run["verdict"]["verdict"] not in {"passed", "failed"}:
        pytest.fail(str(run["verdict"]))
    run["validation_level"] = "real_device"
    write_json(path, run)
    for observation in path.parent.glob("obs/*/observation.json"):
        data = read_json(observation); data["source"] = "hdc"
        write_json(observation, data)
    decision = routing(ws, case, run["run_id"])
    assert decision["route"] == "independent_agent", decision


def initial_tree_evidence(ws):
    case = _asset(ws)
    run, path = evidence(ws, case)
    observation_path = path.parent / "obs/obs-0001/observation.json"
    observation = read_json(observation_path)
    relative = "obs/obs-0001/initial-tree.json"
    observation["tree_recheck"] = {"initial_tree": {"status": "empty", "path": relative, "nodes": 0}}
    write_json(observation_path, observation)
    initial = path.parent / relative
    write_json(initial, {})
    return case, run, initial, observation_path


def test_initial_tree_change_is_diagnostic_not_portable_review_revocation(ws):
    case, run, initial, _ = initial_tree_evidence(ws)
    assert routing(ws, case, run["run_id"])["route"] == "independent_agent"
    approve_agent(ws, case)
    assert _review(ws)["valid"]
    initial.write_bytes(initial.read_bytes() + b" ")
    assert _review(ws)["valid"]


@pytest.mark.parametrize("change", ["missing", "empty", "invalid_json", "other_observation", "traversal"])
def test_initial_tree_must_be_intact_inside_same_observation(ws, change):
    case, run, initial, observation_path = initial_tree_evidence(ws)
    approve_agent(ws, case)
    if change == "missing": initial.unlink()
    elif change == "empty": initial.write_bytes(b"")
    elif change == "invalid_json": initial.write_bytes(b"invalid")
    else:
        observation = read_json(observation_path)
        relative = "obs/obs-0002/tree.json" if change == "other_observation" else "obs/obs-0001/../outside.json"
        write_json(initial.parents[2] / relative, {})
        observation["tree_recheck"]["initial_tree"]["path"] = relative
        write_json(observation_path, observation)
    assert routing(ws, case, run["run_id"])["route"] == "evidence_needed"
    assert _review(ws)["valid"]
