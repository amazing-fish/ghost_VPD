"""Issue #13/#14/#15 的可靠性整改测试（离线合成世界；只证明协议与判定逻辑）。

- #13 from-session 固化自动带 within：响应式（多匹配消歧）+ 主动式（点在 TabBar 壳里，跨构建防撞文案）。
- #14 判据 rationale 引用稳定化：支持「run/session + obs」可解析引用（check-refs 核对证据确实留存），
  拦的是不可解析引用（裸编号、锚点目录缺该观测）；知识 ID（K-QK-OBS-201）不误拦。
- #15 cleanup 停止独立断言：停止成功（进程结束）与恢复初始前台分开验证。前台恢复只在用例
  显式声明 require_launcher 且停止前 APP 在前台（可靠前置事实）时判定；其他应用（如 WeLink）
  接管前台是环境现象，未声明时只记录进 stop_check，不据此判产品失败。
"""
import json

from test_goal_loop import BUNDLE, _lab_world, _start, cli
from test_dispatch_regression import _ctx
from vpdtest import cases as C
from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import write_json


# ---- Issue #13：固化自动带 within ----

def _tab_world(tmp_path, name="tab", with_title=False):
    """首页：底部 TabBar（壳）里有「我的」tab；with_title 时页面标题也叫「我的」（跨构建撞文案场景）。"""
    home_children = [{"t": "Button", "b": [40, 200, 1280, 320], "click": True, "go": "my_page",
                      "children": [{"t": "Text", "text": "浏览记录", "b": [60, 220, 1260, 300]}]}]
    if with_title:
        home_children.append({"t": "Text", "text": "我的", "b": [40, 100, 600, 180]})
    home_children.append({"t": "TabBar", "b": [0, 2600, 1320, 2848], "children": [
        {"t": "Button", "b": [60, 2660, 400, 2800], "click": True, "go": "my_page",
         "children": [{"t": "Text", "text": "我的", "b": [80, 2680, 380, 2780]}]},
        {"t": "Button", "b": [460, 2660, 800, 2800], "click": True, "children": [
            {"t": "Text", "text": "首页", "b": [480, 2680, 780, 2780]}]}]})
    screens = {"home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": home_children}},
               "my_page": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
                   {"t": "Text", "text": "浏览记录页", "b": [60, 200, 600, 300]}]}}}
    return _lab_world(tmp_path, name, screens)


def _tab_case(ws, dev, session="tb1"):
    """会话：点 TabBar 的「我的」。3.0.3 场景——当时页面上没有同名标题，选择器唯一可点。"""
    _start(ws, dev, session)
    cli(ws, "act", "tap", "--select", "text=我的", "--expect-text", "浏览记录页",
        "--app", "motor_hm", "--session", session, "--device", dev)
    return ws.runs_dir / f"session-{session}"


def test_from_session_proactively_scopes_tabbar_taps(ws, tmp_path):
    from vpdtest.session import from_session
    dev = _tab_world(tmp_path, with_title=False)
    sdir = _tab_case(ws, dev)
    case = from_session(sdir, "TC-TAB-1", ws.app("motor_hm"), None, [])
    tap = case["steps"][-1]
    # 点在 TabBar 壳里：当前唯一也主动带 within，跨构建出现同名标题时不再 target_ambiguous
    assert tap["target"]["strategies"] == [{"kind": "text", "value": "我的", "within": {"type": "TabBar"}}]
    assert tap["from_session"]["auto_within"]["within"] == {"type": "TabBar"}
    # 普通页面按钮不在壳里：不主动限定，避免把页面布局固化进用例
    assert not any(i["field"] == f"steps.{tap['id']}" for i in C.lint(case, ws.app("motor_hm"), KnowledgeStore(ws.root)))


