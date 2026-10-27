"""#45: CLI retrieval uses canonical APP scope without hiding other diagnostics."""
from types import SimpleNamespace

import pytest

from vpdtest.cli import build_parser
from vpdtest.commands import cmd_knowledge
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import write_json


def _workspace(tmp_path, scopes):
    root = tmp_path / "data"
    write_json(root / "knowledge" / "items.json", {"items": [
        {"id": name, "kind": "generic_invariant", "subject": "pytest.scope." + name,
         "statement": "scopeprobe synthetic fixture", "expected": True, "status": "confirmed",
         "source": {"type": "reviewer", "ref": "synthetic scope consumer fixture"},
         "applies_to": scope}
        for name, scope in scopes.items()]})
    # Only temporary synthetic files; never load local config or a device.
    return SimpleNamespace(root=root)


def _search(ws, app=None):
    args = ["knowledge", "search", "scopeprobe", "--app-version", "1.0.0", "--device-form", "bar"]
    if app:
        args += ["--app", app]
    return cmd_knowledge(ws, build_parser().parse_args(args))


def _rows(result):
    return {row["ref"]: row for group in result["results"].values() for row in group}


SCOPES = {
    "LIST": {"app": ["motor_hm", "qiankun_hm"]},
    "SCALAR": {"app": "motor_hm"},
    "ANY": {"app": "*"},
    "GLOBAL": {},
    "VERSION": {"app": ["motor_hm", "qiankun_hm"], "app_version": "9.0.0"},
    "FORM": {"app": "motor_hm", "device_form": "foldable"},
}


@pytest.mark.parametrize("app,returned,usable", [
    ("motor_hm", set(SCOPES), {"LIST", "SCALAR", "ANY", "GLOBAL"}),
    ("qiankun_hm", {"LIST", "ANY", "GLOBAL", "VERSION"}, {"LIST", "ANY", "GLOBAL"}),
    ("other_app", {"ANY", "GLOBAL"}, {"ANY", "GLOBAL"}),
])
def test_cli_app_search_returns_matching_lists_and_retains_scope_diagnostics(tmp_path, app, returned, usable):
    ws = _workspace(tmp_path, SCOPES)
    original = (ws.root / "knowledge" / "items.json").read_bytes()
    result = _search(ws, app)
    rows = _rows(result)
    assert set(rows) == returned
    assert set(result["usable_oracles"]) == usable
    kn = KnowledgeStore(ws.root)
    for name in returned:
        eligible = kn.oracle_status([name], facts=result["facts"])["status"] == "confirmed"
        assert rows[name]["can_be_oracle"] is eligible
    for name in returned & {"VERSION", "FORM"}:
        assert rows[name]["applicability"] == "out_of_scope"
        assert rows[name]["can_be_oracle"] is False
    assert (ws.root / "knowledge" / "items.json").read_bytes() == original


def test_search_without_app_keeps_all_scope_diagnostics(tmp_path):
    ws = _workspace(tmp_path, SCOPES)
    result = _search(ws)
    assert set(_rows(result)) == set(SCOPES)
    assert set(result["usable_oracles"]) == {"ANY", "GLOBAL"}
    assert _rows(result)["LIST"]["applicability"] == "unknown"


@pytest.mark.parametrize("status,kind,role", [
    ("unconfirmed", "generic_invariant", "requirement"),
    ("deprecated", "generic_invariant", "background"),
    ("confirmed", "observation", "observation"),
])
def test_list_app_retrieval_does_not_promote_oracle_eligibility(tmp_path, status, kind, role):
    ws = _workspace(tmp_path, {"LIST": SCOPES["LIST"]})
    kn = KnowledgeStore(ws.root)
    item = kn.get("LIST")
    item.update(status=status, kind=kind)
    write_json(ws.root / "knowledge" / "items.json", {"items": [item]})
    original = (ws.root / "knowledge" / "items.json").read_bytes()
    result = _search(ws, "motor_hm")
    row = _rows(result)["LIST"]
    assert row["role"] == role and row["can_be_oracle"] is False
    assert result["usable_oracles"] == []
    assert (ws.root / "knowledge" / "items.json").read_bytes() == original
