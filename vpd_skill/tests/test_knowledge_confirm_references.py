"""#45: confirmation reports declared case references, never predicted impact."""
from copy import deepcopy
import json

import pytest
from verdict_fixtures import NAV_CASE, NAV_RULE

from vpdtest import cases as C
from vpdtest import cli, commands, runner, workspace
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import read_json, write_json


@pytest.fixture
def reference_rig(verdict_ws, monkeypatch):
    ws = verdict_ws
    template = ws.load_case(NAV_CASE)
    for path in (ws.root / "cases").glob("*.json"):
        path.unlink()  # Only this fixture's isolated synthetic corpus.
    monkeypatch.setattr(workspace, "load_config", lambda: {})

    def forbidden(*args, **kwargs):
        raise AssertionError("Reference discovery must not operate a device, run tests, or approve assets")

    monkeypatch.setattr(workspace.Workspace, "device", forbidden)
    monkeypatch.setattr(runner, "run_case", forbidden)
    monkeypatch.setattr(commands, "run_case", forbidden)
    monkeypatch.setattr(C, "approve", forbidden)
    return ws, template


def _case(rig, cid, **changes):
    ws, template = rig
    case = deepcopy(template)
    case.update(id=cid, steps=[{"id": "S1", "action": "launch"}], assertions=[], cleanup=[],
                knowledge_used=[], asset_review={"fixture_only": "must remain byte-identical"})
    case.pop("safety", None)
    case.update(changes)
    write_json(ws.case_path(cid), case)
    return case


def _confirm(ws, capsys, *options):
    code = cli.main(["--workspace", str(ws.root), "knowledge", "confirm", NAV_RULE,
                     "--reviewer", "pytest-synthetic", "--basis", "Only this temporary synthetic rule", *options])
    return code, json.loads(capsys.readouterr().out)


def _references(out, *, status="complete"):
    refs = out["references"]
    assert refs["status"] == status
    assert refs["tests"] == "not_scanned"
    assert isinstance(refs["note"], str) and refs["note"].strip()
    assert isinstance(refs["cases"], list)
    if status == "incomplete":
        assert refs["errors"]
    return refs


@pytest.mark.parametrize("placement", ["assertion", "step", "flow", "recipe", "cleanup", "safe_stop"])
def test_cli_lists_direct_and_expanded_oracle_declarations(reference_rig, capsys, placement):
    ws, _ = reference_rig
    oracle = {"id": "S1", "action": "wait", "oracle_refs": [NAV_RULE]}
    changes = {}
    if placement == "assertion":
        changes["assertions"] = [{"id": "A1", "at": "S1", "type": "state_is", "state": "car_tab",
                                  "oracle_refs": [NAV_RULE]}]
    elif placement == "flow":
        path = ws.root / "apps/motor_hm/flows.json"
        data = read_json(path, {"flows": {}})
        data["flows"]["synthetic_reference_flow"] = {"steps": [oracle]}
        write_json(path, data)
        changes["steps"] = [{"id": "S1", "action": "flow", "flow": "synthetic_reference_flow"}]
    elif placement == "recipe":
        path = ws.root / "apps/motor_hm/states.json"
        data = read_json(path)
        data["recipes"]["synthetic_reference_state"] = [oracle]
        write_json(path, data)
        changes["steps"] = [{"id": "S1", "action": "ensure_state", "state": "synthetic_reference_state"}]
    elif placement == "cleanup":
        changes["cleanup"] = [oracle]
    elif placement == "safe_stop":
        changes["safety"] = {"safe_stop": [oracle]}
    else:
        changes["steps"] = [oracle]
    case = _case(reference_rig, "TC-SYNTHETIC-REF", **changes)
    before = ws.case_path(case["id"]).read_bytes()
    code, out = _confirm(ws, capsys)
    assert code == 0 and out["id"] == NAV_RULE and out["status"] == "confirmed"
    assert _references(out)["cases"] == [{"case": case["id"], "app": "motor_hm", "uses": ["oracle_refs"]}]
    assert ws.case_path(case["id"]).read_bytes() == before


def test_metadata_only_is_distinct_from_oracle_consumers_and_unrelated_rules(reference_rig, capsys):
    ws, _ = reference_rig
    _case(reference_rig, "TC-SYNTHETIC-METADATA", knowledge_used=[{"id": NAV_RULE, "applicability": "unknown"}])
    _case(reference_rig, "TC-SYNTHETIC-BOTH", knowledge_used=[{"id": NAV_RULE}],
          assertions=[{"id": "A1", "oracle_refs": [NAV_RULE]}])
    _case(reference_rig, "TC-SYNTHETIC-OTHER", knowledge_used=[{"id": "K-UNRELATED"}],
          assertions=[{"id": "A1", "oracle_refs": ["K-UNRELATED"]}])
    code, out = _confirm(ws, capsys)
    assert code == 0
    rows = {r["case"]: r for r in _references(out)["cases"]}
    assert set(rows) == {"TC-SYNTHETIC-METADATA", "TC-SYNTHETIC-BOTH"}
    assert rows["TC-SYNTHETIC-METADATA"]["uses"] == ["knowledge_used"]
    assert set(rows["TC-SYNTHETIC-BOTH"]["uses"]) == {"oracle_refs", "knowledge_used"}


