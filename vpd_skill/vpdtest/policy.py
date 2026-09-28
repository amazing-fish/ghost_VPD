"""运行边界的确定性约束。模型只能"提议"动作，是否执行由这里判定。

规则来源：data/policy.json。policy 本身不属于经验，不会被 RSI 候选修改。
"""
from __future__ import annotations

import re
from typing import Optional

from .tree import UiTree
from .util import sha256_obj

VALID_BASIS = ("tree", "template", "point")


class Policy:
    def __init__(self, data: dict):
        self.data = data
        self.version = data.get("version", "unknown")
        self.hash = sha256_obj(data)[:12]
        self.allowed_packages = set(data.get("allowed_packages", []))
        self.deny = [(re.compile(d["pattern"]), d["reason"], d.get("class", "denied"))
                     for d in data.get("deny_text", [])]
        self.denied_side_effects = set(data.get("denied_side_effects", []))
        self.deny_node_types = set(data.get("deny_node_types", []))
        self.allow_point_without_tree = bool(data.get("allow_point_tap_without_tree", False))
        self.allow_input = bool(data.get("allow_text_input", False))
        self.max_actions_per_run = int(data.get("max_actions_per_run", 60))
        self.max_wait_s = float(data.get("max_wait_s", 30))
        self.privacy = data.get("privacy", {})

    def package_allowed(self, pkg: Optional[str]) -> bool:
        return bool(pkg) and pkg in self.allowed_packages

    def screenshot_allowed(self, fg_pkg: Optional[str]) -> bool:
        if not self.privacy.get("screenshot_only_when_target_foreground", True):
            return True
        return self.package_allowed(fg_pkg)

    def match_deny(self, texts: list, extra: Optional[list] = None) -> Optional[dict]:
        rules = list(self.deny) + [(re.compile(p), "页面级禁止", "state_deny") for p in (extra or [])]
        for text in texts:
            for rx, reason, cls in rules:
                if rx.search(text):
                    return {"text": text, "pattern": rx.pattern, "reason": reason, "class": cls}
        return None

    def check_tap(self, tree: Optional[UiTree], point: tuple, basis: str, target: Optional[dict] = None,
                  state_deny: Optional[list] = None) -> dict:
        """判定一次点击。返回 {allowed, reason, label, deny}；不会抛异常。"""
        target = target or {}
        if basis not in VALID_BASIS:
            return {"allowed": False, "reason": f"未知定位依据 {basis}", "label": None, "deny": None}
        effect = target.get("side_effect", "navigation")
        if effect in self.denied_side_effects:
            return {"allowed": False, "reason": f"目标副作用 {effect} 在 policy 中被禁止", "label": None, "deny": None}
        if target.get("forbid_tap"):
            return {"allowed": False, "reason": target.get("forbid_reason", "目标声明禁止点击"), "label": None, "deny": None}

        tree_ok = tree is not None and tree.status == "ok"
        if not tree_ok:
            if basis == "template":
                return {"allowed": True, "reason": "无可用 UI 树，按确定性模板匹配结果执行", "label": None, "deny": None}
            if basis == "point" and self.allow_point_without_tree:
                return {"allowed": True, "reason": "policy 允许无树坐标点击", "label": None, "deny": None}
            return {"allowed": False, "reason": "无可用 UI 树，禁止盲点坐标；请先模板定位或重新观察", "label": None,
                    "deny": None}

        node = tree.node_at(*point)
        if node is None:
            return {"allowed": False, "reason": "点击点不在任何可见节点内", "label": None, "deny": None}
        container = tree.clickable_container(node)
        types = {n.type for n in [node, container] + tree.ancestors(node)[:2]}
        hit_type = types & self.deny_node_types
        if hit_type and not target.get("allow_node_type"):
            return {"allowed": False, "reason": f"控件类型 {sorted(hit_type)} 会直接改变设置，需目标显式声明 allow_node_type",
                    "label": None, "deny": {"class": "setting_change", "types": sorted(hit_type)}}
        texts = tree.subtree_texts(container) + [node.text, node.description]
        texts = [t for t in texts if t]
        hit = self.match_deny(texts, state_deny)
        label = (texts[0] if texts else "")[:40]
        if hit:
            return {"allowed": False, "reason": f"命中禁止文本「{hit['text']}」：{hit['reason']}", "label": label,
                    "deny": hit}
        if basis == "point" and not texts:
            return {"allowed": False, "reason": "坐标落在无文本标识的控件上，无法确认副作用", "label": label, "deny": None}
        return {"allowed": True, "reason": "通过", "label": label, "deny": None}

    def check_input(self, text: str, target: Optional[dict]) -> dict:
        if not self.allow_input and not (target or {}).get("allow_input"):
            return {"allowed": False, "reason": "policy 未开启文本输入；需在目标上声明 allow_input"}
        if (target or {}).get("secret"):
            return {"allowed": False, "reason": "v0.1 不向敏感输入框写入内容"}
        return {"allowed": True, "reason": "通过"}
