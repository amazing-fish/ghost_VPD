"""Tests for P0 capability_runtime, Executor/Runner capability preconditions, and backward compatibility."""
from __future__ import annotations

import json
import pytest

from test_login_capability import _build_login_world
from test_vpd_nav_capabilities import _nav_world
from vpdtest import cases as cases_mod
from vpdtest import cli
from vpdtest.capability_runtime import (
    CapabilityRuntime,
    validate_capability_spec,
    validate_secret_key,
    validate_timeout,
    validate_vin_tail,
)
from vpdtest.devices.replay import ReplayDevice
from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import RunContext


def _base_case(case_id: str = "TC-CAP-HOOK-001", *, requires_capabilities=None, authorizations=None, steps=None, cleanup=None):
    return {
        "id": case_id,
        "version": 1,
        "status": "draft",
        "app": "qiankun_hm",
        "title": "能力前置钩子测试用合成用例",
        "purpose": "验证 L2/L3 能力前置与 L4 业务断言隔离",
        "preconditions": ["能力前置由 requires_capabilities 声明"],
        "env_requirements": {},
        "test_data": {},
        "test_data_none": True,
        "authorizations": authorizations if authorizations is not None else ["vpd_entry"],
        **({"requires_capabilities": requires_capabilities} if requires_capabilities is not None else {}),
        "steps": steps if steps is not None else [
            {"id": "S1", "action": "wait", "duration_s": 0, "expect_state": "qk_vpd_home", "desc": "核对已在 VPD 首页"},
        ],
        "assertions": [
            {"id": "A1", "type": "state_is", "state": "qk_vpd_home", "at": "S1", "advisory": False,
             "oracle_refs": ["K-QK-TC-059"], "desc": "核对当前处于 VPD 首页"},
        ],
        "cleanup": cleanup if cleanup is not None else [
            {"id": "C1", "action": "stop_app", "desc": "退出测试应用"},
        ],
        "sources": [{"type": "unit_test", "ref": "synthetic"}],
    }


def test_capability_runtime_validators():
    assert validate_secret_key(None, "QIANKUN_PHONE", "--account-secret") == "QIANKUN_PHONE"
    assert validate_vin_tail("000228") == "000228"
    assert validate_timeout(15, max_s=60.0) == 15.0
    with pytest.raises(ValueError):
        validate_secret_key("lowercase", "QIANKUN_PHONE", "--account-secret")
    with pytest.raises(ValueError):
        validate_vin_tail("bad-vin")
    with pytest.raises(ValueError):
        validate_timeout(float("nan"), max_s=60.0)


def test_capability_runtime_poll_until_and_scroll_helpers(ws, tmp_path, monkeypatch):
    spec = _nav_world(tmp_path, start="car_tab", ads_target="sms_verify")
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    dev.launch(app.bundle, app.ability)
    rt = CapabilityRuntime.open(
        ws, app, dev,
        kind="unit_cap", capability="unit_cap", prefix="uc",
        granted={"vpd_entry"},
    )
    ok, _ = rt.send_tap("tap_ads", "qk_ads", *rt.observe_fast("uc_init")[::-1])
    assert ok is True

    # Challenge revealed on poll must return sms_required rather than timeout even if clock expires immediately
    now = [0.0]
    import vpdtest.capability_runtime as cr_mod
    monkeypatch.setattr(cr_mod.time, "monotonic", lambda: now[0])
    rt.sleep = lambda _: now.__setitem__(0, now[0] + 50.0)
    res = rt.poll_until("uc_poll", 1.0, 0.2, lambda o, t, k, d, pn: None, replay_max_polls=2)
    assert res["status"] == "sms_required" and res["action_required"] is True

    # scroll_to_edge stops at bottom after 2 unchanged swipes on static screen
    spec2 = _nav_world(tmp_path, start="veh_list")
    dev2 = ws.device(spec2)
    dev2.launch(app.bundle, app.ability)
    rt2 = CapabilityRuntime.open(
        ws, app, dev2, kind="unit_scroll", capability="unit_scroll", prefix="us", granted=set(),
    )
    obs2, tree2 = rt2.observe_fast("us_init")
    hit, _, _, halted = rt2.scroll_to_edge("up", step_prefix="edge", obs_prefix="us_edge", obs=obs2, tree=tree2)
    assert hit is True and halted is None


