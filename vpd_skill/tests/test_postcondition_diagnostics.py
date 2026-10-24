"""#89: state-signature diagnostics explain observations, never infer environment causes.

Synthetic Replay cases exercise the real Executor, Verdict and run report. No
production rule is confirmed and no physical device is used.
"""
from pathlib import Path

import pytest

from test_retry_dispatch_safety import _context, _step
from test_retry_recovery_feedback import _cover
from verdict_fixtures import SETTINGS_RULE
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

ANCHOR = "搜索车位号设为目的地"


def _case_world(ws, tmp_path, texts, *, signature=None, expected="diagnostic_goal", oracle=False):
    path = ws.app("motor_hm").dir / "states.json"
    states = read_json(path)
    states["states"]["diagnostic_goal"] = signature or {"require_text": [ANCHOR]}
    states["states"]["diagnostic_loading"] = {"require_text": ["正在加载..."]}
    write_json(path, states)
    bundle = ws.app("motor_hm").bundle
    root = lambda children: {"root": {"t": "Column", "b": [0, 0, 1264, 1848], "children": children}}
    world = {"bundle": bundle, "app_version": "3.0.3.200", "screen": [1264, 1848],
             "initial": {"foreground": bundle, "installed": True}, "launch_screen": "home",
             "display": {"density": 2.75, "device_form": "foldable"}, "screens": {
                 "home": root([{"t": "Button", "text": "进入", "click": True,
                                "b": [40, 200, 800, 350], "go": "after"}]),
                 "after": root([{"t": "Text", "text": text, "b": [20, 100 + i * 60, 1200, 150 + i * 60]}
                                for i, text in enumerate(texts)])}}
    world_path = tmp_path / "postcondition-diagnostic-world.json"
    write_json(world_path, world)
    if oracle:
        KnowledgeStore(ws.root).confirm(SETTINGS_RULE, reviewer="pytest-synthetic", basis="synthetic diagnostic test")
    case = {"id": "TC-TEST-STATE-DIAGNOSTIC", "version": 1, "status": "draft", "app": "motor_hm",
            "title": "Synthetic state-signature diagnostic", "purpose": "diagnostic only", "env_requirements": {},
            "steps": [{"id": "S1", "action": "launch"},
                      {"id": "S2", "action": "tap", "target": {"side_effect": "navigation", "strategies": [
                          {"kind": "text", "value": "进入"}]}, "expect_state": expected, "timeout_s": 0, "retry": 0,
                       "oracle_refs": [SETTINGS_RULE] if oracle else []}],
            "assertions": [], "cleanup": [{"action": "stop_app"}]}
    return case, f"replay:{world_path}"


def _run(ws, tmp_path, texts, **kwargs):
    case, spec = _case_world(ws, tmp_path, texts, **kwargs)
    return run_case(ws, case=case, device_spec=spec, propose=False)


@pytest.mark.parametrize("texts,state", [(["附近充电站", "停泊位置", "ADS"], "unknown"),
                                        (["ADS"], "unknown"), (["正在加载..."], "diagnostic_loading")])
