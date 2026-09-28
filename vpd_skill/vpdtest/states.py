"""页面状态识别：基于已声明的状态签名做确定性匹配。

签名属于"定位/识别经验"，不是产品规则；未命中任何声明时返回 unknown，并给出文本签名供 Agent
结合截图判断。多个状态同时命中返回 ambiguous，不猜。
"""
from __future__ import annotations

import re
from typing import Optional

from .tree import UiTree
from .util import short_hash


def _texts_match(spec: dict, tree: UiTree) -> bool:
    texts = tree.visible_texts()
    for t in spec.get("require_text", []):
        if t not in texts:
            return False
    if spec.get("require_any_text") and not any(t in texts for t in spec["require_any_text"]):
        return False
    for t in spec.get("forbid_text", []):
        if t in texts:
            return False
    for t in spec.get("require_selected_text", []):
        if not any(is_selected(tree, n) for n in tree.find_text(t)):
            return False
    return True


def is_selected(tree: UiTree, node) -> bool:
    """选中态看节点本身或其最近的可点击容器。
    真机校准（2026-09-29，鸿蒙智行 3.0.3.200）：底部导航的 selected=true 在
    TabBar > Column(clickable) 上，文字节点及其两层父节点都是 false。"""
    return node.selected or tree.clickable_container(node).selected


def signature(tree: Optional[UiTree]) -> Optional[str]:
    if tree is None or tree.status != "ok":
        return None
    texts = sorted({re.sub(r"\d+", "#", t) for t in tree.visible_texts()})
    return "sig-" + short_hash(texts, 10)


class StateModel:
    def __init__(self, data: dict, bundle: str):
        self.data = data
        self.bundle = bundle
        self.states = data.get("states", {})
        self.interrupts = data.get("interrupts", {})
        self.recipes = data.get("recipes", {})

    def identify(self, tree: Optional[UiTree], foreground: Optional[str], alive: Optional[bool]) -> dict:
        if foreground != self.bundle:
            # 是否疑似崩溃取决于动作前是否在 APP 内，由执行层结合前后观测判断
            return {"state": "out_of_app", "foreground": foreground, "process_alive": alive,
                    "interrupts": [], "candidates": []}
        if tree is None or tree.status != "ok":
            return {"state": "undetermined", "reason": f"UI 树不可用（{tree.status if tree else 'missing'}）",
                    "interrupts": [], "candidates": []}
        interrupts = [{"id": iid, "class": spec.get("class", "unknown"), "review": spec.get("review", "pending")}
                      for iid, spec in self.interrupts.items() if _texts_match(spec, tree)]
        cands = [sid for sid, spec in self.states.items() if _texts_match(spec, tree)]
        state = cands[0] if len(cands) == 1 else ("ambiguous" if cands else "unknown")
        out = {"state": state, "candidates": cands, "interrupts": interrupts, "signature": signature(tree)}
        if state == "unknown":
            out["hint"] = "未命中已声明状态：结合截图判断页面语义，必要时提议新增状态签名（需评审）"
        return out

    def is_state(self, ident: dict, expect: str) -> bool:
        if expect == "@in_app":
            return ident["state"] not in ("out_of_app", "undetermined")
        return ident["state"] == expect

    def deny_for(self, state_id: Optional[str]) -> list:
        return list(self.states.get(state_id or "", {}).get("deny_text", []))
