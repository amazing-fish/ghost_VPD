"""PR #23 Codex 检视意见的修复回归（离线合成世界）。

- P1 runs_dir：case lint / from-exploration / from-session 的 check-refs 必须按
  工作区真实 runs 目录（ws.runs_dir）核对，而不是 ws.root/runs（data/runs）
- Issue #32 整屏原文取证：覆盖窗口及窗口查询失败不阻止诊断
- P1 点击前复核：弹窗在采集与下发之间自行消失 → 不按旧坐标盲点，no_cover
- P2 复核失败不谎报：点击后复核采集失败 → indeterminate（效果未知），不是 dismissed
- P2 锚点归属：每个 obs 引用只核对它前方最近的 run/session 锚点，不做跨 run 笛卡尔匹配
"""
from conftest import world
from test_goal_loop import cli
from test_sysdialog import REGISTRY, _register
from vpdtest.cases import unresolvable_obs_refs
from vpdtest.devices import DeviceResult
from vpdtest.dispatch import Dispatcher
from vpdtest.observe import RunStore
from vpdtest.sysdialog import diagnose_cover, load_dialogs, resolve_screen_cover
from vpdtest.util import read_json, read_jsonl, write_json

RUN_A = "run-20261008-201507-4990"
RUN_B = "run-20261008-201709-bec1"


# ---- P1：check-refs 用工作区真实 runs 目录 ----

def _kb_case(ws, rationale, cid="TC-REFSDIR-1"):
    case = {"id": cid, "version": 1, "status": "candidate", "app": "motor_hm", "title": "t", "purpose": "p",
            "preconditions": [], "env_requirements": {}, "test_data": {}, "test_data_none": True,
            "authorizations": [], "steps": [{"id": "S1", "action": "launch"}], "assertions": [],
            "knowledge_used": [{"id": "K-AV-001", "use": "entry_success", "applicability": "applies",
                                "rationale": rationale}],
            "cleanup": [], "sources": []}
    write_json(ws.root / "cases" / f"{cid}.json", case)
    return cid


def test_case_lint_checks_the_workspace_runs_dir(ws):
    """证据留在 ws.runs_dir（RunStore 的真实目录）：CLI lint 应放行锚点引用。
    修复前 lint 检查的是 ws.root/runs（不存在的目录），合法引用一律被误拦。"""
    (ws.runs_dir / RUN_A / "obs" / "obs-0007").mkdir(parents=True)
    _kb_case(ws, f"{RUN_A} 的 obs-0007 显示入口成功")
    out = cli(ws, "case", "lint", "TC-REFSDIR-1")
    assert not any(i["field"] == "knowledge_used" for i in out["issues"])


def test_case_from_session_lint_uses_workspace_runs_dir(ws, tmp_path):
    """from-session 固化的用例补上锚点引用后 lint：session 观测留存于 ws.runs_dir，
    lint 调用点必须按真实目录核对（修复前 from-session 的 lint 检查 ws.root/runs，合法引用被误拦）。"""
    from test_goal_loop import _lab_world, _start
    dev = _lab_world(tmp_path)
    _start(ws, dev, "rsd1")
    cli(ws, "act", "tap", "--select", "text=唯一入口", "--app", "motor_hm", "--session", "rsd1", "--device", dev)
    cli(ws, "case", "from-session", "--session", "rsd1", "--new-id", "TC-REFSDIR-2", "--app", "motor_hm")
    obs_ids = sorted(p.name for p in (ws.runs_dir / "session-rsd1" / "obs").glob("obs-*"))
    case = read_json(ws.root / "cases" / "TC-REFSDIR-2.json", {})
    case.update(purpose="进入详情页", preconditions=["APP 已登录"],
                assertions=[{"id": "A1", "at": case["steps"][-1]["id"], "type": "text_present",
                             "text": "详情页", "oracle_refs": []}],
                knowledge_used=[{"id": None, "note": "无",
                                 "rationale": f"session-rsd1 的 {obs_ids[-1]} 显示唯一入口可点"}])
    write_json(ws.root / "cases" / "TC-REFSDIR-2.json", case)
    out = cli(ws, "case", "lint", "TC-REFSDIR-2")
    assert not [i for i in out["issues"] if i["field"] == "knowledge_used"], out["issues"]


# ---- Issue #32：整屏取证 ----

def _diag_ctx(ws):
    dev = ws.device(world("motor_hm_usb_dialog.json"))
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir, kind="t")
    dev.launch(app.bundle, app.ability)
    return dev, store


def test_diagnose_cover_collects_tree_when_lock_window_shown(ws):
    """锁屏窗口在显示仍采集整屏树；动作能力保持独立约束。"""
    _register(ws)
    dev, store = _diag_ctx(ws)
    dev.state["windows"] = ["app0", "SCBScreenLock"]
    d = store.dir / "diag-private"
    diag = diagnose_cover(dev, store, d, load_dialogs(ws.root))
    assert diag["dump_ok"] and diag["dump_error"] is None
    assert "SCBScreenLock" in diag["windows"]
    assert (store.dir / diag["screen_tree"]).is_file()


