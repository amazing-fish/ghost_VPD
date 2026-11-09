"""Issue #30：审核资产不代替成熟度、Oracle 或设备授权；仅临时工作区。"""
import pytest
from conftest import world
from test_goal_loop import cli

from vpdtest import cases as C
from vpdtest import commands
from vpdtest.experience import ExperienceStore
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

BASE = world("motor_hm_base.json")
REVIEWER = "pytest-human-reviewer"
BASIS = "isolated fixture review: goal, steps, oracle refs, sources and cleanup"


def _asset(ws, cid="TC-REVIEW-1", status="exploration_record"):
    case = ws.load_case("TC-MOTOR-HM-001")
    case.update(id=cid, status=status, sources=[{"type": "session", "run": "session-fixture"}], history=[],
                knowledge_used=[{"id": None, "note": "Synthetic navigation fixture; no observed product knowledge",
                                 "rationale": "Use the declared goal and existing independent oracle refs"}])
    write_json(ws.case_path(cid), case)
    assert not C.lint(case, ws.app(case["app"]), KnowledgeStore(ws.root), ws.policy(), runs_dir=ws.runs_dir)
    return case


def _approve(ws, cid="TC-REVIEW-1"):
    return C.approve(ws, cid, KnowledgeStore(ws.root), reviewer=REVIEWER, basis=BASIS)


def _review(ws, cid="TC-REVIEW-1"):
    return C.review_status(ws, ws.load_case(cid))


def test_complete_unreviewed_exploration_cannot_become_candidate(ws):
    case = _asset(ws)
    before = ws.case_path(case["id"]).read_bytes()
    with pytest.raises(PermissionError, match="审核"):
        C.promote(ws, case["id"], "candidate", KnowledgeStore(ws.root))
    assert ws.case_path(case["id"]).read_bytes() == before


def test_approval_is_explicit_traceable_and_does_not_promote_or_confirm(ws):
    case = _asset(ws)
    knowledge = (ws.root / "knowledge/items.json").read_bytes()
    h = C.content_hash(case)
    approved = _approve(ws)
    info = _review(ws)
    assert approved["status"] == "exploration_record"
    assert info["required"] and info["valid"] and info["state"] == "approved"
    record = approved["asset_review"]
    assert record["reviewer"] == REVIEWER and record["basis"] == BASIS and record["ts"]
    assert record["binding"]["case_id"] == case["id"] and record["binding"]["content_hash"] == h
    assert C.content_hash(approved) == h
    assert (ws.root / "knowledge/items.json").read_bytes() == knowledge
    promoted = C.promote(ws, case["id"], "candidate", KnowledgeStore(ws.root))
    assert promoted["status"] == "candidate" and _review(ws)["valid"]


@pytest.mark.parametrize("reviewer,basis", [(None, BASIS), ("", BASIS), ("   ", BASIS), (7, BASIS),
                                            (REVIEWER, None), (REVIEWER, "\n"), (REVIEWER, {})])
def test_invalid_approval_input_never_writes(ws, reviewer, basis):
    case = _asset(ws)
    before = ws.case_path(case["id"]).read_bytes()
    with pytest.raises(ValueError):
        C.approve(ws, case["id"], KnowledgeStore(ws.root), reviewer=reviewer, basis=basis)
    assert ws.case_path(case["id"]).read_bytes() == before


@pytest.mark.parametrize("status", ["draft", "candidate", "replay_verified", "regression"])
def test_relabelling_session_asset_does_not_erase_need_for_review(ws, status):
    case = _asset(ws, status=status)
    assert _review(ws)["required"] and not _review(ws)["valid"]
    with pytest.raises(PermissionError, match="审核"):
        C.require_review(ws, case)


@pytest.mark.parametrize("trace", ["history", "step", "cleanup", "safe_stop", "origin"])
def test_legacy_provenance_survives_status_and_source_changes(ws, trace):
    case = _asset(ws, status="draft")
    case["sources"] = [{"type": "reviewer", "ref": "not an approval"}]
    if trace == "history":
        case["history"] = [{"from": "exploration_record", "to": "draft"}]
    elif trace == "origin":
        case["origin"] = {"type": "session"}
    else:
        if trace == "step":
            case["steps"][0]["from_session"] = {"session": "fixture"}
        elif trace == "cleanup":
            case["cleanup"][0]["from_transition"] = "T1"
        else:
            case["safety"] = {"safe_stop": [{"action": "tap", "from_session": {"session": "fixture"}}]}
    with pytest.raises(PermissionError, match="审核"):
        C.require_review(ws, case)


