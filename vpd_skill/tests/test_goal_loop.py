"""目标驱动最小闭环的执行器保障（离线合成世界）：现场选择器诊断、执行前复核、前台确认、完整会话轨迹、
会话 → 候选用例、判定修正（未执行断言 / 适用范围 / 清理 / 人工接管）、用例成熟度与产品状态分开、知识检索。

这些测试只证明协议与判定逻辑；它们不说明乾崑/鸿蒙智行的真实产品行为。
"""
import json

import pytest

from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE, NAV_CASE, NAV_RULE, SETTINGS_RULE
from vpdtest import cases as C
from vpdtest.cli import HANDLERS, build_parser
from vpdtest.knowledge import KnowledgeStore, applicability
from vpdtest.runner import run_case
from vpdtest.search import search
from vpdtest.tree import UiTree
from vpdtest.commands import REQUEST_FIELDS
from vpdtest.util import append_jsonl, read_json, read_jsonl, write_json
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
    # text_bound：输入后布局树显示输入值，用来检查原文取证
    box = {"t": "TextInput", "b": [40, 800, 1280, 920], "click": True, "bind": "name", "text_bound": "name"}
    return _lab_world(tmp_path, "inp", {"home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Text", "text": "名称", "b": [40, 700, 600, 780]}, box]}}})


def test_session_keeps_raw_evidence_but_externalizes_generated_secret_input(ws, tmp_path, monkeypatch):
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
    assert "Fake#Pass9" in disk and "测试名A" in disk
    observed = [read_json(p) for p in (sdir / "obs").glob("*/observation.json")]
    raw = [o for o in observed if "Fake#Pass9" in str(o.get("texts"))]
    assert raw and all(o["screenshot"]["status"] == "ok" for o in raw)
    assert not any("redacted" in o for o in observed)
    case = __import__("vpdtest.session", fromlist=["from_session"]).from_session(
        sdir, "TC-I-1", ws.app("motor_hm"), None, ["A1=启动", "A2=被拒的试探"])
    assert case["test_data"]["input_3"] == {"missing_from_session": True, "length": 10}
    assert case["test_data"]["input_4"] == "测试名A"
    assert any(i["field"] == "test_data.input_3" for i in C.lint(case, ws.app("motor_hm"), KnowledgeStore(ws.root)))


def test_partial_secret_is_classified_only_for_generated_case_data(monkeypatch):
    from vpdtest.params import Params
    monkeypatch.setenv("VPD_SECRET_T_PW", "Abc123456xyz")
    p = Params({"pw": {"secret": "T_PW"}}, None, "run-x")
    assert p.is_sensitive("框里是 Abc12345 后面")
    assert p.is_sensitive("456xyz") and not p.is_sensitive("Abc12")
    assert not hasattr(p, "redact")


def test_declared_env_cannot_override_measured_facts(ws):
    from vpdtest.runner import run_facts
    f = run_facts(ws.app("motor_hm"), {"app_version": "3.0.4.100", "display": {"device_form": "bar"}},
                  {"app_version": "3.0.3.200", "device_form": "foldable", "account_state": "logged_in"})
    assert f["app_version"] == "3.0.4.100" and f["device_form"] == "bar" and f["account_state"] == "logged_in"
    assert run_facts(ws.app("motor_hm"), {}, {"app_version": "3.0.3.200"})["app_version"] is None  # 未实测不靠声明制造适用


def test_point_tap_is_converted_only_when_a_unique_selector_exists(ws, tmp_path):
    dev = _lab_world(tmp_path)
    _start(ws, dev, "p1")
    cli(ws, "observe", "--app", "motor_hm", "--session", "p1", "--device", dev)
    cli(ws, "act", "tap", "--point", "660,460", "--basis", "截图中的「唯一入口」", "--app", "motor_hm",
        "--session", "p1", "--device", dev)
    from vpdtest.session import from_session
    case = from_session(ws.runs_dir / "session-p1", "TC-P-1", ws.app("motor_hm"), None, [])
    assert case["origin"] == {"type": "session"}
    tap = case["steps"][-1]
    assert tap["target"]["strategies"] == [{"kind": "text", "value": "唯一入口", "derived_from_point": True}]
    assert tap["point_basis"] and "point" not in tap
    # 反推用到的节点就是证据：lint 不因"坐标点击没有解析节点"而拦下，但证据标明是反推的
    obs = tap["target"]["observed"]
    assert obs["node"]["text"] == "唯一入口" and obs["node_basis"] == "derived_from_point" and obs["dispatched"]
    assert not any(i["field"] == tap["id"] or i["field"] == f"steps.{tap['id']}"
                   for i in C.lint(case, ws.app("motor_hm"), KnowledgeStore(ws.root)))


def test_session_identity_is_fixed_at_open(ws, tmp_path):
    dev, other = _lab_world(tmp_path), _lab_world(tmp_path, "other")
    _start(ws, dev, "id1")
    head = [e for e in read_jsonl(ws.runs_dir / "session-id1" / "events.jsonl") if e["event"] == "session_open"]
    assert len(head) == 1 and head[0]["identity"]["device"].endswith("lab.json")
    with pytest.raises(PermissionError, match="不一致"):
        cli(ws, "observe", "--app", "motor_hm", "--session", "id1", "--device", other)
    with pytest.raises(PermissionError, match="不一致"):
        cli(ws, "act", "back", "--app", "qiankun_hm", "--session", "id1", "--device", dev)
    events = read_jsonl(ws.runs_dir / "session-id1" / "events.jsonl")
    assert [e["event"] for e in events].count("session_mismatch_rejected") == 2
    assert not [e for e in events if e["event"] == "act_request" and e["seq"] > 1]  # 被拒的调用没有写入动作


def test_act_request_is_allowlisted_and_has_no_input_digest(ws, tmp_path):
    dev = _input_world(tmp_path)
    _start(ws, dev, "al1")
    cli(ws, "act", "input", "--select", "xpath=//TextInput", "--text", "123456", "--app", "motor_hm",
        "--session", "al1", "--device", dev)  # 未 --allow-input：被 policy 拒绝
    req = [e["request"] for e in read_jsonl(ws.runs_dir / "session-al1" / "events.jsonl")
           if e["event"] == "act_request"][-1]
    assert set(req) <= set(REQUEST_FIELDS) and req["text_len"] == 6
    assert not any("123456" in json.dumps(v) or len(str(v)) == 10 and k.endswith("sha") for k, v in req.items())
    disk = "".join(p.read_text("utf-8") for p in (ws.runs_dir / "session-al1").rglob("*.json*"))
    assert "123456" not in disk


# ---- 判定修正 ----

