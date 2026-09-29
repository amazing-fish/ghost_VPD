"""目标驱动最小闭环的执行器保障（离线合成世界）：现场选择器诊断、执行前复核、前台确认、完整会话轨迹、
会话 → 候选用例、判定修正（未执行断言 / 适用范围 / 清理 / 人工接管）、用例成熟度与产品状态分开、知识检索。

这些测试只证明协议与判定逻辑；它们不说明乾崑/鸿蒙智行的真实产品行为。
"""
import json

import pytest

from conftest import confirm, world
from vpdtest import cases as C
from vpdtest.cli import HANDLERS, build_parser
from vpdtest.knowledge import KnowledgeStore, applicability
from vpdtest.runner import run_case
from vpdtest.search import search
from vpdtest.tree import UiTree
from vpdtest.util import append_jsonl, read_json, write_json
from vpdtest.verdict import case_verdict

BUNDLE = "app.huawei.hmos.motor"


def _lab_world(tmp_path, name="lab", extra=None) -> str:
    """一屏：两个「设置」（多匹配）、一个「唯一入口」、列表里一条被裁到可视区外、一条只露出一小截。"""
    screens = {
        "home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Button", "b": [40, 200, 640, 320], "click": True, "children": [{"t": "Text", "text": "设置", "b": [60, 220, 600, 300]}]},
            {"t": "Button", "b": [680, 200, 1280, 320], "click": True, "children": [{"t": "Text", "text": "设置", "b": [700, 220, 1260, 300]}]},
            {"t": "Button", "b": [40, 400, 1280, 520], "click": True, "go": "detail",
             "children": [{"t": "Text", "text": "唯一入口", "b": [60, 420, 1260, 500]}]},
            {"t": "List", "b": [0, 1000, 1320, 1600], "scroll": True, "children": [
                {"t": "Button", "b": [40, 1560, 1280, 1700], "click": True,
                 "children": [{"t": "Text", "text": "露一截", "b": [60, 1580, 1260, 1680]}]},
                {"t": "Button", "b": [40, 1800, 1280, 1920], "click": True,
                 "children": [{"t": "Text", "text": "看不见的行", "b": [60, 1820, 1260, 1900]}]}]}]}},
        "detail": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "详情页", "b": [60, 200, 600, 300]}]}},
    }
    screens.update(extra or {})
    w = {"bundle": BUNDLE, "app_version": "3.0.3.200-replay", "device": {"model": "LAB", "os": "replay"},
         "screen": [1320, 2848], "display": {"density": 3.375, "device_form": "bar"},
         "initial": {"foreground": BUNDLE, "installed": True}, "launch_screen": "home", "screens": screens}
    p = tmp_path / f"{name}.json"
    write_json(p, w)
    # 世界文件初始 alive=False：用 replay_state 让 APP 已在前台
    return f"replay:{p}"


def cli(ws, *argv):
    args = build_parser().parse_args(list(argv))
    return HANDLERS[args.cmd](ws, args)


def _start(ws, dev, session="s1"):
    return cli(ws, "act", "launch", "--app", "motor_hm", "--session", session, "--device", dev)


# ---- 现场选择器：唯一 / 多匹配 / 被裁剪 / 部分可见，都不盲点 ----

