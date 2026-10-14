"""页面候选限制与当前确认来源；合成树仅验证执行边界，不确认产品规则。"""
import pytest

from vpdtest.states import StateModel
from vpdtest.tree import UiTree

BUNDLE = "com.yinwang.qiankunapp.hm"


def _node(kind, bounds, text="", children=None, **attrs):
    return {"attributes": {"type": kind, "bounds": bounds, "text": text, **attrs}, "children": children or []}


def _tree(label="确定", title=None, description=False, mixed=False):
    child = _node("Text", "[20,100][180,140]", "" if description else label,
                  description=label if description else "")
    button = _node("Button", "[10,90][190,150]", children=[child], clickable="true", id="confirm")
    if mixed:
        button["children"].append(_node("Text", "[20,140][180,150]", "返回"))
    children = [_node("Text", "[10,10][190,50]", title)] if title else []
    children.append(button)
    raw = _node("orgRoot", "[0,0][200,200]", children=[
        _node("Dialog" if title else "Column", "[0,0][200,200]", children=children)])
    tree = UiTree(raw)
    return tree, next(n for n in tree.nodes if n.id == "confirm")


def _check(ws, tree, node, effect="navigation", rules=None, sources=None, granted=None):
    return ws.policy().check_tap(tree, node.center, "tree", {"side_effect": effect}, rules,
                                 set(granted or []), node=node, confirmation_sources=sources)


def test_ambiguous_candidates_retain_union_without_becoming_active(ws):
    tree, node = _tree(title="开始")
    model = StateModel({"states": {
        "motion": {"require_text": ["开始"], "effect_text": {"^确定$": "vehicle_motion"}},
        "data": {"require_text": ["开始"], "effect_text": {"^确定$": "user_data"}},
    }}, BUNDLE)
    page = model.identify(tree, BUNDLE, True)
    assert page["state"] == "ambiguous" and page["active"] == []
    rules = model.deny_for_page(page)
    assert {r["class"] for r in rules} == {"vehicle_motion", "user_data"}
    for effect in ("navigation", "vehicle_motion", "user_data"):
        assert not _check(ws, tree, node, effect, rules, granted=["vehicle_motion", "user_data"])["allowed"]


def test_pane_only_candidates_retain_restrictions(ws):
    tree = UiTree(_node("orgRoot", "[0,0][400,200]", children=[
        _node("Column", "[0,0][200,200]", children=[_node("Text", "[10,10][100,50]", "LEFT")]),
        _node("Column", "[200,0][400,200]", children=[
            _node("Text", "[210,10][300,50]", "A"), _node("Text", "[310,10][390,50]", "B"),
            _node("Button", "[210,100][390,150]", "确定", clickable="true")])]))
    panes = [n for n in tree.nodes if n.type == "Column"]
    model = StateModel({"states": {
        "motion": {"require_text": ["A"], "forbid_text": ["LEFT"],
                   "effect_text": {"^确定$": "vehicle_motion"}},
        "data": {"require_text": ["B"], "forbid_text": ["LEFT"], "effect_text": {"^确定$": "user_data"}},
    }}, BUNDLE)
    page = model.identify(tree, BUNDLE, True, {"panes": [
        {"index": i, "node_idx": n.idx} for i, n in enumerate(panes)]})
    assert page["candidates"] == [] and page["state"] == "ambiguous" and page["active"] == []
    assert page["panes"][1]["candidates"] == ["motion", "data"]
    node = next(n for n in tree.nodes if n.text == "确定")
    assert not _check(ws, tree, node, rules=model.deny_for_page(page))["allowed"]


@pytest.mark.parametrize("effect", ["navigation", "read_only", "dismiss_dialog", "vehicle_motion"])
@pytest.mark.parametrize("description", [False, True])
def test_generic_declarations_and_grants_do_not_prove_unknown_semantics(ws, effect, description):
    tree, node = _tree(" 确定 ", description=description)
    target = {"side_effect": effect, "review": "reviewed", "source": "self asserted benign"}
    result = ws.policy().check_tap(tree, node.center, "tree", target, authorized={"vehicle_motion"}, node=node)
    assert not result["allowed"] and result["deny"]["class"] == "confirmation_semantics_unknown"