def test_required_assertion_on_missing_step_is_not_pass(verdict_ws):
    confirm(verdict_ws, NAV_RULE, SETTINGS_RULE)
    case = verdict_ws.load_case(NAV_CASE)
    case["assertions"].append({"id": "A9", "at": "S99", "type": "text_present", "text": "我的",
                               "desc": "拼错的步骤", "oracle_refs": [NAV_RULE]})
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"))
    assert run["verdict"]["verdict"] == "error"
    assert any(f["id"] == "A9" and "不存在" in f["reason"] for f in run["verdict"]["findings"])
    assert any(i["field"] == "assertions.A9" for i in C.lint(case, verdict_ws.app("motor_hm"), KnowledgeStore(verdict_ws.root)))


def test_not_run_assertions_are_listed_as_not_verified(verdict_ws):
    confirm(verdict_ws, NAV_RULE, SETTINGS_RULE)
    case = verdict_ws.load_case(NAV_CASE)
    case["steps"][1]["target"] = {"label": "不存在", "strategies": [{"kind": "text", "value": "不存在的入口"}]}
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"))
    v = run["verdict"]
    assert v["verdict"] == "blocked" and v["not_verified"]  # 后续断言没执行：列为"尚未验证"，不推断成立
    assert "尚未验证" in (verdict_ws.runs_dir / run["run_id"] / "report.md").read_text("utf-8")


def test_applicability_unknown_or_out_of_scope_is_never_confirmed(verdict_ws):
    confirm(verdict_ws, NAV_RULE)
    kn = KnowledgeStore(verdict_ws.root)
    assert kn.oracle_status([NAV_RULE], facts={"app": "motor_hm", "app_version": "3.0.3.200"})["status"] == "confirmed"
    assert kn.oracle_status([NAV_RULE], facts={"app": "motor_hm"})["status"] == "applicability_unknown"
    assert kn.oracle_status([NAV_RULE], facts={"app": "motor_hm", "app_version": "3.0.4.100"})["status"] == "out_of_scope"
    assert kn.oracle_status([NAV_RULE])["status"] == "applicability_unknown"  # 没有运行上下文时不默认适用
    debug = {"applies_to": {"app": "qiankun_hm", "app_version": "内部debug版 phone_fc78649"}}
    assert applicability(debug, {"app": "qiankun_hm", "app_version": "1.0.26.100"})["status"] == "unknown"
    # 版本漂移的真机/世界：已确认但范围不符 → 待确认，不是通过
    confirm(verdict_ws, SETTINGS_RULE)
    run = run_case(verdict_ws, NAV_CASE, device_spec=world("motor_hm_drift.json"))
    assert run["verdict"]["verdict"] == "pending"
    assert any(i["status"] == "out_of_scope" for i in run["knowledge_snapshot"])


def test_required_cleanup_failure_blocks_pass(verdict_ws):
    confirm(verdict_ws, NAV_RULE, SETTINGS_RULE)
    case = verdict_ws.load_case(NAV_CASE)
    case["cleanup"] = [{"id": "C1", "action": "tap", "target": {"label": "x", "strategies": [
        {"kind": "text", "value": "不存在的清理按钮"}]}}]
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"))
    assert all(a["result"] == "pass" for a in run["assertions"] if not a["advisory"])
    assert run["verdict"]["verdict"] == "blocked"
    assert any(f["kind"] == "cleanup" for f in run["verdict"]["findings"])
    assert "测试数据可能未恢复" in (verdict_ws.runs_dir / run["run_id"] / "report.md").read_text("utf-8")
    case["cleanup"][0]["required"] = False  # 显式声明非必需的清理：只记录
    assert run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"))["verdict"]["verdict"] == "passed"


def test_failure_signature_distinguishes_failure_modes(verdict_ws):
    kn = KnowledgeStore(verdict_ws.root).bind({"app": "motor_hm"})

    def sig(status):
        steps = [{"step_id": "S2", "outcome": "fail_candidate", "final_status": status, "reason": "x",
                  "critical": True, "oracle_refs": ["K-GEN-001"]}]
        return case_verdict({"ok": True}, {"ok": True}, steps, [], kn)["failure_signature"]
    assert sig("anomaly") != sig("postcondition_not_met")  # 同一步骤"崩溃"与"没到预期页面"不是同一个缺陷
    assert sig("anomaly") == sig("anomaly")


def test_unconfirmed_safe_stop_outranks_product_failure(verdict_ws):
    kn = KnowledgeStore(verdict_ws.root).bind({"app": "motor_hm"})
    steps = [{"step_id": "S1", "outcome": "fail_candidate", "final_status": "anomaly", "reason": "崩溃",
              "critical": True}]
    v = case_verdict({"ok": True}, {"ok": True}, steps, [], kn,
                     [{"trigger": "critical_step_failed", "confirmed_stopped": False, "action_required": "人工接管"}])
    assert v["verdict"] == "blocked" and v["action_required"] == "人工接管"
    assert v["product_failed"] is True  # 产品失败没有丢
    assert any(f["result"] == "failed" for f in v["findings"])


# ---- 用例成熟度与产品状态分开 ----

def _candidate(ws, cid, src=COPY_CASE):
    case = ws.load_case(src)
    case.update(id=cid, status="candidate")
    write_json(ws.case_path(cid), case)
    return case


def test_stable_known_failure_can_mature_without_changing_expectation(verdict_ws):
    confirm(verdict_ws, COPY_RULE)
    _candidate(verdict_ws, "TC-KF-1")
    kn = KnowledgeStore(verdict_ws.root)
    for _ in range(2):
        run = run_case(verdict_ws, "TC-KF-1", device_spec=world("motor_hm_typo.json"))
        assert run["verdict"]["verdict"] == "failed"
        C.record_replay(verdict_ws, "TC-KF-1", run)
    with pytest.raises(PermissionError, match="known-issue"):
        C.promote(verdict_ws, "TC-KF-1", "replay_verified", kn)
    case = C.promote(verdict_ws, "TC-KF-1", "replay_verified", kn, known_issue="DTS-EXAMPLE-1")
    assert case["product_status"]["status"] == "known_failure" and case["product_status"]["signature"]
    assert case["assertions"] == verdict_ws.load_case(COPY_CASE)["assertions"]  # 预期没有被改
    again = run_case(verdict_ws, "TC-KF-1", device_spec=world("motor_hm_typo.json"))
    assert again["verdict"]["verdict"] == "failed" and again["verdict"]["known_issue"]["reproduced"] is True


def test_mixed_replays_are_not_stable(verdict_ws):
    confirm(verdict_ws, COPY_RULE)
    _candidate(verdict_ws, "TC-KF-2")
    kn = KnowledgeStore(verdict_ws.root)
    C.record_replay(verdict_ws, "TC-KF-2", run_case(verdict_ws, "TC-KF-2", device_spec=world("motor_hm_typo.json")))
    C.record_replay(verdict_ws, "TC-KF-2", run_case(verdict_ws, "TC-KF-2", device_spec=world("motor_hm_base.json")))
    with pytest.raises(PermissionError, match="不一致"):
        C.promote(verdict_ws, "TC-KF-2", "replay_verified", kn, known_issue="DTS-EXAMPLE-2")


