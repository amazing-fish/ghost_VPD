"""Synthetic replay only: interruption is diagnostic, never a stop or verdict."""
import json
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from vpdtest import cli, observe, runner
from vpdtest.run_diagnostic import RunDiagnostic
from vpdtest.util import write_json
from vpdtest.workspace import Workspace


@pytest.fixture
def rig(tmp_path):
    root = tmp_path / "data"
    app = root / "apps" / "synthetic"
    write_json(root / "policy.json", {"version": "synthetic", "allowed_packages": ["synthetic.alert"],
                                      "max_actions_per_run": 10, "max_wait_s": 2})
    write_json(app / "app.json", {"id": "synthetic", "bundle": "synthetic.alert", "ability": "EntryAbility"})
    write_json(app / "targets.json", {"targets": {}})
    write_json(app / "states.json", {"states": {"home": {"require_text": ["Synthetic"]}},
                                     "interrupts": {}, "recipes": {}})
    world = {"bundle": "synthetic.alert", "app_version": "1.0", "screen": [400, 800],
             "display": {"density": 1, "device_form": "bar"}, "initial": {"installed": True},
             "launch_screen": "home", "screens": {"home": {"root": {"t": "Column", "b": [0, 0, 400, 800],
             "children": [{"t": "Text", "text": "Synthetic", "b": [10, 20, 380, 55]}]}}}}
    write_json(tmp_path / "world.json", world)
    case = {"id": "TC-SYNTHETIC", "app": "synthetic", "version": 1, "status": "draft",
            "steps": [{"id": "S1", "action": "launch", "expect_state": "home", "retry": 0}],
            "assertions": [], "cleanup": []}
    ws = Workspace(str(root), load_local_config=False)
    write_json(ws.case_path(case["id"]), case)
    return ws, case, "replay:" + str(tmp_path / "world.json")


def run(rig, **kw):
    ws, case, spec = rig
    return runner.run_case(ws, case=case, device_spec=spec, propose=False, **kw)


def test_normal_run_has_no_diagnostic(rig):
    result = run(rig)
    assert not (Path(result["dir"]) / "run_diagnostic.json").exists()
    assert (Path(result["dir"]) / "run.json").exists()


@pytest.mark.parametrize("phase", ["device", "context", "run_start", "step_start", "step", "report", "run_end"])
@pytest.mark.parametrize("error_class", [OSError, KeyboardInterrupt])
def test_original_exception_and_boundary_survive(rig, monkeypatch, phase, error_class):
    error = error_class("synthetic first error")
    def fail(*args, **kwargs):
        raise error
    if phase == "device":
        monkeypatch.setattr(rig[0], "device", fail)
    elif phase == "context":
        monkeypatch.setattr(runner, "RunContext", fail)
    elif phase in ("run_start", "step_start", "run_end"):
        original = observe.RunStore.event
        def event(self, kind, **data):
            if kind == phase:
                fail()
            return original(self, kind, **data)
        monkeypatch.setattr(observe.RunStore, "event", event)
    elif phase == "step":
        original = observe.write_json
        def write(path, data, **kwargs):
            if Path(path).name == "observation.json":
                fail()
            return original(path, data, **kwargs)
        monkeypatch.setattr(observe, "write_json", write)
    else:
        monkeypatch.setattr(runner, "render_run", fail)
    with pytest.raises(error_class) as caught:
        run(rig)
    assert caught.value is error
    d = error.run_diagnostic
    assert d["phase"] == phase and d["persisted"] is True
    assert d["step"] == ("S1" if phase in ("step", "step_start") else None)
    assert json.loads((Path(d["dir"]) / "run_diagnostic.json").read_text()) == d
    assert "fail" in [f.name for f in __import__("traceback").extract_tb(error.__traceback__)]
    if phase in ("report", "run_end"):
        assert (Path(d["dir"]) / "run.json").exists()
    else:
        assert not (Path(d["dir"]) / "run.json").exists()


