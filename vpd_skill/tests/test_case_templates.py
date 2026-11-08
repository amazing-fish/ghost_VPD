"""Literal display-template contracts through real Executor/Verdict, synthetic only.

No production cases, knowledge confirmations, targets or devices are modified.
The Replay device is placed directly in an already-prepared scene by the fixture;
launching or navigating is deliberately not part of the generated case.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from vpdtest import cases
from vpdtest.case_templates import TEMPLATES, build_case, validate_case_id
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_jsonl, write_json
from vpdtest.workspace import Workspace

APP = "qiankun_hm"
BUNDLE = "pytest.synthetic.display.templates"


def _scope(role):
    return {"status": "synthetic", "side_effect": "read_only", "forbid_tap": True,
            "strategies": [{"kind": "id", "value": role}],
            "template_scope": {"role": role, "shape": "text_container", "calibration": {
                "status": "verified", "source": "synthetic_fixture", "ref": "tests/independent-scope.json#" + role,
                "validation_level": "synthetic"}}}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "data"
    directory = root / "apps" / APP
    write_json(root / "policy.json", {"version": "synthetic-only", "allowed_packages": [BUNDLE],
                                     "max_actions_per_run": 5, "max_wait_s": 0})
    write_json(directory / "app.json", {"id": APP, "name": "合成显示测试", "platform": "harmony",
                                         "bundle": BUNDLE, "ability": "EntryAbility"})
    write_json(directory / "states.json", {"states": {"qk_vpd_home": {"require_text": ["独立页面锚"]}},
                                            "interrupts": {}, "recipes": {}})
    write_json(directory / "targets.json", {"targets": {entry["role"]: _scope(entry["role"])
                                                        for entry in TEMPLATES.values()}})
    ws = Workspace(str(root))
    ws.config = {}
    knowledge = KnowledgeStore(root)
    for template in TEMPLATES.values():
        knowledge.add({"id": template["oracle"], "kind": "product_rule", "subject": template["role"],
                       "statement": "仅合成测试的独立显示要求", "expected": "synthetic-only display contract",
                       "applies_to": {"app": APP},
                       "source": {"type": "reviewer", "ref": "tests/test_case_templates.py synthetic only"}})
    # This fixture simulates a page prepared BEFORE the case. It does not call
    # launch() and cannot provide evidence of real-world setup or product coverage.
    def device(spec, state_path=None):
        dev = ReplayDevice(spec.split(":", 1)[1], state_path=state_path)
        dev.state.update(alive=True, foreground=BUNDLE, stack=["display"])
        return dev
    monkeypatch.setattr(ws, "device", device)
    return ws, knowledge, tmp_path


def _spec(template="TC-QK-240", **updates):
    role = TEMPLATES[template]["role"]
    value = {"expected_name": "独立园区A"} if template.endswith("240") else (
        {"expected_floors": ["L1", "B1"]} if template.endswith("243") else {})
    return {"template_id": template, "new_case_id": template + "-SYNTHETIC", "scope_target": role,
            "scope_source": {"source": "synthetic_fixture", "ref": "tests/independent-scope.json#" + role},
            "expected_source": {"source": "test_data", "ref": "tests/independent-expectations.json#" + role},
            **value, **updates}


def _container(identity, texts, y=60):
    return {"t": "Column", "id": identity, "b": [10, y, 390, y + 140], "children": [
        {"t": "Text", "text": text, "b": [20, y + 5 + index * 30, 380, y + 30 + index * 30]}
        for index, text in enumerate(texts)]}


def _run(rig, template, texts, *, expected=None, confirmed=True, others=(), version="9.8.7", scope_id=None, page="独立页面锚"):
    ws, knowledge, tmp_path = rig
    spec = _spec(template, **(expected or {}))
    case = build_case(ws.app(APP), spec)
    if confirmed:
        # Confirmation is strictly local to this independent pytest workspace.
        knowledge.confirm(TEMPLATES[template]["oracle"], reviewer="pytest-synthetic", basis="Synthetic engine contract only")
    role = TEMPLATES[template]["role"]
    world = {"bundle": BUNDLE, "app_version": version, "screen": [400, 800],
             "display": {"density": 1, "device_form": "foldable"}, "initial": {"installed": True},
             "device": {"model": "SYNTHETIC", "os": "Replay"}, "launch_screen": "display",
             "screens": {"display": {"root": {"t": "Root", "b": [0, 0, 400, 800], "children": [
                 {"t": "Text", "text": page, "b": [10, 10, 390, 40]},
                 _container(role if scope_id is None else scope_id, texts), *others]}}}}
    path = tmp_path / "world.json"
    write_json(path, world)
    run = run_case(ws, case=case, device_spec="replay:" + str(path), propose=False)
    events = read_jsonl(Path(run["dir"]) / "events.jsonl")
    assert not [event for event in events if event["event"] == "dispatch"], events
    assert all(not step.get("recoveries") for step in run["steps"] + run["cleanup"])
    return case, run


@pytest.mark.parametrize("template,texts", [("TC-QK-240", ["独立园区A"]), ("TC-QK-241", ["共 12 个"]),
                                            ("TC-QK-243", ["B1", "L1"])])
def test_correct_instances_use_real_runner_without_business_dispatch(rig, template, texts):
    case, run = _run(rig, template, texts)
    assert run["verdict"]["verdict"] == "passed", run["verdict"]
    assert run["steps"][0]["measurement"]["raw"] == " ".join(texts)
    assert all(assertion["result"] == "pass" for assertion in run["assertions"])
    assert case["env_requirements"] == {} and case["status"] == "draft"
    assert {step["action"] for step in case["steps"] + case["cleanup"]} <= {"read", "wait"}
    assert all(step["retry"] == 0 for step in case["steps"] + case["cleanup"])


@pytest.mark.parametrize("template,texts", [("TC-QK-240", ["错误园区"]), ("TC-QK-241", ["共 0 个"]),
                                            ("TC-QK-243", ["B9", "L1"])])
def test_unconfirmed_generic_rules_stay_pending_on_mismatch(rig, template, texts):
    _, run = _run(rig, template, texts, confirmed=False)
    assert run["verdict"]["verdict"] == "pending", run["verdict"]
    assert any(assertion.get("observed_holds") is False for assertion in run["assertions"])
    assert all(assertion["result"] == "pending" for assertion in run["assertions"])


def test_wrong_name_is_observed_mismatch_not_unresolved_locator(rig):
    _, run = _run(rig, "TC-QK-240", ["错误园区"], others=[_container("other", ["独立园区A"], 240)])
    assert run["steps"][0]["outcome"] == "passed"
    assert run["steps"][0]["measurement"]["value"] == "错误园区"
    assert run["assertions"][0]["result"] == "fail"
    assert run["verdict"]["verdict"] == "failed"


@pytest.mark.parametrize("text,valid", [("共 1 个", True), ("共 100 个", True), ("共 0 个", False),
    ("共 -1 个", False), ("共 01 个", False), ("共 +1 个", False), ("共 1.5 个", False),
    ("共 １ 个", False), ("1", False), ("共 1个", False), ("共  1 个", False),
    ("共 1 个extra", False), ("prefix共 1 个", False), ("共  个", False)])
def test_count_is_a_literal_positive_integer_display_rule(rig, text, valid):
    _, run = _run(rig, "TC-QK-241", [text])
    assert run["steps"][0]["outcome"] == "passed"
    assert run["verdict"]["verdict"] == ("passed" if valid else "failed"), run["assertions"]


@pytest.mark.parametrize("texts", [["共 7 个", "共 7 个"], ["共 7 个", "共 0 个"]])
def test_count_does_not_accept_duplicate_or_mixed_values(rig, texts):
    _, run = _run(rig, "TC-QK-241", texts)
    assert run["verdict"]["verdict"] == "failed"


@pytest.mark.parametrize("texts,valid", [(["L1", "B1"], True), (["B1", "L1"], True),
    (["L1"], False), (["L1", "B2"], False), (["L1", "B1", "B1"], False),
    (["L1", "L1"], False), (["L1", "B1", "B2"], False), (["L1 B1"], False)])
def test_floor_set_preserves_exact_labels_and_detects_duplicates(rig, texts, valid):
    _, run = _run(rig, "TC-QK-243", texts)
    assert run["verdict"]["verdict"] == ("passed" if valid else "failed"), run["assertions"]


def test_floor_labels_cannot_be_borrowed_from_another_container(rig):
    _, run = _run(rig, "TC-QK-243", ["L1"], others=[_container("other", ["B1"], 240)])
    assert run["verdict"]["verdict"] == "failed"
    assert any(assertion.get("observed_holds") is False for assertion in run["assertions"])


@pytest.mark.parametrize("duplicate", [False, True])
def test_missing_or_ambiguous_scope_blocks_without_recovery_or_dispatch(rig, duplicate):
    others = [_container("floor_labels", ["L1", "B1"], 240)] if duplicate else []
    _, run = _run(rig, "TC-QK-243", ["L1", "B1"], others=others,
                  scope_id="floor_labels" if duplicate else "other")
    assert run["verdict"]["verdict"] == "blocked", run["verdict"]
    assert run["steps"][0]["final_status"] == ("target_ambiguous" if duplicate else "target_unresolved")


@pytest.mark.parametrize("template,expected,texts", [
    ("TC-QK-240", {"expected_name": "A'\"园区 or //*"}, ["A'\"园区 or //*"]),
    ("TC-QK-243", {"expected_floors": ["L'\"1", "B] | //*"]}, ["B] | //*", "L'\"1"]),
])
def test_xpath_looking_expected_values_remain_literal_data(rig, template, expected, texts):
    case, run = _run(rig, template, texts, expected=expected)
    assert run["verdict"]["verdict"] == "passed", run["assertions"]
    assert case["steps"][0]["target"] == TEMPLATES[template]["role"]


@pytest.mark.parametrize("updates", [
    {"expected_name": "${read:S_ACTUAL}"}, {"expected_name": {"secret": "NAME"}},
    {"expected_name": {"template": "name-{run}"}}, {"scope_target": "${read:S_ACTUAL}"},
    {"expected_source": None}, {"expected_source": {"source": "observation", "ref": "tree"}},
    {"expected_source": {"source": "test_data", "ref": "runs/run-20261108-010101-abcd/obs/obs-001"}},
    {"expected_source": {"source": "test_data", "ref": "tests/independent-scope.json#parking_name"}},
    {"scope_source": {"source": "synthetic_fixture", "ref": "mismatched"}},
    {"steps": [{"action": "tap"}]}, {"pattern": ".*"}, {"xpath": "//*"}, {"run": "random"},
    {"scope_target": "unregistered"}, {"expected_name": ""},
])
def test_missing_independent_sources_and_injected_structure_are_rejected(rig, updates):
    with pytest.raises(ValueError):
        build_case(rig[0].app(APP), _spec(**updates))


@pytest.mark.parametrize("labels", [[], ["L1", "L1"], ["L1", "${read:R1}"], [None], "L1 B1"])
def test_floor_inputs_are_nonempty_independent_unique_literal_set(rig, labels):
    with pytest.raises(ValueError):
        build_case(rig[0].app(APP), _spec("TC-QK-243", expected_floors=labels))


@pytest.mark.parametrize("case_id", [None, 4, "", "TC-QK-240", "../TC-QK-999", "TC-X/../../a", "CON", "LPT1",
                                     "tc-lower", "TC-X.", "TC-X ", "TC-A\\B", "TC-X-", "TC-" + "A" * 126])
def test_only_portable_new_case_ids_are_accepted(case_id):
    with pytest.raises(ValueError):
        validate_case_id(case_id)


@pytest.mark.parametrize("change", ["unregistered", "wrong-role", "wrong-shape", "unverified", "real-claim",
                                    "tap", "scroll", "secret", "text-anchor", "xpath-anchor", "xpath-injection",
                                    "dynamic", "fallback", "region"])
def test_scope_cannot_self_declare_calibration_or_anchor_on_tested_text(rig, change):
    app = rig[0].app(APP)
    target = app.targets["parking_name"]
    if change == "unregistered":
        target.pop("template_scope")
    elif change == "wrong-role":
        target["template_scope"]["role"] = "floor_labels"
    elif change == "wrong-shape":
        target["template_scope"]["shape"] = "entire_page"
    elif change == "unverified":
        target["template_scope"]["calibration"]["status"] = "pending"
    elif change == "real-claim":
        target["template_scope"]["calibration"]["validation_level"] = "real_device"
    elif change == "tap":
        target["forbid_tap"] = False
    elif change == "scroll":
        target["scroll"] = {"direction": "up", "max": 1}
    elif change == "secret":
        target["secret"] = True
    elif change == "text-anchor":
        target["strategies"] = [{"kind": "text", "value": "独立园区A"}]
    elif change == "xpath-anchor":
        target["strategies"] = [{"kind": "xpath", "value": "//Column[Text[@text='独立园区A']]"}]
    elif change == "xpath-injection":
        target["strategies"] = [{"kind": "xpath", "value": "//Column | //*"}]
    elif change == "dynamic":
        target["strategies"][0]["value"] = "${read:S1}"
    elif change == "fallback":
        target["strategies"].append({"kind": "text", "value": "独立园区A"})
    else:
        target["strategies"][0]["region"] = [0, 0, 1, 1]
    with pytest.raises(ValueError):
        build_case(app, _spec())


def test_registered_pure_structural_xpath_is_supported_without_expected_anchor(rig, monkeypatch):
    ws = rig[0]
    app = ws.app(APP)
    app.targets["parking_name"]["strategies"] = [{"kind": "xpath", "value": "//Root/Column[1]"}]
    monkeypatch.setattr(ws, "app", lambda _: app)
    _, run = _run(rig, "TC-QK-240", ["错误园区"])
    assert run["steps"][0]["outcome"] == "passed"
    assert run["verdict"]["verdict"] == "failed"


def test_preview_is_pure_detached_and_creates_no_files_or_locks(rig, monkeypatch):
    ws, _, root = rig
    app = ws.app(APP)
    spec = _spec()
    app_before, spec_before = deepcopy(app.targets), deepcopy(spec)
    files = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail("pure build_case must not perform filesystem I/O")
    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        case = build_case(app, spec)
    assert files == {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert app.targets == app_before and spec == spec_before
    case["origin"]["calibration"]["ref"] = "modified-result"
    assert app.targets == app_before
    assert not ws.runs_dir.exists()
    assert not (ws.root / "cases").exists()


def test_new_instances_have_fresh_provenance_and_bind_independent_inputs(rig):
    ws, knowledge, _ = rig
    app = ws.app(APP)
    first = build_case(app, _spec())
    second = build_case(app, _spec(expected_name="独立园区B", new_case_id="TC-QK-240-SITE-B"))
    for case in (first, second):
        assert case["origin"]["type"] == "template"
        assert case["origin"]["calibration"]["validation_level"] == "synthetic"
        assert not {"asset_review", "replays", "history", "product_status"} & case.keys()
        assert {ref for a in case["assertions"] for ref in a["oracle_refs"]} == {"K-QK-TPL-240"}
        assert not cases.lint(case, app, knowledge, ws.policy()), cases.lint(case, app, knowledge, ws.policy())
    assert cases.content_hash(first) != cases.content_hash(second)
    assert first["origin"]["input_hash"] != second["origin"]["input_hash"]
    before = cases.dependency_hash(first, app, knowledge)
    app.targets["parking_name"]["strategies"][0]["value"] = "changed-independent-id"
    assert cases.dependency_hash(first, app, knowledge) != before


def test_new_template_is_pending_human_review_and_cannot_be_promoted(rig):
    ws, knowledge, _ = rig
    case = build_case(ws.app(APP), _spec())
    write_json(ws.case_path(case["id"]), case)
    review = cases.review_status(ws, case, knowledge)
    assert review["required"] is True and review["state"] == "pending"
    original = ws.case_path(case["id"]).read_bytes()
    with pytest.raises(PermissionError, match="审核"):
        cases.promote(ws, case["id"], "candidate", knowledge, dry_run=True)
    assert ws.case_path(case["id"]).read_bytes() == original


@pytest.mark.parametrize("template,texts", [("TC-QK-240", ["独立园区A"]), ("TC-QK-241", ["共 1 个"]),
                                            ("TC-QK-243", ["L1", "B1"])])
def test_correct_fields_on_wrong_page_cannot_pass_home_display_case(rig, template, texts):
    _, run = _run(rig, template, texts, page="错误页面")
    assert run["steps"][0]["outcome"] == "passed"
    page = next(a for a in run["assertions"] if a["id"] == "A_PAGE")
    assert page["result"] == "fail"
    assert run["verdict"]["verdict"] == "failed"


@pytest.mark.parametrize("hidden", ["ancestor", "floor"])
def test_hidden_text_cannot_fill_the_expected_floor_set(rig, monkeypatch, hidden):
    ws = rig[0]
    original_device = ws.device
    def device(spec, state_path=None):
        dev = original_device(spec, state_path=state_path)
        scope = dev.world["screens"]["display"]["root"]["children"][1]
        target = scope if hidden == "ancestor" else scope["children"][1]
        target["attrs"] = {"visible": "false"}
        return dev
    monkeypatch.setattr(ws, "device", device)
    _, run = _run(rig, "TC-QK-243", ["L1", "B1"])
    assert run["verdict"]["verdict"] != "passed"
    assert not all(a.get("observed_holds") for a in run["assertions"])


def test_other_vehicle_area_and_build_are_not_fixed_business_requirements(rig):
    expected = {"expected_name": "另一个独立园区"}
    case, run = _run(rig, "TC-QK-240", [expected["expected_name"]], expected=expected, version="42.0.0")
    assert run["verdict"]["verdict"] == "passed"
    assert case["env_requirements"] == {}
    assert "vin_tail" not in case["test_data"]


def test_real_scope_evidence_is_preserved_as_a_declaration_not_tested_fact(rig):
    app = rig[0].app(APP)
    target = app.targets["parking_name"]
    target["status"] = "calibrated_on_device"
    target["template_scope"]["calibration"].update(source="device_observation", validation_level="real_device",
                                                  ref="evidence/device-calibration.json")
    spec = _spec(scope_source={"source": "device_observation", "ref": "evidence/device-calibration.json"})
    case = build_case(app, spec)
    assert case["origin"]["calibration"]["validation_level"] == "real_device"
    assert case["origin"]["source_verification"] == "declared_registration_only_requires_human_review"
    assert case["env_requirements"] == {} and "asset_review" not in case


def test_accessibility_description_does_not_excuse_wrong_visible_name(rig, monkeypatch):
    ws = rig[0]
    original_device = ws.device
    def device(spec, state_path=None):
        dev = original_device(spec, state_path=state_path)
        scope = dev.world["screens"]["display"]["root"]["children"][1]
        scope["children"][0]["desc"] = "独立园区A"
        return dev
    monkeypatch.setattr(ws, "device", device)
    _, run = _run(rig, "TC-QK-240", ["错误园区"])
    assert run["verdict"]["verdict"] == "failed"


@pytest.mark.parametrize("template", ["TC-QK-240", "TC-QK-241", "TC-QK-243"])
def test_empty_actual_value_does_not_become_not_applicable(rig, template):
    _, run = _run(rig, template, [], confirmed=False)
    assert run["verdict"]["verdict"] == "pending"
    assert run["verdict"]["product_verdict"] != "not_applicable"


@pytest.mark.parametrize("template,texts", [("TC-QK-240", ["独立园区A"]), ("TC-QK-241", ["共 12 个"]),
                                            ("TC-QK-243", ["L1", "B1"])])
@pytest.mark.parametrize("geometry", ["partial-offscreen", "outside-scope", "nested-clip", "rounding", "valid-nested"])
def test_strict_templates_bind_real_text_to_exact_visible_scope_geometry(rig, monkeypatch, template, texts, geometry):
    ws = rig[0]
    original_device = ws.device
    def device(spec, state_path=None):
        dev = original_device(spec, state_path=state_path)
        root = dev.world["screens"]["display"]["root"]
        scope = root["children"][1]
        if geometry == "partial-offscreen":
            scope["children"][0]["b"] = [20, 790, 380, 830]
        elif geometry == "outside-scope":
            scope["children"][0]["b"] = [20, 250, 380, 280]
        elif geometry == "nested-clip":
            scope["children"] = [{"t": "Scroll", "b": [10, 60, 390, 80], "children": scope["children"]}]
        elif geometry == "rounding":
            dev.world["screen"] = [400, 6000]
            root["b"] = [0, 0, 400, 6000]
            scope["b"] = [10, 60, 390, 5000]
            scope["children"][0]["b"] = [20, 70, 380, 4070]
            scope["children"] = [{"t": "Scroll", "b": [10, 60, 390, 4069], "children": scope["children"]}]
        else:
            scope["children"] = [{"t": "Row", "b": scope["b"], "children": scope["children"]}]
        return dev
    monkeypatch.setattr(ws, "device", device)
    case, run = _run(rig, template, texts)
    positive = [a for a in case["assertions"] if a["type"] in ("text_present", "text_present_any")]
    assert positive and all(a["strict_visible_text"] is True for a in positive)
    assert run["verdict"]["verdict"] == ("passed" if geometry == "valid-nested" else "pending"), run["assertions"]
    if geometry != "valid-nested":
        results = [a for a in run["assertions"] if a["type"] in ("text_present", "text_present_any")]
        assert all(a["result"] == "undeterminable" and not a["verified"] for a in results)
        assert all(a["evidence"]["strict_visible_text"]["invalid"] for a in results)


@pytest.mark.parametrize("template,texts", [("TC-QK-240", ["独立园区A"]), ("TC-QK-241", ["共 12 个"]),
                                            ("TC-QK-243", ["L1", "B1"])])
@pytest.mark.parametrize("hidden", [False, "False", " FALSE ", "0"])
def test_hidden_real_text_cannot_borrow_visible_descriptions(rig, monkeypatch, template, texts, hidden):
    ws = rig[0]
    original_device = ws.device
    def device(spec, state_path=None):
        dev = original_device(spec, state_path=state_path)
        scope = dev.world["screens"]["display"]["root"]["children"][1]
        for child in scope["children"]:
            child["attrs"] = {"visible": hidden}
        scope["children"] += [{"t": "Image", "desc": text, "b": [20, 65 + i * 30, 380, 90 + i * 30]}
                               for i, text in enumerate(texts)]
        return dev
    monkeypatch.setattr(ws, "device", device)
    _, run = _run(rig, template, texts)
    assert run["verdict"]["verdict"] == "pending", run["assertions"]
    results = [a for a in run["assertions"] if a["type"] in ("text_present", "text_present_any")]
    assert all(a["result"] == "undeterminable" and not a["verified"] for a in results)
