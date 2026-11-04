"""退役契约在生产语料删除/失去资格后仍成立；生产历史集成由外层独立执行。"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import SKILL

from vpdtest.util import read_json, write_json


CONTRACT_TESTS = (
    "test_unknown_knowledge_status_never_confirms",
    "test_missing_knowledge_status_is_invalid",
    "test_confirm_refuses_retired_or_invalid_knowledge_without_writing",
    "test_inactive_knowledge_is_searchable_only_as_history",
    "test_inactive_oracle_cannot_turn_observed_success_into_pass",
    "test_confirmation_and_history_do_not_invalidate_active_dependencies",
    "test_retirement_invalidates_replays_and_refuses_promotion",
)


@pytest.mark.parametrize("mutation", ["removed", "ineligible"])
def test_retirement_contract_survives_production_corpus_changes(tmp_path, mutation):
    skill = tmp_path / "skill"
    ignore = shutil.ignore_patterns(
        ".git", ".venv", "runs", "reports", "__pycache__", ".pytest_cache", ".ruff_cache")

    def ignored(directory, names):
        skipped = set(ignore(directory, names))
        if mutation == "removed" and Path(directory) == SKILL / "data":
            skipped.add("cases")
        return skipped

    shutil.copytree(SKILL, skill, ignore=ignored)
    path = skill / "data" / "knowledge" / "items.json"
    if mutation == "removed":
        write_json(path, {"items": []})
    else:
        data = read_json(path)
        for item in data["items"]:
            item.update(status="deprecated", kind="observation", applies_to={"app": "pytest_other_app"})
        write_json(path, data)
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    env.pop("PYTEST_ADDOPTS", None)
    # 仅选通用节点，不递归执行本模块，也不把Issue33生产历史当成通用契约。
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-o", "addopts=",
        "--basetemp", str(tmp_path / "child-pytest"),
        *[f"tests/test_knowledge_retirement.py::{name}" for name in CONTRACT_TESTS],
    ], cwd=skill, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
