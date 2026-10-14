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
        self.gated_side_effects = set(data.get("gated_side_effects", {}))
        # supervised：车辆运动/车控。不是禁止，而是"授权 + 在场监护人 (+ 安全停止声明)"的更高门槛
        self.supervised_side_effects = set(data.get("supervised_side_effects", {}))
        self.authorizable = self.gated_side_effects | self.supervised_side_effects
        self.supervision = data.get("supervision", {})
        self.safe_stop_rx = re.compile(data["safe_stop_text"]) if data.get("safe_stop_text") else None
        self.confirmation_rx = re.compile(data.get("generic_confirmation_text") or r"^(?:确定|确认|OK)$", re.IGNORECASE)
        self.confirmation_contexts = [(re.compile(c["pattern"]), c["class"])
                                     for c in data.get("confirmation_contexts", [])]
        self.deny_node_types = set(data.get("deny_node_types", []))
        self.allow_point_without_tree = bool(data.get("allow_point_tap_without_tree", False))
        self.allow_input = bool(data.get("allow_text_input", False))
        self.max_actions_per_run = int(data.get("max_actions_per_run", 60))
        self.max_wait_s = float(data.get("max_wait_s", 30))

    def package_allowed(self, pkg: Optional[str]) -> bool:
        return bool(pkg) and pkg in self.allowed_packages

    def screenshot_allowed(self, fg_pkg: Optional[str]) -> bool:
        return self.package_allowed(fg_pkg)

    def match_deny(self, texts: list, extra: Optional[list] = None) -> Optional[dict]:
        hits = self.match_all(texts, extra)
        return hits[0] if hits else None

    def match_all(self, texts: list, extra: Optional[list] = None) -> list:
        rules = list(self.deny)
        for p in extra or []:
            if isinstance(p, dict):  # 页面级副作用归类：按该类的授权规则处理
                rules.append((re.compile(p["pattern"]), f"本页面上该控件属于 {p['class']}", p["class"]))
            else:
                rules.append((re.compile(p), "页面级禁止", "state_deny"))
        return [{"text": text, "pattern": rx.pattern, "reason": reason, "class": cls}
                for text in texts for rx, reason, cls in rules if rx.search(text.strip())]

    def check_grants(self, granted) -> None:
        """命令行 --authorize 只接受可授予的副作用名；拼错或硬禁止类直接报错，不静默忽略。"""
        bad = sorted(set(granted or ()) - self.authorizable)
        if bad:
            forbidden = [g for g in bad if g in self.denied_side_effects]
            raise ValueError(f"--authorize 不接受 {bad}：可授予的只有 {sorted(self.authorizable)}"
                             + (f"；{forbidden} 为 policy 硬禁止，任何授权下都不执行" if forbidden else ""))

    def check_authorizations(self, requested: list, granted: set, supervisor: Optional[str] = None,
                             safety: Optional[dict] = None) -> dict:
        """用例申请的授权必须全部由本次运行显式给出；不能申请硬禁止类。
        supervised 类（车辆运动/车控）还需要在场监护人；vehicle_motion 还需要用例声明安全停止序列。"""
        bad = [r for r in requested if r not in self.authorizable]
        missing = [r for r in requested if r in self.authorizable and r not in granted]
        need_sup = [r for r in requested if r in self.supervision.get("requires_supervisor", self.supervised_side_effects)]
        no_sup = need_sup if (need_sup and not (supervisor or "").strip()) else []
        need_safety = [r for r in requested if r in self.supervision.get("requires_safety", [])]
        no_safety = need_safety if (need_safety and not (safety or {}).get("safe_stop")) else []
        return {"ok": not (bad or missing or no_sup or no_safety), "not_grantable": bad, "missing": missing,
                "missing_supervisor": no_sup, "missing_safety": no_safety}

    def is_safe_stop_text(self, text: str) -> bool:
        return bool(self.safe_stop_rx and text and self.safe_stop_rx.search(text))

    def check_effect(self, target: Optional[dict], authorized: Optional[set] = None) -> Optional[dict]:
        """目标声明的副作用门槛：硬禁止类任何授权下都拒绝；gated/supervised 类需本次运行显式授权。
        点击和输入共用这一处：通过返回 None，不通过返回拒绝结果。"""
        effect = (target or {}).get("side_effect", "navigation")
        if effect in self.denied_side_effects:
            return {"allowed": False, "reason": f"目标副作用 {effect} 在 policy 中被禁止", "label": None, "deny": None}
        if effect in self.authorizable and effect not in set(authorized or ()):
            sup = effect in self.supervised_side_effects
            how = f"--authorize {effect}" + (" --supervisor <在场监护人>" if sup else "")
            return {"allowed": False, "reason": f"目标副作用 {effect} 需要本次运行显式授权（{how}）",
                    "label": None, "deny": {"class": effect, "gated": True, "supervised": sup}}
        return None

    def check_tap(self, tree: Optional[UiTree], point: tuple, basis: str, target: Optional[dict] = None,
                  state_deny: Optional[list] = None, authorized: Optional[set] = None, node=None,
                  confirmation_sources: list | None = None) -> dict:
        """判定一次点击。返回 {allowed, reason, label, deny}；不会抛异常。
        authorized：本次运行由人显式授予的 gated 副作用；只放行"目标声明了同一副作用"的命中。"""
        target, authorized = target or {}, set(authorized or ())
        if basis not in VALID_BASIS:
            return {"allowed": False, "reason": f"未知定位依据 {basis}", "label": None, "deny": None}
        effect = target.get("side_effect", "navigation")
        denied = self.check_effect(target, authorized)
        if denied:
            return denied
        if target.get("forbid_tap"):
            return {"allowed": False, "reason": target.get("forbid_reason", "目标声明禁止点击"), "label": None, "deny": None}

        tree_ok = tree is not None and tree.status == "ok"
        if not tree_ok:
            if any(s.get("kind") == "text" and not s.get("exact", True) for s in target.get("strategies", [])):
                return {"allowed": False, "reason": "无可用 UI 树，无法核验非精确文本选择器的确认语义",
                        "label": None, "deny": {"class": "confirmation_semantics_unknown"}}
            labels = [target.get("label", "")] + [s.get("value", "") for s in target.get("strategies", [])
                                                   if s.get("kind") == "text"]
            for strategy in target.get("strategies", []):
                if strategy.get("kind") == "xpath":
                    xpath = strategy.get("value", "")
                    # 没有当前树时不能求值文本/动态表达式；concat 等不能拆成安全的字面标签。
                    expression = re.sub(r"'[^']*'|\"[^\"]*\"", "", xpath)
                    if re.search(r"@\s*(?:text|description)\b|@\s*\*|attribute\s*::|\(|\$", expression):
                        return {"allowed": False, "reason": "无可用 UI 树，无法核验文本或动态 XPath 的确认语义",
                                "label": None, "deny": {"class": "confirmation_semantics_unknown"}}
                    labels.extend(re.findall(r"['\"]([^'\"]+)['\"]", xpath))
            if any(isinstance(t, str) and self.confirmation_rx.fullmatch(t.strip()) for t in labels):
                return {"allowed": False, "reason": "无可用 UI 树，无法核验通用确认的当前语义",
                        "label": None, "deny": {"class": "confirmation_semantics_unknown"}}
            if effect == "safe_stop":
                # 安全停止豁免预算与运动锁，所以只在能按停止类文字确认控件身份时才点；
                # 没有 UI 树时模板/坐标都无法确认，宁可交人工接管也不盲点
                return {"allowed": False, "reason": "无可用 UI 树，无法确认 safe_stop 目标是停止控件；不盲点，需人工接管",
                        "label": None, "deny": {"class": "safe_stop_unconfirmed"}}
            if basis == "template":
                return {"allowed": True, "reason": "无可用 UI 树，按确定性模板匹配结果执行", "label": None, "deny": None}
            if basis == "point" and self.allow_point_without_tree:
                return {"allowed": True, "reason": "policy 允许无树坐标点击", "label": None, "deny": None}
            return {"allowed": False, "reason": "无可用 UI 树，禁止盲点坐标；请先模板定位或重新观察", "label": None,
                    "deny": None}

        # 按 UI 树定位到的节点优先（定位本身已确认它可见、唯一）；坐标类依据才按点击点反查
        node = node if node is not None else tree.node_at(*point)
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
        label = (texts[0] if texts else "")[:40]
        hits = self.match_all(texts, state_deny)
        editable = any(n.type in ("TextInput", "TextArea", "EditText")
                       for n in [node, container] + tree.ancestors(node))
        generic = [] if editable else [t for t in texts if self.confirmation_rx.fullmatch(t.strip())]
        confirmation = None
        confirmation_unknown = False
        if effect == "safe_stop" and any(re.search(r"确定|确认|\bOK\b", t, re.IGNORECASE) for t in texts):
            return {"allowed": False, "reason": "安全停止控件的同一容器含通用确认，无法确认是纯停止动作",
                    "label": label, "deny": {"class": "safe_stop_unconfirmed"}}
        if generic:
            # 同一可点击容器含确认按钮时，safe_stop 声明不能把确认变成停止。
            if effect == "safe_stop":
                return {"allowed": False, "reason": "安全停止控件的同一容器含通用确认，无法确认是纯停止动作",
                        "label": label, "deny": {"class": "safe_stop_unconfirmed"}}
            # 只读目标所属最近弹窗，不能借背景页或另一弹窗的文字证明确认语义。
            modal_ids = {m.idx for m in tree.modal_layers()}
            modal = next((m for m in [container] + tree.ancestors(container) if m.idx in modal_ids), None)
            modal_hits = self.match_all(tree.texts_in([modal]), state_deny) if modal else []
            title = next((n for n in tree.nodes if modal and tree.in_scope(n, [modal]) and tree.shown(n)
                          and n.type == "Text" and n.text.strip() and not n.clickable
                          and next((a.idx for a in tree.ancestors(n) if a.idx in modal_ids), None) == modal.idx
                       and not any(a.clickable or a.type in ("TextInput", "TextArea", "EditText")
                                   for a in tree.ancestors(n) if a.idx != modal.idx and tree.is_within(a, modal))), None)
            context = [{"kind": "policy_confirmation_context", "text": title.text, "pattern": rx.pattern,
                        "class": cls, "modal_idx": modal.idx, "title_node": title.idx}
                       for rx, cls in self.confirmation_contexts if title and rx.fullmatch(title.text.strip())]
            # Modal 状态说明只能增加限制；正向来源另须显式登记的确认标题，不能从“算路中”推定点击效果。
            page_sources = []
            for source in confirmation_sources or []:
                if source.get("kind") != "state_confirmation_candidates":
                    continue
                scope_idx = source.get("scope_idx")
                if scope_idx is not None and not (isinstance(scope_idx, int) and 0 <= scope_idx < len(tree.nodes)
                                                  and tree.is_within(container, tree.nodes[scope_idx])):
                    continue
                effects = [{r["class"] for r in candidate.get("rules", []) if re.search(r["pattern"], t.strip())}
                           for candidate in source.get("candidates", []) for t in generic]
                # 同栏每个候选须对每个确认标签给出同一唯一效果；缺失和冲突都不是正向证据。
                if effects and all(len(classes) == 1 for classes in effects) and len(set.union(*effects)) == 1:
                    page_sources.append({**source, "class": next(iter(effects[0]))})
            proven = bool(page_sources or context)
            hits.extend(modal_hits)
            hits.extend({"text": c["text"], "pattern": c["pattern"], "class": c["class"],
                         "reason": "当前弹窗的已登记确认语义"} for c in context)
            receiver = tree.touch_target(*point)
            benign = [s for s in confirmation_sources or [] if s.get("kind") == "reviewed_benign_interrupt"
                      and effect in ("navigation", "dismiss_dialog", "read_only")
                      and modal and s.get("modal_idx") == modal.idx
                      and receiver is not None and s.get("container_idx") == receiver.idx == container.idx]
            confirmation_unknown = not proven and not benign
            confirmation = {"texts": generic, "modal_idx": modal.idx if modal else None,
                            "rules": hits, "page_sources": page_sources, "contexts": context, "benign": benign}
        if effect == "safe_stop":
            return self._check_safe_stop(texts, hits, label)
        # 硬禁止（不可逆账号变更/升级/支付/清数据、页面级禁止）任何授权下都拒绝；
        # gated/supervised 类需"本次运行已授权 + 目标声明同一副作用"
        hard = [h for h in hits if h["class"] not in self.authorizable]
        if hard:
            h = hard[0]
            return {"allowed": False, "reason": f"命中禁止文本「{h['text']}」：{h['reason']}", "label": label, "deny": h}
        gated = [h for h in hits if not (h["class"] in authorized and h["class"] == effect)]
        if gated:
            h = gated[0]
            sup = h["class"] in self.supervised_side_effects
            why = f"需授权 {h['class']}{'（且有在场监护人）' if sup else ''} 且目标声明 side_effect={h['class']}"
            return {"allowed": False, "reason": f"命中受控文本「{h['text']}」：{why}", "label": label,
                    "deny": {**h, "gated": True, "supervised": sup}}
        if confirmation_unknown:
            return {"allowed": False, "reason": "通用确认缺少当前页面或同一弹窗的具体副作用依据；需先确认语义",
                    "label": label, "deny": {"class": "confirmation_semantics_unknown"}}
        if hits:
            return {"allowed": True, "reason": f"已授权 {effect}，目标声明一致", "label": label, "deny": None,
                    "authorized_by": effect, **({"confirmation_source": confirmation} if confirmation else {})}
        if basis == "point" and not texts:
            return {"allowed": False, "reason": "坐标落在无文本标识的控件上，无法确认副作用", "label": label, "deny": None}
        return {"allowed": True, "reason": "通过", "label": label, "deny": None,
                **({"confirmation_source": confirmation} if confirmation else {})}

    def _check_safe_stop(self, texts: list, hits: list, label: str) -> dict:
        """安全停止（暂停/退出泊车）：不需要授权、不受页面级禁止限制。
        但点的必须真是停止类控件：容器里要有停止类文字；带停止字样的运动/入口文字（"退出泊车"）豁免，
        其余命中（"继续"、账号/支付类……）说明同一容器还有别的效果，拒绝。"""
        if not any(self.is_safe_stop_text(t) for t in texts):
            return {"allowed": False, "reason": "声明为 safe_stop 的目标所在容器没有停止类文字，无法确认是安全停止",
                    "label": label, "deny": {"class": "safe_stop_unconfirmed"}}
        exempt = {"vehicle_motion", "vpd_entry", "route_planning", "state_deny"}
        other = [h for h in hits if not (h["class"] in exempt and self.is_safe_stop_text(h["text"]))]
        if other:
            h = other[0]
            return {"allowed": False, "reason": f"安全停止控件的同一容器内还有「{h['text']}」（{h['class']}），点击效果不确定",
                    "label": label, "deny": {**h, "mixed_with_safe_stop": True}}
        return {"allowed": True, "reason": "安全停止：不受授权门槛与页面级禁止限制", "label": label, "deny": None,
                "safe_stop": True}

    def check_input(self, text: str, target: Optional[dict], is_secret_ref: bool = False) -> dict:
        target = target or {}
        if not self.allow_input and not target.get("allow_input"):
            return {"allowed": False, "reason": "policy 未开启文本输入；需在目标上声明 allow_input"}
        if target.get("secret") and not is_secret_ref:
            return {"allowed": False, "reason": "敏感输入框只接受密钥引用 ${var:x}（x 在 test_data 中声明为 secret），不接受明文"}
        if is_secret_ref and not target.get("secret"):
            return {"allowed": False, "reason": "密钥只能写入声明了 secret 的输入框"}
        return {"allowed": True, "reason": "通过"}
