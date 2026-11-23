"""#299: clip full-screen HarmonyOS IME window bounds to the actual keyboard region."""
from vpdtest.tree import UiTree


def _win_node(t, bounds, *, bundle="", text="", clickable=False, children=None):
    attrs = {"type": t, "bounds": f"[{bounds[0]},{bounds[1]}][{bounds[2]},{bounds[3]}]"}
    if bundle:
        attrs["bundleName"] = bundle
    if text:
        attrs["text"] = text
    if clickable:
        attrs["clickable"] = "true"
    return {"attributes": attrs, "children": children or []}


def _multi_window_tree(*, app_bounds=(0, 107, 1828, 1745), ime_children=None, app_extra=None):
    app_children = [
        _win_node("TextInput", (120, 580, 1700, 680), clickable=True, text=""),
        _win_node("Button", (120, 1000, 520, 1080), clickable=True, text="手动输入"),
    ]
    if app_extra:
        app_children.extend(app_extra)
    ime_default = [
        _win_node("Column", (0, 107, 1828, 2584), children=[
            _win_node("Button", (80, 1760, 260, 1900), clickable=True, text="1"),
            _win_node("Button", (1560, 2400, 1760, 2560), clickable=True, text="完成"),
        ])
    ]
    return UiTree(
        _win_node(
            "root",
            (0, 0, 1828, 2584),
            children=[
                _win_node("root", app_bounds, bundle="com.yinwang.qiankunapp.hm", children=app_children),
                _win_node("root", (0, 0, 1828, 107), bundle="com.ohos.sceneboard", children=[
                    _win_node("Text", (60, 20, 200, 80), text="20:00"),
                ]),
                _win_node(
                    "root",
                    (0, 107, 1828, 2584),
                    bundle="com.huawei.hmos.inputmethod",
                    clickable=True,
                    children=ime_default if ime_children is None else ime_children,
                ),
            ],
        )
    )


def test_299_ime_with_key_children_unblocks_app_form_while_preserving_keyboard_occlusion():
    tree = _multi_window_tree()
    input_node = tree.nodes[2]
    manual_btn = tree.nodes[3]
    assert input_node.type == "TextInput"
    assert manual_btn.text == "手动输入"

    # APP form elements above y=1760 must pass touch_check without target_obscured.
    chk_input = tree.touch_check(900, 630, input_node)
    assert chk_input["allowed"] is True, chk_input
    chk_manual = tree.touch_check(320, 1040, manual_btn)
    assert chk_manual["allowed"] is True, chk_manual

    # Actual keyboard region y=1760..2560 remains owned by IME.
    hit_kb = tree.touch_target(200, 1820)
    assert hit_kb is not None and hit_kb.text == "1"


def test_299_opaque_ime_without_key_children_clips_to_peer_app_bottom():
    tree = _multi_window_tree(app_bounds=(0, 107, 1828, 1745), ime_children=[])
    input_node = tree.nodes[2]
    manual_btn = tree.nodes[3]

    assert tree.touch_check(900, 630, input_node)["allowed"] is True
    assert tree.touch_check(320, 1040, manual_btn)["allowed"] is True

    # Below the resized APP bottom (y >= 1745) still hits the IME interceptor.
    hit_kb = tree.touch_target(900, 2000)
    assert hit_kb is not None and hit_kb.attrs.get("bundleName") == "com.huawei.hmos.inputmethod"


def test_299_non_resized_app_with_key_children_still_unblocks_upper_form_and_blocks_behind_kb():
    behind_kb = _win_node("Button", (600, 1950, 1000, 2080), clickable=True, text="底部被挡按钮")
    tree = _multi_window_tree(app_bounds=(0, 107, 1828, 2584), app_extra=[behind_kb])
    input_node = tree.nodes[2]
    blocked_btn = tree.nodes[4]
    assert blocked_btn.text == "底部被挡按钮"

    assert tree.touch_check(900, 630, input_node)["allowed"] is True
    chk_blocked = tree.touch_check(800, 2015, blocked_btn)
    assert chk_blocked["allowed"] is False and chk_blocked["basis"] == "unrelated_receiver"


def test_299_opaque_ime_without_keys_and_non_resized_app_fails_closed():
    tree = _multi_window_tree(app_bounds=(0, 107, 1828, 2584), ime_children=[])
    input_node = tree.nodes[2]
    chk = tree.touch_check(900, 630, input_node)
    assert chk["allowed"] is False and chk["basis"] == "unrelated_receiver"


def test_299_non_ime_system_overlay_is_never_clipped():
    tree = UiTree(
        _win_node(
            "root",
            (0, 0, 1828, 2584),
            children=[
                _win_node("root", (0, 107, 1828, 1745), bundle="com.yinwang.qiankunapp.hm", children=[
                    _win_node("TextInput", (120, 580, 1700, 680), clickable=True, text=""),
                ]),
                _win_node(
                    "root",
                    (0, 107, 1828, 2584),
                    bundle="com.ohos.sceneboard",
                    clickable=True,
                    children=[_win_node("Text", (80, 1800, 300, 1860), text="系统弹层")],
                ),
            ],
        )
    )
    input_node = tree.nodes[2]
    chk = tree.touch_check(900, 630, input_node)
    assert chk["allowed"] is False and chk["basis"] == "unrelated_receiver"
