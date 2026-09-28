"""判定契约。

断言结果：pass / fail / pending（有证据但判据未确认、冲突或属主观项）/ undeterminable（证据不足）/ not_run。
用例结论：passed 通过 / failed 失败 / blocked 阻塞 / pending 待确认，另记问题归属。
只有"确定性证据 + 已确认判据"才能得出通过或失败；模型意见（visual_review）永远只进入建议。
"""
from __future__ import annotations

from typing import Callable

from .tree import UiTree

RANK = {"failed": 0, "blocked": 1, "pending": 2, "passed": 3}
LABEL = {"passed": "通过", "failed": "失败", "blocked": "阻塞", "pending": "待确认"}
ATTR_LABEL = {"product": "产品", "tool": "工具", "environment": "环境", "requirement_unclear": "需求不明确"}
PASSED = ("passed", "passed_after_adaptation", "passed_after_recovery")
GENERIC_CRASH = "K-GEN-001"


def _check(a: dict, obs: dict, tree: UiTree, app) -> tuple:
    """返回 (holds: bool|None, evidence: dict)。None 表示证据不足。"""
    t = a["type"]
    ev = {"obs": obs["obs_id"], "tree": obs.get("tree", {}).get("path"), "screenshot": obs.get("screenshot", {}).get("path")}
    if t == "state_is":
        st = obs["page"]["state"]
        ev.update(observed_state=st, active=obs["page"].get("active"))
        if st in ("undetermined", "ambiguous"):
            return None, ev
        # 分栏时 active 可能同时含多个状态（左栏列表 + 右栏详情），期望状态在其中即成立
        return (st == a["state"] or a["state"] in (obs["page"].get("active") or [])), ev
    if tree is None or tree.status != "ok":
        return None, {**ev, "reason": f"UI 树不可用（{tree.status if tree else 'missing'}），不能据此判断有无"}
    if t in ("text_present", "text_absent"):
        hits = tree.find_text(a["text"], exact=a.get("exact", True))
        ev["matches"] = [n.brief() for n in hits[:3]]
        return (bool(hits) if t == "text_present" else not hits), ev
    if t in ("target_present", "target_absent", "target_text_equals", "node_selected"):
        from .locate import resolve
        tid, target = app.target(a["target"])
        loc = resolve(tid, target, tree, None, app.dir, layout=obs.get("layout"))
        ev["locate"] = {"status": loc["status"], "strategy": loc.get("strategy_kind"), "bounds": loc.get("bounds"),
                        "visible_fraction": loc.get("visible_fraction")}
        if loc["status"] in ("ambiguous", "error", "partially_visible"):
            return None, ev
        found = loc["status"] == "found"
        if t == "target_present":
            return found, ev
        if t == "target_absent":
            return not found, ev
        if not found:
            return None, ev
        hit = next((x for x in loc["attempts"] if x.get("status") == "found" and x.get("node")), None)
        node = tree.nodes[hit["node"]["idx"]] if hit else (tree.node_at(*loc["point"]) if loc.get("point") else None)
        if t == "node_selected":
            from .states import is_selected
            sel = is_selected(tree, node) if node else False
            ev["selected"] = sel
            return sel == a.get("expected", True), ev
        actual = node.text if node else None
        ev["actual_text"] = actual
        return actual == a["expected"], ev
    raise ValueError(f"未知断言类型 {t}")


def evaluate_assertion(a: dict, steps: dict, load: Callable, knowledge, app) -> dict:
    out = {"id": a["id"], "type": a["type"], "desc": a.get("desc", ""), "advisory": bool(a.get("advisory")),
           "oracle_refs": a.get("oracle_refs", []), "severity": a.get("severity", "major")}
    step = steps.get(a.get("at"))
    if step is None or step["outcome"] not in PASSED:
        return {**out, "result": "not_run", "reason": f"关联步骤 {a.get('at')} 未成功执行"}
    obs, tree = load(step["final_obs"])
    if a["type"] == "visual_review":
        return {**out, "result": "pending", "evidence": {"obs": obs["obs_id"], "screenshot": obs.get("screenshot", {}).get("path")},
                "reason": "主观/视觉检查：只输出建议，需人工或经确认的规则转化为确定性断言", "prompt": a.get("prompt")}
    holds, ev = _check(a, obs, tree, app)
    oracle = knowledge.oracle_status(a.get("oracle_refs", []))
    out.update(evidence=ev, oracle=oracle)
    if holds is None:
        return {**out, "result": "undeterminable", "reason": ev.get("reason", "证据不足以判定")}
    if oracle["status"] != "confirmed":
        return {**out, "result": "pending", "observed_holds": holds,
                "reason": f"观测{'符合' if holds else '不符合'}预期，但{oracle['reason']}"}
    return {**out, "result": "pass" if holds else "fail"}


