"""Explicit no-cleanup declaration, isolated fixtures only."""
import json
from pathlib import Path
import pytest
from conftest import SKILL
from vpdtest import cases as C
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import write_json


def issues(ws, case):
    return C.lint(case, ws.app(case["app"]), KnowledgeStore(ws.root), ws.policy(), runs_dir=ws.runs_dir)


@pytest.mark.parametrize("flag,note", [(False, "navigation"), ("true", "navigation"), (True, ""), (True, "   "), (True, None)])
def test_empty_cleanup_requires_explicit_boolean_and_reason(ws, flag, note):
    case = ws.load_case("TC-HM-S01")
    case.update(cleanup_none=flag, cleanup_note=note)
    assert any(i["field"] == "cleanup" for i in issues(ws, case))


def test_s01_definition_preserves_review_but_invalidates_it(ws):
    case = ws.load_case("TC-HM-S01")
    assert not issues(ws, case)
    assert case["asset_review"]["binding"]["content_hash"] == "0b048a3b3dc6"
    assert C.review_status(ws, case)["state"] == "stale"
    assert case["cleanup"] == [] and case["cleanup_none"] is True
    assert not any(s["action"] == "ensure_state" for s in case["steps"])


@pytest.mark.parametrize("effect", ["user_data", "vehicle_motion"])
def test_cleanup_none_does_not_remove_effect_obligations(ws, effect):
    case = ws.load_case("TC-HM-S01")
    case["authorizations"] = [effect]
    found = issues(ws, case)
    assert found
    if effect == "user_data":
        assert any(i["field"] == "cleanup" for i in found)
    else:
        assert any("safety" in i["field"] for i in found)


def test_nested_user_data_without_declared_authorization_is_still_rejected(ws):
    case = ws.load_case("TC-HM-S01")
    app = ws.app(case["app"])
    app.flows["synthetic-write"] = {"steps": [{"action": "tap", "target": {"side_effect": "user_data", "strategies": [{"kind": "text", "value": "收藏"}]}}]}
    case["steps"].append({"id": "WRITE", "action": "flow", "flow": "synthetic-write"})
    found = C.lint(case, app, KnowledgeStore(ws.root), ws.policy(), runs_dir=ws.runs_dir)
    assert any("未申请 user_data" in i["problem"] for i in found)
    case["authorizations"] = ["user_data"]
    found = C.lint(case, app, KnowledgeStore(ws.root), ws.policy(), runs_dir=ws.runs_dir)
    assert any(i["field"] == "cleanup" for i in found)


def test_s01_offline_navigation_avoids_restart_and_extra_back(ws, tmp_path):
    world = json.loads((SKILL / "fixtures/worlds/motor_hm_base.json").read_text(encoding="utf8"))
    world["env_facts"]["account_state"] = "logged_in"
    world["app_version"] = "3.0.3.200"
    path = tmp_path / "synthetic-logged-in.json"
    write_json(path, world)
    run = run_case(ws, "TC-HM-S01", device_spec="replay:" + str(path), propose=False)
    assert run["validation_level"] == "offline_replay"
    assert run["verdict"]["verdict"] == "passed"
    assert [s["action"] for s in run["steps"]] == ["launch", "tap", "swipe", "tap", "back"]
    assert run["cleanup"] == [] and run["actions"] == 5
    assert run["asset_review"]["state"] == "stale"


@pytest.mark.parametrize("effect", ["user_data", "vehicle_motion", "vehicle_control", None])
@pytest.mark.parametrize("nested", [False, True])
def test_no_policy_cannot_exempt_controlled_or_unknown_target(ws, effect, nested):
    case = ws.load_case("TC-HM-S01")
    app = ws.app(case["app"])
    target = {"side_effect": effect, "strategies": [{"kind": "text", "value": "我的"}]}
    step = next(s for s in case["steps"] if s["id"] == "S1b")
    if nested:
        app.states.recipes["synthetic"] = [{"action": "tap", "target": target}]
        step.update(action="ensure_state", state="synthetic")
        step.pop("target")
    else:
        step["target"] = target
    found = C.lint(case, app, KnowledgeStore(ws.root), policy=None, runs_dir=ws.runs_dir)
    assert any(i["field"] == "cleanup" for i in found)
