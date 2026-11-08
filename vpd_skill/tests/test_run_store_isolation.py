"""Fresh run allocation is exclusive; sessions explicitly opt in to continuation."""
import errno
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest
from conftest import confirm
from verdict_fixtures import COPY_CASE, COPY_RULE, NAV_CASE, NAV_RULE, SETTINGS_RULE
from vpdtest import observe
from vpdtest.cli import main
from vpdtest.observe import RunStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json
from vpdtest.workspace import Workspace

KINDS = ("run", "explore", "suite", "unlock", "sysdiag", "console")


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def occupied(root):
    store = RunStore(root, "occupied")
    _, obs = store.next_obs_dir()
    (obs / "original.bin").write_bytes(b"\x00old observation\xff")
    for name in ("run.json", "report.md", "replay_state.json", "events.jsonl"):
        (store.dir / name).write_bytes(("old " + name).encode())
    return store.dir


@pytest.mark.parametrize("kind", KINDS)
def test_auto_collision_retries_without_touching_old_files(tmp_path, monkeypatch, kind):
    root = tmp_path / "nested" / "runs"
    old = occupied(root)
    before = snapshot(old)
    ids = Mock(side_effect=["occupied", "fresh"])
    monkeypatch.setattr(observe, "new_id", ids)
    store = RunStore(root, kind=kind)
    assert store.run_id == "fresh" and store.dir == root / "fresh"
    assert ids.call_count == 2 and all(c.args == (kind,) for c in ids.call_args_list)
    assert snapshot(old) == before
    assert not store.events_path.exists() and not (store.dir / "replay_state.json").exists()
    assert store.next_obs_dir()[0] == "obs-0001"


def test_continuous_collision_has_bounded_attempts(tmp_path, monkeypatch):
    old = occupied(tmp_path)
    before = snapshot(old)
    ids = Mock(return_value="occupied")
    monkeypatch.setattr(observe, "new_id", ids)
    with pytest.raises(FileExistsError):
        RunStore(tmp_path)
    assert ids.call_count == 10
    assert snapshot(old) == before and list(tmp_path.iterdir()) == [old]


@pytest.mark.parametrize("kind", ("run", "session"))
def test_explicit_id_never_implicitly_resumes(tmp_path, monkeypatch, kind):
    old = occupied(tmp_path)
    before = snapshot(old)
    ids = Mock(side_effect=AssertionError("explicit IDs must not be regenerated"))
    monkeypatch.setattr(observe, "new_id", ids)
    with pytest.raises(FileExistsError):
        RunStore(tmp_path, "occupied", kind=kind)
    assert not ids.called and snapshot(old) == before


@pytest.mark.parametrize("automatic", [False, True])
def test_concurrent_claim_has_exactly_one_owner(tmp_path, monkeypatch, automatic):
    barrier = Barrier(2)
    monkeypatch.setattr(observe, "new_id", lambda kind: "contended")

    def claim():
        barrier.wait(timeout=5)
        try:
            return RunStore(tmp_path, None if automatic else "contended")
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(lambda _: claim(), range(2)))
    assert sum(x is not None for x in owners) == 1
    assert (tmp_path / "contended" / "obs").is_dir()


@pytest.mark.parametrize("stage", ["root", "candidate", "obs"])
@pytest.mark.parametrize("error_type", [PermissionError, OSError])
def test_non_collision_io_error_is_not_retried(tmp_path, monkeypatch, stage, error_type):
    root = tmp_path / "runs"
    ids = Mock(return_value="fresh")
    monkeypatch.setattr(observe, "new_id", ids)
    original = Path.mkdir
    failure = error_type(errno.EACCES if error_type is PermissionError else errno.EIO, "synthetic allocation error")
    target = {"root": root, "candidate": root / "fresh", "obs": root / "fresh" / "obs"}[stage]
    attempts = []

    def mkdir(path, *args, **kwargs):
        if path == target:
            attempts.append(path)
            raise failure
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(error_type) as caught:
        RunStore(root)
    assert caught.value is failure and attempts == [target]
    assert ids.call_count == (0 if stage == "root" else 1)
    assert not (root / "fresh" / "obs").exists()


@pytest.mark.parametrize("existing", ["empty_dir", "file"])
def test_explicit_id_cannot_claim_existing_non_run_path(tmp_path, existing):
    target = tmp_path / "occupied"
    if existing == "empty_dir":
        target.mkdir()
    else:
        target.write_bytes(b"not a run")
    before = snapshot(tmp_path)
    with pytest.raises(FileExistsError):
        RunStore(tmp_path, "occupied")
    assert snapshot(tmp_path) == before
    if existing == "empty_dir":
        assert not list(target.iterdir())


def _process_claim(root, barrier, results):
    barrier.wait(timeout=15)
    try:
        RunStore(Path(root), "contended")
    except FileExistsError:
        results.put("occupied")
    else:
        results.put("owned")