def test_validation_and_collision_do_not_write(rig):
    ws, case, _ = rig
    case["steps"].append(dict(case["steps"][0]))
    with pytest.raises(ValueError) as error:
        run(rig)
    assert not hasattr(error.value, "run_diagnostic")
    case["steps"].pop()
    existing = ws.runs_dir / "run-owned"
    existing.mkdir(parents=True)
    sentinel = existing / "sentinel"
    sentinel.write_text("untouched")
    with pytest.raises(FileExistsError) as error:
        run(rig, run_id="run-owned")
    assert not hasattr(error.value, "run_diagnostic")
    assert list(existing.iterdir()) == [sentinel]


def test_owned_initialization_failure_has_locator(rig, monkeypatch):
    original = Path.mkdir
    error = OSError("synthetic obs allocation failure")
    def mkdir(self, *args, **kwargs):
        if self.name == "obs":
            raise error
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(OSError) as caught:
        run(rig)
    assert caught.value is error
    assert error.run_diagnostic["phase"] == "initialization"
    assert error.run_diagnostic["persisted"] is True
    assert set(error.run_diagnostic["safety"].values()) == {None}


@pytest.mark.parametrize("fault", ["serialize", "write", "replace"])
def test_diagnostic_fault_does_not_mask_primary(rig, monkeypatch, fault):
    import vpdtest.run_diagnostic as module
    error = OSError("primary")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(rig[0], "device", fail)
    def secondary(*args, **kwargs):
        raise RuntimeError("secondary")
    if fault == "serialize":
        monkeypatch.setattr(module, "json", SimpleNamespace(dumps=secondary))
    elif fault == "replace":
        monkeypatch.setattr(module.os, "replace", secondary)
    else:
        original = Path.open
        def opening(self, *args, **kwargs):
            if self.name == "run_diagnostic.json.tmp":
                secondary()
            return original(self, *args, **kwargs)
        monkeypatch.setattr(Path, "open", opening)
    with pytest.raises(OSError) as caught:
        run(rig)
    assert caught.value is error and error.run_diagnostic["persisted"] is False


@pytest.mark.parametrize("interrupt", [False, True])
def test_public_cli_io_and_real_sigint(rig, monkeypatch, capsys, interrupt):
    ws, case, spec = rig
    original = observe.write_json
    def write(path, data, **kwargs):
        if Path(path).name == "observation.json":
            if interrupt:
                signal.raise_signal(signal.SIGINT)
            raise OSError(28, "Synthetic disk full")
        return original(path, data, **kwargs)
    monkeypatch.setattr(observe, "write_json", write)
    code = cli.main(["--workspace", str(ws.root), "run", case["id"], "--device", spec])
    out = json.loads(capsys.readouterr().out)
    assert code == (130 if interrupt else 3)
    assert out["error"] == ("KeyboardInterrupt" if interrupt else "OSError")
    assert out["run_diagnostic"]["phase"] == "step"
    assert out["run_diagnostic"]["persisted"] is True
    assert ("trace" in out) is not interrupt


def test_safety_snapshot_never_calls_locked(tmp_path):
    class Motion:
        armed = None
        def __init__(self):
            self.armed, self.timeout = {"private": "must not leak"}, None
        def locked(self):
            pytest.fail("diagnostic called mutating method")
    d = RunDiagnostic()
    d.allocated(observe.RunStore(tmp_path, "run-snapshot"))
    d.context = SimpleNamespace(motion=Motion(), safety_hold={"secret": "must not leak"})
    error = OSError()
    d.capture(error)
    assert error.run_diagnostic["safety"] == {"motion_armed": True, "motion_timeout": False, "safety_hold": True}
    assert "must not leak" not in json.dumps(error.run_diagnostic)


