"""报告只投影原步骤树，不把子步骤证据归给父流程或遗漏清理。"""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import world
from verdict_fixtures import COPY_CASE

from vpdtest.report import render_run
from vpdtest.runner import run_case
from vpdtest.util import write_json


def _monitor(obs):
    return {"samples": 1, "duration_s": 0, "stopped_by": "duration", "states": ["synthetic"], "hits": {},
            "timeline": [{"t": 0, "state": "synthetic", "new_texts": ["synthetic evidence"], "hits": [], "obs": obs}]}


def _step(sid, action="wait", outcome="passed", *, children=(), monitor=False):
    step = {"step_id": sid, "action": action, "outcome": outcome,
            "attempts": [{"status": "passed"}], "recoveries": [], "final_obs": f"obs-{sid}"}
    if children:
        step["sub_steps"] = list(children)
        step["attempts"] = [a for c in children for a in c["attempts"]]
    if monitor:
        step["monitor"] = _monitor(step["final_obs"])
    return step


def _run(steps, cleanup=()):
    return {"run_id": "synthetic-report", "case": {"id": "TC-REPORT", "hash": "synthetic"},
            "validation_level": "offline_replay", "device": {}, "device_spec": "replay:synthetic",
            "app": {"bundle": "synthetic"}, "policy": {"version": "synthetic"}, "overlay": {"version": 0},
            "actions": 0, "preflight": {"ok": True}, "env": {"ok": True},
            "verdict": {"verdict": "pending", "label": "待确认", "findings": []},
            "steps": list(steps), "cleanup": list(cleanup), "assertions": []}


def _table(report):
    return report.split("## 步骤\n", 1)[1].split("## 断言\n", 1)[0]


def _headings(report):
    return [line for line in report.splitlines() if line.startswith("## 监视时间线：")]


def test_nested_rows_preserve_execution_order_and_direct_parent():
    leaf = _step("unrelated-leaf-id")
    inner = _step("recipe", "ensure_state", children=[leaf])
    run = _run([_step("outer", "flow", children=[inner]), _step("after")])
    table = _table(render_run(run))
    expected = ["| outer | 执行 / — | flow", "| recipe | 执行 / outer | ensure_state",
                "| unrelated-leaf-id | 执行 / recipe | wait", "| after | 执行 / — | wait"]
    assert all(row in table for row in expected)
    assert [table.index(row) for row in expected] == sorted(table.index(row) for row in expected)
    assert "obs-unrelated-leaf-id" in table


def test_cleanup_rows_include_nested_steps_and_phase():
    child = _step("cleanup-child")
    table = _table(render_run(_run([], [_step("cleanup", "flow", children=[child])])))
    assert "| cleanup | 清理 / — | flow" in table
    assert "| cleanup-child | 清理 / cleanup | wait" in table


def test_monitors_belong_to_actual_owner_not_top_level_parent():
    leaf = _step("leaf", "monitor", monitor=True)
    inner = _step("inner", "flow", children=[leaf], monitor=True)
    report = render_run(_run([_step("outer", "flow", children=[inner])]))
    heads = _headings(report)
    assert len(heads) == 2
    assert heads[0].startswith("## 监视时间线：inner（执行；父步骤 `outer`；")
    assert heads[1].startswith("## 监视时间线：leaf（执行；父步骤 `inner`；")
    assert "obs-inner" in report and "obs-leaf" in report


def test_cleanup_monitor_is_rendered_once_under_its_own_step():
    leaf = _step("C-monitor", "monitor", monitor=True)
    report = render_run(_run([], [_step("cleanup", "flow", children=[leaf])]))
    heads = _headings(report)
    assert len(heads) == 1
    assert heads[0].startswith("## 监视时间线：C-monitor（清理；父步骤 `cleanup`；")
    assert "obs-C-monitor" in report


@pytest.mark.parametrize("outcome,label", [("error", "执行错误"), ("blocked", "阻塞"),
                                          ("skipped", "未执行（操作条件不满足）"), ("pending", "待确认")])
def test_nested_outcome_is_not_replaced_by_parent_pass(outcome, label):
    child = _step("child", outcome=outcome)
    run = _run([_step("parent", "flow", children=[child])])
    table = _table(render_run(run))
    assert f"| child | 执行 / parent | wait | {label} |" in table
    assert "| parent | 执行 / — | flow | 通过 |" in table


def test_parent_attempts_are_marked_as_aggregate_not_extra_actions():
    parent = _step("parent", "flow", children=[_step("one"), _step("two")])
    table = _table(render_run(_run([parent])))
    assert "| parent | 执行 / — | flow | 通过 | 汇总 2 |" in table
    assert "| one | 执行 / parent | wait | 通过 | 1 |" in table
    assert "不能与子步骤相加" in table


def test_legacy_leaf_and_top_level_monitor_still_render():
    run = _run([_step("legacy", "monitor", monitor=True)])
    report = render_run(run)
    assert "| legacy | 执行 / — | monitor | 通过 | 1 |" in _table(report)
    assert _headings(report)[0].startswith("## 监视时间线：legacy（执行；父步骤 `—`；")
    assert "离线回放" in report and "结论：待确认" in report


def test_parent_identity_is_escaped_and_not_inferred_from_id_prefix():
    run = _run([_step("parent|line\nbreak", "flow", children=[_step("leaf")])])
    table = _table(render_run(run))
    assert "| leaf | 执行 / parent\\|line break | wait |" in table


def test_render_does_not_mutate_raw_records_or_recompute_verdict():
    run = _run([_step("outer", "flow", children=[_step("child", "monitor", monitor=True)])],
               [_step("cleanup", "monitor", monitor=True)])
    original = deepcopy(run)
    first = render_run(run)
    assert run == original and render_run(run) == first
    assert "| child | 执行 / outer | monitor |" in _table(first)


def test_real_replay_runner_nested_flow_recipe_and_cleanup_evidence(verdict_ws):
    """实际 Runner/Executor 产出记录；仅使用临时合成 flow，无真实设备。"""
    write_json(verdict_ws.root / "apps" / "motor_hm" / "flows.json", {"flows": {
        "report_inner": {"steps": [{"action": "monitor", "duration_s": 0, "interval_s": 1}]},
        "report_outer": {"steps": [{"action": "flow", "flow": "report_inner"}]}}})
    case = verdict_ws.load_case(COPY_CASE)
    case.update(steps=[{"id": "S1", "action": "ensure_state", "state": "my_tab"},
                       {"id": "F", "action": "flow", "flow": "report_outer"}], assertions=[],
                cleanup=[{"id": "C", "action": "flow", "flow": "report_inner"}])
    run = run_case(verdict_ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    report = (Path(run["dir"]) / "report.md").read_text(encoding="utf-8")
    assert run["steps"][1]["sub_steps"][0]["sub_steps"][0]["action"] == "monitor"
    table = _table(report)
    assert "| F.1.1 | 执行 / F.1 | monitor |" in table
    assert "| C.1 | 清理 / C | monitor |" in table
    assert run["steps"][0]["sub_steps"], "ensure_state must exercise a real recipe"
    for child in run["steps"][0]["sub_steps"]:
        assert f"| {child['step_id']} | 执行 / S1 |" in table
    heads = _headings(report)
    assert any(h.startswith("## 监视时间线：F.1.1（执行；父步骤 `F.1`；") for h in heads)
    assert any(h.startswith("## 监视时间线：C.1（清理；父步骤 `C`；") for h in heads)
