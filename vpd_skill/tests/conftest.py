"""测试夹具：把真实 data/ 复制到临时工作区，按需通过真实的 confirm/评审接口确认知识，避免污染仓库数据。"""
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from vpdtest.knowledge import KnowledgeStore  # noqa: E402
from vpdtest.util import read_json, write_json  # noqa: E402
from vpdtest.workspace import Workspace  # noqa: E402

WORLDS = SKILL / "fixtures" / "worlds"


@pytest.fixture
def ws(tmp_path) -> Workspace:
    shutil.copytree(SKILL / "data", tmp_path / "data")
    shutil.copytree(SKILL / "fixtures", tmp_path / "fixtures")
    # 测试隔离：清空经验存储。真机回填的 XP 候选（items/ledger）是运行时捕获状态，
    # 研究候选创建/验证/晋级生命周期的测试需要干净起点；仓库 data/ 原件不受影响（这里只清临时副本）。
    for exp_dir in (tmp_path / "data" / "experience").iterdir():
        if exp_dir.is_dir():
            (exp_dir / "items.json").write_text('{"items": []}', encoding="utf-8")
            (exp_dir / "ledger.jsonl").write_text("", encoding="utf-8")
    w = Workspace(str(tmp_path / "data"))
    w.config.pop("secrets", None)  # 测试不读本机 config.local.json 里的真实密钥
    w.config.pop("device", None)
    return w


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