def _replays(*verdicts) -> dict:
    case = {"app": "motor_hm", "steps": [], "assertions": []}
    h = C.content_hash(case)
    case["replays"] = [{"run_id": f"run-{i}", "verdict": v, "hash": h, "failure_signature": []}
                       for i, v in enumerate(verdicts)]
    return case


def test_indeterminate_replay_after_passes_blocks_promotion():
    # 两次通过之后又出现阻塞/待确认：现在还不能稳定复测，之前的通过不再作数
    for tail in ("blocked", "pending"):
        b = C.replay_basis(_replays("passed", "passed", tail))
        assert b["ok"] is False and "最近一次不确定" in b["why"], b
    # 判据确认之前的待确认运行不永久挡住晋级：之后重新攒够 2 次确定结论即可
    b = C.replay_basis(_replays("pending", "passed", "passed"))
    assert b["ok"] is True and b["replays"] == ["run-1", "run-2"] and b["ignored"][0]["run_id"] == "run-0"
    # 更早的确定结论与之后的不一致：不稳定
    b = C.replay_basis(_replays("failed", "blocked", "passed", "passed"))
    assert b["ok"] is False and "不一致" in b["why"]


def test_conflicts_only_between_overlapping_scopes(ws):
    p = ws.root / "knowledge" / "items.json"
    data = read_json(p)
    base = {"kind": "requirement", "status": "confirmed", "subject": "motor_hm.scope.demo", "statement": "演示",
            "source": {"type": "reviewer", "ref": "pytest"}}
    data["items"] += [{**base, "id": "K-T-V1", "applies_to": {"app": "motor_hm", "app_version": "1.0.0.100"},
                       "expected": {"label": "旧"}},
                      {**base, "id": "K-T-V2", "applies_to": {"app": "motor_hm", "app_version": ["2.0.0.100", "2.0.1.100"]},
                       "expected": {"label": "新"}},
                      {**base, "id": "K-T-ANY", "applies_to": {"app": "qiankun_hm"}, "expected": {"label": "别的APP"}}]
    write_json(p, data)
    kn = KnowledgeStore(ws.root)
    # 按版本分别规定的期望不是冲突；不同 APP 的同主题条目也不是
    assert "motor_hm.scope.demo" not in kn.conflicts()
    assert kn.oracle_status(["K-T-V2"], {"app": "motor_hm", "app_version": "2.0.1.100"})["status"] == "confirmed"
    assert kn.oracle_status(["K-T-V1"], {"app": "motor_hm", "app_version": "2.0.1.100"})["status"] == "out_of_scope"
    # 范围有交集（未限定版本的条目覆盖所有版本）才是冲突
    data["items"].append({**base, "id": "K-T-ALL", "applies_to": {"app": "motor_hm"}, "expected": {"label": "通用"}})
    write_json(p, data)
    assert set(kn.conflicts()["motor_hm.scope.demo"]) == {"K-T-V1", "K-T-V2", "K-T-ALL"}
    assert kn.oracle_status(["K-T-V2"], {"app": "motor_hm", "app_version": "2.0.1.100"})["status"] == "conflicted"


def test_authorization_change_invalidates_replays(verdict_ws):
    case = verdict_ws.load_case(NAV_CASE)
    case["authorizations"] = ["user_data"]
    h = C.content_hash(case)
    case["authorizations"] = case["authorizations"] + ["vehicle_motion"]
    assert C.content_hash(case) != h


# ---- 可见性：被列表裁掉的文字不能证明"出现了" ----

def test_overlay_sheet_wins_over_deeper_page_node():
    # 真机：登录面板盖在「我的」页上，页面里更深的「收藏」节点与密码框重叠；点击实际落在面板上
    page = {"attributes": {"type": "Column", "bounds": "[0,0][1000,2000]"}, "children": [
        {"attributes": {"type": "Column", "bounds": "[0,800][1000,1000]"}, "children": [
            {"attributes": {"type": "Row", "bounds": "[0,800][1000,1000]"}, "children": [
                {"attributes": {"type": "Text", "text": "收藏", "bounds": "[0,800][1000,1000]"}, "children": []}]}]}]}
    sheet = {"attributes": {"type": "Column", "bounds": "[0,600][1000,2000]"}, "children": [
        {"attributes": {"type": "TextInput", "bounds": "[0,850][1000,950]", "clickable": "true"}, "children": []}]}
    t = UiTree({"attributes": {"type": "root", "bounds": "[0,0][1000,2000]"}, "children": [page, sheet]})
    assert t.node_at(500, 900).type == "TextInput"


def test_clipped_text_is_not_visible_evidence():
    raw = {"attributes": {"type": "root", "bounds": "[0,0][1000,2000]"}, "children": [
        {"attributes": {"type": "List", "bounds": "[0,0][1000,500]", "scrollable": "true"}, "children": [
            {"attributes": {"type": "Text", "text": "新名称", "bounds": "[0,900][1000,1000]"}, "children": []}]}]}
    t = UiTree(raw)
    assert t.find_text("新名称") == [] and "新名称" not in t.visible_texts()
    assert len(t.find_text("新名称", include_clipped=True)) == 1  # 定位仍知道它在可视区外（可滑动查找）


# ---- 知识检索：区分产品要求 / 操作经验 / 现场观察，适用范围未知就写未知 ----

def test_knowledge_search_roles_and_applicability(verdict_ws):
    ws = verdict_ws
    kn = KnowledgeStore(ws.root)
    observed = "K-TEST-SEARCH-OBS"
    kn.add({"id": observed, "kind": "product_rule", "subject": "pytest.search.observation",
            "statement": "合成检索观察来源规则", "expected": "synthetic-search",
            "applies_to": {"app": "motor_hm", "app_version": "synthetic-debug-build"},
            "source": {"type": "observation", "ref": "tests/test_goal_loop.py synthetic contract"}})
    facts = {"app_version": "3.0.3.200", "device_form": "bar"}
    out = search(ws, f"{NAV_RULE} {observed} tab_my", "motor_hm", facts=facts)
    by_ref = {r["ref"]: r for rows in out["results"].values() for r in rows}
    assert by_ref[observed]["role"] == "observation" and by_ref[observed]["applicability"] == "unknown"
    assert by_ref[NAV_RULE]["role"] == "requirement" and by_ref[NAV_RULE]["can_be_oracle"] is False
    assert by_ref["target:tab_my"]["callable"].startswith("act tap --target")
    assert out["usable_oracles"] == [] and out["gaps"]
    assert "现场观察" in by_ref[observed]["note"]
    # Only this temporary synthetic rule changes status; unknown scope still cannot be an Oracle.
    kn.confirm(observed, reviewer="pytest-synthetic", basis="仅合成检索分类契约，非产品确认")
    confirmed = search(ws, observed, "motor_hm", facts=facts)
    row = confirmed["results"]["requirement"][0]
    assert row["ref"] == observed and row["applicability"] == "unknown"
    assert row["can_be_oracle"] is False and confirmed["usable_oracles"] == []


