"""窗口形态：按 vp 宽度分档、识别左右分栏、条件匹配。

原则：
- 用例和目标里不写像素坐标。点击点每次都从当前 UI 树（或模板匹配）现算，滑动按可滚动容器的边界现算。
- 直板/折叠是设备属性，只作记录；布局决策看的是"窗口宽度（vp = px / 像素密度）落在哪一档"，
  因为同一台折叠屏折叠时是窄屏、展开时是宽屏，直板横屏也可能变宽。
- 是否分栏以 UI 树里实际出现的并排大容器为准，不从宽度阈值推断：阈值只是分档标签，
  被测 APP 在哪个宽度切换成分栏由它自己的实现决定，需要在真机上确认。
"""
from __future__ import annotations

from typing import Optional

from .tree import UiTree

DEFAULTS = {
    "breakpoints_vp": [["xs", 0], ["sm", 320], ["md", 600], ["lg", 840], ["xl", 1440]],
    "wide_classes": ["md", "lg", "xl"],
    "pane": {"min_width_vp": 200, "min_width_fraction": 0.25, "min_height_fraction": 0.5,
             "coverage": 0.8, "max_overlap_px": 4},
    "min_visible_fraction": 0.5,
}
WHEN_KEYS = ("width_class", "wide", "orientation", "panes", "fold_status", "device_form")
REQUIREMENT_KEYS = ("width_class", "wide", "orientation", "fold_status", "device_form")


def config(data: Optional[dict]) -> dict:
    cfg = {**DEFAULTS, **(data or {})}
    cfg["pane"] = {**DEFAULTS["pane"], **(data or {}).get("pane", {})}
    return cfg


def classify_width(width_vp: Optional[float], cfg: dict) -> Optional[str]:
    if width_vp is None:
        return None
    cls = None
    for name, lower in cfg["breakpoints_vp"]:
        if width_vp >= lower:
            cls = name
    return cls


def detect_panes(tree: UiTree, density: Optional[float], cfg: dict) -> list:
    """找最浅的一组并排大容器：各自高度 ≥ 窗口一半、宽度 ≥ 阈值、互不重叠、合计覆盖父容器大部分宽度。
    底部导航、横向轮播、侧边窄导航栏都不会被当成分栏。返回按 x 排序的栏，不猜测"列表/详情"语义。"""
    win = tree.window_rect()
    if not win:
        return []
    ww, wh = win[2] - win[0], win[3] - win[1]
    p = cfg["pane"]
    min_w = max(p["min_width_fraction"] * ww, (p["min_width_vp"] * density) if density else 0)
    queue = [tree.nodes[0]] if tree.nodes else []
    while queue:
        node = queue.pop(0)
        kids = [tree.nodes[c] for c in node.children]
        big = []
        for k in kids:
            r = tree.visible_rect(k)
            if r and (r[2] - r[0]) >= min_w and (r[3] - r[1]) >= p["min_height_fraction"] * wh:
                big.append((k, r))
        if len(big) >= 2:
            big.sort(key=lambda kr: kr[1][0])
            overlap = any(a[1][2] - b[1][0] > p["max_overlap_px"] for a, b in zip(big, big[1:]))
            parent = tree.visible_rect(node) or win
            covered = sum(r[2] - r[0] for _, r in big)
            if not overlap and covered >= p["coverage"] * (parent[2] - parent[0]):
                return [{"index": i, "node_idx": k.idx, "type": k.type, "bounds": list(r),
                         "width_vp": round((r[2] - r[0]) / density, 1) if density else None}
                        for i, (k, r) in enumerate(big)]
        queue.extend(kids)
    return []


def profile(tree: Optional[UiTree], display: Optional[dict], cfg: dict) -> dict:
    """一次观测的窗口形态。窗口尺寸优先取 UI 树（APP 实际窗口，分屏/悬浮窗时小于屏幕），否则取显示参数。"""
    display = display or {}
    win = tree.window_rect() if (tree is not None and tree.status == "ok") else None
    if win:
        w, h, source = win[2] - win[0], win[3] - win[1], "ui_tree"
    elif display.get("width_px"):
        w, h, source = display["width_px"], display["height_px"], "display"
    else:
        w = h = source = None
    density = display.get("density")
    wvp = round(w / density, 1) if (w and density) else None
    hvp = round(h / density, 1) if (h and density) else None
    wc = classify_width(wvp, cfg)
    panes = detect_panes(tree, density, cfg) if win else []
    return {"status": "ok" if wc else ("no_density" if w else "unknown"),
            "window_px": [w, h] if w else None, "window_rect": list(win) if win else None, "source": source,
            "density": density, "density_source": display.get("source"), "width_vp": wvp, "height_vp": hvp,
            "width_class": wc, "wide": (wc in cfg["wide_classes"]) if wc else None,
            "orientation": ("landscape" if w > h else "portrait") if w and h else None,
            "fold_status": display.get("fold_status"), "device_form": display.get("device_form"),
            "panes": panes, "pane_count": max(1, len(panes)) if win else None,
            "min_visible_fraction": cfg["min_visible_fraction"]}


def key(layout: Optional[dict]) -> Optional[str]:
    """经验按窗口形态分组的键；分栏数不进键（同一形态下不同页面可能分栏也可能不分）。"""
    if not layout or not layout.get("width_class") or not layout.get("orientation"):
        return None
    return f"{layout['width_class']}/{layout['orientation']}"


def when_matches(when: Optional[dict], layout: Optional[dict]) -> Optional[bool]:
    """策略/目标上的形态条件。True/False；缺少判断所需的形态信息时返回 None（由调用方记为跳过，不猜）。"""
    if not when:
        return True
    unknown = [k for k in when if k not in WHEN_KEYS]
    if unknown:
        raise ValueError(f"未知的 when 条件 {unknown}，可用 {list(WHEN_KEYS)}")
    layout = layout or {}
    undecided = False
    for k, want in when.items():
        actual = layout.get("pane_count") if k == "panes" else layout.get(k)
        if actual is None:
            undecided = True
            continue
        ok = (actual in want) if isinstance(want, list) else (actual == want)
        if not ok:
            return False
    return None if undecided else True


def check_requirements(req: dict, layout: Optional[dict]) -> list:
    """用例级形态要求（启动前可判定的项）。返回未满足项。"""
    unmet = []
    for k, want in (req or {}).items():
        actual = (layout or {}).get(k)
        ok = actual is not None and ((actual in want) if isinstance(want, list) else actual == want)
        if not ok:
            unmet.append({"key": f"layout.{k}", "required": want, "actual": actual})
    return unmet


def resolve_scope(within, tree: UiTree, layout: Optional[dict]) -> Optional[list]:
    """within 限定查找范围：{"type": "TabBar"} / {"id": "..."} / {"pane": 0 | -1 | "first" | "last"}。
    返回容器节点列表；None 表示不限定。单栏窗口里的 pane 引用指整个窗口，便于同一目标跨形态复用。"""
    if not within:
        return None
    if "type" in within:
        return [n for n in tree.nodes if n.type == within["type"] and tree.visible_rect(n)]
    if "id" in within:
        return [n for n in tree.nodes if within["id"] in (n.id, n.attrs.get("key", "")) and tree.visible_rect(n)]
    if "pane" in within:
        panes = (layout or {}).get("panes") or []
        if not panes:
            return [tree.nodes[0]]
        ref = {"first": 0, "last": -1}.get(within["pane"], within["pane"])
        try:
            return [tree.nodes[panes[int(ref)]["node_idx"]]]
        except (IndexError, ValueError):
            return []
    raise ValueError(f"未知的 within 限定 {within}，可用 type / id / pane")
