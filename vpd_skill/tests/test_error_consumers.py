"""Issue #56: execution errors remain visible and cannot improve or mature assets.

These are offline consumer contract examples. They do not confirm product knowledge
or approve any repository asset.
"""
from argparse import Namespace
from copy import deepcopy

import pytest
from vpdtest import cases, cli, commands
from vpdtest.experience import ExperienceStore
from vpdtest.util import read_jsonl


def _verdict(result="error", *, execution_error=True, product="pending"):
    finding = {"kind": "cleanup", "step": "C1", "result": "error", "attribution": "tool",
               "reason": "executor failed", "stage": "cleanup", "error_class": "executor_error",
               "error": "synthetic raw error", "evidence": {"obs": "obs-0002", "attempt": 1}}
    return {"verdict": result, "label": result, "attribution": "tool", "recovered": False,
            "adapted": False, "first_anomaly": None, "findings": [finding] if execution_error else [],
            "product_failed": product == "failed", "product_verdict": product,
            "failure_signature": ["assertion:A1:text_present=False"] if product == "failed" else [],
            "execution": {"status": "error" if execution_error else "completed",
                          "errors": [finding] if execution_error else []}}


def _run(ws, case_id="TC-MOTOR-HM-001"):
    case = ws.load_case(case_id)
    return {"run_id": "run-consumer-contract", "case": {"id": case_id, "status": case["status"],
            "hash": cases.content_hash(case), "deps": "fixture-deps"}, "verdict": _verdict(),
            "experience_candidates": [], "validation_level": "offline_replay", "device_spec": "replay:fixture",
            "dir": str(ws.runs_dir / "run-consumer-contract"), "facts": {"app": "motor_hm"}}


@pytest.mark.parametrize("command", ["run", "suite"])
def test_fail_on_error_accepts_new_result_and_returns_one(ws, capsys, monkeypatch, command):
    result = {"verdict": "error"} if command == "run" else {"results": [{"verdict": "error"}]}
    monkeypatch.setitem(cli.HANDLERS, command, lambda ws, args: result)
    assert cli.main(["--workspace", str(ws.root), command, "fixture", "--fail-on", "error"]) == 1
    assert '"error"' in capsys.readouterr().out


def test_error_handoff_keeps_exit_two_priority(ws, capsys, monkeypatch):
    monkeypatch.setitem(cli.HANDLERS, "run", lambda ws, args: {"verdict": "error", "action_required": "inspect"})
    assert cli.main(["--workspace", str(ws.root), "run", "fixture", "--fail-on", "error"]) == 2
    capsys.readouterr()


def test_run_and_suite_briefs_preserve_error_evidence(ws, monkeypatch):
    run = _run(ws)
    monkeypatch.setattr(commands, "run_case", lambda *args, **kwargs: deepcopy(run))
    monkeypatch.setattr(cases, "preflight_batch", lambda *args: [])
    monkeypatch.setattr(ws, "load_suite", lambda sid: {"cases": [run["case"]["id"]]})
    out = commands.cmd_suite(ws, Namespace(suite="fixture", device=["replay:fixture"], authorize=[], supervisor=None))
    row = out["results"][0]
    assert row["execution"] == run["verdict"]["execution"]
    assert row["product_verdict"] == "pending"
    for key in ("stage", "error_class", "error", "evidence"):
        assert row["findings"][0][key] == run["verdict"]["findings"][0][key]
    events = read_jsonl(ws.runs_dir / out["suite_run"] / "events.jsonl")
    assert events[-1]["verdicts"] == ["error"] and out["completed"] is True


def _experience(tmp_path):
    xs = ExperienceStore(tmp_path, "synthetic")
    xs.save_item({"id": "XP-error", "kind": "locator_preference",
                  "change": {"target": "target", "prefer_strategy_index": 1}, "status": "candidate",
                  "requires_review": False, "origin_case": "origin", "evidence": [], "validations": [], "history": []})
    return xs