def test_select_diagnostics_never_blind_tap(ws, tmp_path):
    dev = _lab_world(tmp_path)
    _start(ws, dev)
    amb = cli(ws, "act", "tap", "--select", "text=设置", "--app", "motor_hm", "--session", "s1", "--device", dev)
    assert amb["result"]["outcome"] == "blocked" and amb["diagnosis"]["status"] == "target_ambiguous"
    assert len(amb["diagnosis"]["candidates"]) == 2 and "--within" in amb["diagnosis"]["hint"]
    assert not any(a.get("dispatch") for a in amb["result"]["attempts"])  # 没有下发
    hidden = cli(ws, "act", "tap", "--select", "text=看不见的行", "--app", "motor_hm", "--session", "s1", "--device", dev)
    assert hidden["diagnosis"]["status"] == "target_unresolved" and hidden["diagnosis"]["offscreen"] == 1
    part = cli(ws, "act", "tap", "--select", "text=露一截", "--app", "motor_hm", "--session", "s1", "--device", dev)
    assert part["diagnosis"]["status"] == "target_partially_visible" and part["diagnosis"]["visible_fraction"] < 0.5
    ok = cli(ws, "act", "tap", "--select", "text=唯一入口", "--expect-text", "详情页", "--app", "motor_hm",
             "--session", "s1", "--device", dev)
    att = ok["result"]["attempts"][-1]
    assert ok["result"]["outcome"] == "passed" and att["revalidate"]["result"] == "same"
    # 证据里记下解析出的节点、边界、实际点击点和屏幕形态（用例里不写坐标，证据里要有）
    r = att["resolved"]
    assert r["point"] == [660, 460] and r["node"]["text"] == "唯一入口" and r["window_px"] == [1320, 2848]
    assert r["layout_key"] == "sm/portrait" and r["device_form"] == "bar"


def test_stale_target_is_not_dispatched(ws, tmp_path):
    # 每被观测一次页面就在 flip / flop 之间切换：执行前观测与下发前复核看到的按钮位置不同
    btn = lambda y: {"t": "Button", "b": [40, y, 1280, y + 120], "click": True,  # noqa: E731
                     "children": [{"t": "Text", "text": "会动的按钮", "b": [60, y + 20, 1260, y + 100]}]}
    extra = {"flip": {"after_obs": {"n": 1, "go": "flop", "replace": True},
                      "root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [btn(800)]}},
             "flop": {"after_obs": {"n": 1, "go": "flip", "replace": True},
                      "root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [btn(1600)]}}}
    dev = _lab_world(tmp_path, "flip", extra)
    w = read_json(dev.split(":", 1)[1])
    w["launch_screen"] = "flip"
    write_json(dev.split(":", 1)[1], w)
    _start(ws, dev)
    out = cli(ws, "act", "tap", "--select", "text=会动的按钮", "--app", "motor_hm", "--session", "s1", "--device", dev)
    att = out["result"]["attempts"][-1]
    assert att["status"] == "target_stale" and att["revalidate"]["result"] == "moved"
    assert "dispatch" not in att and out["diagnosis"]["status"] == "target_stale"


def test_foreground_change_before_dispatch_blocks(ws, tmp_path):
    from vpdtest.execute import Executor
    from vpdtest.experience import ExperienceStore
    from vpdtest.observe import RunStore
    from vpdtest.session import parse_select
    from vpdtest.workspace import RunContext
    dev_spec = _lab_world(tmp_path)
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir, "session-fg", kind="session")
    dev = ws.device(dev_spec, state_path=store.dir / "replay_state.json")
    dev.launch(BUNDLE, app.ability)
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    real = ctx.observe

    def observe(label="", screenshot=True):  # 复核时用户切到了别的应用
        out = real(label, screenshot)
        if "predispatch" in label:
            dev.state["foreground"] = "com.tencent.wechat"
        return out
    ctx.observe = observe
    res = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0, "revalidate": True,
                                  "target": parse_select("text=唯一入口")}, "session")
    att = res["attempts"][-1]
    assert att["status"] == "foreground_changed" and "dispatch" not in att
    assert att["foreground_check"]["foreground"] == "com.tencent.wechat" and res["attribution"] == "environment"


# ---- 原始会话 → 候选用例：失败轨迹保留，排除要写理由，中断/效果未知不静默消失 ----

