"""Replay and human review must bind the inputs that can change their meaning."""
from copy import deepcopy

import pytest
from conftest import world
from verdict_fixtures import NAV_CASE, NAV_RULE, SETTINGS_RULE
from vpdtest import cases as C
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, write_json


def _hash(ws):
    return C.dependency_hash(ws.load_case(NAV_CASE), ws.app("motor_hm"), KnowledgeStore(ws.root))


def _peer(ws, **changes):
    peer = {**KnowledgeStore(ws.root).get(NAV_RULE), "id": "K-TEST-PEER", "expected": "opposite", **changes}
    data = read_json(ws.root / "knowledge/items.json")
    data["items"] = [item for item in data["items"] if item["id"] != peer["id"]] + [peer]
    write_json(ws.root / "knowledge/items.json", data)
    return peer


def _record_two(ws):
    kn = KnowledgeStore(ws.root)
    for kid in (NAV_RULE, SETTINGS_RULE):
        kn.confirm(kid, "pytest-synthetic", "isolated dependency contract")
    case = ws.load_case(NAV_CASE)
    case.update(status="candidate", sources=[{"type": "session", "run": "session-synthetic"}],
                knowledge_used=[{"id": None, "note": "Synthetic navigation with independent fixture rules"}])
    write_json(ws.case_path(NAV_CASE), case)
    C.approve(ws, NAV_CASE, kn, "pytest-synthetic", "isolated dependency contract")
    for _ in range(2):
        run = run_case(ws, NAV_CASE, device_spec=world("motor_hm_base.json"), propose=False)
        assert run["verdict"]["verdict"] == "passed"
        C.record_replay(ws, NAV_CASE, run)
    assert C.replay_basis(ws.load_case(NAV_CASE), _hash(ws))["ok"]
    return kn


def test_new_conflict_invalidates_review_and_actual_runner_replays(verdict_ws):
    ws = verdict_ws
    kn = _record_two(ws)
    _peer(ws)
    assert kn.oracle_status([NAV_RULE])["status"] == "conflicted"
    case = ws.load_case(NAV_CASE)
    assert C.review_status(ws, case)["state"] == "stale"
    assert not C.replay_basis(case, _hash(ws))["ok"]
    with pytest.raises(PermissionError, match="审核失效"):
        C.promote(ws, NAV_CASE, "replay_verified", kn)
    # Reviewing the changed inputs does not retroactively re-run the old evidence.
    C.approve(ws, NAV_CASE, kn, "pytest-synthetic", "review changed synthetic rule set")
    with pytest.raises(PermissionError, match="重放不足"):
        C.promote(ws, NAV_CASE, "replay_verified", kn)


@pytest.mark.parametrize("scope", [{}, {"app": "*"}, {"app_version": ["3.0.3.200", "4.0.0"]}])
def test_overlapping_peer_scope_is_bound(verdict_ws, scope):
    before = _hash(verdict_ws)
    _peer(verdict_ws, applies_to=scope)
    assert _hash(verdict_ws) != before


@pytest.mark.parametrize("change", [
    {"subject": "unrelated"}, {"subject": ""}, {"subject": None},
    {"applies_to": {"app_version": "4.0.0"}}, {"status": "deprecated"}, {"status": "invalid"},
    {"expected": "navigation"},
])
def test_unrelated_or_inactive_peer_does_not_invalidate_replays(verdict_ws, change):
    before = _hash(verdict_ws)
    _peer(verdict_ws, **change)
    assert _hash(verdict_ws) == before


def test_peer_confirmation_and_history_do_not_invalidate_but_retirement_does(verdict_ws):
    peer = _peer(verdict_ws)
    before = _hash(verdict_ws)
    _peer(verdict_ws, status="confirmed", history=[{"reason": "synthetic confirmation"}])
    assert _hash(verdict_ws) == before
    _peer(verdict_ws, status="deprecated")
    assert _hash(verdict_ws) != before
    _peer(verdict_ws, **peer)
    assert _hash(verdict_ws) == before


def test_mutually_exclusive_conditions_do_not_conflict_or_invalidate(verdict_ws):
    ws = verdict_ws
    data = read_json(ws.root / "knowledge/items.json")
    next(item for item in data["items"] if item["id"] == NAV_RULE)["condition_requirements"] = {"fault": True}
    write_json(ws.root / "knowledge/items.json", data)
    before = _hash(ws)
    _peer(ws, condition_requirements={"fault": False})
    assert _hash(ws) == before
    _peer(ws, condition_requirements={"fault": True})
    assert _hash(ws) != before