def test_in_page_failure_text_is_flagged_as_ui_alert(ws, tmp_path):
    # 真机：VPD 首页「停车场信息加载失败，」「请重试」——状态识别照样认出页面，必须单独提示
    dev = _lab_world(tmp_path, "fail", {"home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Text", "text": "停车场信息加载失败，", "b": [60, 200, 900, 280]},
        {"t": "Button", "b": [60, 300, 600, 400], "click": True, "children": [{"t": "Text", "text": "请重试", "b": [80, 320, 580, 380]}]}]}}})
    _start(ws, dev, "ua1")
    out = cli(ws, "observe", "--app", "motor_hm", "--session", "ua1", "--device", dev)
    assert out["ui_alerts"]["texts"] == ["停车场信息加载失败，", "请重试"] and "反馈" in out["ui_alerts"]["procedure"]
    assert any(e["event"] == "ui_alert" for e in read_jsonl(ws.runs_dir / "session-ua1" / "events.jsonl"))


def test_locked_screen_blocks_every_dispatch_including_back(ws, tmp_path):
    # 真机：锁屏后 aa dump 没有前台 mission（foreground=None），曾被当成桌面放行返回键，结果误判为产品失败
    dev = _lab_world(tmp_path)
    _start(ws, dev, "lk1")
    st = ws.runs_dir / "session-lk1" / "replay_state.json"
    s = read_json(st)
    s["locked"] = True
    write_json(st, s)
    for action in ("back", "home"):
        out = cli(ws, "act", action, "--app", "motor_hm", "--session", "lk1", "--device", dev)
        att = out["result"]["attempts"][-1]
        assert att["status"] == "screen_locked" and "dispatch" not in att
        assert out["result"]["attribution"] == "environment"


WMS_DUMP = """----------------------------------WindowManagerService----------------------------------
-------------------------------------ScreenGroup 0-------------------------------------
WindowName           DisplayId Pid     WinId Type Mode Flag ZOrd Orientation [ x    y    w    h    ] [ OffsetX OffsetY ]
SCBBannerNotificatio 0         5333    14    2106 1    0    103  0           [ 0    0    1320 2848 ] [ 0       0       ]
SCBStatusBar23       0         5333    26    2108 1    0    2202 0           [ 0    0    1320 131  ] [ 0       0       ]
hm0                  0         57218   255   1    1    0    101  0           [ 0    0    1320 2848 ] [ 0       0       ]
---------------------------------------------------------------------------------------
SCBScreenLock12      0         5333    15    2110 1    0    -1   0           [ 0    0    1320 2848 ] [ 0       0       ]
SCBScenePanel1       0         5333    3     2109 1    0    5    0           [ 0    0    0    0    ] [ 0       0       ]
"""


def test_window_list_parses_only_shown_windows():
    from vpdtest.devices.harmony_hdc import parse_windows
    # ZOrd=-1（隐藏）和零面积的窗口不算显示；认不出表头 → None（不能当成"没有弹层"）
    assert parse_windows(WMS_DUMP) == ["SCBBannerNotificatio", "SCBStatusBar23", "hm0"]
    assert parse_windows("[Fail] permission denied") is None


def _set_windows(ws, session, windows):
    st = ws.runs_dir / f"session-{session}" / "replay_state.json"
    s = read_json(st)
    s["windows"] = windows
    write_json(st, s)


def test_notification_banner_and_unknown_windows_keep_screenshot(ws, tmp_path):
    dev = _lab_world(tmp_path)
    _start(ws, dev, "bn1")
    for windows in (["SCBBannerNotificatio", "app0"], None, ["app0"]):
        _set_windows(ws, "bn1", windows)
        o = cli(ws, "observe", "--app", "motor_hm", "--session", "bn1", "--device", dev)["observation"]
        assert o["tree"]["status"] == "ok" and o["screenshot"]["status"] == "ok"
        assert (ws.runs_dir / "session-bn1" / o["screenshot"]["path"]).is_file()


def test_banner_during_capture_keeps_screenshot(ws, tmp_path, monkeypatch):
    from vpdtest.devices.replay import ReplayDevice
    dev = _lab_world(tmp_path)
    _start(ws, dev, "bn2")
    real = ReplayDevice.screenshot

    def shot_then_banner(self, path):
        r = real(self, path)
        self.state["windows"] = ["SCBBannerNotificatio", "app0"]
        return r
    monkeypatch.setattr(ReplayDevice, "screenshot", shot_then_banner)
    o = cli(ws, "observe", "--app", "motor_hm", "--session", "bn2", "--device", dev)["observation"]
    assert o["screenshot"]["status"] == "ok"
    assert (ws.runs_dir / "session-bn2" / o["screenshot"]["path"]).is_file()


def test_old_secret_input_event_does_not_gate_keyboard_screenshots(ws, tmp_path):
    dev = _input_world(tmp_path)
    _start(ws, dev, "kb1")
    _set_windows(ws, "kb1", ["softKeyboard1", "app0"])
    ev = ws.runs_dir / "session-kb1" / "events.jsonl"
    append_jsonl(ev, {"ts": "t", "event": "secret_typed", "step": "A9"})  # 旧会话兼容
    o = cli(ws, "observe", "--app", "motor_hm", "--session", "kb1", "--device", dev)["observation"]
    assert o["screenshot"]["status"] == "ok" and o["fresh"]
    assert (ws.runs_dir / "session-kb1" / o["screenshot"]["path"]).is_file()
    from vpdtest.session import trail
    assert "secret_typed" not in trail(ws.runs_dir / "session-kb1")


