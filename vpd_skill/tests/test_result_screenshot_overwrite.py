"""#56：删除非白名单截图文件时保留此前真实采集错误，不扩大截图权限。"""
import json
from pathlib import Path

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_RULE
from vpdtest.devices import DeviceResult
from vpdtest.knowledge import KnowledgeStore
from vpdtest.observe import Observer, RunStore
from vpdtest.verdict import case_verdict, evaluate_assertion


@pytest.mark.parametrize("error_class", ["timeout", "command_failed", None],
                         ids=["partial_timeout", "partial_command_failure", "successful_capture_withheld"])
def test_withholding_deleted_screenshot_preserves_actual_capture_failure(verdict_ws, monkeypatch, error_class):
    app = verdict_ws.app("motor_hm")
    store = RunStore(verdict_ws.runs_dir)
    device = verdict_ws.device(world("motor_hm_base.json"))
    device.state.update(foreground=app.bundle, alive=True, stack=["my_tab"])
    observer = Observer(device, app.bundle, verdict_ws.policy(), app.states, store, display=device.display_info())
    confirm(verdict_ws, COPY_RULE)
    knowledge = KnowledgeStore(verdict_ws.root)
    knowledge.bind({"app": app.id, "account_state": "logged_out"})
    healthy, healthy_tree = observer.observe("independent-required-text")
    required = evaluate_assertion({"id": "A1", "at": "S1", "type": "text_present", "text": "注册/登录",
                                    "oracle_refs": [COPY_RULE]},
                                   {"S1": {"outcome": "passed", "final_obs": healthy["obs_id"]}},
                                   lambda _: (healthy, healthy_tree), knowledge, app)
    assert required["result"] == "pass"
    original_screenshot = device.screenshot
    paths = []
    raw = f"RAW_CAPTURE_{error_class}_BEFORE_WITHHOLDING"

    def capture_then_foreground_change(path):
        paths.append(Path(path))
        if error_class:
            Path(path).write_bytes(b"synthetic partial screenshot bytes")
            result = DeviceResult("screenshot", True, False, error_class,
                                  f"synthetic screenshot {error_class}", raw)
        else:
            result = original_screenshot(path)
        device.state["foreground"] = "synthetic.unallowlisted.app"
        return result

    monkeypatch.setattr(device, "screenshot", capture_then_foreground_change)
    obs, tree = observer.observe("capture-then-unallowlisted-foreground")
    assert obs["fresh"] is False and obs["foreground"] == "synthetic.unallowlisted.app"
    assert obs["screenshot"]["status"] == "withheld"
    assert paths and all(not path.exists() for path in paths)
    visual = evaluate_assertion({"id": "A2", "at": "S2", "type": "visual_review", "advisory": True},
                                {"S2": {"outcome": "passed", "final_obs": obs["obs_id"]}},
                                lambda _: (obs, tree), knowledge, app)
    if error_class:
        error = obs["screenshot"].get("capture_error")
        assert error and error["error_class"] == error_class and error["raw"] == raw
        assert visual["result"] == "error" and visual["error_class"] == error_class
        assert raw in json.dumps(visual, ensure_ascii=False)
    else:
        assert not obs["screenshot"].get("capture_error")
        assert visual["result"] == "pending" and "error_class" not in visual
    verdict = case_verdict({"ok": True}, {"ok": True}, [], [required, visual], knowledge)
    assert verdict["verdict"] == ("error" if error_class else "passed")
    assert verdict["product_verdict"] == "passed" and verdict["product_failed"] is False
    assert verdict["execution"]["status"] == ("error" if error_class else "completed")
