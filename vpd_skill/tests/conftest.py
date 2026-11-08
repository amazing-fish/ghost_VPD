"""ws 用于生产资产集成；verdict_ws 独立构造合成知识/用例，避免产品治理改变判定契约。"""
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from verdict_fixtures import make_verdict_workspace

from vpdtest.knowledge import KnowledgeStore  # noqa: E402
from vpdtest.util import read_json, write_json  # noqa: E402
from vpdtest.workspace import Workspace  # noqa: E402

WORLDS = SKILL / "fixtures" / "worlds"


@pytest.fixture(autouse=True)
def isolate_console_scan_cache(monkeypatch):
    """独立用例不继承前例缓存；tmp_path 清理后路径及 inode 可以复用。"""
    from vpdtest.console import progress

    monkeypatch.setattr(progress, "_SCANS", {})


@pytest.fixture
def ws(tmp_path) -> Workspace:
    shutil.copytree(SKILL / "data", tmp_path / "data")
    shutil.copytree(SKILL / "fixtures", tmp_path / "fixtures")
    # 单元夹具隔离作者运行产生的XP候选；生产items/ledger不变，真实候选加载另有测试。
    for exp_dir in (tmp_path / "data" / "experience").iterdir():
        if exp_dir.is_dir():
            (exp_dir / "items.json").write_text('{"items": []}', encoding="utf-8")
            (exp_dir / "ledger.jsonl").write_text("", encoding="utf-8")
    w = Workspace(str(tmp_path / "data"))
    w.config.pop("secrets", None)  # 测试不读本机 config.local.json 里的真实密钥
    w.config.pop("device", None)
    return w


@pytest.fixture
def verdict_ws(tmp_path) -> Workspace:
    return make_verdict_workspace(tmp_path, SKILL)


def confirm(ws: Workspace, *ids: str) -> None:
    kn = KnowledgeStore(ws.root)
    for k in ids:
        kn.confirm(k, reviewer="pytest-reviewer")


def unconfirm(ws: Workspace, *ids: str) -> None:
    """模拟判据尚未确认：种子数据里已被评审确认的条目，测"未确认判据"语义时在临时工作区退回 unconfirmed。"""
    p = ws.root / "knowledge" / "items.json"
    data = read_json(p)
    for item in data["items"]:
        if item["id"] in ids:
            item["status"] = "unconfirmed"
            for k in ("confirmed_by", "confirmed_at", "confirm_basis"):
                item.pop(k, None)
    write_json(p, data)


def widen_scope(ws: Workspace, kid: str, **scope) -> None:
    """模拟评审人扩大知识的适用范围（例如确认新版本仍适用），用于跨版本的回放世界。"""
    p = ws.root / "knowledge" / "items.json"
    data = read_json(p)
    item = next(i for i in data["items"] if i["id"] == kid)
    item["applies_to"] = {**item.get("applies_to", {}), **scope}
    write_json(p, data)


def review_interrupt(ws: Workspace, iid: str) -> None:
    p = ws.root / "apps" / "motor_hm" / "states.json"
    data = read_json(p)
    data["interrupts"][iid]["review"] = "reviewed"
    write_json(p, data)


def world(name: str) -> str:
    return f"replay:{WORLDS / name}"
