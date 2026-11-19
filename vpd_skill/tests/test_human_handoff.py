"""Local handoff registration/claim, separate from device actions and external delivery."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from vpdtest.cli import HANDLERS, main
from vpdtest.human_handoff import cmd_handoff, register_handoffs
from vpdtest.util import read_json


def register(ws, command, result):
    return register_handoffs(ws.runs_dir, command, result, [result, *result.get("results", [])])


def test_cli_publishes_top_level_takeover_without_asset_drift(ws, monkeypatch, capsys):
    out = {"run_id": "run-one", "action_required": "Check the device in person", "verdict": "blocked"}
    monkeypatch.setitem(HANDLERS, "run", lambda *_: dict(out))
    assert main(["--workspace", str(ws.root), "run", "CASE"]) == 2
    brief = json.loads(capsys.readouterr().out)
    assert brief["action_required"] == out["action_required"] and "host_followup" not in brief
    event, = brief["human_handoffs"]
    assert set(event) == {"event_id", "source"} and event["source"] == {"record": "run-one"}
    path = ws.runs_dir / ".human-handoffs" / (event["event_id"] + ".json")
    assert read_json(path) == event and out["action_required"] not in path.read_text(encoding="utf-8")
    args = ["--workspace", str(ws.root), "handoff", event["event_id"]]
    for flags, expected in [([], "unclaimed"), (["--claim"], "acquired"),
                            (["--claim"], "already_claimed"), ([], "claimed")]:
        assert main(args + flags) == 0
        assert json.loads(capsys.readouterr().out) == {**event, "claim": expected}


@pytest.mark.parametrize("result", [{"verdict": "blocked"}, {"verdict": "pending"}, {"host_followup": {
    "next": "observe_only", "requires_human": False, "notification_destination": "existing_authorized_task_channel"}},
    {"results": [{"verdict": "error", "action_required": False}]}])
def test_non_interventions_create_no_local_event(ws, result):
    assert register(ws, "run", result) == []
    assert not (ws.runs_dir / ".human-handoffs").exists()


def test_repeated_concurrent_wrappers_and_claims_share_one_event(ws):
    action = "现场核对"
    run = {"run_id": "run-a", "action_required": action}
    suite = {"suite_run": "suite-a", "action_required": action, "results": [run]}
    experience = {"handoff": {"run_id": "run-a", "phase": "trial"}, "action_required": action}
    requests = [("run", run), ("suite", suite), ("experience", experience)] * 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        items = list(pool.map(lambda pair: register(ws, *pair), requests))
    first, = items[0]
    assert all(row == [first] for row in items)
    assert len(list((ws.runs_dir / ".human-handoffs").glob("*.json"))) == 1
    args = SimpleNamespace(event_id=first["event_id"], claim=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(lambda _: cmd_handoff(ws, args)["claim"], requests))
    assert states.count("acquired") == 1 and states.count("already_claimed") == len(requests) - 1
    # Two independent runs cannot suppress one another.
    other, = register(ws, "run", {**run, "run_id": "run-b"})
    assert other["event_id"] != first["event_id"]


def test_separate_session_steps_and_changed_instruction_do_not_collapse(ws):
    row = {"session": "session-one", "seq": 3, "action_required": "人工检查"}
    one, = register(ws, "act", row)
    again, = register(ws, "act", row)
    two, = register(ws, "act", {**row, "seq": 4})
    changed, = register(ws, "act", {**row, "action_required": "需要现场重新确认"})
    assert one == again and len({one["event_id"], two["event_id"], changed["event_id"]}) == 3


def test_static_knowledge_fields_do_not_register_device_interventions(ws, monkeypatch, capsys):
    from vpdtest.knowledge import KnowledgeStore
    entry = {"id": "K-TEST", "action_required": "示例文字", "handoff": "非执行记录", "run_id": "example"}
    monkeypatch.setattr(KnowledgeStore, "get", lambda self, key: entry)
    # Keep the existing general exit convention even when a data command echoes this field.
    assert main(["--workspace", str(ws.root), "knowledge", "show", entry["id"]]) == 2
    assert json.loads(capsys.readouterr().out) == entry
    assert not (ws.runs_dir / ".human-handoffs").exists()


def test_path_resolution_error_preserves_takeover_exit(ws, monkeypatch, capsys):
    from vpdtest import human_handoff

    def unavailable(_):
        raise RuntimeError("symlink loop on Python 3.12")

    monkeypatch.setattr(human_handoff, "_directory", unavailable)
    monkeypatch.setitem(HANDLERS, "unlock", lambda *_: {"run": "unlock-a", "action_required": True})
    assert main(["--workspace", str(ws.root), "unlock"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["action_required"] and out["human_handoffs"][0]["registration"] == "unavailable"


def test_unbound_and_unwritable_still_report_intervention(ws, monkeypatch, capsys):
    monkeypatch.setitem(HANDLERS, "unlock", lambda *_: {"action_required": True})
    assert main(["--workspace", str(ws.root), "unlock"]) == 2
    assert json.loads(capsys.readouterr().out)["human_handoffs"][0]["registration"] == "unbound"
    ws.runs_dir.mkdir(exist_ok=True)
    (ws.runs_dir / ".human-handoffs").write_text("not a directory", encoding="utf-8")
    monkeypatch.setitem(HANDLERS, "unlock", lambda *_: {"run": "unlock-a", "action_required": True})
    assert main(["--workspace", str(ws.root), "unlock"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["action_required"] and out["human_handoffs"][0]["registration"] == "unavailable"


@pytest.mark.parametrize("key", ["../wrong", "a" * 64])
def test_claim_never_creates_an_unknown_event(ws, key):
    with pytest.raises((ValueError, FileNotFoundError)):
        cmd_handoff(ws, SimpleNamespace(event_id=key, claim=True))
    assert not (ws.runs_dir / ".human-handoffs").exists()
