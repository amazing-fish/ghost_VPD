"""A preserved earlier error must not erase a retry's uncertain auxiliary gesture."""

from copy import deepcopy
from pathlib import Path

from test_condition_runner import APP, condition_rig

from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import ReplayDevice
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

__all__ = ("condition_rig",)


def test_retained_first_error_keeps_latest_unknown_effect_handoff(
    condition_rig, monkeypatch
):
    ws, original, world = condition_rig
    case = deepcopy(original)
    app = ws.app(APP)
    targets = read_json(app.dir / "targets.json")
    targets["targets"]["dismiss"] = {
        "desc": "合成关闭",
        "side_effect": "dismiss_dialog",
        "strategies": [{"kind": "text", "value": "合成稍后"}],
    }
    write_json(app.dir / "targets.json", targets)
    states = read_json(app.dir / "states.json")
    states["interrupts"]["synthetic"] = {
        "require_text": ["合成覆盖物"],
        "class": "benign",
        "review": "reviewed",
        "dismiss_target": "dismiss",
    }
    write_json(app.dir / "states.json", states)
    spec = world()
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    data["screens"]["cover"] = {
        "root": {
            "t": "Column",
            "b": [0, 0, 400, 800],
            "children": [
                {"t": "Text", "text": "合成覆盖物", "b": [10, 480, 350, 530]},
                {
                    "t": "Button",
                    "text": "合成稍后",
                    "click": True,
                    "b": [10, 580, 350, 640],
                },
            ],
        }
    }
    write_json(path, data)

    def key(self, name):
        self.state["popup"] = "cover"
        return DeviceResult(
            "key",
            False,
            False,
            "command_failed",
            "first original command error",
            raw="first original receipt",
        )

    def tap(self, x, y):
        return DeviceResult(
            "tap",
            None,
            False,
            "timeout",
            "dismiss possibly sent",
            raw="second unknown receipt",
        )

    monkeypatch.setattr(ReplayDevice, "key", key)
    monkeypatch.setattr(ReplayDevice, "tap", tap)
    case["steps"].append(
        {
            "id": "S2",
            "action": "back",
            "retry": 1,
            "when": {"condition": "fault", "equals": True},
        }
    )
    run = run_case(ws, case=case, device_spec=spec, propose=False)
    step = run["steps"][1]
    assert step["outcome"] == "error"
    assert step["attempts"][-1]["status"] == "preparation_error"
    assert step["attempts"][-1]["dismissed"][0]["sent"] is None
    assert step.get("effect_unknown"), step
    assert step.get("action_required"), step
    assert run["verdict"].get("action_required"), run["verdict"]
    assert run["verdict"]["execution"]["status"] == "error"
    assert run["verdict"]["execution"]["errors"][0]["stage"] == "prepare_interrupt"
    assert "first original receipt" in str(run["verdict"]["execution"]["errors"])
    assert "second unknown receipt" in (Path(run["dir"]) / "events.jsonl").read_text(encoding="utf-8")
