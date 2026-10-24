"""Observed VIN suffix spellings must share identity and ambiguity semantics."""
from copy import deepcopy

import pytest

from vpdtest.locate import resolve
from vpdtest.params import Params
from vpdtest.tree import UiTree


def _vehicles(*labels):
    return UiTree({"attributes": {"type": "Column", "bounds": "[0,0][500,800]"},
                   "children": [{"attributes": {"type": "Text", "text": label,
                                  "clickable": "true", "bounds": f"[0,{i*100}][480,{i*100+80}]"}}
                                for i, label in enumerate(labels)]})


@pytest.mark.parametrize("suffix", ["后6位", "后六位"])
def test_vehicle_list_and_parameterized_target_recognize_vin_spelling(ws, suffix):
    app = ws.app("qiankun_hm")
    tree = _vehicles(f"车架号 000228({suffix})", f"车架号 002685({suffix})")
    assert app.states.identify(tree, app.bundle, True)["state"] == "qk_vehicle_list"
    target = Params({}, {}, "vin-test").subst(deepcopy(app.targets["qk_car_by_vin"]), {"vin_tail": "0228"})
    found = resolve("qk_car_by_vin", target, tree, None, app.dir)
    assert found["status"] == "found"
    assert found["attempts"][0]["node"]["text"] == f"车架号 000228({suffix})"
    page = app.states.identify(tree, app.bundle, True)
    sources = app.states.confirmation_sources(tree, page=page)
    assert sources[0]["candidates"][0]["rules"] == [{"pattern": "车架号", "class": "vehicle_selection"}]


@pytest.mark.parametrize("vin_tail,other", [("0228", "110228"), ("000228", "000228")])
def test_mixed_spelling_duplicate_suffix_remains_ambiguous(ws, vin_tail, other):
    app = ws.app("qiankun_hm")
    tree = _vehicles("车架号 000228(后6位)", f"车架号 {other}(后六位)")
    target = Params({}, {}, "vin-test").subst(deepcopy(app.targets["qk_car_by_vin"]), {"vin_tail": vin_tail})
    assert resolve("qk_car_by_vin", target, tree, None, app.dir)["status"] == "ambiguous"


@pytest.mark.parametrize("label", ["车架号 00228(后六位)", "车架号 0000228(后六位)",
                                  "订单号 000228(后六位)", "车架号 000228(后四位)"])
def test_other_labels_do_not_identify_vehicle_list(ws, label):
    app = ws.app("qiankun_hm")
    assert app.states.identify(_vehicles(label), app.bundle, True)["state"] == "unknown"
