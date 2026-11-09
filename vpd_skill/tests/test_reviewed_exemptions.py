"""#207: App-specific page exemptions live in policy.json, not in the generic Policy engine."""
from copy import deepcopy
from pathlib import Path

import pytest
from test_favorites_browse_policy import _check as favorites_check
from test_favorites_browse_policy import _raw as favorites_raw
from vpdtest.policy import Policy
from vpdtest.tree import UiTree

POLICY_PY = Path(__file__).resolve().parents[1] / "vpdtest" / "policy.py"


@pytest.mark.parametrize("literal", ["qiankunapp", "VPD_HOME_VIEW", "收藏夹", "收藏及常去", "充电桩", "电梯口"])
def test_generic_policy_engine_has_no_app_specific_literals(literal):
    assert literal not in POLICY_PY.read_text(encoding="utf-8")


def _policy(ws, change):
    data = deepcopy(ws.policy().data)
    change(data)
    return Policy(data)


def test_editing_the_reclassified_rule_pattern_keeps_the_declared_exemption(ws):
    """The exemption binds the rule id, so a pattern edit no longer silently disables it."""
    def widen(data):
        rule = next(d for d in data["deny_text"] if d.get("id") == "user_data_keywords")
        rule["pattern"] += "|^置顶$"
    result = favorites_check(ws, UiTree(favorites_raw()), policy=_policy(ws, widen))
    assert result["allowed"], result
    assert result["navigation_source"]["reclassified_rule"]["rule_id"] == "user_data_keywords"


def test_missing_declaration_keeps_the_old_denial(ws):
    def drop(data):
        data["reviewed_exemptions"] = []
    result = favorites_check(ws, UiTree(favorites_raw()), policy=_policy(ws, drop))
    assert not result["allowed"] and result["deny"]["class"] == "user_data"


@pytest.mark.parametrize("change", [
    lambda d: d["reviewed_exemptions"].append(dict(d["reviewed_exemptions"][0])),
    lambda d: d["reviewed_exemptions"][0].update(reclassifies_rule="no_such_rule"),
    lambda d: d["reviewed_exemptions"][0].update(kind="unknown_kind"),
    lambda d: d["reviewed_exemptions"][0].update(entry_text=" "),
    lambda d: d["reviewed_exemptions"][1].update(group_labels=["充电桩"]),
    lambda d: d["reviewed_exemptions"][1].update(target_label="其他"),
    lambda d: next(r for r in d["deny_text"] if r.get("class") == "app_update").update(id="user_data_keywords"),
])
def test_invalid_declarations_are_rejected_when_policy_loads(ws, change):
    data = deepcopy(ws.policy().data)
    change(data)
    with pytest.raises(ValueError):
        Policy(data)
