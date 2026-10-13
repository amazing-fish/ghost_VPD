"""A live process after a confirmed stop differs from an unknown process query."""

import pytest
from test_retry_dispatch_safety import _context, _counts

from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.session import dispatched, effect_unknown


@pytest.mark.parametrize("alive_after_stop", [True, None])
def test_confirmed_stop_ack_preserves_process_query_certainty(ws, tmp_path, alive_after_stop):
    ctx = _context(ws, tmp_path)
    process_alive = ctx.device.process_alive
    stopped_command = [False]

    def acknowledged_but_ineffective_stop(bundle):
        # Count an issued transport command with a reliable ACK, without
        # applying the normal ReplayDevice process kill. Observer and _post
        # must determine what actually happened to the target process.
        result = ctx.device._physical(
            "stop_app", (bundle,), lambda: DeviceResult("stop_app", True, True),
        )
        stopped_command[0] = True
        return result

    def process_query(bundle):
        if stopped_command[0] and alive_after_stop is None:
            ctx.device.last_query_error = "transport_unavailable"
            return None
        return process_alive(bundle)

    ctx.device.stop = acknowledged_but_ineffective_stop
    ctx.device.process_alive = process_query
    result = Executor(ctx).run_step({
        "id": "STOP1", "action": "stop_app", "retry": 0, "timeout_s": 0,
    }, "stop-effect-certainty-fixture")
    attempt = result["attempts"][0]
    assert _counts(ctx.device)["stop_app"] == 1
    assert len(result["attempts"]) == 1
    assert attempt["dispatch"]["sent"] is True and attempt["dispatch"]["executor_ok"] is True
    assert attempt["stop_check"]["process_alive"] is alive_after_stop
    assert result["final_status"] == result["first_failure"]["status"] == "stop_not_confirmed"
    assert not result["outcome"].startswith("passed")
    assert dispatched(result) is True and result["action_required"]
    expected_unknown = alive_after_stop is None
    checks = {
        "step certainty retained": bool(result.get("effect_unknown")) is expected_unknown,
        "session certainty retained": effect_unknown(result) is expected_unknown,
    }
    assert all(checks.values()), checks
