"""#49: delegated VPD entry/planning needs no repeated grant, with truthful audit."""
from pathlib import Path

import pytest

from test_goal_loop import cli
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.util import read_json, read_jsonl, write_json


def _tree(texts):
    return UiTree({"attributes": {"type": "Button", "bounds": "[0,0][200,200]", "clickable": "true"},
                   "children": [{"attributes": {"type": "Text", "text": text,
                                  "bounds": f"[0,{i * 30}][180,{i * 30 + 25}]"}}
                                for i, text in enumerate(texts)]})


def _permission_world(tmp_path):
    # Isolate permission tests from production fixtures' loading/error scenarios.
    path = tmp_path / "entry-planning-permissions.json"
    write_json(path, {"bundle": "com.yinwang.qiankunapp.hm", "app_version": "permission-fixture",
                     "screen": [1320, 2848], "display": {"density": 3.375, "device_form": "bar"},
                     "launch_screen": "car", "screens": {
                         "car": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
                             {"t": "Text", "text": "爱车", "b": [10, 20, 400, 100]},
                             {"t": "Button", "text": "ADS", "b": [40, 300, 900, 420], "go": "home"}]}},
                         "home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
                             {"t": "Text", "text": "搜索车位号设为目的地", "b": [10, 20, 900, 100]},
                             {"t": "Button", "text": "沿途找车位", "b": [40, 300, 900, 420], "go": "result"}]}},
                         "result": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
                             {"t": "Text", "text": "路线规划中", "b": [10, 20, 900, 100]}]}}}})
    return f"replay:{path}"


@pytest.mark.parametrize("effect,label", [("vpd_entry", "ADS"), ("route_planning", "算路")])
def test_task_effect_needs_no_repeat_grant_and_still_requires_matching_target(ws, effect, label):
    policy = ws.policy()
    assert policy.check_authorizations([effect], set())["ok"]
    allowed = policy.check_tap(_tree([label]), (50, 15), "tree", {"side_effect": effect})
    assert allowed["allowed"] and allowed["authorization_source"] == "task_policy"
    assert not policy.check_tap(_tree([label]), (50, 15), "tree", {"side_effect": "navigation"})["allowed"]
    policy.check_grants([effect])  # historical explicit CLI spelling remains valid


