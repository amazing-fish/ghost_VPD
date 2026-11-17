"""Producer-bound aggregate roots; synthetic data, no exported device tree."""
from copy import deepcopy
import json

import pytest

from test_dispatch_regression import _ctx
from test_relative_swipe_layout_chain import hierarchy, step
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.execute import Executor
from vpdtest.tree import UiTree
from vpdtest.tree_contract import AGGREGATE_FIELDS, bundle_dump_origin, document_root_evidence


BUNDLE = "synthetic.bundle"


def aggregate(window=None, bundle=BUNDLE):
    attrs = dict.fromkeys(AGGREGATE_FIELDS, "")
    attrs["bounds"] = "[0,0][2584,1828]"
    if window is None:
        widget_attrs = dict.fromkeys(AGGREGATE_FIELDS, "")
        widget_attrs.update(type="root", hierarchy="ROOT", bounds=attrs["bounds"],
                            clickable="false", longClickable="false", scrollable="false")
        window = {"attributes": widget_attrs, "children": []}
    window = deepcopy(window)
    window["attributes"].update(bundleName=bundle, abilityName="synthetic", pagePath="",
                                  hierarchy="ROOT", visible="true", hashcode="synthetic", hint="")
    return {"attributes": attrs, "children": [window]}


def test_matching_schema_is_diagnostic_not_permission_on_unknown_build():
    raw = aggregate()
    original = deepcopy(raw)
    origin = bundle_dump_origin(raw, BUNDLE)
    evidence = document_root_evidence(raw, origin)
    assert evidence["schema_status"] == "matched"
    assert evidence["status"] == "unverified"
    assert evidence["reason"] == "producer_build_unverified"
    assert evidence["may_exclude_root"] is False
    assert raw == original and UiTree(raw).nodes[0].attrs["clickable"] == ""
    assert document_root_evidence(raw, {**origin, "status": "verified", "may_exclude_root": True})[
        "may_exclude_root"] is False


@pytest.mark.parametrize("change", [
    lambda raw: raw["attributes"].update(clickable="false"),
    lambda raw: raw["attributes"].update(longClickable="true"),
    lambda raw: raw["attributes"].update(type="Column"),
    lambda raw: raw["attributes"].update(text="支付"),
    lambda raw: raw["attributes"].update(gesture="unknown"),
    lambda raw: raw["attributes"].pop("key"),
    lambda raw: raw["attributes"].update(bounds="[0,0][0,0]"),
    lambda raw: raw.update(document_root_origin={"status": "verified"}),
    lambda raw: raw.update(children=[]),
    lambda raw: raw["children"][0]["attributes"].update(bundleName="other.bundle"),
    lambda raw: raw["children"][0]["attributes"].pop("hierarchy"),
    lambda raw: raw["children"][0]["attributes"].pop("hashcode"),
])
def test_unknown_roots_and_wrong_windows_do_not_get_wrapper_proof(change):
    raw = aggregate()
    change(raw)
    origin = bundle_dump_origin(raw, BUNDLE)
    assert origin["status"] == "unverified"
    assert document_root_evidence(raw, origin)["may_exclude_root"] is False


def test_provenance_for_another_snapshot_or_producer_is_not_reused():
    raw = aggregate()
    origin = bundle_dump_origin(raw, BUNDLE)
    changed = deepcopy(raw)
    changed["children"][0]["attributes"]["hint"] = "changed"
    assert document_root_evidence(changed, origin)["reason"] == "raw_fingerprint_mismatch"
    assert document_root_evidence(raw, {**origin, "producer": "untrusted"})["status"] == "unverified"


def test_hdc_certifies_only_its_actual_bundle_dump_and_preserves_raw(monkeypatch, tmp_path):
    raw = aggregate()
    dev = HdcDevice("synthetic")
    calls = []
    def shell(*args, **kwargs):
        calls.append(args)
        return DeviceResult("dump_tree", True, True)
    def pull(remote, local, op):
        local.write_text(json.dumps(raw), encoding="utf-8")
        return DeviceResult(op, True, True)
    monkeypatch.setattr(dev, "_shell", shell)
    monkeypatch.setattr(dev, "_pull", pull)
    result = dev.dump_tree(BUNDLE, tmp_path / "not-written.json")
    assert calls[0][:4] == ("uitest", "dumpLayout", "-b", BUNDLE)
    assert "-i" not in calls[0] and "-a" not in calls[0]
    assert result.data["document_root_origin"]["status"] == "unverified"
    assert result.data["document_root_origin"]["schema_status"] == "matched"
    assert result.data["tree"] == raw
    assert not (tmp_path / "not-written.json").exists()


@pytest.mark.parametrize("producer", ["hdc", "replay"])
@pytest.mark.parametrize("danger", [False, True])
def test_complete_observer_to_swipe_path_uses_provenance_not_unknown_false(
        ws, tmp_path, monkeypatch, producer, danger):
    ctx = _ctx(ws, hierarchy(tmp_path, 3))
    original = ctx.device.dump_tree
    ctx.device.kind = producer
    captured = []
    def dump(bundle, path):
        result = original(bundle, path)
        raw = aggregate(result.data["tree"]["children"][0], bundle)
        if danger:
            raw["children"][0]["attributes"]["longClickable"] = ""
        result.data["tree"] = raw
        result.data["document_root_origin"] = bundle_dump_origin(raw, bundle)
        captured.append(deepcopy(raw))
        return result
    monkeypatch.setattr(ctx.device, "dump_tree", dump)
    result = Executor(ctx).run_step(step(), "producer-root-scroll")
    assert captured and all(raw["attributes"]["clickable"] == "" for raw in captured)
    assert result["final_status"] == "policy_denied", result
    assert ctx.actions == 0
    obs, tree = ctx.observe("diagnostic-summary")
    diagnostic = obs["tree"]["document_root"]
    assert diagnostic["status"] == "unverified" and diagnostic["may_exclude_root"] is False
    assert diagnostic["reason"] == ("producer_build_unverified" if producer == "hdc"
                                    else "producer_not_verified")
