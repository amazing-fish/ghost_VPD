"""Synthetic evidence and public CLI Replay regressions, never physical devices."""
import json
import shutil

import pytest

from test_goal_loop import _lab_world
from vpdtest.cli import main
from vpdtest.commands import _last_window
from vpdtest.observe import RunStore, observation_number
from vpdtest.util import append_jsonl, read_jsonl, write_json


def command(ws, capsys, spec, *args):
    code = main(["--workspace", str(ws.root), *args, "--session", "ordering", "--device", spec])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    return out


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("value, expected", [
    ("obs-0001", 1), ("obs-9999", 9999), ("obs-10000", 10000),
    ("obs-0000", None), ("obs-1", None), ("obs-00001", None),
    ("obs-١٠٠٠", None), ("obs-1000\n", None), ("obs-+001", None),
    ("obs--001", None), ("notes.txt", None), (None, None), (1, None),
])
def test_canonical_observation_number(value, expected):
    assert observation_number(value) == expected


@pytest.mark.parametrize("kind", ["empty", "partial", "file", "link", "dangling"])
def test_all_occupied_canonical_names_reserve_ids(tmp_path, kind):
    store = RunStore(tmp_path, "synthetic")
    path = store.dir / "obs" / "obs-10000"
    if kind in ("empty", "partial"):
        path.mkdir()
        if kind == "partial":
            (path / "tree.json").write_bytes(b"partial original evidence")
    elif kind == "file":
        path.write_bytes(b"occupied original file")
    else:
        target = tmp_path / "target"
        if kind == "link":
            target.mkdir()
        try:
            path.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"native symlink unavailable: {exc}")
    before = snapshot(store.dir)
    resumed = RunStore(tmp_path, "synthetic", resume=True)
    assert resumed.next_obs_dir()[0] == "obs-10001"
    assert snapshot(store.dir) == before
    if kind in ("link", "dangling"):
        assert path.is_symlink()


def test_exclusive_mkdir_still_refuses_late_collision(tmp_path):
    store = RunStore(tmp_path, "synthetic")
    path = store.dir / "obs" / "obs-0001"
    path.mkdir()
    (path / "original").write_bytes(b"untouched")
    with pytest.raises(FileExistsError):
        store.next_obs_dir()
    assert (path / "original").read_bytes() == b"untouched"


@pytest.mark.parametrize("mutation", ["normal", "sparse", "misc", "empty", "partial", "deleted_tip"])
def test_public_cli_continuation_preserves_evidence(ws, tmp_path, capsys, mutation):
    spec = _lab_world(tmp_path)
    assert command(ws, capsys, spec, "observe")["observation"]["obs_id"] == "obs-0001"
    assert command(ws, capsys, spec, "observe")["observation"]["obs_id"] == "obs-0002"
    session = ws.runs_dir / "session-ordering"
    obs = session / "obs"
    expected = 3
    if mutation == "sparse":
        shutil.rmtree(obs / "obs-0001")  # Delete only disposable synthetic fixtures.
    elif mutation == "deleted_tip":
        shutil.rmtree(obs / "obs-0002")
    elif mutation == "misc":
        (obs / "notes.txt").write_bytes(b"unrelated")
        (obs / "obs-00009").mkdir()
    elif mutation in ("empty", "partial"):
        (obs / "obs-0003").mkdir()
        expected = 4
        if mutation == "partial":
            (obs / "obs-0003" / "tree.json").write_bytes(b"partial")
    before = snapshot(obs)
    journal = (session / "events.jsonl").read_bytes()
    for number in (expected, expected + 1):
        out = command(ws, capsys, spec, "observe")
        assert out["observation"]["obs_id"] == f"obs-{number:04d}"
        if mutation == "misc" and (obs / "notes.txt").exists():
            (obs / "notes.txt").unlink()
            before.pop((obs / "notes.txt").relative_to(obs))
    after = snapshot(obs)
    assert all(after[name] == data for name, data in before.items())
    assert (session / "events.jsonl").read_bytes().startswith(journal)
    refs = [e["obs_id"] for e in read_jsonl(session / "events.jsonl") if e["event"] == "observe"]
    assert len(refs) == len(set(refs))


