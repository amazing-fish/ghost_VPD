"""One run, one knowledge version. Synthetic assets; real CLI replay/record/readiness."""
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import SKILL, world
from verdict_fixtures import NAV_CASE, NAV_RULE
from vpdtest import cases as C
from vpdtest import knowledge as K
from vpdtest.knowledge import KnowledgeStore
from vpdtest.util import read_json, write_json


def asset(ws, status="confirmed"):
    kn = KnowledgeStore(ws.root)
    if status == "confirmed":
        kn.confirm(NAV_RULE, "pytest-synthetic", "isolated snapshot contract, not product approval")
    case = ws.load_case(NAV_CASE)
    case.update(status="candidate", sources=[{"type": "text_case", "ref": "synthetic snapshot fixture"}],
                steps=[{"id": "S1", "action": "ensure_state", "state": "my_tab", "oracle_refs": [NAV_RULE]}],
                assertions=[{"id": aid, "at": "S1", "type": "text_present", "text": "注册/登录",
                             "oracle_refs": [NAV_RULE]} for aid in ("A1", "A2")])
    write_json(ws.case_path(NAV_CASE), case)
    assert not C.lint(case, ws.app(case["app"]), kn, ws.policy(), runs_dir=ws.runs_dir)
    return kn


def deps(ws, kn=None):
    return C.dependency_hash(ws.load_case(NAV_CASE), ws.app("motor_hm"), kn or KnowledgeStore(ws.root))


def peer(kn, **changes):
    return {**kn.get(NAV_RULE), "id": "K-TEST-SNAPSHOT-PEER", "expected": "opposite", **changes}


def test_snapshot_reads_once_and_defends_nested_return_values(verdict_ws, monkeypatch):
    kn = asset(verdict_ws)
    loaded = {"items": kn.items()}
    original = deepcopy(loaded["items"])
    reads = []

    def read(*args):
        reads.append(args)
        return loaded

    monkeypatch.setattr(K, "read_json", read)
    snap = kn.snapshot()
    loaded["items"][0]["applies_to"]["app"] = "external mutation"
    loaded["items"].clear()
    for result in (snap.items(), [snap.get(NAV_RULE)], list(snap.dependency_items([NAV_RULE]).values())):
        result[0]["source"]["ref"] = "caller mutation"
        result[0]["applies_to"].clear()
        result[0]["history"].append({"to": "deprecated"})
        result.clear()
    report = snap.oracle_status([NAV_RULE])
    report["items"][0]["applies_to"].clear()
    assert snap.items() == original
    assert snap.conflicts() == {}
    assert snap.snapshot().items() == original
    assert len(reads) == 1


def test_snapshot_isolated_from_live_and_still_binds_measured_facts(verdict_ws):
    ws = verdict_ws
    live = asset(ws)
    snap = live.snapshot()
    before = deps(ws, snap)
    assert snap.oracle_status([NAV_RULE])["status"] == "applicability_unknown"
    assert snap.bind({"app": "motor_hm", "app_version": "3.0.3.200-replay"}) is snap
    assert snap.oracle_status([NAV_RULE])["status"] == "confirmed"
    other = snap.snapshot()
    snap.bind({"app": "motor_hm", "app_version": "4.0.0"})
    assert snap.oracle_status([NAV_RULE])["status"] == "out_of_scope"
    assert other.oracle_status([NAV_RULE])["status"] == "confirmed"
    live.add(peer(live))
    assert live.oracle_status([NAV_RULE])["status"] == "conflicted"
    assert live.snapshot().conflicts()
    assert not snap.conflicts() and deps(ws, snap) == before != deps(ws, live)
    assert live.get("K-TEST-SNAPSHOT-PEER") and snap.get("K-TEST-SNAPSHOT-PEER") is None


def test_empty_snapshot_never_falls_back_to_live_file(verdict_ws, tmp_path):
    live = KnowledgeStore(tmp_path / "empty-knowledge")
    snap = live.snapshot()
    live.add(peer(KnowledgeStore(verdict_ws.root)))
    assert live.items() and snap.items() == []
    assert snap.get("K-TEST-SNAPSHOT-PEER") is None
    assert snap.dependency_items([NAV_RULE]) == {NAV_RULE: {}}
    assert snap.oracle_status([NAV_RULE])["status"] == "missing"
    with pytest.raises(PermissionError):
        snap._save([])


def test_snapshot_read_failure_precedes_run_allocation_and_device(verdict_ws, monkeypatch):
    from vpdtest import runner

    asset(verdict_ws)

    def fail(*args):
        raise OSError("synthetic knowledge read failed")

    def forbidden(*args, **kwargs):
        pytest.fail("knowledge capture failed before any run directory or device")

    monkeypatch.setattr(K, "read_json", fail)
    monkeypatch.setattr(runner, "RunStore", forbidden)
    monkeypatch.setattr(verdict_ws, "device", forbidden)
    with pytest.raises(OSError, match="synthetic knowledge read failed"):
        runner.run_case(verdict_ws, NAV_CASE, device_spec=world("motor_hm_base.json"))
    assert not verdict_ws.runs_dir.exists()


