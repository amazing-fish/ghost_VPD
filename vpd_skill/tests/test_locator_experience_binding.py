"""An index is meaningful only within the locator definition that produced it."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from vpdtest.experience import ExperienceStore, Overlay
from vpdtest.locate import locator_fingerprint
from vpdtest.util import write_json


def seed_real_legacy(store):
    source = Path(__file__).resolve().parents[1] / "data/experience/motor_hm/items.json"
    old = next(x for x in json.loads(source.read_text(encoding="utf-8"))["items"]
               if x["id"] == "XP-20261013-101449-def5")
    store.save_item(old)


def item(target):
    return {"id": "XP-binding", "kind": "locator_preference", "status": "validated",
            "requires_review": False, "history": [], "validations": [],
            "change": {"target": "tab_my", "prefer_strategy_index": 1,
                       "locator_fingerprint": locator_fingerprint(target)}}


def test_real_legacy_candidate_does_not_reinterpret_index_after_navigation_fix(ws):
    store = ExperienceStore(ws.root, "motor_hm")
    seed_real_legacy(store)
    before = store.items_path.read_bytes()
    candidate = store.get("XP-20261013-101449-def5")
    assert candidate["change"]["prefer_strategy_index"] == 1
    target = ws.app("motor_hm").targets["tab_my"]
    assert store.overlay(trial=candidate["id"]).locator_pref("tab_my", "sm/portrait", target) is None
    with pytest.raises(PermissionError, match="策略绑定"):
        store.validate(candidate["id"], [], [], lambda *a: pytest.fail("must not run"))
    with pytest.raises(PermissionError, match="策略绑定"):
        store.adopt(candidate["id"])
    assert store.items_path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["reorder", "remove", "scope", "when", "value"])
def test_bound_index_rejects_changed_definition(ws, mutation):
    target = deepcopy(ws.app("motor_hm").targets["tab_my"])
    candidate = item(target)
    overlay = Overlay([candidate])
    assert overlay.locator_pref("tab_my", target=target) == 1
    changed = deepcopy(target)
    if mutation == "reorder": changed["strategies"].reverse()
    elif mutation == "remove": changed["strategies"].pop(0)
    elif mutation == "scope": changed["strategies"][1]["region"] = [0, 0, 1, 1]
    elif mutation == "when": changed["strategies"][1]["when"] = {"wide": True}
    else: changed["strategies"][1]["value"] = "other"
    assert overlay.locator_pref("tab_my", target=changed) is None
    assert overlay.describe()["locator_ignored"]["tab_my"] == "definition_changed_or_invalid_index"
    store = ExperienceStore(ws.root, "motor_hm")
    store.save_item(candidate)
    write_json(store.targets_path, {"targets": {"tab_my": changed}})
    before = store.items_path.read_bytes()
    with pytest.raises(PermissionError): store.adopt(candidate["id"])
    assert store.items_path.read_bytes() == before


def test_valid_bound_candidate_adopts_without_rewriting_old_history(ws):
    store = ExperienceStore(ws.root, "motor_hm")
    target = ws.app("motor_hm").targets["tab_my"]
    candidate = item(target)
    store.save_item(candidate)
    seed_real_legacy(store)
    before = store.get("XP-20261013-101449-def5")
    store.adopt(candidate["id"])
    assert store.overlay().locator_pref("tab_my", target=target) == 1
    assert store.get("XP-20261013-101449-def5") == before


@pytest.mark.parametrize("fingerprint,pref", [(None, 1), ("different", 1), ("correct", None), ("correct", True)])
def test_trial_that_did_not_apply_binding_cannot_validate(ws, fingerprint, pref):
    store = ExperienceStore(ws.root, "motor_hm")
    candidate = item(ws.app("motor_hm").targets["tab_my"])
    candidate.update(status="candidate", origin_case="origin", evidence=[])
    store.save_item(candidate)
    def run(cid, dev, overlay):
        loc = {"target": "tab_my", "adapted": not overlay.trial,
               "locator_fingerprint": candidate["change"]["locator_fingerprint"] if fingerprint == "correct" else fingerprint,
               "overlay_pref": pref}
        return {"run_id": cid + str(bool(overlay.trial)), "verdict": {"verdict": "passed"},
                "steps": [{"attempts": [{"locate": loc}]}]}
    assert store.validate(candidate["id"], ["origin", "independent"], ["synthetic"], run)["status"] == "candidate"
    with pytest.raises(PermissionError): store.adopt(candidate["id"])
