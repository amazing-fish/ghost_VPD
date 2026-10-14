"""页面状态识别：基于已声明的状态签名做确定性匹配。

签名属于"定位/识别经验"，不是产品规则；未命中任何声明时返回 unknown，并给出文本签名供 Agent
结合截图判断。多个状态同时命中返回 ambiguous，不猜。
"""
from __future__ import annotations

import re
from typing import Optional

from .tree import MODAL_TYPES, UiTree
from .util import short_hash


def _texts_match(spec: dict, tree: UiTree, scope: Optional[list] = None, owner_modal=None) -> bool:
    """scope=None 在整个窗口上匹配；给定分栏节点时只看该栏内的文本（选中态仍按整棵树的容器判断）。"""
    def owned(node):
        if owner_modal is None:
            return True
        nearest = next((n for n in [node] + tree.ancestors(node) if n.type in MODAL_TYPES), None)
        return nearest is not None and nearest.idx == owner_modal.idx

    texts = (tree.texts_in(scope) if owner_modal is None else
             [t for n in tree.nodes if tree.shown(n) and tree.in_scope(n, scope) and owned(n)
              for t in (n.text.strip(), n.description.strip()) if t])
    for t in spec.get("require_text", []):
        if t not in texts:
            return False
    if spec.get("require_any_text") and not any(t in texts for t in spec["require_any_text"]):
        return False
    for pat in spec.get("require_text_pattern", []):  # 文案带变量时用正则，例如「车架号 xxxxxx(后6位)」
        if not any(re.search(pat, x) for x in texts):
            return False
    for t in spec.get("forbid_text", []):
        if t in texts:
            return False
    for pat in spec.get("forbid_text_pattern", []):
        if any(re.search(pat, x) for x in texts):
            return False
    for t in spec.get("require_selected_text", []):
        if not any(is_selected(tree, n) for n in tree.find_text(t) if tree.in_scope(n, scope) and owned(n)):
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

    def identify(self, tree: Optional[UiTree], foreground: Optional[str], alive: Optional[bool],
                 layout: Optional[dict] = None, foreground_error: Optional[str] = None) -> dict:
        """单栏：唯一命中即为当前状态，多个命中为 ambiguous。
        分栏（折叠屏展开等，由 layout.panes 给出）：每栏单独匹配，整窗命中与各栏唯一命中合起来是
        active（例如左栏「我的」列表 + 右栏「设置」详情 → my_tab+settings）；只有同一栏内多个命中才是 ambiguous。"""
        if foreground is None and foreground_error:
            # 查询失败（例如 hdc 通道重建）≠ 不在 APP 内；不能据此判断崩溃或页面
            return {"state": "undetermined", "reason": f"前台应用查询失败（{foreground_error}）",
                    "observation_error": foreground_error, "interrupts": [], "candidates": [], "active": []}
        if foreground != self.bundle:
            # 是否疑似崩溃取决于动作前是否在 APP 内，由执行层结合前后观测判断
            return {"state": "out_of_app", "foreground": foreground, "process_alive": alive,
                    "interrupts": [], "candidates": [], "active": []}
        if tree is None or tree.status != "ok":
            return {"state": "undetermined", "reason": f"UI 树不可用（{tree.status if tree else 'missing'}）",
                    "interrupts": [], "candidates": [], "active": []}
        interrupts = [{"id": iid, "class": spec.get("class", "unknown"), "review": spec.get("review", "pending")}
                      for iid, spec in self.interrupts.items() if _texts_match(spec, tree)]
        cands = [sid for sid, spec in self.states.items() if _texts_match(spec, tree)]
        out = {"candidates": cands, "interrupts": interrupts, "signature": signature(tree)}
        dialogs = []
        for m in tree.modal_layers():
            texts = tree.texts_in([m])
            hit = next((iid for iid, spec in self.interrupts.items() if _texts_match(spec, tree, [m])), None)
            dialogs.append({"texts": texts[:20], "interrupt": hit})
        if dialogs:
            # 弹窗按结构识别（Dialog 节点），不只靠文案：文案变体没登记时也知道"页面被弹窗挡住"，并把弹窗文字带进证据
            out["dialogs"] = dialogs
        panes = (layout or {}).get("panes") or []
        if len(panes) < 2:
            active = cands if len(cands) == 1 else []
            state = cands[0] if len(cands) == 1 else ("ambiguous" if cands else "unknown")
        else:
            per = []
            for p in panes:
                node = tree.nodes[p["node_idx"]]
                per.append({"pane": p["index"], "node_idx": p["node_idx"],
                            "candidates": [sid for sid, spec in self.states.items()
                                                               if _texts_match(spec, tree, [node])]})
            out["panes"] = per
            if any(len(x["candidates"]) > 1 for x in per) or (len(cands) > 1 and not any(x["candidates"] for x in per)):
                active, state = [], "ambiguous"
            else:
                active = list(dict.fromkeys(cands + [x["candidates"][0] for x in per if x["candidates"]]))
                state = "+".join(active) if active else "unknown"
        out.update(state=state, active=active)
        if state == "unknown":
            out["hint"] = "未命中已声明状态：结合截图判断页面语义，必要时提议新增状态签名（需评审）"
        return out

    def is_state(self, ident: dict, expect: str) -> bool:
        if expect == "@in_app":
            return ident["state"] not in ("out_of_app", "undetermined")
        return ident["state"] == expect or expect in (ident.get("active") or [])

    def deny_for(self, state_id: Optional[str]) -> list:
        """页面级规则：deny_text 为纯禁止；effect_text {正则: 副作用类} 把本页上的通用文字（如确认弹窗的「确定」）
        归到某个副作用类，按该类的授权规则处理（例如点「开始」后的「确定」才真正让车动）。"""
        spec = self.states.get(state_id or "", {})
        return list(spec.get("deny_text", [])) + [{"pattern": p, "class": c} for p, c in spec.get("effect_text", {}).items()]

    def deny_for_page(self, page: dict) -> list:
        """歧义不解除限制：取全窗、各栏候选与已识别状态的限制并集，不制造 active 状态。"""
        ids = list(page.get("active") or []) + list(page.get("candidates") or [])
        for pane in page.get("panes") or []:
            ids.extend(pane.get("candidates") or [])
        ids = list(dict.fromkeys(s for s in ids + [page.get("state")] if isinstance(s, str)))
        return [t for sid in ids for t in self.deny_for(sid)]

    def confirmation_sources(self, tree: UiTree | None, target_ref=None, target=None, page=None) -> list:
        """只认当前同一弹窗的完整登记签名及已评审关闭方式，不信目标自填的 review/source。"""
        if tree is None or tree.status != "ok":
            return []
        out = []
        page = page or {}
        panes = page.get("panes") or []
        scopes = [(p["node_idx"], p.get("candidates") or []) for p in panes
                  if isinstance(p.get("node_idx"), int) and 0 <= p["node_idx"] < len(tree.nodes)]
        if not panes:
            scopes = [(None, page.get("candidates") or page.get("active") or [page.get("state")])]
        for scope_idx, candidates in scopes:
            candidates = list(dict.fromkeys(sid for sid in candidates if isinstance(sid, str)))
            if candidates:
                # 空规则也保留：一个候选知道按钮效果，不能代表另一个未知候选。
                out.append({"kind": "state_confirmation_candidates", "scope_idx": scope_idx,
                            "candidates": [{"state": sid, "rules": [
                                {"pattern": pattern, "class": effect}
                                for pattern, effect in self.states.get(sid, {}).get("effect_text", {}).items()]}
                                for sid in candidates]})
        for modal in tree.modal_layers():
            matched = [(iid, spec) for iid, spec in self.interrupts.items()
                       if _texts_match(spec, tree, [modal], owner_modal=modal)]
            if any(spec.get("class") != "benign" or spec.get("review") != "reviewed" for _, spec in matched):
                continue
            for iid, spec in matched:
                if not any(spec.get(k) for k in ("require_text", "require_any_text", "require_text_pattern",
                                                "require_selected_text")):
                    continue
                dismiss = spec.get("dismiss_target")
                matches = (isinstance(dismiss, str) and dismiss == target_ref) or (
                    isinstance(dismiss, dict) and dismiss == target)
                if matches or spec.get("dismiss_text"):
                    out.append({"kind": "reviewed_benign_interrupt", "interrupt": iid, "modal_idx": modal.idx,
                                "button_text": None if matches else spec["dismiss_text"]})
        return out