def test_runner_requires_capabilities_chains_all_three_and_shares_budget(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900008888")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd888")
    orig_key = ReplayDevice.key
    monkeypatch.setattr(
        ReplayDevice, "key",
        lambda d, name: d._res("key_back") if name == "back" and d.current_screen() == "password" else orig_key(d, name),
    )
    spec = _nav_world(tmp_path, start="logged_out_dialog", post_vin_screen="car_tab_k89")
    case = _base_case(
        requires_capabilities=[
            {"capability": "logged_in"},
            {"capability": "vehicle_vin", "vin_tail": "000228"},
            {"capability": "vpd_ready"},
        ],
        authorizations=["account_state", "vehicle_selection", "vpd_entry"],
    )
    run = run_case(ws, case=case, device_spec=spec, propose=False,
                   authorize=["account_state", "vehicle_selection", "vpd_entry"])
    caps = run.get("capability_preconditions") or []
    assert [c["capability"] for c in caps] == ["logged_in", "vehicle_vin", "vpd_ready"]
    assert all(c["outcome"] == "passed" for c in caps)
    assert run["steps"][0]["outcome"] == "passed"
    assert run["verdict"]["product_verdict"] == "passed"
    assert run["actions"] >= sum(c["actions"] for c in caps)
    events = read_jsonl(ws.runs_dir / run["run_id"] / "events.jsonl")
    assert any(e.get("event") == "capability_precondition_done" and e.get("capability") == "vpd_ready" for e in events)


def test_runner_capability_precondition_blocks_preserve_screen_and_never_mark_product_fail(ws, tmp_path, capsys):
    # 1) vpd_ready hits location_permission_required -> blocked + action_required + CLI exit 2, cleanup NOT executed
    spec_loc = _nav_world(tmp_path, start="car_tab", ads_target="vpd_location_off")
    case = _base_case(
        case_id="TC-CAP-LOC-OFF",
        requires_capabilities=[{"capability": "vpd_ready"}],
        authorizations=["vpd_entry"],
    )
    write_json(ws.case_path(case["id"]), case)
    rc = cli.main(["--workspace", str(ws.root), "run", case["id"], "--device", spec_loc])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out["verdict"] == "blocked"
    assert out["action_required"]
    assert out.get("human_handoffs")

    run_data = read_json(ws.runs_dir / out["run_id"] / "run.json")
    assert run_data["verdict"]["product_verdict"] == "pending"
    assert run_data["verdict"]["product_failed"] is False
    assert run_data["capability_preconditions"][0]["capability_status"] == "location_permission_required"
    assert run_data["steps"][0]["outcome"] == "not_run"
    assert run_data["steps"][0]["final_status"] == "capability_precondition_failed"
    assert run_data["cleanup"] == []
    events = read_jsonl(ws.runs_dir / out["run_id"] / "events.jsonl")
    assert not any(e.get("event") == "dispatch" and e.get("op") == "stop_app" for e in events)

    # 2) vpd_unavailable -> attribution environment, blocked (no action_required)
    spec_unavail = _nav_world(tmp_path, start="car_tab", ads_target="vpd_charging_only")
    run_unavail = run_case(ws, case=case, device_spec=spec_unavail, propose=False)
    assert run_unavail["verdict"]["verdict"] == "blocked"
    assert run_unavail["verdict"]["attribution"] == "environment"
    assert run_unavail["verdict"]["product_verdict"] == "pending"
    assert run_unavail["verdict"]["product_failed"] is False
    assert run_unavail["cleanup"] == []