def test_session_to_candidate_keeps_failure_trail(ws, tmp_path):
    dev = _lab_world(tmp_path)
    _start(ws, dev)
    cli(ws, "observe", "--app", "motor_hm", "--session", "s1", "--device", dev)
    cli(ws, "act", "tap", "--select", "text=设置", "--app", "motor_hm", "--session", "s1", "--device", dev)  # A2 多匹配
    cli(ws, "act", "tap", "--select", "text=唯一入口", "--expect-text", "详情页", "--app", "motor_hm",
        "--session", "s1", "--device", dev)  # A3
    with pytest.raises(ValueError):  # 未写 --target/--select/--point：记为被拒，不执行
        cli(ws, "act", "tap", "--app", "motor_hm", "--session", "s1", "--device", dev)
    sdir = ws.runs_dir / "session-s1"
    # 模拟一次进程中断（有请求没结果）和一次命令已发出但执行器报错（现场可能已变化）
    append_jsonl(sdir / "events.jsonl", {"ts": "t", "event": "act_request", "seq": 5, "step_id": "A5",
                                         "request": {"action": "back"}})
    trail = cli(ws, "session", "s1")["trail"]
    kinds = [(r.get("step"), r["kind"], r.get("outcome")) for r in trail if r["kind"] != "observe"]
    assert kinds == [("A1", "act", "passed"), ("A2", "act", "blocked"), ("A3", "act", "passed"),
                     ("A4", "rejected", None), ("A5", "incomplete", None)]
    with pytest.raises(ValueError, match="理由"):
        cli(ws, "case", "from-session", "--session", "s1", "--new-id", "TC-S-1", "--app", "motor_hm", "--drop", "A2")
    out = cli(ws, "case", "from-session", "--session", "s1", "--new-id", "TC-S-1", "--app", "motor_hm",
              "--goal", "从首页进入详情", "--drop", "A2=多匹配的试探，改用唯一入口")
    case = ws.load_case("TC-S-1")
    src = case["sources"][0]
    assert [s["from_session"]["seq"] for s in case["steps"]] == [1, 3, 5]  # 中断的 A5 默认保留
    assert src["excluded"][0]["seq"] == 2 and src["excluded"][0]["outcome"] == "blocked"
    assert src["rejected"][0]["seq"] == 4 and src["incomplete"][0]["seq"] == 5
    s3 = case["steps"][1]
    assert s3["target"]["strategies"] == [{"kind": "text", "value": "唯一入口"}] and s3["expect_text"] == "详情页"
    assert s3["target"]["observed"]["node"]["text"] == "唯一入口" and s3["revalidate"] is True
    assert s3["from_session"]["obs_before"] and "point" not in s3  # 用例不写坐标
    assert case["goal"] == "从首页进入详情" and src["facts"]["validation_level"] == "offline_replay"
    fields = {i["field"] for i in out["issues"]}
    assert {"purpose", "assertions", "cleanup", "knowledge_used"} <= fields
    assert "steps.S3" not in fields  # 解析成功并下发过的选择器可以留在用例里
    # 不排除多匹配的 A2：它的选择器从没解析成功，lint 拦下，不能靠"有 observed 字段"混过去
    kept = cli(ws, "case", "from-session", "--session", "s1", "--new-id", "TC-S-2", "--app", "motor_hm")
    s2 = next(i for i in kept["issues"] if i["field"] == "steps.S2")
    assert "没有解析成功" in s2["problem"]


def _input_world(tmp_path):
    # text_bound：输入后布局树里显示输入值（真机输入框也是这样），用来检查落盘前脱敏
    box = {"t": "TextInput", "b": [40, 800, 1280, 920], "click": True, "bind": "name", "text_bound": "name"}
    return _lab_world(tmp_path, "inp", {"home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Text", "text": "名称", "b": [40, 700, 600, 780]}, box]}}})


