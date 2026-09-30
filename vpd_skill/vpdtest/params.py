"""用例参数：test_data 变量、密钥引用、流程参数与脱敏。

- test_data 中 {"secret": "NAME"} 的值只从环境变量 VPD_SECRET_<NAME> 或 config.local.json 的 secrets 读取，
  不写入用例、事件、观测、报告；UI 树与可见文本里出现的密钥值在落盘前替换为 ***。
- {"template": "VPD测试-{run}"} 在每次运行时生成（避免多次运行互相干扰），实际值记入 run.json。
- 步骤/目标中的 ${var:x} 引用 test_data，${param:x} 引用流程参数。
"""
from __future__ import annotations

import copy
import os
import re
from typing import Optional

_REF = re.compile(r"\$\{(var|param|secret):([A-Za-z0-9_.-]+)\}")
MASK = "***"
MIN_FRAGMENT = 6  # 密钥片段达到这个长度就脱敏


class Params:
    def __init__(self, test_data: Optional[dict], secrets_cfg: Optional[dict], run_id: str):
        self.vars, self.secret_vars, self.missing = {}, set(), []
        self._secret_values = set()
        short = run_id.rsplit("-", 1)[-1]
        for k, v in (test_data or {}).items():
            if isinstance(v, dict) and "secret" in v:
                name = v["secret"]
                val = os.environ.get(f"VPD_SECRET_{name}") or (secrets_cfg or {}).get(name)
                if not val:
                    self.missing.append(name)
                    continue
                self.vars[k] = str(val)
                self.secret_vars.add(k)
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
                if kind == "param":
                    return str(local.get(name, m.group(0)))
                return str(self.vars.get(name, m.group(0)))
            return _REF.sub(rep, obj)
        if isinstance(obj, list):
            return [self.subst(x, local) for x in obj]
        if isinstance(obj, dict):
            return {k: self.subst(v, local) for k, v in obj.items()}
        return obj

    def _fragments(self) -> list:
        """密钥本身及其所有长度 ≥ MIN_FRAGMENT 的连续片段，长的在前。输入框可能截断、只收下一部分，
        或把密码的一段落进另一个框（真机上见过），只按全值替换会漏。"""
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

    def redact(self, obj):
        if not self._secret_values:
            return obj
        if isinstance(obj, str):
            for s in self._fragments():
                if s in obj:
                    obj = obj.replace(s, MASK)
            return obj
        if isinstance(obj, list):
            return [self.redact(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.redact(v) for k, v in obj.items()}
        return obj

    def is_sensitive(self, text) -> bool:
        """落盘前判断明文的唯一出口：含任一已配置密钥或其长度 ≥ MIN_FRAGMENT 的片段就算敏感（不只比较全等）。"""
        t = str(text or "")
        return bool(t) and any(s in t for s in self._fragments())

    def contains_secret(self, texts) -> bool:
        return any(self.is_sensitive(t) for t in texts)


def with_params(target: dict, params: Optional[Params], local: Optional[dict] = None) -> dict:
    return params.subst(copy.deepcopy(target), local) if params else target