def test_runner_capability_precondition_sms_and_indeterminate_error(ws, tmp_path, monkeypatch):
    # 1) sms_required preserves screen and does not run cleanup
    spec_sms = _nav_world(tmp_path, start="car_tab", ads_target="sms_verify")
    case = _base_case(requires_capabilities=["vpd_ready"], authorizations=["vpd_entry"])
    run_sms = run_case(ws, case=case, device_spec=spec_sms, propose=False)
    assert run_sms["verdict"]["verdict"] == "blocked"
    assert run_sms["verdict"]["action_required"]
    assert run_sms["capability_preconditions"][0]["capability_status"] == "sms_required"
    assert run_sms["cleanup"] == []

    # 2) indeterminate query error in capability maps to verdict == "error" (not failed)
    orig_preflight = ReplayDevice.preflight
    orig_locked = ReplayDevice.screen_locked
    after_preflight = {"ready": False}

    def mark_preflight(self, bundle):
        res = orig_preflight(self, bundle)
        after_preflight["ready"] = True
        return res

    def query_fails_on_cap(self):
        if after_preflight["ready"]:
            self.screen_query_error = {"error_class": "transport_unavailable", "error": "hdc dropped"}
            return None
        return orig_locked(self)

    monkeypatch.setattr(ReplayDevice, "preflight", mark_preflight)
    monkeypatch.setattr(ReplayDevice, "screen_locked", query_fails_on_cap)
    spec_ok = _nav_world(tmp_path, start="car_tab")
    run_err = run_case(ws, case=case, device_spec=spec_ok, propose=False)
    assert run_err["verdict"]["verdict"] == "error"
    assert run_err["verdict"]["attribution"] == "environment"
    assert run_err["verdict"]["product_verdict"] == "pending"
    assert run_err["verdict"]["product_failed"] is False


def test_runner_vpd_ready_does_not_auto_login_unless_explicit_and_enforces_authorize(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900008888")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd888")
    spec = _nav_world(tmp_path, start="logged_out")

    # Even when account_state is both requested on the case AND granted via CLI (so ctx.authorized
    # actually holds account_state), vpd_ready with auto_login omitted/False must stop at login_required.
    case_no_auto = _base_case(
        requires_capabilities=[{"capability": "vpd_ready"}],
        authorizations=["vpd_entry", "account_state"],
    )
    run = run_case(ws, case=case_no_auto, device_spec=spec, propose=False, authorize=["account_state"])
    assert "account_state" in run["authorized"]
    assert run["verdict"]["verdict"] == "blocked"
    assert run["capability_preconditions"][0]["capability_status"] == "login_required"

    # Missing required --authorize vehicle_selection fails closed at env check with 0 actions
    case_vin = _base_case(
        requires_capabilities=[{"capability": "vehicle_vin", "vin_tail": "000228"}],
        authorizations=["vehicle_selection"],
    )
    run_unauth = run_case(ws, case=case_vin, device_spec=_nav_world(tmp_path, start="car_tab"), propose=False, authorize=[])
    assert run_unauth["verdict"]["verdict"] == "blocked"
    assert run_unauth["actions"] == 0
    assert any(u["key"] == "authorize.vehicle_selection" for u in run_unauth["env"]["unmet"])


