"""A reliable product anomaly does not imply an unknown physical dispatch."""

import pytest
from test_goal_loop import _lab_world, _start, cli
from test_retry_dispatch_safety import _context, _counts, _step

from vpdtest import cases
from vpdtest.execute import Executor
from vpdtest.knowledge import KnowledgeStore
from vpdtest.session import dispatched, effect_unknown, from_session, trail
from vpdtest.util import read_json, write_json

ANOMALY = "合成产品异常提示"
ANOMALY_SPEC = {"class": "anomaly", "review": "reviewed", "require_text": [ANOMALY]}


@pytest.mark.parametrize("retry", [0, 2])
def test_acknowledged_tap_with_reliable_anomaly_has_known_effect(ws, tmp_path, retry):
    ctx = _context(ws, tmp_path)
    root = ctx.device.world["screens"]["fixture"]["root"]
    root["children"][1]["set_flag"] = {"product_fault": True}
    root["children"].append({"t": "Text", "text": ANOMALY, "show_if_flag": "product_fault",
                             "b": [40, 900, 1280, 1000]})
    ctx.app.states.interrupts["fixture_anomaly"] = ANOMALY_SPEC
    result = Executor(ctx).run_step(_step(retry=retry), "known-anomaly-fixture")
    attempt = result["attempts"][0]
    assert _counts(ctx.device)["tap"] == len(result["attempts"]) == 1
    assert attempt["dispatch"]["sent"] is True and attempt["dispatch"]["executor_ok"] is True
    assert attempt["post"]["anomalies"][0]["id"] == "fixture_anomaly"
    assert result["outcome"] == "fail_candidate" and result["attribution"] == "product"
    assert result["final_status"] == result["first_failure"]["status"] == "anomaly"
    assert result["action_required"]
    assert dispatched(result) is True
    assert effect_unknown(result) is False
    assert not result.get("effect_unknown", False)


def test_anomaly_session_conversion_does_not_require_unknown_effect_review(ws, tmp_path):
    spec = _lab_world(tmp_path, "known-anomaly", {"detail": {"root": {
        "t": "Column", "b": [0, 0, 1320, 2848], "children": [
            {"t": "Text", "text": "详情页", "b": [40, 200, 1280, 300]},
            {"t": "Text", "text": ANOMALY, "b": [40, 400, 1280, 500]},
        ],
    }}})
    states_path = ws.root / "apps" / "motor_hm" / "states.json"
    states = read_json(states_path)
    states["interrupts"]["fixture_anomaly"] = ANOMALY_SPEC
    write_json(states_path, states)
    _start(ws, spec, "known-anomaly")
    output = cli(ws, "act", "tap", "--select", "text=唯一入口", "--app", "motor_hm",
                 "--session", "known-anomaly", "--device", spec, "--timeout", "0")
    result = output["result"]
    assert result["outcome"] == "fail_candidate" and result["final_status"] == "anomaly"
    assert len(result["attempts"]) == 1 and result["action_required"]
    sdir = ws.runs_dir / "session-known-anomaly"
    candidate = from_session(sdir, "TC-KNOWN-ANOMALY", ws.app("motor_hm"), None, ["A1=启动不属于异常验证"])
    issues = cases.lint(candidate, ws.app("motor_hm"), KnowledgeStore(ws.root))
    assert not [issue for issue in issues if "已下发但结果未确认" in issue["problem"]]
    kept = candidate["steps"][0]
    assert kept["from_session"]["outcome"] == "fail_candidate"
    assert kept["from_session"]["effect_unknown"] is False
    assert not kept.get("effect_reviewed")
    item = next(item for item in trail(sdir)["items"] if item.get("step_id") == "A2")
    assert item["dispatched"] is True and item["effect_unknown"] is False


def test_acknowledged_tap_with_failed_observation_keeps_unknown_effect(ws, tmp_path):
    ctx = _context(ws, tmp_path)
    tap = ctx.device.tap

    def tap_then_failed_query(x, y):
        result = tap(x, y)
        ctx.device._failed_foreground_queries = 2
        return result

    ctx.device.tap = tap_then_failed_query
    result = Executor(ctx).run_step(_step(retry=0), "known-anomaly-fixture")
    assert _counts(ctx.device)["tap"] == len(result["attempts"]) == 1
    assert result["attempts"][0]["dispatch"]["sent"] is True
    assert result["attempts"][0]["dispatch"]["executor_ok"] is True
    assert result["final_status"] == "observation_failed"
    assert effect_unknown(result) is True and result["action_required"]
    assert "未执行动作" not in result["reason"]
