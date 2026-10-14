"""在独立 Skill 副本改变生产语料，验证判定契约不会借用生产确认记录或用例。"""
import os
import shutil
import subprocess
import sys

import pytest
from conftest import SKILL

from vpdtest.util import read_json, write_json


@pytest.mark.parametrize("mutation", ["removed", "ineligible"])
def test_verdict_contract_survives_production_corpus_changes(tmp_path, mutation):
    skill = tmp_path / "skill"
    # 子进程只运行 test_verdict，不会递归运行本模块；生产集成检查仍由外层全量执行。
    shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns(
        ".git", ".venv", "runs", "reports", "__pycache__", ".pytest_cache", ".ruff_cache"))
    path = skill / "data" / "knowledge" / "items.json"
    if mutation == "removed":
        write_json(path, {"items": []})
        shutil.rmtree(skill / "data" / "cases")
    else:
        data = read_json(path)
        for item in data["items"]:
            item.update(status="deprecated", kind="observation", applies_to={"app": "pytest_other_app"})
        write_json(path, data)
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "tests/test_verdict.py"],
                            cwd=skill, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
