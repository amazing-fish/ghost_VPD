"""Case integration and same-row counterexamples, using synthetic UI only."""
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import unconfirm

from vpdtest import cases
from vpdtest.cases import lint, require_review, review_status
from vpdtest.execute import _measure
from vpdtest.knowledge import KnowledgeStore
from vpdtest.locate import resolve
from vpdtest.params import Params
from vpdtest.tree import UiTree
from vpdtest.util import read_json
from vpdtest.verdict import evaluate_assertion

AREA = "由度金闽园地下停车场"
OTHER = "华为练秋湖研发中心"
REPO = Path(__file__).resolve().parents[2]
ACCEPTED = {240, 241, 243, 244, 245, 246, 247, 248, 249}


def _node(typ, text="", children=None, y=0):
    return {"attributes": {"type": typ, "text": text, "bounds": f"[0,{y}][1000,{y+160}]"},
            "children": children or []}


def _tree(rows):
    return UiTree({"attributes": {"type": "Column", "bounds": "[0,0][1100,1600]"}, "children": rows})


def _row(name, fields, y, typ="ListItem"):
    return _node(typ, children=[_node("Text", name, y=y),
                               *[_node("Text", s, y=y+20+i*20) for i, s in enumerate(fields)]], y=y)


def _read(ws, case_id, step_id, tree):
    case = ws.load_case(case_id)
    app = ws.app(case["app"])
    step = next(s for s in case["steps"] if s["id"] == step_id)
    target = Params(case["test_data"], {}, "synthetic").subst(step["target"])
    loc = resolve("synthetic-case-read", target, tree, None, app.dir)
    if loc["status"] != "found":
        return loc["status"], None
    return loc["status"], _measure(tree, loc, step)


@pytest.mark.parametrize("number", range(240, 250))
def test_assets_preserve_the_users_per_case_review_decisions(ws, number):
    case = ws.load_case(f"TC-QK-{number}")
    kn = KnowledgeStore(ws.root)
    issues = lint(case, ws.app(case["app"]), kn, ws.policy(), runs_dir=None)
    if number == 242:
        # Preserve the original scope declaration and pending human decision;
        # its free text now has a precise metadata diagnostic, never an automatic migration.
        assert case["knowledge_used"][0]["applicability"] == (
            "qiankun_hm 1.0.26.100 / K89由度园区A207专用子范围")
        assert len(issues) == 1 and issues[0]["field"] == "knowledge_used[0].applicability"
        assert "rationale" in issues[0]["problem"]
    else:
        assert issues == []
    review = review_status(ws, case, kn)
    first = read_json(REPO / "docs/reviews/K89_20261026.json")
    followup = read_json(REPO / "docs/reviews/K89_20261026_FOLLOWUP.json")
    decision = next(d for d in first["decisions"] if d["case"] == case["id"])
    assert review["required"]
    if number in ACCEPTED:
        if number in {244, 248}:
            assert followup["decisions"][str(number)].startswith("accept ")
            reviewed = followup["reviewed_bindings"][str(number)]
        else:
            assert decision["decision"] == "accept"
            reviewed = decision["reviewed_binding"]
        archive = read_json(REPO / "docs/evidence/K89_TAG_ENGINE_20261026.json")
        prior = next(r for r in archive["unchanged_cases"] if r["case"] == case["id"])
        record = case["asset_review"]
        assert record["schema"] == 1 and record["reviewer"] == first["reviewer"]
        assert record["basis"] == prior["basis"]
        assert record["binding"] == prior["after"]
        assert {k: v for k, v in record["binding"].items() if k != "engine_hash"} == {
            k: v for k, v in reviewed.items() if k != "engine_hash"}
        # These are historical approvals, not permanent permission to run changed
        # dependencies or engine bytes. Current validity has its own gate below.
        assert review["record"] == record
    else:
        assert decision["decision"] == "change"
        assert followup["decisions"][str(number)].startswith("clarify:")
        assert case.get("asset_review") is None
        assert review["state"] == "pending" and review["valid"] is False
    assert case["onebox_case_ref"]["full_row_coverage"] is False
    assert all(kn.get(ref)["status"] == "confirmed"
               for a in case["assertions"] for ref in a["oracle_refs"])