def test_legacy_pane_record_without_owner_is_not_positive_confirmation_proof():
    tree, _ = _tree()
    model = StateModel({"states": {"known": {"effect_text": {"^确定$": "user_data"}}}}, BUNDLE)
    page = {"panes": [{"pane": 0, "candidates": ["known"]}], "candidates": [], "active": []}
    assert model.deny_for_page(page)
    assert model.confirmation_sources(tree, page=page) == []


@pytest.mark.parametrize("other_rules", [{}, {"^返回$": "vehicle_motion"}])
def test_every_ambiguous_candidate_must_explain_the_confirmation(ws, other_rules):
    tree, node = _tree(title="开始")
    model = StateModel({"states": {
        "motion": {"require_text": ["开始"], "effect_text": {"^确定$": "vehicle_motion"}},
        "other": {"require_text": ["开始"], "effect_text": other_rules},
    }}, BUNDLE)
    page = model.identify(tree, BUNDLE, True)
    assert page["state"] == "ambiguous"
    result = _check(ws, tree, node, "vehicle_motion", model.deny_for_page(page),
                    model.confirmation_sources(tree, page=page), ["vehicle_motion"])
    assert not result["allowed"] and result["deny"]["class"] == "confirmation_semantics_unknown"


def test_same_effect_patterns_in_every_candidate_are_positive_evidence(ws):
    tree, node = _tree(title="开始")
    model = StateModel({"states": {
        "motion": {"require_text": ["开始"], "effect_text": {"^确定$": "vehicle_motion"}},
        "other": {"require_text": ["开始"], "effect_text": {
            "^确定$": "vehicle_motion", "确定": "vehicle_motion"}},
    }}, BUNDLE)
    page = model.identify(tree, BUNDLE, True)
    result = _check(ws, tree, node, "vehicle_motion", model.deny_for_page(page),
                    model.confirmation_sources(tree, page=page), ["vehicle_motion"])
    assert result["allowed"]
    source = result["confirmation_source"]["page_sources"][0]
    assert source["class"] == "vehicle_motion" and len(source["candidates"]) == 2


def test_every_generic_label_in_the_container_needs_candidate_consensus(ws):
    tree, node = _tree(title="开始", mixed=True)
    next(n for n in tree.nodes if n.text == "返回").text = "OK"
    model = StateModel({"states": {"motion": {
        "require_text": ["开始"], "effect_text": {"^确定$": "vehicle_motion"}}}}, BUNDLE)
    page = model.identify(tree, BUNDLE, True)
    result = _check(ws, tree, node, "vehicle_motion", model.deny_for_page(page),
                    model.confirmation_sources(tree, page=page), ["vehicle_motion"])
    assert not result["allowed"] and result["deny"]["class"] == "confirmation_semantics_unknown"


def test_input_value_named_confirmation_is_not_a_confirmation_button(ws):
    tree, node = _tree()
    node.type = "TextInput"
    assert _check(ws, tree, node, "user_data", granted=["user_data"])["allowed"]


def test_page_effect_rules_match_normalized_label_but_keep_raw_evidence(ws):
    tree, node = _tree(" 确定 ")
    result = _check(ws, tree, node, rules=[{"pattern": "^确定$", "class": "vehicle_motion"}])
    assert not result["allowed"] and result["deny"]["class"] == "vehicle_motion"
    assert result["deny"]["text"] == " 确定 "


