"""Harmony `uitest dumpLayout` JSON 的归一化与查询。

XPath 语义与 hmdriver2 `_XPath._json2xml` 一致（type 作标签、attributes 作属性），
因此仓库 app/config/*.py 里的 xpath 可以原样复用。与原实现不同：空树、解析失败
会显式报告状态，不再返回 {} 让上层误以为"元素不存在"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_ATTR_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
# 这些容器会裁剪子节点：滚出列表边界的条目在树里仍有 bounds，但实际不可见，也点不到
CLIP_TYPES = {"List", "Scroll", "Grid", "WaterFlow", "Swiper", "Refresh"}
# 横向翻页容器不作为"上下滑动查找"的滚动容器
HORIZONTAL_SCROLL_TYPES = {"Swiper", "Tabs", "TabBar"}
# 模态层：ArkUI 的 Dialog 节点带全窗口蒙层，先序在它之前、被它盖住的页面节点用户看得到轮廓，但点击会被蒙层吞掉
MODAL_TYPES = {"Dialog"}


def intersect(a: Optional[tuple], b: Optional[tuple]) -> Optional[tuple]:
    if a is None or b is None:
        return None
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[2] > r[0] and r[3] > r[1] else None


def parse_bounds(raw: str) -> Optional[tuple]:
    m = _BOUNDS_RE.match(raw or "")
    return tuple(int(g) for g in m.groups()) if m else None


@dataclass
class Node:
    idx: int
    parent: Optional[int]
    depth: int
    type: str
    text: str
    id: str
    description: str
    bounds: Optional[tuple]
    clickable: bool
    enabled: bool
    selected: bool
    visible: bool
    scrollable: bool = False
    attrs: dict = field(repr=False, default_factory=dict)
    children: list = field(default_factory=list)

    @property
    def center(self) -> Optional[tuple]:
        if not self.bounds:
            return None
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def area(self) -> int:
        if not self.bounds:
            return 0
        x1, y1, x2, y2 = self.bounds
        return max(0, x2 - x1) * max(0, y2 - y1)

    def brief(self) -> dict:
        return {"idx": self.idx, "type": self.type, "text": self.text, "id": self.id,
                "bounds": list(self.bounds) if self.bounds else None,
                "clickable": self.clickable, "selected": self.selected}


def _flag(attrs: dict, key: str, default: bool) -> bool:
    v = str(attrs.get(key, "")).strip().lower()
    if v in ("true", "1"):
        return True
    if v in ("false", "0"):
        return False
    return default


class UiTree:
    def __init__(self, raw: Optional[dict]):
        self.raw = raw or {}
        self.nodes: list = []
        self._xml = None
        self._vis = None
        self._flatten(self.raw, None, 0)

    def _flatten(self, raw: dict, parent: Optional[int], depth: int) -> None:
        attrs = raw.get("attributes", {}) or {}
        b = parse_bounds(str(attrs.get("bounds", "")))
        node = Node(
            idx=len(self.nodes), parent=parent, depth=depth,
            type=str(attrs.get("type", "")), text=str(attrs.get("text", "")),
            id=str(attrs.get("id", "")), description=str(attrs.get("description", "")),
            bounds=b, clickable=_flag(attrs, "clickable", False), enabled=_flag(attrs, "enabled", True),
            selected=_flag(attrs, "selected", False),
            visible=_flag(attrs, "visible", True) and b is not None and (b[2] - b[0]) > 0 and (b[3] - b[1]) > 0,
            scrollable=_flag(attrs, "scrollable", False), attrs=attrs)
        self.nodes.append(node)
        if parent is not None:
            self.nodes[parent].children.append(node.idx)
        for child in raw.get("children", []) or []:
            self._flatten(child, node.idx, depth + 1)

    # ---- 状态 ----
    @property
    def status(self) -> str:
        """ok：有可用内容；empty：调用成功但只有占位根节点（例如目标窗口不在前台）。"""
        meaningful = [n for n in self.nodes[1:] if n.visible]
        return "ok" if meaningful else "empty"

    def shown(self, n: "Node") -> bool:
        """用户实际看得到：visible 且可见矩形非空（被列表/滚动容器裁到可视区外的条目不算）。"""
        return n.visible and self._visibility()[n.idx] is not None

    def visible_texts(self) -> list:
        seen, out = set(), []
        for n in self.nodes:
            for t in (n.text, n.description):
                t = t.strip()
                if t and self.shown(n) and t not in seen:
                    seen.add(t)
                    out.append(t)
        return out

    def screen_size(self) -> Optional[tuple]:
        best = max((n for n in self.nodes if n.bounds), key=lambda n: n.area, default=None)
        if not best or best.area == 0:
            return None
        return best.bounds[2], best.bounds[3]

    # ---- 查询 ----
    def find_text(self, text: str, exact: bool = True, include_clipped: bool = False) -> list:
        """include_clipped=True 时也返回被列表裁到可视区外的节点（定位用它判断"滑动后可能出现"）；
        断言与状态识别用默认值，只认用户看得到的节点。"""
        ok = (lambda n: n.visible) if include_clipped else self.shown
        return [n for n in self.nodes if ok(n) and
                ((n.text == text or n.description == text) if exact else (text in n.text or text in n.description))]

    def find_id(self, value: str, include_clipped: bool = False) -> list:
        ok = (lambda n: n.visible) if include_clipped else self.shown
        return [n for n in self.nodes if ok(n) and value in (n.id, n.attrs.get("key", ""))]

    def xpath(self, expr: str) -> list:
        """返回匹配节点；表达式错误会抛出 lxml 异常，由调用方记录为 error 而不是"未找到"。"""
        root = self._xml_root()
        result = root.xpath(expr)
        out = []
        for el in result if isinstance(result, list) else []:
            if hasattr(el, "get") and el.get("_vpd_idx") is not None:
                out.append(self.nodes[int(el.get("_vpd_idx"))])
        return out

    def _xml_root(self):
        if self._xml is None:
            from lxml import etree
            counter = [0]

            def build(raw: dict):
                attrs = {k: str(v) for k, v in (raw.get("attributes", {}) or {}).items() if _ATTR_KEY_RE.match(str(k))}
                attrs["_vpd_idx"] = str(counter[0])
                counter[0] += 1
                el = etree.Element(attrs.get("type") or "orgRoot", attrib=attrs)
                for child in raw.get("children", []) or []:
                    el.append(build(child))
                return el
            self._xml = build(self.raw)
        return self._xml

    # ---- 窗口与可见区域（全部相对当前 UI 树现算，不记像素位置）----
    def window_rect(self) -> Optional[tuple]:
        best = max((n for n in self.nodes if n.bounds), key=lambda n: n.area, default=None)
        return best.bounds if best and best.area else None

    def _visibility(self) -> list:
        """每个节点的可见矩形 = 自身 bounds ∩ 窗口 ∩ 祖先中裁剪型容器（List/Scroll 等）的可见区域。"""
        if self._vis is None:
            win = self.window_rect()
            vis, clip = [None] * len(self.nodes), [None] * len(self.nodes)
            for n in self.nodes:  # nodes 为先序，父节点先于子节点
                inherited = win if n.parent is None else clip[n.parent]
                vis[n.idx] = intersect(n.bounds, inherited) if n.visible else None
                clip[n.idx] = (vis[n.idx] if n.type in CLIP_TYPES or n.scrollable else inherited)
            for m in (n for n in self.nodes if n.type in MODAL_TYPES and vis[n.idx]):
                anc = set(self._ancestor_idx(m))
                box = vis[m.idx]
                for n in self.nodes[:m.idx]:
                    r = vis[n.idx]
                    if r and n.idx not in anc and box[0] <= (r[0] + r[2]) // 2 < box[2] \
                            and box[1] <= (r[1] + r[3]) // 2 < box[3]:
                        vis[n.idx] = None  # 被模态层盖住：不算可见、不可定位、状态签名也看不到
            self._vis = vis
        return self._vis

    def _ancestor_idx(self, node: Node) -> list:
        out, cur = [], node.parent
        while cur is not None:
            out.append(cur)
            cur = self.nodes[cur].parent
        return out

    def modal_layers(self) -> list:
        """当前盖在页面上的模态层（Dialog），先序靠后的在上。"""
        vis = self._visibility()
        return [n for n in self.nodes if n.type in MODAL_TYPES and vis[n.idx]]

    def visible_rect(self, node: Node) -> Optional[tuple]:
        return self._visibility()[node.idx]

    def visibility(self, node: Node) -> dict:
        r = self.visible_rect(node)
        frac = round(((r[2] - r[0]) * (r[3] - r[1])) / node.area, 3) if (r and node.area) else 0.0
        return {"rect": r, "fraction": frac, "center": ((r[0] + r[2]) // 2, (r[1] + r[3]) // 2) if r else None}

    def node_at(self, x: int, y: int) -> Optional[Node]:
        """点击点实际落在哪个节点：只看可见矩形，被列表裁掉的条目不算（否则会把底部导航上的点击
        误判为列表项）。"""
        vis = self._visibility()
        hits = [n for n in self.nodes if vis[n.idx] and vis[n.idx][0] <= x < vis[n.idx][2]
                and vis[n.idx][1] <= y < vis[n.idx][3]]
        # 先序靠后 = 绘制在上层（后兄弟盖住前兄弟，子节点盖住父节点）。弹层/底部面板是后加入的兄弟，
        # 按深度取会选中面板下面更深的页面节点（真机上登录面板下的「收藏」就这样被误认成点击对象）
        return max(hits, key=lambda n: n.idx, default=None)

    def touch_target(self, x: int, y: int) -> Optional[Node]:
        """触摸实际交给谁：落点上最上层的可点击节点（透明、不可点击的浮层容器会被穿透）。"""
        vis = self._visibility()
        hits = [n for n in self.nodes if n.clickable and vis[n.idx] and vis[n.idx][0] <= x < vis[n.idx][2]
                and vis[n.idx][1] <= y < vis[n.idx][3]]
        return max(hits, key=lambda n: n.idx, default=None)

    def touch_check(self, x: int, y: int, scope: Node) -> dict:
        """选点和下发共用：无接收节点可穿透；目标子树及其可点击祖先属于同一触摸路径。"""
        actual = self.touch_target(x, y)
        if actual is None:
            allowed, basis = True, "no_clickable_receiver"
        elif self.is_within(actual, scope):
            allowed, basis = True, "receiver_in_target"
        elif self.is_within(scope, actual):
            allowed, basis = True, "receiver_ancestor"
        else:
            allowed, basis = False, "unrelated_receiver"
        return {"allowed": allowed, "basis": basis, "receiver": actual.brief() if actual else None,
                "scope": scope.brief()}

    def is_within(self, node: Node, scope: Node) -> bool:
        cur = node.idx
        while cur is not None:
            if cur == scope.idx:
                return True
            cur = self.nodes[cur].parent
        return False

    def in_scope(self, node: Node, scope: Optional[list]) -> bool:
        return scope is None or any(self.is_within(node, s) for s in scope)

    def texts_in(self, scope: Optional[list]) -> list:
        if scope is None:
            return self.visible_texts()
        return [t for n in self.nodes if self.shown(n) and self.in_scope(n, scope)
                for t in (n.text.strip(), n.description.strip()) if t]

    def scroll_container(self, scope: Optional[list] = None) -> Optional[Node]:
        """上下滑动查找用的容器：范围内可见面积最大的可滚动节点（排除横向翻页容器）。"""
        cands = [n for n in self.nodes if n.scrollable and n.type not in HORIZONTAL_SCROLL_TYPES
                 and self.visible_rect(n) and self.in_scope(n, scope)]

        def area(n):
            r = self.visible_rect(n)
            return (r[2] - r[0]) * (r[3] - r[1])
        return max(cands, key=area, default=None)

    def fingerprint(self) -> str:
        """可见内容指纹：滑动前后相同说明已到底或滑动没有生效。"""
        from .util import short_hash
        vis = self._visibility()
        return short_hash([(n.text, vis[n.idx]) for n in self.nodes if vis[n.idx] and n.text], 12)

    def ancestors(self, node: Node) -> list:
        out, cur = [], node.parent
        while cur is not None:
            out.append(self.nodes[cur])
            cur = self.nodes[cur].parent
        return out

    def clickable_container(self, node: Node) -> Node:
        for n in [node] + self.ancestors(node):
            if n.clickable:
                return n
        return node

    def enabled_state(self, node: Node, rule: Optional[dict] = None) -> tuple:
        """控件是否可用 → (enabled | None, 证据)。节点或其可点击容器任一 enabled=false 即不可用。
        有的 APP 置灰只改透明度（原工程乾崑判断「开始」可用用的是 Button opacity=1）：目标可声明
        enabled_by={"attr": "opacity", "min": 1.0}，两种依据都记入证据；声明的属性缺失或不是数值时返回 None（不可判定）。"""
        holder = self.clickable_container(node)
        enabled = bool(node.enabled and holder.enabled)
        ev = {"enabled_attr": enabled}
        if rule:
            vals = [x.attrs.get(rule["attr"]) for x in (node, holder) if x.attrs.get(rule["attr"]) not in (None, "")]
            if not vals:
                return None, {**ev, "reason": f"节点没有 {rule['attr']} 属性，无法按声明的依据判断可用性"}
            try:
                ok = all(float(v) >= float(rule.get("min", 1.0)) - 1e-6 for v in vals)
            except ValueError:
                return None, {**ev, "reason": f"{rule['attr']} 不是数值：{vals}"}
            ev[rule["attr"]] = vals
            enabled = enabled and ok
        ev["enabled"] = enabled
        return enabled, ev

    def subtree_texts(self, node: Node) -> list:
        out, stack = [], [node.idx]
        while stack:  # 先序遍历，保持与界面阅读顺序一致
            n = self.nodes[stack.pop()]
            out.extend(t for t in (n.text, n.description) if t.strip())
            stack.extend(reversed(n.children))
        return out

    def region_box(self, region: list) -> Optional[tuple]:
        """region 是相对当前窗口的比例 [x1, y1, x2, y2]，不是像素。"""
        win = self.window_rect()
        if not win or not region:
            return None
        x0, y0, w, h = win[0], win[1], win[2] - win[0], win[3] - win[1]
        return (int(x0 + region[0] * w), int(y0 + region[1] * h), int(x0 + region[2] * w), int(y0 + region[3] * h))


def in_box(point: tuple, box: Optional[tuple]) -> bool:
    if box is None:
        return True
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]