@pytest.mark.parametrize("number", sorted(ACCEPTED))
@pytest.mark.parametrize("change", ["dependency", "engine"])
def test_historical_acceptance_cannot_authorize_changed_execution(ws, tmp_path, monkeypatch, number, change):
    case = ws.load_case(f"TC-QK-{number}")
    before = ws.case_path(case["id"]).read_bytes()
    app = ws.app(case["app"])
    current = review_status(ws, case, app=app)["current"]
    if change == "dependency":
        app.states.states["qk_vpd_home"]["forbid_text"].append("Synthetic changed home requirement")
        changed_key = "dependency_hash"
    else:
        engine = tmp_path / "unreviewed-engine"
        engine.mkdir()
        (engine / "fixture.py").write_bytes(b"pass\n")
        monkeypatch.setattr(cases, "__file__", str(engine / "fixture.py"))
        changed_key = "engine_hash"
    result = review_status(ws, case, app=app)
    assert result["current"][changed_key] != current[changed_key]
    assert result["current"][changed_key] != case["asset_review"]["binding"][changed_key]
    assert result["required"] and result["valid"] is False and result["state"] == "stale"
    with pytest.raises(PermissionError, match="审核失效"):
        require_review(ws, case, app=app)
    assert ws.case_path(case["id"]).read_bytes() == before


@pytest.mark.parametrize("case_id,step_id,typ,good_fields", [
    ("TC-QK-241", "S_COUNT", "Row", ["泊车代驾", "共 695 个"]),
    ("TC-QK-246", "S_PARK_RESULT", "ListItem", ["当前位置", "共 695 个", "可用 L1、B1/共 2 层"]),
    ("TC-QK-248", "S_CURRENT", "ListItem", ["当前位置", "共 695 个", "可用 L1、B1/共 2 层"]),
    ("TC-QK-249", "S_INFO_COUNT", "ListItem", ["当前位置", "共 695 个", "可用 L1、B1/共 2 层"]),
    ("TC-QK-249", "S_INFO_FLOORS", "ListItem", ["当前位置", "共 695 个", "可用 L1、B1/共 2 层"]),
])
def test_neighbor_fields_cannot_complete_current_parking_row(ws, case_id, step_id, typ, good_fields):
    good = _tree([_row(AREA, good_fields, 0, typ), _row(OTHER, ["63.7km"], 300, typ)])
    status, measurement = _read(ws, case_id, step_id, good)
    assert status == "found" and measurement["value"] is not None
    # Same visible whole-page words, different entity ownership.
    wrong_row = _tree([_row(AREA, [], 0, typ), _row(OTHER, good_fields, 300, typ)])
    status, measurement = _read(ws, case_id, step_id, wrong_row)
    assert status == "found" and measurement["value"] is None


def test_same_parking_name_in_two_rows_is_not_silently_selected(ws):
    tree = _tree([_row(AREA, ["当前位置", "共 695 个", "可用 L1、B1/共 2 层"], y)
                  for y in (0, 300)])
    assert _read(ws, "TC-QK-249", "S_INFO_COUNT", tree) == ("ambiguous", None)


def test_display_evidence_keeps_unconfirmed_product_verdict(ws):
    unconfirm(ws, "K-QK-IDX-241")
    case = ws.load_case("TC-QK-241")
    status, measurement = _read(ws, case["id"], "S_COUNT", _tree([_row(AREA, ["共 695 个"], 0, "Row")]))
    assert status == "found"
    assertion = deepcopy(case["assertions"][0])
    evaluated = evaluate_assertion(assertion, {"S_COUNT": {"outcome": "passed", "measurement": measurement}},
                                   lambda _: (_ for _ in ()).throw(AssertionError("read measurement should be used")),
                                   KnowledgeStore(ws.root), ws.app(case["app"]))
    assert evaluated["observed_holds"] is True and evaluated["result"] == "pending"