def test_session_never_writes_rejected_or_secret_input_text(ws, tmp_path, monkeypatch):
    dev = _input_world(tmp_path)
    _start(ws, dev, "i1")
    monkeypatch.setenv("VPD_SECRET_FAKE_PW", "Fake#Pass9")
    base = ["act", "input", "--select", "xpath=//TextInput", "--app", "motor_hm", "--session", "i1", "--device", dev]
    cli(ws, *base, "--text", "Fake#Pass9")                        # 没有 --allow-input：policy 拒绝
    cli(ws, *base, "--allow-input", "--text", "Fake#Pass9")       # 允许输入，但等于已配置密钥：不留原文
    ok = cli(ws, *base, "--allow-input", "--text", "测试名A")      # 普通测试数据：保留原文供复测
    assert ok["input_value_recorded"] is True
    sdir = ws.runs_dir / "session-i1"
    disk = "".join(p.read_text("utf-8") for p in sdir.rglob("*") if p.suffix in (".json", ".jsonl"))
    assert "Fake#Pass9" not in disk and "测试名A" in disk
    # 输入后布局树里出现了密钥值：落盘前替换为 ***，该观测不截图
    red = [read_json(p) for p in (sdir / "obs").glob("*/observation.json") if read_json(p).get("redacted")]
    assert red and all(o["screenshot"]["status"] == "withheld" for o in red)
    case = __import__("vpdtest.session", fromlist=["from_session"]).from_session(
        sdir, "TC-I-1", ws.app("motor_hm"), None, ["A1=启动", "A2=被拒的试探"])
    assert case["test_data"]["input_3"] == {"missing_from_session": True, "length": 10}
    assert case["test_data"]["input_4"] == "测试名A"
    assert any(i["field"] == "test_data.input_3" for i in C.lint(case, ws.app("motor_hm"), KnowledgeStore(ws.root)))


def test_declared_env_cannot_override_measured_facts(ws):
    from vpdtest.runner import run_facts
    f = run_facts(ws.app("motor_hm"), {"app_version": "3.0.4.100", "display": {"device_form": "bar"}},
                  {"app_version": "3.0.3.200", "device_form": "foldable", "account_state": "logged_in"})
    assert f["app_version"] == "3.0.4.100" and f["device_form"] == "bar" and f["account_state"] == "logged_in"
    assert run_facts(ws.app("motor_hm"), {}, {"app_version": "3.0.3.200"})["app_version"] == "3.0.3.200"  # 查不到时才用声明


def test_point_tap_is_converted_only_when_a_unique_selector_exists(ws, tmp_path):
    dev = _lab_world(tmp_path)
    _start(ws, dev, "p1")
    cli(ws, "observe", "--app", "motor_hm", "--session", "p1", "--device", dev)
    cli(ws, "act", "tap", "--point", "660,460", "--basis", "截图中的「唯一入口」", "--app", "motor_hm",
        "--session", "p1", "--device", dev)
    from vpdtest.session import from_session
    case = from_session(ws.runs_dir / "session-p1", "TC-P-1", ws.app("motor_hm"), None, [])
    tap = case["steps"][-1]
    assert tap["target"]["strategies"] == [{"kind": "text", "value": "唯一入口", "derived_from_point": True}]
    assert tap["point_basis"] and "point" not in tap


# ---- 判定修正 ----