@pytest.mark.parametrize("change", ["step", "purpose", "goal", "sources", "knowledge_used", "id", "target", "rule", "policy", "layout", "app"])
def test_review_binds_execution_and_reviewed_context(ws, change):
    case = _asset(ws)
    _approve(ws)
    edited = ws.load_case(case["id"])
    if change == "step":
        edited["steps"][0]["timeout_s"] = 9
    elif change == "purpose":
        edited["purpose"] += " changed"
    elif change == "goal":
        edited["goal"] = "different goal"
    elif change == "sources":
        edited["sources"][0]["excluded"] = [{"seq": 1, "reason": "changed"}]
    elif change == "knowledge_used":
        edited["knowledge_used"][0]["rationale"] += " changed"
    elif change == "id":
        edited["id"] = "TC-COPIED-APPROVAL"
    else:
        paths = {"target": ws.root / "apps/motor_hm/targets.json", "rule": ws.root / "knowledge/items.json",
                 "policy": ws.root / "policy.json", "layout": ws.root / "layout.json", "app": ws.root / "apps/motor_hm/app.json"}
        p = paths[change]
        data = read_json(p)
        if change == "target":
            data["targets"]["tab_my"]["desc"] = "changed target definition"
        elif change == "rule":
            next(i for i in data["items"] if i["id"] == "K-MOT-001")["statement"] += " changed"
        else:
            data["review_fixture_note"] = "changed"
        write_json(p, data)
    write_json(ws.case_path(case["id"]), edited)
    info = _review(ws)
    assert info["required"] and not info["valid"] and info["state"] == "stale"
    with pytest.raises(PermissionError, match="审核"):
        C.require_review(ws, edited)


def test_replays_history_and_active_knowledge_confirmation_do_not_invalidate_review(ws):
    _asset(ws)
    _approve(ws)
    case = ws.load_case("TC-REVIEW-1")
    case["replays"] = [{"run_id": "fixture", "verdict": "pending"}]
    case["history"].append({"from": "exploration_record", "to": "candidate"})
    case.update(status="candidate", product_status={"status": "conforming"})
    write_json(ws.case_path(case["id"]), case)
    kn = KnowledgeStore(ws.root)
    kn.confirm("K-MOT-001", "pytest-oracle-reviewer", "fixture confirmation")
    assert _review(ws)["valid"]


@pytest.mark.parametrize("record", [None, [], "reviewed", {}, {"reviewer": REVIEWER},
                                     {"reviewer": " ", "basis": BASIS, "ts": "t", "binding": {}}])
def test_malformed_review_record_is_not_an_approval(ws, record):
    case = _asset(ws)
    case["asset_review"] = record
    write_json(ws.case_path(case["id"]), case)
    assert not _review(ws)["valid"]
    with pytest.raises(PermissionError, match="审核"):
        C.require_review(ws, case)


@pytest.mark.parametrize("object_member", [False, True])
def test_suite_prechecks_all_members_before_first_run(ws, monkeypatch, object_member):
    _asset(ws)
    member = {"id": "TC-REVIEW-1", "env": {"fixture": "yes"}} if object_member else "TC-REVIEW-1"
    write_json(ws.root / "suites/review-mixed.json", {"id": "review-mixed", "cases": ["TC-MOTOR-HM-001", member]})
    calls = []
    monkeypatch.setattr(commands, "run_case", lambda *a, **k: calls.append((a, k)) or {})
    with pytest.raises(PermissionError, match="审核"):
        cli(ws, "suite", "review-mixed", "--device", BASE, "--device", BASE)
    assert calls == [] and not list(ws.runs_dir.glob("suite-*"))


@pytest.mark.parametrize("selection", ["cases", "suite"])
def test_experience_batch_prechecks_before_validation_callback(ws, monkeypatch, selection):
    _asset(ws)
    write_json(ws.root / "suites/review-exp.json", {"id": "review-exp", "cases": ["TC-MOTOR-HM-001", "TC-REVIEW-1"]})
    calls = []
    monkeypatch.setattr(ExperienceStore, "validate", lambda *a, **k: calls.append((a, k)) or {})
    argv = ["--cases", "TC-MOTOR-HM-001", "TC-REVIEW-1"] if selection == "cases" else ["--suite", "review-exp"]
    with pytest.raises(PermissionError, match="审核"):
        cli(ws, "experience", "--app", "motor_hm", "validate", "fixture-xp", "--device", BASE, *argv)
    assert calls == []