def case_verdict(preflight: dict, env_check: dict, steps: list, assertions: list, knowledge) -> dict:
    findings, suggestions = [], []
    if not preflight.get("ok"):
        bad = [c for c in preflight.get("checks", []) if not c["ok"]]
        return _final("blocked", "environment", [{"kind": "preflight", "result": "blocked", "attribution": "environment",
                                                  "reason": "前置检查未通过：" + "、".join(
                                                      f"{c['check']}({c.get('error_class')})" for c in bad),
                                                  "detail": bad}], [], steps)
    if not env_check.get("ok"):
        return _final("blocked", "environment", [{"kind": "env_requirement", "result": "blocked",
                                                  "attribution": "environment",
                                                  "reason": "环境要求未满足：" + "、".join(
                                                      f"{u['key']}={u['actual']}（需 {u['required']}）" for u in env_check["unmet"]),
                                                  "detail": env_check["unmet"]}], [], steps)
    for s in steps:
        if s["outcome"] in PASSED or s["outcome"] == "not_run":
            continue
        if s["outcome"] == "fail_candidate":
            # 崩溃/异常中断由通用不变量判定，不依赖该步骤业务判据是否已确认
            refs = [GENERIC_CRASH] if s.get("final_status") == "anomaly" else list(s.get("oracle_refs", []))
            oracle = knowledge.oracle_status(refs)
            if oracle["status"] == "confirmed":
                findings.append({"kind": "step", "step": s["step_id"], "result": "failed", "attribution": "product",
                                 "reason": s["reason"], "oracle": oracle})
            else:
                findings.append({"kind": "step", "step": s["step_id"], "result": "pending",
                                 "attribution": "requirement_unclear", "reason": f"{s['reason']}；{oracle['reason']}",
                                 "oracle": oracle})
        else:
            findings.append({"kind": "step", "step": s["step_id"], "result": "blocked",
                             "attribution": s.get("attribution", "tool"), "reason": s.get("reason")})
    for a in assertions:
        if a["advisory"]:
            if a["result"] in ("pending", "fail", "undeterminable"):
                suggestions.append(a)
            continue
        if a["result"] == "fail":
            findings.append({"kind": "assertion", "id": a["id"], "result": "failed", "attribution": "product",
                             "reason": a.get("desc")})
        elif a["result"] in ("pending", "undeterminable"):
            findings.append({"kind": "assertion", "id": a["id"], "result": "pending",
                             "attribution": "requirement_unclear" if a["result"] == "pending" else "tool",
                             "reason": a.get("reason")})
    results = {f["result"] for f in findings}
    if not any(a["result"] in ("pass", "fail") for a in assertions if not a["advisory"]) and not results & {"failed", "blocked"}:
        findings.append({"kind": "case", "result": "pending", "attribution": "requirement_unclear",
                         "reason": "没有任何非建议类断言得到确定判定，不能判通过"})
        results.add("pending")
    for v in ("failed", "blocked", "pending"):
        if v in results:
            attr = next(f["attribution"] for f in findings if f["result"] == v)
            return _final(v, attr, findings, suggestions, steps)
    return _final("passed", None, findings, suggestions, steps)


def _final(verdict: str, attribution, findings: list, suggestions: list, steps: list) -> dict:
    first = next(({"step": s["step_id"], **s["first_failure"]} for s in steps if s.get("first_failure")), None)
    return {"verdict": verdict, "label": LABEL[verdict], "attribution": attribution,
            "attribution_label": ATTR_LABEL.get(attribution), "findings": findings,
            "suggestions": [{"id": s["id"], "desc": s["desc"], "reason": s.get("reason")} for s in suggestions],
            "recovered": any(s["outcome"] == "passed_after_recovery" for s in steps),
            "adapted": any(s["outcome"] == "passed_after_adaptation" or s.get("adapted") for s in steps),
            "first_anomaly": first}
