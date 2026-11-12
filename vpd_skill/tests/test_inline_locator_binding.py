from copy import deepcopy
import pytest
from conftest import confirm, widen_scope, world
from vpdtest.experience import ExperienceStore
from vpdtest.runner import run_case
from vpdtest.util import write_json, read_json


@pytest.fixture
def inline_rig(ws):
    for kid in ("K-MOT-001", "K-MOT-002"):
        widen_scope(ws, kid, app_version=["3.0.3.200", "3.0.4.100"])
    confirm(ws, "K-MOT-001", "K-MOT-002")
    target = deepcopy(ws.app("motor_hm").targets["tab_my"])
    target["label"] = "shared-navigation"
    # Main still contains the legacy ambiguous first strategy: this forces fallback.
    if target["strategies"][0].get("within"):
        target["strategies"].insert(0, {"kind": "text", "value": "我的"})
    for cid in ("INLINE-ORIGIN", "INLINE-INDEPENDENT"):
        case = deepcopy(ws.load_case("TC-MOTOR-HM-001"))
        case["id"] = cid
        for step in case["steps"]:
            if step.get("target") == "tab_my": step["target"] = deepcopy(target)
        write_json(ws.case_path(cid), case)
    spec = world("motor_hm_drift.json")
    run = run_case(ws, "INLINE-ORIGIN", device_spec=spec)
    assert run["verdict"]["verdict"] == "passed"
    store = ExperienceStore(ws.root, "motor_hm")
    xid = next(x for x in run["experience_candidates"] if store.get(x)["change"]["target"].startswith("inline:"))
    return ws, store, xid, spec


def test_inline_generation_validation_adoption_and_runtime(inline_rig):
    ws, store, xid, spec = inline_rig
    result = store.validate(xid, ["INLINE-ORIGIN", "INLINE-INDEPENDENT"], [spec],
        lambda cid, dev, overlay: run_case(ws, cid, device_spec=dev, overlay=overlay, propose=False))
    assert result["status"] == "validated", result
    store.adopt(xid)
    run = run_case(ws, "INLINE-INDEPENDENT", device_spec=spec, propose=False)
    assert run["verdict"]["verdict"] == "passed"
    assert not run["verdict"]["adapted"]


@pytest.mark.parametrize("change", ["removed", "strategy", "app", "forged_target", "path", "dynamic"])
def test_source_must_remain_current_not_candidate_claim(inline_rig, change):
    ws, store, xid, spec = inline_rig
    item = store.get(xid)
    case = ws.load_case("INLINE-ORIGIN")
    if change == "path":
        item["origin_case"] = "../INLINE-ORIGIN"
        store.save_item(item)
    elif change == "forged_target":
        item["change"]["target"] = "inline:invented"
        item["change"]["definition"] = next(s["target"] for s in case["steps"] if isinstance(s.get("target"), dict))
        store.save_item(item)
    else:
        if change == "app": case["app"] = "other"
        else:
            step = next(s for s in case["steps"] if isinstance(s.get("target"), dict))
            if change == "removed": step["target"] = "tab_my"
            elif change == "dynamic": step["target"]["strategies"][0]["value"] = "${var:label}"
            else: step["target"]["strategies"].reverse()
        write_json(ws.case_path("INLINE-ORIGIN"), case)
    before = store.items_path.read_bytes()
    with pytest.raises(PermissionError):
        store.validate(xid, ["INLINE-ORIGIN"], [spec], lambda *a: pytest.fail("must not execute"))
    assert before == store.items_path.read_bytes()


def test_same_label_different_definition_is_negative_control_not_coverage(inline_rig):
    ws, store, xid, spec = inline_rig
    case = ws.load_case("INLINE-INDEPENDENT")
    target = next(s["target"] for s in case["steps"] if isinstance(s.get("target"), dict))
    target["strategies"] = [target["strategies"][1]]
    write_json(ws.case_path(case["id"]), case)
    result = store.validate(xid, ["INLINE-ORIGIN", "INLINE-INDEPENDENT"], [spec],
        lambda cid, dev, overlay: run_case(ws, cid, device_spec=dev, overlay=overlay, propose=False))
    assert result["status"] == "candidate"
    assert result["exercised_cases"] == ["INLINE-ORIGIN"]
    with pytest.raises(PermissionError): store.adopt(xid)


@pytest.mark.parametrize("change", ["removed", "strategy"])
def test_adoption_rechecks_actual_independent_case(inline_rig, change):
    ws, store, xid, spec = inline_rig
    result = store.validate(xid, ["INLINE-ORIGIN", "INLINE-INDEPENDENT"], [spec],
        lambda cid, dev, overlay: run_case(ws, cid, device_spec=dev, overlay=overlay, propose=False))
    assert result["status"] == "validated"
    case = ws.load_case("INLINE-INDEPENDENT")
    step = next(s for s in case["steps"] if isinstance(s.get("target"), dict))
    if change == "removed": step["target"] = "tab_my"
    else: step["target"]["strategies"].reverse()
    write_json(ws.case_path(case["id"]), case)
    before = store.items_path.read_bytes()
    with pytest.raises(PermissionError): store.adopt(xid)
    assert store.items_path.read_bytes() == before


@pytest.mark.parametrize("container", ["flow", "recipe", "cleanup", "safe_stop"])
def test_inline_binding_expands_current_executable_sources(inline_rig, container):
    ws, store, xid, _ = inline_rig
    case = ws.load_case("INLINE-ORIGIN")
    step = next(s for s in case["steps"] if isinstance(s.get("target"), dict))
    case["steps"].remove(step)
    if container == "cleanup": case["cleanup"] = [step]
    elif container == "safe_stop": case.setdefault("safety", {})["safe_stop"] = [step]
    elif container == "flow":
        path = ws.app("motor_hm").dir / "flows.json"
        data = read_json(path, {"flows": {}})
        data["flows"]["inline-flow"] = {"steps": [step]}
        write_json(path, data)
        case["steps"].append({"id": "FLOW", "action": "flow", "flow": "inline-flow"})
    else:
        path = ws.app("motor_hm").dir / "states.json"
        data = read_json(path)
        data.setdefault("recipes", {})["inline-state"] = [step]
        write_json(path, data)
        case["steps"].append({"id": "STATE", "action": "ensure_state", "state": "inline-state"})
    write_json(ws.case_path(case["id"]), case)
    store._require_locator_binding(store.get(xid))


@pytest.mark.parametrize("cases,status", [(None, "validated"), ([], "validated"), (["INLINE-ORIGIN"], "validated"),
                                         (["INLINE-ORIGIN", "INLINE-INDEPENDENT"], "candidate")])
def test_inline_adoption_requires_recorded_valid_independent_exercise(inline_rig, cases, status):
    ws, store, xid, _ = inline_rig
    item = store.get(xid)
    item["status"] = "validated"
    item["validations"] = [{"status": status, "exercised_cases": cases}]
    store.save_item(item)
    before = store.items_path.read_bytes()
    with pytest.raises(PermissionError): store.adopt(xid)
    assert store.items_path.read_bytes() == before
