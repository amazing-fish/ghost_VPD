"""Tag selection, empty-query and same-POI counterexamples use the real assertion path."""
import pytest

from vpdtest.execute import _measure
from vpdtest.knowledge import KnowledgeStore
from vpdtest.locate import resolve
from vpdtest.tree import UiTree
from vpdtest.verdict import evaluate_assertion


def node(kind, text="", children=(), **attrs):
    return {"attributes": {"type": kind, "text": text, "bounds": "[0,0][1000,900]", **attrs},
            "children": list(children)}


def tree(*, selected=True, query="", park="由度金闽园地下停车场", poi="4000191310", title="B1层-A207 车位"):
    chip=node("Row", children=[node("Text", "楼梯口")], backgroundColor="#FFEFF2F9" if selected else "#FFFFFFFF")
    result=node("Column", children=[node("Text", park), node("__Common__", id="searchPoiSpot_"+poi,
               children=[node("Row", children=[node("Text",title),node("Text","路线")])])])
    return UiTree(node("Column", children=[node("RelativeContainer", id="SearchPoiPage", children=[
        node("SearchField",query,id="__SearchField__search_input"),node("Row",id="my_location_and_filter_view",children=[chip]),result])]))


@pytest.mark.parametrize("selected,query,expected", [(True,"",True),(False,"",False),(True,"A207",False)])
def test_selected_tag_and_blank_query_are_separate_requirements(ws, selected, query, expected):
    case=ws.load_case("TC-QK-242")
    app=ws.app(case["app"])
    ui=tree(selected=selected,query=query)
    observed={"obs_id":"fixture","fresh":True,"tree":{"status":"ok"}}
    knowledge=KnowledgeStore(ws.root).bind({"app":"qiankun_hm","app_version":"1.0.26.100"})
    outcomes=[]
    for aid in ["A_TAG_SELECTED","A_QUERY_EMPTY"]:
        assertion=next(a for a in case["assertions"] if a["id"]==aid)
        r=evaluate_assertion(assertion,{"S_FILTER":{"outcome":"passed","final_obs":"fixture"}},
                             lambda _: (observed,ui),knowledge,app)
        outcomes.append(r["result"]=="pass")
    assert all(outcomes) is expected


@pytest.mark.parametrize("variant,expected", [({},True),({"park":"其他园区"},False),
    ({"poi":"another-poi"},False),({"title":"L1层-A207 车位"},False)])
def test_result_must_match_same_park_floor_and_poi_not_just_a207_text(ws,variant,expected):
    case=ws.load_case("TC-QK-242")
    app=ws.app(case["app"])
    ui=tree(**variant)
    step=next(s for s in case["steps"] if s["id"]=="S_RESULT")
    loc=resolve(step["target"],app.targets[step["target"]],ui,None,app.dir)
    measurement=_measure(ui,loc,step) if loc["status"]=="found" else {"value":None}
    assertion=next(a for a in case["assertions"] if a["id"]=="A_SAME_POI")
    knowledge=KnowledgeStore(ws.root).bind({"app":"qiankun_hm","app_version":"1.0.26.100"})
    result=evaluate_assertion(assertion,{"S_RESULT":{"outcome":"passed","measurement":measurement}},lambda _:None,knowledge,app)
    assert (result["result"]=="pass") is expected