def test_peer_kind_and_bound_device_facts_do_not_hide_definition_conflicts(verdict_ws):
    ws = verdict_ws
    before = _hash(ws)
    _peer(ws, kind="observation")  # conflicts() includes active non-Oracle items too.
    changed = _hash(ws)
    assert changed != before
    kn = KnowledgeStore(ws.root).bind({"app_version": "4.0.0"})
    assert C.dependency_hash(ws.load_case(NAV_CASE), ws.app("motor_hm"), kn) == changed
    kn.resolve(kn.get(NAV_RULE)["subject"], NAV_RULE, "pytest-synthetic", "resolve isolated conflict")
    assert _hash(ws) == before


def test_conflict_dependencies_are_direct_not_transitive(verdict_ws):
    ws = verdict_ws
    _peer(ws, applies_to={})
    before = _hash(ws)
    _peer(ws, id="K-TEST-PEER-SECOND", applies_to={"app_version": "4.0.0"}, expected="third")
    assert _hash(ws) == before


@pytest.mark.parametrize("key,value", [("kind", "observation"), ("subject", "other")])
def test_explicit_oracle_semantic_identity_is_bound(verdict_ws, key, value):
    before = _hash(verdict_ws)
    data = read_json(verdict_ws.root / "knowledge/items.json")
    next(item for item in data["items"] if item["id"] == NAV_RULE)[key] = value
    write_json(verdict_ws.root / "knowledge/items.json", data)
    assert _hash(verdict_ws) != before


def _template(ws):
    path = ws.root / "apps/motor_hm/templates/dependency-fixture.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic template bytes; tree strategy wins in this replay")
    targets_path = ws.root / "apps/motor_hm/targets.json"
    targets = read_json(targets_path)
    targets["targets"]["tab_my"]["strategies"].append({"kind": "template", "file": "templates/dependency-fixture.png"})
    write_json(targets_path, targets)
    return path


def test_template_change_cannot_reuse_old_runs_after_new_review(verdict_ws):
    ws = verdict_ws
    path = _template(ws)
    kn = _record_two(ws)
    path.write_bytes(b"different template bytes")
    # Existing review already binds templates; the missing link was replay eligibility.
    assert C.review_status(ws, ws.load_case(NAV_CASE))["state"] == "stale"
    C.approve(ws, NAV_CASE, kn, "pytest-synthetic", "review changed synthetic template")
    with pytest.raises(PermissionError, match="重放不足"):
        C.promote(ws, NAV_CASE, "replay_verified", kn)


def test_only_referenced_template_bytes_affect_replay_fingerprint(verdict_ws):
    path = _template(verdict_ws)
    before = _hash(verdict_ws)
    (path.parent / "unrelated.png").write_bytes(b"unrelated")
    assert _hash(verdict_ws) == before
    path.unlink()
    missing = _hash(verdict_ws)
    assert missing != before
    path.write_bytes(b"new bytes")
    assert _hash(verdict_ws) not in (before, missing)


def test_condition_and_inline_template_targets_bind_files(verdict_ws):
    ws = verdict_ws
    path = _template(ws)
    app = ws.app("motor_hm")
    case = ws.load_case(NAV_CASE)
    # Inline diagnostic targets and condition-only targets use the same locator.
    inline = deepcopy(app.targets["tab_my"])
    for steps, conditions in [
        ([{"id": "S1", "action": "tap", "target": inline}], {}),
        ([{"id": "S1", "action": "wait", "when": {"condition": "visible", "equals": True}}],
         {"visible": {"kind": "page", "true": {"type": "target_present", "target": "tab_my"},
                      "false": {"type": "text_present", "text": "NOT VISIBLE"}}}),
    ]:
        probe = {**case, "steps": steps, "assertions": [], "conditions": conditions, "cleanup": []}
        before = C.dependency_hash(probe, app, KnowledgeStore(ws.root))
        path.write_bytes(path.read_bytes() + b"changed")
        assert C.dependency_hash(probe, app, KnowledgeStore(ws.root)) != before


