"""#56：同一次观测中的前台查询故障不能被末查成功抹掉。"""
import json

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_RULE
from vpdtest.devices import DeviceResult
from vpdtest.knowledge import KnowledgeStore
from vpdtest.observe import Observer, RunStore
from vpdtest.util import read_json
from vpdtest.verdict import case_verdict, collection_error, evaluate_assertion


def _observer(ws):
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir)
    device = ws.device(world("motor_hm_base.json"))
    device.state.update(foreground=app.bundle, alive=True, stack=["my_tab"])
    observer = Observer(device, app.bundle, ws.policy(), app.states, store, display=device.display_info())
    confirm(ws, COPY_RULE)
    knowledge = KnowledgeStore(ws.root)
    knowledge.bind({"app": app.id, "account_state": "logged_out"})
    return observer, device, app, knowledge


def _queries(monkeypatch, device, responses):
    responses = iter(responses)

    def foreground():
        value, error_class, raw = next(responses, (device.bundle, None, None))
        device.last_query_error = error_class
        device.last_query_error_detail = ({"error": f"synthetic foreground {error_class}", "raw": raw}
                                          if error_class else None)
        return value

    monkeypatch.setattr(device, "foreground", foreground)


def _evaluate(obs, tree, app, knowledge):
    assertion = {"id": "A1", "at": "S1", "type": "text_present", "text": "注册/登录", "oracle_refs": [COPY_RULE]}
    step = {"step_id": "S1", "action": "wait", "outcome": "passed", "final_obs": obs["obs_id"]}
    result = evaluate_assertion(assertion, {"S1": step}, lambda _: (obs, tree), knowledge, app)
    verdict = case_verdict({"ok": True}, {"ok": True}, [step], [result], knowledge, step_ids={"S1"})
    return result, verdict


@pytest.mark.parametrize("stage", ["initial", "before_screenshot"])
def test_explicit_foreground_error_survives_successful_final_query(verdict_ws, monkeypatch, stage):
    observer, device, app, knowledge = _observer(verdict_ws)
    marker = f"RAW_{stage.upper()}_FOREGROUND_TIMEOUT"
    responses = ([(None, "timeout", marker), (app.bundle, None, None)] if stage == "initial" else
                 [(app.bundle, None, None), (None, "timeout", marker), (app.bundle, None, None)])
    _queries(monkeypatch, device, responses)
    obs, tree = observer.observe("transient-query-error")
    assert obs["fresh"] is False and tree is None and obs["tree"]["status"] == "stale"
    error = collection_error(obs)
    assert error and error["error_class"] == "timeout" and error["attribution"] == "tool"
    assert marker in json.dumps(error, ensure_ascii=False)
    assert marker in json.dumps(read_json(observer.store.dir / "obs" / obs["obs_id"] / "observation.json"))
    assert any(item["stage"] == stage for item in obs["foreground_query_errors"])
    result, verdict = _evaluate(obs, tree, app, knowledge)
    assert result["result"] == "error" and verdict["verdict"] == "error"
    assert verdict["execution"]["status"] == "error"
    assert marker in json.dumps(verdict["execution"]["errors"], ensure_ascii=False)


@pytest.mark.parametrize("stage", ["initial", "before_screenshot"])
def test_successful_but_unknown_foreground_does_not_create_collection_error(verdict_ws, monkeypatch, stage):
    observer, device, app, knowledge = _observer(verdict_ws)
    responses = ([(None, None, None), (app.bundle, None, None)] if stage == "initial" else
                 [(app.bundle, None, None), (None, None, None), (app.bundle, None, None)])
    _queries(monkeypatch, device, responses)
    obs, tree = observer.observe("unknown-query")
    assert obs["fresh"] is False and tree is None
    assert not collection_error(obs) and not obs.get("foreground_query_errors")
    result, verdict = _evaluate(obs, tree, app, knowledge)
    assert result["result"] == "undeterminable" and verdict["verdict"] == "pending"
    assert verdict["execution"]["errors"] == []


def test_followup_complete_observation_does_not_inherit_previous_query_error(verdict_ws, monkeypatch):
    observer, device, app, knowledge = _observer(verdict_ws)
    marker = "RAW_PREVIOUS_OBSERVATION_TIMEOUT"
    _queries(monkeypatch, device, [(None, "timeout", marker), (app.bundle, None, None)])
    previous, _ = observer.observe("earlier-incomplete-observation")
    previous_path = observer.store.dir / "obs" / previous["obs_id"] / "observation.json"
    original = previous_path.read_bytes()
    current, tree = observer.observe("complete-followup-observation")
    assert current["fresh"] is True and tree is not None
    assert not collection_error(current) and not current.get("foreground_query_errors")
    result, verdict = _evaluate(current, tree, app, knowledge)
    assert result["result"] == "pass" and verdict["verdict"] == "passed"
    assert marker not in json.dumps(current, ensure_ascii=False)
    assert previous_path.read_bytes() == original