def test_executor_capability_step_and_budget_exhausted_attribution(ws, tmp_path, monkeypatch):
    # Step-level action=capability with ${var:vin_tail} param resolution + safety_hold guard
    spec = _nav_world(tmp_path, start="car_tab", post_vin_screen="car_tab_k89")
    case = _base_case(
        authorizations=["vehicle_selection", "vpd_entry"],
        steps=[
            {"id": "S1", "action": "capability", "capability": "vehicle_vin", "vin_tail": "${var:target_vin}"},
            {"id": "S2", "action": "capability", "capability": "vpd_ready"},
        ],
        cleanup=[],
    )
    case["test_data"] = {"target_vin": "000228"}
    case["test_data_none"] = False
    case["assertions"][0]["at"] = "S2"
    run = run_case(ws, case=case, device_spec=spec, propose=False, authorize=["vehicle_selection"])
    assert [s["outcome"] for s in run["steps"]] == ["passed", "passed"]
    assert run["verdict"]["product_verdict"] == "passed"

    # Budget exhaustion inside capability attributes to tool
    pol = read_json(ws.root / "policy.json")
    pol["max_actions_per_run"] = 1
    write_json(ws.root / "policy.json", pol)
    case_bud = _base_case(
        requires_capabilities=[{"capability": "vpd_ready"}],
        authorizations=["vpd_entry"],
    )
    run_bud = run_case(ws, case=case_bud, device_spec=_nav_world(tmp_path, start="car_tab"), propose=False)
    assert run_bud["verdict"]["verdict"] == "blocked"
    assert run_bud["verdict"]["attribution"] == "tool"

    # safety_hold blocks Executor.run_capability before dispatching
    app = ws.app("qiankun_hm")
    dev = ws.device(spec)
    from vpdtest.observe import RunStore
    store = RunStore(ws.runs_dir, kind="run")
    ctx = RunContext(ws, app, dev, store, None, mode="regression")
    ctx.safety_hold = {"trigger": "unconfirmed"}
    res = Executor(ctx).run_capability({"capability": "vpd_ready"}, step_id="CAP1")
    assert res["outcome"] == "blocked" and res["final_status"] == "safety_hold" and res["action_required"]


def test_capability_validation_lint_and_zero_hash_drift_on_existing_cases(ws):
    app_qk = ws.app("qiankun_hm")
    app_motor = ws.app("motor_hm")
    kn = KnowledgeStore(ws.root)

    assert validate_capability_spec({"capability": "unknown_cap"}, "qiankun_hm", "req[0]")
    assert validate_capability_spec({"capability": "vpd_ready"}, "motor_hm", "req[0]")
    assert validate_capability_spec({"capability": "logged_in", "account_secret": "13800138000"}, "qiankun_hm", "req[0]")
    assert validate_capability_spec({"capability": "vehicle_vin"}, "qiankun_hm", "req[0]")

    # safe_stop may not call action=capability; missing authorizations caught by lint
    bad_case = _base_case(
        requires_capabilities=[{"capability": "logged_in"}],
        authorizations=[],
    )
    bad_case["safety"] = {"max_motion_s": 60, "safe_stop": [{"action": "capability", "capability": "vpd_ready"}]}
    issues = cases_mod.lint(bad_case, app_qk, kn, ws.policy())
    assert any("safe_stop" in x["field"] for x in issues)
    assert any(x["field"] == "authorizations" and "account_state" in x["problem"] for x in issues)

    # Existing workspace cases remain structurally clean and keep identical hashes
    for p in sorted((ws.root / "cases").glob("*.json")):
        c = read_json(p)
        app = app_qk if c["app"] == "qiankun_hm" else app_motor
        assert cases_mod.capability_issues(c, app) == []
        assert cases_mod.content_hash(c) == cases_mod.content_hash({k: v for k, v in c.items() if k != "requires_capabilities"})

    # Capability failure also stops missing_secret cleanly through login_app reuse
    spec_lo = _build_login_world(ws.runs_dir, start="logged_out")
    assert spec_lo.startswith("replay:")


