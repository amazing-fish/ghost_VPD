"""知识检索：在现有文件上做轻量关键词检索，帮助 Agent 选择操作与判据。不建索引、不做向量检索。

每条结果标明它回答哪类问题：
- requirement 产品要求："应该怎样"。只有这一类在 confirmed 且适用时才能作判据。
- operation   操作经验："可以怎样操作"（目标/选择器、流程、状态签名、真机校准笔记）。
- observation 现场观察："某次实际发生了什么"（单次观测、历史问题单）。不能直接证明"应该是什么"。
- background  背景：数据口径、项目记忆等，不作判据也不直接指导操作。
并附来源、适用范围（applies_to 与本次事实比较，未知就是 unknown）、验证状态与实际可调用的命令。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .knowledge import ORACLE_REASON, KnowledgeStore, applicability
from .util import read_json

ROLE_LABEL = {"requirement": "产品要求", "operation": "操作经验", "observation": "现场观察", "background": "背景"}
DOC_ROLES = {  # knowledge/ 下 Markdown 文档的角色（按文件）
    "auto-vpd/atom-registry.md": "operation", "auto-vpd/map-interaction-knowledge.md": "operation",
    "auto-vpd/troubleshooting.md": "operation", "auto-vpd/device-knowledge.md": "observation",
    "auto-vpd/README.md": "background", "project-memory/project-knowledge.md": "background",
}


def item_role(it: dict) -> tuple:
    """(role, note)。按来源而不是只按 kind 归类：来自单次观察的"产品规则"如实标为现场观察。"""
    kind, src = it["kind"], (it.get("source") or {}).get("type")
    if kind == "operation":
        return "operation", None
    if kind == "observation":
        return "observation", None
    if src == "observation":
        # Issue #9：与判定端（knowledge.oracle_status）保持一致的 Oracle 资格——观察来源的规则
        # 一经确认（confirm 对观察来源强制要求依据）即具备判据资格，search 端不能永久把它按
        # 现场观察排除；两端不一致会让 Agent 在检索时看不到、判定时却有效的判据（或反之）。
        # 来源事实仍如实标注，证据链可回溯。
        if it.get("status") == "confirmed":
            return "requirement", "来源是现场观察，已凭依据确认；作判据时证据链可回溯到观察记录"
        return "observation", f"标为 {kind}，但来源是现场观察：确认（需依据）前不能当作产品要求"
    if kind in ("requirement", "product_rule", "ui_copy", "generic_invariant"):
        return "requirement", None
    return "background", None


def _terms(query: str) -> list:
    return [t.lower() for t in re.split(r"\s+", query or "") if t.strip()]


def _score(text: str, terms: list, strong: str = "") -> int:
    low, strong = text.lower(), strong.lower()
    return sum(2 if t in strong else 1 for t in terms if t in low)


def _items(ws, kn: KnowledgeStore, terms: list, facts: dict) -> list:
    conflicted = {k: s for s, ids in kn.conflicts(facts).items() for k in ids}
    out = []
    for it in kn.items():
        blob = json.dumps({k: it.get(k) for k in ("id", "subject", "statement", "expected", "note", "source")},
                          ensure_ascii=False)
        sc = _score(blob, terms, f"{it['id']} {it.get('subject', '')}")
        if not sc:
            continue
        role, note = item_role(it)
        app = applicability(it, facts)
        oracle = kn.oracle_status([it["id"]], facts=facts) if role == "requirement" else None
        out.append({"score": sc, "role": role, "role_label": ROLE_LABEL[role], "ref": it["id"], "kind": it["kind"],
                    "subject": it.get("subject"), "statement": it["statement"][:160], "expected": it.get("expected"),
                    "status": it["status"], "conflict_subject": conflicted.get(it["id"]),
                    "source": {"type": (it.get("source") or {}).get("type"), "ref": (it.get("source") or {}).get("ref")},
                    "applies_to": it.get("applies_to"), "applicability": app["status"],
                    "applicability_detail": app["unknown"] + app["mismatch"],
                    "can_be_oracle": (oracle or {}).get("status") == "confirmed",
                    "oracle_block": None if oracle is None or oracle["status"] == "confirmed"
                    else ORACLE_REASON.get(oracle["status"]),
                    "note": note or it.get("note"),
                    "callable": f"断言 oracle_refs: [\"{it['id']}\"]" if role == "requirement" else None})
    return out


def _app_model(ws, app_id: str, terms: list) -> list:
    d = ws.root / "apps" / app_id
    out = []
    for tid, t in read_json(d / "targets.json", {"targets": {}})["targets"].items():
        blob = json.dumps({"id": tid, **{k: t.get(k) for k in ("desc", "strategies", "note", "status", "side_effect")}},
                          ensure_ascii=False)
        sc = _score(blob, terms, f"{tid} {t.get('desc', '')}")
        if sc:
            eff = t.get("side_effect", "navigation")
            out.append({"score": sc, "role": "operation", "role_label": ROLE_LABEL["operation"], "ref": f"target:{tid}",
                        "statement": t.get("desc"), "status": t.get("status", "unverified"),
                        "strategies": [{k: s.get(k) for k in ("kind", "value", "file", "source") if s.get(k)}
                                       for s in t.get("strategies", [])][:3],
                        "side_effect": eff, "applicability": "unknown" if t.get("status") != "calibrated_on_device" else "calibrated",
                        "callable": f"act tap --target {tid}" + (f" --authorize {eff}" if eff != "navigation" else "")
                                    + f" --app {app_id}"})
    for fid, f in read_json(d / "flows.json", {"flows": {}})["flows"].items():
        blob = json.dumps({"id": fid, **f}, ensure_ascii=False)
        sc = _score(blob, terms, f"{fid} {f.get('desc', '')}")
        if sc:
            out.append({"score": sc, "role": "operation", "role_label": ROLE_LABEL["operation"], "ref": f"flow:{fid}",
                        "statement": f.get("desc"), "status": f.get("status", "unverified"),
                        "note": f.get("status_note") or f.get("note"),
                        "steps": [f"{s.get('action')}:{s.get('target') or s.get('state') or ''}" for s in f.get("steps", [])][:12],
                        "applicability": "unknown",
                        "callable": f"用例步骤 {{\"action\": \"flow\", \"flow\": \"{fid}\"}}（不是单步命令；"
                                    "逐步用 act 复现时按 steps 里的目标执行）"})
    states = read_json(d / "states.json", {"states": {}}).get("states", {})
    for sid, s in states.items():
        sc = _score(json.dumps({"id": sid, **s}, ensure_ascii=False), terms, sid)
        if sc:
            out.append({"score": sc, "role": "operation", "role_label": ROLE_LABEL["operation"], "ref": f"state:{sid}",
                        "statement": s.get("desc"), "status": s.get("review", "pending"), "applicability": "unknown",
                        "callable": f"act ... --expect {sid}（按状态签名判断是否到达）"})
    return out


def _docs(ws, terms: list) -> list:
    out = []
    base = ws.root / "knowledge"
    for rel, role in DOC_ROLES.items():
        p = base / rel
        if not p.exists():
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        heading, start = rel, 0
        sections = []
        for i, ln in enumerate(lines + ["# end"]):
            if ln.startswith("#"):
                if i > start:
                    sections.append((heading, start, lines[start:i]))
                heading, start = ln.lstrip("# ").strip(), i
        for head, at, body in sections:
            text = "\n".join(body)
            sc = _score(text, terms, head)
            if not sc:
                continue
            hit = next((j for j, ln in enumerate(body) if j and ln.strip() and any(t in ln.lower() for t in terms)),
                       next((j for j, ln in enumerate(body) if j and ln.strip()), 0))  # 跳过标题行本身
            out.append({"score": sc, "role": role, "role_label": ROLE_LABEL[role], "ref": f"{rel}:{at + hit + 1}",
                        "statement": f"[{head}] " + body[hit].strip()[:160], "status": "unconfirmed_doc",
                        "source": {"type": "doc", "ref": f"data/knowledge/{rel}"}, "applicability": "unknown",
                        "note": "auto-vpd 文档来自内部 debug 版 phone_fc78649（阔折叠）的真机校准，其他版本/形态需重新观察"
                        if rel.startswith("auto-vpd") else None,
                        "callable": "无（atom-registry 里的原子在 auto-vpd 工程 scripts/atoms.py，本仓库没有；"
                                    "用 act --select/--target 组合实现）" if rel.endswith("atom-registry.md") else None})
    return out


def _defects(ws, terms: list) -> list:
    p = ws.root / "knowledge" / "sources" / "vpd_product_kb_260.json"
    out = []
    for i, k in enumerate(read_json(p, [])):
        blob = json.dumps({x: k.get(x) for x in ("title", "desc", "symptom", "cause", "keywords", "category")},
                          ensure_ascii=False)
        sc = _score(blob, terms, k.get("title", ""))
        if sc:
            out.append({"score": sc, "role": "observation", "role_label": ROLE_LABEL["observation"],
                        "ref": f"kb260#{i}", "statement": f"{k.get('title')}：{(k.get('symptom') or k.get('desc') or '')[:120]}",
                        "status": "historical_issue", "source": {"type": "defect_record",
                                                                 "ref": (k.get("origin") or {}).get("dtsTicket")},
                        "applies_to": {"version": k.get("version"), "endpoint": k.get("endpoint")},
                        "applicability": "unknown",
                        "note": "历史问题单：说明曾经出过什么问题，可用来想到该测什么，不是产品要求"})
    return out


def search(ws, query: str, app_id: Optional[str] = None, roles: Optional[list] = None, facts: Optional[dict] = None,
           per_role: int = 8) -> dict:
    terms = _terms(query)
    if not terms:
        raise ValueError("请给出检索词（空格分隔，任一命中即返回，命中越多越靠前）")
    facts = dict(facts or {})
    if app_id:
        facts.setdefault("app", app_id)
    kn = KnowledgeStore(ws.root)
    rows = _items(ws, kn, terms, facts) + _docs(ws, terms) + _defects(ws, terms)
    if app_id:
        rows += _app_model(ws, app_id, terms)
        rows = [r for r in rows if not (r.get("applies_to") or {}).get("app") or
                (r["applies_to"]["app"] in ("*", app_id))]
    if roles:
        rows = [r for r in rows if r["role"] in roles]
    rows.sort(key=lambda r: -r["score"])
    grouped, counts = {}, {}
    for r in rows:
        counts[r["role"]] = counts.get(r["role"], 0) + 1
        if len(grouped.setdefault(r["role"], [])) < per_role:
            grouped[r["role"]].append(r)
    usable = [r["ref"] for r in grouped.get("requirement", []) if r.get("can_be_oracle")]
    gaps = []
    if not usable:
        gaps.append("没有已确认且适用于本次事实的产品要求可作判据：相关检查只能判待确认；"
                    "如测试目标本身提出了期望，用 knowledge add（source.type=test_goal，unconfirmed）记下待负责人确认")
    if not facts.get("app_version"):
        gaps.append("未提供 APP 版本（--app-version 或 --device）：带版本范围的条目适用性一律未知")
    return {"query": query, "terms": terms, "facts": facts, "counts": counts, "results": grouped,
            "usable_oracles": usable, "gaps": gaps,
            "legend": {k: v for k, v in ROLE_LABEL.items()}}
