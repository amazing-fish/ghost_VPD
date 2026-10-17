"""#56 第二切片的只读消费者：同一份选支与覆盖证据不可在输出时丢失。"""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import confirm, world
from verdict_fixtures import COPY_CASE, COPY_RULE
from vpdtest import cli as cli_mod
from vpdtest.cli import build_parser
from vpdtest.commands import _run_brief
from vpdtest.console import progress
from vpdtest.report import render_run
from vpdtest.runner import run_case
from vpdtest.util import append_jsonl, write_json


@pytest.fixture
def consumer_run(verdict_ws):
    """真实 Runner 生成完整记录，再注入待展示契约；不确认任何生产知识。"""
    confirm(verdict_ws, COPY_RULE)
    return run_case(verdict_ws, COPY_CASE, device_spec=world("motor_hm_base.json"),
                    env={"account_state": "logged_out"}, propose=False)


def _guard(status="false"):
    return {"status": status, "condition": "synthetic_fault", "equals": True,
            "source": "observation", "obs": "obs-guard-before-action", "ts": "2026-10-17T10:00:00Z",
            "scope": {"app": "motor_hm", "build": "3.0.3.200", "vehicle": "synthetic-vehicle"},
            "action_epoch": 2, "reason": "当前独立故障观测明确不满足",
            "checks": {"true": {"holds": False, "evidence": {"id": "fault-check"}},
                       "false": {"holds": True, "evidence": {"obs": "obs-guard-before-action"}}}}


def _set_contract(run, *, all_na=False):
    run = deepcopy(run)
    assertion = run["assertions"][0]
    assertion.update(result="not_applicable", guard=_guard(), verified=False)
    run["assertions"] = [assertion]
    coverage_row = {k: assertion.get(k) for k in ("id", "result", "verified", "guard")}
    coverage_row["applicable"] = False
    coverage = {"assertions": [coverage_row], "verified": [], "not_applicable": [assertion["id"]],
                "unverified": [assertion["id"]]}
    run["verdict"].update(coverage=coverage, findings=[], not_verified=[])
    if all_na:
        run["verdict"].update(verdict="not_applicable", label="不涉及", product_verdict="not_applicable")
    return run


@pytest.mark.parametrize("command", ["run", "suite"])
def test_cli_accepts_not_applicable_fail_on(command):
    parsed = build_parser().parse_args([command, "synthetic", "--fail-on", "not_applicable"])
    assert parsed.fail_on == ["not_applicable"]


@pytest.mark.parametrize("command", ["run", "suite"])
def test_cli_not_applicable_exit_one_when_requested(command, verdict_ws, monkeypatch, capsys):
    output = {"verdict": "not_applicable"} if command == "run" else {"results": [{"verdict": "not_applicable"}]}
    monkeypatch.setitem(cli_mod.HANDLERS, command, lambda ws, args: output)
    code = cli_mod.main(["--workspace", str(verdict_ws.root), command, "synthetic", "--fail-on", "not_applicable"])
    assert code == 1
    assert "not_applicable" in capsys.readouterr().out


def test_run_brief_keeps_assertion_guard_and_coverage(consumer_run):
    run = _set_contract(consumer_run, all_na=True)
    brief = _run_brief(run)
    assert brief["coverage"] == run["verdict"]["coverage"]
    assertion = brief["assertions"][0]
    assert assertion["guard"] == _guard()
    assert assertion["result"] == "not_applicable" and assertion["verified"] is False


def test_run_brief_keeps_nested_operation_selection_evidence(consumer_run):
    consumer_run["steps"][0]["sub_steps"] = [{"step_id": "S1.branch", "outcome": "skipped", "condition": _guard()}]
    brief = _run_brief(consumer_run)
    assert brief["operation_conditions"] == [
        {"step": "S1.branch", "phase": "steps", "outcome": "skipped", "condition": _guard()}]


def test_all_not_applicable_report_never_invents_pass(consumer_run):
    report = render_run(_set_contract(consumer_run, all_na=True))
    assert "非建议类断言均在已确认判据下通过" not in report
    assert "[通过]" not in report
    assert "不涉及" in report and "未验证覆盖" in report


def test_report_preserves_branch_evidence_and_distinguishes_operation_conditions(consumer_run):
    run = _set_contract(consumer_run)
    run["steps"][0].update(condition=_guard("true"))
    report = render_run(run)
    assert "操作条件" in report and "断言前置" in report
    assert "未验证覆盖" in report
    for value in ("observation", "obs-guard-before-action", "2026-10-17T10:00:00Z", "synthetic-vehicle", "fault-check"):
        assert value in report


def test_report_keeps_each_aggregate_guard_record(consumer_run):
    run = _set_contract(consumer_run)
    second = {**_guard("true"), "obs": "obs-second-guard", "condition": "synthetic_other"}
    run["assertions"][0]["guard"] = {"status": "false", "records": [_guard(), second]}
    report = render_run(run)
    assert "obs-guard-before-action" in report and "obs-second-guard" in report
    assert "synthetic_fault" in report and "synthetic_other" in report


def test_skipped_cleanup_is_never_reported_as_completed(consumer_run):
    run = _set_contract(consumer_run, all_na=True)
    run["cleanup"][0].update(outcome="skipped", condition=_guard(), reason="操作条件不满足")
    report = render_run(run)
    cleanup = report.split("## 清理", 1)[1].split("## ", 1)[0]
    assert "全部完成" not in cleanup and "未执行" in cleanup


def test_nested_skipped_cleanup_is_never_reported_as_completed(consumer_run):
    run = _set_contract(consumer_run, all_na=True)
    run["cleanup"][0]["sub_steps"] = [{"step_id": "C1.branch", "action": "wait", "outcome": "skipped",
                                      "condition": _guard(), "reason": "操作条件不满足"}]
    report = render_run(run)
    cleanup = report.split("## 清理", 1)[1].split("## ", 1)[0]
    assert "全部完成" not in cleanup and "C1.branch" in cleanup and "未执行" in cleanup


def test_console_keeps_guard_coverage_and_nested_operation_conditions(consumer_run, tmp_path):
    run = _set_contract(consumer_run, all_na=True)
    run["steps"][0].update(condition=_guard("true"))
    child = {"step_id": "S1.branch", "action": "wait", "outcome": "skipped", "attempts": [],
             "condition": _guard(), "reason": "操作条件不满足"}
    run["steps"][0]["sub_steps"] = [child]
    path = tmp_path / "run-consumer-contract"
    path.mkdir()
    write_json(path / "run.json", run)
    append_jsonl(path / "events.jsonl", {"event": "run_start", "steps": ["S1"]})
    append_jsonl(path / "events.jsonl", {"event": "run_end", "verdict": "not_applicable"})
    detail = progress.detail(tmp_path, path.name)
    assert detail["coverage"] == run["verdict"]["coverage"]
    assert detail["assertions"][0]["guard"] == _guard()
    assert detail["assertions"][0]["verified"] is False
    assert detail["steps"][0]["condition"] == _guard("true")
    assert detail["steps"][0]["sub_steps"][0]["condition"] == _guard()


def test_console_page_displays_operation_and_assertion_evidence_separately():
    page = (Path(progress.__file__).parent / "page.html").read_text(encoding="utf-8")
    assert "操作条件" in page and "断言前置" in page and "未验证覆盖" in page
    assert "d.coverage" in page
