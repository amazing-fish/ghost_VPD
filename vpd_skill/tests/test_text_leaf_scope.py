"""Explicit Text-leaf scope contracts. All text, geometry and registrations are synthetic."""
from copy import deepcopy
from pathlib import Path

import pytest
import test_case_templates as T
import test_scope_inspection as D
from test_case_templates import rig as _rig
from vpdtest import cases
from vpdtest.case_templates import build_case
from vpdtest.cli import build_parser
from vpdtest.commands import cmd_case
from vpdtest.scope_inspection import inspect_scope
from vpdtest.util import write_json


rig = _rig


@pytest.fixture
def leaf_rig(rig, monkeypatch):
    ws = rig[0]
    app = ws.app(T.APP)
    app.targets["parking_name"]["template_scope"]["shape"] = "text_leaf"
    monkeypatch.setattr(ws, "app", lambda _: app)
    original = T._container
    def leaf(identity, texts, y=60):
        if identity == "parking_name":
            return {"t": "Text", "id": identity, "text": texts[0] if texts else "",
                    "b": [20, y, 380, y + 30]}
        return original(identity, texts, y)
    monkeypatch.setattr(T, "_container", leaf)
    return rig


def run_leaf(rig, texts, **kwargs):
    return T._run(rig, "TC-QK-240", texts, expected={"scope_shape": "text_leaf"}, **kwargs)


def test_leaf_reads_full_exact_name_and_does_not_borrow_other_labels(leaf_rig):
    case, run = run_leaf(leaf_rig, ["独立园区A"])
    assert run["verdict"]["verdict"] == "passed"
    assert run["steps"][0]["measurement"]["raw"] == "独立园区A"
    assert case["origin"]["template_version"] == 2
    assert case["origin"]["inputs"]["scope_shape"] == "text_leaf"
    assert case["status"] == "draft"
    case, run = run_leaf(leaf_rig, ["错误园区"], others=[T._container("other", ["独立园区A"], 240)])
    assert run["steps"][0]["outcome"] == "passed"
    assert run["steps"][0]["measurement"]["raw"] == "错误园区"
    assert run["assertions"][0]["result"] == "fail"
    assert run["verdict"]["verdict"] == "failed"


def test_leaf_mismatch_remains_pending_without_confirmed_oracle(leaf_rig):
    _, run = run_leaf(leaf_rig, ["错误园区"], confirmed=False)
    assert run["verdict"]["verdict"] == "pending"


@pytest.mark.parametrize("kind", ["duplicate", "hidden_duplicate", "wrong_duplicate", "hidden", "ancestor",
    "clipped", "rounding", "no_bounds", "non_text", "child", "empty_child", "root", "description", "empty"])
def test_leaf_runtime_rejects_invalid_structure_and_geometry(leaf_rig, monkeypatch, kind):
    ws = leaf_rig[0]
    original = ws.device
    def device(spec, state_path=None):
        dev = original(spec, state_path=state_path)
        root = dev.world["screens"]["display"]["root"]
        leaf = root["children"][1]
        if kind.endswith("duplicate"):
            duplicate = deepcopy(leaf)
            if kind == "hidden_duplicate":
                duplicate["attrs"] = {"visible": "false"}
            if kind == "wrong_duplicate":
                duplicate["text"] = "不同名称"
            root["children"].append(duplicate)
        elif kind == "hidden":
            leaf["attrs"] = {"visible": "false"}
        elif kind == "ancestor":
            root["children"][1] = {"t": "Row", "b": [0, 40, 400, 200], "attrs": {"visible": "false"},
                                   "children": [leaf]}
        elif kind == "clipped":
            root["children"][1] = {"t": "Scroll", "b": [0, 40, 400, 75], "children": [leaf]}
        elif kind == "rounding":
            root["b"] = [0, 0, 10000, 10000]
            leaf["b"] = [0, 0, 10000, 10000]
            root["children"][1] = {"t": "Scroll", "b": [0, 0, 10000, 9999], "children": [leaf]}
        elif kind == "no_bounds":
            leaf["b"] = [0, 0, 0, 0]
        elif kind == "non_text":
            leaf["t"] = "Row"
        elif kind in ("child", "empty_child"):
            leaf["children"] = [{"t": "Text", "text": "额外文字" if kind == "child" else "",
                                 "b": [30, 65, 100, 80]}]
        elif kind == "root":
            root.update(id="parking_name", t="Text", text=leaf["text"])
            leaf["id"] = "other"
        elif kind == "description":
            leaf.update(text="错误园区", desc="独立园区A")
        else:
            leaf["text"] = ""
        return dev
    monkeypatch.setattr(ws, "device", device)
    _, run = run_leaf(leaf_rig, ["独立园区A"])
    assert run["verdict"]["verdict"] != "passed", run["assertions"]