@pytest.mark.parametrize("title", ["重命名", "修改名称"])
def test_modal_controlled_text_proves_effect_only_with_matching_declaration_and_grant(ws, title):
    tree, node = _tree(title=title)
    result = _check(ws, tree, node, "user_data", granted=["user_data"])
    assert result["allowed"] and result["confirmation_source"]["modal_idx"] is not None
    assert not _check(ws, tree, node, granted=["user_data"])["allowed"]
    assert not _check(ws, tree, node, "user_data")["allowed"]


@pytest.mark.parametrize("review", ["reviewed", "pending"])
def test_benign_confirmation_uses_current_registered_modal_and_declared_button(ws, review):
    tree, node = _tree(title="说明已阅读")
    model = StateModel({"interrupts": {"fixture_notice": {"class": "benign", "review": review,
                        "require_text": ["说明已阅读"], "dismiss_text": "确定"}}}, BUNDLE)
    sources = model.confirmation_sources(tree)
    result = _check(ws, tree, node, sources=sources)
    assert result["allowed"] is (review == "reviewed")
    if review == "reviewed":
        assert result["confirmation_source"]["benign"][0]["interrupt"] == "fixture_notice"
    other, other_node = _tree(title="未登记通知")
    assert not _check(ws, other, other_node, sources=model.confirmation_sources(other))["allowed"]


def test_benign_source_cannot_cancel_candidate_motion_or_hard_rule(ws):
    tree, node = _tree(title="说明已阅读")
    model = StateModel({"interrupts": {"notice": {"class": "benign", "review": "reviewed",
                        "require_text": ["说明已阅读"], "dismiss_text": "确定"}}}, BUNDLE)
    sources = model.confirmation_sources(tree)
    assert not _check(ws, tree, node, sources=sources,
                      rules=[{"pattern": "^确定$", "class": "vehicle_motion"}])["allowed"]
    paid, paid_node = _tree(title="支付说明")
    assert not _check(ws, paid, paid_node, "payment", granted=["payment"])["allowed"]


def test_benign_registration_does_not_override_an_unreviewed_interrupt_in_same_modal(ws):
    tree, node = _tree(title="说明已阅读")
    model = StateModel({"interrupts": {
        "reviewed": {"class": "benign", "review": "reviewed", "require_text": ["说明已阅读"],
                     "dismiss_text": "确定"},
        "unreviewed": {"class": "benign", "review": "pending", "require_text": ["说明已阅读"],
                       "dismiss_text": "确定"},
    }}, BUNDLE)
    assert model.confirmation_sources(tree) == []
    assert not _check(ws, tree, node, sources=model.confirmation_sources(tree))["allowed"]


def test_pure_safe_stop_and_navigation_remain_allowed_but_mixed_confirmation_does_not(ws):
    for label, effect in (("返回", "safe_stop"), ("暂停", "safe_stop"), ("我的", "navigation")):
        tree, node = _tree(label)
        assert _check(ws, tree, node, effect)["allowed"]
    tree, node = _tree(mixed=True)
    assert not _check(ws, tree, node, "safe_stop")["allowed"]


def test_known_generic_label_cannot_use_no_tree_template_as_semantic_proof(ws):
    policy = ws.policy()
    assert not policy.check_tap(None, (10, 10), "template", {"label": "确定"})["allowed"]
    assert policy.check_tap(None, (10, 10), "template", {"label": "我的"})["allowed"]


def test_legacy_page_record_still_gets_declared_state_rules():
    model = StateModel({"states": {"known": {"deny_text": ["blocked"]}}}, BUNDLE)
    assert model.deny_for_page({"state": "known"}) == ["blocked"]


def test_planning_status_in_modal_does_not_prove_confirmation_even_with_planning_grant(ws):
    tree, node = _tree(title="路线规划中，请稍后")
    result = _check(ws, tree, node, "route_planning", granted=["route_planning"])
    assert not result["allowed"] and result["deny"]["class"] == "confirmation_semantics_unknown"


