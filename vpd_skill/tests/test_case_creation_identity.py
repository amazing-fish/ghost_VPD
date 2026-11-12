"""Creation and lifecycle share stable IDs; real CLI Replay sources, no production assets."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from verdict_fixtures import make_verdict_workspace
from vpdtest import cases, commands
from vpdtest.case_readiness import readiness
from vpdtest.case_templates import validate_case_id as validate_template_id
from vpdtest.cli import build_parser
from vpdtest.workspace import Workspace

SKILL = Path(__file__).resolve().parents[1]
ID_ERROR = "用例 ID 不能是路径，须为字母/数字开头的稳定 ID"
INVALID_IDS = ("TC-搜索-001", "TC-SEARCH/001", "../escaped-case")


def _snapshot(root):
    # Include empty directories and lock files, not only JSON contents.
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            if p.is_file() else None for p in root.rglob("*")}


def _cli(root, *args):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "vpdtest", "--workspace", str(root / "data"), *args],
        cwd=SKILL, env=env, capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


@pytest.fixture(scope="module")
def real_sources(tmp_path_factory):
    root = tmp_path_factory.mktemp("case-identity-sources")
    make_verdict_workspace(root, SKILL)
    world = SKILL / "fixtures/worlds/motor_hm_base.json"
    code, result = _cli(root, "act", "launch", "--app", "motor_hm", "--session", "seed",
                        "--device", f"replay:{world}")
    assert code == 0, result
    code, result = _cli(root, "explore", "--app", "motor_hm", "--start", "car_tab",
                        "--budget", "4", "--prefer", "我的", "--device", f"replay:{world}")
    assert code == 0 and result["transitions"], result
    return root, result["run_id"], result["transitions"][0]["id"]


def _create_args(entry, cid, run="missing-run", transition="T1"):
    args = ["case", entry, f"--new-id={cid}"]
    if entry == "from-session":
        return args + ["--session", "seed", "--app", "motor_hm", "--goal", "合成身份契约"]
    return args + ["--run", run, "--transitions", transition]


@pytest.mark.parametrize("entry", ["from-session", "from-exploration"])
@pytest.mark.parametrize("cid", INVALID_IDS)
def test_public_cli_rejects_unsupported_id_before_writing(tmp_path, real_sources, entry, cid):
    seed, run, transition = real_sources
    root = tmp_path / "workspace"
    shutil.copytree(seed, root)
    before = _snapshot(root)
    code, result = _cli(root, *_create_args(entry, cid, run, transition))
    assert code == 3, result
    assert result["error"] == "ValueError" and result["message"] == ID_ERROR
    assert _snapshot(root) == before
    code, result = _cli(root, "case", "readiness")
    assert code == 0 and result["summary"]["requested"] == 2, result
    assert _snapshot(root) == before


@pytest.mark.parametrize("entry", ["from-session", "from-exploration"])
def test_public_cli_normal_id_stays_visible_and_exclusive(tmp_path, real_sources, entry):
    seed, run, transition = real_sources
    root = tmp_path / "workspace"
    shutil.copytree(seed, root)
    before = _snapshot(root)
    cid = "normal_1.2-ID"
    args = _create_args(entry, cid, run, transition)
    code, result = _cli(root, *args)
    assert code == 0 and result["case"] == cid, result
    path = root / "data/cases" / f"{cid}.json"
    case = json.loads(path.read_text(encoding="utf-8"))
    assert case["id"] == cid and case["status"] == "exploration_record"
    assert not any(key in case for key in ("asset_review", "replays", "product_status"))
    created = _snapshot(root)
    assert set(created) - set(before) == {f"data/cases/{cid}.json"}
    assert all(created[key] == value for key, value in before.items())
    code, result = _cli(root, *args)
    assert code == 3 and result["error"] == "FileExistsError", result
    assert _snapshot(root) == created
    code, result = _cli(root, "case", "readiness")
    assert code == 0 and result["summary"]["requested"] == 3, result
    row = next(row for row in result["results"] if row["case"] == cid)
    assert row["review"]["state"] == "pending" and row["promotion"]["status"] == "blocked"
    assert _snapshot(root) == created
    ws = Workspace(str(root / "data"), load_local_config=False)
    with pytest.raises(PermissionError, match="尚无有效资产审核.*case review-route"):
        cases.promote(ws, cid, "candidate", commands.KnowledgeStore(ws.root), dry_run=True)
    assert _snapshot(root) == created


@pytest.mark.parametrize("entry", ["from-session", "from-exploration"])
@pytest.mark.parametrize("cid", [*INVALID_IDS, "", ".", "..", "_bad", "-bad", "bad id", "a\\b", "a\n"])
def test_rejection_precedes_all_source_reads_locks_and_paths(tmp_path, monkeypatch, entry, cid):
    ws = Workspace(str(tmp_path / "missing/data"), load_local_config=False)
    args = build_parser().parse_args(_create_args(entry, cid))

    def forbidden(*args, **kwargs):
        pytest.fail("invalid new ID reached a dependency, source, lock, path or write")

    for name in ("KnowledgeStore", "_session_dir", "session_lock", "from_session", "write_json"):
        monkeypatch.setattr(commands, name, forbidden)
    monkeypatch.setattr(cases, "load_exploration", forbidden)
    for name in ("app", "policy", "case_path", "device"):
        monkeypatch.setattr(ws, name, forbidden)
    with pytest.raises(ValueError, match=ID_ERROR):
        commands.cmd_case(ws, args)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("entry", ["from-session", "from-exploration"])
@pytest.mark.parametrize("broken_knowledge", [False, True])
def test_public_cli_missing_source_still_reports_id_first(tmp_path, entry, broken_knowledge):
    root = tmp_path / "workspace"
    if broken_knowledge:
        knowledge = root / "data/knowledge/items.json"
        knowledge.parent.mkdir(parents=True)
        knowledge.write_text("{broken", encoding="utf-8")
    before = _snapshot(tmp_path)
    code, result = _cli(root, *_create_args(entry, "TC-搜索-001"))
    assert code == 3 and result["message"] == ID_ERROR, result
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("cid", ["a", "Z", "0", "9mixed_1.2-ID", "normal_1.2-ID", "TC-ABC-123"])
def test_shared_general_contract_preserves_existing_identifiers(cid):
    cases.validate_case_id(cid)
    if cid == "TC-ABC-123":
        validate_template_id(cid)
    else:
        with pytest.raises(ValueError):
            validate_template_id(cid)


@pytest.mark.parametrize("cid", [*INVALID_IDS, "", ".", "..", None, 123])
def test_update_and_readiness_reject_same_invalid_id_without_io(tmp_path, monkeypatch, cid):
    ws = Workspace(str(tmp_path / "missing/data"), load_local_config=False)

    def forbidden(*args, **kwargs):
        pytest.fail("invalid case ID reached I/O")

    monkeypatch.setattr(cases, "session_lock", forbidden)
    monkeypatch.setattr(ws, "load_case", forbidden)
    for dry_run in (False, True):
        with pytest.raises(ValueError, match=ID_ERROR):
            cases.promote(ws, cid, "candidate", None, dry_run=dry_run)
    with pytest.raises(ValueError, match=ID_ERROR):
        readiness(ws, [cid])
    assert list(tmp_path.iterdir()) == []