def test_runner_batch_guard_rejects_before_device_and_run_store(ws, monkeypatch):
    _asset(ws)
    calls = []
    monkeypatch.setattr(ws, "device", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(PermissionError, match="审核"):
        run_case(ws, "TC-REVIEW-1", device_spec=BASE, require_asset_review=True)
    assert calls == [] and not list(ws.runs_dir.glob("run-*"))


def test_unapproved_single_replay_is_informal_even_if_status_is_regression(ws):
    _asset(ws, status="regression")
    run = run_case(ws, "TC-REVIEW-1", device_spec=BASE, propose=False)
    assert run["informal"] is True
    assert run["asset_review"]["required"] and not run["asset_review"]["valid"]


def test_cli_approve_and_approved_batch_keep_informal_and_oracle_semantics(ws):
    _asset(ws)
    out = cli(ws, "case", "approve", "TC-REVIEW-1", "--reviewer", REVIEWER, "--basis", BASIS)
    assert out["asset_review"]["valid"] and out["status"] == "exploration_record"
    write_json(ws.root / "suites/review-ok.json", {"id": "review-ok", "cases": ["TC-REVIEW-1"]})
    row = cli(ws, "suite", "review-ok", "--device", BASE)["results"][0]
    assert row["informal"] is True and row["asset_review"]["valid"]
    assert row["verdict"] == "passed"


def test_manual_draft_batch_path_and_review_readout_remain_available(ws):
    info = cli(ws, "case", "review", "TC-MOTOR-HM-001")["asset_review"]
    assert not info["required"] and info["state"] == "not_required"
    row = cli(ws, "suite", "motor_hm_smoke", "--device", BASE)["results"][0]
    assert row["informal"] is True


@pytest.mark.parametrize("field,value", [("schema", True), ("schema", "1"), ("schema", 2),
                                        ("ts", "x"), ("ts", "2026-10-13T00:00:00"),
                                        ("reviewer", " "), ("basis", "\n"), ("binding", []), ("binding", {})])
def test_corrupt_complete_review_is_rejected(ws, field, value):
    _asset(ws)
    case = _approve(ws)
    case["asset_review"][field] = value
    with pytest.raises(PermissionError, match="审核"):
        C.require_review(ws, case)


def test_incomplete_asset_cannot_be_approved(ws):
    case = _asset(ws)
    case["purpose"] = ""
    write_json(ws.case_path(case["id"]), case)
    before = ws.case_path(case["id"]).read_bytes()
    with pytest.raises(PermissionError, match="不完整"):
        _approve(ws)
    assert ws.case_path(case["id"]).read_bytes() == before


@pytest.mark.parametrize("change", ["template", "system_dialogs"])
def test_review_invalidates_when_execution_environment_changes(ws, change):
    _asset(ws)
    _approve(ws)
    if change == "template":
        p = ws.root / "apps/motor_hm/templates/review-fixture.bin"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"changed template fixture")
    else:
        write_json(ws.root / "system_dialogs.json", {"dialogs": {"fixture": {"review": "pending"}}})
    info = _review(ws)
    assert info["state"] == "stale"
    with pytest.raises(PermissionError, match="审核"):
        C.require_review(ws, ws.load_case("TC-REVIEW-1"))


def test_engine_source_change_keeps_review_and_reports_changed_files(ws, monkeypatch):
    engine_file = ws.root.parent / "review-engine-fixture/runner.py"
    engine_file.parent.mkdir()
    engine_file.write_bytes(b"before = 1\n")
    monkeypatch.setattr(C, "__file__", str(engine_file.parent / "cases.py"))
    _asset(ws)
    _approve(ws)
    engine_file.write_bytes(b"after = 2\n")
    info = _review(ws)
    assert info["state"] == "approved" and info["valid"] is True
    assert info["engine_changes"]["modified"] == ["runner.py"]
    assert C.require_review(ws, ws.load_case("TC-REVIEW-1"))["valid"] is True


def test_suite_rechecks_after_preflight_before_device(ws, monkeypatch):
    _asset(ws)
    _approve(ws)
    write_json(ws.root / "suites/review-recheck.json", {"id": "review-recheck", "cases": ["TC-REVIEW-1"]})
    original = C.preflight_batch
    devices = []

    def preflight_then_edit(*args):
        result = original(*args)
        case = ws.load_case("TC-REVIEW-1")
        case["purpose"] += " changed after initial preflight"
        write_json(ws.case_path(case["id"]), case)
        return result

    monkeypatch.setattr(C, "preflight_batch", preflight_then_edit)
    monkeypatch.setattr(ws, "device", lambda *a, **k: devices.append((a, k)))
    with pytest.raises(PermissionError, match="审核失效"):
        cli(ws, "suite", "review-recheck", "--device", BASE)
    assert not devices and not list(ws.runs_dir.glob("run-*"))


