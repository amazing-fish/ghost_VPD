"""#56 第二切片：合成 APP 经真实 Runner 留下断言选支与独立产品结论。

这些契约只存在于 pytest 临时工作区，不读取/确认生产知识，不声称真机覆盖。
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from vpdtest.cases import content_hash, dependency_hash, record_replay, replay_basis
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import Workspace

APP = "pytest_conditions"
BUNDLE = "pytest.synthetic.conditions"
CASE = "TC-TEST-CONDITIONS-216"
COMMON_RULE = "K-TEST-CONDITIONS-TASK"
FAULT_RULE = "K-TEST-CONDITIONS-FAULT"
FAULT = "独立故障标签"
HEALTHY = "独立健康标签"


@pytest.fixture
def condition_rig(tmp_path):
    """APP、policy、KnowledgeStore、case、Replay world 全部独立构造。"""
    root = tmp_path / "data"
    app_dir = root / "apps" / APP
    write_json(root / "policy.json", {"version": "pytest-only", "allowed_packages": [BUNDLE],
               "max_actions_per_run": 60, "max_wait_s": 2})
    write_json(app_dir / "app.json", {"id": APP, "name": "合成分支工作台", "platform": "harmony",
               "bundle": BUNDLE, "ability": "EntryAbility"})
    write_json(app_dir / "targets.json", {"targets": {
        "advance": {"desc": "合成下一页", "side_effect": "navigation", "strategies": [
            {"kind": "text", "value": "合成下一页"}]},
        "start": {"desc": "合成开始控件", "side_effect": "navigation", "strategies": [
            {"kind": "text", "value": "合成开始"}]}}})
    write_json(app_dir / "states.json", {"states": {
        "dashboard": {"require_text": ["合成工作台"]},
        "details": {"require_text": ["合成详情"]}}, "interrupts": {}, "recipes": {}})
    ws = Workspace(str(root))
    ws.config = {}
    knowledge = KnowledgeStore(root)
    for kid, subject in ((COMMON_RULE, "任务跨启动可见"), (FAULT_RULE, "故障控件不可用")):
        knowledge.add({"id": kid, "kind": "product_rule", "subject": "pytest.conditions." + kid,
                       "statement": "独立合成规则：" + subject, "expected": subject,
                       "applies_to": {"app": APP, "app_version": "1.0.0"},
                       "source": {"type": "reviewer", "ref": "tests/test_condition_runner.py synthetic only"}})
        knowledge.confirm(kid, reviewer="pytest-synthetic", basis="合成引擎契约，不是产品要求确认")

    def build(markers=(FAULT,), enabled=False, common=True):
        def screen(title, go=None):
            texts = [title, *markers, *(["合成任务仍可见"] if common else [])]
            children = [{"t": "Text", "text": text, "b": [10, 20 + i * 45, 360, 55 + i * 45]}
                        for i, text in enumerate(texts)]
            children += [{"t": "Button", "text": "合成开始", "b": [10, 360, 170, 420],
                          "click": True, "attrs": {"enabled": str(enabled).lower()}}]
            if go:
                children += [{"t": "Button", "text": "合成下一页", "b": [190, 360, 380, 420], "go": go}]
            return {"root": {"t": "Column", "b": [0, 0, 400, 800], "children": children}}
        spec = {"bundle": BUNDLE, "app_version": "1.0.0", "screen": [400, 800],
                "display": {"density": 1, "device_form": "bar"},
                "device": {"model": "SYNTHETIC-ONLY", "os": "Replay"},
                "initial": {"installed": True}, "launch_screen": "dashboard",
                "screens": {"dashboard": screen("合成工作台", "details"), "details": screen("合成详情")}}
        path = tmp_path / "world.json"
        write_json(path, spec)
        return "replay:" + str(path)

    conditions = {"fault": {"kind": "page", "true": {"type": "text_present", "text": FAULT},
                            "false": {"type": "text_present", "text": HEALTHY}}}
    case = {"id": CASE, "version": 1, "status": "draft", "app": APP,
            "title": "216 合成设计：共同断言与故障专属断言独立", "purpose": "断言级选支回归",
            "preconditions": ["pytest 独立合成环境"], "test_data": {}, "test_data_none": True,
            "env_requirements": {}, "conditions": conditions,
            "steps": [{"id": "S1", "action": "launch", "expect_state": "dashboard", "retry": 0}],
            "assertions": [
                {"id": "A-common", "at": "S1", "type": "text_present", "text": "合成任务仍可见",
                 "oracle_refs": [COMMON_RULE]},
                {"id": "A-fault", "at": "S1", "type": "target_enabled", "target": "start", "expected": False,
                 "guard": {"condition": "fault", "equals": True}, "oracle_refs": [FAULT_RULE]}],
            "cleanup": [{"id": "C1", "action": "stop_app", "retry": 0}],
            "sources": [{"type": "text_case", "ref": "tests/test_condition_runner.py synthetic only"}]}
    write_json(ws.case_path(CASE), case)
    return ws, case, build


def _run(rig, *, case=None, markers=(FAULT,), enabled=False, common=True, env=None):
    ws, template, world = rig
    return run_case(ws, case=case or deepcopy(template), device_spec=world(markers, enabled, common),
                    env=env, propose=False)


def _assertions(run):
    return {a["id"]: a for a in run["assertions"]}


def _coverage(run, *, verified=(), not_applicable=(), unverified=()):
    actual = run["verdict"]["coverage"]
    for name, expected in (("verified", verified), ("not_applicable", not_applicable), ("unverified", unverified)):
        assert set(actual[name]) == set(expected), actual


@pytest.mark.parametrize("markers,enabled,expected,verdict", [
    ((FAULT,), False, "pass", "passed"),
    ((FAULT,), True, "fail", "failed"),
    ((HEALTHY,), False, "not_applicable", "passed"),
    ((HEALTHY,), True, "not_applicable", "passed"),
    ((), False, "pending", "pending"),
    ((), True, "pending", "pending"),
    ((FAULT, HEALTHY), False, "pending", "pending"),
    ((FAULT, HEALTHY), True, "pending", "pending"),
])
def test_same_conditional_assertion_uses_independent_current_evidence(condition_rig, markers, enabled, expected, verdict):
    # 控件可用/不可用不改变故障前置。两个结果都保留同样 unknown/conflict/false 分支。
    run = _run(condition_rig, markers=markers, enabled=enabled)
    assertions = _assertions(run)
    assert assertions["A-common"]["result"] == "pass"
    assert assertions["A-fault"]["result"] == expected, assertions["A-fault"]
    assert run["verdict"]["verdict"] == verdict, run["verdict"]
    assert run["verdict"]["product_verdict"] == verdict
    _coverage(run, verified=("A-common", "A-fault") if expected in ("pass", "fail") else ("A-common",),
              not_applicable=("A-fault",) if expected == "not_applicable" else (),
              unverified=("A-fault",) if expected == "pending" else ())


@pytest.mark.parametrize("markers,expected", [((HEALTHY,), "not_applicable"), ((), "pending")])
def test_false_and_unknown_cannot_make_an_all_conditional_case_pass(condition_rig, markers, expected):
    _, case, _ = condition_rig
    case["assertions"] = [case["assertions"][1]]
    run = _run(condition_rig, case=case, markers=markers)
    assert run["verdict"]["verdict"] == expected, run["verdict"]
    assert run["verdict"]["product_verdict"] == expected
    _coverage(run, not_applicable=("A-fault",) if expected == "not_applicable" else (),
              unverified=("A-fault",) if expected == "pending" else ())


def test_common_failure_survives_conditional_not_applicable(condition_rig):
    run = _run(condition_rig, markers=(HEALTHY,), common=False)
    assert _assertions(run)["A-common"]["result"] == "fail"
    assert _assertions(run)["A-fault"]["result"] == "not_applicable"
    assert run["verdict"]["verdict"] == "failed"
    assert run["verdict"]["product_failed"] is True
    _coverage(run, verified=("A-common",), not_applicable=("A-fault",))


def test_unconfirmed_applicable_rule_is_neither_verified_nor_pass(condition_rig):
    ws, _, _ = condition_rig
    data = read_json(ws.root / "knowledge" / "items.json")
    next(x for x in data["items"] if x["id"] == FAULT_RULE)["status"] = "unconfirmed"
    write_json(ws.root / "knowledge" / "items.json", data)
    run = _run(condition_rig)
    assert _assertions(run)["A-fault"]["result"] == "pending"
    assert run["verdict"]["verdict"] == "pending"
    _coverage(run, verified=("A-common",), unverified=("A-fault",))


def test_condition_in_app_metadata_is_a_real_provider(condition_rig):
    ws, case, _ = condition_rig
    path = ws.app(APP).dir / "app.json"
    meta = read_json(path)
    meta["conditions"] = case.pop("conditions")
    write_json(path, meta)
    run = _run(condition_rig, case=case, markers=(HEALTHY,))
    assert _assertions(run)["A-fault"]["result"] == "not_applicable"
    assert run["verdict"]["verdict"] == "passed"


@pytest.mark.parametrize("provider,markers,expected", [
    (True, (FAULT,), "pass"), (True, (HEALTHY,), "not_applicable"),
    (True, (), "pending"), (False, (FAULT,), "pending"),
])
def test_knowledge_requirement_applies_without_explicit_assertion_guard_and_env_cannot_supply_it(
        condition_rig, provider, markers, expected):
    ws, case, _ = condition_rig
    case["assertions"][1].pop("guard")
    if not provider:
        case.pop("conditions")
    data = read_json(ws.root / "knowledge" / "items.json")
    next(x for x in data["items"] if x["id"] == FAULT_RULE)["condition_requirements"] = {"fault": True}
    write_json(ws.root / "knowledge" / "items.json", data)
    run = _run(condition_rig, case=case, markers=markers,
               env={"fault": True, "vehicle_fault": True, "account_state": "logged_in"})
    assert _assertions(run)["A-fault"]["result"] == expected, run["assertions"]
    assert run["verdict"]["verdict"] == ("pending" if expected == "pending" else "passed")


@pytest.mark.parametrize("markers,step_outcome,assertion_result,verdict", [
    ((FAULT,), "passed", "pass", "passed"),
    ((HEALTHY,), "skipped", "not_applicable", "passed"),
    ((), "pending", "pending", "pending"),
])
def test_operational_when_selects_a_step_without_counting_it_as_coverage(
        condition_rig, markers, step_outcome, assertion_result, verdict):
    _, case, _ = condition_rig
    case["steps"].append({"id": "S2", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0,
                          "when": {"condition": "fault", "equals": True}})
    case["assertions"][1]["at"] = "S2"
    run = _run(condition_rig, case=case, markers=markers)
    assert run["steps"][1]["outcome"] == step_outcome, run["steps"][1]
    assert _assertions(run)["A-fault"]["result"] == assertion_result
    assert run["verdict"]["verdict"] == verdict
    assert (len(run["steps"][1]["attempts"]) > 0) is (step_outcome == "passed")
    _coverage(run, verified=("A-common", "A-fault") if assertion_result == "pass" else ("A-common",),
              not_applicable=("A-fault",) if assertion_result == "not_applicable" else (),
              unverified=("A-fault",) if assertion_result == "pending" else ())


def test_action_invalidates_a_previous_page_condition_even_when_labels_happen_to_match(condition_rig):
    _, case, _ = condition_rig
    case["steps"].append({"id": "S2", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0})
    case["assertions"][1].update(at="S2", guard={"condition": "fault", "equals": True, "at": "S1"})
    run = _run(condition_rig, case=case)
    assert run["steps"][1]["outcome"] == "passed"
    assert _assertions(run)["A-fault"]["result"] == "pending", run["assertions"]
    assert run["verdict"]["verdict"] == "pending"
    _coverage(run, verified=("A-common",), unverified=("A-fault",))


@pytest.mark.parametrize("guarded", [False, True])
def test_skipped_operation_cannot_validate_an_unconditional_or_applicable_assertion(condition_rig, guarded):
    _, case, _ = condition_rig
    case["steps"].append({"id": "S2", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0,
                          "when": {"condition": "fault", "equals": False}})
    case["assertions"][1]["at"] = "S2"
    if not guarded:
        case["assertions"][1].pop("guard")
    run = _run(condition_rig, case=case)
    assert run["steps"][1]["outcome"] == "skipped" and run["steps"][1]["attempts"] == []
    assert _assertions(run)["A-fault"]["result"] == "not_run"
    assert run["verdict"]["verdict"] == "pending"
    _coverage(run, verified=("A-common",), unverified=("A-fault",))


def test_a_new_independent_observation_after_action_can_select_the_same_condition(condition_rig):
    _, case, _ = condition_rig
    case["steps"].append({"id": "S2", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0})
    case["assertions"][1]["at"] = "S2"  # 默认读本步骤 final_obs，不能沿用 S1 缓存。
    run = _run(condition_rig, case=case)
    assert _assertions(run)["A-fault"]["result"] == "pass"
    assert run["verdict"]["verdict"] == "passed"
    assert run["steps"][0]["final_obs"] != run["steps"][1]["final_obs"]


def test_fact_specific_age_limit_is_checked_using_runner_virtual_clock(condition_rig):
    _, case, _ = condition_rig
    case["conditions"]["fault"]["max_age_s"] = 1
    case["steps"].append({"id": "S2", "action": "wait", "duration_s": 3, "expect_state": "dashboard", "retry": 0})
    case["assertions"][1].update(at="S2", guard={"condition": "fault", "equals": True, "at": "S1"})
    run = _run(condition_rig, case=case)
    assert run["steps"][1]["outcome"] == "passed"
    assert _assertions(run)["A-fault"]["result"] == "pending"
    assert run["verdict"]["verdict"] == "pending"
    _coverage(run, verified=("A-common",), unverified=("A-fault",))


def test_key_unexecuted_step_is_not_covered_or_a_pass(condition_rig, monkeypatch):
    _, case, _ = condition_rig
    case["steps"].append({"id": "S2", "action": "wait", "expect_state": "dashboard", "retry": 0})
    case["assertions"][1]["at"] = "S2"
    monkeypatch.setattr(ReplayDevice, "launch", lambda *args: DeviceResult(
        "launch", True, False, "timeout", "synthetic launch timeout"))
    run = _run(condition_rig, case=case)
    assert run["steps"][1]["outcome"] == "not_run"
    assert _assertions(run)["A-fault"]["result"] == "not_run"
    assert run["verdict"]["verdict"] == "error"
    _coverage(run, unverified=("A-common", "A-fault"))


def test_product_failure_and_later_tool_error_keep_separate_signatures(condition_rig, monkeypatch):
    _, case, _ = condition_rig
    case["steps"].append({"id": "S2", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0})
    monkeypatch.setattr(ReplayDevice, "tap", lambda *args: DeviceResult(
        "tap", True, False, "timeout", "synthetic tap timeout full evidence", raw="UNTRUNCATED-CONDITION-ERROR"))
    run = _run(condition_rig, case=case, enabled=True)
    verdict = run["verdict"]
    assert _assertions(run)["A-fault"]["result"] == "fail"
    assert verdict["verdict"] == "error" and verdict["attribution"] == "tool"
    assert verdict["product_verdict"] == "failed" and verdict["product_failed"] is True
    assert any("A-fault" in signature for signature in verdict["failure_signature"])
    serialized = json.dumps(verdict["execution"]["errors"], ensure_ascii=False)
    assert "synthetic tap timeout full evidence" in serialized and "UNTRUNCATED-CONDITION-ERROR" in serialized
    assert run["steps"][1]["final_obs"] in serialized
    _coverage(run, verified=("A-common", "A-fault"))


def test_conditional_rule_does_not_use_failed_tree_collection_as_false(condition_rig, monkeypatch):
    _, case, _ = condition_rig
    original = ReplayDevice.dump_tree

    def fail_after_launch(self, bundle, path):
        if self._in_app():
            return DeviceResult("dump_tree", True, False, "timeout", "synthetic condition tree timeout")
        return original(self, bundle, path)

    monkeypatch.setattr(ReplayDevice, "dump_tree", fail_after_launch)
    run = _run(condition_rig, case=case)
    assert run["verdict"]["verdict"] == "error"
    assert all(a["result"] not in ("pass", "not_applicable") for a in run["assertions"])
    assert "synthetic condition tree timeout" in json.dumps(run, ensure_ascii=False)
    _coverage(run, unverified=("A-common", "A-fault"))


def test_unconditional_legacy_asset_keeps_its_meaning_and_raw_run(condition_rig):
    ws, case, _ = condition_rig
    case.pop("conditions")
    case["assertions"][1].pop("guard")
    before = deepcopy(case)
    run = _run(condition_rig, case=case, markers=())
    assert case == before
    assert run["verdict"]["verdict"] == "passed"
    assert all(a["result"] == "pass" for a in run["assertions"])
    assert run["validation_level"] == "offline_replay"
    raw_path = Path(run["dir"]) / "run.json"
    raw = raw_path.read_bytes()
    assert read_json(raw_path)["verdict"] == run["verdict"]
    _coverage(run, verified=("A-common", "A-fault"))
    # 新运行不得就地改写第一次的 raw run。
    _run(condition_rig, case=case, markers=(), enabled=True)
    assert raw_path.read_bytes() == raw
    assert not (ws.root / "apps" / "motor_hm").exists()


def test_guard_provider_and_knowledge_requirement_changes_invalidate_replay_basis(condition_rig):
    ws, case, _ = condition_rig
    write_json(ws.case_path(CASE), case)
    for _ in range(2):
        record_replay(ws, CASE, _run(condition_rig, case=case))
    recorded = ws.load_case(CASE)
    knowledge = KnowledgeStore(ws.root)
    original_dep = dependency_hash(recorded, ws.app(APP), knowledge)
    assert replay_basis(recorded, original_dep)["count"] == 2
    modified = deepcopy(recorded)
    modified["conditions"]["fault"]["false"]["text"] = "不同的独立健康标签"
    assert content_hash(modified) != content_hash(recorded)
    assert replay_basis(modified, dependency_hash(modified, ws.app(APP), knowledge))["count"] == 0
    # 条件也可以来自 APP 元数据，其变化必须进入外部依赖指纹。
    meta_path = ws.app(APP).dir / "app.json"
    meta = read_json(meta_path)
    app_case = deepcopy(recorded)
    meta["conditions"] = app_case.pop("conditions")
    write_json(meta_path, meta)
    write_json(ws.case_path(CASE), app_case)
    for _ in range(2):
        record_replay(ws, CASE, _run(condition_rig, case=app_case))
    app_case = ws.load_case(CASE)
    app_dep = dependency_hash(app_case, ws.app(APP), knowledge)
    assert replay_basis(app_case, app_dep)["count"] == 2
    meta["conditions"]["fault"]["false"]["text"] = "修改共享健康证据"
    write_json(meta_path, meta)
    changed_dep = dependency_hash(app_case, ws.app(APP), knowledge)
    assert changed_dep != app_dep
    assert replay_basis(app_case, changed_dep)["count"] == 0
    data = read_json(knowledge.path)
    next(x for x in data["items"] if x["id"] == FAULT_RULE)["condition_requirements"] = {"fault": True}
    write_json(knowledge.path, data)
    assert dependency_hash(app_case, ws.app(APP), KnowledgeStore(ws.root)) != changed_dep


def test_runner_json_report_and_observations_keep_auditable_branch_evidence(condition_rig):
    run = _run(condition_rig, markers=(HEALTHY,), enabled=True)
    directory = Path(run["dir"])
    raw = read_json(directory / "run.json")
    assertion = _assertions(run)["A-fault"]
    assert assertion["result"] == "not_applicable"
    assert raw["assertions"] == run["assertions"] and raw["verdict"] == run["verdict"]
    source_obs = run["steps"][0]["final_obs"]
    serialized = json.dumps(assertion, ensure_ascii=False)
    assert "fault" in serialized and source_obs in serialized, assertion
    obs = read_json(directory / "obs" / source_obs / "observation.json")
    assert obs["ts"] and isinstance(obs["clock_s"], (int, float))
    assert isinstance(obs["action_epoch"], int)
    assert obs["scope"]["app"] == APP and obs["scope"]["app_version"] == "1.0.0"
    report = (directory / "report.md").read_text(encoding="utf-8")
    assert "A-common" in report and "A-fault" in report and source_obs in report
    assert "不涉及" in report and "fault" in report


@pytest.mark.parametrize("kind,changed", [("vehicle", None), ("vehicle", "vehicle"), ("task", None),
                                          ("task", "vehicle"), ("task", "task"), ("account", "account")])
def test_identity_scope_is_measured_and_old_fact_cannot_cross_vehicles_or_tasks(condition_rig, kind, changed):
    ws, case, world = condition_rig
    required = {"vehicle": ["vehicle"], "task": ["vehicle", "task"], "account": ["account"]}[kind]
    scope = {key: "identity_" + key for key in required}
    case["conditions"]["fault"].update(kind=kind, scope=scope)
    targets_path = ws.app(APP).dir / "targets.json"
    targets = read_json(targets_path)
    for key in required:
        targets["targets"]["identity_" + key] = {
            "desc": "合成身份 " + key, "strategies": [{"kind": "id", "value": "synthetic_" + key}]}
    write_json(targets_path, targets)
    spec = world()
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    for i, key in enumerate(required):
        data["screens"]["dashboard"]["root"]["children"].append({
            "t": "Text", "id": "synthetic_" + key, "text": key + "-A", "b": [10, 500 + i * 50, 300, 540 + i * 50]})
    data["screens"]["changed"] = deepcopy(data["screens"]["dashboard"])
    if changed:
        next(node for node in data["screens"]["changed"]["root"]["children"]
             if node.get("id") == "synthetic_" + changed)["text"] = changed + "-B"
    # 模拟后台身份/任务更新，无程序动作：两份证据epoch相同，必须实际比较身份。
    data["screens"]["dashboard"]["after_obs"] = {"n": 1, "go": "changed", "replace": True}
    write_json(path, data)
    case["steps"].append({"id": "S2", "action": "wait", "expect_state": "dashboard", "retry": 0})
    case["assertions"][1].update(at="S2", guard={"condition": "fault", "equals": True, "at": "S1"})
    run = run_case(ws, case=case, device_spec=spec, propose=False,
                   env={key: key + "-A" for key in required})
    assert run["steps"][1]["outcome"] == "passed"
    expected = "pending" if changed else "pass"
    assert _assertions(run)["A-fault"]["result"] == expected, run["assertions"]
    assert run["verdict"]["verdict"] == ("pending" if changed else "passed")
    for step in run["steps"]:
        observation = read_json(Path(run["dir"]) / "obs" / step["final_obs"] / "observation.json")
        assert observation["action_epoch"] == read_json(
            Path(run["dir"]) / "obs" / run["steps"][0]["final_obs"] / "observation.json")["action_epoch"]
    _coverage(run, verified=("A-common",) if changed else ("A-common", "A-fault"),
              unverified=("A-fault",) if changed else ())


def test_declared_vehicle_identity_does_not_replace_missing_measured_identity(condition_rig):
    ws, case, _ = condition_rig
    path = ws.app(APP).dir / "targets.json"
    targets = read_json(path)
    targets["targets"]["identity_vehicle"] = {"desc": "合成车辆身份", "strategies": [
        {"kind": "id", "value": "synthetic_vehicle"}]}
    write_json(path, targets)
    case["conditions"]["fault"].update(kind="vehicle", scope={"vehicle": "identity_vehicle"})
    run = _run(condition_rig, case=case, env={"vehicle": "vehicle-A", "fault": True})
    assert _assertions(run)["A-fault"]["result"] == "pending"
    assert run["verdict"]["verdict"] == "pending"
    _coverage(run, verified=("A-common",), unverified=("A-fault",))


@pytest.mark.parametrize("confirmed,expected", [(True, "failed"), (False, "pending")])
def test_reaching_a_declared_terminal_does_not_automatically_pass_its_response_contract(condition_rig, confirmed, expected):
    ws, _, _ = condition_rig
    if not confirmed:
        data = read_json(ws.root / "knowledge" / "items.json")
        next(item for item in data["items"] if item["id"] == FAULT_RULE)["status"] = "unconfirmed"
        write_json(ws.root / "knowledge" / "items.json", data)
    run = _run(condition_rig, enabled=True)
    step = run["steps"][0]
    assert step["outcome"] == "passed" and step["attempts"][-1]["post"]["met"] is True
    assert _assertions(run)["A-fault"]["observed_holds"] is False
    assert run["verdict"]["verdict"] == expected
    _coverage(run, verified=("A-common", "A-fault") if confirmed else ("A-common",),
              unverified=() if confirmed else ("A-fault",))


def test_conditional_step_oracle_cannot_bypass_missing_provider_by_becoming_fail_candidate(condition_rig):
    ws, case, _ = condition_rig
    case.pop("conditions")
    case["steps"][0].update(expect_state="details", oracle_refs=[FAULT_RULE])
    case["assertions"] = [case["assertions"][0]]
    data = read_json(ws.root / "knowledge" / "items.json")
    next(item for item in data["items"] if item["id"] == FAULT_RULE)["condition_requirements"] = {"fault": True}
    write_json(ws.root / "knowledge" / "items.json", data)
    run = _run(condition_rig, case=case, env={"fault": True})
    assert run["steps"][0]["outcome"] == "fail_candidate"
    assert _assertions(run)["A-common"]["result"] == "not_run"
    assert run["verdict"]["verdict"] == "pending", run["verdict"]
    assert run["verdict"]["product_failed"] is False
    assert run["verdict"]["failure_signature"] == []
    _coverage(run, unverified=("A-common",))


def test_locator_scroll_invalidates_true_condition_before_the_intended_tap(condition_rig):
    ws, case, world = condition_rig
    target_path = ws.app(APP).dir / "targets.json"
    targets = read_json(target_path)
    targets["targets"]["advance"]["scroll"] = {"direction": "up", "max": 1}
    write_json(target_path, targets)
    spec = world()
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    data["screens"]["scrolled"] = deepcopy(data["screens"]["dashboard"])
    dashboard = data["screens"]["dashboard"]
    dashboard["root"]["children"] = [node for node in dashboard["root"]["children"]
                                            if node.get("text") != "合成下一页"]
    dashboard["on_swipe_up"] = "scrolled"
    write_json(path, data)
    case["steps"].append({"id": "S2", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0,
                          "when": {"condition": "fault", "equals": True}})
    case["assertions"][1]["at"] = "S2"
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    step = run["steps"][1]
    assert step["outcome"] == "pending" and step["final_status"] == "condition_unknown", step
    assert step["attempts"][0]["locate"]["scroll"][0]["sent"] is True
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    physical = [event for event in events if event["event"] == "dispatch" and event.get("step") == "S2"]
    assert any(event["op"] == "swipe" and event["sent"] is True for event in physical)
    taps = [event for event in physical if event["op"] == "tap"]
    assert len(taps) == 1 and taps[0]["sent"] is False and taps[0]["error_class"] == "condition_unknown"
    assert _assertions(run)["A-fault"]["result"] not in ("pass", "not_applicable")
    assert run["verdict"]["verdict"] == "pending"
    _coverage(run, verified=("A-common",), unverified=("A-fault",))


def test_automatic_unlock_invalidates_true_condition_before_the_intended_tap(condition_rig, monkeypatch):
    from vpdtest.observe import Observer

    ws, case, world = condition_rig
    spec = world()
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    data["initial"]["lock_pin"] = "1234"  # 只属于合成设备的测试PIN。
    write_json(path, data)
    ws.config = {"unlock_secrets": {spec: "LOCK_PIN"}, "secrets": {"LOCK_PIN": "1234"}}
    original = Observer.observe

    def lock_after_current_observation(self, label="", screenshot=True):
        result = original(self, label, screenshot)
        if label == "S2#1:before":
            # 观测之后手机自动锁屏；下发前复核会走真实受控解锁，无直接动作调用。
            self.device.state["locked"] = True
        return result

    monkeypatch.setattr(Observer, "observe", lock_after_current_observation)
    case["steps"].append({"id": "S2", "action": "tap", "target": "advance", "expect_state": "details", "retry": 0,
                          "when": {"condition": "fault", "equals": True}})
    case["assertions"][1]["at"] = "S2"
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    step = run["steps"][1]
    assert step["outcome"] == "pending" and step["final_status"] == "condition_unknown", step
    assert step["attempts"][0]["unlock"]["status"] == "unlocked"
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    pin_actions = [event for event in events if event["event"] == "dispatch" and event.get("action") == "pin_digit"]
    assert len(pin_actions) == 4 and all(event["sent"] is True for event in pin_actions)
    target_taps = [event for event in events if event["event"] == "dispatch" and event.get("step") == "S2"
                   and event["op"] == "tap"]
    assert len(target_taps) == 1 and target_taps[0]["sent"] is False
    assert target_taps[0]["error_class"] == "condition_unknown"
    assert run["verdict"]["verdict"] == "pending"
    _coverage(run, verified=("A-common",), unverified=("A-fault",))


def _scope_rules(rig, *, overlap=False):
    ws, case, _=rig
    case=deepcopy(case)
    data=read_json(ws.root/'knowledge'/'items.json')
    for rule in data['items']:
        fault=rule['id']==FAULT_RULE
        rule.update(subject='synthetic.activation',expected=not fault,condition_requirements={'fault':False if overlap else fault})
    write_json(ws.root/'knowledge'/'items.json',data)
    case['assertions'][0].update(type='target_enabled',target='start',expected=True)
    case['assertions'][0].pop('text',None)
    case['assertions'][1].pop('guard',None)
    return case


def test_exclusive_conditions_choose_health_rule_without_false_knowledge_conflict(condition_rig):
    case=_scope_rules(condition_rig)
    run=_run(condition_rig,case=case,markers=(HEALTHY,),enabled=True)
    results={a['id']:a for a in run['assertions']}
    assert results['A-common']['result']=='pass',run
    assert results['A-fault']['result']=='not_applicable'
    assert run['verdict']['verdict']=='passed'


def test_exclusive_conditions_choose_fault_rule_without_false_knowledge_conflict(condition_rig):
    case=_scope_rules(condition_rig)
    run=_run(condition_rig,case=case,markers=(FAULT,),enabled=False)
    results={a['id']:a for a in run['assertions']}
    assert results['A-common']['result']=='not_applicable',run
    assert results['A-fault']['result']=='pass'
    assert run['verdict']['verdict']=='passed'


def test_overlapping_conditions_keep_true_knowledge_conflict_pending(condition_rig):
    case=_scope_rules(condition_rig,overlap=True)
    run=_run(condition_rig,case=case,markers=(HEALTHY,),enabled=True)
    results={a['id']:a for a in run['assertions']}
    assert results['A-common']['result']=='pending',run
    assert results['A-common']['oracle']['status']=='conflicted'
    assert run['verdict']['verdict']=='pending'


def test_resolve_does_not_deprecate_rule_from_exclusive_condition(condition_rig):
    _scope_rules(condition_rig)
    ws,_,_=condition_rig
    kn=KnowledgeStore(ws.root)
    kn.resolve('synthetic.activation',keep=COMMON_RULE,reviewer='pytest-synthetic',reason='only same health scene reviewed')
    assert kn.get(COMMON_RULE)['status']=='confirmed'
    assert kn.get(FAULT_RULE)['status']=='confirmed'
    assert kn.conflicts()=={}
