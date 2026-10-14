"""Issue #45：CLI 对 Agent 的契约——退出码不混用、参数错误给结构化 JSON、不静默跑回放、阻塞条件可逐项读取。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_goal_loop import cli

from vpdtest import workspace as workspace_mod
from vpdtest.cli import build_parser, main
from vpdtest.util import SKILL_ROOT, read_json


def _main(capsys, *argv):
    try:
        code = main(list(argv))
    except SystemExit as e:  # argparse 用法错误经 _Parser.error 退出
        code = e.code
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else None


def test_usage_errors_exit_3_with_json_not_2(capsys):
    # 退出码 2 只表示需要人工接管；拼错参数/子命令不能被宿主当成接管请求
    for argv in (["no-such-cmd"], ["run"], ["case", "from-session", "--session", "s", "--new-id", "X"]):
        code, out = _main(capsys, *argv)
        assert code == 3 and out["error"] == "UsageError" and out["usage"].startswith("usage: vpdtest")


def test_env_requires_key_value(capsys):
    code, out = _main(capsys, "run", "TC-MOTOR-HM-001", "--env", "vehicle_in_vpd_map")
    assert code == 3 and out["error"] == "UsageError" and "key=value" in out["message"]
    for good in ("a=b", "a="):
        assert build_parser().parse_args(["run", "X", "--env", good]).env == [good]


def test_authorize_rejects_unknown_and_hard_forbidden(ws, capsys):
    base = ["--workspace", str(ws.root), "run", "TC-MOTOR-HM-001", "--device", "replay:motor_hm_base.json"]
    code, out = _main(capsys, *base, "--authorize", "account-state")
    assert code == 3 and "account-state" in out["message"] and "account_state" in out["message"]
    code, out = _main(capsys, *base, "--authorize", "user_data", "payment")
    assert code == 3 and "硬禁止" in out["message"] and "user_data" not in out["message"].split("：")[0]
    assert not list(ws.runs_dir.glob("run-*")), "非法授权必须在任何运行开始前拒绝"


def test_missing_device_errors_instead_of_silent_replay(ws, capsys, monkeypatch):
    assert "device" not in read_json(SKILL_ROOT / "config.example.json")
    monkeypatch.setattr(workspace_mod, "load_config", lambda: read_json(SKILL_ROOT / "config.example.json"))
    code, out = _main(capsys, "--workspace", str(ws.root), "run", "TC-MOTOR-HM-001")
    assert code == 3 and "未指定设备" in out["message"]


def test_blocked_brief_keeps_structured_unmet(ws):
    out = cli(ws, "run", "TC-QK-101", "--device", "replay:qiankun_hm_ready.json")
    assert out["verdict"] == "blocked"
    keys = {d["key"] for f in out["findings"] for d in f.get("detail") or []}
    assert {"authorize.account_state", "authorize.vehicle_selection"} <= keys
    detail = next(d for f in out["findings"] for d in f["detail"] if d["key"] == "authorize.account_state")
    assert detail["required"] == "本次运行 --authorize account_state" and detail["actual"] == "未授权"


def test_experience_app_accepted_after_subcommand():
    p = build_parser()
    assert p.parse_args(["experience", "list", "--app", "qiankun_hm"]).app == "qiankun_hm"
    assert p.parse_args(["experience", "--app", "qiankun_hm", "ledger"]).app == "qiankun_hm"
    assert p.parse_args(["experience", "list"]).app == "motor_hm"
    assert p.parse_args(["experience", "adopt", "XP-1", "--app", "qiankun_hm"]).app == "qiankun_hm"


def test_report_header_without_review_record(ws):
    out = cli(ws, "run", "TC-MOTOR-HM-001", "--device", "replay:motor_hm_base.json")
    head = Path(out["report"]).read_text(encoding="utf-8")
    assert "审核人 ，" not in head and "尚无审核记录" in head


@pytest.mark.skipif(sys.platform != "win32", reason="vpdtest.ps1 只在 Windows 上使用")
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_wrapper_without_venv_prints_json_and_exits_3(tmp_path, shell):
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} 不在 PATH")
    shutil.copy(SKILL_ROOT / "vpdtest.ps1", tmp_path / "vpdtest.ps1")
    r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                        str(tmp_path / "vpdtest.ps1"), "doctor"], check=False, capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 3, r.stderr
    out = json.loads(r.stdout)
    assert out["error"] == "EnvironmentError" and "install.ps1" in out["message"]