def test_run_without_flags_physically_enters_and_plans_and_reports_policy_source(ws, tmp_path):
    case = {"id": "TC-TASK-DIAG", "version": 1, "status": "draft", "app": "qiankun_hm",
            "title": "Synthetic entry/planning diagnostic", "purpose": "Observe without fault-scene claims",
            "authorizations": ["vpd_entry", "route_planning"], "env_requirements": {},
            "steps": [{"id": "S1", "action": "launch"},
                      {"id": "S2", "action": "tap", "target": "qk_ads", "expect_state": "qk_vpd_home"},
                      {"id": "S3", "action": "tap", "target": "qk_find_along"}],
            "assertions": [], "cleanup": [{"action": "stop_app"}]}
    run = run_case(ws, case=case, device_spec=_permission_world(tmp_path), propose=False)
    assert all(s["outcome"].startswith("passed") for s in run["steps"])
    assert run["authorization_sources"] == {"route_planning": "task_policy", "vpd_entry": "task_policy"}
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert len([e for e in events if e["event"] == "dispatch" and e.get("op") == "tap" and e.get("sent")]) == 2
    auth = next(e for e in events if e["event"] == "authorization")
    assert auth["grant_sources"] == run["authorization_sources"]
    assert run["verdict"]["verdict"] == "pending" and run["informal"]
    report = (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")
    assert "任务范围允许" in report and "由人授权的副作用：route_planning" not in report


def test_act_without_flags_uses_registered_target_and_records_sources(ws, tmp_path):
    spec = _permission_world(tmp_path)
    base = ["--app", "qiankun_hm", "--device", spec, "--session", "task-entry"]
    cli(ws, "act", "launch", *base)
    entered = cli(ws, "act", "tap", "--target", "qk_ads", *base)
    planned = cli(ws, "act", "tap", "--target", "qk_find_along", *base)
    assert entered["result"]["outcome"].startswith("passed")
    assert planned["result"]["outcome"].startswith("passed")
    events = read_jsonl(ws.runs_dir / "session-task-entry" / "events.jsonl")
    for auth in (e for e in events if e["event"] == "authorization"):
        assert auth["grant_sources"]["vpd_entry"] == "task_policy"
        assert auth["grant_sources"]["route_planning"] == "task_policy"
    assert len([e for e in events if e["event"] == "dispatch" and e.get("op") == "tap" and e.get("sent")]) == 2


@pytest.mark.parametrize("explicit", [False, True])
def test_registered_route_label_dispatches_without_a_deny_pattern_hit(ws, tmp_path, explicit):
    spec = _permission_world(tmp_path)
    path = Path(spec.removeprefix("replay:"))
    fixture = read_json(path)
    fixture["screens"]["car"]["root"]["children"].append(
        {"t": "Button", "text": "路线", "b": [40, 600, 900, 720], "go": "result"})
    write_json(path, fixture)
    assert not ws.policy().match_all(["路线"])
    base = ["--app", "qiankun_hm", "--device", spec, "--session", "task-route-label"]
    cli(ws, "act", "launch", *base)
    flags = ["--authorize", "route_planning"] if explicit else []
    result = cli(ws, "act", "tap", "--target", "qk_spot_route_btn", *flags, *base)
    assert result["result"]["outcome"].startswith("passed")
    events = read_jsonl(ws.runs_dir / "session-task-route-label" / "events.jsonl")
    assert len([e for e in events if e["event"] == "dispatch" and e.get("op") == "tap" and e.get("sent")]) == 1


@pytest.mark.parametrize("restriction,expected", [
    ([{"pattern": "^路线$", "class": "state_deny"}], "state_deny"),
    ([{"pattern": "^路线$", "class": "vehicle_motion"}], "vehicle_motion")])
def test_allowlisted_route_label_still_obeys_independent_restrictions(ws, restriction, expected):
    denied = ws.policy().check_tap(_tree(["路线"]), (50, 15), "tree",
                                   {"side_effect": "route_planning"}, state_deny=restriction)
    assert not denied["allowed"] and denied["deny"]["class"] == expected


@pytest.mark.parametrize("effect,label", [("account_state", "登录"), ("vehicle_selection", "选择车辆"),
                                         ("user_data", "收藏"), ("vehicle_motion", "开始"),
                                         ("vehicle_control", "车窗"), ("payment", "支付")])
def test_task_policy_does_not_grant_other_effects(ws, effect, label):
    policy = ws.policy()
    assert not policy.check_authorizations([effect], set())["ok"]
    assert not policy.check_tap(_tree([label]), (50, 15), "tree", {"side_effect": effect})["allowed"]


def test_planning_label_cannot_grant_motion_or_unknown_confirmation(ws):
    policy = ws.policy()
    target = {"side_effect": "route_planning"}
    mixed = policy.check_tap(_tree(["算路", "激活"]), (50, 15), "tree", target)
    assert not mixed["allowed"] and mixed["deny"]["class"] == "vehicle_motion"
    generic = policy.check_tap(_tree(["确定"]), (50, 15), "tree", target)
    assert not generic["allowed"] and generic["deny"]["class"] == "confirmation_semantics_unknown"


@pytest.mark.parametrize("effect", ["vpd_entry", "route_planning"])
@pytest.mark.parametrize("basis", ["tree", "template", "point"])
def test_mislabeled_unclassified_control_cannot_take_automatic_task_grant(ws, effect, basis):
    denied = ws.policy().check_tap(_tree(["帮助"]), (50, 15), basis,
                                   {"side_effect": effect, "strategies": [{"kind": "text", "value": "帮助"}]})
    assert not denied["allowed"] and denied["deny"]["class"] == "task_semantics_unverified"
    assert "authorized_by" not in denied


@pytest.mark.parametrize("effect,label", [("vpd_entry", "ADS"), ("route_planning", "算路")])
def test_no_tree_cannot_use_declared_label_as_actual_task_evidence(ws, effect, label):
    denied = ws.policy().check_tap(None, (50, 15), "template",
                                   {"side_effect": effect, "label": label})
    assert not denied["allowed"] and denied["deny"]["class"] == "task_semantics_unverified"


@pytest.mark.parametrize("effect", ["vpd_entry", "route_planning"])
def test_inline_misclassification_does_not_dispatch(ws, tmp_path, effect):
    base = ["--app", "qiankun_hm", "--device", _permission_world(tmp_path), "--session", "task-inline"]
    cli(ws, "act", "launch", *base)
    denied = cli(ws, "act", "tap", "--select", "text=爱车", "--side-effect", effect, *base)
    assert denied["result"]["attempts"][0]["status"] == "policy_denied"
    events = read_jsonl(ws.runs_dir / "session-task-inline" / "events.jsonl")
    assert not any(e["event"] == "dispatch" and e.get("op") == "tap" and e.get("sent") for e in events)


@pytest.mark.parametrize("effect", ["vpd_entry", "route_planning"])
@pytest.mark.parametrize("field_value", ["帮助", "算路"])
@pytest.mark.parametrize("field_type", ["TextInput", "SearchField"])
def test_unreviewed_registered_input_cannot_claim_task_permission(ws, tmp_path, effect, field_value, field_type):
    spec = _permission_world(tmp_path)
    path = Path(spec.removeprefix("replay:"))
    fixture = read_json(path)
    fixture["screens"]["car"]["root"]["children"].append(
        {"t": field_type, "text": field_value, "b": [40, 600, 900, 720]})
    write_json(path, fixture)
    targets_path = ws.root / "apps" / "qiankun_hm" / "targets.json"
    targets = read_json(targets_path)
    targets["targets"]["unreviewed_task_input"] = {
        "status": "unverified", "side_effect": effect, "allow_input": True,
        "strategies": [{"kind": "text", "value": field_value}]}
    write_json(targets_path, targets)
    case = {"id": "TC-TASK-BAD-INPUT", "version": 1, "status": "draft", "app": "qiankun_hm",
            "authorizations": [effect], "steps": [{"id": "S1", "action": "launch"},
                {"id": "S2", "action": "input", "target": "unreviewed_task_input", "text": "x"}],
            "assertions": []}
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    assert run["steps"][1]["attempts"][0]["status"] == "policy_denied"
    assert not any(e["event"] == "dispatch" and e.get("op") in {"tap", "input_text"} and e.get("sent")
                   for e in read_jsonl(Path(run["dir"]) / "events.jsonl"))


@pytest.mark.parametrize("field_type", ["TextInput", "SearchField"])
def test_nested_editable_value_cannot_prove_confirmation_title(ws, field_type):
    tree = UiTree({"attributes": {"type": "Dialog", "bounds": "[0,0][200,200]"}, "children": [
        {"attributes": {"type": field_type, "bounds": "[0,0][200,60]"}, "children": [
            {"attributes": {"type": "Text", "text": "修改名称", "bounds": "[10,10][190,50]"}}]},
        {"attributes": {"type": "Button", "text": "确定", "clickable": "true", "bounds": "[10,90][190,150]"}}]})
    node = next(n for n in tree.nodes if n.type == "Button")
    denied = ws.policy().check_tap(tree, node.center, "tree", {"side_effect": "user_data"},
                                   authorized={"user_data"}, node=node)
    assert not denied["allowed"] and denied["deny"]["class"] == "confirmation_semantics_unknown"


@pytest.mark.parametrize("effect,label", [
    ("route_planning", "算路帮助"), ("route_planning", "取消算路"),
    ("route_planning", "删除算路记录"), ("route_planning", "路线规划中"),
    ("vpd_entry", "泊车代驾帮助"), ("vpd_entry", "退出泊车代驾")])
def test_task_keyword_is_not_an_exact_action_label(ws, effect, label):
    denied = ws.policy().check_tap(_tree([label]), (50, 15), "tree", {"side_effect": effect})
    assert not denied["allowed"] and denied["deny"]["class"] == "task_semantics_unverified"


@pytest.mark.parametrize("effect,action,other", [
    ("route_planning", "算路", "算路帮助"), ("vpd_entry", "ADS", "泊车代驾帮助"),
    ("route_planning", "算路", "帮助"), ("vpd_entry", "ADS", "帮助")])
def test_exact_label_cannot_hide_an_unreviewed_task_keyword_in_same_receiver(ws, effect, action, other):
    denied = ws.policy().check_tap(_tree([action, other]), (50, 15), "tree", {"side_effect": effect})
    assert not denied["allowed"] and denied["deny"]["class"] == "task_semantics_unverified"


@pytest.mark.parametrize("effect,label", [("route_planning", "算路帮助"), ("vpd_entry", "泊车代驾帮助")])
def test_inline_task_keyword_control_does_not_dispatch(ws, tmp_path, effect, label):
    spec = _permission_world(tmp_path)
    path = Path(spec.removeprefix("replay:"))
    fixture = read_json(path)
    fixture["screens"]["car"]["root"]["children"].append(
        {"t": "Button", "text": label, "b": [40, 600, 900, 720], "go": "result"})
    write_json(path, fixture)
    base = ["--app", "qiankun_hm", "--device", spec, "--session", "task-keyword"]
    cli(ws, "act", "launch", *base)
    denied = cli(ws, "act", "tap", "--select", f"text={label}", "--side-effect", effect, *base)
    assert denied["result"]["attempts"][0]["status"] == "policy_denied"
    assert not any(e["event"] == "dispatch" and e.get("op") == "tap" and e.get("sent")
                   for e in read_jsonl(ws.runs_dir / "session-task-keyword" / "events.jsonl"))


@pytest.mark.parametrize("field_type", ["TextInput", "SearchField"])
def test_receiver_cannot_borrow_its_child_editor_value(ws, field_type):
    tree = UiTree({"attributes": {"type": "Button", "bounds": "[0,0][200,200]", "clickable": "true"},
                   "children": [{"attributes": {"type": field_type, "text": "算路", "bounds": "[0,0][200,50]"}}]})
    denied = ws.policy().check_tap(tree, (100, 150), "tree", {"side_effect": "route_planning"}, node=tree.nodes[0])
    assert not denied["allowed"] and denied["deny"]["class"] == "task_semantics_unverified"


def test_hidden_label_cannot_prove_current_action(ws):
    tree = _tree(["算路"])
    tree.nodes[1].visible = False
    denied = ws.policy().check_tap(tree, (100, 150), "tree", {"side_effect": "route_planning"}, node=tree.nodes[0])
    assert not denied["allowed"] and denied["deny"]["class"] == "task_semantics_unverified"


def test_old_policy_without_task_categories_keeps_explicit_gate(ws):
    from vpdtest.policy import Policy
    data = {**ws.policy().data}
    data.pop("task_side_effects", None)
    data.pop("task_action_labels", None)
    data["gated_side_effects"] = {**data["gated_side_effects"], "route_planning": "legacy explicit gate"}
    policy = Policy(data)
    assert not policy.check_authorizations(["route_planning"], set())["ok"]
    assert not policy.check_tap(_tree(["算路"]), (50, 15), "tree", {"side_effect": "route_planning"})["allowed"]


@pytest.mark.parametrize("effect", ["vehicle_motion", "payment", "account_state"])
def test_task_configuration_cannot_override_other_permission_categories(ws, effect):
    from vpdtest.policy import Policy
    data = {**ws.policy().data, "task_side_effects": {effect: "invalid overlap"}}
    with pytest.raises(ValueError, match="不能.*重叠"):
        Policy(data)
