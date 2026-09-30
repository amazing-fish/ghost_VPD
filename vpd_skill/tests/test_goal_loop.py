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


def test_partial_secret_is_redacted():
    # 真机上见过：密码的前一段落进了另一个输入框。只按全值替换会把这段明文写进 tree.json
    from vpdtest.params import Params
    import os
    os.environ["VPD_SECRET_T_PW"] = "Abc123456xyz"
    try:
        p = Params({"pw": {"secret": "T_PW"}}, None, "run-x")
        assert p.redact("框里是 Abc12345 后面") == "框里是 *** 后面"
        assert p.contains_secret(["456xyz"]) and not p.contains_secret(["Abc12"])
    finally:
        del os.environ["VPD_SECRET_T_PW"]


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


def test_notification_banner_withholds_screenshot(ws, tmp_path):
    # 真机：聊天通知横幅盖住 APP 顶部，aa dump 仍报 APP 在前台，按 bundle 取的树不含横幅——截图会带出消息内容
    dev = _lab_world(tmp_path)
    _start(ws, dev, "bn1")
    _set_windows(ws, "bn1", ["SCBBannerNotificatio", "app0"])
    out = cli(ws, "observe", "--app", "motor_hm", "--session", "bn1", "--device", dev)
    o = out["observation"]
    assert o["screenshot"]["status"] == "withheld" and o["system_overlays"] == ["SCBBannerNotificatio"]
    assert not (ws.runs_dir / "session-bn1" / "obs" / o["obs_id"] / "screen.png").exists()
    assert o["tree"]["status"] == "ok"  # 树照样取：Agent 还能按树判断
    _set_windows(ws, "bn1", None)  # 查询失败：无法确认，也不截图
    o = cli(ws, "observe", "--app", "motor_hm", "--session", "bn1", "--device", dev)["observation"]
    assert o["screenshot"]["status"] == "withheld" and "查询失败" in o["screenshot"]["reason"]
    _set_windows(ws, "bn1", ["app0"])
    o = cli(ws, "observe", "--app", "motor_hm", "--session", "bn1", "--device", dev)["observation"]
    assert o["screenshot"]["status"] == "ok"


def test_banner_during_capture_deletes_screenshot(ws, tmp_path, monkeypatch):
    from vpdtest.devices.replay import ReplayDevice
    dev = _lab_world(tmp_path)
    _start(ws, dev, "bn2")
    calls = iter([["app0"], ["SCBBannerNotificatio", "app0"]])  # 截图前没有，截完出现
    monkeypatch.setattr(ReplayDevice, "shown_windows", lambda self: next(calls))
    o = cli(ws, "observe", "--app", "motor_hm", "--session", "bn2", "--device", dev)["observation"]
    assert o["screenshot"]["status"] == "withheld" and "已删除" in o["screenshot"]["reason"]
    assert not (ws.runs_dir / "session-bn2" / "obs" / o["obs_id"] / "screen.png").exists()


def test_secret_input_hides_keyboard_screenshots_for_rest_of_session(ws, tmp_path):
    # uitest inputText 经剪贴板：输入法「来自剪贴板」栏会显示刚输入的原文
    dev = _input_world(tmp_path)
    _start(ws, dev, "kb1")
    _set_windows(ws, "kb1", ["softKeyboard1", "app0"])
    o = cli(ws, "observe", "--app", "motor_hm", "--session", "kb1", "--device", dev)["observation"]
    assert o["screenshot"]["status"] == "ok"  # 没输入过密钥：输入法在场也照常截图
    ws.config["secrets"] = {"PW": "Kx9-secret-value"}
    ev = ws.runs_dir / "session-kb1" / "events.jsonl"
    append_jsonl(ev, {"ts": "t", "event": "secret_typed", "step": "A9"})  # 前一条 act 输入过密钥
    o = cli(ws, "observe", "--app", "motor_hm", "--session", "kb1", "--device", dev)["observation"]
    assert o["screenshot"]["status"] == "withheld" and o["system_overlays"] == ["softKeyboard1"]
    assert "Kx9-secret-value" not in ev.read_text(encoding="utf-8")


