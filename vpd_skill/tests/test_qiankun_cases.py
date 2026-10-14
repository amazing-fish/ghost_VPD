"""乾崑样例用例（TC-QK-101/102/103）在合成回放世界上的闭环：
101 登录→切车→进 VPD→默认车位算路，有故障时「开始」不可用；102 地图选点收藏/重命名/标签→按名搜索算路→读距离；
103 有人监护的激活→运动中监视→暂停→退后台→拉回→继续→泊入完成。

世界 qiankun_hm_base/ready.json 的层级按原工程绝对 xpath 搭建，文案标（占位）的都是虚构：这些测试只证明
执行器、策略、原文取证与判定逻辑，不代表乾崑 APP 的真实行为。
"""
import json

import pytest

from conftest import WORLDS, world
from vpdtest.cases import lint
from vpdtest.devices.replay import load_world
from vpdtest.explore import explore
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

PHONE, PASSWORD = "13900001111", "Fake#Pass9"  # 测试用假值
AUTH_101 = ["account_state", "vehicle_selection", "vpd_entry", "route_planning"]
AUTH_102 = ["vpd_entry", "user_data", "route_planning"]
AUTH_103 = ["vpd_entry", "route_planning", "vehicle_motion"]


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", PHONE)
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", PASSWORD)


def _by(run):
    return {s["step_id"]: s for s in run["steps"]}


def _outcomes(run):
    return [(s["step_id"], s["outcome"], s.get("reason")) for s in run["steps"]]


def _classified_map_world(tmp_path, name):
    # 仅合成正例提供地图动作语义；不能从这里推导真实无文字地图的效果或提升生产资产状态。
    fixture = load_world(WORLDS / name)
    stack = [fixture["screens"]["qk_vpd_home"]["root"]]
    found = 0
    while stack:
        node = stack.pop()
        if node.get("t") == "XComponent":
            node["text"] = "规划路线（合成地图占位）"
            found += 1
        stack.extend(node.get("children", []))
    assert found == 1
    fixture.pop("_path", None)
    path = tmp_path / name
    write_json(path, fixture)
    return f"replay:{path}"


def test_101_fault_blocks_activation_without_touching_start(ws, secrets):
    run = run_case(ws, "TC-QK-101", device_spec=world("qiankun_hm_base.json"), propose=False, authorize=AUTH_101)
    assert all(s["outcome"] == "passed" for s in run["steps"]), _outcomes(run)
    a = {x["id"]: x for x in run["assertions"]}
    # 判据未确认 → pending；观测本身成立：「开始」置灰且 10 秒后仍有不可用提示
    assert a["A1"]["result"] == "pending" and a["A1"]["observed_holds"] is True
    assert a["A2"]["observed_holds"] is True and a["A3"]["observed_holds"] is True
    assert run["verdict"]["verdict"] == "pending"
    meas = {m["step"]: m["value"] for m in run["measurements"]}
    assert meas["S3"] == "问界M5（占位）" and meas["S7"] == "泊车代驾辅助暂不可用"
    assert run["motion"] == [] and run["safety_stops"] == []
    taps = [x for s in run["steps"] for x in s["attempts"] if x.get("locate")]
    assert not any((t["locate"].get("target") or "") in ("qk_start", "qk_start_confirm") for t in taps)


def test_101_keeps_raw_observation_and_secret_parameter_references(ws, secrets):
    run = run_case(ws, "TC-QK-101", device_spec=world("qiankun_hm_base.json"), propose=False, authorize=AUTH_101)
    d = ws.runs_dir / run["run_id"]
    observed = [read_json(p) for p in d.glob("obs/*/observation.json")]
    raw = [o for o in observed if PHONE in str(o.get("texts"))]
    assert raw and all(o["screenshot"]["status"] == "ok" for o in raw)
    assert not any("redacted" in o for o in observed)
    assert PHONE in "".join(p.read_text() for p in d.glob("obs/*/tree.json"))
    inputs = [x["input"] for s in run["steps"] for x in s["attempts"] if x.get("input")]
    assert {i["secret"] for i in inputs} == {True} and all("ref" in i and "length" in i for i in inputs)
    assert "clipboard_may_hold_secret" not in run
    assert not any(e.get("event") == "secret_typed" for e in __import__("vpdtest.util", fromlist=["read_jsonl"]).read_jsonl(d / "events.jsonl"))


