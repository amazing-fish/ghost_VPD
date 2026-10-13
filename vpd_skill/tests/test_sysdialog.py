"""系统弹层原子能力（2026-10-08 USB 连接方式弹窗阻塞场景）：
- preflight 窗口名快查 → 关闭已登记已评审弹窗（launch 前完成，步骤零感知）；
- unobservable 恢复路径 → 诊断 → 关闭 → 重试，首次 unobservable 异常保留；
- 治理边界：未评审的登记弹窗不关闭，维持阻塞；
- 截图取证：弹窗盖住且窗口表确认无隐私窗口时截屏留证；锁屏盖住仍不截图。"""
from pathlib import Path

from vpdtest.devices.replay import expand
from vpdtest.experience import Overlay
from vpdtest.observe import RunStore
from vpdtest.runner import run_case
from vpdtest.sysdialog import pick_dismiss
from vpdtest.tree import UiTree
from vpdtest.util import read_jsonl, write_json
from vpdtest.workspace import RunContext

from conftest import confirm, world

REGISTRY = {"dialogs": {"usb_mode": {
    "desc": "USB 连接方式选择弹窗（测试登记）", "class": "benign",
    "window_names": ["UsbModeDialog"],
    "match": {"require_any_text": ["USB连接方式", "USB 连接方式"]},
    "dismiss": {"prefer_text": ["取消", "仅充电"], "forbid_text": ["传输文件", "传输照片"]},
    "review": "reviewed", "review_basis": "pytest: 模拟评审通过"}}}


def _register(ws, **patch):
    write_json(ws.root / "system_dialogs.json",
               {"dialogs": {"usb_mode": {**REGISTRY["dialogs"]["usb_mode"], **patch}}})


def _steps(run):
    for s in run["steps"]:
        for sub in s.get("sub_steps", [s]):
            yield sub


def test_preflight_dismisses_registered_dialog_before_steps(ws):
    """窗口名快查命中：弹窗在第一个步骤之前被原子能力关闭，用例正常执行。"""
    confirm(ws, "K-MOT-001", "K-MOT-002")
    _register(ws)
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_usb_dialog.json"))
    v = run["verdict"]
    assert v["verdict"] == "passed", v
    assert run["preflight"]["system_dialog"]["status"] == "dismissed"
    assert run["system_dialogs"][0]["dialog"] == "usb_mode"
    ev = [e for e in read_jsonl(f"{run['dir']}/events.jsonl")
          if e.get("event") == "capability_result" and e.get("capability") == "system_dialog"]
    assert ev and ev[0]["status"] == "dismissed" and ev[0]["diagnosis"]["matched"] == "usb_mode"
    # forbid 按钮绝不被点击；关闭按钮出现在能力事件里而不是用例步骤里
    assert ev[0]["dismiss"]["button"] == "取消"
    taps = [a.get("locate", {}).get("target") for sub in _steps(run) for a in sub.get("attempts", [])]
    assert "传输文件" not in taps


def test_unobservable_recovery_dismisses_dialog_and_first_anomaly_kept(ws):
    """preflight 快查未命中（登记无窗口名）：launch 后取不到可判定观测 → 恢复分支整屏诊断 →
    关闭已评审弹窗 → 重试通过。第一次 unobservable 尝试保留在 attempts 里。"""
    confirm(ws, "K-MOT-001", "K-MOT-002")
    _register(ws, window_names=None)
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_usb_dialog.json"))
    v = run["verdict"]
    assert v["verdict"] == "passed", v
    assert run["preflight"]["system_dialog"]["status"] == "no_cover"
    atts = [a for sub in _steps(run) for a in sub.get("attempts", [])]
    bad = [a for a in atts if a["status"] != "ok"]
    assert bad and bad[0]["status"] == "unobservable"  # 首次异常不擦除
    recs = [r for sub in _steps(run) for r in sub.get("recoveries", [])]
    assert any(r["action"] == "dismiss_system_dialog" and r["performed"] for r in recs)
    assert run["system_dialogs"][0]["status"] == "dismissed"