@pytest.mark.parametrize("event", ["observe", "agent_observe", "system_dialog", "observation_stale", "layout_changed", "ui_alert"])
def test_public_cli_missing_highest_reference_is_never_rebound(ws, tmp_path, capsys, event):
    spec = _lab_world(tmp_path)
    command(ws, capsys, spec, "observe")
    session = ws.runs_dir / "session-ordering"
    append_jsonl(session / "events.jsonl", {"ts": "synthetic", "event": event, "obs_id": "obs-10000", "label": "synthetic lost evidence"})
    prefix = (session / "events.jsonl").read_bytes()
    assert command(ws, capsys, spec, "observe")["observation"]["obs_id"] == "obs-10001"
    assert (session / "events.jsonl").read_bytes().startswith(prefix)
    assert not (session / "obs" / "obs-10000").exists()


@pytest.mark.parametrize("bad", [b'{"event":', b'{"event": tru}\n', b'{"event":"\xff"}\n'])
def test_runstore_never_guesses_highwater_from_unparseable_journal(tmp_path, bad):
    store = RunStore(tmp_path, "synthetic")
    original = b'{"event":"observe","obs_id":"obs-9999"}\n' + bad
    store.events_path.write_bytes(original)
    with pytest.raises((ValueError, UnicodeDecodeError)):
        RunStore(tmp_path, "synthetic", resume=True)
    assert store.events_path.read_bytes() == original
    assert not list((store.dir / "obs").iterdir())


def test_numeric_window_ignores_unrelated_empty_and_linked_entries(tmp_path):
    store = RunStore(tmp_path, "synthetic")
    root = store.dir / "obs"
    for name, win in [("obs-9999", [1, 1]), ("obs-10000", [1320, 2848]),
                      ("zz-unrelated", [2, 2]), ("obs-010001", [3, 3])]:
        (root / name).mkdir()
        write_json(root / name / "observation.json", {"layout": {"window_px": win}})
    (root / "obs-10001").mkdir()
    (root / "obs-10002").write_bytes(b"occupied file")
    try:
        (root / "obs-10003").symlink_to(root / "obs-9999", target_is_directory=True)
        (root / "obs-10004").symlink_to(root / "missing", target_is_directory=True)
    except OSError:
        pass  # Other platforms still exercise numeric, unrelated and placeholder cases.
    assert _last_window(store) == [1320, 2848]
    highest = max(observation_number(p.name) or 0 for p in root.iterdir())
    assert RunStore(tmp_path, "synthetic", resume=True).next_obs_dir()[0] == f"obs-{highest + 1:04d}"


@pytest.mark.parametrize("numeric", [False, True])
def test_public_cli_point_uses_latest_canonical_numeric_window(ws, tmp_path, capsys, numeric):
    spec = _lab_world(tmp_path)
    command(ws, capsys, spec, "act", "launch")
    root = ws.runs_dir / "session-ordering" / "obs"
    if numeric:
        for name, win in [("obs-9999", [1, 1]), ("obs-10000", [1320, 2848])]:
            (root / name).mkdir()
            write_json(root / name / "observation.json", {"layout": {"window_px": win}})
    else:
        (root / "zz-not-an-observation").mkdir()
        write_json(root / "zz-not-an-observation" / "observation.json", {"layout": {"window_px": [1, 1]}})
    out = command(ws, capsys, spec, "act", "tap", "--point", "100,100", "--basis", "synthetic point")
    # Window precheck succeeds, but a point without a receiver remains policy-blocked.
    assert out["result"]["outcome"] == "blocked"
    assert out["result"]["attempts"][0]["policy"]["deny"]["class"] == "no_receiver_at_point"
    steps = read_jsonl(root.parent / "steps.jsonl")
    assert steps[-1]["step"]["point_window"] == [1320, 2848]
    if numeric:
        assert (root / "obs-10001").is_dir()


def test_old_session_facts_remains_compatible(ws, tmp_path, capsys):
    spec = _lab_world(tmp_path)
    command(ws, capsys, spec, "observe")
    path = ws.runs_dir / "session-ordering" / "events.jsonl"
    events = read_jsonl(path)
    for event in events:
        if event["event"] == "session_open":
            event["event"] = "session_facts"
            event.pop("identity")
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    prefix = path.read_bytes()
    assert command(ws, capsys, spec, "observe")["observation"]["obs_id"] == "obs-0002"
    assert path.read_bytes().startswith(prefix)
