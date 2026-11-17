"""Synthetic BM output checks target BundleInfo vs nested module versions."""
import json
import pytest
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice, parse_version


def test_bundle_root_version_wins_regardless_of_nested_order():
    doc = {"name": "synthetic.app", "applicationInfo": {"versionName": "2.0.4.102"},
           "versionName": "1.0.26.100"}
    for raw in (json.dumps(doc), "synthetic.app:\n" + json.dumps(doc, sort_keys=True)):
        assert parse_version(raw) == "1.0.26.100"


def test_legacy_partial_output_does_not_guess_distinct_versions():
    assert parse_version('"versionName":"2" "versionName":"1"') is None
    assert parse_version('"versionName":"" "versionName":"1.2"') == "1.2"
    assert parse_version('"versionName":"1.2" "versionName":"1.2"') == "1.2"
    assert parse_version('{"nested":{"versionName":"1.2"}}') is None


@pytest.mark.parametrize("raw,reason", [
    ('other.app:\n{"name":"other.app","versionName":"1"}', "app_version_bundle_mismatch"),
    ('{"name":"other.app","versionName":"1"}', "app_version_bundle_mismatch"),
    ('"versionName":"1" "versionName":"2"', "app_version_ambiguous"),
    ('{"name":"synthetic.app","nested":{"versionName":"1"}}', "app_version_unavailable"),
    ('synthetic.app:\n{"versionName":"1"', "app_version_unavailable"),
])
def test_unknown_version_records_diagnostic(monkeypatch, raw, reason):
    device = HdcDevice("synthetic")
    monkeypatch.setattr(device, "_shell", lambda *a, **kw: DeviceResult("bm_dump", True, True, raw=raw))
    assert device.app_version("synthetic.app") is None
    assert device.version_query_error["error_class"] == reason


def test_preflight_exposes_ambiguous_query_not_a_certain_version(monkeypatch):
    device = HdcDevice("synthetic")
    raw = '"versionName":"2" "versionName":"1"'
    replies = {"bm_dump": raw, "power_state": "Current State: AWAKE", "lock_state": "screenLocked false"}
    monkeypatch.setattr(device, "_shell", lambda *a, op, **kw: DeviceResult(op, True, True, raw=replies[op]))
    monkeypatch.setattr(device, "info", lambda: {"serial": "synthetic"})
    monkeypatch.setattr(device, "display_info", dict)
    result = device.preflight("synthetic.app")
    check = next(c for c in result["checks"] if c["check"] == "app_installed")
    assert result["app_version"] is None
    assert check["ok"] is False and check["error_class"] == "app_version_ambiguous"
    assert check["execution_error"]["error_class"] == "app_version_ambiguous"
