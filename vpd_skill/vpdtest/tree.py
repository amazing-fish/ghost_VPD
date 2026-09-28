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
            attrs=attrs)
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

    def visible_texts(self) -> list:
        seen, out = set(), []
        for n in self.nodes:
            for t in (n.text, n.description):
                t = t.strip()
                if t and n.visible and t not in seen:
                    seen.add(t)
                    out.append(t)
        return out

    def screen_size(self) -> Optional[tuple]:
        best = max((n for n in self.nodes if n.bounds), key=lambda n: n.area, default=None)
        if not best or best.area == 0:
            return None
        return best.bounds[2], best.bounds[3]

    # ---- 查询 ----
    def find_text(self, text: str, exact: bool = True) -> list:
        return [n for n in self.nodes if n.visible and
                ((n.text == text or n.description == text) if exact else (text in n.text or text in n.description))]

    def find_id(self, value: str) -> list:
        return [n for n in self.nodes if n.visible and value in (n.id, n.attrs.get("key", ""))]

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

    def node_at(self, x: int, y: int) -> Optional[Node]:
        hits = [n for n in self.nodes if n.visible and n.bounds and
                n.bounds[0] <= x < n.bounds[2] and n.bounds[1] <= y < n.bounds[3]]
        return max(hits, key=lambda n: (n.depth, -n.area), default=None)

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

    def subtree_texts(self, node: Node) -> list:
        out, stack = [], [node.idx]
        while stack:  # 先序遍历，保持与界面阅读顺序一致
            n = self.nodes[stack.pop()]
            out.extend(t for t in (n.text, n.description) if t.strip())
            stack.extend(reversed(n.children))
        return out

    def region_box(self, region: list) -> Optional[tuple]:
        size = self.screen_size()
        if not size or not region:
            return None
        w, h = size
        return int(region[0] * w), int(region[1] * h), int(region[2] * w), int(region[3] * h)


def in_box(point: tuple, box: Optional[tuple]) -> bool:
    if box is None:
        return True
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]
