"""TC-QK-230 return navigation on synthetic worlds, never real-device evidence."""
import pytest

from conftest import WORLDS
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import load_world
from vpdtest.execute import Executor
from vpdtest.experience import ExperienceStore
from vpdtest.observe import RunStore
from vpdtest.params import Params
from vpdtest.util import write_json
from vpdtest.workspace import RunContext


@pytest.mark.parametrize("keyboard_open", [False, True])
def test_230_reaches_second_search_without_backing_out_of_home(ws, tmp_path, monkeypatch, keyboard_open):
    fixture = load_world(WORLDS / "qiankun_hm_ready.json")
    fixture.pop("_path", None)
    # Existing synthetic search screen predates the production page signature.
    fixture["screens"]["qk_search"]["root"]["children"].append(
        {"t": "Text", "b": [20, 260, 900, 295], "text": "输入目的地或车位号"})
    path = tmp_path / "search-return.json"
    write_json(path, fixture)
    app = ws.app("qiankun_hm")
    store = RunStore(ws.runs_dir, "session-search-return", kind="session")
    device = ws.device(f"replay:{path}")
    device.state.update(alive=True, foreground=app.bundle, stack=["qk_vpd_home", "qk_search"])
    original_key, backs = device.key, []

    def key(name):
        if name == "back":
            backs.append(device.current_screen())
            if keyboard_open and len(backs) == 1:
                return DeviceResult("key_back", sent=True, ok=True, raw="synthetic keyboard dismissed")
        return original_key(name)

    monkeypatch.setattr(device, "key", key)
    case = ws.load_case("TC-QK-230")
    ctx = RunContext(ws, app, device, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    ctx.params = Params(case["test_data"], {}, store.run_id)
    executor = Executor(ctx)
    results = []
    for step in case["steps"][7:]:
        result = executor.run_step(step, case["id"])
        results.append(result)
        assert result["outcome"] == "passed", (step["id"], result)
    assert device.current_screen() == "qk_search"
    assert device.bound["search_q"] == case["test_data"]["fav_waypoint"]
    assert "qk_vpd_home" not in backs
    # Once S8 reached home, navigation has no dispatch or recipe side effects.
    if not keyboard_open:
        assert results[1]["attempts"] == []
        assert not results[1].get("sub_steps")