def test_coderabbit_p0_security_and_edge_fixes(ws, tmp_path, monkeypatch):
    from vpdtest.dispatch import Dispatcher
    from vpdtest.devices import DeviceResult
    from vpdtest.login import login_app

    # 1. Timeout validation rejects None and 401-digit integer without raising OverflowError
    assert validate_capability_spec({"capability": "vpd_ready", "timeout_s": None}, "qiankun_hm", "req[0]")
    assert validate_capability_spec({"capability": "vpd_ready", "timeout_s": 10**450}, "qiankun_hm", "req[0]")
    with pytest.raises(ValueError, match="timeout_s"):
        validate_timeout(10**450, max_s=60.0)

    # 2. Successful already-ready capability step preserves final_obs for state_is assertions
    spec_ready = _nav_world(tmp_path, start="vpd_home")
    case_already = _base_case(
        authorizations=["vpd_entry"],
        steps=[{"id": "S1", "action": "capability", "capability": "vpd_ready"}],
        cleanup=[],
    )
    run_already = run_case(ws, case=case_already, device_spec=spec_ready, propose=False)
    assert run_already["steps"][0]["final_obs"]
    assert run_already["assertions"][0]["result"] == "pass"

    # 3. Secret-backed vin_tail (direct & flow alias) is rejected without leaking secret value
    secret_vin = "SECRET999"
    monkeypatch.setenv("VPD_SECRET_VIN_SECRET", secret_vin)
    case_sec_vin = _base_case(
        authorizations=["vehicle_selection"],
        steps=[{"id": "S1", "action": "capability", "capability": "vehicle_vin", "vin_tail": "${var:vin_ref}"}],
        cleanup=[],
    )
    case_sec_vin["test_data"] = {"vin_ref": {"secret": "VIN_SECRET"}}
    case_sec_vin["test_data_none"] = False
    case_sec_vin["assertions"] = []
    run_sec = run_case(ws, case=case_sec_vin, device_spec=_nav_world(tmp_path, start="car_tab"),
                       propose=False, authorize=["vehicle_selection"])
    assert run_sec["steps"][0]["final_status"] == "capability_invalid_param"
    assert secret_vin not in json.dumps(run_sec, ensure_ascii=False)
    assert secret_vin not in (ws.runs_dir / run_sec["run_id"] / "events.jsonl").read_text(encoding="utf-8")

    # Flow alias pointing to secret-marked test_data is also rejected
    app_qk = ws.app("qiankun_hm")
    app_qk.flows["cap_vin_alias"] = {
        "params": {"vin": "vin tail"},
        "steps": [{"action": "capability", "capability": "vehicle_vin", "vin_tail": "${param:vin}"}],
    }
    monkeypatch.setattr(ws, "app", lambda _: app_qk)
    case_flow_sec = _base_case(
        authorizations=["vehicle_selection"],
        steps=[{"id": "F1", "action": "flow", "flow": "cap_vin_alias", "params": {"vin": "${var:vin_ref}"}}],
        cleanup=[],
    )
    case_flow_sec["test_data"] = {"vin_ref": {"secret": "VIN_SECRET"}}
    case_flow_sec["test_data_none"] = False
    case_flow_sec["assertions"] = []
    run_flow_sec = run_case(ws, case=case_flow_sec, device_spec=_nav_world(tmp_path, start="car_tab"),
                            propose=False, authorize=["vehicle_selection"])
    assert run_flow_sec["steps"][0]["sub_steps"][0]["final_status"] == "capability_invalid_param"
    assert secret_vin not in json.dumps(run_flow_sec, ensure_ascii=False)

    # 4. Terminal nested execution_error propagates to capability outcome="error"
    orig_send = Dispatcher.send
    def fail_tap_exec(self, op, *args, preconditions=None, att=None, tap=None, transient=None):
        if att and att.get("step_id") == "ev_tap_ads":
            att.update(status="device_error", execution_error={
                "stage": "dispatch", "error_class": "transport_unavailable",
                "attribution": "environment", "error": "hdc lost",
            })
            return DeviceResult(op, False, False, "transport_unavailable", "hdc lost")
        return orig_send(self, op, *args, preconditions=preconditions, att=att, tap=tap, transient=transient)

    with monkeypatch.context() as mp:
        mp.setattr(Dispatcher, "send", fail_tap_exec)
        case_tap_err = _base_case(requires_capabilities=["vpd_ready"], authorizations=["vpd_entry"])
        run_tap_err = run_case(ws, case=case_tap_err, device_spec=_nav_world(tmp_path, start="car_tab"), propose=False)
        assert run_tap_err["verdict"]["verdict"] == "error"
        assert run_tap_err["capability_preconditions"][0]["execution_error"]["error_class"] == "transport_unavailable"

    # 5. Non-critical inline capability sms_required halts business steps & defers ordinary cleanup
    spec_sms = _nav_world(tmp_path, start="car_tab", ads_target="sms_verify")
    case_inline_sms = _base_case(
        authorizations=["vpd_entry"],
        steps=[
            {"id": "S1", "action": "capability", "capability": "vpd_ready", "critical": False},
            {"id": "S2", "action": "wait", "duration_s": 0, "desc": "must not run"},
        ],
        cleanup=[{"id": "C1", "action": "stop_app"}],
    )
    case_inline_sms["assertions"] = []
    run_inline_sms = run_case(ws, case=case_inline_sms, device_spec=spec_sms, propose=False)
    assert run_inline_sms["steps"][1]["outcome"] == "not_run"
    assert run_inline_sms["cleanup"] == []
    assert run_inline_sms["verdict"]["action_required"]

    # 6. scroll_to_edge refuses to confirm edge on unusable trees, and checks initial screen for challenges
    rt_edge = CapabilityRuntime.open(
        ws, app_qk, ws.device(_nav_world(tmp_path, start="sms_verify")),
        kind="unit_chk", capability="unit_chk", prefix="uk", granted=set(),
    )
    rt_edge.device.launch(app_qk.bundle, app_qk.ability)
    o_sms, t_sms = rt_edge.observe_fast("uk_sms")
    hit_sms, _, _, halted_sms = rt_edge.scroll_to_edge("up", step_prefix="e", obs_prefix="o", obs=o_sms, tree=t_sms)
    assert hit_sms is False and halted_sms is not None and halted_sms["status"] == "sms_required"

    dev_unobs = ws.device(_nav_world(tmp_path, start="veh_list"))
    dev_unobs.launch(app_qk.bundle, app_qk.ability)
    rt_unobs = CapabilityRuntime.open(
        ws, app_qk, dev_unobs, kind="unit_unobs", capability="unit_unobs", prefix="uu", granted=set(),
    )
    o0, t0 = rt_unobs.observe_fast("uu_0")
    orig_obs_fast = rt_unobs.observe_fast
    monkeypatch.setattr(rt_unobs, "observe_fast",
                        lambda lbl: ({"obs_id": "obs-bad", "fresh": False, "page": {"state": "undetermined"}}, None))
    hit_unobs, _, _, _ = rt_unobs.scroll_to_edge("up", step_prefix="e", obs_prefix="o", max_swipes=3, obs=o0, tree=t0)
    assert hit_unobs is False
    monkeypatch.setattr(rt_unobs, "observe_fast", orig_obs_fast)

    # 7. login_app stops immediately if focusing phone opens sms_verify
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PHONE", "13900001111")
    monkeypatch.setenv("VPD_SECRET_QIANKUN_PASSWORD", "SyntheticPwd111")
    spec_pwd = _build_login_world(tmp_path, start="password", unchecked_protocol=False)
    p_pwd = spec_pwd.split("replay:", 1)[1]
    w_pwd = read_json(p_pwd)
    w_pwd["screens"]["password"]["root"]["children"][2]["go"] = "sms"
    write_json(p_pwd, w_pwd)
    dev_pwd = ws.device(spec_pwd)
    dev_pwd.launch(app_qk.bundle, app_qk.ability)
    res_focus_sms = login_app(ws, app_qk, dev_pwd, authorize=["account_state"])
    assert res_focus_sms["status"] == "sms_required" and res_focus_sms["action_required"] is True
    assert not any(s.get("step") == "input_phone" for s in res_focus_sms["steps"])

    # 8. Composite flow optional capability handoff stops flow & propagates action_required
    app_qk.flows["cap_opt_handoff"] = {
        "params": {},
        "steps": [
            {"action": "capability", "capability": "vpd_ready", "critical": False},
            {"action": "wait", "duration_s": 0},
        ],
    }
    case_comp = _base_case(
        authorizations=["vpd_entry"],
        steps=[{"id": "F1", "action": "flow", "flow": "cap_opt_handoff", "params": {}}],
        cleanup=[],
    )
    case_comp["assertions"] = []
    run_comp = run_case(ws, case=case_comp, device_spec=spec_sms, propose=False)
    assert len(run_comp["steps"][0]["sub_steps"]) == 1
    assert run_comp["steps"][0]["action_required"]