@pytest.mark.parametrize("items", [None, {}, "invalid", 42, True])
def test_malformed_snapshot_rejected_before_run_allocation_or_device(verdict_ws, monkeypatch, items):
    from vpdtest import runner

    kn = asset(verdict_ws)
    write_json(kn.path, {"items": items})
    original = kn.path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("malformed knowledge reached run directory or device")

    monkeypatch.setattr(runner, "RunStore", forbidden)
    monkeypatch.setattr(verdict_ws, "device", forbidden)
    with pytest.raises(ValueError, match="items.*列表"):
        kn.snapshot()
    with pytest.raises(ValueError, match="items.*列表"):
        runner.run_case(verdict_ws, NAV_CASE, device_spec=world("motor_hm_base.json"))
    assert not verdict_ws.runs_dir.exists() and kn.path.read_bytes() == original


@pytest.mark.parametrize("operation", ["add", "confirm", "resolve", "_save"])
def test_snapshot_cannot_write_back(verdict_ws, operation):
    live = asset(verdict_ws)
    live.add(peer(live))
    snap = live.snapshot()
    original = snap.items()
    disk = live.path.read_bytes()
    calls = {"add": lambda: snap.add(peer(snap, id="new")),
             "confirm": lambda: snap.confirm(NAV_RULE, "pytest-synthetic"),
             "resolve": lambda: snap.resolve(snap.get(NAV_RULE)["subject"], NAV_RULE,
                                             "pytest-synthetic", "isolated fixture"),
             "_save": lambda: snap._save([])}
    with pytest.raises(PermissionError, match="只读|read.only"):
        calls[operation]()
    assert snap.items() == original and live.path.read_bytes() == disk


# Child trace controls scheduling only. Real functions return unchanged, and the
# writer uses a second public CLI process. No product definitions or run JSON edits.
DRIVER = r'''
import json
from pathlib import Path
import sys
import time
from vpdtest.cli import main

root, fixture, stage, marker, release = sys.argv[1:]
paused = False

def trace(frame, event, result):
    global paused
    name = frame.f_code.co_name
    module = frame.f_globals.get('__name__')
    hit = ((stage == 'admission' and module == 'vpdtest.verdict' and name == 'oracle_guards' and event == 'call')
           or (stage == 'execution' and module == 'vpdtest.execute' and name == 'run_step' and event == 'return'
               and frame.f_locals['step'].get('id') == 'S1')
           or (stage == 'assertions' and module == 'vpdtest.verdict' and name == 'evaluate_assertion'
               and event == 'return' and frame.f_locals['a']['id'] == 'A1')
           or (stage == 'verdict' and module == 'vpdtest.verdict' and name == 'case_verdict' and event == 'return'))
    if not paused and hit:
        paused = True
        Path(marker).write_text(json.dumps({'stage': stage, 'function': name, 'event': event}), encoding='utf-8')
        deadline = time.monotonic() + 90
        while not Path(release).exists():
            if time.monotonic() > deadline:
                raise RuntimeError('test scheduling release timeout')
            time.sleep(.01)
    return trace

sys.settrace(trace)
code = main(['--workspace', root, 'run', 'TC-TEST-VERDICT-NAV', '--device', fixture, '--record-replay'])
sys.settrace(None)
raise SystemExit(code)
'''


