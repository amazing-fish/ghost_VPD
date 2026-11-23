"""#234: same-session account condition carry and motor_hm login markers."""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import world

from vpdtest import cases as C
from vpdtest.conditions import evaluate_guard, validate_definitions
from vpdtest.devices.replay import ReplayDevice, load_world
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.tree import UiTree
from vpdtest.util import read_json, write_json


def _s01_like_case():
    return {
        "id": "TC-S01-ACCOUNT-CARRY",
        "version": 1,
        "status": "draft",
        "app": "motor_hm",
        "title": "Synthetic S01 with same_session account guard",
        "purpose": "Execute navigation without --env and guard A1 with my_tab account proof",
        "preconditions": ["synthetic test"],
        "env_requirements": {},
        "test_data": {},
        "test_data_none": True,
        "authorizations": [],
        "conditions": {
            "motor_logged_in": {
                "kind": "account",
                "presence_only": True,
                "carry": "same_session",
                "max_age_s": 120,
                "true": {"type": "target_present", "target": "motor_my_logged_in"},
                "false": {"type": "target_present", "target": "motor_my_logged_out"},
            }
        },
        "steps": [
            {"id": "S1", "action": "launch", "expect_state": "@in_app"},
            {"id": "S1b", "action": "tap", "target": "tab_my", "expect_state": "my_tab"},
            {"id": "S3", "action": "swipe", "direction": "up"},
            {"id": "S4", "action": "tap", "target": "settings_entry", "expect_state": "settings"},
            {"id": "S5", "action": "back", "expect_state": "my_tab"},
        ],
        "assertions": [
            {
                "id": "A1",
                "at": "S4",
                "type": "target_present",
                "target": "logout_entry",
                "desc": "logout entry visible when logged in",
                "oracle_refs": ["K-MOT-002"],
                "guard": {"condition": "motor_logged_in", "equals": True, "at": "S1b"},
            },
            {
                "id": "A2",
                "at": "S5",
                "type": "state_is",
                "state": "my_tab",
                "desc": "back returns to my_tab",
                "oracle_refs": ["K-MOT-001"],
            },
        ],
        "cleanup": [],
        "cleanup_none": True,
        "cleanup_note": "synthetic navigation only",
        "sources": [{"type": "manual", "ref": "test_account_condition_carry synthetic"}],
    }


def _world(tmp_path, name="world.json", *, logged_in=True, with_logout=True, hide_my_account=False):
    data = load_world(world("motor_hm_base.json").split(":", 1)[1])
    data["app_version"] = "3.0.3.200"
    if logged_in:
        def _walk(node):
            yield node
            for child in node.get("children", []):
                yield from _walk(child)
        for node in _walk(data["screens"]["my_tab"]["root"]):
            if node.get("text") == "注册/登录":
                node["text"] = "无关文本" if hide_my_account else "我的车辆"
    if not with_logout:
        for ch in data["screens"]["settings"]["root"]["children"]:
            for sub in ch.get("children", []):
                if sub.get("text") == "退出登录":
                    sub["text"] = "其他设置项"
    p = tmp_path / name
    write_json(p, data)
    return f"replay:{p}"


def test_validate_definitions_checks_carry_and_presence_only_rules(ws):
    app = ws.app("motor_hm")
    valid = {
        "motor_logged_in": {
            "kind": "account",
            "presence_only": True,
            "carry": "same_session",
            "max_age_s": 120,
            "true": {"type": "target_present", "target": "motor_my_logged_in"},
            "false": {"type": "target_present", "target": "motor_my_logged_out"},
        }
    }
    assert validate_definitions(valid, app) == []

    bad_carry = deepcopy(valid)
    bad_carry["motor_logged_in"]["carry"] = "cross_restart"
    assert any("carry" in i["field"] for i in validate_definitions(bad_carry, app))

    non_account = deepcopy(valid)
    non_account["motor_logged_in"].update(kind="page", presence_only=False)
    assert any("carry" in i["field"] for i in validate_definitions(non_account, app))

    missing_scope = deepcopy(valid)
    missing_scope["motor_logged_in"].pop("presence_only")
    assert any("scope" in i["field"] for i in validate_definitions(missing_scope, app))