def test_different_initial_and_final_query_errors_keep_both_original_records(verdict_ws, monkeypatch):
    observer, device, app, knowledge = _observer(verdict_ws)
    markers = ["RAW_INITIAL_FOREGROUND_TIMEOUT", "RAW_FINAL_FOREGROUND_TRANSPORT"]
    _queries(monkeypatch, device, [(None, "timeout", markers[0]), (None, "transport_unavailable", markers[1])])
    obs, tree = observer.observe("multiple-query-errors")
    serialized = json.dumps(obs, ensure_ascii=False)
    assert all(marker in serialized for marker in markers)
    error = collection_error(obs)
    assert all(marker in json.dumps(error, ensure_ascii=False) for marker in markers)
    result, verdict = _evaluate(obs, tree, app, knowledge)
    assert result["result"] == "error" and verdict["verdict"] == "error"
    assert all(marker in json.dumps(verdict["execution"]["errors"], ensure_ascii=False) for marker in markers)


@pytest.mark.parametrize("stage", ["initial", "before_screenshot"])
def test_advisory_visual_query_error_preserves_independent_required_product_pass(verdict_ws, monkeypatch, stage):
    observer, device, app, knowledge = _observer(verdict_ws)
    healthy, healthy_tree = observer.observe("independent-required-text-evidence")
    required, _ = _evaluate(healthy, healthy_tree, app, knowledge)
    assert required["result"] == "pass"
    marker = f"RAW_VISUAL_{stage.upper()}_FOREGROUND_TIMEOUT"
    responses = ([(None, "timeout", marker), (app.bundle, None, None)] if stage == "initial" else
                 [(app.bundle, None, None), (None, "timeout", marker), (app.bundle, None, None)])
    _queries(monkeypatch, device, responses)
    unavailable, tree = observer.observe("advisory-visual-input-unavailable")
    assert unavailable["fresh"] is False and unavailable["screenshot"]["status"] == "withheld"
    visual = evaluate_assertion({"id": "A2", "at": "S2", "type": "visual_review", "advisory": True},
                                {"S2": {"outcome": "passed", "final_obs": unavailable["obs_id"]}},
                                lambda _: (unavailable, tree), knowledge, app)
    assert visual["result"] == "error", visual
    assert visual["error_class"] == "timeout" and visual["attribution"] == "tool"
    verdict = case_verdict({"ok": True}, {"ok": True}, [], [required, visual], knowledge)
    assert verdict["verdict"] == "error" and verdict["product_verdict"] == "passed"
    assert verdict["product_failed"] is False and verdict["execution"]["status"] == "error"
    errors = verdict["execution"]["errors"]
    assert len(errors) == 1 and errors[0]["id"] == "A2"
    serialized = json.dumps(errors, ensure_ascii=False)
    assert marker in serialized and unavailable["obs_id"] in serialized


def test_usable_visual_screenshot_does_not_depend_on_unneeded_tree_collection(verdict_ws, monkeypatch):
    observer, device, app, knowledge = _observer(verdict_ws)
    healthy, healthy_tree = observer.observe("independent-required-text-evidence")
    required, _ = _evaluate(healthy, healthy_tree, app, knowledge)
    monkeypatch.setattr(device, "dump_tree", lambda *args, **kwargs: DeviceResult(
        "dump_tree", True, False, "timeout", "synthetic unrelated tree query timeout", "UNRELATED_TREE_RAW"))
    visual_obs, tree = observer.observe("usable-screenshot-unavailable-tree")
    assert visual_obs["fresh"] is True and visual_obs["screenshot"]["status"] == "ok"
    assert tree is None and collection_error(visual_obs)["error_class"] == "timeout"
    visual = evaluate_assertion({"id": "A2", "at": "S2", "type": "visual_review", "advisory": True},
                                {"S2": {"outcome": "passed", "final_obs": visual_obs["obs_id"]}},
                                lambda _: (visual_obs, tree), knowledge, app)
    assert visual["result"] == "pending" and "error_class" not in visual
    verdict = case_verdict({"ok": True}, {"ok": True}, [], [required, visual], knowledge)
    assert verdict["verdict"] == verdict["product_verdict"] == "passed"
    assert verdict["execution"]["status"] == "completed" and verdict["execution"]["errors"] == []