def test_missing_anchor_diagnostics_reach_findings_and_report_without_environment_inference(verdict_ws, tmp_path, texts, state):
    run = _run(verdict_ws, tmp_path, texts)
    step = run["steps"][1]
    diagnostic = step["state_diagnostics"]
    assert step["outcome"] == "fail_candidate" and step["attribution"] == "product"
    assert step["final_status"] == "postcondition_not_met" and step["action_required"]
    assert diagnostic["diag_only"] and diagnostic["expected"] == "diagnostic_goal"
    assert diagnostic["signature"] == {"require_text": [ANCHOR]}
    assert diagnostic["observed"]["state"] == state
    assert diagnostic["obs"] == step["final_obs"] and diagnostic["tree"]["status"] == "ok"
    assert diagnostic["require_text"] == {"scope": "whole_window_visible_text", "available": True,
                                           "required": [ANCHOR], "present": [], "missing": [ANCHOR]}
    assert "environment" not in {run["verdict"]["attribution"], step["attribution"]}
    finding = next(f for f in run["verdict"]["findings"] if f.get("step") == "S2")
    assert finding["evidence"]["state_diagnostics"] == diagnostic
    assert step["first_failure"]["detail"]["post"]["state_diagnostics"] == diagnostic
    report = (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")
    assert "状态诊断" in report and ANCHOR in report and diagnostic["obs"] in report
    assert "非环境证明" in report


def test_require_text_evidence_does_not_replace_other_signature_constraints(verdict_ws, tmp_path):
    signature = {"require_text": [ANCHOR], "forbid_text": ["禁止测试标记"]}
    run = _run(verdict_ws, tmp_path, [ANCHOR, "禁止测试标记"], signature=signature)
    step = run["steps"][1]
    diagnostic = step["state_diagnostics"]
    assert step["final_status"] == "postcondition_not_met"
    assert diagnostic["signature"] == signature and diagnostic["observed"]["state"] == "unknown"
    assert diagnostic["require_text"]["present"] == [ANCHOR]
    assert diagnostic["require_text"]["missing"] == []


@pytest.mark.parametrize("failure,outcome,status", [("unavailable", "blocked", "unobservable"),
                                                    ("collection", "error", "unobservable")])
def test_unavailable_tree_does_not_turn_unknown_text_into_missing_anchors(verdict_ws, tmp_path, monkeypatch,
                                                                         failure, outcome, status):
    original = ReplayDevice.dump_tree
    def dump(device, bundle, path):
        if device.current_screen() == "after":
            return DeviceResult("dump_tree", True, False, "tree_unavailable" if failure == "unavailable" else "timeout",
                                "synthetic tree diagnostic failure")
        return original(device, bundle, path)
    monkeypatch.setattr(ReplayDevice, "dump_tree", dump)
    run = _run(verdict_ws, tmp_path, ["ADS"])
    step = run["steps"][1]
    diagnostic = step["state_diagnostics"]
    assert step["outcome"] == outcome and step["attribution"] == "tool" and step["final_status"] == status
    assert diagnostic["observed"]["state"] == "undetermined"
    assert diagnostic["require_text"]["available"] is False
    assert diagnostic["require_text"]["present"] is None and diagnostic["require_text"]["missing"] is None
    assert diagnostic["tree"]["status"] == ("unavailable" if failure == "unavailable" else "error")
    assert step["action_required"]
    finding = next(f for f in run["verdict"]["findings"] if f.get("step") == "S2")
    assert finding["evidence"]["state_diagnostics"] == diagnostic
    assert "状态诊断" in (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")


def test_successful_state_has_no_failure_diagnostic(verdict_ws, tmp_path):
    run = _run(verdict_ws, tmp_path, [ANCHOR])
    step = run["steps"][1]
    assert step["outcome"] == "passed"
    assert "state_diagnostics" not in step and "state_diagnostics" not in step["attempts"][0]["post"]


def test_state_without_required_text_does_not_claim_all_anchors_missing(verdict_ws, tmp_path):
    run = _run(verdict_ws, tmp_path, ["ADS"], signature={"require_text_pattern": ["^目标模式$"]})
    diagnostic = run["steps"][1]["state_diagnostics"]
    assert diagnostic["require_text"]["required"] == diagnostic["require_text"]["present"] == diagnostic["require_text"]["missing"] == []
    assert "未声明require_text" in run["steps"][1]["reason"]


@pytest.mark.parametrize("expected", ["@in_app", "unregistered_state"])
def test_builtin_and_unregistered_states_do_not_invent_signature_diagnostics(verdict_ws, tmp_path, expected):
    run = _run(verdict_ws, tmp_path, ["ADS"], expected=expected)
    step = run["steps"][1]
    assert "state_diagnostics" not in step and "state_diagnostics" not in step["attempts"][0]["post"]


def test_confirmed_oracle_preserves_product_failure(verdict_ws, tmp_path):
    run = _run(verdict_ws, tmp_path, ["ADS"], oracle=True)
    assert run["verdict"]["verdict"] == "failed" and run["verdict"]["attribution"] == "product"
    assert run["steps"][1]["state_diagnostics"]["diag_only"] and run["steps"][1]["action_required"]


@pytest.mark.parametrize("succeeds", [False, True])
def test_final_diagnostic_uses_recovery_observation_and_keeps_first_failure(ws, tmp_path, succeeds):
    ctx = _context(ws, tmp_path)
    ctx.app.states.states["diagnostic_goal"] = {"require_text": [ANCHOR]}
    input_text = ctx.device.input_text
    def input_then_cover(x, y, text):
        result = input_text(x, y, text)
        _cover(ctx)
        return result
    ctx.device.input_text = input_then_cover
    expected = "retry_fixture" if succeeds else "diagnostic_goal"
    step = _step("input", clear=True, retry=1, expect_state=expected, expect_text="新值" if succeeds else None)
    result = Executor(ctx).run_step(step, "synthetic-state-diagnostic-recovery")
    first = result["first_failure"]["detail"]["post"]["state_diagnostics"]
    recovery = result["recoveries"][0]["post"]
    assert first["require_text"]["available"] is False and first["obs"] != recovery["obs_id"]
    if succeeds:
        assert result["outcome"] == "passed_after_recovery" and "state_diagnostics" not in result
        assert "state_diagnostics" not in recovery
    else:
        final = result["state_diagnostics"]
        assert final == recovery["state_diagnostics"] and final["obs"] == result["final_obs"]
        assert final["require_text"]["available"] and final["require_text"]["missing"] == [ANCHOR]
        assert final["obs"] in result["reason"] and first["obs"] not in result["reason"]


@pytest.mark.parametrize("required", [True, False])
def test_cleanup_diagnostic_preserves_required_and_optional_semantics(verdict_ws, tmp_path, required):
    case, spec = _case_world(verdict_ws, tmp_path, [ANCHOR], oracle=True)
    path = verdict_ws.app("motor_hm").dir / "states.json"
    states = read_json(path)
    states["states"]["cleanup_goal"] = {"require_text": ["清理终态"]}
    write_json(path, states)
    case["assertions"] = [{"id": "A1", "at": "S2", "type": "state_is", "state": "diagnostic_goal",
                            "oracle_refs": [SETTINGS_RULE]}]
    case["cleanup"] = [{"id": "C1", "action": "wait", "duration_s": 0, "expect_state": "cleanup_goal",
                        "timeout_s": 0, "retry": 0, "required": required}]
    run = run_case(verdict_ws, case=case, device_spec=spec, propose=False)
    cleanup = run["cleanup"][0]
    diagnostic = cleanup["state_diagnostics"]
    assert cleanup["outcome"] == "fail_candidate" and cleanup["attribution"] == "product"
    assert diagnostic["obs"] == cleanup["final_obs"]
    assert diagnostic["require_text"]["missing"] == ["清理终态"]
    assert run["verdict"]["product_verdict"] == "passed"
    findings = [f for f in run["verdict"]["findings"] if f["kind"] == "cleanup"]
    if required:
        assert run["verdict"]["verdict"] == "blocked" and run["verdict"]["attribution"] == "product"
        assert len(findings) == 1 and findings[0]["cleanup_required"] is True
        assert findings[0]["evidence"]["state_diagnostics"] == diagnostic
    else:
        assert run["verdict"]["verdict"] == "passed" and run["verdict"]["attribution"] is None
        assert findings == []  # Optional cleanup does not acquire a new blocking finding.
    report = (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")
    assert "状态诊断" in report and "清理终态" in report and diagnostic["obs"] in report
