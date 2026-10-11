"""有来源的需求与业务知识。判定只能引用这里的条目，且只有 confirmed 条目能支撑通过/失败。

- 新增条目一律 unconfirmed；confirm 需要评审人，模型推断/单次观察来源还必须给出依据。
- 同一 subject 下多个有效条目的 expected 不一致即视为冲突，引用方判为待确认；冲突不会被自动消解，
  resolve 会保留被废弃条目及理由。
- applies_to 参与判据适用性：本次运行的事实（APP、版本、屏幕形态、账号状态……）缺失或无法比较时是
  applicability_unknown，明确不符时是 out_of_scope，两者都不能支撑通过/失败。
- operation（怎样操作）与 observation（某次实际发生了什么）两类条目不是判据，引用它们只能得到待确认。
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Optional

from .util import now_iso, read_json, write_json

KINDS = {"requirement", "product_rule", "ui_copy", "generic_invariant", "domain_note", "operation", "observation"}
NOT_ORACLE_KINDS = {"operation", "observation"}
SOURCE_TYPES = {"requirement_doc", "text_case", "repo_code", "reviewer", "skill_policy", "model_inference", "observation",
                "test_goal"}
ACTIVE = {"unconfirmed", "confirmed"}
# 按严重程度排序：前面的状态出现就决定整体判据状态
ORACLE_ORDER = ("missing", "conflicted", "out_of_scope", "applicability_unknown", "not_oracle", "unconfirmed")
ORACLE_REASON = {"missing": "引用的知识不存在或已废弃", "conflicted": "引用的知识存在冲突",
                 "out_of_scope": "引用的知识适用范围与本次运行不符",
                 "applicability_unknown": "无法确认引用的知识适用于本次运行（版本/形态等事实未知或不可比较）",
                 "not_oracle": "引用的是操作经验或现场观察，不是产品要求",
                 "unconfirmed": "引用的知识尚未确认"}
_VERSION = re.compile(r"^\d+(\.\d+)+$")


def _base_version(v) -> Optional[str]:
    """3.0.3.200-replay → 3.0.3.200；不是点分数字版本（例如"内部debug版 phone_fc78649"）返回 None。"""
    s = str(v or "").strip().split("-", 1)[0]
    return s if _VERSION.match(s) else None


def applicability(item: dict, facts: Optional[dict]) -> dict:
    """applies_to 与本次事实逐项比较。事实缺失、值不可比较 → unknown；不相等 → out_of_scope。"""
    scope = item.get("applies_to") or {}
    facts = facts or {}
    unknown, mismatch = [], []
    for k, want in scope.items():
        if want in ("*", None, ""):
            continue
        have = facts.get(k)
        if have in (None, ""):
            unknown.append({"key": k, "required": want, "actual": None, "why": "本次运行没有这项事实"})
        elif k == "app_version":
            wants = [_base_version(w) for w in (want if isinstance(want, list) else [want])]
            b = _base_version(have)
            if b is None or None in wants:
                unknown.append({"key": k, "required": want, "actual": have, "why": "版本描述不是可比较的版本号"})
            elif b not in wants:
                mismatch.append({"key": k, "required": want, "actual": have})
        elif str(have) not in [str(w) for w in (want if isinstance(want, list) else [want])]:
            mismatch.append({"key": k, "required": want, "actual": have})
    status = "out_of_scope" if mismatch else ("unknown" if unknown else "applies")
    return {"status": status, "unknown": unknown, "mismatch": mismatch}


def _scope_values(k: str, want) -> set:
    vals = want if isinstance(want, list) else [want]
    if k == "app_version":
        return {_base_version(v) or str(v).strip() for v in vals}
    return {str(v) for v in vals}


def _scopes_overlap(a: dict, b: dict) -> bool:
    """两条知识的 applies_to 是否可能同时适用：共同限定的每一项取值都有交集（未限定 = 适用于所有取值）。"""
    sa, sb = a.get("applies_to") or {}, b.get("applies_to") or {}
    for k in set(sa) & set(sb):
        if sa[k] in ("*", None, "") or sb[k] in ("*", None, ""):
            continue
        if not _scope_values(k, sa[k]) & _scope_values(k, sb[k]):
            return False
    return True


class KnowledgeStore:
    def __init__(self, root: Path, facts: Optional[dict] = None):
        self.path = root / "knowledge" / "items.json"
        self.facts = facts  # 本次运行的事实，由 runner 在前置检查后绑定；None 表示没有运行上下文

    def bind(self, facts: dict) -> "KnowledgeStore":
        self.facts = dict(facts)
        return self

    def items(self) -> list:
        return read_json(self.path, {"items": []})["items"]

    def _save(self, items: list) -> None:
        write_json(self.path, {"items": sorted(items, key=lambda i: i["id"])})

    def get(self, kid: str) -> Optional[dict]:
        return next((i for i in self.items() if i["id"] == kid), None)

    def conflicts(self, facts: Optional[dict] = None) -> dict:
        """同一主题、期望不同、且适用范围有交集的条目才算冲突：按版本/形态分别规定的期望不是冲突。
        给出 facts 时只比较不排除本次运行的条目（out_of_scope 的那条对本次运行不存在）。"""
        groups = {}
        for it in self.items():
            if it["status"] in ACTIVE and it.get("subject") and \
                    (facts is None or applicability(it, facts)["status"] != "out_of_scope"):
                groups.setdefault(it["subject"], []).append(it)
        out = {}
        for s, g in groups.items():
            ids = {x["id"] for a in g for b in g if a["id"] < b["id"] and repr(a.get("expected")) != repr(b.get("expected"))
                   and _scopes_overlap(a, b) for x in (a, b)}
            if ids:
                out[s] = [i["id"] for i in g if i["id"] in ids]
        return out

    def oracle_status(self, refs: list, facts: Optional[dict] = None) -> dict:
        """判据状态。facts 默认取 bind() 绑定的本次运行事实；没有事实时，带范围限制的条目一律 applicability_unknown。"""
        if not refs:
            return {"status": "missing", "reason": "未关联需求/已确认用例/产品规则", "items": []}
        facts = self.facts if facts is None else facts
        conflicted = {k for ids in self.conflicts(facts).values() for k in ids}
        snap, statuses = [], set()
        for r in refs:
            it = self.get(r)
            if it is None or it["status"] == "deprecated":
                snap.append({"id": r, "status": "missing" if it is None else "deprecated"})
                statuses.add("missing")
                continue
            app = applicability(it, facts)
            if r in conflicted:
                st = "conflicted"
            elif app["status"] == "out_of_scope":
                st = "out_of_scope"
            elif app["status"] == "unknown":
                st = "applicability_unknown"
            elif it["kind"] in NOT_ORACLE_KINDS:
                st = "not_oracle"
            else:
                st = it["status"]
            snap.append({"id": r, "status": st, "statement": it["statement"], "source": it.get("source", {}).get("ref"),
                         "applies_to": it.get("applies_to"), "applicability": app})
            statuses.add(st)
        for st in ORACLE_ORDER:
            if st in statuses:
                return {"status": st, "items": snap, "reason": ORACLE_REASON[st]}
        return {"status": "confirmed", "items": snap}

    def add(self, item: dict) -> dict:
        for k in ("id", "kind", "statement", "source"):
            if not item.get(k):
                raise ValueError(f"知识条目缺少字段 {k}")
        if item["kind"] not in KINDS or item["source"].get("type") not in SOURCE_TYPES:
            raise ValueError(f"kind 须为 {sorted(KINDS)}，source.type 须为 {sorted(SOURCE_TYPES)}")
        if self.get(item["id"]):
            raise ValueError(f"知识 {item['id']} 已存在")
        item = {**item, "status": "unconfirmed", "created": now_iso(),
                "history": [{"ts": now_iso(), "to": "unconfirmed", "why": "新增"}]}
        self._save(self.items() + [item])
        return item

    def confirm(self, kid: str, reviewer: str, basis: Optional[str] = None) -> dict:
        items = self.items()
        it = next((i for i in items if i["id"] == kid), None)
        if it is None:
            raise ValueError(f"没有知识 {kid}")
        if not reviewer:
            raise PermissionError("确认知识必须提供评审人")
        if it["source"]["type"] in ("model_inference", "observation") and not basis:
            raise PermissionError("模型推断或单次观察来源的知识，确认时必须提供依据（--basis）")
        it["history"].append({"ts": now_iso(), "from": it["status"], "to": "confirmed", "by": reviewer, "basis": basis})
        it.update(status="confirmed", confirmed_by=reviewer, confirmed_at=now_iso(), confirm_basis=basis)
        self._save(items)
        return it

    def resolve(self, subject: str, keep: str, reviewer: str, reason: str) -> dict:
        items = self.items()
        group = [i for i in items if i.get("subject") == subject and i["status"] in ACTIVE]
        if keep not in [i["id"] for i in group]:
            raise ValueError(f"{keep} 不在 subject={subject} 的有效条目中")
        for i in group:
            if i["id"] != keep:
                i["history"].append({"ts": now_iso(), "from": i["status"], "to": "deprecated", "by": reviewer,
                                     "why": f"冲突裁决保留 {keep}：{reason}"})
                i["status"] = "deprecated"
        kept = next(i for i in group if i["id"] == keep)
        kept["history"].append({"ts": now_iso(), "event": "conflict_resolved", "by": reviewer, "reason": reason,
                                "deprecated": [i["id"] for i in group if i["id"] != keep]})
        self._save(items)
        return kept


def import_repo_selectors(py_file: Path, var_name: str, out_path: Path, repo_root: Optional[Path] = None) -> dict:
    """用 AST 读取仓库 app/config/*.py 中的选择器字典（不 import，避免触发原工程副作用），附来源行号与提交。"""
    src = Path(py_file).read_text(encoding="utf-8")
    tree = ast.parse(src)
    node = next((n for n in tree.body if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", None) == var_name for t in n.targets)), None)
    if node is None or not isinstance(node.value, ast.Dict):
        raise ValueError(f"{py_file} 中没有字典常量 {var_name}")
    commit = None
    if repo_root:
        r = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        commit = r.stdout.strip() or None
    rel = str(Path(py_file).resolve().relative_to(Path(repo_root).resolve())) if repo_root else str(py_file)
    selectors = {}
    for k, v in zip(node.value.keys, node.value.values):
        selectors[ast.literal_eval(k)] = {"value": ast.literal_eval(v), "line": k.lineno}
    out = {"source": {"file": rel.replace("\\", "/"), "variable": var_name, "commit": commit, "imported": now_iso()},
           "status": "unconfirmed_locator_hints",
           "note": "来自原工程自动化代码的定位线索，只说明'当时脚本这样定位'，不是产品规则", "selectors": selectors}
    write_json(out_path, out)
    return {"count": len(selectors), "out": str(out_path), "commit": commit}
