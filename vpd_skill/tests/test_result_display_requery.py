"""Display metadata failures remain evidence without invalidating an independent UI tree."""
from types import SimpleNamespace

from conftest import confirm
from verdict_fixtures import COPY_RULE
from vpdtest.devices import DeviceResult
from vpdtest.devices.replay import expand
from vpdtest.knowledge import KnowledgeStore
from vpdtest.observe import Observer, RunStore
from vpdtest.states import StateModel
from vpdtest.util import read_json, read_jsonl
from vpdtest.verdict import case_verdict, evaluate_assertion


def test_display_requery_error_retains_raw_while_independent_text_remains_decisive(verdict_ws):
    confirm(verdict_ws, COPY_RULE)
    app = verdict_ws.app("motor_hm")
    store = RunStore(verdict_ws.runs_dir)
    error = {"error_class": "timeout", "error": "synthetic rotated display query timeout",
             "raw": "raw-prefix" * 100 + "DISPLAY_REQUERY_RAW_TAIL", "query": "display_dump"}
    displays = [{"width_px": 400, "height_px": 800, "density": 2, "source": "synthetic display"},
                {"density": None, "source": "synthetic display", "execution_error": error}]
    sizes = [(400, 800), (800, 400)]
    calls = []

    def display():
        calls.append("display_info")
        return displays.pop(0)

    def dump(bundle, path):
        width, height = sizes.pop(0)
        root = expand({"t": "Column", "b": [0, 0, width, height], "children": [
            {"t": "Text", "b": [20, 20, 380, 60], "text": "独立可见产品文本"}]})
        return DeviceResult("dump_tree", True, True, data={"tree": root})

    device = SimpleNamespace(serial="synthetic-display-requery", kind="replay",
                             foreground=lambda: app.bundle, process_alive=lambda bundle: True,
                             dump_tree=dump, display_info=display)
    states = StateModel({"states": {"synthetic_ready": {"require_text": ["独立可见产品文本"]}}}, app.bundle)
    observer = Observer(device, app.bundle, verdict_ws.policy(), states, store)
    first, _ = observer.observe("before-resize", screenshot=False)
    second, tree = observer.observe("after-resize", screenshot=False)
    assert calls == ["display_info", "display_info"]
    assert not first.get("display_error")
    assert second["display_error"] == error
    persisted = read_json(store.dir / "obs" / second["obs_id"] / "observation.json")
    assert persisted["display_error"]["raw"].endswith("DISPLAY_REQUERY_RAW_TAIL")
    assert any(event["event"] == "display_requery" for event in read_jsonl(store.events_path))
    assert second["fresh"] is True and second["tree"]["status"] == "ok"

    knowledge = KnowledgeStore(verdict_ws.root).bind({"app": "motor_hm", "account_state": "logged_out"})
    step = {"step_id": "S1", "outcome": "passed", "final_obs": second["obs_id"],
            "attempts": [], "recoveries": []}
    assertion = {"id": "A1", "at": "S1", "type": "text_present", "text": "独立可见产品文本",
                 "oracle_refs": [COPY_RULE]}
    evaluated = evaluate_assertion(assertion, {"S1": step}, lambda obs_id: (second, tree), knowledge, app)
    assert evaluated["result"] == "pass"
    verdict = case_verdict({"ok": True}, {"ok": True}, [step], [evaluated], knowledge)
    assert verdict["verdict"] == verdict["product_verdict"] == "passed"
    assert verdict["execution"] == {"status": "completed", "errors": []}