def test_diagnose_cover_collects_tree_when_window_query_fails(ws):
    _register(ws)
    dev, store = _diag_ctx(ws)
    dev.state["windows"] = None  # 窗口查询失败不影响整屏采集
    diag = diagnose_cover(dev, store, store.dir / "diag-qfail", load_dialogs(ws.root))
    assert diag["dump_ok"] and diag["windows"] is None
    assert (store.dir / diag["screen_tree"]).is_file()


def test_diagnose_cover_collects_registered_dialog(ws):
    """已登记系统弹窗：整屏诊断照常取证。"""
    _register(ws)
    dev, store = _diag_ctx(ws)
    dev.state["windows"] = ["app0", "UsbModeDialog"]
    diag = diagnose_cover(dev, store, store.dir / "diag-ok", load_dialogs(ws.root))
    assert diag["dump_ok"] and diag["matched"] == "usb_mode"
    assert diag["screen_tree"] and (store.dir / "diag-ok" / "screen_tree.json").exists()


# ---- P1：点击前复核 / P2：复核失败不谎报 ----

def test_dialog_vanishing_before_tap_is_not_blindly_tapped(ws, monkeypatch):
    """采集与下发之间弹窗自行消失：不按旧坐标盲点（可能误中底下带副作用的控件）。"""
    _register(ws)
    dev, store = _diag_ctx(ws)
    dev.state["windows"] = ["app0", "UsbModeDialog"]
    real = dev.dump_screen

    def vanish_after_first_dump(path):
        r = real(path)
        dev.state["system_dialog"] = None  # 第一次采集完成后、点击下发前弹窗自行消失
        return r

    monkeypatch.setattr(dev, "dump_screen", vanish_after_first_dump)
    res = resolve_screen_cover(ws, dev, store, dispatcher=Dispatcher(dev, store),
                               dialogs=load_dialogs(ws.root))
    assert res["status"] == "no_cover" and "自行消失" in res["reason"]
    assert not [e for e in read_jsonl(store.events_path) if e["event"] == "dispatch"]  # 没有下发任何点击


def test_failed_recheck_is_indeterminate_not_dismissed(ws, monkeypatch):
    """点击后复核采集失败：关闭效果未知，不谎报 dismissed。"""
    _register(ws)
    dev, store = _diag_ctx(ws)
    dev.state["windows"] = ["app0", "UsbModeDialog"]
    real = dev.dump_screen
    calls = {"n": 0}

    def flaky_dump(path):
        calls["n"] += 1
        if calls["n"] >= 3:  # 第 3 次（点击后复核）失败
            return DeviceResult("dump_screen", True, False, "timeout", "fixture 复核失败")
        return real(path)

    monkeypatch.setattr(dev, "dump_screen", flaky_dump)
    res = resolve_screen_cover(ws, dev, store, dispatcher=Dispatcher(dev, store),
                               dialogs=load_dialogs(ws.root))
    assert res["status"] == "indeterminate" and "效果未知" in res["reason"]
    assert res["dismiss"]["sent"] is True  # 点击确实发生了——不掩盖首次结果


# ---- P2：obs 引用与锚点一一归属 ----

def test_obs_ref_binds_to_its_nearest_preceding_anchor(tmp_path):
    """“run-A obs-1；run-B obs-2”各归各：obs-0001 只存在于 run-B 时，跟在 run-A 后面就是错引。
    修复前的笛卡尔匹配会让它借 run-B 的留存蒙混过关。"""
    (tmp_path / RUN_B / "obs" / "obs-0001").mkdir(parents=True)
    (tmp_path / RUN_B / "obs" / "obs-0002").mkdir(parents=True)
    problems = unresolvable_obs_refs(f"{RUN_A} 的 obs-0001 显示X；{RUN_B} 的 obs-0002 显示Y", tmp_path)
    assert any("obs-0001" in p and RUN_A in p for p in problems)  # run-A 未留存 → 拦
    assert not any("obs-0002" in p for p in problems)             # run-B 留存 → 放行


def test_obs_ref_without_preceding_anchor_is_flagged(tmp_path):
    """锚点写在 obs 之后（或没有锚点）：引用不归属任何锚点，按裸引用拦。"""
    (tmp_path / RUN_A / "obs" / "obs-0009").mkdir(parents=True)
    problems = unresolvable_obs_refs(f"obs-0009（见 {RUN_A}）", tmp_path)
    assert any("obs-0009" in p and "锚点" in p for p in problems)
    assert not problems or all("未留存" not in p for p in problems)  # 不再退化为查错锚点
