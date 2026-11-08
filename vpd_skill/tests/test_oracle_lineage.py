"""Issue #187: declared source identity, not source authenticity; synthetic data only."""
import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import world
from verdict_fixtures import NAV_CASE

from vpdtest import cases as C
from vpdtest.case_readiness import readiness
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

SKILL = Path(__file__).resolve().parent.parent
ORIGIN = "session-lineage-origin"
OTHER = "session-lineage-independent"
RUN = "run-20261108-120000-abcd"
REVIEWER = "pytest-synthetic-lineage"
BASIS = "Isolated synthetic protocol fixture, not a production approval."
KID = "K-TEST-LINEAGE"


def asset(ws, source, sources=None):
    knowledge = KnowledgeStore(ws.root)
    knowledge.add({"id": KID, "kind": "product_rule", "subject": "pytest.lineage",
                   "statement": "Synthetic my-tab contract", "expected": "my_tab",
                   "applies_to": {"app": "motor_hm"}, "source": source})
    case = ws.load_case(NAV_CASE)
    case.update(id="TC-TEST-LINEAGE", status="draft", history=[],
                steps=[{"id": "S1", "action": "launch"},
                       {"id": "S2", "action": "tap", "target": "tab_my", "expect_state": "my_tab"}],
                assertions=[{"id": "A1", "at": "S2", "type": "state_is", "state": "my_tab",
                             "oracle_refs": [KID]}],
                sources=sources if sources is not None else [{"type": "session", "run": ORIGIN}],
                knowledge_used=[{"id": KID, "rationale": "Synthetic declared source comparison."}])
    write_json(ws.case_path(case["id"]), case)
    return case, knowledge


def lint(ws, case, knowledge):
    return C.lint(case, ws.app(case["app"]), knowledge, ws.policy(), runs_dir=ws.runs_dir)


@pytest.mark.parametrize("identity", [ORIGIN, RUN, "session-会话", "session-Name.X", "session-name with spaces",
                                      "session-é", "session-x obs-0001"])
@pytest.mark.parametrize("ref", ["{id}", "runs/{id}", "{id}/obs/obs-0001", "runs/{id}/obs/obs-0001"])
@pytest.mark.parametrize("reverse", [False, True])
def test_exact_supported_refs_match_structured_run(verdict_ws, identity, ref, reverse):
    left, right = {"run": identity}, {"ref": ref.format(id=identity)}
    if reverse:
        left, right = right, left
    case, knowledge = asset(verdict_ws, {"type": "observation", **left},
                            [{"type": "session", **right}])
    assert any("不独立" in row["problem"] for row in lint(verdict_ws, case, knowledge))


@pytest.mark.parametrize("source,sources", [
    ({"run": OTHER, "ref": ORIGIN}, [{"run": ORIGIN}]),
    ({"run": ORIGIN, "ref": OTHER}, [{"run": ORIGIN}]),
    ({"ref": ORIGIN}, [{"run": OTHER}, {"ref": ORIGIN}]),
    ({"ref": ORIGIN}, [{"run": OTHER, "ref": ORIGIN}]),
    ({"ref": OTHER}, [{"run": ORIGIN, "ref": OTHER}]),
    ({"run": "explicit-legacy-id", "ref": "free text"}, [{"run": "explicit-legacy-id"}]),
    ({"run": "session-会话", "ref": "说明"}, [{"run": "session-会话"}]),
])
def test_all_declared_identities_participate(verdict_ws, source, sources):
    case, knowledge = asset(verdict_ws, {"type": "observation", **source}, sources)
    assert any("不独立" in row["problem"] for row in lint(verdict_ws, case, knowledge))


@pytest.mark.parametrize("ref", [OTHER, ORIGIN + "-other", ORIGIN + "_other", ORIGIN + ".other",
                                 "prefix-" + ORIGIN, "prefix/" + ORIGIN, ORIGIN.upper(),
                                 "说明 " + ORIGIN, ORIGIN + "（说明）", ORIGIN + "\n",
                                 ORIGIN + " obs-0001 extra", ORIGIN + "/obs/obs-0001/tree.json",
                                 ORIGIN + " obs-0001", ORIGIN + "/obs/obs-0000", ORIGIN + "/obs/obs-001",
                                 ORIGIN + "/../" + OTHER, "https://host/runs/" + ORIGIN,
                                 ORIGIN + "、" + OTHER, "session-lineage-orıgin", "session-lineage-origin中"])
def test_ref_is_not_arbitrary_substring_or_normalized_identity(verdict_ws, ref):
    case, knowledge = asset(verdict_ws, {"type": "observation", "ref": ref})
    assert lint(verdict_ws, case, knowledge) == []