def _pad(extra=()):
    """锁屏键盘：1-9 三列三行、0 在 8 下方；extra 追加干扰节点。"""
    keys = []
    for i, d in enumerate("1234567890"):
        col, row = (1, 3) if d == "0" else (i % 3, i // 3)
        x, y = 200 + col * 330, 1400 + row * 260
        keys.append({"attributes": {"type": "Button", "clickable": "true", "visible": "true",
                                    "bounds": f"[{x},{y}][{x + 260},{y + 200}]", "text": ""},
                     "children": [{"attributes": {"type": "Text", "visible": "true", "text": d,
                                                  "bounds": f"[{x + 100},{y + 60}][{x + 160},{y + 140}]"}, "children": []}]})
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


def _locked_world(tmp_path, name, pin="246810", locked=True):
    p = tmp_path / f"{name}.json"
    write_json(p, {"bundle": BUNDLE, "app_version": "3.0.3.200-replay", "device": {"model": "LAB", "os": "replay"},
                   "screen": [1320, 2848], "initial": {"locked": locked, "lock_pin": pin, "installed": True},
                   "launch_screen": "home", "screens": {"home": {"root": {"t": "Column", "b": [0, 0, 1320, 2848]}}}})
    return f"replay:{p}"


def test_unlock_uses_configured_pin_once_and_never_echoes_it(ws, tmp_path):
    ws.config["secrets"] = {"LOCK_PIN": "246810"}
    out = cli(ws, "unlock", "--device", _locked_world(tmp_path, "lk"))
    assert out["status"] == "unlocked" and out["method"] == "pin" and "246810" not in json.dumps(out)
    assert cli(ws, "unlock", "--device", _locked_world(tmp_path, "open", locked=False))["method"] == "already"
    # PIN 不对：只输一次，写防重入标记；之后不再输入（连续输错会锁机）
    dev = _locked_world(tmp_path, "other", pin="135790")
    bad = cli(ws, "unlock", "--device", dev)
    assert bad["status"] == "pin_rejected" and bad["action_required"]
    again = cli(ws, "unlock", "--device", dev)
    assert again["status"] == "refused" and (ws.runs_dir / "unlock_guard.json").exists()
    assert "246810" not in (ws.runs_dir / "unlock_guard.json").read_text(encoding="utf-8")
    with pytest.raises(SystemExit):  # 没有命令行传 PIN 的入口
        build_parser().parse_args(["unlock", "--pin", "1"])
    ws.config["secrets"] = {"LOCK_PIN": "12ab"}
    (ws.runs_dir / "unlock_guard.json").unlink()
    assert cli(ws, "unlock", "--device", dev)["status"] == "refused"  # 不是纯数字：不输入


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
        out = out.pop(0) if isinstance(out, list) else out
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
    f = _FakeHdc(monkeypatch, {"power_state": [ASLEEP, AWAKE], "lock_state": [False]})
    out = f.dev.wake_unlock("000000")
    assert out["status"] == "indeterminate" and "wakeup" in f.ops and "unlock_swipe" not in f.ops


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
    r = f.dev.clear_text(10, 20, 5)
    assert not r.ok and r.sent and "MoveEnd" in r.error and "key_del" not in f.ops


def test_clear_failure_is_recorded_as_dispatched(ws, tmp_path, monkeypatch):
    from vpdtest.devices import DeviceResult
    from vpdtest.devices.replay import ReplayDevice
    monkeypatch.setattr(ReplayDevice, "clear_text",
                        lambda self, x, y, n: DeviceResult("clear_text", True, False, "command_failed", "MoveEnd 失败"))
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
            assert "dispatch" not in att and out["result"]["attribution"] == "environment", (q, action)
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

def test_screenshot_withheld_when_foreground_switches_to_other_allowed_app(ws, tmp_path, monkeypatch):
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
    assert obs["screenshot"]["status"] == "withheld" and obs["foreground_after"] == "com.yinwang.qiankunapp.hm"
    assert not (ws.runs_dir / "session-fg2" / "obs" / obs["obs_id"] / "screen.png").exists()


def test_act_surfaces_action_required_at_top_level_for_exit_code(ws, tmp_path):
    dev = _lab_world(tmp_path)
    _start(ws, dev, "ar1")
    _set_state(ws, "ar1", query_fail=["screen_locked"])
    out = cli(ws, "act", "back", "--app", "motor_hm", "--session", "ar1", "--device", dev)
    assert out["diagnosis"]["action_required"] and out["action_required"] == out["diagnosis"]["action_required"]
    _set_state(ws, "ar1", query_fail=[])
    assert "action_required" not in cli(ws, "act", "back", "--app", "motor_hm", "--session", "ar1", "--device", dev)


def test_input_containing_a_secret_is_not_recorded(ws, tmp_path, monkeypatch):
    dev = _input_world(tmp_path)
    _start(ws, dev, "i2")
    monkeypatch.setenv("VPD_SECRET_FAKE_PW", "Fake#Pass9")
    base = ["act", "input", "--select", "xpath=//TextInput", "--allow-input", "--app", "motor_hm", "--session", "i2",
            "--device", dev]
    assert cli(ws, *base, "--text", "前缀Fake#Pass9")["input_value_recorded"] is False   # 包含整段密钥
    assert cli(ws, *base, "--text", "xFake#Pa")["input_value_recorded"] is False         # 包含 ≥6 位片段
    assert cli(ws, *base, "--text", "测试名B")["input_value_recorded"] is True
    disk = "".join(p.read_text("utf-8") for p in (ws.runs_dir / "session-i2").rglob("*") if p.suffix in (".json", ".jsonl"))
    assert "Fake#Pa" not in disk and "测试名B" in disk


def test_unlock_guard_is_written_before_pin_and_blocks_concurrent_or_crashed_runs(ws, tmp_path, monkeypatch):
    from vpdtest.devices.replay import ReplayDevice
    ws.config["secrets"] = {"LOCK_PIN": "246810"}
    guard = ws.runs_dir / "unlock_guard.json"
    seen = {}

    def crash(self, pin):
        seen["guard_during"] = guard.exists() and read_json(guard)["status"]
        raise RuntimeError("进程在输 PIN 中途退出")
    monkeypatch.setattr(ReplayDevice, "wake_unlock", crash)
    with pytest.raises(RuntimeError):
        cli(ws, "unlock", "--device", _locked_world(tmp_path, "c1"))
    assert seen["guard_during"] == "in_progress" and guard.exists()   # 输 PIN 前已落标记，崩溃后仍在
    monkeypatch.undo()
    ws.config["secrets"] = {"LOCK_PIN": "246810"}
    out = cli(ws, "unlock", "--device", _locked_world(tmp_path, "c2"))
    assert out["status"] == "refused" and out["action_required"]      # 标记在：不再输 PIN
    guard.unlink()
    assert cli(ws, "unlock", "--device", _locked_world(tmp_path, "c3"))["status"] == "unlocked"
    assert not guard.exists()                                           # 确认解锁才删


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


def test_secret_outside_text_is_redacted_in_steps(ws, tmp_path, monkeypatch):
    # 密钥出现在 --select / --expect-text 里：steps.jsonl 整条记录都要脱敏，不只 --text
    dev = _lab_world(tmp_path)
    _start(ws, dev, "rd1")
    monkeypatch.setenv("VPD_SECRET_ACCT", "Acct-7h3x-9921")
    cli(ws, "act", "tap", "--select", "text=唯一入口Acct-7h3x-9921", "--expect-text", "Acct-7h3x-9921",
        "--app", "motor_hm", "--session", "rd1", "--device", dev)
    cli(ws, "act", "tap", "--select", "text=唯一入口", "--expect-text", "详情Acct-7h3x",
        "--app", "motor_hm", "--session", "rd1", "--device", dev)
    sdir = ws.runs_dir / "session-rd1"
    disk = "".join(p.read_text("utf-8") for p in sdir.rglob("*") if p.suffix in (".json", ".jsonl"))
    assert "Acct-7h3x" not in disk and "7h3x-9921" not in disk


def test_env_and_config_values_of_same_secret_are_both_redacted(ws, tmp_path, monkeypatch):
    dev = _lab_world(tmp_path)
    _start(ws, dev, "rd2")
    monkeypatch.setenv("VPD_SECRET_PW", "EnvPass-4411")
    ws.config["secrets"] = {"PW": "CfgPass-8822"}
    for v in ("EnvPass-4411", "CfgPass-8822"):
        cli(ws, "act", "tap", "--select", f"text={v}", "--app", "motor_hm", "--session", "rd2", "--device", dev)
    disk = "".join(p.read_text("utf-8") for p in (ws.runs_dir / "session-rd2").rglob("*")
                   if p.suffix in (".json", ".jsonl"))
    assert "EnvPass-4411" not in disk and "CfgPass-8822" not in disk


def test_app_switch_during_capture_deletes_screenshot(ws, tmp_path, monkeypatch):
    # 截图期间别的普通应用到前台：private_windows 认不出，必须再查前台
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