def _pad(extra=(), original_text=False):
    """锁屏键盘：1-9 三列三行、0 在 8 下方；extra 追加干扰节点。original_text=True 模拟 Pura X Max：
    数字 text 为空、只在 originalText 里，另有一个 text 为空、originalText 为字母的角标节点。"""
    keys = []
    for i, d in enumerate("1234567890"):
        col, row = (1, 3) if d == "0" else (i % 3, i // 3)
        x, y = 200 + col * 330, 1400 + row * 260
        digit = {"type": "Text", "visible": "true", "bounds": f"[{x + 100},{y + 60}][{x + 160},{y + 140}]"}
        digit.update({"text": "", "originalText": d} if original_text else {"text": d})
        kids = [{"attributes": digit, "children": []}]
        if original_text:
            kids.append({"attributes": {"type": "Text", "visible": "true", "text": "", "originalText": "ABC",
                                        "bounds": f"[{x + 60},{y + 150}][{x + 200},{y + 190}]"}, "children": []})
        keys.append({"attributes": {"type": "Stack", "clickable": "true", "visible": "true",
                                    "bounds": f"[{x},{y}][{x + 260},{y + 200}]", "text": ""},
                     "children": kids})
    return {"attributes": {"type": "root", "visible": "true", "bounds": "[0,0][1320,2848]"}, "children": keys + list(extra)}


def test_pin_pad_recognised_only_when_unambiguous():
    from vpdtest.devices.harmony_hdc import find_pin_pad
    pad = find_pin_pad(_pad())
    assert pad["1"] == (330, 1500) and pad["0"] == (660, 2280)
    # 时钟之类不可点击的数字不算；通知里再出现一个可点击的「5」→ 不认，不猜着点
    clock = {"attributes": {"type": "Text", "visible": "true", "text": "1", "bounds": "[10,100][60,160]"}, "children": []}
    assert find_pin_pad(_pad([clock])) is not None
    dup = {"attributes": {"type": "Button", "clickable": "true", "visible": "true", "bounds": "[10,300][300,400]"},
           "children": [{"attributes": {"type": "Text", "visible": "true", "text": "5", "bounds": "[20,310][80,390]"}, "children": []}]}
    assert find_pin_pad(_pad([dup])) is None
    assert find_pin_pad({"attributes": {"type": "root", "bounds": "[0,0][1320,2848]"}, "children": []}) is None


def test_pin_pad_digits_from_original_text():
    # Pura X Max 锁屏键盘：text 全空、数字在 originalText 里
    from vpdtest.devices.harmony_hdc import find_pin_pad
    pad = find_pin_pad(_pad(original_text=True))
    assert pad is not None and pad["1"] == (330, 1500) and pad["0"] == (660, 2280)


def _locked_world(tmp_path, name, pin="246810", locked=True):
    p = tmp_path / f"{name}.json"
    write_json(p, {"bundle": BUNDLE, "app_version": "3.0.3.200-replay", "device": {"model": "LAB", "os": "replay"},
                   "screen": [1320, 2848], "initial": {"locked": locked, "lock_pin": pin, "installed": True},
                   "launch_screen": "home", "screens": {"home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848]}}}})
    return f"replay:{p}"


def test_unlock_uses_configured_pin_once_and_never_echoes_it(ws, tmp_path):
    ws.config["secrets"] = {"LOCK_PIN": "246810"}
    dev = _locked_world(tmp_path, "lk")
    ws.config["unlock_secrets"] = {dev: "LOCK_PIN"}
    out = cli(ws, "unlock", "--device", dev)
    assert out["status"] == "unlocked" and out["method"] == "pin" and "246810" not in json.dumps(out)
    assert cli(ws, "unlock", "--device", _locked_world(tmp_path, "open", locked=False))["method"] == "already"
    dev = _locked_world(tmp_path, "other", pin="135790")
    ws.config["unlock_secrets"][dev] = "LOCK_PIN"
    bad = cli(ws, "unlock", "--device", dev)
    assert bad["status"] == "pin_rejected" and bad["action_required"]
    again = cli(ws, "unlock", "--device", dev)
    guard = next(ws.runs_dir.glob("unlock-guard-*.json"))
    assert again["status"] == "pin_rejected" and guard.exists()
    assert "246810" not in guard.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["unlock", "--pin", "1"])
    ws.config["secrets"] = {"LOCK_PIN": "12ab"}
    guard.unlink()
    assert cli(ws, "unlock", "--device", dev)["status"] == "missing_secret"


def test_unlock_pin_secret_selects_key_by_name_only(ws, tmp_path):
    ws.config["secrets"] = {"LOCK_PIN": "111111", "LOCK_PIN_TEST": "246810"}
    out = cli(ws, "unlock", "--lock-secret", "LOCK_PIN_TEST", "--device", _locked_world(tmp_path, "t1"))
    assert out["status"] == "unlocked" and out["method"] == "pin" and "246810" not in json.dumps(out)
    # 键名之外的值（包括 PIN 本身、别的密钥名）一律拒绝，不会被当成 PIN 或去读别的密钥
    for bad in ("246810", "QIANKUN_PASSWORD", "LOCK_PIN_test"):
        with pytest.raises(ValueError) as e:
            cli(ws, "unlock", "--lock-secret", bad, "--device", _locked_world(tmp_path, "t2"))
        assert bad not in str(e.value)
    with pytest.raises(SystemExit):  # 前缀缩写也不能把 PIN 塞进命令行
        build_parser().parse_args(["unlock", "--pin", "1"])


def test_input_clear_replaces_instead_of_appending(ws, tmp_path):
    dev = _input_world(tmp_path)
    _start(ws, dev, "cl1")
    base = ["act", "input", "--select", "xpath=//TextInput", "--allow-input", "--app", "motor_hm",
            "--session", "cl1", "--device", dev]
    out = cli(ws, *base, "--text", "新名字", "--clear")
    att = out["result"]["attempts"][-1]
    assert att["clear"]["ok"] and att["dispatch"]["executor_ok"]
    case = __import__("vpdtest.session", fromlist=["from_session"]).from_session(
        ws.runs_dir / "session-cl1", "TC-CL-1", ws.app("motor_hm"), None, ["A1=启动"])
    assert case["steps"][0]["clear"] is True


def test_input_after_clear_follows_relayout(ws, tmp_path):
    """点进输入框后键盘把它顶上去：清空后重新定位，文字输到新位置，而不是清空前的旧坐标。"""
    low = {"t": "TextInput", "b": [40, 2400, 1280, 2520], "click": True, "bind": "name", "text": "旧名字",
           "set_flag": {"focused": True}, "hide_if_flag": "focused"}
    high = {"t": "TextInput", "b": [40, 1200, 1280, 1320], "click": True, "bind": "name", "text_bound": "name",
            "show_if_flag": "focused"}
    dev = _lab_world(tmp_path, "relayout", {"home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848], "children": [
        {"t": "Text", "text": "名称", "b": [40, 700, 600, 780]}, low, high]}}})
    _start(ws, dev, "rl1")
    out = cli(ws, "act", "input", "--select", "xpath=//TextInput", "--allow-input", "--clear", "--text", "新名字",
              "--expect-text", "新名字", "--app", "motor_hm", "--session", "rl1", "--device", dev)
    att = out["result"]["attempts"][-1]
    assert att["status"] == "ok", att.get("post")  # 旧坐标上的输入框已经不在：照旧坐标输入，文字不会出现
    assert att["clear"]["ok"] and att["clear"]["relocate"]["status"] == "found"
    assert att["clear"]["relocate"]["point"][1] < 1400


# ---- R4：会话只从 trail() 读、以 dispatch 事件判定是否执行；设备查询三态 ----

