"""#64 blank Canvas keypad: synthetic trees and HDC transport, never a real device."""
import copy
import json

import pytest

from vpdtest.devices import DeviceResult, create_device
from vpdtest.devices.harmony_hdc import BLANK_PIN_LAYOUT, HdcDevice, find_blank_pin_pad
from vpdtest.tree import UiTree
from vpdtest.unlock import wake_unlock
from vpdtest.workspace import Workspace


def _node(bounds, *, clickable=False, children=(), **attrs):
    return {"attributes": {"type": "Stack", "bounds": "[%d,%d][%d,%d]" % tuple(bounds),
                           "clickable": str(clickable).lower(), "visible": "true", "enabled": "true", **attrs},
            "children": list(children)}


def _blank():
    cells = [_node((150 + c * 300, 1300 + r * 250, 350 + c * 300, 1500 + r * 250), clickable=True)
             for r in range(4) for c in range(3)]
    return _node((0, 0, 1320, 2848), children=cells)


def _device(monkeypatch, tree=None, *, layout=None):
    device = HdcDevice("synthetic-device", pin_pad_layout=layout)
    state = {"tree": tree if tree is not None else _blank(), "failure": None}
    calls = []

    def shell(*args, op, **kwargs):
        calls.append((op, args))
        if op == "lock_dump" and state["failure"] == "dump":
            return DeviceResult(op, True, False, "timeout", "synthetic dump timeout", "BLANK_DUMP_RAW")
        if op == "lock_dump_read":
            if state["failure"] == "cat":
                return DeviceResult(op, True, False, "command_failed", "synthetic cat failure", "BLANK_CAT_RAW")
            return DeviceResult(op, True, True, raw="INVALID_BLANK_JSON_RAW" if state["failure"] == "json"
                                else json.dumps(state["tree"]))
        return DeviceResult(op, True, True)

    monkeypatch.setattr(device, "_shell", shell)
    return device, state, calls