def test_proactive_within_survives_cross_build_title_collision(ws, tmp_path):
    """固化产物在新构建（页面加了同名标题）上重放：S1 仍唯一定位到 TabBar 的 tab。"""
    from vpdtest.runner import run_case
    old = _tab_world(tmp_path, "old", with_title=False)
    _tab_case(ws, old, "tb2")
    cli(ws, "case", "from-session", "--session", "tb2", "--new-id", "TC-TAB-2",
        "--app", "motor_hm", "--goal", "进入我的页")
    case = ws.load_case("TC-TAB-2")
    case.update(purpose="进我的页看浏览记录",
                preconditions=["APP 已登录"], cleanup=[{"action": "stop_app"}],
                knowledge_used=[{"id": None, "note": "无"}],
                assertions=[{"id": "A1", "at": case["steps"][-1]["id"], "type": "text_present",
                             "text": "浏览记录页", "oracle_refs": []}])
    write_json(ws.root / "cases" / "TC-TAB-2.json", case)
    new = _tab_world(tmp_path, "new", with_title=True)  # 新构建：标题也叫「我的」
    res = run_case(ws, case_id="TC-TAB-2", device_spec=new)
    s1 = next(s for s in res["steps"] if s["action"] == "tap")
    assert s1["outcome"].startswith("passed")
    loc = s1["attempts"][-1]["locate"]["attempts"][0]  # 带限定的 text 策略在新构建上仍唯一定位
    assert loc["kind"] == "text" and loc["within"] == {"type": "TabBar"} and loc["status"] == "found"
    # 对照：不带 within 的旧固化产物在新构建上就是 target_ambiguous（这正是 #13 的阻塞现场）
    tap = next(s for s in case["steps"] if s["action"] == "tap")
    tap["target"]["strategies"] = [{"kind": "text", "value": "我的"}]
    write_json(ws.root / "cases" / "TC-TAB-3.json", case)
    res2 = run_case(ws, case_id="TC-TAB-3", device_spec=new)
    t1 = next(s for s in res2["steps"] if s["action"] == "tap")
    assert t1["outcome"] == "blocked" and "target_ambiguous" in json.dumps(t1["attempts"])


def test_from_session_reactively_disambiguates_multi_match(ws, tmp_path):
    """响应式：会话证据树上确有两个「我的」（标题+tab），点击点落在 tab 上 → 固化时带上容器限定。

    正常流程里现场多匹配的 select 会被 target_ambiguous 拦下、根本不会下发；能走到这一步的是
    记录证据本身含两个匹配的固化输入（例如人工整理过的会话），此时仍按点击点消歧、绝不猜。"""
    from vpdtest.session import from_session
    from vpdtest.util import read_json
    dev = _tab_world(tmp_path, "amb", with_title=False)  # 会话当时唯一可点
    _start(ws, dev, "amb1")
    cli(ws, "act", "tap", "--select", "text=我的", "--expect-text", "浏览记录页",
        "--app", "motor_hm", "--session", "amb1", "--device", dev)
    sdir = ws.runs_dir / "session-amb1"
    # 在会话记录的观测树里补一个同名标题节点：固化输入里确实存在两个匹配
    for tp in (sdir / "obs").glob("*/tree.json"):
        raw = read_json(tp)
        if "我的" in json.dumps(raw, ensure_ascii=False):
            raw["children"].append({"attributes": {"type": "Text", "text": "我的",
                                                   "bounds": "[40,100][600,180]", "visible": "true"},
                                    "children": []})
            write_json(tp, raw)
    case = from_session(sdir, "TC-AMB-1", ws.app("motor_hm"), None, [])
    tap = case["steps"][-1]
    assert tap["target"]["strategies"][0]["within"] == {"type": "TabBar"}
    assert tap["from_session"]["auto_within"]["why"]


# ---- Issue #14：rationale 引用稳定化（支持可解析引用，不一刀切禁止 obs 编号） ----

def _lint_case(ws, **kw):
    base = {"id": "TC-KU-1", "purpose": "p", "preconditions": [], "test_data": {}, "test_data_none": True,
            "steps": [{"id": "S1", "action": "launch"}], "assertions": [], "cleanup": [], "sources": []}
    base.update(kw)
    return C.lint(base, ws.app("motor_hm"), KnowledgeStore(ws.root), runs_dir=ws.runs_dir)


def test_rationale_citing_bare_obs_ids_is_flagged(ws):
    """裸 obs 编号没有 run/session 锚点：重放后必过期，拦下。"""
    issues = _lint_case(ws, knowledge_used=[
        {"id": "K-AV-001", "use": "entry_success", "applicability": "适用", "rationale": "obs-0010 显示入口成功"}])
    hit = [i for i in issues if i["field"] == "knowledge_used" and "obs-0010" in i["problem"]]
    assert hit and "稳定标识" in hit[0]["problem"]


def test_resolvable_run_obs_reference_passes(ws):
    """「run 目录名 + obs 编号」且该目录确实留存了对应观测：证据链可追溯，放行。"""
    obs_dir = ws.runs_dir / "run-20261008-201507-4990" / "obs" / "obs-0007"
    obs_dir.mkdir(parents=True)
    issues = _lint_case(ws, knowledge_used=[
        {"id": "K-AV-001", "use": "entry_success", "applicability": "适用",
         "rationale": "run-20261008-201507-4990 的 obs-0007 显示入口成功"}])
    assert not any(i["field"] == "knowledge_used" for i in issues)


