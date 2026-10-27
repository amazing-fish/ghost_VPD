"""#34 候选定义与治理边界；只在隔离工作区/合成树/回放世界验证。"""
import pytest
from conftest import SKILL

from vpdtest import cases as C
from vpdtest.experience import ExperienceStore
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.util import read_json, write_json
from vpdtest.verdict import _check

IDS = [f"TC-MOTOR-HM-{n:03}" for n in (6, 7, 8)] + [f"TC-QK-{n}" for n in
    (211, 212, 213, 214, 215, 216, 217, 218, 219, 221, 222, 223, 226, 227, 228, 229)]
NEW_KNOWLEDGE = [f"K-MOT-{n:03}" for n in (7, 8, 9)] + [f"K-QK-{n:03}" for n in range(11, 22)]


def _assertion(ws, cid, aid):
    return next(a for a in ws.load_case(cid)["assertions"] if a["id"] == aid)


def _tree(texts, opacity=None):
    children = [{"attributes": {"type": "Text", "text": text, "bounds": f"[20,{20 + 45 * n}][900,{60 + 45 * n}]"}}
                for n, text in enumerate(texts)]
    if opacity is not None:
        children.append({"attributes": {"type": "Button", "text": "开始", "enabled": "true", "opacity": str(opacity),
                                         "bounds": "[20,500][200,550]"}})
    return UiTree({"attributes": {"type": "Column", "bounds": "[0,0][1000,1000]"}, "children": children})


def _holds(ws, cid, aid, texts, opacity=None):
    case = ws.load_case(cid)
    obs = {"obs_id": "fixture", "fresh": True, "tree": {"status": "ok", "path": "fixture/tree.json"},
           "screenshot": {}, "page": {"state": "unknown"}}
    return _check(_assertion(ws, cid, aid), obs, _tree(texts, opacity), ws.app(case["app"]))[0]


@pytest.mark.parametrize("cid", IDS)
def test_batch_candidate_needs_human_review_before_promotion_or_device(ws, monkeypatch, cid):
    calls = []
    monkeypatch.setattr(ws, "device", lambda *a, **k: calls.append((a, k)))
    case = ws.load_case(cid)
    assert C.review_status(ws, case)["required"]
    before = ws.case_path(cid).read_bytes()
    with pytest.raises(PermissionError, match="人工审核"):
        C.promote(ws, cid, "candidate", KnowledgeStore(ws.root))
    with pytest.raises(PermissionError, match="人工审核"):
        run_case(ws, cid, device_spec="replay:unused", require_asset_review=True)
    assert ws.case_path(cid).read_bytes() == before and not calls and not list(ws.runs_dir.glob("run-*"))


@pytest.mark.parametrize("cid,aid,wrong,right", [
    ("TC-QK-211", "A2", ["验证码已发至"], ["验证码已发至151****3183"]),
    ("TC-QK-212", "A2", ["爱车", "我的"], ["黄子傲"]),
    ("TC-QK-213", "A4", ["我的车辆"], ["黄子傲"]),
    ("TC-QK-214", "A3", ["开始"], ["VPD功能故障"]),
    ("TC-QK-216", "A2", ["开始"], ["泊车代驾辅助暂不可用"]),
    ("TC-QK-219", "A3", ["泊入车位"], ["L1层-A43 车位"]),
    ("TC-QK-219", "A4", ["路线规划中"], ["VPD功能故障"]),
    ("TC-QK-227", "A1", ["我的爱车"], ["我的位置"]),
    ("TC-QK-227", "A3", ["路线距离1.8km"], ["车辆距离目的地较远"]),
    ("TC-MOTOR-HM-008", "A1", ["邀请好友"], ["浏览记录"]),
])
def test_display_predicate_rejects_old_false_positive(ws, cid, aid, wrong, right):
    assert _holds(ws, cid, aid, wrong) is False
    assert _holds(ws, cid, aid, right) is True


@pytest.mark.parametrize("cid", ["TC-QK-214", "TC-QK-216", "TC-QK-219", "TC-QK-227"])
@pytest.mark.parametrize("opacity,expected", [(1.0, False), (0.3, True), (None, True)])
def test_fault_activation_requires_readonly_disabled_or_absent_start(ws, cid, opacity, expected):
    assert _holds(ws, cid, "A_DISABLED", ["VPD功能故障"], opacity) is expected