def test_101_blocked_before_any_step_without_secrets_or_authorization(ws, monkeypatch):
    monkeypatch.delenv("VPD_SECRET_QIANKUN_PHONE", raising=False)
    monkeypatch.delenv("VPD_SECRET_QIANKUN_PASSWORD", raising=False)
    run = run_case(ws, "TC-QK-101", device_spec=world("qiankun_hm_base.json"), propose=False,
                   authorize=["vehicle_selection", "vpd_entry", "route_planning"])
    assert run["verdict"]["verdict"] == "blocked" and run["verdict"]["attribution"] == "environment"
    keys = {u["key"] for u in run["env"]["unmet"]}
    assert keys == {"secret.QIANKUN_PHONE", "secret.QIANKUN_PASSWORD", "authorize.account_state"}
    assert all(s["outcome"] == "not_run" for s in run["steps"]) and run["actions"] == 0


def test_102_favorite_rename_tag_search_and_read_distance(ws, tmp_path):
    run = run_case(ws, "TC-QK-102", device_spec=_classified_map_world(tmp_path, "qiankun_hm_ready.json"),
                   propose=False, authorize=AUTH_102)
    assert all(s["outcome"] == "passed" for s in run["steps"]), _outcomes(run)
    assert all(c["outcome"] == "passed" for c in run["cleanup"]), [(c["step_id"], c.get("reason")) for c in run["cleanup"]]
    assert {m["step"]: m["value"] for m in run["measurements"]}["S4"] == "356"
    assert all(a["observed_holds"] for a in run["assertions"])
    name = run["test_data"]["spot_name"]
    assert name.startswith("VPDT")
    # 按重命名后的名字搜索命中（结果行只在搜索词与收藏名一致时出现），清理后已取消收藏
    s3 = _by(run)["S3"]["sub_steps"]
    go = next(x for x in s3 if x["attempts"][0].get("locate", {}).get("target") == "qk_search_go")
    assert go["outcome"] == "passed"
    state = read_json(ws.runs_dir / run["run_id"] / "replay_state.json")
    assert state["flags"]["faved"] is False and state["flags"]["tag_label"] == "常用"
    assert name not in json.dumps(state, ensure_ascii=False)  # 输入值不落盘到回放状态


def test_102_unclassified_map_cannot_claim_automatic_route_permission(ws):
    run = run_case(ws, "TC-QK-102", device_spec=world("qiankun_hm_ready.json"), propose=False, authorize=AUTH_102)
    map_step = _by(run)["S2"]["sub_steps"][1]
    assert map_step["outcome"] == "blocked"
    assert all(a["policy"]["deny"]["class"] == "task_semantics_unverified" and "dispatch" not in a
               for a in map_step["attempts"])
    assert all(_by(run)[s]["outcome"] == "not_run" for s in ("S3", "S4"))


def test_102_blocked_without_user_data_authorization(ws):
    run = run_case(ws, "TC-QK-102", device_spec=world("qiankun_hm_ready.json"), propose=False,
                   authorize=["vpd_entry", "route_planning"])
    assert run["verdict"]["verdict"] == "blocked"
    assert {u["key"] for u in run["env"]["unmet"]} == {"authorize.user_data"}


def test_102_requires_vehicle_in_vpd_map(ws):
    """收藏/搜索的需求只在车辆位于 VPD 地图范围内时成立：前提写进判据适用范围，用例要求测试人员声明。"""
    kn = KnowledgeStore(ws.root)
    for kid in ("K-QK-USR-003", "K-QK-TG-201"):
        assert kn.oracle_status([kid], {"app": "qiankun_hm"})["status"] == "applicability_unknown"
        assert kn.oracle_status([kid], {"app": "qiankun_hm", "vehicle_in_vpd_map": "no"})["status"] == "out_of_scope"
        assert kn.oracle_status([kid], {"app": "qiankun_hm", "vehicle_in_vpd_map": "yes"})["status"] == "confirmed"
    run = run_case(ws, "TC-QK-102", device_spec=world("qiankun_hm_ready.json"), propose=False, authorize=AUTH_102,
                   env={"vehicle_in_vpd_map": "no"})
    assert run["verdict"]["verdict"] == "blocked"
    assert {u["key"] for u in run["env"]["unmet"]} == {"vehicle_in_vpd_map"}
    assert all(s["outcome"] == "not_run" for s in run["steps"])