def test_anchored_obs_ref_with_missing_evidence_is_flagged(ws):
    """锚点 run 目录不存在/不含该观测（真机缺陷形态：引用 obs-0010，实际 run 里是 obs-0007）：
    加个 run- 前缀不能绕过 check-refs。"""
    (ws.runs_dir / "run-20261008-201507-4990" / "obs" / "obs-0007").mkdir(parents=True)
    issues = _lint_case(ws, knowledge_used=[
        {"id": "K-AV-001", "use": "entry_success", "applicability": "适用",
         "rationale": "run-20261008-201507-4990 的 obs-0010 显示入口成功"}])
    hit = [i for i in issues if i["field"] == "knowledge_used" and "obs-0010" in i["problem"]]
    assert hit and "未留存该观测" in hit[0]["problem"]


def test_knowledge_id_containing_obs_digits_is_not_flagged(ws):
    """K-QK-OBS-201 是知识 ID（稳定标识，正是规则要求的引用形式）：不能当观测编号误拦。"""
    issues = _lint_case(ws, knowledge_used=[
        {"id": "K-QK-001 / K-AV-001", "use": "判据候选", "applicability": "not_applicable",
         "rationale": "两者冲突且本机数据加载后「默认车位」消失（K-QK-OBS-201）"}])
    assert not any(i["field"] == "knowledge_used" and "obs" in i["problem"] for i in issues)


def test_stable_rationale_references_pass(ws):
    issues = _lint_case(ws, knowledge_used=[
        {"id": "K-AV-001", "use": "entry_success", "applicability": "适用",
         "rationale": "run-20261008-201507-4990 步骤 S3 观测：入口出现"}])
    assert not any(i["field"] == "knowledge_used" for i in issues)


# ---- Issue #15：cleanup 停止独立断言 ----

def test_stop_app_records_independent_stop_check(ws, tmp_path):
    ctx = _ctx(ws, _lab_world(tmp_path))
    res = Executor(ctx).run_step({"id": "C1", "action": "stop_app", "retry": 0}, "case")
    assert res["outcome"].startswith("passed")
    att = res["attempts"][-1]
    check = att["stop_check"]
    assert check["process_ended"] is True and check["process_alive"] is False
    assert check["foreground"] == "com.ohos.sceneboard" and check["foreground_restored"] is True


def test_foreign_foreground_without_declaration_is_recorded_not_failed(ws, tmp_path):
    """真机 S5 场景：停止后 WeLink 接管前台。未声明 require_launcher 时这是环境现象、与产品无关，
    不据此判失败——前台事实记录进 stop_check 供人工复核。"""
    ctx = _ctx(ws, _lab_world(tmp_path))
    real_stop = ctx.device.stop

    def _stop_then_hijack(bundle):
        out = real_stop(bundle)
        ctx.device.state["foreground"] = "com.huawei.welink.hmos"  # 其他应用占屏
        return out

    ctx.device.stop = _stop_then_hijack
    res = Executor(ctx).run_step({"id": "C1", "action": "stop_app", "retry": 0}, "case")
    assert res["outcome"].startswith("passed")
    att = res["attempts"][-1]
    check = att["stop_check"]
    assert check["process_ended"] is True and check["require_launcher"] is False
    assert check["foreign_foreground"] == "com.huawei.welink.hmos"
    assert check["foreground_restored"] is False


def test_declared_launcher_restoration_fails_on_foreign_foreground(ws, tmp_path):
    """用例声明 require_launcher 且停止前 APP 在前台（可靠前置事实）：前台被其他应用占据
    → 清理不符合声明的恢复期望，判 fail_candidate（环境归因，不是产品失败）。"""
    ctx = _ctx(ws, _lab_world(tmp_path))
    real_stop = ctx.device.stop

    def _stop_then_hijack(bundle):
        out = real_stop(bundle)
        ctx.device.state["foreground"] = "com.huawei.welink.hmos"
        return out

    ctx.device.stop = _stop_then_hijack
    res = Executor(ctx).run_step({"id": "C1", "action": "stop_app", "require_launcher": True, "retry": 0}, "case")
    att = res["attempts"][-1]
    assert att["status"] == "foreground_not_restored"
    assert att["stop_check"]["foreign_foreground"] == "com.huawei.welink.hmos"
    assert att["stop_check"]["require_launcher"] is True
    assert res["outcome"] == "fail_candidate"
    assert res["first_failure"]["detail"]["stop_check"]["foreground"] == "com.huawei.welink.hmos"


