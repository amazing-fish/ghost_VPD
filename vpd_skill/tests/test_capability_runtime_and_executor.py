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

    # Even if --authorize account_state is passed, vpd_ready without auto_login=True must stop at login_required
    case_no_auto = _base_case(requires_capabilities=[{"capability": "vpd_ready"}], authorizations=["vpd_entry"])
    run = run_case(ws, case=case_no_auto, device_spec=spec, propose=False, authorize=["account_state"])
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
