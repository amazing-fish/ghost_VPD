"""目标定位：按 targets.json 中声明的策略顺序尝试，记录每一次尝试。

定位不可靠（未找到 / 多个匹配 / 只露出一小部分 / 出错）时返回相应状态，由执行层决定滑动、重新观察
或结束为不可判定，这里绝不"选第一个"盲点。经验 overlay 只能调整策略顺序，不能删除仓库来源的原始策略。

屏幕形态自适应：
- 点击点 = 目标节点"可见部分"的中心，按当前 UI 树现算；被列表裁剪、被底部导航挡住的部分不算。
- 策略可带 when（只在某些窗口形态下使用）、within（只在某个容器/分栏内查找）、region（相对窗口的比例）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .layout import resolve_scope, when_matches
from .tree import UiTree, in_box
from .util import sha256_obj

DEFAULT_SCALES = (0.5, 2.0, 31)


def locator_fingerprint(target: dict) -> str:
    """Bind an index to its complete locator definition, including scope/order."""
    return sha256_obj(target)


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
        return tree.find_text(strategy["value"], exact=strategy.get("exact", True), include_clipped=True)
    if kind == "id":
        return tree.find_id(strategy["value"], include_clipped=True)
    raise ValueError(f"未知策略 {kind}")


def template_scales(strategy: dict, layout: Optional[dict]) -> tuple:
    """模板按像素密度缩放：已知模板采集时的密度，就在"当前密度 / 采集密度"附近搜索，否则全范围搜索。"""
    td, d = strategy.get("density"), (layout or {}).get("density")
    if not td or not d:
        return DEFAULT_SCALES
    expect = d / td
    return (round(expect * 0.8, 3), round(expect * 1.25, 3), 15)


def _tree_attempt(rec: dict, s: dict, tree: UiTree, layout: Optional[dict], min_vis: float) -> None:
    scope = resolve_scope(s.get("within"), tree, layout)
    if scope is not None and not scope:
        rec.update(status="not_found", reason=f"限定范围 {s['within']} 在当前页面不存在")
        return
    box = tree.region_box(s["region"]) if s.get("region") else None
    nodes = [n for n in _nodes_for(s, tree) if n.visible and tree.in_scope(n, scope)]
    shown = []
    for n in nodes:
        v = tree.visibility(n)
        if v["center"] and in_box(v["center"], box):
            shown.append((n, v))
    rec["matches"] = len(shown)
    if len(shown) > 1:
        rec.update(status="ambiguous", candidates=[n.brief() for n, _ in shown[:5]])
    elif len(shown) == 1:
        n, v = shown[0]
        rec["visible_fraction"] = v["fraction"]
        if v["fraction"] >= min_vis:
            rec.update(status="found", point=list(v["center"]), bounds=list(n.bounds), visible=list(v["rect"]),
                       node=n.brief())
        else:
            rec.update(status="partially_visible", bounds=list(n.bounds), visible=list(v["rect"]), node=n.brief(),
                       reason=f"只露出 {v['fraction']:.0%}（阈值 {min_vis:.0%}），可能被裁剪或遮挡，不点边缘")
    else:
        rec["status"] = "not_found"
        if nodes and box is None:
            rec["offscreen"] = len(nodes)  # 在树里但被列表裁剪到可视区外，滑动后可能出现
            rec["clipped_candidates"] = [tree.visibility_evidence(n) for n in nodes[:5]]


def resolve(target_id: str, target: dict, tree: Optional[UiTree], screenshot: Optional[str], app_dir: Path,
            overlay_pref: Optional[int] = None, point: Optional[list] = None, layout: Optional[dict] = None) -> dict:
    attempts, found = [], None
    tree_ok = tree is not None and tree.status == "ok"
    min_vis = float(target.get("min_visible_fraction", (layout or {}).get("min_visible_fraction", 0.5)))
    for i, s in ordered_strategies(target, overlay_pref):
        rec = {"index": i, "kind": s["kind"], "value": s.get("value") or s.get("file")}
        for k in ("when", "within", "region"):
            if s.get(k) is not None:
                rec[k] = s[k]
        try:
            applies = when_matches(s.get("when"), layout)
            if applies is not True:
                rec["status"] = "skipped_layout" if applies is False else "skipped_layout_unknown"
            elif s["kind"] in ("xpath", "text", "id"):
                if not tree_ok:
                    rec["status"] = "skipped_no_tree"
                else:
                    _tree_attempt(rec, s, tree, layout, min_vis)
            elif s["kind"] == "template":
                file = (app_dir / s["file"]).resolve()
                if not screenshot:
                    rec["status"] = "skipped_no_screenshot"
                elif not file.exists():
                    rec.update(status="error", error=f"模板文件不存在: {file}")
                else:
                    from .templates import match_template
                    box = tree.region_box(s["region"]) if (tree_ok and s.get("region")) else None
                    rec["scales"] = list(template_scales(s, layout))
                    m = match_template(screenshot, file, s.get("threshold", 0.85), region=box,
                                       scales=tuple(rec["scales"]))
                    rec.update(m)
            elif s["kind"] == "point":
                rec["status"] = "skipped_not_allowed"
            else:
                rec.update(status="error", error=f"未知策略 {s['kind']}")
        except Exception as e:  # xpath 语法错误、未知 when/within 等：记录为 error，而不是当作"元素不存在"
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
                "visible": found.get("visible"),
                "strategy_index": found["index"], "strategy_kind": found["kind"],
                "locator_fingerprint": locator_fingerprint(target),
                "basis": {"xpath": "tree", "text": "tree", "id": "tree", "template": "template"}.get(found["kind"], "point"),
                # adapted：本次首个尝试的策略失败、由后备策略定位成功（经验候选的来源）
                "adapted": any(a.get("status") in ("not_found", "ambiguous", "error")
                               for a in attempts[:attempts.index(found)]),
                "overlay_pref": overlay_pref, "visible_fraction": found.get("visible_fraction"),
                "primary_kind": primary, "score": found.get("score"), "attempts": attempts}
    statuses = [a.get("status") for a in attempts]
    status = next((s for s in ("ambiguous", "partially_visible", "error") if s in statuses), "not_found")
    out = {"target": target_id, "status": status, "attempts": attempts, "tree_status": tree.status if tree else None}
    if attempts and all(str(s).startswith("skipped_layout") for s in statuses):
        out["reason"] = "所有策略都不适用于当前窗口形态（when 条件不满足或形态未知）"
    return out