def test_103_supervised_lifecycle_pause_background_resume_continue(ws):
    run = run_case(ws, "TC-QK-103", device_spec=world("qiankun_hm_ready.json"), propose=False, authorize=AUTH_103,
                   supervisor="现场监护人A")
    by = _by(run)
    assert all(s["outcome"] == "passed" for s in run["steps"]), _outcomes(run)
    assert [h["event"] for h in run["motion"]] == ["armed", "stopped_observed", "armed", "stopped_observed"]
    assert [h.get("state") for h in run["motion"] if h["event"] == "stopped_observed"] == ["qk_paused", "qk_parked"]
    assert run["safety_stops"] == []
    hits = by["S5"]["monitor"]["hits"]
    assert hits["tor"]["text"] == "前方有行人，请注意（占位）" and hits["moving"]["count"] >= 1 and "fault" not in hits
    assert by["S9"]["resume"] == {"process_alive_before": True, "cold_start": False}
    assert not by["S10"]["attempts"][0].get("skipped")  # 拉回后仍是暂停：真的点了「继续」
    assert by["S11"]["monitor"]["stopped_by"] == "until_state:qk_parked"
    assert {a["id"]: a["observed_holds"] for a in run["assertions"] if not a["advisory"]} == \
        {"A1": True, "A2": True, "A3": True, "A4": True}


def test_103_fault_world_blocks_at_start_without_arming(ws):
    run = run_case(ws, "TC-QK-103", device_spec=world("qiankun_hm_base.json"), propose=False, authorize=AUTH_103,
                   supervisor="现场监护人A", env={"account_state": "logged_in"})
    by = _by(run)
    assert run["verdict"]["verdict"] == "blocked" and run["verdict"]["attribution"] == "environment"
    att = by["S3"]["attempts"][0]
    assert att["status"] == "target_disabled" and "dispatch" not in att
    assert att["enabled_check"]["enabled"] is False and att["enabled_check"]["opacity"] == ["0.400000"]
    assert run["motion"] == [] and run["safety_stops"] == []
    assert all(by[s]["outcome"] == "not_run" for s in ("S4", "S5", "S11"))


def test_103_requires_supervisor(ws):
    run = run_case(ws, "TC-QK-103", device_spec=world("qiankun_hm_ready.json"), propose=False, authorize=AUTH_103)
    assert run["verdict"]["verdict"] == "blocked" and "supervisor" in {u["key"] for u in run["env"]["unmet"]}
    assert run["actions"] == 0


def test_103_vehicle_resuming_by_itself_is_armed_by_observation(ws, tmp_path):
    # 变体：暂停后退后台，拉回时车已自行恢复运动（不点「继续」）→ 看门狗按 moving_states 观测挂上，泊入后解除
    w = load_world(WORLDS / "qiankun_hm_ready.json")
    w["screens"]["qk_paused"]["on_resume"] = "qk_moving_final"
    w.pop("_path", None)
    p = tmp_path / "qiankun_auto_resume.json"
    write_json(p, w)
    run = run_case(ws, "TC-QK-103", device_spec=f"replay:{p}", propose=False, authorize=AUTH_103,
                   supervisor="现场监护人A")
    by = _by(run)
    assert all(s["outcome"] == "passed" for s in run["steps"]), _outcomes(run)
    assert by["S10"]["attempts"][0].get("skipped")  # 页面上没有「继续」：跳过
    assert [(h["event"], h.get("label")) for h in run["motion"]][2] == ("armed", "state:qk_moving")
    assert run["motion"][2]["step"] == "observed" and run["motion"][-1]["state"] == "qk_parked"
    assert run["safety_stops"] == []


def test_qiankun_lint_and_explore_boundary(ws):
    app, kn, pol = ws.app("qiankun_hm"), KnowledgeStore(ws.root), ws.policy()
    # 101 的登录流程已被 auto-vpd 真机校准证伪（C8）：lint 如实指出，不再是"无问题"
    issues101 = lint(ws.load_case("TC-QK-101"), app, kn, pol)
    assert len(issues101) == 1 and "login_password" in issues101[0]["problem"] and "证伪" in issues101[0]["problem"]
    assert lint(ws.load_case("TC-QK-103"), app, kn, pol) == []
    issues = lint(ws.load_case("TC-QK-102"), app, kn, pol)
    assert len(issues) == 1 and "qk_map" in issues[0]["problem"] and "hypothesis" in issues[0]["problem"]
    # 不带显式 grant 也可按任务策略进入 ADS；加载态没有 recipe，覆盖如实停止。
    exp = explore(ws, "qiankun_hm", "qk_car_tab", world("qiankun_hm_base.json"), budget=40)
    assert exp["exit"] == {"reason": "reset_failed", "state": "qk_vpd_loading"}
    assert any(t["label"] == "ADS" and t["to"] == "qk_vpd_loading" for t in exp["transitions"])
    assert "qk_vpd_loading" in exp["coverage"]["unvisited_declared"]
    assert any("没有到达状态 qk_vpd_loading 的 recipe" in a for a in exp["anomalies"])
    dispatches = [e for e in _events(ws, exp) if e["event"] == "dispatch" and e.get("sent")]
    permissions = [e.get("checks", {}).get("tap", {}).get("policy", {}) for e in dispatches]
    assert any(p.get("authorized_by") == "vpd_entry" and p.get("authorization_source") == "task_policy"
               for p in permissions)
    assert not any(p.get("authorized_by") in {"account_state", "vehicle_selection", "user_data",
                                               "vehicle_motion", "vehicle_control"} for p in permissions)