def test_act_error_after_dispatch_is_kept_as_effect_unknown(ws, tmp_path):
    import re as _re
    dev = _lab_world(tmp_path)
    _start(ws, dev, "ae1")
    # 点击已下发，之后 _post 里 expect_text 的正则出错：动作确实发生了，不能当成被拒丢掉
    with pytest.raises(_re.error):
        cli(ws, "act", "tap", "--select", "text=唯一入口", "--expect-text", "[", "--app", "motor_hm",
            "--session", "ae1", "--device", dev)
    sdir = ws.runs_dir / "session-ae1"
    # 另一条下发前就出错的请求：没有 dispatch 事件 → 算被拒
    append_jsonl(sdir / "events.jsonl", {"ts": "t", "event": "act_request", "seq": 3, "step_id": "A3",
                                         "request": {"action": "back"}})
    append_jsonl(sdir / "events.jsonl", {"ts": "t", "event": "act_error", "seq": 3, "reason": "RuntimeError: x"})
    rows = {r["step"]: r for r in cli(ws, "session", "ae1")["trail"] if r["kind"] != "observe"}
    assert rows["A2"]["kind"] == "error" and rows["A2"]["dispatched"] and rows["A2"]["effect_unknown"]
    assert rows["A3"]["kind"] == "error" and not rows["A3"]["dispatched"]
    out = cli(ws, "case", "from-session", "--session", "ae1", "--new-id", "TC-AE-1", "--app", "motor_hm",
              "--drop", "A1=启动")
    case = ws.load_case("TC-AE-1")
    src = case["sources"][0]
    assert [s["from_session"]["seq"] for s in case["steps"]] == [2]  # 已下发的 A2 保留为步骤
    assert case["steps"][0]["from_session"]["effect_unknown"] is True
    assert [r["seq"] for r in src["rejected"]] == [3] and src["effect_unknown"][0]["seq"] == 2
    assert any(i["field"] == "steps.S2" and "已下发但结果未确认" in i["problem"] for i in out["issues"])
    # 手工把已下发的动作塞进 rejected：lint 报错
    case["sources"][0]["rejected"].append({"seq": 2, "dispatched": True})
    assert any(i["field"] == "sources.rejected.A2" for i in C.lint(case, ws.app("motor_hm"), KnowledgeStore(ws.root)))
    case["sources"][0]["rejected"].pop()
    case["steps"][0]["effect_reviewed"] = "obs_after 显示已进入详情页"
    assert not any(i["field"] == "steps.S2" and "已下发" in i["problem"]
                   for i in C.lint(case, ws.app("motor_hm"), KnowledgeStore(ws.root)))


def test_case_from_session_refuses_app_different_from_session(ws, tmp_path):
    dev = _lab_world(tmp_path)
    _start(ws, dev, "ap1")
    cli(ws, "act", "tap", "--select", "text=唯一入口", "--app", "motor_hm", "--session", "ap1", "--device", dev)
    with pytest.raises(PermissionError, match="不一致"):
        cli(ws, "case", "from-session", "--session", "ap1", "--new-id", "TC-AP-1", "--app", "qiankun_hm")
    assert not ws.case_path("TC-AP-1").exists()
    # 没有会话头也没有 session_facts 的会话：无法确认身份，拒绝转换
    bare = ws.runs_dir / "session-ap2"
    bare.mkdir(parents=True)
    append_jsonl(bare / "events.jsonl", {"ts": "t", "event": "act_request", "seq": 1, "step_id": "A1",
                                         "request": {"action": "back"}})
    with pytest.raises(PermissionError, match="没有会话头"):
        cli(ws, "case", "from-session", "--session", "ap2", "--new-id", "TC-AP-2", "--app", "motor_hm")


class _FakeHdc:
    """HdcDevice 的 _shell 替身：按 op 返回预设输出（列表按调用顺序取；False = 命令失败），记录发出过的 op。"""

    def __init__(self, monkeypatch, outputs):
        from vpdtest.devices import harmony_hdc as H
        monkeypatch.setattr(H.time, "sleep", lambda s: None)
        self.dev = object.__new__(H.HdcDevice)
        self.dev.serial, self.dev.spec, self.dev.last_query_error = "fake", "hdc:fake", None
        self.ops, self.outputs, self.DR = [], outputs, H.DeviceResult
        self.dev._shell = self._shell

    def _shell(self, *args, op, timeout=None):
        self.ops.append(op)
        out = self.outputs.get(op)
        out = (out.pop(0) if len(out) > 1 else out[0]) if isinstance(out, list) else out  # 列表取完后一直返回最后一项
        if out is False:
            return self.DR(op, True, False, "command_failed", "boom", "")
        return self.DR(op, True, True, None, None, out or "")


AWAKE, ASLEEP, UNLOCKED = "Current State: AWAKE", "Current State: SLEEP", "screenLocked: false"


def test_screen_locked_is_tristate_and_wake_unlock_never_gestures_on_unknown(monkeypatch):
    f = _FakeHdc(monkeypatch, {"power_state": AWAKE, "lock_state": False})
    assert f.dev.screen_locked() is None
    out = f.dev.wake_unlock("000000")
    assert out["status"] == "indeterminate" and "pin_entered" not in out
    assert not {"wakeup", "unlock_swipe", "tap"} & set(f.ops)  # 查询失败：一个手势都不发
    assert _FakeHdc(monkeypatch, {"power_state": "garbled", "lock_state": UNLOCKED}).dev.screen_locked() is None
    assert _FakeHdc(monkeypatch, {"power_state": ASLEEP}).dev.screen_locked() is True
    assert _FakeHdc(monkeypatch, {"power_state": AWAKE, "lock_state": UNLOCKED}).dev.screen_locked() is False
    # 明确熄屏 → 唤醒；唤醒后锁屏查询失败 → 不上滑
    f = _FakeHdc(monkeypatch, {"power_state": [ASLEEP, ASLEEP, AWAKE], "lock_state": [False]})
    out = f.dev.wake_unlock("000000")
    assert out["status"] == "indeterminate" and "wakeup" in f.ops and "unlock_swipe" not in f.ops


def test_screen_locked_retries_transient_empty_dump(monkeypatch):
    # 真机上 hidumper 偶发空输出：重查一次拿到明确值就用，不因一次空输出阻塞整条用例
    f = _FakeHdc(monkeypatch, {"power_state": ["", AWAKE], "lock_state": UNLOCKED})
    assert f.dev.screen_locked() is False and f.ops.count("power_state") == 2
    f = _FakeHdc(monkeypatch, {"power_state": "", "lock_state": UNLOCKED})
    assert f.dev.screen_locked() is None and f.ops.count("power_state") == 3  # 连续失败仍如实返回 None


