"""#45: retrieve historical KPI sources without duplicating structured IC rules."""
from types import SimpleNamespace

import pytest

from vpdtest.cli import build_parser
from vpdtest.commands import cmd_knowledge
from vpdtest.util import write_json


IC_SECTION = "1. 车外语音对讲：冻结业务规则（用例集的验收锚点）"
PROJECT_DOC = "project-memory/project-knowledge.md"
KPI_DOCS = ["vpd-kpi/README.md", "vpd-kpi/business-cases.md", "vpd-kpi/step-scripts.md",
            "vpd-kpi/weekly-report.md"]


def _workspace(tmp_path):
    root = tmp_path / "data"
    write_json(root / "knowledge" / "items.json", {"items": []})
    # No local configuration, device, production knowledge or approvals are loaded.
    return SimpleNamespace(root=root)


def _doc(ws, relative, text):
    path = ws.root / "knowledge" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _search(ws, query, *roles):
    args = ["knowledge", "search", query]
    if roles:
        args += ["--role", *roles]
    return cmd_knowledge(ws, build_parser().parse_args(args))


def _rows(result):
    return [row for group in result["results"].values() for row in group]


@pytest.mark.parametrize("relative", KPI_DOCS)
def test_kpi_source_document_is_discoverable_as_historical_background(tmp_path, relative):
    ws = _workspace(tmp_path)
    source = _doc(ws, relative, "# 合成 KPI 源件\n\n## 历史 SQL 与数字\nSYNTHETIC_KPI_SOURCE 示例。\n")
    before = source.read_bytes()
    result = _search(ws, "SYNTHETIC_KPI_SOURCE")
    rows = _rows(result)
    assert len(rows) == 1, result
    row = rows[0]
    assert row["role"] == "background" and row["status"] == "unconfirmed_doc"
    assert row["applicability"] == "unknown"
    assert row["source"] == {"type": "doc", "ref": "data/knowledge/" + relative}
    assert row["ref"] == relative + ":4"
    assert "历史" in row["note"]
    assert row.get("callable") is None and not row.get("can_be_oracle")
    assert result["usable_oracles"] == []
    assert source.read_bytes() == before


def test_cli_role_filter_does_not_promote_kpi_documents_to_requirements(tmp_path):
    ws = _workspace(tmp_path)
    for relative in KPI_DOCS:
        _doc(ws, relative, "# 合成 KPI 源件\nSYNTHETIC_KPI_ROLES 旧统计。\n")
    background = _search(ws, "SYNTHETIC_KPI_ROLES", "background")
    assert len(background["results"]["background"]) == 4
    assert background["usable_oracles"] == []
    other_roles = _search(ws, "SYNTHETIC_KPI_ROLES", "requirement", "operation", "observation")
    assert _rows(other_roles) == [] and other_roles["usable_oracles"] == []


@pytest.mark.parametrize("status", ["unconfirmed", "deprecated", "removed"])
def test_structured_ic_source_section_does_not_duplicate_or_revive_a_rule(tmp_path, status):
    ws = _workspace(tmp_path)
    _doc(ws, PROJECT_DOC, f"# 合成项目记忆\n\n## {IC_SECTION}\nSYNTHETIC_IC_RULE 合成规则。\n"
                          "\n## 2. 用例工程约定\nSYNTHETIC_ENGINE_UNIQUE 独有工程信息。\n")
    item = {"id": "K-TEST-IC", "kind": "generic_invariant", "subject": "pytest.ic.structured",
            "statement": "SYNTHETIC_IC_RULE 合成规则。", "expected": True, "status": status,
            "source": {"type": "test_goal", "ref": "isolated structured-source fixture"}, "applies_to": {}}
    write_json(ws.root / "knowledge" / "items.json", {"items": [] if status == "removed" else [item]})
    result = _search(ws, "SYNTHETIC_IC_RULE")
    rows = _rows(result)
    assert [row["ref"] for row in rows] == ([] if status == "removed" else ["K-TEST-IC"])
    if status == "deprecated":
        assert rows[0]["role"] == "background" and rows[0]["status"] == "deprecated"
    assert result["usable_oracles"] == []
    engineering = _search(ws, "SYNTHETIC_ENGINE_UNIQUE")
    assert [row["source"]["ref"] for row in _rows(engineering)] == ["data/knowledge/" + PROJECT_DOC]


def test_only_the_imported_project_section_is_excluded(tmp_path):
    ws = _workspace(tmp_path)
    _doc(ws, PROJECT_DOC, "# 合成项目记忆\n\n## 2. 用例工程约定\nSYNTHETIC_KEEP_PROJECT 工程知识。\n")
    # A matching heading in another source is not evidence that its contents were imported.
    _doc(ws, "auto-vpd/device-knowledge.md", f"# 合成校准报告\n\n## {IC_SECTION}\n"
                                          "SYNTHETIC_KEEP_DEVICE 未提炼的 opacity 校准限制。\n")
    project = _rows(_search(ws, "SYNTHETIC_KEEP_PROJECT"))
    device = _rows(_search(ws, "SYNTHETIC_KEEP_DEVICE"))
    assert len(project) == 1 and project[0]["role"] == "background"
    assert len(device) == 1 and device[0]["role"] == "observation"
    assert device[0]["source"]["ref"] == "data/knowledge/auto-vpd/device-knowledge.md"