@pytest.mark.parametrize("missing", [None, "", "  ", 0, False, [], {}, "session-\n", "session-A\x7f",
                                     "session-A/../B", "session-A.", "session-A "])
def test_invalid_or_missing_run_values_do_not_match_each_other(verdict_ws, missing):
    # Knowledge.add accepts a normal source; malformed legacy metadata is a direct-file fixture.
    case, _knowledge = asset(verdict_ws, {"type": "observation", "ref": OTHER},
                            [{"type": "text_case", "run": missing, "ref": "synthetic manual case"}])
    path = verdict_ws.root / "knowledge/items.json"
    data = read_json(path)
    next(item for item in data["items"] if item["id"] == KID)["source"]["run"] = missing
    write_json(path, data)
    assert lint(verdict_ws, case, KnowledgeStore(verdict_ws.root)) == []


def test_independent_confirmed_observation_and_non_observation_keep_existing_contract(verdict_ws):
    case, knowledge = asset(verdict_ws, {"type": "observation", "ref": OTHER},
                            [{"type": "text_case", "ref": "synthetic manual case"}])
    assert knowledge.oracle_status([KID], {"app": "motor_hm"})["status"] == "unconfirmed"
    knowledge.confirm(KID, REVIEWER, basis=BASIS)
    assert knowledge.oracle_status([KID], {"app": "motor_hm"})["status"] == "confirmed"
    assert lint(verdict_ws, case, knowledge) == []
    C.approve(verdict_ws, case["id"], knowledge, REVIEWER, BASIS)
    # Non-observation provenance is not reinterpreted as self-observation.
    data = read_json(verdict_ws.root / "knowledge/items.json")
    next(item for item in data["items"] if item["id"] == KID)["source"] = {"type": "reviewer", "run": ORIGIN}
    write_json(verdict_ws.root / "knowledge/items.json", data)
    case["sources"] = [{"type": "session", "run": ORIGIN}]
    assert lint(verdict_ws, case, KnowledgeStore(verdict_ws.root)) == []


@pytest.mark.parametrize("source_type", ["requirement_doc", "text_case", "repo_code", "reviewer", "skill_policy",
                                        "model_inference", "test_goal"])
def test_only_observation_source_type_is_subject_to_lineage_gate(verdict_ws, source_type):
    case, knowledge = asset(verdict_ws, {"type": source_type, "run": ORIGIN, "ref": ORIGIN})
    assert lint(verdict_ws, case, knowledge) == []


def test_unicode_identity_is_exact_without_normalization(verdict_ws):
    case, knowledge = asset(verdict_ws, {"type": "observation", "ref": "session-e\u0301"},
                            [{"type": "session", "run": "session-é"}])
    assert lint(verdict_ws, case, knowledge) == []


def test_oversized_observation_locator_stays_unparsed(verdict_ws):
    case, knowledge = asset(verdict_ws, {"type": "observation", "ref": ORIGIN + "/obs/obs-" + "1" * 5000})
    assert lint(verdict_ws, case, knowledge) == []