def test_empty_reference_list_is_not_an_assertion_of_no_test_impact(reference_rig, capsys):
    ws, _ = reference_rig
    _case(reference_rig, "TC-SYNTHETIC-UNRELATED")
    code, out = _confirm(ws, capsys)
    assert code == 0 and _references(out)["cases"] == []


@pytest.mark.parametrize("broken", ["json", "unknown_app", "missing_flow", "recursive_flow", "missing_recipe",
                                    "empty_flow", "missing_flow_steps", "null_flow_steps", "object_flow_steps",
                                    "object_recipe"])
def test_incomplete_reference_scan_does_not_misreport_a_successful_confirmation(reference_rig, capsys, broken):
    ws, _ = reference_rig
    good = _case(reference_rig, "TC-SYNTHETIC-GOOD", assertions=[{"id": "A1", "oracle_refs": [NAV_RULE]}])
    bad = _case(reference_rig, "TC-SYNTHETIC-BROKEN")
    path = ws.case_path(bad["id"])
    if broken == "json":
        path.write_text("{invalid synthetic json", encoding="utf-8")
    elif broken == "unknown_app":
        bad["app"] = "synthetic_missing_app"
        write_json(path, bad)
    elif broken in ("missing_recipe", "object_recipe"):
        bad["steps"] = [{"id": "S1", "action": "ensure_state", "state": "synthetic_missing_state"}]
        write_json(path, bad)
        if broken == "object_recipe":
            states = ws.root / "apps/motor_hm/states.json"
            data = read_json(states)
            data["recipes"]["synthetic_missing_state"] = {}
            write_json(states, data)
    else:
        name = "synthetic_reference_flow"
        bad["steps"] = [{"id": "S1", "action": "flow", "flow": name}]
        write_json(path, bad)
        if broken != "missing_flow":
            flows = ws.root / "apps/motor_hm/flows.json"
            data = read_json(flows, {"flows": {}})
            data["flows"][name] = {
                "recursive_flow": {"steps": [{"id": "RECURSE", "action": "flow", "flow": name}]},
                "empty_flow": {},
                "missing_flow_steps": {"desc": "Synthetic flow with no explicit steps"},
                "null_flow_steps": {"steps": None},
                "object_flow_steps": {"steps": {}},
            }[broken]
            write_json(flows, data)
    before = {p: p.read_bytes() for p in (ws.root / "cases").glob("*.json")}
    code, out = _confirm(ws, capsys)
    assert code == 0 and out["status"] == "confirmed"
    refs = _references(out, status="incomplete")
    assert any(r["case"] == good["id"] and "oracle_refs" in r["uses"] for r in refs["cases"])
    assert bad["id"] in json.dumps(refs["errors"])
    assert {p: p.read_bytes() for p in before} == before
    assert KnowledgeStore(ws.root).get(NAV_RULE)["status"] == "confirmed"


def test_confirmation_preserves_records_hashes_and_does_not_persist_the_summary(reference_rig, capsys):
    ws, _ = reference_rig
    case = _case(reference_rig, "TC-SYNTHETIC-UNCHANGED", assertions=[{"id": "A1", "oracle_refs": [NAV_RULE]}])
    kn = KnowledgeStore(ws.root)
    other = {i["id"]: i for i in kn.items() if i["id"] != NAV_RULE}
    before = {p: p.read_bytes() for p in ws.root.rglob("*") if p.is_file() and p != kn.path}
    dep = C.dependency_hash(case, ws.app(case["app"]), kn)
    code, out = _confirm(ws, capsys)
    assert code == 0
    _references(out)
    kn = KnowledgeStore(ws.root)
    assert {i["id"]: i for i in kn.items() if i["id"] != NAV_RULE} == other
    assert "references" not in kn.get(NAV_RULE)
    assert C.dependency_hash(case, ws.app(case["app"]), kn) == dep
    assert {p: p.read_bytes() for p in before} == before
    assert not ws.runs_dir.exists()


@pytest.mark.parametrize("missing", ["reviewer", "basis"])
def test_invalid_confirmation_still_fails_without_writing(reference_rig, capsys, missing):
    ws, _ = reference_rig
    kn = KnowledgeStore(ws.root)
    data = read_json(kn.path)
    next(i for i in data["items"] if i["id"] == NAV_RULE)["source"]["type"] = "observation"
    write_json(kn.path, data)
    before = kn.path.read_bytes()
    args = ["--workspace", str(ws.root), "knowledge", "confirm", NAV_RULE]
    if missing != "reviewer":
        args += ["--reviewer", "pytest-synthetic"]
    if missing != "basis":
        args += ["--basis", "Only isolated synthetic evidence"]
    if missing == "reviewer":
        with pytest.raises(SystemExit) as error:
            cli.main(args)
        assert error.value.code == 3
    else:
        assert cli.main(args) == 3
    assert json.loads(capsys.readouterr().out)["error"]
    assert kn.path.read_bytes() == before