def cli_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("VPD_SECRET_")}
    env.update(PYTHONPATH=os.pathsep.join([str(SKILL), env.get("PYTHONPATH", "")]),
               PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    return env


def cli(ws, *args):
    result = subprocess.run([sys.executable, "-m", "vpdtest", "--workspace", str(ws.root), *args],
                            env=cli_env(), capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return json.loads(result.stdout)


def add_peer(ws, **changes):
    path = ws.root.parent / "synthetic-peer.json"
    write_json(path, peer(KnowledgeStore(ws.root), **changes))
    result = cli(ws, "knowledge", "add", str(path))
    assert result["status"] == "unconfirmed"


def race(ws, tmp_path, stage, change):
    driver = tmp_path / "schedule.py"
    driver.write_text(DRIVER, encoding="utf-8")
    release = tmp_path / "release"
    markers = [tmp_path / f"paused-{i}.json" for i in range(2)]
    processes = []
    try:
        for marker in markers:
            processes.append(subprocess.Popen(
                [sys.executable, str(driver), str(ws.root), world("motor_hm_base.json"),
                 stage, str(marker), str(release)], env=cli_env(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"))
        deadline = time.monotonic() + 60
        while not all(marker.exists() for marker in markers):
            assert all(p.poll() is None for p in processes), [p.communicate() for p in processes if p.poll() is not None]
            assert time.monotonic() < deadline, "CLI did not reach scheduling barrier"
            time.sleep(.01)
        change()
    finally:
        release.write_text("resume real CLI", encoding="utf-8")
        outputs = []
        for process in processes:
            try:
                outputs.append(process.communicate(timeout=60))
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
    runs = []
    for process, (stdout, stderr) in zip(processes, outputs):
        assert process.returncode == 0, (stdout, stderr)
        brief = json.loads(stdout)
        runs.append(read_json(Path(brief["report"]).with_name("run.json")))
    assert len({r["run_id"] for r in runs}) == 2
    return runs


def readiness(ws):
    paths = [ws.case_path(NAV_CASE), KnowledgeStore(ws.root).path]
    before = [p.read_bytes() for p in paths]
    row = cli(ws, "case", "readiness", NAV_CASE, "--reviewer", "pytest-synthetic", "--allow-offline")["results"][0]
    assert [p.read_bytes() for p in paths] == before
    assert "error" not in row
    return row


def coherent(runs, before, status="confirmed", verdict="passed"):
    for run in runs:
        assert run["case"]["deps"] == before
        assert run["asset_review"]["current"]["dependency_hash"] == before
        assert run["asset_review"]["required"] is False
        assert run["validation_level"] == "offline_replay"
        assert run["verdict"]["verdict"] == verdict
        assert {a["oracle"]["status"] for a in run["assertions"]} == {status}
        assert {k["status"] for k in run["knowledge_snapshot"]} == {status}
        assert run["oracle_facts"]["app_version"] == "3.0.3.200-replay"


@pytest.mark.parametrize("stage", ["admission", "execution", "assertions", "verdict"])
def test_cli_concurrent_conflict_preserves_actual_old_run_binding(verdict_ws, tmp_path, stage):
    ws = verdict_ws
    asset(ws)
    before = deps(ws)
    runs = race(ws, tmp_path, stage, lambda: add_peer(ws))
    coherent(runs, before)
    assert deps(ws) != before
    stored = ws.load_case(NAV_CASE)["replays"]
    assert len(stored) == 2 and {r["deps"] for r in stored} == {before}
    row = readiness(ws)
    assert not row["replay"]["ok"] and row["promotion"]["status"] == "blocked"
    historical = {p: p.read_bytes() for r in runs for p in [ws.runs_dir / r["run_id"] / "run.json"]}
    result = cli(ws, "run", NAV_CASE, "--device", world("motor_hm_base.json"))
    assert result["verdict"] == "pending"
    assert all(p.read_bytes() == data for p, data in historical.items())
    assert len(ws.load_case(NAV_CASE)["replays"]) == 2


@pytest.mark.parametrize("changes", [{"subject": "unrelated"}, {"expected": "navigation"},
                                    {"applies_to": {"app_version": "4.0.0"}}])
def test_cli_unrelated_same_expected_and_disjoint_scope_remain_eligible(verdict_ws, tmp_path, changes):
    ws = verdict_ws
    asset(ws)
    before = deps(ws)
    coherent(race(ws, tmp_path, "verdict", lambda: add_peer(ws, **changes)), before)
    assert deps(ws) == before
    row = readiness(ws)
    assert row["replay"]["ok"] and row["promotion"]["status"] == "eligible"


def test_cli_midrun_confirm_applies_next_run_without_rewriting_pending(verdict_ws, tmp_path):
    ws = verdict_ws
    asset(ws, status="unconfirmed")
    before = deps(ws)
    runs = race(ws, tmp_path, "assertions", lambda: cli(
        ws, "knowledge", "confirm", NAV_RULE, "--reviewer", "pytest-synthetic", "--basis", "synthetic fixture only"))
    coherent(runs, before, "unconfirmed", "pending")
    assert deps(ws) == before  # Confirmation/history still do not change semantic dependencies.
    saved = [deepcopy(r) for r in ws.load_case(NAV_CASE)["replays"]]
    assert not readiness(ws)["replay"]["ok"]
    for count in (1, 2):
        result = cli(ws, "run", NAV_CASE, "--device", world("motor_hm_base.json"), "--record-replay")
        assert result["verdict"] == "passed"
        assert readiness(ws)["replay"]["ok"] is (count == 2)
    assert ws.load_case(NAV_CASE)["replays"][:2] == saved
    for run in runs:
        assert read_json(ws.runs_dir / run["run_id"] / "run.json") == run


@pytest.mark.parametrize("change", ["deprecated", "invalid", "missing"])
def test_current_invalid_or_retired_knowledge_cannot_reuse_old_runs(verdict_ws, change):
    ws = verdict_ws
    kn = asset(ws)
    before = deps(ws)
    for _ in range(2):
        assert cli(ws, "run", NAV_CASE, "--device", world("motor_hm_base.json"), "--record-replay")["verdict"] == "passed"
    assert readiness(ws)["replay"]["ok"]
    items = kn.items()
    if change == "missing":
        items = [i for i in items if i["id"] != NAV_RULE]
    else:
        next(i for i in items if i["id"] == NAV_RULE)["status"] = change
    write_json(kn.path, {"items": items})  # Synthetic lifecycle fixture, no production knowledge.
    assert deps(ws) != before
    row = readiness(ws)
    assert row["issues"] and not row["replay"]["ok"] and row["promotion"]["status"] == "blocked"
    assert cli(ws, "run", NAV_CASE, "--device", world("motor_hm_base.json"))["verdict"] == "pending"