def test_interrupt_dismiss_target_and_its_template_are_dependencies(verdict_ws):
    ws = verdict_ws
    path = _template(ws)
    app = ws.app("motor_hm")
    # The locator accepts paths relative to the APP directory, not only templates/.
    outside = app.dir / "dismiss.png"
    path.rename(outside)
    app.targets["tab_my"]["strategies"][-1]["file"] = "dismiss.png"
    app.states.interrupts = {"synthetic": {"class": "benign", "review": "reviewed", "dismiss_target": "tab_my"}}
    case = {"steps": [{"id": "S1", "action": "wait"}], "assertions": [], "cleanup": []}
    kn = KnowledgeStore(ws.root)
    before = C.dependency_hash(case, app, kn)
    outside.write_bytes(b"changed interrupt template")
    assert C.dependency_hash(case, app, kn) != before
    before = C.dependency_hash(case, app, kn)
    app.targets["tab_my"]["strategies"][0]["value"] = "different dismiss selector"
    assert C.dependency_hash(case, app, kn) != before


@pytest.mark.parametrize("via", ["variable", "flow"])
def test_parameterized_template_bytes_are_bound(verdict_ws, via):
    ws = verdict_ws
    path = _template(ws)
    app, case = ws.app("motor_hm"), ws.load_case(NAV_CASE)
    case["assertions"] = []
    template = str(path.relative_to(app.dir))
    case["test_data"] = {"template_file": template, "locator_kind": "template",
                         "name": {"template": "name-{run[1]}"}}
    app.targets["tab_my"]["strategies"][-1]["kind"] = "${var:locator_kind}"
    if via == "flow":
        app.targets["tab_my"]["strategies"][-1]["file"] = "${param:file}"
        app.flows["synthetic"] = {"steps": [{"action": "tap", "target": "tab_my"}]}
        case["steps"] = [{"id": "S1", "action": "flow", "flow": "synthetic", "params": {"file": "${var:template_file}"}}]
    else:
        app.targets["tab_my"]["strategies"][-1]["file"] = "${var:template_file}"
    kn = KnowledgeStore(ws.root)
    before = C.dependency_hash(case, app, kn)
    path.write_bytes(b"changed actual parameterized template")
    assert C.dependency_hash(case, app, kn) != before


@pytest.mark.parametrize("value", [{"secret": "SYNTHETIC_TEMPLATE"}, {"template": "map-{run}.png"},
                                   {"template": "constant.png"}, None])
def test_dynamic_or_unknown_template_path_rejected_before_device_creation(verdict_ws, monkeypatch, value):
    ws = verdict_ws
    _template(ws)
    target_path = ws.root / "apps/motor_hm/targets.json"
    targets = read_json(target_path)
    targets["targets"]["tab_my"]["strategies"][-1]["file"] = "${var:template_file}"
    write_json(target_path, targets)
    case = ws.load_case(NAV_CASE)
    case["test_data"] = {"template_file": value} if value is not None else {}
    write_json(ws.case_path(NAV_CASE), case)
    monkeypatch.setenv("VPD_SECRET_SYNTHETIC_TEMPLATE", "do-not-read.png")
    monkeypatch.setattr(ws, "device", lambda *a, **kw: pytest.fail("dynamic template must fail before device creation"))
    with pytest.raises(ValueError, match="模板路径无法静态绑定"):
        run_case(ws, NAV_CASE, device_spec=world("motor_hm_base.json"))


def test_each_flow_call_binds_its_own_template_argument(verdict_ws):
    ws = verdict_ws
    path = _template(ws)
    second = path.with_name("second.png")
    second.write_bytes(b"second")
    app, case = ws.app("motor_hm"), ws.load_case(NAV_CASE)
    app.targets["tab_my"]["strategies"][-1]["file"] = "${param:file}"
    app.flows["synthetic"] = {"steps": [{"action": "tap", "target": "tab_my"}]}
    case["assertions"] = []
    case["steps"] = [{"id": f"S{i}", "action": "flow", "flow": "synthetic",
                      "params": {"file": str(p.relative_to(app.dir))}} for i, p in enumerate((path, second))]
    kn = KnowledgeStore(ws.root)
    before = C.dependency_hash(case, app, kn)
    path.write_bytes(b"first changed")
    middle = C.dependency_hash(case, app, kn)
    second.write_bytes(b"second changed")
    assert len({before, middle, C.dependency_hash(case, app, kn)}) == 3


def test_unrelated_run_generated_data_is_not_evaluated_for_fingerprint(verdict_ws):
    ws = verdict_ws
    case, app = ws.load_case(NAV_CASE), ws.app("motor_hm")
    kn = KnowledgeStore(ws.root)
    before = C.dependency_hash(case, app, kn)
    case["test_data"] = {"name": {"template": "name-{run[1]}"}}
    assert C.dependency_hash(case, app, kn) == before
