from vpdtest.devices.replay import ReplayDevice
from vpdtest.tree import UiTree

from conftest import WORLDS


def _car_tab_tree(tmp_path):
    dev = ReplayDevice(WORLDS / "motor_hm_base.json")
    dev.launch(dev.bundle, "EntryAbility")
    return UiTree(dev.dump_tree(dev.bundle, tmp_path / "t.json").data["tree"])


def test_vehicle_and_account_actions_denied(ws, tmp_path):
    pol, tree = ws.policy(), _car_tab_tree(tmp_path)
    for text in ("解锁", "泊车代驾", "空调"):
        n = tree.find_text(text)[0]
        r = pol.check_tap(tree, n.center, "tree")
        assert not r["allowed"] and r["deny"], text
    assert pol.match_deny(["注册/登陆"])["class"] == "account_state"
    assert pol.match_deny(["更新"])["class"] == "app_update"
    assert pol.match_deny(["下次再说"]) is None


def test_navigation_allowed_and_state_deny_scoped_to_container(ws, tmp_path):
    pol, tree = ws.policy(), _car_tab_tree(tmp_path)
    n = tree.find_text("我的")[0]
    assert pol.check_tap(tree, n.center, "tree", None, ["车架号"])["allowed"]
    card = tree.find_text("示例车辆")[0]
    assert not pol.check_tap(tree, card.center, "tree", None, ["车架号"])["allowed"]


def test_no_blind_point_tap(ws, tmp_path):
    pol, tree = ws.policy(), _car_tab_tree(tmp_path)
    assert not pol.check_tap(None, (100, 100), "point")["allowed"]
    assert pol.check_tap(None, (100, 100), "template", {"side_effect": "navigation"})["allowed"]
    assert not pol.check_tap(None, (100, 100), "template", {"side_effect": "vehicle_control"})["allowed"]
    icon = [n for n in tree.nodes if n.attrs.get("type") == "Image" and n.bounds and n.bounds[1] == 880][0]
    r = pol.check_tap(tree, icon.center, "point")
    assert not r["allowed"] and "无文本" in r["reason"]


def test_switch_controls_denied_unless_target_opts_in(ws):
    raw = {"attributes": {"type": "orgRoot", "bounds": "[0,0][100,100]"}, "children": [
        {"attributes": {"type": "Row", "bounds": "[0,0][100,50]", "clickable": "true"}, "children": [
            {"attributes": {"type": "Text", "text": "消息通知", "bounds": "[0,0][60,50]"}, "children": []},
            {"attributes": {"type": "Toggle", "bounds": "[60,0][100,50]", "clickable": "true"}, "children": []}]}]}
    pol, tree = ws.policy(), UiTree(raw)
    r = pol.check_tap(tree, (80, 25), "tree")
    assert not r["allowed"] and r["deny"]["class"] == "setting_change"
    assert pol.check_tap(tree, (80, 25), "tree", {"allow_node_type": True})["allowed"]


def test_forbidden_targets(ws):
    pol = ws.policy()
    t = ws.app("motor_hm").targets
    assert not pol.check_tap(None, (0, 0), "template", t["update_now"])["allowed"]
    assert not pol.check_tap(None, (0, 0), "template", t["logout_entry"])["allowed"]