def test_unlock_command_reports_indeterminate_as_action_required(ws, tmp_path, monkeypatch):
    from vpdtest.devices.replay import ReplayDevice
    monkeypatch.setattr(ReplayDevice, "_query_fails", lambda self, name: name == "screen_locked")
    ws.config["secrets"] = {"LOCK_PIN": "246810"}
    out = cli(ws, "unlock", "--device", _locked_world(tmp_path, "qf"))
    assert out["status"] == "indeterminate" and out["action_required"]
    assert not (ws.runs_dir / "unlock_guard.json").exists()  # 没输 PIN，不写防重入标记


def test_clear_text_aborts_when_move_end_fails(monkeypatch):
    f = _FakeHdc(monkeypatch, {"key_move_end": False})
    f.dev.tap = lambda x, y: f.DR("tap", True, True)
    from vpdtest.dispatch import Dispatcher
    r = f.dev.clear_text(10, 20, 5, Dispatcher(f.dev), {})
    assert not r.ok and r.sent and "MoveEnd" in r.error and "key_del" not in f.ops


def test_clear_failure_is_recorded_as_dispatched(ws, tmp_path, monkeypatch):
    from vpdtest.devices import DeviceResult
    from vpdtest.devices.replay import ReplayDevice
    monkeypatch.setattr(ReplayDevice, "clear_text",
                        lambda self, x, y, n, **kw: DeviceResult("clear_text", True, False, "command_failed", "MoveEnd 失败"))
    dev = _input_world(tmp_path)
    _start(ws, dev, "cf1")
    out = cli(ws, "act", "input", "--select", "xpath=//TextInput", "--allow-input", "--clear", "--text", "新名字",
              "--app", "motor_hm", "--session", "cf1", "--device", dev)
    att = out["result"]["attempts"][0]
    assert att["status"] == "device_error" and att["dispatch"]["sent"] and not att["dispatch"]["executor_ok"]
    row = [r for r in cli(ws, "session", "cf1")["trail"] if r.get("step") == "A2"][0]
    assert row["dispatched"] and row["effect_unknown"]


def _set_state(ws, session, **kw):
    st = ws.runs_dir / f"session-{session}" / "replay_state.json"
    s = read_json(st)
    s.update(kw)
    write_json(st, s)


def test_gesture_is_not_sent_when_lock_or_foreground_query_fails(ws, tmp_path):
    dev = _lab_world(tmp_path)
    _start(ws, dev, "qf1")
    for q in ("screen_locked", "foreground"):
        _set_state(ws, "qf1", query_fail=[q])
        for action in ("back", "swipe"):
            out = cli(ws, "act", action, "--app", "motor_hm", "--session", "qf1", "--device", dev)
            att = out["result"]["attempts"][-1]
            assert "dispatch" not in att and out["result"]["attribution"] == ("tool" if q == "foreground" else "environment"), (q, action)
            if q == "foreground" and action == "swipe":
                # 滑动先按观测定滑动区域：前台查不到时观测本身就失败，在门禁之前已停下（同样不发）
                assert att["status"] == "observation_failed"
                continue
            assert att["status"] == "state_indeterminate", (q, action)
            assert att["state_check"]["query"] == q and out["diagnosis"]["action_required"]
    _set_state(ws, "qf1", query_fail=[])
    att = cli(ws, "act", "back", "--app", "motor_hm", "--session", "qf1", "--device", dev)["result"]["attempts"][-1]
    assert att["dispatch"]["sent"]


def test_scroll_search_does_not_swipe_when_lock_state_unknown(ws, tmp_path):
    from vpdtest.execute import Executor
    from vpdtest.experience import ExperienceStore
    from vpdtest.observe import RunStore
    from vpdtest.session import parse_select
    from vpdtest.workspace import RunContext
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir, "session-sc", kind="session")
    dev = ws.device(_lab_world(tmp_path), state_path=store.dir / "replay_state.json")
    dev.launch(BUNDLE, app.ability)
    dev.state["query_fail"] = ["screen_locked"]
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    swipes = []
    dev.swipe = lambda *a, **k: swipes.append(a)
    target = {**parse_select("text=看不见的行"), "scroll": {"direction": "up", "max": 3}}
    res = Executor(ctx).run_step({"id": "A1", "action": "tap", "retry": 0, "target": target}, "session")
    att = res["attempts"][-1]
    assert att["status"] == "state_indeterminate" and not swipes and "dispatch" not in att


# ---- R5/R6 ----

def test_screenshot_kept_but_tree_stale_when_switching_to_other_allowed_app(ws, tmp_path, monkeypatch):
    from vpdtest.devices.replay import ReplayDevice
    dev = _lab_world(tmp_path)
    _start(ws, dev, "fg2")
    orig, calls = ReplayDevice.foreground, []

    def fg(self):
        calls.append(1)
        # 取树前是被测 APP；取树后换成白名单里的另一个 APP（乾崑）
        return orig(self) if len(calls) == 1 else "com.yinwang.qiankunapp.hm"
    monkeypatch.setattr(ReplayDevice, "foreground", fg)
    obs = cli(ws, "observe", "--app", "motor_hm", "--session", "fg2", "--device", dev)["observation"]
    assert obs["screenshot"]["status"] == "ok" and obs["foreground_after"] == "com.yinwang.qiankunapp.hm"
    assert obs["fresh"] is False and obs["tree"]["status"] == "stale"
    assert (ws.runs_dir / "session-fg2" / obs["screenshot"]["path"]).is_file()


def test_act_surfaces_action_required_at_top_level_for_exit_code(ws, tmp_path):
    dev = _lab_world(tmp_path)
    _start(ws, dev, "ar1")
    _set_state(ws, "ar1", query_fail=["screen_locked"])
    out = cli(ws, "act", "back", "--app", "motor_hm", "--session", "ar1", "--device", dev)
    assert out["diagnosis"]["action_required"] and out["action_required"] == out["diagnosis"]["action_required"]
    _set_state(ws, "ar1", query_fail=[])
    assert "action_required" not in cli(ws, "act", "back", "--app", "motor_hm", "--session", "ar1", "--device", dev)


def test_external_secret_input_keeps_evidence_but_is_not_embedded_in_generated_case(ws, tmp_path, monkeypatch):
    dev = _input_world(tmp_path)
    _start(ws, dev, "i2")
    monkeypatch.setenv("VPD_SECRET_FAKE_PW", "Fake#Pass9")
    base = ["act", "input", "--select", "xpath=//TextInput", "--allow-input", "--app", "motor_hm", "--session", "i2",
            "--device", dev]
    assert cli(ws, *base, "--text", "前缀Fake#Pass9")["input_value_recorded"] is False   # 包含整段密钥
    assert cli(ws, *base, "--text", "xFake#Pa")["input_value_recorded"] is False         # 包含 ≥6 位片段
    assert cli(ws, *base, "--text", "测试名B")["input_value_recorded"] is True
    disk = "".join(p.read_text("utf-8") for p in (ws.runs_dir / "session-i2").rglob("*") if p.suffix in (".json", ".jsonl"))
    assert "Fake#Pa" in disk and "测试名B" in disk


