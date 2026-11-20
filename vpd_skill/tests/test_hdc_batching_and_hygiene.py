"""HDC 批量采集、离线快失败、单一前置门禁、弹窗诊断与存储并发回归测试（纯离线合成，不访问真机）。"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import world
from test_goal_loop import BUNDLE, _lab_world
from vpdtest import cases, commands, util
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.execute import Executor
from vpdtest.experience import ExperienceStore
from vpdtest.knowledge import KnowledgeStore
from vpdtest.observe import Observer, RunStore
from vpdtest.report import render_run
from vpdtest.session import parse_select
from vpdtest.util import read_json, write_json
from vpdtest.workspace import RunContext, Workspace


def _extract_sep(script: str, prefix: str) -> str:
    m = re.search(rf"({re.escape(prefix)}[0-9a-f]+__)", script)
    assert m, f"missing sentinel {prefix} in {script!r}"
    return m.group(1)


def _batch_out(sep: str, sections: list[tuple[str, str, int]]) -> bytes:
    lines = []
    for label, body, rc in sections:
        lines.append(f"{sep}:BEGIN:{label}\r\n{body}\r\n\r\n{sep}:END:{label}:{rc}\r\n")
    return "".join(lines).encode("utf-8")


# ---- 1. HDC 批量采集与离线快失败 ----

def test_hdc_info_batches_four_params_into_one_shell_call(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        sep = _extract_sep(cmd[-1], "__VPD_INFO_")
        out = _batch_out(sep, [
            ("model", "SGT-AL10", 0),
            ("name", "Mate 80 Pro Max", 0),
            ("os", "OpenHarmony-7.0.0.111", 0),
            ("software", "Get parameter const.product.software.version fail!", 1),
        ])
        return subprocess.CompletedProcess(cmd, 0, out, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dev = HdcDevice("synthetic-hdc")
    info = dev.info()
    assert len(calls) == 1
    assert info["model"] == "SGT-AL10"
    assert info["name"] == "Mate 80 Pro Max"
    assert info["os"] == "OpenHarmony-7.0.0.111"
    assert info["software"] is None
    assert "query_errors" not in info


def test_hdc_info_batch_preserves_per_property_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        sep = _extract_sep(cmd[-1], "__VPD_INFO_")
        out = _batch_out(sep, [
            ("model", "error: permission denied", 1),
            ("name", "Mate 80", 0),
            ("os", "OpenHarmony-7.0", 0),
            ("software", "7.0.0", 0),
        ])
        return subprocess.CompletedProcess(cmd, 0, out, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    info = HdcDevice("synthetic-hdc").info()
    assert info["model"] is None and info["name"] == "Mate 80"
    assert info["query_errors"]["const.product.model"]["error_class"] == "command_failed"


def test_hdc_display_info_batches_dump_and_fold_param(monkeypatch):
    calls = []
    display_dump = (
        "---------------- Screen ID: 0 ----------------\n"
        "VirtualPixelRatio:            3.375\n"
        "DisplayFoldStatus:            UNKNOWN\n"
        "---------------- Display ID: 0 ----------------\n"
        "Width:                        1320\n"
        "Height:                       2848\n"
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        sep = _extract_sep(cmd[-1], "__VPD_DISP_")
        out = _batch_out(sep, [
            ("DISP", display_dump, 0),
            ("FOLD", "Get parameter const.window.foldscreen.type fail!", 1),
        ])
        return subprocess.CompletedProcess(cmd, 0, out, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = HdcDevice("synthetic-hdc").display_info()
    assert len(calls) == 1
    assert d["width_px"] == 1320 and d["height_px"] == 2848 and d["density"] == 3.375
    assert d["device_form"] == "bar" and not d.get("execution_error")


def test_hdc_screen_locked_batches_and_short_circuits_offline(monkeypatch):
    calls = []
    slept = []
    monkeypatch.setattr("vpdtest.devices.harmony_hdc.time.sleep", lambda s: slept.append(s))

    def fake_ok(cmd, **kwargs):
        calls.append(cmd)
        sep = _extract_sep(cmd[-1], "__VPD_LK_")
        out = _batch_out(sep, [
            ("POWER", "Current State: AWAKE", 0),
            ("LOCK", "screenLocked false", 0),
        ])
        return subprocess.CompletedProcess(cmd, 0, out, b"")

    monkeypatch.setattr(subprocess, "run", fake_ok)
    dev = HdcDevice("synthetic-hdc")
    assert dev.screen_locked() is False
    assert len(calls) == 1 and slept == []

    calls.clear()

    def fake_offline(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"[Fail][E001005] Device not found or connected\r\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_offline)
    assert dev.screen_locked(tries=3) is None
    assert len(calls) == 1 and slept == []
    assert dev.screen_query_error["error_class"] == "device_offline"


def test_hdc_dump_tree_single_shell_ignores_error_text_inside_ui_json(monkeypatch, tmp_path):
    calls = []
    tree_payload = {
        "attributes": {"type": "Column", "bounds": "[0,0][100,100]"},
        "children": [
            {"attributes": {"type": "Text", "text": "error: 网络异常 [Fail] 请重试", "bounds": "[10,10][90,50]"},
             "children": []}
        ],
    }

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        marker = _extract_sep(cmd[-1], "__VPD_TREE_")
        assert "rm -f /data/local/tmp/vpd_" in cmd[-1]
        out = f"DumpLayout saved to:/data/local/tmp/vpd_x.json\n{marker}\n{json.dumps(tree_payload, ensure_ascii=False)}\n"
        return subprocess.CompletedProcess(cmd, 0, out.encode("utf-8"), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = HdcDevice("synthetic-hdc").dump_tree("com.example.app", tmp_path / "tree.json")
    assert len(calls) == 1
    assert res.ok is True and res.data["tree"] == tree_payload
    assert not (tmp_path / "tree.json").exists()  # 原契约：落盘由调用方负责


def test_hdc_dump_tree_still_detects_pre_marker_header_failure(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, b"error: screen is locked\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = HdcDevice("synthetic-hdc").dump_tree("com.example.app", tmp_path / "tree.json")
    assert res.ok is False and res.error_class == "screen_locked"


def test_hdc_foreground_and_alive_batches_into_one_shell_call(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert "pidof com.example.app 2>&1" in cmd[-1]
        sep = _extract_sep(cmd[-1], "__VPD_FA_")
        aa = "Mission ID #1\n  bundle name [com.example.app]\n  state #FOREGROUND\n"
        return subprocess.CompletedProcess(cmd, 0, _batch_out(sep, [("AA", aa, 0), ("PID", "4242", 0)]), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dev = HdcDevice("synthetic-hdc")
    fg, alive = dev.foreground_and_alive("com.example.app")
    assert len(calls) == 1 and fg == "com.example.app" and alive is True
    assert dev.last_query_error is None and dev.process_query_error is None


@pytest.mark.parametrize("pid_raw,pid_rc,expected_alive", [
    ("", 1, False),
    ("/system/bin/sh: pidof: not found", 127, None),
    ("", 127, None),
])
def test_hdc_foreground_and_alive_distinguishes_no_process_from_pidof_failure(monkeypatch, pid_raw, pid_rc, expected_alive):
    def fake_run(cmd, **kwargs):
        sep = _extract_sep(cmd[-1], "__VPD_FA_")
        aa = "Mission ID #1\n  bundle name [com.example.app]\n  state #FOREGROUND\n"
        return subprocess.CompletedProcess(cmd, 0, _batch_out(sep, [("AA", aa, 0), ("PID", pid_raw, pid_rc)]), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dev = HdcDevice("synthetic-hdc")
    fg, alive = dev.foreground_and_alive("com.example.app")
    assert fg == "com.example.app"
    assert alive is expected_alive
    if expected_alive is None:
        assert dev.process_query_error is not None
        assert dev.process_query_error["error_class"] == "command_failed"
    else:
        assert dev.process_query_error is None


def test_hdc_batch_rejects_trailing_host_stderr_transport_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        sep = _extract_sep(cmd[-1], "__VPD_FA_")
        aa = "Mission ID #1\n  bundle name [com.example.app]\n  state #FOREGROUND\n"
        return subprocess.CompletedProcess(
            cmd, 0, _batch_out(sep, [("AA", aa, 0), ("PID", "", 1)]),
            b"[Fail][E000004] Communication channel is being established\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    dev = HdcDevice("synthetic-hdc")
    fg, alive = dev.foreground_and_alive("com.example.app")
    assert fg is None and alive is None
    assert dev.last_query_error == "transport_unavailable"
    assert dev.process_query_error["error_class"] == "transport_unavailable"


@pytest.mark.parametrize("mode", ["device_offline", "tool_missing"])
def test_hdc_preflight_short_circuits_after_one_call_when_offline(monkeypatch, mode):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if mode == "tool_missing":
            raise FileNotFoundError("no hdc")
        return subprocess.CompletedProcess(cmd, 0, b"[Fail][E001005] Device not found or connected\r\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    pre = HdcDevice("synthetic-offline", hdc="missing-hdc").preflight("com.example.app")
    assert len(calls) == 1
    assert pre["ok"] is False
    checks = {c["check"]: c for c in pre["checks"]}
    assert checks["device_online"]["ok"] is False and checks["device_online"]["error_class"] == mode
    assert "execution_error" not in checks["device_online"]
    for key in ("screen_awake", "screen_unlocked", "app_installed", "display_info"):
        assert checks[key]["ok"] is False
        assert checks[key]["error_class"] == mode  # 保留离线/缺工具根因，不误报 screen_off/screen_locked/app_missing
        assert checks[key]["execution_error"]["error_class"] == mode
    from vpdtest.verdict import case_verdict
    verdict = case_verdict(pre, {"ok": True}, [], [], None)
    reason = verdict["findings"][0]["reason"]
    assert mode in reason
    assert not any(misleading in reason for misleading in ("screen_off", "screen_locked", "app_missing"))
    assert pre["device"]["model"] is None and pre["app_version"] is None


def test_hdc_preflight_marks_device_online_false_on_timeout(monkeypatch):
    dev = HdcDevice("synthetic-hdc")
    values = {"lock_state": "screenLocked false", "bm_dump": '"versionName": "1.0.0"'}

    def fake_shell(*args, op):
        if op == "power_state":
            return DeviceResult(op, True, False, "timeout", "power_state timeout", "RAW_TIMEOUT")
        return DeviceResult(op, True, True, raw=values[op])

    monkeypatch.setattr(dev, "_shell", fake_shell)
    monkeypatch.setattr(dev, "info", lambda: {"serial": dev.serial})
    monkeypatch.setattr(dev, "display_info", dict)
    pre = dev.preflight("com.example.app")
    online = next(c for c in pre["checks"] if c["check"] == "device_online")
    assert online["ok"] is False and online["error_class"] == "timeout"
    assert "execution_error" not in online


# ---- 2. 单一前置状态复核与策略/弹窗诊断 ----

def test_executor_performs_single_pre_dispatch_state_check(ws, tmp_path):
    spec = _lab_world(tmp_path)
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir)
    dev = ws.device(spec, state_path=store.dir / "replay_state.json")
    dev.launch(BUNDLE, app.ability)
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    lock_calls, fg_calls = [], []
    orig_lock, orig_fg = dev.screen_locked, dev.foreground

    def count_lock():
        lock_calls.append(True)
        return orig_lock()

    def count_fg():
        fg_calls.append(True)
        return orig_fg()

    dev.screen_locked = count_lock
    dev.foreground = count_fg
    res = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0, "revalidate": True,
                                  "target": parse_select("text=唯一入口")}, "session")
    assert res["outcome"] == "passed"
    # 1 次步骤开头的锁屏检查 + 1 次 Dispatcher 下发前复核（原实现会多做 1 次 execute._foreground_ok 导致 3 次）
    assert len(lock_calls) == 2
    att = res["attempts"][0]
    assert att["foreground_check"]["after_revalidate"] is True
    assert "screen_locked" in att["state_recheck"] and "foreground" in att["state_recheck"]


def test_cmd_observe_respects_reviewed_policy_exemptions_for_favorites(ws, tmp_path):
    app = ws.app("qiankun_hm")
    world_path = tmp_path / "qk_fav.json"
    write_json(world_path, {
        "bundle": app.bundle,
        "app_version": "2.0.4.102-replay",
        "device": {"model": "Mate 80", "os": "OpenHarmony-7.0"},
        "screen": [1320, 2848],
        "display": {"density": 3.375, "fold_status": "unknown", "device_form": "bar"},
        "initial": {"locked": False, "screen_on": True, "foreground": app.bundle, "installed": True},
        "launch_screen": "vpd_home",
        "screens": {
            "vpd_home": {
                "root": {"t": "root", "b": [0, 0, 1320, 2848], "children": [
                    {"t": "Stack", "id": "VPD_HOME_VIEW", "b": [0, 0, 1320, 2848], "children": [
                        {"t": "Text", "text": "收藏及常去", "b": [40, 200, 400, 260]},
                        {"t": "Row", "b": [40, 320, 280, 420], "click": True, "children": [
                            {"t": "Text", "text": "收藏夹", "b": [60, 340, 260, 400]}
                        ]},
                    ]},
                ]}
            }
        },
    })
    from vpdtest.cli import build_parser
    spec = f"replay:{world_path}"
    launch = build_parser().parse_args(["act", "launch", "--app", "qiankun_hm", "--session", "obs-fav", "--device", spec])
    commands.cmd_act(ws, launch)
    args = SimpleNamespace(app="qiankun_hm", device=spec, session="obs-fav", label="fav")
    out = commands.cmd_observe(ws, args)
    fav = next(c for c in out["clickable"] if c["label"] == "收藏夹")
    assert fav["allowed"] is True, fav


def test_locate_records_unregistered_blocking_dialogs_and_renders_report(ws, tmp_path):
    spec = _lab_world(tmp_path)
    data = read_json(spec.split(":", 1)[1])
    data["screens"]["home"]["root"]["children"] = [
        {"t": "Dialog", "b": [100, 900, 1220, 1600], "children": [
            {"t": "Text", "text": "您的账号在其他设备登录，此设备已下线", "b": [140, 1000, 1180, 1200]},
            {"t": "Button", "text": "知道了", "b": [400, 1350, 920, 1500], "click": True},
        ]}
    ]
    write_json(spec.split(":", 1)[1], data)
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir)
    dev = ws.device(spec, state_path=store.dir / "replay_state.json")
    dev.launch(BUNDLE, app.ability)
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    res = Executor(ctx).run_step({"id": "S1", "action": "tap", "retry": 0,
                                  "target": parse_select("text=唯一入口")}, "TC-DLG")
    loc = res["attempts"][0]["locate"]
    assert loc["status"] == "not_found"
    assert loc["blocking_dialogs"] == [["您的账号在其他设备登录，此设备已下线", "知道了"]]
    fake_run = {
        "run_id": store.run_id, "validation_level": "offline_replay", "device": {}, "device_spec": spec,
        "app": {"bundle": app.bundle, "version": "3.0.3.200"}, "policy": {"version": "v1"},
        "overlay": {"version": 0}, "actions": 0, "preflight": {"ok": True, "checks": []},
        "env": {"ok": True, "unmet": []}, "steps": [res], "assertions": [], "cleanup": [],
        "case": {"id": "TC-DLG", "title": "弹窗测试", "version": 1, "hash": "000000000000"},
        "verdict": {
            "verdict": "blocked", "label": "阻塞", "findings": [],
            "first_anomaly": {"step": "S1", "attempt": 1, "status": "target_unresolved",
                              "obs_before": res["attempts"][0]["obs_before"], "detail": {"locate": loc}},
        },
    }
    md = render_run(fake_run)
    assert "未登记弹窗遮挡：您的账号在其他设备登录，此设备已下线 / 知道了" in md


# ---- 3. 存储原子性、ID 边界与经验去重 ----

def test_write_json_cleans_up_temp_file_on_write_failure(tmp_path, monkeypatch):
    target = tmp_path / "out.json"

    class _FailingWriter:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, data):
            raise OSError("simulated disk full")

    real_fdopen = os.fdopen

    def fail_fdopen(fd, *args, **kwargs):
        os.close(fd)
        return _FailingWriter()

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    with pytest.raises(OSError, match="simulated disk full"):
        write_json(target, {"a": 1})
    assert list(tmp_path.glob("*.tmp")) == []
    assert real_fdopen is not None


@pytest.mark.parametrize("bad_id", ["../escape", "TC-1.", "NUL", "CON", "aux.1", "COM3", "LPT1.test", "A" * 129])
def test_workspace_and_case_id_reject_unsafe_or_windows_reserved_ids(tmp_path, bad_id):
    ws = Workspace(str(tmp_path / "data"), load_local_config=False)
    with pytest.raises(ValueError):
        cases.validate_case_id(bad_id)
    with pytest.raises(ValueError):
        ws.case_path(bad_id)
    with pytest.raises(ValueError):
        ws.load_suite(bad_id)


def test_experience_upsert_deduplicates_wait_timeout_by_key(ws):
    xs = ExperienceStore(ws.root, "qiankun_hm")
    run1 = {"run_id": "run-1", "case": {"id": "TC-QK-214"}, "device_spec": "hdc:dev1"}
    run2 = {"run_id": "run-2", "case": {"id": "TC-QK-214"}, "device_spec": "hdc:dev1"}
    run3 = {"run_id": "run-3", "case": {"id": "TC-QK-214"}, "device_spec": "hdc:dev1"}
    step, att = {"step_id": "S3"}, {"n": 1, "obs_before": "obs-0001"}
    first = xs._upsert("wait_timeout", {"key": "TC-QK-214:S3", "timeout_s": 11.9}, "d1", run1, step, att)
    assert first is not None
    # 较小抖动值 11.7 不新建重复候选，保留保守上限 11.9
    second = xs._upsert("wait_timeout", {"key": "TC-QK-214:S3", "timeout_s": 11.7}, "d2", run2, step, att)
    assert second is None and len(xs.items()) == 1
    assert xs.get(first["id"])["change"]["timeout_s"] == 11.9
    assert len(xs.get(first["id"])["evidence"]) == 2
    # 更大值 13.5 合并进同一候选并上调 timeout_s
    third = xs._upsert("wait_timeout", {"key": "TC-QK-214:S3", "timeout_s": 13.5}, "d3", run3, step, att)
    assert third is None and len(xs.items()) == 1
    assert xs.get(first["id"])["change"]["timeout_s"] == 13.5
    assert len(xs.get(first["id"])["evidence"]) == 3
    # 已 validated 的条目绝不原地改 change；更高超时会新建独立候选走验证
    item = xs.get(first["id"])
    item["status"] = "validated"
    xs.save_item(item)
    covered = xs._upsert("wait_timeout", {"key": "TC-QK-214:S3", "timeout_s": 12.0}, "d4", run2, step, att)
    assert covered is None and len(xs.items()) == 1
    higher = xs._upsert("wait_timeout", {"key": "TC-QK-214:S3", "timeout_s": 16.0}, "d5", run3, step, att)
    assert higher is not None and len(xs.items()) == 2
    assert xs.get(first["id"])["change"]["timeout_s"] == 13.5


def test_case_readiness_reuses_single_engine_snapshot_and_knowledge_snapshot(ws, monkeypatch):
    from vpdtest import case_readiness, review_snapshot
    orig_engine = review_snapshot.engine_files
    calls = []

    def counting_engine(engine_dir):
        calls.append(engine_dir)
        return orig_engine(engine_dir)

    monkeypatch.setattr(review_snapshot, "engine_files", counting_engine)
    out = case_readiness.readiness(ws, ["TC-HM-S01", "TC-MOTOR-HM-002", "TC-MOTOR-HM-003"])
    assert out["summary"]["requested"] == 3 and out["summary"]["analysis_errors"] == 0
    assert len(calls) == 1  # 单次批量盘点只哈希引擎源码一次，而非每例重复读取


def test_knowledge_snapshot_indexed_get_and_oracle_status_remain_immutable(ws):
    snap = KnowledgeStore(ws.root).snapshot()
    item = snap.get("K-MOT-001")
    assert item is not None
    orig_app = dict(item["applies_to"])
    item["applies_to"]["app"] = "mutated"
    report = snap.oracle_status(["K-MOT-001"], facts={"app": "motor_hm", "app_version": "3.0.3.200"})
    report["items"][0]["applies_to"]["app"] = "mutated-2"
    assert snap.get("K-MOT-001")["applies_to"] == orig_app
