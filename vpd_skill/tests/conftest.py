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
    return Workspace(str(tmp_path / "data"))


def confirm(ws: Workspace, *ids: str) -> None:
    kn = KnowledgeStore(ws.root)
    for k in ids:
        kn.confirm(k, reviewer="pytest-reviewer")


def review_interrupt(ws: Workspace, iid: str) -> None:
    p = ws.root / "apps" / "motor_hm" / "states.json"
    data = read_json(p)
    data["interrupts"][iid]["review"] = "reviewed"
    write_json(p, data)


def world(name: str) -> str:
    return f"replay:{WORLDS / name}"