def test_fault_page_does_not_claim_pass_from_fault_text_with_enabled_start(ws):
    assert _holds(ws, "TC-QK-214", "A3", ["VPD功能故障"], 1.0) is True
    assert _holds(ws, "TC-QK-214", "A_DISABLED", ["VPD功能故障"], 1.0) is False


@pytest.mark.parametrize("kid", NEW_KNOWLEDGE)
def test_imported_claims_cannot_confirm_without_independent_basis(ws, kid):
    kn = KnowledgeStore(ws.root)
    before = (ws.root / "knowledge/items.json").read_bytes()
    assert kn.get(kid)["status"] == "unconfirmed"
    with pytest.raises(PermissionError, match="依据"):
        kn.confirm(kid, "pytest-human-reviewer")
    assert (ws.root / "knowledge/items.json").read_bytes() == before


@pytest.mark.parametrize("kid", ["K-QK-020", "K-QK-021"])
def test_lifecycle_and_root_cause_reports_remain_background_even_after_fixture_confirm(ws, kid):
    kn = KnowledgeStore(ws.root)
    kn.confirm(kid, "pytest-human-reviewer", "synthetic fixture only")
    assert kn.oracle_status([kid], facts={"app": "qiankun_hm", "app_version": "1.0.26.100", "device_form": "bar"})["status"] == "not_oracle"


def test_import_preserves_retired_rule_and_selected_debug_scope(ws):
    kn = KnowledgeStore(ws.root)
    assert kn.get("K-QK-001")["status"] == "deprecated"
    assert kn.get("K-QK-001")["superseded_by"] == "K-AV-001"
    assert kn.get("K-AV-001")["status"] == "unconfirmed"
    assert kn.get("K-AV-001")["applies_to"]["app_version"] == "内部debug版 phone_fc78649"
    with pytest.raises(PermissionError, match="废弃"):
        kn.confirm("K-QK-001", "pytest-human-reviewer", "fixture must not revive retired knowledge")


def test_unavailable_evidence_does_not_create_fake_obs_or_lint_success(ws):
    kn = KnowledgeStore(ws.root)
    problems = {}
    for cid in IDS:
        c = ws.load_case(cid)
        issues = C.lint(c, ws.app(c["app"]), kn, ws.policy(), runs_dir=ws.runs_dir)
        assert not [i for i in issues if i["field"] == "knowledge_used"]
        # Keep historical production metadata unchanged and require its diagnostic;
        # independently retain the original evidence/provider expectations below.
        declarations = [i for i in issues if i["field"].startswith("knowledge_used[")
                        and i["field"].endswith(".applicability")]
        expected = {f"knowledge_used[{index}].applicability"
                    for index, entry in enumerate(c.get("knowledge_used") or [])
                    if "applicability" in entry and (not isinstance(entry["applicability"], str)
                                                     or entry["applicability"] not in
                                                     ("applies", "unknown", "out_of_scope"))}
        assert {i["field"] for i in declarations} == expected
        issues = [i for i in issues if i not in declarations]
        if issues:
            problems[cid] = issues
    assert set(problems) == {"TC-QK-218", "TC-QK-214", "TC-QK-216", "TC-QK-219", "TC-QK-227"}
    for cid in ("TC-QK-214", "TC-QK-216", "TC-QK-219", "TC-QK-227"):
        assert all(i["field"].endswith(".oracle_refs") and "vehicle_fault" in i["problem"]
                   and "provider" in i["problem"] for i in problems[cid])
    assert problems["TC-QK-218"][0]["field"] == "assertions"
    assert not list(ws.runs_dir.rglob("obs-*"))


def test_scope_changes_do_not_expand_old_confirmed_motor_oracles(ws):
    c = ws.load_case("TC-MOTOR-HM-007")
    assert "K-MOT-001" not in [r for a in c["assertions"] for r in a.get("oracle_refs", [])]
    c = ws.load_case("TC-MOTOR-HM-008")
    assert "K-MOT-002" not in [r for a in c["assertions"] for r in a.get("oracle_refs", [])]
    assert all(not _holds(ws, c["id"], aid, ["邀请好友"]) for aid in ["A1", "A4", "A5"])