def test_leaf_wrong_page_is_not_display_success(leaf_rig):
    _, run = run_leaf(leaf_rig, ["独立园区A"], page="错误页面")
    assert run["verdict"]["verdict"] == "failed"


@pytest.mark.parametrize("value", [None, False, "", "unknown", 0, [], {}])
def test_invalid_explicit_shape_never_falls_back(rig, value):
    with pytest.raises(ValueError):
        build_case(rig[0].app(T.APP), T._spec(scope_shape=value))
    with pytest.raises(ValueError):
        inspect_scope(D.APP, {**D.spec(), "scope_shape": value}, D.observation(), D.tree())


@pytest.mark.parametrize("template", ["TC-QK-241", "TC-QK-243"])
def test_other_templates_never_accept_leaf_mode(rig, template):
    with pytest.raises(ValueError):
        build_case(rig[0].app(T.APP), T._spec(template, scope_shape="text_leaf"))
    with pytest.raises(ValueError):
        inspect_scope(D.APP, {**D.spec(template), "scope_shape": "text_leaf"}, D.observation(), D.tree())


@pytest.mark.parametrize("change", ["default", "container", "role", "unverified", "status", "source", "expected_ui"])
def test_leaf_needs_explicit_matching_independent_registration(leaf_rig, change):
    app = leaf_rig[0].app(T.APP)
    spec = T._spec(scope_shape="text_leaf")
    target = app.targets["parking_name"]
    if change == "default":
        spec.pop("scope_shape")
    elif change == "container":
        target["template_scope"]["shape"] = "text_container"
    elif change == "role":
        target["template_scope"]["role"] = "parking_count"
    elif change == "unverified":
        target["template_scope"]["calibration"]["status"] = "pending"
    elif change == "status":
        target["status"] = "calibrated_on_device"
    elif change == "source":
        spec["scope_source"]["ref"] = "unregistered.json"
    else:
        spec["expected_source"] = {"source": "test_data", "ref": "session-test/obs-0001"}
    with pytest.raises(ValueError):
        build_case(app, spec)


def test_container_default_keeps_original_assertions(rig):
    app = rig[0].app(T.APP)
    default = build_case(app, T._spec())
    explicit = build_case(app, T._spec(scope_shape="text_container"))
    assert default == explicit
    assert default["origin"]["inputs"]["scope_shape"] == "text_container"


def leaf_tree():
    return D.node("Root", bounds=(0, 0, 400, 800), children=[
        D.node("Row", children=[D.node("Text", id="scope", text="合成完整名称", bounds=(20, 20, 200, 40)),
                               D.node("Text", text="其他标签", bounds=(20, 60, 200, 80))])])


def test_inspector_is_explicit_read_only_and_not_an_expected_generator(monkeypatch):
    raw, spec, obs = leaf_tree(), {**D.spec(), "scope_shape": "text_leaf"}, D.observation()
    before = deepcopy((raw, spec, obs, D.APP.targets))
    def forbidden(*args, **kwargs):
        pytest.fail("pure inspection performed filesystem I/O")
    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        result = inspect_scope(D.APP, spec, obs, raw)
    assert result["shape"]["compatible"] is True
    assert result["scope_shape"] == "text_leaf" and result["template_version"] == 2
    assert result["supported_shapes"] == ["text_container", "text_leaf"]
    assert result["production_calibration"] == "not_granted"
    assert not {"expected_name", "case", "assertions"} & result.keys()
    assert (raw, spec, obs, D.APP.targets) == before
    assert inspect_scope(D.APP, D.spec(), obs, raw)["shape"]["compatible"] is False


@pytest.mark.parametrize("kind", ["non_text", "child", "empty_child", "empty", "root", "hidden", "clipped",
                                  "duplicate", "hidden_duplicate"])