def test_unlock_guard_is_written_before_pin_and_blocks_concurrent_or_crashed_runs(ws, tmp_path, monkeypatch):
    from vpdtest.util import sha256_obj
    ws.config["secrets"] = {"LOCK_PIN": "246810"}
    dev = _locked_world(tmp_path, "c1")
    ws.config["unlock_secrets"] = {dev: "LOCK_PIN"}
    guard = ws.runs_dir / f"unlock-guard-{sha256_obj(dev)[:12]}.json"
    seen = {}
    def crash(device, pin, dispatcher, **kwargs):
        seen["guard_during"] = guard.exists() and read_json(guard)["status"]
        raise RuntimeError("进程在输 PIN 中途退出")
    monkeypatch.setattr("vpdtest.unlock.wake_unlock", crash)
    with pytest.raises(RuntimeError):
        cli(ws, "unlock", "--device", dev)
    assert seen["guard_during"] == "in_progress" and guard.exists()
    monkeypatch.undo()
    out = cli(ws, "unlock", "--device", dev)
    assert out["status"] == "pin_rejected" and out["action_required"]
    # 不同设备的防重入记录互不污染。
    other = _locked_world(tmp_path, "c2")
    ws.config["unlock_secrets"][other] = "LOCK_PIN"
    assert cli(ws, "unlock", "--device", other)["status"] == "unlocked"
    guard.unlink()
    assert cli(ws, "unlock", "--device", dev)["status"] == "unlocked"
    assert not guard.exists()


def test_unlock_swipe_scales_with_display_and_stops_without_size(monkeypatch):
    from vpdtest.devices.harmony_hdc import unlock_swipe_points
    assert unlock_swipe_points({"width_px": 1320, "height_px": 2848}) == (660, 2400, 660, 899)
    x1, y1, x2, y2 = unlock_swipe_points({"width_px": 2472, "height_px": 1648})   # 阔折叠展开
    assert x1 == x2 == 1236 and 1648 * 0.8 < y1 < 1648 and y2 < 1648 * 0.4
    assert unlock_swipe_points({"width_px": None, "height_px": 2848}) is None
    f = _FakeHdc(monkeypatch, {"power_state": [AWAKE] * 3, "lock_state": ["screenLocked: true"] * 3})
    f.dev.display_info = lambda: {"width_px": None, "height_px": None}
    out = f.dev.wake_unlock("000000")
    assert out["status"] == "indeterminate" and "unlock_swipe" not in f.ops and "tap" not in f.ops


def test_non_input_fields_keep_original_evidence_in_steps(ws, tmp_path, monkeypatch):
    # 选择器和预期文字按原文记录，不再执行运行级动态替换
    dev = _lab_world(tmp_path)
    _start(ws, dev, "rd1")
    monkeypatch.setenv("VPD_SECRET_ACCT", "Acct-7h3x-9921")
    cli(ws, "act", "tap", "--select", "text=唯一入口Acct-7h3x-9921", "--expect-text", "Acct-7h3x-9921",
        "--app", "motor_hm", "--session", "rd1", "--device", dev)
    cli(ws, "act", "tap", "--select", "text=唯一入口", "--expect-text", "详情Acct-7h3x",
        "--app", "motor_hm", "--session", "rd1", "--device", dev)
    sdir = ws.runs_dir / "session-rd1"
    disk = "".join(p.read_text("utf-8") for p in sdir.rglob("*") if p.suffix in (".json", ".jsonl"))
    assert "Acct-7h3x-9921" in disk and "详情Acct-7h3x" in disk


def test_env_and_config_values_do_not_rewrite_evidence(ws, tmp_path, monkeypatch):
    dev = _lab_world(tmp_path)
    _start(ws, dev, "rd2")
    monkeypatch.setenv("VPD_SECRET_PW", "EnvPass-4411")
    ws.config["secrets"] = {"PW": "CfgPass-8822"}
    for v in ("EnvPass-4411", "CfgPass-8822"):
        cli(ws, "act", "tap", "--select", f"text={v}", "--app", "motor_hm", "--session", "rd2", "--device", dev)
    disk = "".join(p.read_text("utf-8") for p in (ws.runs_dir / "session-rd2").rglob("*")
                   if p.suffix in (".json", ".jsonl"))
    assert "EnvPass-4411" in disk and "CfgPass-8822" in disk


def test_app_switch_during_capture_deletes_screenshot(ws, tmp_path, monkeypatch):
    # 截图期间未在白名单中的应用到前台：末查保持 APP 边界
    from vpdtest.devices.replay import ReplayDevice
    dev = _lab_world(tmp_path)
    _start(ws, dev, "bn3")
    real = ReplayDevice.screenshot

    def shot_then_switch(self, path):
        r = real(self, path)
        self.state["foreground"] = "com.tencent.mm"
        return r
    monkeypatch.setattr(ReplayDevice, "screenshot", shot_then_switch)
    o = cli(ws, "observe", "--app", "motor_hm", "--session", "bn3", "--device", dev)["observation"]
    assert o["screenshot"]["status"] == "withheld" and "已删除" in o["screenshot"]["reason"]
    assert o["foreground_after"] == "com.tencent.mm"
    assert not (ws.runs_dir / "session-bn3" / "obs" / o["obs_id"] / "screen.png").exists()


def test_random_point_skips_points_covered_by_sheet():
    # 真机 run-20261001-023742-e4dd：地图下半被底部面板盖住，随机点落到「附近充电站」tab 上被策略拒绝
    from types import SimpleNamespace
    from vpdtest.execute import Executor
    mapx = {"attributes": {"type": "XComponent", "bounds": "[0,0][1000,2000]"}, "children": []}
    sheet = {"attributes": {"type": "Column", "bounds": "[0,1000][1000,2000]"}, "children": [
        {"attributes": {"type": "Text", "text": "附近充电站", "bounds": "[0,1000][1000,2000]", "clickable": "true"}, "children": []}]}
    # 地图上方还有一层透明、不可点击的浮层容器：触摸会穿透它，不应被当成遮挡
    veil = {"attributes": {"type": "Column", "bounds": "[0,0][1000,1000]"}, "children": []}
    t = UiTree({"attributes": {"type": "root", "bounds": "[0,0][1000,2000]"}, "children": [mapx, veil, sheet]})
    idx = next(n.idx for n in t.nodes if n.type == "XComponent")
    ex = Executor.__new__(Executor)
    ex.ctx = SimpleNamespace(seed="s")
    loc = {"visible": (0, 0, 1000, 2000), "attempts": [{"status": "found", "node": {"idx": idx}}]}
    for n in range(1, 8):
        x, y = ex._point_in(loc, {"id": "S2", "random_point": True}, n, t)
        assert y < 1000 and t.touch_target(x, y) is None