@pytest.mark.parametrize("change_after_baseline", [False, True])
def test_experience_baseline_trial_recheck_real_runner(ws, monkeypatch, change_after_baseline):
    """实际经验算法调度 baseline/trial；回调走真实 runner 和回放设备。"""
    _asset(ws)
    _approve(ws)
    xs = ExperienceStore(ws.root, "motor_hm")
    xs.save_item({"id": "fixture-xp", "kind": "wait_timeout", "status": "candidate",
                  "change": {"key": "TC-REVIEW-1:S2", "timeout_s": 10}, "origin_case": "TC-REVIEW-1",
                  "evidence": [{"device": BASE}], "validations": [], "history": []})
    runs = []

    def record_run(*args, **kwargs):
        run = run_case(*args, **kwargs)
        runs.append(run)
        if change_after_baseline and len(runs) == 1:
            case = ws.load_case("TC-REVIEW-1")
            case["purpose"] += " changed after baseline"
            write_json(ws.case_path(case["id"]), case)
        return run

    original_device = ws.device
    devices = []

    def device(*args, **kwargs):
        devices.append(args)
        return original_device(*args, **kwargs)

    monkeypatch.setattr(commands, "run_case", record_run)
    monkeypatch.setattr(ws, "device", device)
    args = ("experience", "--app", "motor_hm", "validate", "fixture-xp", "--device", BASE, "--cases", "TC-REVIEW-1")
    if change_after_baseline:
        with pytest.raises(PermissionError, match="审核失效"):
            cli(ws, *args)
    else:
        cli(ws, *args)
    expected = 1 if change_after_baseline else 2
    assert len(runs) == len(devices) == len(list(ws.runs_dir.glob("run-*"))) == expected
    assert all(r["asset_review"]["valid"] and r["asset_review"]["use"] == "batch" for r in runs)
    if not change_after_baseline:
        assert runs[0]["overlay"]["trial"] is None and runs[1]["overlay"]["trial"] == "fixture-xp"


def test_run_uses_checked_policy_layout_and_dialog_snapshots(ws, monkeypatch):
    _asset(ws)
    _approve(ws)
    original_policy, original_layout, original_device = ws.policy, ws.layout_config, ws.device
    policy_calls, layout_calls = [], []
    before = _review(ws)["current"]

    def once_policy():
        policy_calls.append(True)
        assert len(policy_calls) == 1, "runner/context reloaded an unchecked policy"
        return original_policy()

    def once_layout():
        layout_calls.append(True)
        assert len(layout_calls) == 1, "runner/context reloaded an unchecked layout"
        return original_layout()

    def device_then_change(*args, **kwargs):
        write_json(ws.root / "system_dialogs.json", {"dialogs": {"unchecked": {"window_names": ["x"]}}})
        return original_device(*args, **kwargs)

    from vpdtest import sysdialog
    from vpdtest.workspace import RunContext
    original_context = RunContext.__init__
    contexts, preflight_dialogs = [], []

    def context(self, *args, **kwargs):
        original_context(self, *args, **kwargs)
        contexts.append(self)

    monkeypatch.setattr(ws, "policy", once_policy)
    monkeypatch.setattr(ws, "layout_config", once_layout)
    monkeypatch.setattr(ws, "device", device_then_change)
    monkeypatch.setattr(RunContext, "__init__", context)
    monkeypatch.setattr(sysdialog, "preflight_dialog_check", lambda *a, **k: preflight_dialogs.append(k["dialogs"]) or {})
    run = run_case(ws, "TC-REVIEW-1", device_spec=BASE, propose=False, require_asset_review=True)
    assert run["asset_review"]["current"] == before
    assert run["policy"]["hash"] == before["policy_hash"]
    assert contexts[0].observer.dialogs == preflight_dialogs[0] and "unchecked" not in preflight_dialogs[0]


def test_approval_does_not_grant_runtime_side_effect_authorization(ws):
    case = _asset(ws)
    case["authorizations"] = ["user_data"]
    write_json(ws.case_path(case["id"]), case)
    _approve(ws)
    run = run_case(ws, case["id"], device_spec=BASE, require_asset_review=True, propose=False)
    assert run["asset_review"]["valid"] and run["verdict"]["verdict"] == "blocked"
    assert "user_data" not in run["authorized"] and all(s["outcome"] == "not_run" for s in run["steps"])
    assert run["authorization_sources"] == {"route_planning": "task_policy", "vpd_entry": "task_policy"}
    assert any(u["key"] == "authorize.user_data" for u in run["env"]["unmet"])


def test_promotion_uses_one_checked_case_and_policy_snapshot(ws, monkeypatch):
    _asset(ws)
    _approve(ws)
    original_policy, original_case = ws.policy, ws.load_case
    policy_calls, case_calls = [], []

    def policy():
        policy_calls.append(True)
        assert len(policy_calls) == 1
        return original_policy()

    def case(cid):
        case_calls.append(cid)
        assert len(case_calls) == 1
        return original_case(cid)

    monkeypatch.setattr(ws, "policy", policy)
    monkeypatch.setattr(ws, "load_case", case)
    promoted = C.promote(ws, "TC-REVIEW-1", "candidate", KnowledgeStore(ws.root))
    assert promoted["status"] == "candidate" and len(policy_calls) == len(case_calls) == 1