def test_resume_does_not_signal_fresh_ownership(tmp_path):
    observe.RunStore(tmp_path, "run-old")
    calls = []
    observe.RunStore(tmp_path, "run-old", resume=True, on_allocated=calls.append)
    assert calls == []


def test_systemexit_not_caught(rig, monkeypatch):
    def stop(*args, **kwargs):
        raise SystemExit(17)
    monkeypatch.setattr(rig[0], "device", stop)
    with pytest.raises(SystemExit) as caught:
        run(rig)
    assert caught.value.code == 17 and not hasattr(caught.value, "run_diagnostic")


def test_repeated_capture_preserves_first_record(tmp_path):
    d = RunDiagnostic()
    d.allocated(observe.RunStore(tmp_path, "run-once"))
    first, second = OSError("first"), ValueError("second")
    d.capture(first)
    path = tmp_path / "run-once" / "run_diagnostic.json"
    before = path.read_bytes()
    d.phase = "later"
    d.capture(second)
    assert path.read_bytes() == before
    assert not hasattr(second, "run_diagnostic")
    assert first.run_diagnostic["phase"] == "initialization"


@pytest.mark.parametrize("kind", ["getter", "unserializable", "setter"])
def test_diagnostic_attribute_failure_keeps_cli_error(rig, monkeypatch, capsys, kind):
    class RejectAttribute(OSError):
        def __setattr__(self, key, value):
            if key == "run_diagnostic":
                raise RuntimeError("no attributes")
            return super().__setattr__(key, value)
        @property
        def run_diagnostic(self):
            raise RuntimeError("no getter")
    error = RejectAttribute("original") if kind != "unserializable" else OSError("original")
    if kind == "unserializable":
        error.run_diagnostic = {"bad": object()}
    def fail(ws, args):
        if kind == "setter":
            d = RunDiagnostic()
            d.allocated(observe.RunStore(ws.runs_dir))
            d.capture(error)
        raise error
    monkeypatch.setitem(cli.HANDLERS, "run", fail)
    ws, case, spec = rig
    assert cli.main(["--workspace", str(ws.root), "run", case["id"], "--device", spec]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["message"] == "original" and "run_diagnostic" not in output
    assert "fail" in output["trace"]


def test_device_send_then_journal_failure_is_never_replayed(rig, monkeypatch):
    from vpdtest.devices.replay import ReplayDevice
    calls = []
    original_launch, original_event = ReplayDevice.launch, observe.RunStore.event
    def launch(self, *args):
        calls.append(args)
        return original_launch(self, *args)
    error = OSError("journal failed after send")
    def event(self, kind, **data):
        if kind == "dispatch":
            assert data["sent"] is True
            raise error
        return original_event(self, kind, **data)
    monkeypatch.setattr(ReplayDevice, "launch", launch)
    monkeypatch.setattr(observe.RunStore, "event", event)
    with pytest.raises(OSError) as caught:
        run(rig)
    assert caught.value is error and len(calls) == 1
    record = error.run_diagnostic
    assert record["phase"] == "step" and record["step"] == "S1"
    assert "sent" not in record and "safe" not in record
    assert not (Path(record["dir"]) / "run.json").exists()


def test_rejected_exception_attribute_still_persists_run_diagnostic(rig, monkeypatch):
    import traceback
    from vpdtest.console.progress import summarize

    class RejectAttribute(OSError):
        def __setattr__(self, name, value):
            if name == "run_diagnostic":
                raise RuntimeError("diagnostic attribute unavailable")
            super().__setattr__(name, value)

    error = RejectAttribute("original failure")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(rig[0], "device", fail)
    with pytest.raises(RejectAttribute) as caught:
        run(rig, run_id="run-reject-attribute")
    assert caught.value is error
    assert "fail" in [frame.name for frame in traceback.extract_tb(caught.value.__traceback__)]
    assert not hasattr(error, "run_diagnostic")
    directory = rig[0].runs_dir / "run-reject-attribute"
    record = json.loads((directory / "run_diagnostic.json").read_text(encoding="utf-8"))
    assert record["persisted"] is True and record["phase"] == "device"
    assert record["error_type"] == "RejectAttribute"
    summary = summarize(directory)
    assert summary["state"] == "tool_error"
    assert summary["progress_reliable"] is False  # run_start was never written.


def test_unreadable_safety_snapshot_does_not_block_diagnostic(tmp_path):
    class BrokenSnapshot:
        @property
        def __dict__(self):
            raise RuntimeError("optional safety metadata unavailable")

    diagnostic = RunDiagnostic()
    store = observe.RunStore(tmp_path, "run-snapshot-error")
    diagnostic.allocated(store)
    diagnostic.context = BrokenSnapshot()
    error = OSError("original failure")
    diagnostic.capture(error)
    record = json.loads((store.dir / "run_diagnostic.json").read_text(encoding="utf-8"))
    assert record["persisted"] is True and error.run_diagnostic["persisted"] is True
    assert record["safety"] == {"motion_armed": None, "motion_timeout": None, "safety_hold": None}


def test_cli_reports_failed_diagnostic_persistence(rig, monkeypatch, capsys):
    import vpdtest.run_diagnostic as module
    original = module.os.replace
    def replace(src, target):
        if Path(target).name == "run_diagnostic.json":
            raise OSError("diagnostic unavailable")
        return original(src, target)
    def fail(self, *args, **kwargs):
        raise OSError("original context failure")
    monkeypatch.setattr(module.os, "replace", replace)
    monkeypatch.setattr(runner, "RunContext", fail)
    ws, case, spec = rig
    assert cli.main(["--workspace", str(ws.root), "run", case["id"], "--device", spec]) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["message"] == "original context failure"
    assert out["run_diagnostic"]["persisted"] is False


def test_suite_propagates_failed_current_subrun(rig, monkeypatch, capsys):
    from vpdtest import commands
    ws, case, spec = rig
    monkeypatch.setattr(Workspace, "load_suite", lambda self, name: {"cases": [case["id"], case["id"]]})
    monkeypatch.setattr(commands.case_mod, "preflight_batch", lambda *args: None)
    # Synthetic review gate only; exercise the actual suite -> run_case -> CLI path.
    monkeypatch.setattr(runner, "require_review", runner.review_status)
    error = OSError("subrun allocation succeeded")
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr(runner, "RunContext", fail)
    code = cli.main(["--workspace", str(ws.root), "suite", "synthetic", "--device", spec])
    out = json.loads(capsys.readouterr().out)
    assert code == 3 and len(calls) == 1
    assert out["run_diagnostic"]["run_id"].startswith("run-")
    suite = next(ws.runs_dir.glob("suite-*"))
    events = (suite / "events.jsonl").read_text()
    assert 'case_start' in events and 'suite_end' not in events
    assert not (suite / "run_diagnostic.json").exists()


@pytest.mark.parametrize("mode", ["blocked", "device_result"])
def test_expected_failures_do_not_become_diagnostics(rig, monkeypatch, mode):
    from vpdtest.devices import DeviceResult
    from vpdtest.devices.replay import ReplayDevice
    if mode == "blocked":
        original = ReplayDevice.preflight
        def blocked(self, bundle):
            result = original(self, bundle)
            result["ok"] = False
            result["checks"].append({"check": "synthetic_missing", "ok": False, "error_class": "app_missing"})
            return result
        monkeypatch.setattr(ReplayDevice, "preflight", blocked)
    else:
        monkeypatch.setattr(ReplayDevice, "launch", lambda *args: DeviceResult(
            "launch", False, False, "synthetic_executor_error", "synthetic result"))
    result = run(rig)
    assert not (Path(result["dir"]) / "run_diagnostic.json").exists()
    assert 'run_end' in (Path(result["dir"]) / "events.jsonl").read_text()
    assert result["verdict"]["verdict"] != "passed"