def snapshot(ws):
    """Original case/knowledge/observations stay byte-for-byte; ignore transient case locks."""
    return {str(p.relative_to(ws.root.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
            for base in (ws.root, ws.runs_dir) for p in base.rglob("*")
            if p.is_file() and ".case-locks" not in p.parts}


def test_current_same_origin_blocks_formal_gates_even_when_review_binding_matches(verdict_ws, monkeypatch):
    ws = verdict_ws
    case, knowledge = asset(ws, {"type": "observation", "ref": OTHER})
    knowledge.confirm(KID, REVIEWER, basis=BASIS)
    C.approve(ws, case["id"], knowledge, REVIEWER, BASIS)
    case = ws.load_case(case["id"])
    approved = C.review_status(ws, case, knowledge)
    frozen = knowledge.snapshot()
    assert approved["valid"]
    # Explicit source-file edit, not a claimed public knowledge edit command. No old run is rewritten.
    path = ws.root / "knowledge/items.json"
    data = read_json(path)
    next(item for item in data["items"] if item["id"] == KID)["source"]["ref"] = ORIGIN
    write_json(path, data)
    knowledge = KnowledgeStore(ws.root)
    review = C.review_status(ws, case, knowledge)
    assert review["current"] == approved["current"] == review["record"]["binding"]
    assert review["required"] and not review["valid"] and review["state"] == "stale"
    assert "不独立" in review["lineage_issues"][0]["problem"]
    assert C.review_status(ws, case, frozen)["valid"]  # Run snapshots retain their original source metadata.
    before = snapshot(ws)
    assert any("不独立" in row["problem"] for row in lint(ws, case, knowledge))
    for action in (lambda: C.approve(ws, case["id"], knowledge, REVIEWER, BASIS),
                   lambda: C.require_review(ws, case, knowledge),
                   lambda: C.preflight_batch(ws, [case["id"]]),
                   lambda: C.promote(ws, case["id"], "candidate", knowledge, dry_run=True),
                   lambda: C.promote(ws, case["id"], "candidate", knowledge)):
        with pytest.raises(PermissionError, match="不独立"):
            action()
        assert snapshot(ws) == before
    row = readiness(ws, [case["id"]], to="candidate")["results"][0]
    assert row["promotion"]["status"] == "blocked" and "不独立" in row["promotion"]["reason"]
    assert snapshot(ws) == before

    def no_device(*args, **kwargs):
        pytest.fail("formal source rejection must precede device creation")

    monkeypatch.setattr(ws, "device", no_device)
    with pytest.raises(PermissionError, match="不独立"):
        run_case(ws, case=case, device_spec=world("motor_hm_base.json"), require_asset_review=True)
    assert snapshot(ws) == before


def test_advisory_scope_and_informal_diagnostics_are_unchanged(verdict_ws):
    ws = verdict_ws
    case, knowledge = asset(ws, {"type": "observation", "ref": ORIGIN})
    knowledge.confirm(KID, REVIEWER, basis=BASIS)
    advisory = deepcopy(case)
    advisory["assertions"][0]["advisory"] = True
    assert not any("不独立" in row["problem"] for row in lint(ws, advisory, knowledge))
    result = run_case(ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert result["informal"] is True and not result["asset_review"]["valid"]
    with pytest.raises(PermissionError, match="不独立"):
        C.promote(ws, case["id"], "candidate", knowledge, dry_run=True)


def test_real_cli_sessions_ref_source_and_independent_controls(verdict_ws, tmp_path):
    """Real public CLI + Replay, with actual session evidence and untouched run summaries."""
    ws = verdict_ws
    log = []

    def cli(*args, code=0):
        command = [sys.executable, "-m", "vpdtest", "--workspace", str(ws.root), *args]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(command, check=False, cwd=SKILL, env=env, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        log.append({"argv": command, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        write_json(tmp_path / "cli-log.json", log)
        assert result.returncode == code, log[-1]
        return json.loads(result.stdout)

    for name in ("lineage-origin", "lineage-independent"):
        for action in (("launch",), ("tap", "--target", "tab_my", "--expect", "my_tab")):
            out = cli("act", *action, "--app", "motor_hm", "--session", name,
                      "--device", world("motor_hm_base.json"))
            assert out["result"]["outcome"] == "passed"
    cli("case", "from-session", "--session", "lineage-origin", "--new-id", "TC-LINEAGE-CLI",
        "--app", "motor_hm", "--goal", "Synthetic declared lineage")
    template = ws.load_case("TC-LINEAGE-CLI")
    assert template["sources"][0]["run"] == ORIGIN
    for mode in ("ref", "run", "independent", "manual"):
        kid, cid = "K-CLI-" + mode, "TC-CLI-" + mode
        source = {"type": "observation", "ref": ORIGIN if mode in ("ref", "run") else OTHER}
        if mode == "run":
            source["run"] = ORIGIN
        rule = {"id": kid, "kind": "product_rule", "subject": "pytest.cli.lineage", "expected": "my_tab",
                "statement": "Synthetic my-tab contract", "applies_to": {"app": "motor_hm"}, "source": source}
        rule_path = tmp_path / (kid + ".json")
        write_json(rule_path, rule)
        cli("knowledge", "add", str(rule_path))
        cli("knowledge", "confirm", kid, "--reviewer", REVIEWER, "--basis", BASIS)
        case = deepcopy(template)
        case.update(id=cid, purpose="Synthetic source identity contract", preconditions=["Bundled Replay fixture"],
                    cleanup=[{"action": "stop_app"}],
                    assertions=[{"id": "A1", "at": "S2", "type": "state_is", "state": "my_tab",
                                 "oracle_refs": [kid]}],
                    knowledge_used=[{"id": kid, "rationale": "Synthetic source comparison."}])
        if mode == "manual":
            case.pop("origin", None)
            case.update(status="draft", history=[], sources=[{"type": "text_case", "ref": "Synthetic manual case"}],
                        steps=[{"id": "S1", "action": "launch"},
                               {"id": "S2", "action": "tap", "target": "tab_my", "expect_state": "my_tab"}])
        write_json(ws.case_path(cid), case)
        before = snapshot(ws)
        out = cli("case", "lint", cid)
        if mode in ("ref", "run"):
            assert any("不独立" in row["problem"] for row in out["issues"])
            assert "不独立" in cli("case", "approve", cid, "--reviewer", REVIEWER, "--basis", BASIS,
                                 code=3)["message"]
            row = cli("case", "readiness", cid, "--to", "candidate")["results"][0]
            assert row["promotion"]["status"] == "blocked"
            assert "不独立" in cli("case", "promote", cid, "--to", "candidate", code=3)["message"]
            assert snapshot(ws) == before
            write_json(ws.root / "suites/lineage.json", {"id": "lineage", "cases": [cid]})
            before = snapshot(ws)
            assert "不独立" in cli("suite", "lineage", "--device", world("motor_hm_base.json"), code=3)["message"]
            assert snapshot(ws) == before
            continue
        assert out["issues"] == []
        assert snapshot(ws) == before
        assert cli("case", "approve", cid, "--reviewer", REVIEWER, "--basis", BASIS)["asset_review"]["valid"]
        cli("case", "promote", cid, "--to", "candidate")
        runs = [cli("run", cid, "--device", world("motor_hm_base.json"), "--record-replay") for _ in range(2)]
        assert len({row["run_id"] for row in runs}) == 2
        assert all(row["verdict"] == "passed" for row in runs)
        before = snapshot(ws)
        row = cli("case", "readiness", cid, "--to", "replay_verified")["results"][0]
        assert row["promotion"]["status"] == "eligible"
        assert snapshot(ws) == before
        cli("case", "promote", cid, "--to", "replay_verified")
        row = cli("case", "readiness", cid, "--to", "regression", "--reviewer", REVIEWER,
                  "--allow-offline")["results"][0]
        assert row["promotion"]["status"] == "eligible"


@pytest.mark.parametrize("source_type", ["session", "text_case"])
def test_ordinary_regression_run_with_new_same_origin_is_only_informal(verdict_ws, source_type):
    """Real Replay/promote cycle; even a manual asset with no exploration signals cannot stay formal."""
    ws = verdict_ws
    case, knowledge = asset(ws, {"type": "observation", "ref": OTHER},
                            [{"type": source_type, "run": ORIGIN, "ref": "Synthetic declared case source"}])
    if source_type == "text_case":
        assert C.exploration_signals(case) == []
    knowledge.confirm(KID, REVIEWER, basis=BASIS)
    C.approve(ws, case["id"], knowledge, REVIEWER, BASIS)
    C.promote(ws, case["id"], "candidate", knowledge)
    for _ in range(2):
        result = run_case(ws, case_id=case["id"], device_spec=world("motor_hm_base.json"), propose=False)
        assert result["verdict"]["verdict"] == "passed"
        C.record_replay(ws, case["id"], result)
    C.promote(ws, case["id"], "replay_verified", knowledge)
    C.promote(ws, case["id"], "regression", knowledge, reviewer=REVIEWER, allow_offline=True)
    before = run_case(ws, case_id=case["id"], device_spec=world("motor_hm_base.json"), propose=False)
    assert before["informal"] is False and before["asset_review"]["valid"]
    original_runs = {str(p): p.read_bytes() for p in ws.runs_dir.rglob("run.json")}
    case_bytes = ws.case_path(case["id"]).read_bytes()
    path = ws.root / "knowledge/items.json"
    data = read_json(path)
    next(item for item in data["items"] if item["id"] == KID)["source"]["ref"] = ORIGIN
    write_json(path, data)  # Explicit metadata-edit fixture; not a public CLI edit claim.
    knowledge_bytes = path.read_bytes()
    after = run_case(ws, case_id=case["id"], device_spec=world("motor_hm_base.json"), propose=False)
    assert after["informal"] is True
    review = after["asset_review"]
    assert review["required"] and not review["valid"] and review["state"] == "stale"
    assert review["current"] == before["asset_review"]["current"] == review["record"]["binding"]
    assert "不独立" in review["lineage_issues"][0]["problem"]
    report = (Path(after["dir"]) / "report.md").read_text(encoding="utf-8")
    assert "非正式运行" in report and "`stale`" in report
    assert "不独立" in report and "既有批准也不能作为当前有效审核" in report
    row = readiness(ws, [case["id"]])["results"][0]
    assert row["review"]["required"] and not row["review"]["valid"]
    assert row["review"]["changed_binding_fields"] == []
    assert any("不独立" in issue["problem"] for issue in row["issues"])
    assert ws.case_path(case["id"]).read_bytes() == case_bytes and path.read_bytes() == knowledge_bytes
    assert all(Path(p).read_bytes() == raw for p, raw in original_runs.items())
    with pytest.raises(PermissionError, match="不独立"):
        C.preflight_batch(ws, [case["id"]])
