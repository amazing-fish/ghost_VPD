"""基于 2026-09-29 在 SGT-AL10 / OpenHarmony-7.0.0.105 上实际采集的输出（已去除无关内容）。"""
from vpdtest.devices.harmony_hdc import classify_output, parse_foreground, parse_kv, parse_version
from vpdtest.devices.replay import placeholder_tree
from vpdtest.tree import UiTree

LOCKED = ("error: failed to start ability.\nError Code:10106102  Error Message:The device screen is locked during the "
          "application launch, unlock screen failed.\nError cause: The current mode is developer mode")
BAD_SERIAL = "[Fail]Not match target founded, check connect-key please"
BM_MISSING = "error: failed to get information and the parameters may be wrong."
AA_DUMP = """
    Mission ID #143  mission name #[#com.example.chat:entry:EntryAbility]  lockedState #0  mission affinity #[]
        bundle name [com.example.chat]
        state #FOREGROUND  start time [120440906]
        app state #FOREGROUND
    Mission ID #463  mission name #[#com.huawei.hmos.aidispatchservice:aisuggestion:AiSuggestionUIAbility]
        bundle name [com.huawei.hmos.aidispatchservice]
        state #BACKGROUND  start time [120603292]
"""
LOCK_DUMP = """ * screenState  		true		screen on / off status
 * deviceLocked  		true		100
 * screenLocked  		true		100"""


def test_hdc_exit_code_zero_but_output_is_failure():
    # 真机上这三条命令的 hdc 退出码都是 0，只能看输出
    assert classify_output(LOCKED) == "screen_locked"
    assert classify_output(BAD_SERIAL) == "device_offline"
    assert classify_output(BM_MISSING) == "command_failed"
    assert classify_output("start ability successfully.") is None


def test_foreground_and_lock_parsing():
    assert parse_foreground(AA_DUMP) == "com.example.chat"
    assert parse_foreground(AA_DUMP.replace("#FOREGROUND  start", "#BACKGROUND  start")) is None
    assert parse_kv(LOCK_DUMP, "screenLocked") == "true"
    assert parse_kv("Current State: SLEEP  Reason: 1", "Current State") == "SLEEP"
    assert parse_version('"versionName": ""\n"versionName": "3.0.3.200"') == "3.0.3.200"


def test_scoped_dump_of_background_app_is_empty_not_ok():
    # dumpLayout -b <bundle> 在目标不在前台时返回全空占位根节点：必须识别为 empty，而不是"元素不存在"
    t = UiTree(placeholder_tree())
    assert t.status == "empty"
    assert t.visible_texts() == []


def _node(t, text="", b="[0,0][10,10]", click="false", sel="false", kids=()):
    return {"attributes": {"type": t, "text": text, "bounds": b, "clickable": click, "selected": sel},
            "children": list(kids)}


def test_tab_selected_state_lives_on_clickable_container():
    # 2026-09-29 真机（鸿蒙智行 3.0.3.200）的底部导航结构：Text/Column/Stack 的 selected 均为 false，
    # TabBar 下可点击的 Column 为 true。这里只保留结构，不含任何真实页面内容。
    from vpdtest.states import StateModel, is_selected
    def tab(text, x, sel):
        return _node("Column", b=f"[{x},2640][{x + 440},2848]", click="true", sel=sel, kids=[
            _node("Stack", b=f"[{x},2640][{x + 440},2848]", kids=[
                _node("Column", b=f"[{x + 100},2650][{x + 340},2740]", kids=[
                    _node("Image", b=f"[{x + 180},2600][{x + 260},2670]"),
                    _node("Text", text, f"[{x + 186},2676][{x + 254},2723]")])])])
    raw = _node("orgRoot", b="[0,0][1320,2848]", kids=[_node("TabBar", b="[0,2640][1320,2848]", kids=[
        tab("发现", 0, "false"), tab("爱车", 440, "true"), tab("我的", 880, "false")])])
    t = UiTree(raw)
    assert not t.find_text("爱车")[0].selected
    assert is_selected(t, t.find_text("爱车")[0]) and not is_selected(t, t.find_text("我的")[0])
    sm = StateModel({"states": {"car_tab": {"require_selected_text": ["爱车"]},
                                "my_tab": {"require_selected_text": ["我的"]}}}, "b")
    assert sm.identify(t, "b", True)["state"] == "car_tab"


def test_repo_xpath_works_on_normalized_tree():
    raw = {"attributes": {"type": "orgRoot", "bounds": "[0,0][100,100]"}, "children": [
        {"attributes": {"type": "Text", "text": "默认车位", "bounds": "[0,0][50,20]"}, "children": []},
        {"attributes": {"type": "Text", "text": "A-102", "bounds": "[50,0][100,20]"}, "children": []}]}
    t = UiTree(raw)
    # 与 app/config/motor_config.py 中 default_parking_space_no_set 相同写法
    hits = t.xpath('//*[@text="默认车位"]/following-sibling::*[1]')
    assert [n.text for n in hits] == ["A-102"]
    assert t.find_text("默认车位")[0].center == (25, 10)