def test_status_in_same_clickable_container_is_only_a_restriction_not_a_source(ws):
    tree, node = _tree()
    node.text = "路线规划中"
    result = _check(ws, tree, node, "route_planning", granted=["route_planning"])
    assert not result["allowed"] and result["deny"]["class"] == "confirmation_semantics_unknown"


def test_no_tree_xpath_literal_confirmation_cannot_use_template_fallback(ws):
    target = {"strategies": [{"kind": "xpath", "value": '//*[@text="确定"]'},
                             {"kind": "template", "file": "confirm.png"}]}
    assert not ws.policy().check_tap(None, (10, 10), "template", target)["allowed"]


def test_editable_value_cannot_impersonate_a_registered_modal_title(ws):
    tree, node = _tree(title="未登记语义")
    title = next(n for n in tree.nodes if n.text == "未登记语义")
    title.type = "TextInput"
    title.text = "修改名称"
    result = _check(ws, tree, node, "user_data", granted=["user_data"])
    assert not result["allowed"] and result["deny"]["class"] == "confirmation_semantics_unknown"


@pytest.mark.parametrize("label", ["返回\n确定", "暂停 确定", "确认停止", "确定暂停"])
def test_combined_stop_and_confirmation_labels_do_not_get_safe_stop_exemption(ws, label):
    tree, node = _tree(label)
    assert not _check(ws, tree, node, "safe_stop")["allowed"]


@pytest.mark.parametrize("xpath", ['//*[normalize-space(@text)="确定"]', '//*["确定"=@text]',
                                   '//*[contains(@description,"确认")]'])
def test_no_tree_xpath_confirmation_literals_cannot_hide_behind_other_xpath_shapes(ws, xpath):
    target = {"strategies": [{"kind": "xpath", "value": xpath}, {"kind": "template", "file": "ok.png"}]}
    assert not ws.policy().check_tap(None, (10, 10), "template", target)["allowed"]


def test_nested_modal_caption_cannot_supply_parent_confirmation_source(ws):
    tree = UiTree(_node("orgRoot", "[0,0][400,400]", children=[
        _node("Dialog", "[0,0][400,400]", children=[
            _node("Dialog", "[0,0][200,150]", children=[_node("Text", "[10,10][190,50]", "修改名称")]),
            _node("Button", "[210,200][390,250]", "确定", clickable="true", id="confirm")])]))
    node = next(n for n in tree.nodes if n.id == "confirm")
    assert not _check(ws, tree, node, "user_data", granted=["user_data"])["allowed"]
    model = StateModel({"interrupts": {"nested": {"class": "benign", "review": "reviewed",
                        "require_text": ["修改名称"], "dismiss_text": "确定"}}}, BUNDLE)
    assert not _check(ws, tree, node, sources=model.confirmation_sources(tree))["allowed"]


def test_other_pane_effect_is_a_restriction_but_cannot_prove_unknown_confirmation(ws):
    tree = UiTree(_node("orgRoot", "[0,0][400,200]", children=[
        _node("Column", "[0,0][200,200]", children=[_node("Text", "[10,10][190,50]", "LEFT")]),
        _node("Column", "[200,0][400,200]", children=[
            _node("Text", "[210,10][390,50]", "RIGHT"),
            _node("Button", "[210,100][390,150]", "确定", clickable="true", id="confirm")])]))
    panes = [n for n in tree.nodes if n.type == "Column"]
    model = StateModel({"states": {"left": {"require_text": ["LEFT"], "forbid_text": ["RIGHT"],
                        "effect_text": {"^确定$": "user_data"}}}}, BUNDLE)
    page = model.identify(tree, BUNDLE, True, {"panes": [
        {"index": i, "node_idx": n.idx} for i, n in enumerate(panes)]})
    node = next(n for n in tree.nodes if n.id == "confirm")
    result = _check(ws, tree, node, "user_data", model.deny_for_page(page),
                    model.confirmation_sources(tree, page=page), ["user_data"])
    assert not result["allowed"] and result["deny"]["class"] == "confirmation_semantics_unknown"
