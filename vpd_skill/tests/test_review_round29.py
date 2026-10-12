"""第 29 轮检视修复的回归测试（Issue #18/#19/#20/#21/#22）；仅离线合成数据。

- #18 被中断的 monitor/wait 请求不计入物理动作预算；手势类仍按已下发保守计入
- #19 case from-session 读会话记录期间持有会话锁；锁被占用时与 observe/act 一致地超时
- #20 会话 JSONL 末行被截断时可恢复（丢弃并留证），中间行损坏仍报错；预算不低估
- #21 vehicle_motion 命令已发出（未确认成功）即挂看门狗；未发出不挂；
      观测认不出停止状态时 safe_stop 仍执行声明的步骤
- #22 布局树解析失败时运行目录不留未打码的 tree.json；正常路径行为不变
"""
import json
import threading
from pathlib import Path

import pytest

from test_dispatch_regression import _ctx
from test_goal_loop import _lab_world, _start, cli
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.execute import Executor
from vpdtest.safety import MotionGuard, safe_stop
from vpdtest.session import trail
from vpdtest.util import append_jsonl, read_json, write_json


# ---- #18：被中断的 monitor/wait 不计入物理动作预算 ----

def test_interrupted_monitor_and_wait_do_not_count_against_budget(tmp_path):
    path = tmp_path / "events.jsonl"

    def ev(event, **kw):
        append_jsonl(path, {"ts": "t", "event": event, **kw})

    # monitor/wait 被中断（没有结果事件）：不下发手势，不应计入预算
    ev("act_request", seq=1, step_id="A1", request={"action": "monitor", "duration": 2})
    ev("act_request", seq=2, step_id="A2", request={"action": "wait", "duration": 1})
    # 手势类被中断：仍按已下发保守计入（命令可能已到设备）
    ev("act_request", seq=3, step_id="A3", request={"action": "tap"})
    ev("act_request", seq=4, step_id="A4", request={"action": "back"})
    tr = trail(tmp_path)
    kinds = [i["kind"] for i in tr["items"]]
    assert kinds == ["incomplete"] * 4
    assert all(i["effect_unknown"] for i in tr["items"])
    assert tr["actions"] == 2  # 只有 tap/back 两个手势类按保守口径计入


# ---- #20：末行截断可恢复，中间行损坏仍报错 ----

def test_truncated_last_line_is_dropped_and_budget_stays_conservative(tmp_path):
    path = tmp_path / "events.jsonl"
    append_jsonl(path, {"ts": "t", "event": "act_request", "seq": 1, "step_id": "A1",
                        "request": {"action": "tap", "select": "text=唯一入口"}})
    # 进程被杀：末行写了一半，没有换行
    path.write_text(path.read_text(encoding="utf-8") + '{"ts": "t", "event": "act_result", "seq": 1, "outco',
                    encoding="utf-8")
    tr = trail(tmp_path)  # 恢复成功，不再抛 JSON 解析错误
    assert tr["items"][0]["kind"] == "incomplete"
    assert tr["actions"] == 1  # 被丢弃的可能是该请求的结果事件：手势类按已下发保守计入，不低估
    assert "act_result" in tr["truncated_tail"]["events"]["tail"]  # 丢弃的行留证


def test_corrupted_middle_line_still_raises(tmp_path):
    path = tmp_path / "events.jsonl"
    append_jsonl(path, {"ts": "t", "event": "act_request", "seq": 1, "step_id": "A1",
                        "request": {"action": "tap"}})
    append_jsonl(path, {"ts": "t", "event": "dispatch", "op": "tap", "sent": True})
    # 中间行损坏：不是截断，必须报错，不静默吞掉
    lines = path.read_text(encoding="utf-8").splitlines()
    lines.insert(1, '{"broken": tru')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        trail(tmp_path)


def test_clean_session_has_no_truncated_tail_marker(tmp_path):
    path = tmp_path / "events.jsonl"
    append_jsonl(path, {"ts": "t", "event": "act_request", "seq": 1, "step_id": "A1",
                        "request": {"action": "tap"}})
    append_jsonl(path, {"ts": "t", "event": "act_rejected", "seq": 1, "reason": "policy"})
    tr = trail(tmp_path)
    assert "truncated_tail" not in tr and tr["actions"] == 0


# ---- #19：case from-session 持有会话锁 ----

def test_case_from_session_holds_the_session_lock(ws, tmp_path, monkeypatch):
    spec = _lab_world(tmp_path)
    _start(ws, spec, "s1")
    cli(ws, "act", "tap", "--select", "text=唯一入口", "--app", "motor_hm", "--session", "s1", "--device", spec)

    import vpdtest.commands as commands_mod
    from vpdtest.session import session_lock as real_lock
    acquired = []

    def recording_lock(session_dir, timeout_s=30.0):
        acquired.append(Path(session_dir).name)
        return real_lock(session_dir, timeout_s=1.0)  # 测试里不等 30 秒

    monkeypatch.setattr(commands_mod, "session_lock", recording_lock)
    sdir = ws.runs_dir / "session-s1"

    # 正常路径：from-session 读取会话期间持锁（与 observe/act 同一互斥）
    out = cli(ws, "case", "from-session", "--session", "s1", "--new-id", "TC-LOCK-1", "--app", "motor_hm")
    assert out["case"] == "TC-LOCK-1" and "session-s1" in acquired

    # 锁被占用：from-session 与其它命令一致地超时，而不是读到写了一半的记录
    acquired.clear()
    box = {}

    def run_from_session():
        try:
            cli(ws, "case", "from-session", "--session", "s1", "--new-id", "TC-LOCK-2", "--app", "motor_hm")
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    with real_lock(sdir, timeout_s=1.0):
        t = threading.Thread(target=run_from_session)
        t.start()
        t.join(timeout=10)
    assert not t.is_alive()
    assert isinstance(box.get("error"), TimeoutError)
    assert not (ws.root / "cases" / "TC-LOCK-2.json").exists()  # 没有基于半份记录生成用例


