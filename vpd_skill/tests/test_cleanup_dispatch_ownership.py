"""A failed baseline must not remove an existing user's tag; sends still require cleanup."""
from copy import deepcopy

import pytest
from conftest import world

from vpdtest.execute import Executor
from vpdtest.runner import cleanup_dispatch_owned, run_case


@pytest.mark.parametrize("dispatch,clear,expected", [
    (None, None, False), ({"sent": False}, None, False),
    ({"sent": True, "executor_ok": True}, None, True),
    ({"sent": True, "executor_ok": False}, None, True),
    ({"sent": None}, None, True), ({"sent": False}, {"sent": True}, True),
])
def test_ownership_comes_from_primary_mutation_or_clear_even_when_effect_is_unknown(dispatch, clear, expected):
    attempt = {"locate": {"scroll": [{"sent": True}]}, "dismissed": [{"sent": True}]}
    if dispatch is not None:
        attempt["dispatch"] = dispatch
    if clear is not None:
        attempt["clear"] = clear
    assert cleanup_dispatch_owned([{"step_id": "WRITE", "attempts": [attempt]}], "WRITE") is expected
    assert cleanup_dispatch_owned([{"step_id": "OTHER", "attempts": [attempt]}], "WRITE") is False


def asset(ws):
    case = ws.load_case("TC-MOTOR-HM-001")
    target = {"label": "synthetic tag", "side_effect": "user_data",
              "strategies": [{"kind": "text", "value": "synthetic tag"}]}
    case.update(id="TC-OWNERSHIP", env_requirements={}, authorizations=["user_data"], assertions=[],
                steps=[{"id": "BASELINE", "action": "wait", "duration_s": 0},
                       {"id": "WRITE", "action": "tap", "target": target}],
                cleanup=[{"id": "RESTORE", "action": "tap", "target": deepcopy(target), "after_dispatch": "WRITE"},
                         {"id": "STOP", "action": "stop_app"}])
    return case


@pytest.mark.parametrize("baseline,send,expected", [(False, True, False), (True, False, False),
                                                    (True, True, True), (True, None, True)])
def test_runner_does_not_restore_preexisting_data_but_cleans_after_possible_write(ws, monkeypatch, baseline, send, expected):
    called = []
    def execute(self, step, case_id):
        called.append(step["id"])
        outcome = "fail_candidate" if step["id"] == "BASELINE" and not baseline else "passed"
        attempts = [{"dispatch": {"sent": send, "executor_ok": send is True}}] if step["id"] == "WRITE" else []
        return {"step_id": step["id"], "action": step["action"], "critical": True, "outcome": outcome,
                "reason": "synthetic ownership scenario",
                "attempts": attempts, "recoveries": [], "adapted": False}
    monkeypatch.setattr(Executor, "run_step", execute)
    result = run_case(ws, case=asset(ws), device_spec=world("motor_hm_base.json"), propose=False, authorize=["user_data"])
    assert ("RESTORE" in called) is expected
    assert "STOP" in called
    restore = result["cleanup"][0]
    assert restore["cleanup_guard"]["may_have_changed_user_data"] is expected
    if not expected:
        assert restore["outcome"] == "skipped" and restore["required"] is False


@pytest.mark.parametrize("owner", ["MISSING", "BASELINE", None, {}, ""])
def test_invalid_cleanup_owner_is_rejected_before_any_device_access(ws, monkeypatch, owner):
    case = asset(ws)
    case["cleanup"][0]["after_dispatch"] = owner
    monkeypatch.setattr(ws, "device", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must validate first")))
    with pytest.raises(ValueError, match="after_dispatch"):
        run_case(ws, case=case, device_spec=world("motor_hm_base.json"), propose=False, authorize=["user_data"])