def test_unreviewed_dialog_is_never_dismissed(ws):
    """命中登记但 review=pending：能力只诊断不动作，步骤维持 unobservable 阻塞（工具归属）。"""
    confirm(ws, "K-MOT-001", "K-MOT-002")
    _register(ws, review="pending")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_usb_dialog.json"))
    v = run["verdict"]
    assert v["verdict"] == "blocked"
    assert run["system_dialogs"] and run["system_dialogs"][0]["status"] == "needs_review"
    ev = [e for e in read_jsonl(f"{run['dir']}/events.jsonl")
          if e.get("event") == "capability_result" and e.get("capability") == "system_dialog"]
    assert ev and ev[0]["status"] == "needs_review" and "dismiss" not in ev[0]
    atts = [a for sub in _steps(run) for a in sub.get("attempts", [])]
    bad = [a for a in atts if a["status"] != "ok"]
    assert bad and bad[0]["status"] == "unobservable"


def _covered_ctx(ws, windows=None):
    dev = ws.device(world("motor_hm_usb_dialog.json"))
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir, kind="t")
    ctx = RunContext(ws, app, dev, store, Overlay([]), mode="regression")
    dev.launch(app.bundle, app.ability)
    if windows is not None:
        dev.state["windows"] = windows
    return ctx, dev, store


def test_covered_screen_screenshot_captured_as_evidence(ws):
    """弹窗盖住（树为空）：截屏留证 + 整屏诊断树落盘——
    2026-10-08 治理要求：无法确认的屏幕状态尽可能通过截屏解决。"""
    _register(ws)
    ctx, dev, store = _covered_ctx(ws)
    obs, tree = ctx.observe("covered")
    assert obs["tree"]["status"] == "empty"
    assert obs["screen_cover"]["matched"] == "usb_mode"
    assert obs["screen_cover"]["windows"] == ["app0", "UsbModeDialog"]
    assert obs["screenshot"]["status"] == "ok"
    assert (Path(store.dir) / obs["screenshot"]["path"]).exists()
    assert (Path(store.dir) / obs["screen_cover"]["screen_tree"]).exists()


def test_lock_screen_cover_keeps_screenshot_and_tree(ws):
    _register(ws)
    ctx, dev, store = _covered_ctx(ws, windows=["app0", "SCBScreenLock"])
    obs, tree = ctx.observe("lock-covered")
    assert tree.status == "empty"
    assert obs["screenshot"]["status"] == "ok"
    assert (store.dir / obs["screenshot"]["path"]).is_file()
    assert "SCBScreenLock" in obs["screen_cover"]["windows"]
    assert (store.dir / obs["screen_cover"]["screen_tree"]).is_file()


def test_pick_dismiss_prefers_cancel_and_never_forbidden():
    """关闭按钮选择：prefer 顺序优先「取消」；forbid 的「传输文件」永不出现在候选里。"""
    root = {"t": "Dialog", "b": [160, 1100, 1160, 1800], "children": [
        {"t": "Text", "text": "USB连接方式", "b": [220, 1160, 1100, 1240]},
        {"t": "Button", "b": [220, 1420, 640, 1560], "click": True, "children": [
            {"t": "Text", "text": "仅充电", "b": [300, 1450, 560, 1530]}]},
        {"t": "Button", "b": [680, 1420, 1100, 1560], "click": True, "children": [
            {"t": "Text", "text": "传输文件", "b": [760, 1450, 1020, 1530]}]},
        {"t": "Button", "b": [220, 1620, 1100, 1740], "click": True, "children": [
            {"t": "Text", "text": "取消", "b": [560, 1650, 700, 1710]}]}]}
    tree = UiTree({"attributes": {"type": "orgRoot", "bounds": "[0,0][1320,2848]"},
                   "children": [expand(root, "")]})
    btn = pick_dismiss(REGISTRY["dialogs"]["usb_mode"], tree)
    assert btn and btn["prefer"] == "取消" and "取消" in btn["text"]
    assert 220 <= btn["point"][0] <= 1100 and 1620 <= btn["point"][1] <= 1740
    # 整屏树里只有 forbid 按钮时：宁可不关闭（no_safe_button），不点危险按钮
    only_forbidden = {"t": "Dialog", "b": [160, 1100, 1160, 1800], "children": [
        {"t": "Text", "text": "USB连接方式", "b": [220, 1160, 1100, 1240]},
        {"t": "Button", "b": [220, 1620, 1100, 1740], "click": True, "children": [
            {"t": "Text", "text": "传输文件", "b": [560, 1650, 700, 1710]}]}]}
    tree2 = UiTree({"attributes": {"type": "orgRoot", "bounds": "[0,0][1320,2848]"},
                    "children": [expand(only_forbidden, "")]})
    assert pick_dismiss(REGISTRY["dialogs"]["usb_mode"], tree2) is None