def test_initial_home_loading_failure_stays_a_failure_without_unconditional_restart(ws, tmp_path):
    data = read_json(SKILL / "fixtures/worlds/qiankun_hm_base.json")
    data["app_version"] = ws.load_case("TC-QK-228")["env_requirements"]["app_version"]
    data["initial_flags"].update(logged_in=True, vehicle="F03")
    data["screens"]["qk_vpd_home"] = {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Text", "text": "正在加载（合成占位）", "b": [40, 500, 900, 600]}]}}
    path = tmp_path / "loading.json"
    write_json(path, data)
    case = ws.load_case("TC-QK-228")
    run = run_case(ws, case["id"], device_spec=f"replay:{path}", propose=False,
                   env=case["env_requirements"], authorize=["vpd_entry", "route_planning"])
    first = next(s for s in run["steps"] if s["step_id"] == "S4")
    assert first["outcome"] not in ("passed", "passed_after_recovery", "passed_after_adaptation")
    assert any(a["status"] == "postcondition_not_met" for a in first["attempts"])
    assert not any(s["step_id"] == "S6" for s in run["steps"])
    assert next(s for s in run["steps"] if s["step_id"] == "S11")["outcome"] == "not_run"
    assert run["informal"] is True


def test_real_xp_proposals_remain_candidates_and_deduplicate_in_temporary_store(ws):
    # 加载生产候选到临时store，独立于清空XP的单元夹具，验证候选没有丢失/自动激活。
    for app in ("motor_hm", "qiankun_hm"):
        raw = read_json(SKILL / "data/experience" / app / "items.json")
        assert all(i["status"] == "candidate" and i["validations"] == [] for i in raw["items"])
        assert ExperienceStore(SKILL / "data", app).active()["items"] == []
        write_json(ws.root / "experience" / app / "items.json", raw)
        xs = ExperienceStore(ws.root, app)
        assert len(xs.items()) == len(raw["items"])
        item = raw["items"][0]
        evidence = item["evidence"][0]
        xs._upsert(item["kind"], item["change"], "synthetic duplicate fixture",
                   {"run_id": "fixture-duplicate", "case": {"id": evidence["case"]}, "device_spec": evidence["device"]},
                   {"step_id": evidence["step"]}, {"n": 1, "obs_before": "fixture-obs"})
        assert len(xs.items()) == len(raw["items"]) and xs.active()["items"] == []


@pytest.mark.parametrize("texts,expected", [
    (["泊入沿途最近空车位", "开始"], "qk_route_result"),
    (["泊入沿途最近空车位", "开始", "VPD功能故障"], "qk_route_result"),
    (["泊入沿途最近空车位", "开始", "路线规划中，请稍后…"], "qk_route_planning"),
    (["泊入沿途最近空车位", "路线规划中，请稍后…"], "qk_route_planning"),
    (["泊入沿途最近空车位", "开始", "暂停"], "qk_moving"),
    (["泊入沿途最近空车位", "开始", "继续", "退出"], "qk_paused"),
])
def test_task_description_does_not_shadow_route_or_motion_states(ws, texts, expected):
    app = ws.app("qiankun_hm")
    page = app.states.identify(_tree(texts), app.bundle, True)
    assert page["state"] == expected
    assert page["active"] == [expected]
    assert app.states.is_state(page, expected)


@pytest.mark.parametrize("target", [None, {"side_effect": "navigation"}, {"side_effect": "route_planning"}])
def test_task_result_generic_confirmation_keeps_vehicle_motion_gate(ws, target):
    app = ws.app("qiankun_hm")
    tree = UiTree({"attributes": {"type": "Column", "bounds": "[0,0][1000,1000]"}, "children": [
        {"attributes": {"type": "Text", "text": "泊入沿途最近空车位", "bounds": "[20,20][900,60]"}},
        {"attributes": {"type": "Button", "text": "开始", "bounds": "[20,100][200,150]", "clickable": "true"}},
        {"attributes": {"type": "Button", "text": "确定", "bounds": "[20,200][200,250]", "clickable": "true"}},
    ]})
    page = app.states.identify(tree, app.bundle, True)
    denied = ws.policy().check_tap(tree, (50, 225), "tree", target, app.states.deny_for_page(page),
                                   {"vpd_entry", "route_planning"})
    assert denied["allowed"] is False
    assert denied["deny"]["class"] == "vehicle_motion"
    assert denied["deny"]["supervised"] is True


@pytest.mark.parametrize("other_texts", [
    ["金桥出口加工区", "泊车代驾 共 742 个", "常去地点", "3号车位[默认]"],
    ["华为练秋湖研发中心", "泊车代驾 共 13308 个", "常去地点", "1号停车点[上车点]"],
])
def test_home_checks_accept_loaded_display_without_default_parking_placeholder(ws, other_texts):
    case = ws.load_case("TC-QK-215")
    texts = ["搜索车位号设为目的地"] + other_texts
    assert all(_holds(ws, case["id"], a["id"], texts) is True
               for a in case["assertions"] if not a.get("advisory"))
    assert KnowledgeStore(ws.root).oracle_status(["K-QK-013"], facts={
        "app": "qiankun_hm", "app_version": "1.0.26.100", "device_form": "bar"})["status"] == "unconfirmed"


def test_failed_parking_placeholder_does_not_satisfy_home_entry(ws):
    app = ws.app("qiankun_hm")
    texts = ["搜索车位号设为目的地", "默认车位", "停车场信息加载失败，请重试"]
    page = app.states.identify(_tree(texts), app.bundle, True)
    assert not app.states.is_state(page, "qk_vpd_home")
    assert _holds(ws, "TC-QK-215", "A2", texts) is False


def test_first_failed_parking_placeholder_stops_before_along_route_action(ws, tmp_path):
    data = read_json(SKILL / "fixtures/worlds/qiankun_hm_base.json")
    data["app_version"] = ws.load_case("TC-QK-228")["env_requirements"]["app_version"]
    data["initial_flags"].update(logged_in=True, vehicle="F03")
    data["screens"]["qk_vpd_home"] = {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Text", "text": text, "b": [40, 300 + n * 70, 1100, 350 + n * 70]}
        for n, text in enumerate(["搜索车位号设为目的地", "默认车位", "停车场信息加载失败，请重试"]) ]}}
    path = tmp_path / "failed-parking.json"
    write_json(path, data)
    case = ws.load_case("TC-QK-228")
    run = run_case(ws, case["id"], device_spec=f"replay:{path}", propose=False,
                   env=case["env_requirements"], authorize=["vpd_entry", "route_planning"])
    first = next(s for s in run["steps"] if s["step_id"] == "S4")
    assert any(a["status"] == "postcondition_not_met" for a in first["attempts"])
    assert next(s for s in run["steps"] if s["step_id"] == "S11")["outcome"] == "not_run"


@pytest.mark.parametrize("kid,context", [
    ("K-QK-015", {"vpd_task_state": "none", "task_type": "along_parking"}),
    ("K-QK-015", {"vpd_task_state": "active", "task_type": "specific_spot"}),
    ("K-QK-016", {"parking_area": "another-area"}),
    ("K-QK-017", {"vehicle_name": "another-vehicle"}),
    ("K-QK-018", {"network": "on"}),
    ("K-QK-019", {"parking_area": "another-area"}),
])
def test_scenario_specific_claims_cannot_generalize_to_other_task_vehicle_or_map(ws, kid, context):
    facts = {"app": "qiankun_hm", "app_version": "1.0.26.100", "device_form": "bar", **context}
    assert KnowledgeStore(ws.root).oracle_status([kid], facts=facts)["status"] == "out_of_scope"


def test_cold_start_vehicle_data_cannot_pass_from_position_label_alone(ws):
    assert _holds(ws, "TC-QK-229", "A2", ["车辆位置"]) is False
    assert _holds(ws, "TC-QK-229", "A2", ["更新于"]) is True