def _validate(xs, baseline, trial, *, mixed=False, healthy_independent=False):
    def run_case(cid, dev, overlay):
        result = trial if overlay.trial else baseline
        if healthy_independent and cid == "other":
            result = "passed"
        v = _verdict(result, execution_error=result == "error" or mixed,
                     product="failed" if result == "failed" or mixed else ("passed" if result == "passed" else "pending"))
        return {"run_id": f"{cid}-{bool(overlay.trial)}", "verdict": v,
                "steps": [{"attempts": [{"locate": {"target": "target", "adapted": not overlay.trial}}]}]}
    return xs.validate("XP-error", ["origin", "other"], ["fixture"], run_case)


@pytest.mark.parametrize(("baseline", "trial", "status"), [
    ("error", "error", "candidate"), ("error", "pending", "candidate"),
    ("pending", "error", "rejected"), ("passed", "error", "rejected"), ("failed", "error", "rejected"),
    ("error", "passed", "validated"),
])
def test_experience_error_transitions_do_not_crash_or_fake_improvement(tmp_path, baseline, trial, status):
    assert _validate(_experience(tmp_path), baseline, trial)["status"] == status


def test_unresolved_error_pair_cannot_be_hidden_by_independent_improvement(tmp_path):
    out = _validate(_experience(tmp_path), "error", "pending", healthy_independent=True)
    assert out["status"] == "candidate"


def test_mixed_failure_and_error_cannot_validate_by_fewer_adaptations(tmp_path):
    out = _validate(_experience(tmp_path), "failed", "failed", mixed=True)
    assert out["status"] == "candidate"


def test_mixed_error_summaries_keep_retained_product_failure(tmp_path):
    out = _validate(_experience(tmp_path), "error", "error", mixed=True)
    assert out["status"] == "candidate"
    assert not any(row.get("regression") for row in out["rows"])


def test_error_summary_cannot_hide_previous_product_failure(tmp_path):
    xs = _experience(tmp_path)

    def run_case(cid, dev, overlay):
        return {"run_id": f"{cid}-{bool(overlay.trial)}",
                "verdict": _verdict("error", product="pending" if overlay.trial else "failed"), "steps": []}

    assert xs.validate("XP-error", ["origin", "other"], ["fixture"], run_case)["status"] == "rejected"


def test_record_replay_retains_execution_and_product_projection(ws):
    run = _run(ws)
    run["verdict"] = _verdict("failed", product="failed")  # compatible historical/mixed input
    saved = cases.record_replay(ws, run["case"]["id"], run)["replays"][-1]
    assert saved["execution"] == run["verdict"]["execution"]
    assert saved["product_verdict"] == "failed" and saved["product_failed"] is True


def _replays(*rows):
    case = {"app": "synthetic", "steps": [], "assertions": []}
    case["replays"] = [{"run_id": f"r{i}", "hash": cases.content_hash(case),
                        "failure_signature": ["assertion:A1:text_present=False"], **row}
                       for i, row in enumerate(rows)]
    return case


def test_mixed_failed_execution_error_is_not_decisive_for_promotion():
    mixed = {"verdict": "failed", "execution": {"status": "error", "errors": [{"stage": "cleanup"}]}}
    assert cases.replay_basis(_replays(mixed, mixed))["ok"] is False
    assert cases.replay_basis(_replays({"verdict": "failed"}, {"verdict": "failed"}, mixed))["ok"] is False


def test_error_reset_requires_two_fresh_decisive_replays_without_rewriting_history():
    error = {"verdict": "error", "execution": {"status": "error", "errors": [{"stage": "step"}]}}
    case = _replays({"verdict": "passed"}, {"verdict": "passed"}, error)
    original = deepcopy(case)
    assert cases.replay_basis(case)["ok"] is False and case == original
    case = _replays(error, {"verdict": "passed"}, {"verdict": "passed"})
    assert cases.replay_basis(case)["ok"] is True