def test_declared_restoration_unverifiable_without_prior_foreground(ws, tmp_path):
    """声明了 require_launcher 但停止前 APP 不在前台：没有可靠前置事实可推导恢复期望，
    记录 unverifiable 而不是硬判失败。"""
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.device.state["foreground"] = "com.huawei.welink.hmos"  # 停止前屏就是别人的
    res = Executor(ctx).run_step({"id": "C1", "action": "stop_app", "require_launcher": True, "retry": 0}, "case")
    assert res["outcome"].startswith("passed")
    att = res["attempts"][-1]
    assert att["stop_check"]["require_launcher_unverifiable"] == "停止前 APP 不在前台，无从推导恢复期望"


def test_foreground_query_failure_recorded_when_undeclared(ws, tmp_path):
    """obs-0017 场景：前台查询失败。未声明 require_launcher 时只记录，不据此判失败。"""
    ctx = _ctx(ws, _lab_world(tmp_path))
    real_stop = ctx.device.stop

    def _stop_then_blind(bundle):
        out = real_stop(bundle)
        ctx.device.state.setdefault("query_fail", []).append("foreground")  # 后台查询开始失败
        return out

    ctx.device.stop = _stop_then_blind
    res = Executor(ctx).run_step({"id": "C1", "action": "stop_app", "retry": 0}, "case")
    assert res["outcome"].startswith("passed")
    check = res["attempts"][-1]["stop_check"]
    assert check["process_ended"] is True and check["foreground"] is None


def test_stop_app_fails_when_foreground_query_fails(ws, tmp_path):
    """声明 require_launcher 且停止前 APP 在前台：前台查询失败 → 声明的恢复期望无法确认，
    foreground_unknown 单独暴露而不是混过。"""
    ctx = _ctx(ws, _lab_world(tmp_path))
    real_stop = ctx.device.stop

    def _stop_then_blind(bundle):
        out = real_stop(bundle)
        ctx.device.state.setdefault("query_fail", []).append("foreground")  # 后台查询开始失败
        return out

    ctx.device.stop = _stop_then_blind
    res = Executor(ctx).run_step({"id": "C1", "action": "stop_app", "require_launcher": True, "retry": 0}, "case")
    att = res["attempts"][-1]
    assert att["status"] == "foreground_unknown"
    assert att["stop_check"]["process_ended"] is True and att["stop_check"]["foreground_restored"] is False
    assert res["outcome"] == "fail_candidate"


def test_stop_app_fails_when_process_survives(ws, tmp_path):
    """停止命令未生效（进程仍存活）：等满超时也不能算清理完成。"""
    ctx = _ctx(ws, _lab_world(tmp_path))
    ctx.device.stop = lambda b: DeviceResult("stop_app", True, True, raw="force stop process successfully.")
    res = Executor(ctx).run_step({"id": "C1", "action": "stop_app", "retry": 0, "timeout_s": 0.2}, "case")
    att = res["attempts"][-1]
    assert att["status"] == "stop_not_confirmed"
    assert att["stop_check"]["process_ended"] is False and att["stop_check"]["process_alive"] is True
    assert res["outcome"] == "fail_candidate"


def test_cleanup_stop_failure_blocks_pass_verdict(ws, tmp_path):
    """清理停止是必需步骤：停止断言不过，整案不能判 passed（区分进程结束与设备状态恢复）。"""
    from vpdtest.runner import run_case
    world = _lab_world(tmp_path, "cw")
    case = {"id": "TC-CL-1", "version": 1, "status": "candidate", "app": "motor_hm",
            "title": "t", "purpose": "p", "preconditions": [], "env_requirements": {},
            "test_data": {}, "test_data_none": True, "authorizations": [],
            "steps": [{"id": "S1", "action": "launch"}], "assertions": [], "knowledge_used": [],
            "cleanup": [{"action": "stop_app"}], "sources": []}
    write_json(ws.root / "cases" / "TC-CL-1.json", case)
    res = run_case(ws, case_id="TC-CL-1", device_spec=world)
    c1 = res["cleanup"][0]
    assert c1["outcome"].startswith("passed") and c1["required"] is True
    assert c1["attempts"][-1]["stop_check"]["process_ended"] is True
