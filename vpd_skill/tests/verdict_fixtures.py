"""判定契约的合成知识与用例；APP 定位、策略和回放世界仍走现有集成路径。"""
import shutil
from pathlib import Path

from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import write_json
from vpdtest.workspace import Workspace

NAV_CASE = "TC-TEST-VERDICT-NAV"
COPY_CASE = "TC-TEST-VERDICT-COPY"
NAV_RULE = "K-TEST-VERDICT-NAV"
SETTINGS_RULE = "K-TEST-VERDICT-SETTINGS"
COPY_RULE = "K-TEST-VERDICT-COPY"


def make_verdict_workspace(tmp_path: Path, skill: Path) -> Workspace:
    """不读取生产 knowledge/cases/experience，也不继承本机密钥或设备默认值。"""
    root = tmp_path / "data"
    shutil.copytree(skill / "data" / "apps" / "motor_hm", root / "apps" / "motor_hm")
    for name in ("policy.json", "layout.json", "system_dialogs.json"):
        shutil.copy2(skill / "data" / name, root / name)
    ws = Workspace(str(root))
    ws.config = {}
    knowledge = KnowledgeStore(root)
    rules = [
        (NAV_RULE, "product_rule", "navigation", {"app": "motor_hm", "app_version": "3.0.3.200"}),
        (SETTINGS_RULE, "product_rule", "settings", {"app": "motor_hm", "app_version": "3.0.3.200"}),
        (COPY_RULE, "ui_copy", "copy", {"app": "motor_hm", "account_state": "logged_out"}),
        # case_verdict 对异常固定引用 K-GEN-001；这里自建测试条目，绝不沿用生产确认记录。
        ("K-GEN-001", "generic_invariant", "no_crash", {"app": "motor_hm"}),
    ]
    for kid, kind, subject, scope in rules:
        knowledge.add({"id": kid, "kind": kind, "subject": f"pytest.verdict.{subject}",
                       "statement": f"仅供合成回放判定测试：{subject}", "expected": subject,
                       "applies_to": scope,
                       "source": {"type": "reviewer", "ref": "tests/verdict_fixtures.py synthetic contract"}})
    knowledge.confirm("K-GEN-001", reviewer="pytest-synthetic", basis="合成崩溃判定契约，非产品确认")

    common = {"version": 1, "status": "draft", "app": "motor_hm", "purpose": "离线判定契约",
              "preconditions": ["合成回放环境"], "test_data": {}, "test_data_none": True,
              "cleanup": [{"action": "stop_app"}],
              "sources": [{"type": "text_case", "ref": "tests/verdict_fixtures.py synthetic contract"}]}
    nav = {**common, "id": NAV_CASE, "title": "合成导航判定", "env_requirements": {}, "steps": [
        {"id": "S1", "action": "ensure_state", "state": "car_tab"},
        {"id": "S2", "action": "tap", "target": "tab_my", "expect_state": "my_tab",
         "retry": 1, "critical": True, "oracle_refs": [NAV_RULE]},
        {"id": "S3", "action": "tap", "target": "settings_entry", "expect_state": "settings",
         "retry": 1, "critical": True, "oracle_refs": [SETTINGS_RULE]},
        {"id": "S4", "action": "back", "expect_state": "my_tab", "retry": 0,
         "critical": True, "oracle_refs": [NAV_RULE]}], "assertions": [
        {"id": "A1", "at": "S2", "type": "node_selected", "target": "tab_my", "expected": True,
         "oracle_refs": [NAV_RULE]},
        {"id": "A2", "at": "S3", "type": "target_present", "target": "logout_entry",
         "oracle_refs": [SETTINGS_RULE]},
        {"id": "A3", "at": "S4", "type": "state_is", "state": "my_tab", "oracle_refs": [NAV_RULE]},
        {"id": "A4", "at": "S3", "type": "visual_review", "advisory": True, "prompt": "仅输出建议"}]}
    copy = {**common, "id": COPY_CASE, "title": "合成文案判定",
            "env_requirements": {"account_state": "logged_out"},
            "steps": [{"id": "S1", "action": "ensure_state", "state": "my_tab"}], "assertions": [
                {"id": "A1", "at": "S1", "type": "text_present", "text": "注册/登录", "oracle_refs": [COPY_RULE]},
                {"id": "A2", "at": "S1", "type": "visual_review", "advisory": True, "prompt": "仅输出建议"}]}
    for case in (nav, copy):
        write_json(ws.case_path(case["id"]), case)
    return ws
