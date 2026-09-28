"""目标定位：按 targets.json 中声明的策略顺序尝试，记录每一次尝试。

定位不可靠（未找到 / 多个匹配 / 出错）时返回相应状态，由执行层决定重新观察或结束为不可判定，
这里绝不"选第一个"盲点。经验 overlay 只能调整策略顺序，不能删除仓库来源的原始策略。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .tree import UiTree, in_box


def ordered_strategies(target: dict, overlay_pref: Optional[int] = None) -> list:
    strategies = list(enumerate(target.get("strategies", [])))
    if overlay_pref is not None and 0 <= overlay_pref < len(strategies):
        strategies.insert(0, strategies.pop(overlay_pref))
    return strategies


def _nodes_for(strategy: dict, tree: UiTree) -> list:
    kind = strategy["kind"]
    if kind == "xpath":
        return tree.xpath(strategy["value"])
    if kind == "text":
        return tree.find_text(strategy["value"], exact=strategy.get("exact", True))
    if kind == "id":
        return tree.find_id(strategy["value"])
    raise ValueError(f"未知策略 {kind}")


def resolve(target_id: str, target: dict, tree: Optional[UiTree], screenshot: Optional[str], app_dir: Path,
            overlay_pref: Optional[int] = None, point: Optional[list] = None) -> dict:
    attempts, found = [], None
    tree_ok = tree is not None and tree.status == "ok"
    for i, s in ordered_strategies(target, overlay_pref):
        rec = {"index": i, "kind": s["kind"], "value": s.get("value") or s.get("file")}
        try:
            if s["kind"] in ("xpath", "text", "id"):
                if not tree_ok:
                    rec["status"] = "skipped_no_tree"
                else:
                    box = tree.region_box(s["region"]) if s.get("region") else None
                    nodes = [n for n in _nodes_for(s, tree) if n.center and in_box(n.center, box)]
                    rec["matches"] = len(nodes)
                    if len(nodes) == 1:
                        rec.update(status="found", point=list(nodes[0].center), bounds=list(nodes[0].bounds),
                                   node=nodes[0].brief())
                    elif len(nodes) > 1:
                        rec.update(status="ambiguous", candidates=[n.brief() for n in nodes[:5]])
                    else:
                        rec["status"] = "not_found"
            elif s["kind"] == "template":
                file = (app_dir / s["file"]).resolve()
                if not screenshot:
                    rec["status"] = "skipped_no_screenshot"
                elif not file.exists():
                    rec.update(status="error", error=f"模板文件不存在: {file}")
                else:
                    from .templates import match_template
                    box = tree.region_box(s["region"]) if (tree_ok and s.get("region")) else None
                    m = match_template(screenshot, file, s.get("threshold", 0.85), region=box)
                    rec.update(m)
            elif s["kind"] == "point":
                rec["status"] = "skipped_not_allowed"
            else:
                rec.update(status="error", error=f"未知策略 {s['kind']}")
        except Exception as e:  # xpath 语法错误等：记录为 error，而不是当作"元素不存在"
            rec.update(status="error", error=f"{type(e).__name__}: {e}")
        attempts.append(rec)
        if rec.get("status") == "found":
            found = rec
            break

    if found is None and point is not None:
        rec = {"index": None, "kind": "point", "value": point, "status": "found", "point": list(point),
               "basis": "agent_proposed"}
        attempts.append(rec)
        found = rec

    primary = target.get("strategies", [{}])[0].get("kind") if target.get("strategies") else None
    if found:
        return {"target": target_id, "status": "found", "point": found["point"], "bounds": found.get("bounds"),
                "strategy_index": found["index"], "strategy_kind": found["kind"],
                "basis": {"xpath": "tree", "text": "tree", "id": "tree", "template": "template"}.get(found["kind"], "point"),
                # adapted：本次首个尝试的策略失败、由后备策略定位成功（经验候选的来源）
                "adapted": any(a.get("status") in ("not_found", "ambiguous", "error")
                               for a in attempts[:attempts.index(found)]),
                "overlay_pref": overlay_pref,
                "primary_kind": primary, "score": found.get("score"), "attempts": attempts}
    status = "ambiguous" if any(a.get("status") == "ambiguous" for a in attempts) else (
        "error" if any(a.get("status") == "error" for a in attempts) else "not_found")
    return {"target": target_id, "status": status, "attempts": attempts, "tree_status": tree.status if tree else None}
