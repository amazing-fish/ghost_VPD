"""Issue #282: motor_hm vehicle resource dialog dismissal, tightened update_dialog, and dialog_policy."""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import world
from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json

BASE_WORLD = Path(__file__).resolve().parents[1] / "fixtures" / "worlds" / "motor_hm_base.json"


def _popup_world(tmp_path: Path, name: str, title: str, left_btn: str, right_btn: str | None = None,
                 *, left_dismiss: bool = True) -> str:
    data = deepcopy(read_json(BASE_WORLD))
    # Mirror real obs-0008 car_tab texts that must never falsely trigger update_dialog
    # or motor_vehicle_resource_dialog.
    data["screens"]["car_tab"]["root"]["children"].extend([
        {"t": "Text", "text": "更新于 11/21 01:44", "b": [508, 1450, 860, 1497]},
        {"t": "Button", "text": "下载车辆资源", "b": [457, 1211, 862, 1313], "click": True},
    ])
    buttons = [{
        "t": "Button",
        "b": [80, 2571, 658, 2714],
        "click": True,
        **({"go": "@dismiss"} if left_dismiss else {}),
        "children": [{"t": "Text", "id": "dialogBtn_0", "text": left_btn, "b": [80, 2571, 658, 2714]}],
    }]
    if right_btn is not None:
        buttons.append({
            "t": "Button",
            "b": [662, 2571, 1240, 2714],
            "click": True,
            "children": [{"t": "Text", "id": "dialogBtn_1", "text": right_btn, "b": [662, 2571, 1240, 2714]}],
        })
    data["screens"][name] = {
        "kind": "popup",
        "root": {
            "t": "Dialog",
            "b": [0, 132, 1320, 2848],
            "click": True,
            "children": [
                {"t": "Text", "text": title, "b": [80, 2466, 1240, 2537]},
                *buttons,
            ],
        },
    }
    data["launch_popup"] = name
    path = tmp_path / f"{name}.json"
    write_json(path, data)
    return f"replay:{path}"


def _tapped_targets(run: dict) -> list[str]:
    out = []
    for s in run["steps"]:
        for sub in s.get("sub_steps", [s]):
            for a in sub.get("attempts", []):
                if a.get("locate"):
                    out.append(str(a["locate"].get("target")))
            for d in sub.get("dismissed", []):
                out.append(str(d.get("target")))
    return out


@pytest.mark.parametrize("title,action_btn", [
    ("请下载车辆资源包", "现在下载"),
    ("请下载更新后的车辆资源包", "立即更新"),
])
def test_motor_vehicle_resource_dialog_is_dismissed_inline_without_hitting_update_dialog(
    ws, tmp_path, title, action_btn
):
    spec = _popup_world(tmp_path, "motor_resource_popup", title, "下次再说", action_btn)
    run = run_case(ws, "TC-MOTOR-HM-002", device_spec=spec, propose=False, require_asset_review=True)
    assert run["verdict"]["verdict"] == "passed", run["verdict"]
    assert all(a["result"] == "pass" for a in run["assertions"]), run["assertions"]
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    dismissed = [e for e in events if e["event"] == "interrupt_dismissed"]
    assert dismissed and all(
        e["interrupt"] == "motor_vehicle_resource_dialog" and e["ok"] for e in dismissed
    ), dismissed
    obs = read_json(Path(run["dir"]) / "obs" / dismissed[0]["obs"] / "observation.json")
    assert [i["id"] for i in obs["page"]["interrupts"]] == ["motor_vehicle_resource_dialog"]
    taps = _tapped_targets(run)
    assert not any(action_btn in t for t in taps), taps


def test_motor_unregistered_popup_with_safe_defer_button_is_dismissed_via_dialog_policy(ws, tmp_path):
    spec = _popup_world(tmp_path, "motor_unregistered_defer", "春季活动推荐", "以后再说", "去看看")
    run = run_case(ws, "TC-MOTOR-HM-002", device_spec=spec, propose=False, require_asset_review=True)
    assert run["verdict"]["verdict"] == "passed", run["verdict"]
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    dismissed = [e for e in events if e["event"] == "interrupt_dismissed"]
    assert dismissed and dismissed[0]["interrupt"].startswith("unregistered_dialog:") and dismissed[0]["ok"]
    taps = _tapped_targets(run)
    assert not any("去看看" in t for t in taps), taps


def test_motor_unregistered_popup_without_safe_defer_button_is_not_touched(ws, tmp_path):
    spec = _popup_world(tmp_path, "motor_unregistered_consent", "隐私政策更新", "同意", None, left_dismiss=False)
    run = run_case(ws, "TC-MOTOR-HM-002", device_spec=spec, propose=False, require_asset_review=True)
    assert run["verdict"]["verdict"] != "passed", run["verdict"]
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert not [e for e in events if e["event"] == "interrupt_dismissed"]
    taps = _tapped_targets(run)
    assert not any("同意" in t for t in taps), taps


def test_tightened_update_dialog_still_matches_app_update_and_blocks_while_pending(ws):
    run = run_case(ws, "TC-MOTOR-HM-002", device_spec=world("motor_hm_popup.json"),
                   propose=False, require_asset_review=True)
    assert run["verdict"]["verdict"] == "blocked", run["verdict"]
    assert run["verdict"]["attribution"] == "requirement_unclear"
    s1 = run["steps"][0]
    launch = next(sub for sub in s1["sub_steps"] if sub["action"] == "launch")
    assert [i["id"] for i in launch["attempts"][0]["post"]["interrupts"]] == ["update_dialog"]
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert not [e for e in events if e["event"] == "interrupt_dismissed"]