def test_separate_processes_cannot_own_the_same_id(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    workers = [context.Process(target=_process_claim, args=(str(tmp_path), barrier, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        outcomes = [results.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(timeout=20)
        assert all(worker.exitcode == 0 for worker in workers)
        assert sorted(outcomes) == ["occupied", "owned"]
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        results.close()


def test_resume_requires_an_explicit_id(tmp_path):
    with pytest.raises(ValueError):
        RunStore(tmp_path, resume=True)
    assert not list(tmp_path.iterdir())


@pytest.fixture
def synthetic_ws(verdict_ws):
    confirm(verdict_ws, NAV_RULE, SETTINGS_RULE, COPY_RULE)
    for cid in (NAV_CASE, COPY_CASE):
        case = verdict_ws.load_case(cid)
        case["assertions"] = [a for a in case["assertions"] if a["type"] != "visual_review"]
        write_json(verdict_ws.case_path(cid), case)
    write_json(verdict_ws.root / "suites" / "synthetic.json", {"cases": [NAV_CASE]})
    return verdict_ws


def command(ws, capsys, *args):
    code = main(["--workspace", str(ws.root), *args])
    return code, json.loads(capsys.readouterr().out)


def test_real_cli_runs_keep_independent_reports_events_and_replay_state(synthetic_ws, monkeypatch, capsys):
    ws = synthetic_ws
    ids = Mock(side_effect=["run-first", "run-first", "run-second"])
    monkeypatch.setattr(observe, "new_id", ids)
    code, first = command(ws, capsys, "run", NAV_CASE, "--device", "replay:motor_hm_base.json", "--record-replay")
    assert code == 0 and first["verdict"] == "passed" and first["validation_level"] == "offline_replay"
    old = ws.runs_dir / first["run_id"]
    before = snapshot(old)
    code, second = command(ws, capsys, "run", COPY_CASE, "--device", "replay:motor_hm_base.json", "--record-replay")
    assert code == 0 and second["verdict"] == "passed" and second["validation_level"] == "offline_replay"
    assert first["run_id"] != second["run_id"] and snapshot(old) == before
    assert ids.call_count == 3
    for result, cid in ((first, NAV_CASE), (second, COPY_CASE)):
        root = ws.runs_dir / result["run_id"]
        assert read_json(root / "run.json")["case"]["id"] == cid
        assert [e["case"] for e in read_jsonl(root / "events.jsonl") if e["event"] == "run_start"] == [cid]
        assert (root / "replay_state.json").is_file()
        assert ws.load_case(cid)["replays"][0]["run_id"] == result["run_id"]


@pytest.mark.parametrize("run_id", ["explicit", "nested/explicit"])
def test_explicit_run_case_collision_precedes_device_and_preserves_first_run(synthetic_ws, monkeypatch, run_id):
    ws = synthetic_ws
    first = run_case(ws, NAV_CASE, device_spec="replay:motor_hm_base.json", run_id=run_id, propose=False)
    before = snapshot(Path(first["dir"]))
    device = Mock(side_effect=AssertionError("must fail before opening any device"))
    monkeypatch.setattr(ws, "device", device)
    with pytest.raises(FileExistsError):
        run_case(ws, COPY_CASE, device_spec="replay:motor_hm_base.json", run_id=run_id, propose=False)
    assert not device.called and snapshot(Path(first["dir"])) == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("failure", ["collision", "io"])
def test_cli_allocation_failure_precedes_every_device_entry(synthetic_ws, monkeypatch, capsys, kind, failure):
    ws = synthetic_ws
    old = occupied(ws.runs_dir)
    before = snapshot(old)
    ids = Mock(return_value="occupied")
    monkeypatch.setattr(observe, "new_id", ids)
    if failure == "io":
        original = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if path == old:
                raise PermissionError(errno.EACCES, "synthetic allocation denied")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", mkdir)
    device = Mock(side_effect=AssertionError("must fail before opening any device"))
    monkeypatch.setattr(Workspace, "device", device)
    argv = {"run": ["run", NAV_CASE], "suite": ["suite", "synthetic"],
            "explore": ["explore", "--start", "car_tab"], "unlock": ["unlock"],
            "sysdiag": ["sysdiag"], "console": ["console"]}[kind]
    # The device factory is a fail-fast sentinel; no HDC object or command can be created.
    code, out = command(ws, capsys, *argv, "--device", "hdc:synthetic-never-opened")
    assert code == 3 and out["error"] == ("FileExistsError" if failure == "collision" else "PermissionError")
    assert not device.called and ids.call_count == (10 if failure == "collision" else 1)
    assert snapshot(old) == before


@pytest.mark.parametrize("argv", [["--host", "0.0.0.0"], ["--device", "replay:invalid.json"]])
def test_console_invalid_parameters_still_precede_allocation(synthetic_ws, monkeypatch, capsys, argv):
    ids = Mock(side_effect=AssertionError("invalid parameters must not allocate"))
    device = Mock(side_effect=AssertionError("invalid parameters must not open devices"))
    monkeypatch.setattr(observe, "new_id", ids)
    monkeypatch.setattr(Workspace, "device", device)
    code, out = command(synthetic_ws, capsys, "console", *argv)
    assert code == 3 and out["error"] == "ValueError"
    assert not ids.called and not device.called and not synthetic_ws.runs_dir.exists()


def test_named_session_cli_continues_without_rewriting_observations(synthetic_ws, capsys):
    ws = synthetic_ws
    argv = ["--session", "continuation", "--device", "replay:motor_hm_base.json"]
    code, first = command(ws, capsys, "observe", *argv)
    assert code == 0
    root = ws.runs_dir / first["session"]
    observations = snapshot(root / "obs")
    events = (root / "events.jsonl").read_bytes()
    code, second = command(ws, capsys, "act", "launch", *argv)
    assert code == 0 and second["result"]["outcome"].startswith("passed")
    after = snapshot(root / "obs")
    assert all(after[k] == v for k, v in observations.items()) and len(after) > len(observations)
    assert (root / "events.jsonl").read_bytes().startswith(events)
    assert [e["event"] for e in read_jsonl(root / "events.jsonl")].count("session_open") == 1
