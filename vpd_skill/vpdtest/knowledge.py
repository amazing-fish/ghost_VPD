"""有来源的需求与业务知识。判定只能引用这里的条目，且只有 confirmed 条目能支撑通过/失败。

- 新增条目一律 unconfirmed；confirm 需要评审人，模型推断/单次观察来源还必须给出依据。
- 同一 subject 下多个有效条目的 expected 不一致即视为冲突，引用方判为待确认；冲突不会被自动消解，
  resolve 会保留被废弃条目及理由。
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Optional

from .util import now_iso, read_json, write_json

KINDS = {"requirement", "product_rule", "ui_copy", "generic_invariant", "domain_note"}
SOURCE_TYPES = {"requirement_doc", "text_case", "repo_code", "reviewer", "skill_policy", "model_inference", "observation"}
ACTIVE = {"unconfirmed", "confirmed"}


class KnowledgeStore:
    def __init__(self, root: Path):
        self.path = root / "knowledge" / "items.json"

    def items(self) -> list:
        return read_json(self.path, {"items": []})["items"]

    def _save(self, items: list) -> None:
        write_json(self.path, {"items": sorted(items, key=lambda i: i["id"])})

    def get(self, kid: str) -> Optional[dict]:
        return next((i for i in self.items() if i["id"] == kid), None)

    def conflicts(self) -> dict:
        groups = {}
        for it in self.items():
            if it["status"] in ACTIVE and it.get("subject"):
                groups.setdefault(it["subject"], []).append(it)
        return {s: [i["id"] for i in g] for s, g in groups.items()
                if len({repr(i.get("expected")) for i in g}) > 1}

    def oracle_status(self, refs: list) -> dict:
        if not refs:
            return {"status": "missing", "reason": "未关联需求/已确认用例/产品规则", "items": []}
        conflicted = {k for ids in self.conflicts().values() for k in ids}
        snap, statuses = [], set()
        for r in refs:
            it = self.get(r)
            if it is None or it["status"] == "deprecated":
                snap.append({"id": r, "status": "missing" if it is None else "deprecated"})
                statuses.add("missing")
                continue
            st = "conflicted" if r in conflicted else it["status"]
            snap.append({"id": r, "status": st, "statement": it["statement"], "source": it.get("source", {}).get("ref")})
            statuses.add(st)
        for st in ("missing", "conflicted", "unconfirmed"):
            if st in statuses:
                return {"status": st, "items": snap, "reason": {"missing": "引用的知识不存在或已废弃",
                        "conflicted": "引用的知识存在冲突", "unconfirmed": "引用的知识尚未确认"}[st]}
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
