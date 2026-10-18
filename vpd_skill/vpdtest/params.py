"""用例参数：test_data 变量、密钥引用、流程参数与可复用输入分类。

- test_data 中 {"secret": "NAME"} 的值只从环境变量 VPD_SECRET_<NAME> 或 config.local.json 的 secrets 读取，
  生成用例与参数汇总保留引用；现场树、事件和报告按采集原文留证。
- {"template": "VPD测试-{run}"} 在每次运行时生成（避免多次运行互相干扰），实际值记入 run.json。
- 步骤/目标中的 ${var:x} 引用 test_data，${param:x} 引用流程参数。
"""
from __future__ import annotations

import copy
import os
import re
from typing import Optional

_REF = re.compile(r"\$\{(var|param|secret):([A-Za-z0-9_.-]+)\}")
MIN_FRAGMENT = 6  # 可复用输入分类的密钥片段最短长度


class Params:
    def __init__(self, test_data: Optional[dict], secrets_cfg: Optional[dict], run_id: str):
        self.vars, self.secret_vars, self.missing = {}, set(), []
        self._secret_values = set()
        short = run_id.rsplit("-", 1)[-1]
        for k, v in (test_data or {}).items():
            if isinstance(v, dict) and "secret" in v:
                name = v["secret"]
                self.secret_vars.add(k)  # 声明身份不因本次缺值而消失。
                val = os.environ.get(f"VPD_SECRET_{name}") or (secrets_cfg or {}).get(name)
                if not val:
                    self.missing.append(name)
                    continue
                self.vars[k] = str(val)
                self._secret_values.add(str(val))
            elif isinstance(v, dict) and "template" in v:
                self.vars[k] = v["template"].format(run=short)
            else:
                self.vars[k] = v

    def public(self) -> dict:
        """可以写进 run.json 的参数值（密钥只写出引用名）。"""
        return {k: (f"<secret:{k}>" if k in self.secret_vars else v) for k, v in self.vars.items()}

    def is_secret_ref(self, text) -> bool:
        m = _REF.fullmatch(str(text or ""))
        return bool(m and m.group(1) == "var" and m.group(2) in self.secret_vars)

    def subst(self, obj, local: Optional[dict] = None):
        """深度替换字符串中的引用；未知引用保持原样并由调用方的定位/校验暴露出来。"""
        local = local or {}
        if isinstance(obj, str):
            def rep(m):
                kind, name = m.group(1), m.group(2)
                value = local.get(name) if kind == "param" else self.vars.get(name)
                return m.group(0) if value is None else str(value)
            return _REF.sub(rep, obj)
        if isinstance(obj, list):
            return [self.subst(x, local) for x in obj]
        if isinstance(obj, dict):
            return {k: self.subst(v, local) for k, v in obj.items()}
        return obj

    def preflight_missing(self, case, app) -> list:
        """只延期所有用途均为条件 input 的密钥；旧未使用声明仍保持预检。"""
        uses = {key: [] for key in self.secret_vars}

        def refs(value):
            if isinstance(value, str):
                return {m.group(2) for m in _REF.finditer(value) if m.group(1) in ("var", "secret")}
            if isinstance(value, dict):
                return set().union(*(refs(v) for v in value.values())) if value else set()
            if isinstance(value, list):
                return set().union(*(refs(v) for v in value)) if value else set()
            return set()

        def walk(step, guarded=False, local=None, ancestry=()):
            guarded = guarded or step.get("when") is not None
            # 这里只保留符号引用，不装载值，也不改变现有 flow 的参数解析语义。
            symbolic = Params({}, {}, "preflight").subst(step, local)
            action = step.get("action")
            if action == "flow":
                key = (action, step.get("flow"))
                children = (app.flows.get(key[1]) or {}).get("steps", [])
                child_local = symbolic.get("params", {})
            elif action == "ensure_state":
                key = (action, step.get("state"))
                children, child_local = app.states.recipes.get(key[1], []), None
            else:
                key, children, child_local = None, [], None
            fields = {k: v for k, v in symbolic.items() if k != "params" or key is None}
            other_refs = refs({k: v for k, v in fields.items() if k != "text"})
            for name in refs(fields):
                if name in uses:
                    uses[name].append(guarded and action == "input" and name in refs(symbolic.get("text"))
                                      and name not in other_refs)
            if key is not None and key not in ancestry:
                for child in children:
                    walk(child, guarded, child_local, ancestry + (key,))

        for step in [*case.get("steps", []), *case.get("cleanup", []), *((case.get("safety") or {}).get("safe_stop", []))]:
            walk(step)
        deferred = {case["test_data"][key]["secret"] for key, paths in uses.items() if paths and all(paths)}
        required = {case["test_data"][key]["secret"] for key, paths in uses.items() if not paths or not all(paths)}
        return [name for name in self.missing if name not in deferred or name in required]

    def add_secret_values(self, values) -> None:
        """只参与可复用输入分类，不成为可引用变量，也不改写现场证据。"""
        self._secret_values |= {str(v) for v in values if v}
        self._frag_cache = None

    def _fragments(self) -> list:
        """密钥本身及其所有长度 ≥ MIN_FRAGMENT 的连续片段，长的在前。输入框可能截断、只收下一部分，
        或把密码的一段落进另一个框（真机上见过），仅按全值分类会漏掉外置参数片段。"""
        if getattr(self, "_frag_cache", None) is None:
            frags = set()
            for s in self._secret_values:
                n = len(s)
                if n < MIN_FRAGMENT:
                    frags.add(s)
                    continue
                frags |= {s[i:j] for i in range(n) for j in range(i + MIN_FRAGMENT, n + 1)}
            self._frag_cache = sorted(frags, key=len, reverse=True)
        return self._frag_cache

    def is_sensitive(self, text) -> bool:
        """生成用例时判断输入是否引用外置参数；不影响现场树、事件或截图。"""
        t = str(text or "")
        return bool(t) and any(s in t for s in self._fragments())



def with_params(target: dict, params: Optional[Params], local: Optional[dict] = None) -> dict:
    return params.subst(copy.deepcopy(target), local) if params else target
