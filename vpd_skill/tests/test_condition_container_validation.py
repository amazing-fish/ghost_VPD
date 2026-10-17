"""Malformed condition containers are asset issues, not a batch-check exception.

Only isolated synthetic assets and Replay worlds are used. No production
knowledge, case approval or historical run is changed by these checks.
"""
import json
from copy import deepcopy

import pytest
from test_condition_runner import APP, condition_rig

from vpdtest import cases, cli
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json

__all__ = ("condition_rig",)

BAD_CONTAINERS = [pytest.param([], id="list"), pytest.param(None, id="null"), pytest.param("fault", id="string")]


def _set_container(ws, case, owner, value):
    if owner == "case":
        case["conditions"] = value
    else:
        path = ws.app(APP).dir / "app.json"
        meta = read_json(path)
        meta["conditions"] = value
        write_json(path, meta)


def _issues(ws, case, app=None):
    return cases.lint(case, app or ws.app(case["app"]), KnowledgeStore(ws.root), ws.policy(), runs_dir=ws.runs_dir)


@pytest.mark.parametrize("owner", ["case", "app"])
@pytest.mark.parametrize("container", BAD_CONTAINERS)
def test_lint_returns_structured_issues_for_invalid_condition_containers(condition_rig, owner, container):
    ws, template, _ = condition_rig
    case = deepcopy(template)
    _set_container(ws, case, owner, container)
    # For an invalid app container the case still has a complete, valid local
    # provider. Local overrides must not hide a malformed registration container.
    issues = _issues(ws, case)
    assert issues
    assert any("conditions" in issue["field"] and issue["problem"] for issue in issues)


@pytest.mark.parametrize("owner", ["case", "app"])
@pytest.mark.parametrize("container", BAD_CONTAINERS)
def test_invalid_asset_does_not_abort_checking_the_next_asset(condition_rig, owner, container):
    ws, template, _ = condition_rig
    good_case, bad_case = deepcopy(template), deepcopy(template)
    good_case["id"], bad_case["id"] = "TC-SYNTHETIC-GOOD", "TC-SYNTHETIC-BAD"
    good_app = ws.app(APP)
    _set_container(ws, bad_case, owner, container)
    bad_app = ws.app(APP)
    # Exercise the same lint entry used by asset checks, with no exception
    # swallowing in the batch caller. The later valid asset must still be seen.
    checked = [{"case": case["id"], "issues": _issues(ws, case, app)}
               for case, app in ((bad_case, bad_app), (good_case, good_app))]
    assert [row["case"] for row in checked] == [bad_case["id"], good_case["id"]]
    assert checked[0]["issues"]
    assert checked[1]["issues"] == []


@pytest.mark.parametrize("owner", ["case", "app"])
@pytest.mark.parametrize("container", BAD_CONTAINERS)
def test_runner_rejects_invalid_containers_before_constructing_a_device(condition_rig, monkeypatch, owner, container):
    ws, template, world = condition_rig
    case = deepcopy(template)
    _set_container(ws, case, owner, container)
    constructed = []

    def construct(*args, **kwargs):
        constructed.append(True)
        raise AssertionError("invalid condition container reached device construction")

    monkeypatch.setattr(ws, "device", construct)
    with pytest.raises(ValueError, match="conditions|条件"):
        run_case(ws, case=case, device_spec=world(), propose=False)
    assert constructed == []
    assert not ws.runs_dir.exists()


@pytest.mark.parametrize("case_empty", [False, True], ids=["legacy-case", "empty-case"])
@pytest.mark.parametrize("app_empty", [False, True], ids=["legacy-app", "empty-app"])
def test_missing_and_explicit_empty_condition_objects_keep_unconditional_assets_compatible(
        condition_rig, case_empty, app_empty):
    ws, template, world = condition_rig
    case = deepcopy(template)
    case["assertions"] = [case["assertions"][0]]
    if case_empty:
        case["conditions"] = {}
    else:
        case.pop("conditions")
    if app_empty:
        _set_container(ws, case, "app", {})
    assert _issues(ws, case) == []
    run = run_case(ws, case=case, device_spec=world(), propose=False)
    assert run["verdict"]["verdict"] == "passed"
    assert run["verdict"]["coverage"]["verified"] == ["A-common"]


@pytest.mark.parametrize("container", BAD_CONTAINERS)
def test_case_lint_cli_reports_invalid_asset_then_can_check_a_valid_asset(condition_rig, capsys, container):
    ws, template, _ = condition_rig
    bad_case = deepcopy(template)
    bad_case["id"] = "TC-SYNTHETIC-BAD-CLI"
    bad_case["conditions"] = container
    write_json(ws.case_path(bad_case["id"]), bad_case)
    base = ["--workspace", str(ws.root), "case", "lint"]
    assert cli.main([*base, bad_case["id"]]) == 0
    bad = json.loads(capsys.readouterr().out)
    assert bad["case"] == bad_case["id"] and bad["issues"]
    assert cli.main([*base, template["id"]]) == 0
    good = json.loads(capsys.readouterr().out)
    assert good["case"] == template["id"] and good["issues"] == []
