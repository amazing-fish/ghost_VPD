"""检索展示与现有Oracle资格一致；确认仅发生在测试临时目录。"""
from types import SimpleNamespace

import pytest

from vpdtest.cli import build_parser
from vpdtest.commands import cmd_knowledge
from vpdtest.knowledge import KnowledgeStore, SOURCE_TYPES
from vpdtest.util import read_json, write_json
from vpdtest.verdict import evaluate_assertion


KID = "K-TEST-DOMAIN-NOTE"
APP = "motor_hm"
VERSION = "3.0.3.200"


def _seed(ws, source="reviewer", kind="domain_note", scope=None):
    kn = KnowledgeStore(ws.root)
    kn.add({"id": KID, "kind": kind, "subject": "pytest.domain_note.search",
            "statement": "Synthetic domain-note search contract", "expected": {"present": True},
            "applies_to": {"app": APP, "app_version": VERSION} if scope is None else scope,
            "source": {"type": source, "ref": "tests/test_domain_note_search.py synthetic only"}})
    return kn


def _confirm(kn):
    kn.confirm(KID, reviewer="pytest-synthetic", basis="Synthetic qualification contract, not production")


def _cli(ws, role=None, version=VERSION):
    args = ["knowledge", "search", KID, "--app", APP]
    if version is not None:
        args += ["--app-version", version]
    if role:
        args += ["--role", role]
    return cmd_knowledge(ws, build_parser().parse_args(args))


def _rows(result):
    return {row["ref"]: row for group in result["results"].values() for row in group}


@pytest.mark.parametrize("source", sorted(SOURCE_TYPES))
def test_domain_note_is_discoverable_only_after_existing_confirmation(tmp_path, source):
    ws = SimpleNamespace(root=tmp_path / "data")
    kn = _seed(ws, source=source)
    unconfirmed = _cli(ws)
    row = _rows(unconfirmed)[KID]
    assert row["role"] == ("observation" if source == "observation" else "background")
    assert row["can_be_oracle"] is False and unconfirmed["usable_oracles"] == []
    _confirm(kn)
    before = kn.path.read_bytes()
    confirmed = _cli(ws, role="requirement")
    assert kn.oracle_status([KID], facts=confirmed["facts"])["status"] == "confirmed"
    row = _rows(confirmed)[KID]
    assert row["kind"] == "domain_note" and row["status"] == "confirmed"
    assert row["role"] == "requirement" and row["can_be_oracle"] is True
    assert confirmed["usable_oracles"] == [KID] and row["callable"]
    assert kn.path.read_bytes() == before


@pytest.mark.parametrize("value,expected", [(1, "pass"), (None, "fail")])
def test_cli_candidate_agrees_with_real_assertion_consumer(verdict_ws, value, expected):
    kn = _seed(verdict_ws)
    _confirm(kn)
    before = kn.path.read_bytes()
    result = _cli(verdict_ws, role="requirement")
    kn.bind(result["facts"])
    actual = evaluate_assertion(
        {"id": "A1", "type": "value_present", "at": "S1", "oracle_refs": [KID]},
        {"S1": {"outcome": "passed", "measurement": {"value": value}}},
        lambda _: None, kn, verdict_ws.app(APP))
    assert actual["oracle"]["status"] == "confirmed" and actual["result"] == expected
    assert KID in result["usable_oracles"], {"cli": result, "actual": actual}
    assert kn.path.read_bytes() == before


@pytest.mark.parametrize("scenario,oracle_status", [
    ("unconfirmed", "unconfirmed"), ("deprecated", "missing"),
    ("invalid_status", "invalid"), ("missing_status", "invalid"),
    ("unknown_scope", "applicability_unknown"), ("wrong_scope", "out_of_scope"),
    ("conflicted", "conflicted"),
])
def test_domain_note_keeps_existing_status_scope_and_conflict_gates(tmp_path, scenario, oracle_status):
    ws = SimpleNamespace(root=tmp_path / "data")
    scope = {"app": APP, "app_version": "9.9.9"} if scenario == "wrong_scope" else None
    kn = _seed(ws, scope=scope)
    if scenario != "unconfirmed":
        _confirm(kn)
    data = read_json(kn.path)
    item = next(x for x in data["items"] if x["id"] == KID)
    if scenario == "deprecated":
        item["status"] = "deprecated"
    elif scenario == "invalid_status":
        item["status"] = "superseded"
    elif scenario == "missing_status":
        item.pop("status")
    write_json(kn.path, data)
    if scenario == "conflicted":
        kn.add({**kn.get(KID), "id": "K-TEST-DOMAIN-PEER", "expected": {"present": False}})
    before = kn.path.read_bytes()
    result = _cli(ws, version=None if scenario == "unknown_scope" else VERSION)
    assert kn.oracle_status([KID], facts=result["facts"])["status"] == oracle_status
    assert _rows(result)[KID]["can_be_oracle"] is False and result["usable_oracles"] == []
    assert kn.path.read_bytes() == before


@pytest.mark.parametrize("source", ["observation", "model_inference"])
def test_search_does_not_replace_required_confirmation_basis(tmp_path, source):
    ws = SimpleNamespace(root=tmp_path / "data")
    kn = _seed(ws, source=source)
    before = kn.path.read_bytes()
    with pytest.raises(PermissionError, match="依据"):
        kn.confirm(KID, reviewer="pytest-synthetic")
    assert _rows(_cli(ws))[KID]["can_be_oracle"] is False
    assert kn.path.read_bytes() == before


@pytest.mark.parametrize("kind", ["operation", "observation"])
def test_search_does_not_grant_non_oracle_kinds(tmp_path, kind):
    ws = SimpleNamespace(root=tmp_path / "data")
    kn = _seed(ws, kind=kind)
    _confirm(kn)
    result = _cli(ws)
    assert kn.oracle_status([KID], facts=result["facts"])["status"] == "not_oracle"
    assert _rows(result)[KID]["role"] == kind
    assert _rows(result)[KID]["can_be_oracle"] is False and result["usable_oracles"] == []