def test_blank_grid_only_with_explicit_layout(monkeypatch):
    device, _, _ = _device(monkeypatch)
    assert device.lock_pin_pad() is None and device.pin_pad_source is None
    device.pin_pad_layout = BLANK_PIN_LAYOUT
    pad = device.lock_pin_pad()
    assert pad == {str(i + 1): (250 + (i % 3) * 300, 1400 + (i // 3) * 250) for i in range(9)} | {"0": (550, 2150)}
    assert device.pin_pad_source == "configured_geometry"
    assert (250, 2150) not in pad.values() and (850, 2150) not in pad.values()


@pytest.mark.parametrize("dx,dy,valid", [(-1000, 0, False), (0, -2000, False), (100, 200, True)])
def test_shifted_root_rejects_negative_cells_but_accepts_positive_origin(monkeypatch, dx, dy, valid):
    tree = _blank()
    for node in UiTree(tree).nodes:
        left, top, right, bottom = node.bounds
        node.attrs["bounds"] = "[%d,%d][%d,%d]" % (left + dx, top + dy, right + dx, bottom + dy)
    device, _, _ = _device(monkeypatch, tree, layout=BLANK_PIN_LAYOUT)
    pad = device.lock_pin_pad()
    if valid:
        assert pad["1"] == (250 + dx, 1400 + dy) and pad["0"] == (550 + dx, 2150 + dy)
        assert device.pin_pad_source == "configured_geometry"
    else:
        assert pad is None and device.pin_pad_source is None


@pytest.mark.parametrize("label_field", ["text", "originalText"])
def test_labels_take_priority_over_geometry(monkeypatch, label_field):
    tree = _blank()
    for cell, digit in zip(tree["children"][:9] + [tree["children"][10]], "1234567890"):
        cell["children"] = [_node((0, 0, 10, 10))]
        # Label bounds stay within the key, independently of field choice.
        cell["children"][0]["attributes"].update(bounds=cell["attributes"]["bounds"], **{label_field: digit})
    device, _, _ = _device(monkeypatch, tree, layout=BLANK_PIN_LAYOUT)
    assert device.lock_pin_pad()["0"] == (550, 2150)
    assert device.pin_pad_source == "labels"


@pytest.mark.parametrize("field", ["text", "originalText", "description", "id"])
@pytest.mark.parametrize("descendant", [False, True])
def test_any_key_label_blocks_geometry(monkeypatch, field, descendant):
    tree = _blank()
    cell = tree["children"][0]
    target = _node((180, 1320, 220, 1360)) if descendant else cell
    target["attributes"][field] = "5"  # Incomplete or conflicting labels must not become positional digits.
    if descendant:
        cell["children"].append(target)
    device, _, _ = _device(monkeypatch, tree, layout=BLANK_PIN_LAYOUT)
    assert device.lock_pin_pad() is None and device.pin_pad_source is None


@pytest.mark.parametrize("field", ["text", "originalText"])
def test_independent_partial_digit_label_does_not_fall_back(monkeypatch, field):
    tree = _blank()
    tree["children"].append(_node((10, 50, 80, 100), **{field: "5"}))
    device, _, _ = _device(monkeypatch, tree, layout=BLANK_PIN_LAYOUT)
    assert device.lock_pin_pad() is None and device.pin_pad_source is None


@pytest.mark.parametrize("field", ["text", "originalText", "description", "id"])
@pytest.mark.parametrize("value", [0, False])
def test_non_string_key_fields_are_not_empty(monkeypatch, field, value):
    tree = _blank()
    tree["children"][0]["attributes"][field] = value
    device, _, _ = _device(monkeypatch, tree, layout=BLANK_PIN_LAYOUT)
    assert device.lock_pin_pad() is None and device.pin_pad_source is None


@pytest.mark.parametrize("field", ["text", "originalText"])
def test_independent_numeric_zero_label_does_not_fall_back(monkeypatch, field):
    tree = _blank()
    tree["children"].append(_node((10, 50, 80, 100), **{field: 0}))
    device, _, _ = _device(monkeypatch, tree, layout=BLANK_PIN_LAYOUT)
    assert device.lock_pin_pad() is None and device.pin_pad_source is None


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "nested", "different_size", "uneven_column",
                                     "uneven_row", "overlap", "out_of_window", "invisible", "disabled",
                                     "hidden_parent", "disabled_parent", "clipped", "modal", "no_root_bounds"])
def test_irregular_or_unusable_grid_is_rejected(monkeypatch, mutation):
    tree = _blank()
    cells = tree["children"]
    if mutation == "missing":
        cells.pop()
    elif mutation == "extra":
        cells.append(_node((10, 10, 80, 80), clickable=True))
    elif mutation == "duplicate":
        cells[-1] = copy.deepcopy(cells[0])
    elif mutation == "nested":
        cells[0]["children"].append(_node((170, 1320, 300, 1450), clickable=True))
    elif mutation == "different_size":
        cells[0]["attributes"]["bounds"] = "[150,1300][349,1500]"
    elif mutation == "uneven_column":
        for i in (2, 5, 8, 11):
            cells[i]["attributes"]["bounds"] = cells[i]["attributes"]["bounds"].replace("750", "751").replace("950", "951")
    elif mutation == "uneven_row":
        for i in (9, 10, 11):
            cells[i]["attributes"]["bounds"] = cells[i]["attributes"]["bounds"].replace("2050", "2051").replace("2250", "2251")
    elif mutation == "overlap":
        for i, cell in enumerate(cells):
            c, r = i % 3, i // 3
            cell["attributes"]["bounds"] = "[%d,%d][%d,%d]" % (150 + c * 150, 1300 + r * 250, 350 + c * 150, 1500 + r * 250)
    elif mutation == "out_of_window":
        tree["attributes"]["bounds"] = "[0,0][900,2848]"
    elif mutation in {"invisible", "disabled"}:
        cells[0]["attributes"]["visible" if mutation == "invisible" else "enabled"] = "false"
    elif mutation in {"hidden_parent", "disabled_parent", "clipped"}:
        parent = _node((0, 0, 1320, 2848), children=cells)
        if mutation == "clipped":
            parent["attributes"].update(type="Scroll", bounds="[0,0][1320,2200]")
        else:
            parent["attributes"]["visible" if mutation == "hidden_parent" else "enabled"] = "false"
        tree["children"] = [parent]
    elif mutation == "modal":
        cells.append(_node((0, 0, 1320, 2848), type="Dialog"))
    elif mutation == "no_root_bounds":
        del tree["attributes"]["bounds"]
    device, _, calls = _device(monkeypatch, tree, layout=BLANK_PIN_LAYOUT)
    assert device.lock_pin_pad() is None and device.pin_pad_source is None
    assert not [op for op, _ in calls if op == "tap"]


@pytest.mark.parametrize("actual", [None, "ancestor", "other"])
def test_center_requires_actual_key_receiver(monkeypatch, actual):
    tree = _blank()
    original = UiTree.touch_target

    def receiver(self, x, y):
        if actual is None:
            return None
        if actual == "ancestor":
            return self.nodes[0]
        return self.nodes[2] if (x, y) == (250, 1400) else original(self, x, y)

    monkeypatch.setattr(UiTree, "touch_target", receiver)
    assert find_blank_pin_pad(tree) is None


@pytest.mark.parametrize("failure,error_class,raw", [("dump", "timeout", "BLANK_DUMP_RAW"),
                                                    ("cat", "command_failed", "BLANK_CAT_RAW"),
                                                    ("json", "tree_parse_error", "INVALID_BLANK_JSON_RAW"),
                                                    (None, None, None)])
def test_query_clears_old_source_and_preserves_errors(monkeypatch, failure, error_class, raw):
    device, state, _ = _device(monkeypatch, layout=BLANK_PIN_LAYOUT)
    assert device.lock_pin_pad() is not None
    state.update(failure=failure, tree=_node((0, 0, 1320, 2848)))
    assert device.lock_pin_pad() is None and device.pin_pad_source is None
    if failure:
        assert device.pin_pad_query_error["error_class"] == error_class
        assert device.pin_pad_query_error["raw"] == raw
    else:
        assert device.pin_pad_query_error is None


@pytest.mark.parametrize("layout", [None, "unknown", True, {}, [BLANK_PIN_LAYOUT]])
def test_unknown_layout_value_stays_off(monkeypatch, layout):
    device, _, _ = _device(monkeypatch, layout=layout)
    assert device.pin_pad_layout is None and device.lock_pin_pad() is None


def test_workspace_config_uses_final_exact_explicit_device(monkeypatch, tmp_path):
    monkeypatch.setattr(HdcDevice, "_first_target", lambda self: "configured-device")
    ws = Workspace(str(tmp_path / "data"))
    ws.config = {"device": "hdc:configured-device", "unlock_pin_layouts": {
        "hdc:configured-device": BLANK_PIN_LAYOUT, "hdc:": BLANK_PIN_LAYOUT, "hdc:*": BLANK_PIN_LAYOUT}}
    assert ws.device(None).pin_pad_layout == BLANK_PIN_LAYOUT
    assert ws.device("hdc:configured-device").pin_pad_layout == BLANK_PIN_LAYOUT
    assert ws.device("hdc:other-device").pin_pad_layout is None
    assert ws.device("hdc:*").pin_pad_layout is None
    discovered = ws.device("hdc:")
    assert discovered.serial == "configured-device" and discovered.pin_pad_layout is None
    assert create_device("hdc:", pin_pad_layout=BLANK_PIN_LAYOUT).pin_pad_layout is None
    assert HdcDevice(pin_pad_layout=BLANK_PIN_LAYOUT).pin_pad_layout is None


@pytest.mark.parametrize("layouts", [None, True, [], "standard_3x4_blank"])
def test_malformed_config_stays_off(tmp_path, layouts):
    ws = Workspace(str(tmp_path / "data"))
    ws.config = {"unlock_pin_layouts": layouts}
    assert ws.device("hdc:synthetic-device").pin_pad_layout is None


def test_replay_never_receives_geometry_config(monkeypatch, tmp_path):
    from vpdtest.devices import replay
    path = tmp_path / "synthetic-world.json"
    path.write_text("{}", encoding="utf-8")
    ws = Workspace(str(tmp_path / "data"))
    ws.config = {"unlock_pin_layouts": {"replay:" + str(path): BLANK_PIN_LAYOUT}}
    monkeypatch.setattr(replay, "ReplayDevice", lambda arg, state_path=None: {"arg": arg})
    assert ws.device("replay:" + str(path)) == {"arg": str(path.resolve())}


@pytest.mark.parametrize("valid", [True, False])
def test_unlock_real_dispatcher_records_source_without_pin_data(monkeypatch, valid):
    tree = _blank()
    if not valid:
        tree["children"][0]["attributes"]["description"] = "unknown control"
    device, _, calls = _device(monkeypatch, tree, layout=BLANK_PIN_LAYOUT)
    taps = []
    checks = []

    def locked():
        checks.append(True)
        return len(taps) < 4

    monkeypatch.setattr(device, "screen_locked", locked)
    monkeypatch.setattr(device, "display_info", lambda: {"width_px": 1320, "height_px": 2848})
    monkeypatch.setattr(device, "tap", lambda x, y: taps.append((x, y)) or DeviceResult("tap", True, True))
    result = wake_unlock(device, "2468", sleep=lambda _: None)  # synthetic test PIN
    assert result["entered_digits"] == (4 if valid else 0)
    assert result["status"] == ("unlocked" if valid else "indeterminate")
    sources = [item for item in result["evidence"] if item.get("stage") == "pin_pad"]
    assert sources == ([{"stage": "pin_pad", "pad_source": "configured_geometry"}] if valid else [])
    assert "2468" not in json.dumps(result)
    assert len(taps) == (4 if valid else 0)
    if valid:
        assert len(checks) >= 8  # Dispatcher still rechecks screen_locked before every digit.