def test_inspector_leaf_counterexamples(kind):
    raw = leaf_tree()
    leaf = raw["children"][0]["children"][0]
    if kind == "non_text":
        leaf["attributes"]["type"] = "Row"
    elif kind in ("child", "empty_child"):
        leaf["children"] = [D.node("Text", text="更多" if kind == "child" else "", bounds=(25, 25, 30, 30))]
    elif kind == "empty":
        leaf["attributes"].update(text="", description="合成完整名称")
    elif kind == "root":
        raw = leaf
    elif kind == "hidden":
        raw["children"][0]["attributes"]["visible"] = "false"
    elif kind == "clipped":
        raw["children"][0]["attributes"].update(type="Scroll", bounds="[10,10][390,30]")
    else:
        duplicate = deepcopy(leaf)
        if kind == "hidden_duplicate":
            duplicate["attributes"]["visible"] = "false"
        raw["children"][0]["children"].append(duplicate)
    result = inspect_scope(D.APP, {**D.spec(), "scope_shape": "text_leaf"}, D.observation(), raw)
    assert result["shape"]["compatible"] is not True


def test_leaf_preview_and_binding_are_pure_and_fresh(leaf_rig, monkeypatch):
    ws, knowledge, root = leaf_rig
    path = root / "leaf-input.json"
    write_json(path, T._spec(scope_shape="text_leaf"))
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(ws, "device", lambda *a, **k: pytest.fail("preview touched device"))
    result = cmd_case(ws, build_parser().parse_args(["case", "instantiate", "--input", str(path)]))
    assert result["mode"] == "preview" and result["asset_review"]["state"] == "pending"
    assert not result["issues"]
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    case = result["case"]
    app = ws.app(T.APP)
    binding = cases._review_binding(ws, case, app, knowledge, ws.policy())
    changed = deepcopy(case)
    changed["origin"]["inputs"]["scope_shape"] = "text_container"
    assert cases._review_binding(ws, changed, app, knowledge, ws.policy())["content_hash"] != binding["content_hash"]
    app.targets["parking_name"]["template_scope"]["shape"] = "text_container"
    assert cases._review_binding(ws, case, app, knowledge, ws.policy())["dependency_hash"] != binding["dependency_hash"]


@pytest.mark.parametrize("mutation", ["shape", "expected_source", "registration", "result_contract"])
def test_leaf_changes_invalidate_existing_review(leaf_rig, monkeypatch, mutation):
    ws, knowledge, _ = leaf_rig
    case = build_case(ws.app(T.APP), T._spec(scope_shape="text_leaf"))
    write_json(ws.case_path(case["id"]), case)
    # Approval exists only in this synthetic temporary workspace.
    reviewed = cases.approve(ws, case["id"], knowledge, "pytest-synthetic", "Synthetic binding test only")
    assert cases.review_status(ws, reviewed, knowledge)["state"] == "approved"
    if mutation == "shape":
        reviewed["origin"]["inputs"]["scope_shape"] = "text_container"
    elif mutation == "expected_source":
        reviewed["origin"]["inputs"]["expected_source"]["ref"] = "independent/revised.json"
    elif mutation == "registration":
        ws.app(T.APP).targets["parking_name"]["template_scope"]["shape"] = "text_container"
    else:
        monkeypatch.setattr(cases, "RESULT_CONTRACT", cases.RESULT_CONTRACT + 1)
    info = cases.review_status(ws, reviewed, knowledge)
    assert info["state"] == "stale" and info["valid"] is False


def test_leaf_engine_change_is_reported_without_invalidating_review(leaf_rig, monkeypatch):
    ws, knowledge, _ = leaf_rig
    case = build_case(ws.app(T.APP), T._spec(scope_shape="text_leaf"))
    write_json(ws.case_path(case["id"]), case)
    reviewed = cases.approve(ws, case["id"], knowledge, "pytest-synthetic", "Synthetic binding test only")
    original = cases.review_snapshot.engine_files
    monkeypatch.setattr(cases.review_snapshot, "engine_files",
                        lambda root: {**original(root), "case_templates.py": "0" * 64})
    info = cases.review_status(ws, reviewed, knowledge)
    assert info["state"] == "approved" and info["valid"] is True
    assert "case_templates.py" in info["engine_changes"]["modified"]