def test_required_assertion_on_missing_step_is_not_pass(ws):
    confirm(ws, "K-MOT-001", "K-MOT-002")
    case = ws.load_case("TC-MOTOR-HM-001")
    case["assertions"].append({"id": "A9", "at": "S99", "type": "text_present", "text": "我的",
                               "desc": "拼错的步骤", "oracle_refs": ["K-MOT-001"]})
    run = run_case(ws, case=case, device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "blocked"
    assert any(f["id"] == "A9" and "不存在" in f["reason"] for f in run["verdict"]["findings"])
    assert any(i["field"] == "assertions.A9" for i in C.lint(case, ws.app("motor_hm"), KnowledgeStore(ws.root)))


def test_not_run_assertions_are_listed_as_not_verified(ws):
    confirm(ws, "K-MOT-001", "K-MOT-002")
    case = ws.load_case("TC-MOTOR-HM-001")
    case["steps"][1]["target"] = {"label": "不存在", "strategies": [{"kind": "text", "value": "不存在的入口"}]}
    run = run_case(ws, case=case, device_spec=world("motor_hm_base.json"))
    v = run["verdict"]
    assert v["verdict"] == "blocked" and v["not_verified"]  # 后续断言没执行：列为"尚未验证"，不推断成立
    assert "尚未验证" in (ws.runs_dir / run["run_id"] / "report.md").read_text("utf-8")


def test_applicability_unknown_or_out_of_scope_is_never_confirmed(ws):
    confirm(ws, "K-MOT-001")
    kn = KnowledgeStore(ws.root)
    assert kn.oracle_status(["K-MOT-001"], facts={"app": "motor_hm", "app_version": "3.0.3.200"})["status"] == "confirmed"
    assert kn.oracle_status(["K-MOT-001"], facts={"app": "motor_hm"})["status"] == "applicability_unknown"
    assert kn.oracle_status(["K-MOT-001"], facts={"app": "motor_hm", "app_version": "3.0.4.100"})["status"] == "out_of_scope"
    assert kn.oracle_status(["K-MOT-001"])["status"] == "applicability_unknown"  # 没有运行上下文时不默认适用
    debug = {"applies_to": {"app": "qiankun_hm", "app_version": "内部debug版 phone_fc78649"}}
    assert applicability(debug, {"app": "qiankun_hm", "app_version": "1.0.26.100"})["status"] == "unknown"
    # 版本漂移的真机/世界：已确认但范围不符 → 待确认，不是通过
    confirm(ws, "K-MOT-002")
    run = run_case(ws, "TC-MOTOR-HM-001", device_spec=world("motor_hm_drift.json"))
    assert run["verdict"]["verdict"] == "pending"
    assert any(i["status"] == "out_of_scope" for i in run["knowledge_snapshot"])


def test_required_cleanup_failure_blocks_pass(ws):
    confirm(ws, "K-MOT-001", "K-MOT-002")
    case = ws.load_case("TC-MOTOR-HM-001")
    case["cleanup"] = [{"id": "C1", "action": "tap", "target": {"label": "x", "strategies": [
        {"kind": "text", "value": "不存在的清理按钮"}]}}]
    run = run_case(ws, case=case, device_spec=world("motor_hm_base.json"))
    assert all(a["result"] == "pass" for a in run["assertions"] if not a["advisory"])
    assert run["verdict"]["verdict"] == "blocked"
    assert any(f["kind"] == "cleanup" for f in run["verdict"]["findings"])
    assert "测试数据可能未恢复" in (ws.runs_dir / run["run_id"] / "report.md").read_text("utf-8")
    case["cleanup"][0]["required"] = False  # 显式声明非必需的清理：只记录
    assert run_case(ws, case=case, device_spec=world("motor_hm_base.json"))["verdict"]["verdict"] == "passed"


def test_failure_signature_distinguishes_failure_modes(ws):
    kn = KnowledgeStore(ws.root)
    confirm(ws, "K-GEN-001")

    def sig(status):
        steps = [{"step_id": "S2", "outcome": "fail_candidate", "final_status": status, "reason": "x",
                  "critical": True, "oracle_refs": ["K-GEN-001"]}]
        return case_verdict({"ok": True}, {"ok": True}, steps, [], kn)["failure_signature"]
    assert sig("anomaly") != sig("postcondition_not_met")  # 同一步骤"崩溃"与"没到预期页面"不是同一个缺陷
    assert sig("anomaly") == sig("anomaly")


def test_unconfirmed_safe_stop_outranks_product_failure(ws):
    kn = KnowledgeStore(ws.root)
    confirm(ws, "K-GEN-001")
    steps = [{"step_id": "S1", "outcome": "fail_candidate", "final_status": "anomaly", "reason": "崩溃",
              "critical": True}]
    v = case_verdict({"ok": True}, {"ok": True}, steps, [], kn,
                     [{"trigger": "critical_step_failed", "confirmed_stopped": False, "action_required": "人工接管"}])
    assert v["verdict"] == "blocked" and v["action_required"] == "人工接管"
    assert v["product_failed"] is True  # 产品失败没有丢
    assert any(f["result"] == "failed" for f in v["findings"])


# ---- 用例成熟度与产品状态分开 ----

def _candidate(ws, cid, src="TC-MOTOR-HM-003"):
    case = ws.load_case(src)
    case.update(id=cid, status="candidate")
    write_json(ws.case_path(cid), case)
    return case


def test_stable_known_failure_can_mature_without_changing_expectation(ws):
    confirm(ws, "K-MOT-003")
    _candidate(ws, "TC-KF-1")
    kn = KnowledgeStore(ws.root)
    for _ in range(2):
        run = run_case(ws, "TC-KF-1", device_spec=world("motor_hm_typo.json"))
        assert run["verdict"]["verdict"] == "failed"
        C.record_replay(ws, "TC-KF-1", run)
    with pytest.raises(PermissionError, match="known-issue"):
        C.promote(ws, "TC-KF-1", "replay_verified", kn)
    case = C.promote(ws, "TC-KF-1", "replay_verified", kn, known_issue="DTS-EXAMPLE-1")
    assert case["product_status"]["status"] == "known_failure" and case["product_status"]["signature"]
    assert case["assertions"] == ws.load_case("TC-MOTOR-HM-003")["assertions"]  # 预期没有被改
    again = run_case(ws, "TC-KF-1", device_spec=world("motor_hm_typo.json"))
    assert again["verdict"]["verdict"] == "failed" and again["verdict"]["known_issue"]["reproduced"] is True


def test_mixed_replays_are_not_stable(ws):
    confirm(ws, "K-MOT-003")
    _candidate(ws, "TC-KF-2")
    kn = KnowledgeStore(ws.root)
    C.record_replay(ws, "TC-KF-2", run_case(ws, "TC-KF-2", device_spec=world("motor_hm_typo.json")))
    C.record_replay(ws, "TC-KF-2", run_case(ws, "TC-KF-2", device_spec=world("motor_hm_base.json")))
    with pytest.raises(PermissionError, match="不一致"):
        C.promote(ws, "TC-KF-2", "replay_verified", kn, known_issue="DTS-EXAMPLE-2")


def test_authorization_change_invalidates_replays(ws):
    case = ws.load_case("TC-QK-102")
    h = C.content_hash(case)
    case["authorizations"] = case["authorizations"] + ["vehicle_motion"]
    assert C.content_hash(case) != h


# ---- 可见性：被列表裁掉的文字不能证明"出现了" ----

def test_clipped_text_is_not_visible_evidence():
    raw = {"attributes": {"type": "root", "bounds": "[0,0][1000,2000]"}, "children": [
        {"attributes": {"type": "List", "bounds": "[0,0][1000,500]", "scrollable": "true"}, "children": [
            {"attributes": {"type": "Text", "text": "新名称", "bounds": "[0,900][1000,1000]"}, "children": []}]}]}
    t = UiTree(raw)
    assert t.find_text("新名称") == [] and "新名称" not in t.visible_texts()
    assert len(t.find_text("新名称", include_clipped=True)) == 1  # 定位仍知道它在可视区外（可滑动查找）


# ---- 知识检索：区分产品要求 / 操作经验 / 现场观察，适用范围未知就写未知 ----

def test_knowledge_search_roles_and_applicability(ws):
    out = search(ws, "收藏 重命名", "qiankun_hm", facts={"app_version": "1.0.26.100", "device_form": "bar"})
    by_ref = {r["ref"]: r for rows in out["results"].values() for r in rows}
    assert by_ref["K-AV-010"]["role"] == "observation" and by_ref["K-AV-010"]["applicability"] == "unknown"
    assert by_ref["K-QK-USR-003"]["role"] == "requirement" and by_ref["K-QK-USR-003"]["can_be_oracle"] is False
    assert any(r.startswith("target:") and by_ref[r]["callable"].startswith("act tap --target") for r in by_ref)
    assert out["usable_oracles"] == [] and out["gaps"]
    # 标为 product_rule 但来源是单次观察：如实归为现场观察
    k12 = search(ws, "K-AV-012", "qiankun_hm")["results"]["observation"][0]
    assert k12["ref"] == "K-AV-012" and "现场观察" in k12["note"]