# ---- #21：运动命令已发出但未确认成功时也要挂看门狗 ----

def _motion_ctx(ws, tmp_path):
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.authorized = {"vehicle_motion"}
    # 观测认不出停止状态的世界：stopped_states 用一个不会出现的状态
    ctx.motion = MotionGuard({"safe_stop": [{"action": "back"}], "max_motion_s": 60,
                              "stopped_states": ["never_seen_state"]}, ctx.clock, ctx.store)
    return ctx


def test_motion_tap_sent_but_failed_still_arms_watchdog_and_safe_stop_runs(ws, tmp_path):
    ctx = _motion_ctx(ws, tmp_path)
    # HDC 超时：命令已发出（sent=True）、执行器未确认成功（ok=False）——车可能已收到命令
    ctx.device.tap = lambda *p: DeviceResult("tap", True, False, "timeout", "hdc 超时")
    keys = []
    ctx.device.key = lambda name: keys.append(name) or DeviceResult("key", True, True)
    target = {"desc": "开始泊车", "side_effect": "vehicle_motion",
              "strategies": [{"kind": "text", "value": "唯一入口"}]}
    Executor(ctx).run_step({"id": "M1", "action": "tap", "target": target, "retry": 0}, "fixture")
    # 修复前：只有 res.ok 才挂看门狗 → armed 为 None，safe_stop 全部跳过
    assert ctx.motion.armed is not None and ctx.motion.armed["step"] == "M1"
    rec = safe_stop(Executor(ctx), "motion_timeout")
    # 观测认不出停止状态：声明好的停止步骤仍被执行（未被当作「已观测到停止」跳过）
    assert keys == ["back"] and rec["steps"][0].get("skipped") is None
    assert not rec["confirmed_stopped"]  # 不谎报已停：要求人工接管


def test_motion_tap_not_sent_does_not_arm_watchdog(ws, tmp_path):
    ctx = _motion_ctx(ws, tmp_path)
    # hdc 工具缺失：命令根本没发出去（sent=False）——没有理由监护
    ctx.device.tap = lambda *p: DeviceResult("tap", False, False, "tool_missing", "找不到 hdc")
    target = {"desc": "开始泊车", "side_effect": "vehicle_motion",
              "strategies": [{"kind": "text", "value": "唯一入口"}]}
    Executor(ctx).run_step({"id": "M1", "action": "tap", "target": target, "retry": 0}, "fixture")
    assert ctx.motion.armed is None


# ---- #22：布局树解析失败时运行目录不留未打码的原文 ----

def _stub_hdc(dev, tree_text):
    """桩掉 hdc 子进程：dumpLayout 成功，file recv 把给定内容写到目标文件（模拟拉取）。"""
    pulled = []

    def fake_shell(*args, op, timeout=None):
        return DeviceResult(op, True, True, raw="ok")

    def fake_run(args, op, timeout=None, with_target=True):
        if args[:2] == ["file", "recv"]:
            local = Path(args[3])
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(tree_text, encoding="utf-8")
            pulled.append(local)
            return DeviceResult(op, True, True, raw="transfer ok")
        return DeviceResult(op, True, True, raw="ok")

    dev._shell, dev._run = fake_shell, fake_run
    return pulled


def test_dump_tree_parse_failure_leaves_no_raw_tree(tmp_path):
    dev = HdcDevice(serial="FAKE")
    pulled = _stub_hdc(dev, '{"attributes": {"type": "orgRoot"')  # 截断的 JSON
    final = tmp_path / "obs" / "obs-000001" / "tree.json"
    final.parent.mkdir(parents=True)
    r = dev.dump_tree("com.vpd.app", final)
    assert not r.ok and r.error_class == "tree_parse_error"
    assert not final.exists()                    # 最终路径没有出现原文
    assert list(tmp_path.rglob("tree.json")) == []  # 整个运行目录都没有
    assert all(not p.exists() for p in pulled)   # 临时文件已清理


def test_dump_tree_success_returns_tree_without_writing_final_path(tmp_path):
    dev = HdcDevice(serial="FAKE")
    tree = {"attributes": {"type": "orgRoot", "bounds": "[0,0][100,100]"}, "children": []}
    _stub_hdc(dev, json.dumps(tree, ensure_ascii=False))
    final = tmp_path / "obs" / "obs-000001" / "tree.json"
    final.parent.mkdir(parents=True)
    r = dev.dump_tree("com.vpd.app", final)
    assert r.ok and r.data["tree"] == tree
    assert not final.exists()  # 原文不落盘；最终路径由调用方打码后写


def test_observe_normal_path_still_writes_tree_json(ws, tmp_path):
    ctx = _ctx(ws, _lab_world(tmp_path))
    obs, tree = ctx.observe("before")
    tree_path = Path(ctx.store.dir) / "obs" / obs["obs_id"] / "tree.json"
    assert tree_path.exists()  # 正常路径行为不变：合成世界的树仍落盘（回放数据本就无敏感值）
    assert read_json(tree_path, {})["attributes"]["type"] == "orgRoot"