def test_bare_run_without_env_passes_when_logged_in_and_fails_when_logout_missing(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(ReplayDevice, "env_facts", lambda self: {})
    case = _s01_like_case()
    assert C.lint(case, ws.app("motor_hm"), KnowledgeStore(ws.root), ws.policy()) == []

    ok = run_case(ws, case=case, device_spec=_world(tmp_path, "in.json", logged_in=True, with_logout=True), propose=False)
    assert ok["verdict"]["verdict"] == "passed", ok["verdict"]
    assert ok["verdict"]["coverage"]["verified"] == ["A1", "A2"]
    assert ok["actions"] >= 5

    # Verify S4 landing on Settings with missing logout_entry fails A1 via carried logged-in guard
    fail_case = deepcopy(case)
    fail_case["steps"][3].pop("expect_state")
    fail_case["steps"][3]["expect_text"] = "设置"
    fail = run_case(ws, case=fail_case, device_spec=_world(tmp_path, "missing-logout.json", logged_in=True, with_logout=False), propose=False)
    assert fail["verdict"]["verdict"] == "failed", fail["verdict"]
    assert fail["verdict"]["product_failed"] is True
    assert fail["assertions"][0]["result"] == "fail"
    assert fail["assertions"][1]["result"] == "pass"


def test_bare_run_marks_a1_not_applicable_when_logged_out_even_if_env_declared(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(ReplayDevice, "env_facts", lambda self: {})
    spec = _world(tmp_path, "out.json", logged_in=False)
    for env in (None, {"account_state": "logged_in"}):
        run = run_case(ws, case=_s01_like_case(), device_spec=spec, env=env, propose=False)
        assert run["verdict"]["verdict"] != "blocked" and not run["verdict"]["product_failed"]
        assert run["assertions"][0]["result"] == "not_applicable", run["assertions"][0]
        assert run["assertions"][1]["result"] == "pass", run["assertions"][1]


def test_bare_run_keeps_a1_pending_when_account_markers_absent(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(ReplayDevice, "env_facts", lambda self: {})
    spec = _world(tmp_path, "hidden.json", logged_in=True, hide_my_account=True)
    run = run_case(ws, case=_s01_like_case(), device_spec=spec, propose=False)
    assert run["assertions"][0]["result"] == "pending", run["assertions"][0]
    assert run["assertions"][1]["result"] == "pass", run["assertions"][1]


def test_account_carry_invalidates_across_restart_or_out_of_app_or_expiry_or_contradiction(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(ReplayDevice, "env_facts", lambda self: {})
    restart_case = _s01_like_case()
    restart_case["steps"].insert(2, {"id": "S2b", "action": "launch"})
    run = run_case(ws, case=restart_case, device_spec=_world(tmp_path, "restart.json", logged_in=True), propose=False)
    assert run["assertions"][0]["result"] == "pending"
    assert "会话" in run["assertions"][0]["guard"]["reason"]

    app = ws.app("motor_hm")
    defs = _s01_like_case()["conditions"]
    guard = {"condition": "motor_logged_in", "equals": True, "at": "S1b"}
    obs_in = {
        "obs_id": "obs-0001", "ts": "2026-11-22T00:00:00+08:00", "fresh": True, "source": "replay",
        "foreground": app.bundle, "process_alive": True, "tree": {"status": "ok"},
        "action_epoch": 1, "account_epoch": 0, "clock_s": 10.0,
        "scope": {"app": "motor_hm", "app_version": "3.0.3.200"}, "page": {"state": "my_tab"},
    }
    tree_in = read_json(Path(_world(tmp_path, "w.json", logged_in=True).split(":", 1)[1]))
    def to_ui(screen_name, wdata):
        def cvt(n):
            attrs = {"type": n["t"], "bounds": f"[{n['b'][0]},{n['b'][1]}][{n['b'][2]},{n['b'][3]}]"}
            if "text" in n:
                attrs["text"] = n["text"]
            return {"attributes": attrs, "children": [cvt(c) for c in n.get("children", [])]}
        return UiTree(cvt(wdata["screens"][screen_name]["root"]))
    t_my = to_ui("my_tab", tree_in)
    t_set = to_ui("settings", tree_in)
    t_out = to_ui("my_tab", load_world(world("motor_hm_base.json").split(":", 1)[1]))

    # Expired max_age_s
    obs_expired = {**obs_in, "obs_id": "obs-0002", "clock_s": 200.0, "page": {"state": "settings"}}
    res_exp = evaluate_guard(guard, defs, {"S1b": {"outcome": "passed", "final_obs": "obs-0001"}},
                             lambda _: (obs_in, t_my), app, obs_expired, t_set)
    assert res_exp["status"] == "unknown"

    # Foreground flip (out_of_app)
    obs_fg = {**obs_in, "obs_id": "obs-0003", "foreground": "com.other.app", "clock_s": 15.0, "page": {"state": "out_of_app"}}
    res_fg = evaluate_guard(guard, defs, {"S1b": {"outcome": "passed", "final_obs": "obs-0001"}},
                            lambda _: (obs_in, t_my), app, obs_fg, t_set)
    assert res_fg["status"] == "unknown"

    # Contradiction on target screen (logged_out marker appears)
    obs_contra = {**obs_in, "obs_id": "obs-0004", "clock_s": 15.0, "page": {"state": "my_tab"}}
    res_contra = evaluate_guard(guard, defs, {"S1b": {"outcome": "passed", "final_obs": "obs-0001"}},
                                lambda _: (obs_in, t_my), app, obs_contra, t_out)
    assert res_contra["status"] == "unknown"

    # Anomaly interrupt on target must invalidate even if epoch numerically matched
    obs_anom = {**obs_in, "obs_id": "obs-0005", "clock_s": 15.0,
                "page": {"state": "settings", "interrupts": [{"id": "app_error_dialog", "class": "anomaly"}]}}
    res_anom = evaluate_guard(guard, defs, {"S1b": {"outcome": "passed", "final_obs": "obs-0001"}},
                              lambda _: (obs_in, t_my), app, obs_anom, t_set)
    assert res_anom["status"] == "unknown"


def test_anomaly_observation_records_incremented_account_epoch_and_blocks_carry(ws, tmp_path):
    from vpdtest.observe import RunStore
    from vpdtest.workspace import RunContext

    data = load_world(world("motor_hm_base.json").split(":", 1)[1])
    def _walk(node):
        yield node
        for child in node.get("children", []):
            yield from _walk(child)
    for node in _walk(data["screens"]["my_tab"]["root"]):
        if node.get("text") == "注册/登录":
            node["text"] = "我的车辆"
    for ch in data["screens"]["settings"]["root"]["children"]:
        for sub in ch.get("children", []):
            if sub.get("text") == "退出登录":
                sub["text"] = "应用无响应"
    wpath = tmp_path / "anomaly.json"
    write_json(wpath, data)
    dev = ReplayDevice(wpath)
    store = RunStore(tmp_path / "runs")
    ctx = RunContext(ws, ws.app("motor_hm"), dev, store, {})

    dev.state.update(foreground=ctx.app.bundle, alive=True, stack=["my_tab"])
    obs1, t1 = ctx.observe("clean_my")
    assert obs1["account_epoch"] == 0

    dev.state["stack"] = ["settings"]
    obs2, t2 = ctx.observe("anomaly_settings")
    assert any(i.get("class") == "anomaly" for i in (obs2.get("page") or {}).get("interrupts") or [])
    assert obs2["account_epoch"] == 1
    assert read_json(store.dir / "obs" / obs2["obs_id"] / "observation.json")["account_epoch"] == 1

    defs = _s01_like_case()["conditions"]
    guard = {"condition": "motor_logged_in", "equals": True, "at": "S1b"}
    res = evaluate_guard(guard, defs, {"S1b": {"outcome": "passed", "final_obs": obs1["obs_id"]}},
                         lambda _: (obs1, t1), ctx.app, obs2, t2)
    assert res["status"] == "unknown"


def test_approved_motor_hm_assets_stay_valid_after_234_capability(ws):
    for cid, c_hash, d_hash in [
        ("TC-HM-S01", "220e6c5e32a4", "12d98ffe6920"),
        ("TC-MOTOR-HM-002", "b4de659323c5", "25f15f39c405"),
    ]:
        case = ws.load_case(cid)
        info = C.review_status(ws, case)
        assert info["valid"] is True and info["state"] == "approved"
        assert info["current"]["content_hash"] == c_hash
        assert info["current"]["dependency_hash"] == d_hash


def test_dispatcher_tolerates_stub_ctx_without_account_epoch(ws):
    from types import SimpleNamespace
    from vpdtest.devices import DeviceResult
    from vpdtest.dispatch import Dispatcher

    class _FakeBridge:
        def __init__(self, fg):
            self.fg = fg
            self.calls = 0

        def screen_locked(self):
            return False

        def foreground(self):
            return self.fg

        def tap(self, *args):
            self.calls += 1
            return DeviceResult("tap", True, True)

    app, policy = ws.app("motor_hm"), ws.policy()
    motion = SimpleNamespace(over=lambda: False, locked=lambda: False)
    ctx_disrupted = SimpleNamespace(app=app, policy=policy, authorized=set(), actions=0, safety_hold=None, motion=motion)
    att1 = {"action": "tap"}
    res1 = Dispatcher(_FakeBridge("other.app"), ctx=ctx_disrupted).send(
        "tap", 10, 20, preconditions={"screen_locked": False, "foreground": app.bundle}, att=att1
    )
    assert res1.sent is False and res1.error_class == "foreground_changed"
    assert ctx_disrupted.account_epoch == 1

    ctx_same = SimpleNamespace(app=app, policy=policy, authorized=set(), actions=0, safety_hold=None,
                               motion=motion, clock=lambda: 10.0)
    att2 = {
        "action": "tap",
        "condition": {"status": "matched", "records": [{"carry": "same_session", "account_epoch": 0, "clock_s": 5.0}]},
    }
    bridge2 = _FakeBridge(app.bundle)
    res2 = Dispatcher(bridge2, ctx=ctx_same).send(
        "tap", 10, 20, preconditions={"screen_locked": False, "foreground": app.bundle}, att=att2
    )
    assert res2.sent is False and bridge2.calls == 0
    assert att2["status"] == "condition_unknown"
