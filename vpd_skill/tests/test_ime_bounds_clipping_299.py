"""#299: clip full-screen HarmonyOS IME window bounds to the actual keyboard region."""
import pytest

from vpdtest.tree import UiTree


def _win_node(t, bounds, *, bundle="", text="", clickable=False, visible=True, children=None):
    attrs = {"type": t, "bounds": f"[{bounds[0]},{bounds[1]}][{bounds[2]},{bounds[3]}]"}
    if bundle:
        attrs["bundleName"] = bundle
    if text:
        attrs["text"] = text
    if clickable:
        attrs["clickable"] = "true"
    if not visible:
        attrs["visible"] = "false"
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


def test_299_hidden_ime_subtree_above_keyboard_does_not_expand_occlusion():
    ime_children = [
        _win_node("Column", (0, 107, 1828, 2584), children=[
            _win_node("Column", (0, 500, 400, 700), visible=False, children=[
                _win_node("Text", (80, 580, 300, 660), text="隐藏候选"),
            ]),
            _win_node("Button", (80, 1760, 260, 1900), clickable=True, text="1"),
            _win_node("Button", (1560, 2400, 1760, 2560), clickable=True, text="完成"),
        ])
    ]
    tree = _multi_window_tree(app_bounds=(0, 107, 1828, 2584), ime_children=ime_children)
    input_node = tree.nodes[2]
    assert tree.touch_check(900, 630, input_node)["allowed"] is True
    hit_kb = tree.touch_target(200, 1820)
    assert hit_kb is not None and hit_kb.text == "1"


@pytest.mark.parametrize("panel_clickable", [True, False])
def test_299_enclosing_keyboard_panel_over_half_frame_stays_fully_occluded(panel_clickable):
    tree = UiTree(
        _win_node(
            "root",
            (0, 0, 1000, 1000),
            children=[
                _win_node("root", (0, 0, 1000, 1000), bundle="com.yinwang.qiankunapp.hm", children=[
                    _win_node("Button", (400, 150, 600, 250), clickable=True, text="上方按钮"),
                    _win_node("Button", (400, 650, 600, 750), clickable=True, text="键盘区下方按钮"),
                ]),
                _win_node(
                    "root",
                    (0, 0, 1000, 1000),
                    bundle="com.huawei.hmos.inputmethod",
                    clickable=True,
                    children=[
                        _win_node(
                            "Column",
                            (0, 400, 1000, 1000),  # 60% enclosing keyboard panel (> 50% of frame)
                            clickable=panel_clickable,
                            children=[
                                _win_node("Text", (80, 420, 200, 470), text="完成"),
                            ],
                        )
                    ],
                ),
            ],
        )
    )
    upper_btn = tree.nodes[2]
    lower_btn = tree.nodes[3]
    assert upper_btn.text == "上方按钮" and lower_btn.text == "键盘区下方按钮"

    assert tree.touch_check(500, 200, upper_btn)["allowed"] is True
    chk_lower = tree.touch_check(500, 700, lower_btn)
    assert chk_lower["allowed"] is False and chk_lower["basis"] == "unrelated_receiver"


def test_299_transparent_blank_spacer_above_keyboard_panel_is_not_counted():
    ime_children = [
        _win_node("Column", (0, 107, 1828, 2584), children=[
            _win_node("Blank", (0, 107, 1828, 1745)),
            _win_node("Column", (0, 1745, 1828, 2584), children=[
                _win_node("Button", (80, 1760, 260, 1900), clickable=True, text="1"),
            ]),
        ])
    ]
    behind_kb = _win_node("Button", (600, 1950, 1000, 2080), clickable=True, text="键盘后按钮")
    tree = _multi_window_tree(app_bounds=(0, 107, 1828, 2584), ime_children=ime_children, app_extra=[behind_kb])
    input_node = tree.nodes[2]
    blocked_btn = tree.nodes[4]

    assert tree.touch_check(900, 630, input_node)["allowed"] is True
    chk_blocked = tree.touch_check(800, 2015, blocked_btn)
    assert chk_blocked["allowed"] is False and chk_blocked["basis"] == "unrelated_receiver"


def test_299_transparent_enclosing_wrapper_above_keyboard_does_not_expand_upward():
    tree = UiTree(
        _win_node(
            "root",
            (0, 0, 1000, 1000),
            children=[
                _win_node("root", (0, 0, 1000, 1000), bundle="com.yinwang.qiankunapp.hm", children=[
                    _win_node("Button", (400, 150, 600, 250), clickable=True, text="上方按钮"),
                    _win_node("Button", (400, 650, 600, 750), clickable=True, text="键盘区下方按钮"),
                ]),
                _win_node(
                    "root",
                    (0, 0, 1000, 1000),
                    bundle="com.huawei.hmos.inputmethod",
                    clickable=True,
                    children=[
                        _win_node(
                            "Column",
                            (0, 0, 1000, 850),  # transparent wrapper (< 90% of frame) starting at y=0
                            children=[
                                _win_node("Column", (0, 600, 1000, 850), children=[
                                    _win_node("Button", (80, 640, 220, 740), clickable=True, text="1"),
                                ]),
                            ],
                        )
                    ],
                ),
            ],
        )
    )
    upper_btn = tree.nodes[2]
    lower_btn = tree.nodes[3]
    assert tree.touch_check(500, 200, upper_btn)["allowed"] is True
    chk_lower = tree.touch_check(500, 700, lower_btn)
    assert chk_lower["allowed"] is False and chk_lower["basis"] == "unrelated_receiver"


def test_299_mid_frame_spacer_wrapper_above_panel_does_not_expand_upward():
    tree = UiTree(
        _win_node(
            "root",
            (0, 0, 1000, 1000),
            children=[
                _win_node("root", (0, 0, 1000, 1000), bundle="com.yinwang.qiankunapp.hm", children=[
                    _win_node("Button", (400, 400, 600, 500), clickable=True, text="中部按钮"),
                    _win_node("Button", (400, 650, 600, 750), clickable=True, text="键盘区下方按钮"),
                ]),
                _win_node(
                    "root",
                    (0, 0, 1000, 1000),
                    bundle="com.huawei.hmos.inputmethod",
                    clickable=True,
                    children=[
                        _win_node(
                            "Column",
                            (0, 350, 1000, 850),  # mid-frame wrapper containing spacer + panel
                            children=[
                                _win_node("Blank", (0, 350, 1000, 600)),
                                _win_node("Column", (0, 600, 1000, 850), children=[
                                    _win_node("Button", (80, 640, 220, 740), clickable=True, text="1"),
                                ]),
                            ],
                        )
                    ],
                ),
            ],
        )
    )
    mid_btn = tree.nodes[2]
    lower_btn = tree.nodes[3]
    assert tree.touch_check(500, 450, mid_btn)["allowed"] is True
    chk_lower = tree.touch_check(500, 700, lower_btn)
    assert chk_lower["allowed"] is False and chk_lower["basis"] == "unrelated_receiver"
