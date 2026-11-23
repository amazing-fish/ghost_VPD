"""#298: modal overlay occlusion uses inner content card rect when outer Dialog is a full-screen touch interceptor."""
from vpdtest import cases as C
from vpdtest.locate import resolve
from vpdtest.tree import UiTree


def _node(t, bounds, *, text="", clickable=False, children=None):
    attrs = {"type": t, "bounds": f"[{bounds[0]},{bounds[1]}][{bounds[2]},{bounds[3]}]"}
    if text:
        attrs["text"] = text
    if clickable:
        attrs["clickable"] = "true"
    return {"attributes": attrs, "children": children or []}


def _foldable_298_tree():
    """Exact geometry captured on HOP-AL00 foldable in #298 (run-20261123-181159-f107/obs-0006)."""
    return UiTree(
        _node(
            "root",
            (0, 0, 2584, 1828),
            children=[
                _node(
                    "Column",
                    (0, 107, 2584, 1828),
                    children=[
                        _node("Text", (1000, 800, 1200, 860), text="背景被盖项"),
                        _node(
                            "TabBar",
                            (0, 1602, 2584, 1751),
                            children=[
                                _node("Column", (1230, 1620, 1350, 1750), clickable=True, children=[
                                    _node("Text", (1264, 1715, 1319, 1747), text="爱车"),
                                ]),
                            ],
                        ),
                    ],
                ),
                _node(
                    "Dialog",
                    (0, 107, 2584, 1828),
                    clickable=True,
                    children=[
                        _node(
                            "Column",
                            (841, 423, 1743, 1405),  # ~0.199 of outer area (< 1/4)
                            children=[
                                _node("Text", (920, 500, 1660, 580), text="数字车钥匙开通邀请"),
                                _node("Text", (920, 620, 1660, 680), text="来自崑友xxxx"),
                                _node("Button", (960, 1240, 1220, 1330), text="取消", clickable=True),
                                _node("Button", (1340, 1240, 1620, 1330), text="开通", clickable=True),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )


def test_modal_content_box_unmasks_tabbar_while_preserving_card_occlusion_and_touch_interception(ws):
    tree = _foldable_298_tree()
    car_node = tree.find_text("爱车")[0]
    bg_node = tree.nodes[2]
    assert bg_node.text == "背景被盖项"

    # Outer TabBar item below the centered card remains visible and locatable.
    vis_info = tree.visibility(car_node)
    assert vis_info["rect"] == (1264, 1715, 1319, 1747)
    assert vis_info["fraction"] == 1.0

    app = ws.app("qiankun_hm")
    loc = resolve("qk_tab_car", app.targets["qk_tab_car"], tree, None, app.dir)
    assert loc["status"] == "found"
    assert loc["point"] == [1291, 1731]

    # Background node inside the centered card rect is still occluded.
    assert tree.visible_rect(bg_node) is None

    # Full-screen clickable Dialog outer bounds still intercepts touch_target until dismissed.
    hit = tree.touch_target(1291, 1731)
    assert hit is not None and hit.type == "Dialog"


def test_modal_empty_multichild_and_large_dialogs_still_occlude_full_outer_box():
    # 1. Empty Dialog (0 children) -> falls back to outer box.
    t_empty = UiTree(_node("root", (0, 0, 1000, 1000), children=[
        _node("Text", (100, 850, 300, 900), text="底栏项"),
        _node("Dialog", (0, 0, 1000, 1000), clickable=True),
    ]))
    assert t_empty.visible_rect(t_empty.nodes[1]) is None

    # 2. Multi-child Dialog (2 direct visible children) -> falls back to outer box.
    t_multi = UiTree(_node("root", (0, 0, 1000, 1000), children=[
        _node("Text", (100, 850, 300, 900), text="底栏项"),
        _node("Dialog", (0, 0, 1000, 1000), clickable=True, children=[
            _node("Text", (300, 300, 500, 360), text="标题"),
            _node("Button", (300, 400, 500, 460), text="确定", clickable=True),
        ]),
    ]))
    assert t_multi.visible_rect(t_multi.nodes[1]) is None

    # 3. Single child >= 1/4 of outer area (50% area) -> falls back to outer box.
    t_large = UiTree(_node("root", (0, 0, 1000, 1000), children=[
        _node("Text", (100, 850, 300, 900), text="底栏项"),
        _node("Dialog", (0, 0, 1000, 1000), clickable=True, children=[
            _node("Column", (0, 0, 1000, 500), children=[
                _node("Text", (300, 200, 500, 260), text="大弹窗"),
            ]),
        ]),
    ]))
    assert t_large.visible_rect(t_large.nodes[1]) is None


def test_qk_digital_key_invite_registered_without_breaking_motor_hm_approvals(ws):
    app = ws.app("qiankun_hm")
    tree = _foldable_298_tree()
    page = app.states.identify(tree, app.bundle, True)
    matches = [i for i in (page.get("interrupts") or []) if i["id"] == "qk_digital_key_invite"]
    assert len(matches) == 1
    assert matches[0]["class"] == "benign" and matches[0]["review"] == "reviewed"

    spec = app.states.interrupts["qk_digital_key_invite"]
    assert spec["dismiss_text"] == "取消"
    assert "开通" not in spec["dismiss_text"]

    # Registering under qiankun_hm/states.json leaves approved motor_hm bindings intact.
    for cid in ("TC-HM-S01", "TC-MOTOR-HM-002"):
        info = C.review_status(ws, ws.load_case(cid))
        assert info["state"] == "approved" and info["valid"] is True