def test_102_resource_pack_popup_is_dismissed_inline(ws, tmp_path):
    """冷启动后盖着「请下载车辆资源包」：执行层在动作前自动点「下次再说」，用例照常通过，不点「立即下载」。"""
    run = run_case(ws, "TC-QK-102", device_spec=_classified_map_world(tmp_path, "qiankun_hm_resource_popup.json"), propose=False,
                   authorize=AUTH_102)
    assert all(s["outcome"] == "passed" for s in run["steps"]), _outcomes(run)
    events = [json.loads(x) for x in (ws.runs_dir / run["run_id"] / "events.jsonl").read_text("utf-8").splitlines()]
    dis = [e for e in events if e["event"] == "interrupt_dismissed"]
    assert dis and all(e["interrupt"] == "qk_vehicle_resource_dialog" and e["ok"] for e in dis)
    taps = [a["locate"].get("target") for s in run["steps"] for x in s.get("sub_steps", [s])
            for a in x["attempts"] if a.get("locate")]
    assert not any("立即下载" in str(t) for t in taps)


def _events(ws, run):
    return [json.loads(x) for x in (ws.runs_dir / run["run_id"] / "events.jsonl").read_text("utf-8").splitlines()]


def _tapped_texts(run):
    return [str(a["locate"].get("target")) for s in run["steps"] for x in s.get("sub_steps", [s])
            for a in x["attempts"] if a.get("locate")]


def test_102_resource_pack_popup_variant_text_is_dismissed(ws, tmp_path):
    """真机出现过的文案变体「请下载更新后的车辆资源包」/「立即更新」也要认出并点「下次再说」。"""
    run = run_case(ws, "TC-QK-102", device_spec=_classified_map_world(tmp_path, "qiankun_hm_resource_popup_v2.json"), propose=False,
                   authorize=AUTH_102)
    assert all(s["outcome"] == "passed" for s in run["steps"]), _outcomes(run)
    dis = [e for e in _events(ws, run) if e["event"] == "interrupt_dismissed"]
    assert dis and dis[0]["interrupt"] == "qk_vehicle_resource_dialog" and dis[0]["ok"]
    assert not any("立即更新" in t for t in _tapped_texts(run))


def test_102_unregistered_popup_with_defer_button_is_dismissed(ws, tmp_path):
    """没登记的弹窗：按 Dialog 结构认出，里面有「以后再说」这类推迟按钮就点掉，不点「去开启」。"""
    run = run_case(ws, "TC-QK-102", device_spec=_classified_map_world(tmp_path, "qiankun_hm_unregistered_popup.json"), propose=False,
                   authorize=AUTH_102)
    assert all(s["outcome"] == "passed" for s in run["steps"]), _outcomes(run)
    dis = [e for e in _events(ws, run) if e["event"] == "interrupt_dismissed"]
    assert dis and dis[0]["interrupt"].startswith("unregistered_dialog:") and dis[0]["ok"]
    assert not any("去开启" in t for t in _tapped_texts(run))


def test_102_unregistered_popup_without_defer_button_is_not_touched(ws):
    """没登记、只有「同意」的弹窗：不自动点，步骤失败时证据里带着弹窗文字。"""
    run = run_case(ws, "TC-QK-102", device_spec=world("qiankun_hm_unknown_blocking_popup.json"), propose=False,
                   authorize=AUTH_102)
    assert not any(e["event"] == "interrupt_dismissed" for e in _events(ws, run))
    assert not any("同意" in t for t in _tapped_texts(run))
    assert run["verdict"]["verdict"] != "passed"
    dialogs = [d for s in run["steps"] for x in s.get("sub_steps", [s]) for a in x["attempts"]
               for d in (a.get("post") or {}).get("dialogs", [])]
    assert any("车辆服务协议已更新" in d["texts"] for d in dialogs)
