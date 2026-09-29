"""判定契约。

断言结果：pass / fail / pending（有证据但判据未确认、冲突或属主观项）/ undeterminable（证据不足）/ not_run。
用例结论：passed 通过 / failed 失败 / blocked 阻塞 / pending 待确认，另记问题归属。
只有"确定性证据 + 已确认判据"才能得出通过或失败；模型意见（visual_review）永远只进入建议。
"""
from __future__ import annotations

from typing import Callable, Optional

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
    if t == "text_present_any":
        # 任一文本（子串）出现即成立，例如故障提示的几种可能措辞；措辞本身须来自已确认判据。
        # 只看可见矩形非空的节点：被列表裁到可视区外的条目用户看不到，不能当作"出现了"
        hits = [x for x in a["any"] for n in tree.nodes if tree.visible_rect(n) and x in (n.text or "")]
        ev["matched"] = sorted(set(hits))
        return bool(hits), ev
    if t in ("text_present", "text_absent"):
        hits = tree.find_text(a["text"], exact=a.get("exact", True))
        ev["matches"] = [n.brief() for n in hits[:3]]
        return (bool(hits) if t == "text_present" else not hits), ev
    if t in ("target_present", "target_absent", "target_text_equals", "node_selected", "target_enabled"):
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
            if t == "target_enabled" and a.get("absent_is") == "disabled":
                # 显式声明"控件不存在也算不可用"（例如有故障时整个「开始」按钮不出现）；证据里保留"未找到"
                ev.update(enabled=False, absent=True)
                return a.get("expected", True) is False, ev
            return None, ev
        hit = next((x for x in loc["attempts"] if x.get("status") == "found" and x.get("node")), None)
        node = tree.nodes[hit["node"]["idx"]] if hit else (tree.node_at(*loc["point"]) if loc.get("point") else None)
        if t == "target_enabled":
            # 不点击，只读可用性：例如"有故障时激活按钮不可用"
            if node is None:
                return None, ev
            enabled, e2 = tree.enabled_state(node, target.get("enabled_by"))
            ev.update(e2)
            return (None if enabled is None else enabled == a.get("expected", True)), ev
        if t == "node_selected":
            from .states import is_selected
            sel = is_selected(tree, node) if node else False
            ev["selected"] = sel
            return sel == a.get("expected", True), ev
        actual = node.text if node else None
        ev["actual_text"] = actual
        return actual == a["expected"], ev
    raise ValueError(f"未知断言类型 {t}")


def _monitors(step: dict) -> list:
    out = [step["monitor"]] if step.get("monitor") else []
    for s in step.get("sub_steps", []):
        out += _monitors(s)
    return out


def _seen(a: dict, step: dict, load: Callable) -> tuple:
    """text_seen：pattern 是否在监视时间线里出现过（至少 min_count 次采样）；state_seen：某状态是否出现过。
    expected=false 表示"整个窗口内都不应出现"。没有监视记录时退回到步骤终态观测。"""
    import re
    mons = _monitors(step)
    expected = a.get("expected", True)
    if a["type"] == "state_seen":
        states = [s for m in mons for s in m["states"]] or [load(step["final_obs"])[0]["page"]["state"]]
        holds = any(s == a["state"] or a["state"] in s.split("+") for s in states)
        return holds == expected, {"states": states, "expected_state": a["state"], "seen": holds}
    rx = re.compile(a["pattern"])
    samples = []
    for m in mons:
        samples += [{"t": e["t"], "obs": e["obs"], "text": x} for e in m["timeline"] for x in e.get("new_texts", [])
                    if rx.search(x)]
    if not mons:
        obs, _ = load(step["final_obs"])
        if obs is None:
            return None, {"reason": "没有监视记录也没有终态观测"}
        samples = [{"t": None, "obs": obs["obs_id"], "text": x} for x in obs.get("texts", []) if rx.search(x)]
    seen = len({s["obs"] for s in samples}) >= int(a.get("min_count", 1))
    return seen == expected, {"pattern": a["pattern"], "seen": seen, "matches": samples[:10],
                              "windows": len(mons), "source": "monitor_timeline" if mons else "final_obs"}


def evaluate_assertion(a: dict, steps: dict, load: Callable, knowledge, app) -> dict:
    out = {"id": a["id"], "type": a["type"], "desc": a.get("desc", ""), "advisory": bool(a.get("advisory")),
           "oracle_refs": a.get("oracle_refs", []), "severity": a.get("severity", "major"), "at": a.get("at")}
    step = steps.get(a.get("at"))
    if step is None or step["outcome"] not in PASSED:
        return {**out, "result": "not_run", "reason": f"关联步骤 {a.get('at')} 未成功执行"}
    if a["type"] == "value_present":
        # 读数类：步骤 read 取到的值存在且符合格式；读数本身作为测量结果输出，不与任何"期望距离"比较
        import re
        m = step.get("measurement") or {}
        holds = m.get("value") is not None and (not a.get("pattern") or bool(re.fullmatch(a["pattern"], str(m["value"]))))
        oracle = knowledge.oracle_status(a.get("oracle_refs", []))
        out.update(evidence={"measurement": m}, oracle=oracle)
        if oracle["status"] != "confirmed":
            return {**out, "result": "pending", "observed_holds": holds, "reason": f"读数{'有效' if holds else '无效'}，但{oracle['reason']}"}
        return {**out, "result": "pass" if holds else "fail"}
    if a["type"] in ("text_seen", "state_seen"):
        # 时间窗口类：在 monitor 时间线里找（运动中信息、TOR 接管提示、异常显示往往一闪而过，终态里看不到）
        holds, ev = _seen(a, step, load)
        oracle = knowledge.oracle_status(a.get("oracle_refs", []))
        out.update(evidence=ev, oracle=oracle)
        if holds is None:
            return {**out, "result": "undeterminable", "reason": ev.get("reason", "证据不足以判定")}
        if oracle["status"] != "confirmed":
            return {**out, "result": "pending", "observed_holds": holds,
                    "reason": f"观测{'符合' if holds else '不符合'}预期，但{oracle['reason']}"}
        return {**out, "result": "pass" if holds else "fail"}
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


def case_verdict(preflight: dict, env_check: dict, steps: list, assertions: list, knowledge,
                 safety_stops: Optional[list] = None, cleanup: Optional[list] = None,
                 step_ids: Optional[set] = None) -> dict:
    """step_ids：用例声明的全部步骤 ID，用来区分"断言引用了不存在的步骤"（用例缺陷）和"步骤未执行"。"""
    findings, suggestions, alerts = [], [], []
    for s in safety_stops or []:
        if not s.get("confirmed_stopped"):
            # 安全停止没能被观测确认：现场安全问题。它单独成为 alert 并决定结论为阻塞，产品失败仍保留在 findings 里
            alerts.append({"kind": "safety_stop", "trigger": s["trigger"], "action_required": s.get("action_required")
                           or "未能通过观测确认车辆已停止：请现场监护人立即人工接管"})
            findings.append({"kind": "safety_stop", "result": "blocked", "attribution": "environment",
                             "reason": f"安全停止（触发：{s['trigger']}）未能通过观测确认车辆已停止，需现场人工接管"})
    if not preflight.get("ok"):
        bad = [c for c in preflight.get("checks", []) if not c["ok"]]
        return _final("blocked", "environment", [{"kind": "preflight", "result": "blocked", "attribution": "environment",
                                                  "reason": "前置检查未通过：" + "、".join(
                                                      f"{c['check']}({c.get('error_class')})" for c in bad),
                                                  "detail": bad}], [], steps, assertions=assertions)
    if not env_check.get("ok"):
        return _final("blocked", "environment", [{"kind": "env_requirement", "result": "blocked",
                                                  "attribution": "environment",
                                                  "reason": "环境要求未满足：" + "、".join(
                                                      f"{u['key']}={u['actual']}（需 {u['required']}）" for u in env_check["unmet"]),
                                                  "detail": env_check["unmet"]}], [], steps, assertions=assertions)
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
        elif a["result"] == "not_run" and step_ids is not None and a.get("at") not in step_ids:
            # 引用了不存在的步骤（拼写错误或步骤被删）：这是用例缺陷，不能静默忽略
            findings.append({"kind": "assertion", "id": a["id"], "result": "blocked", "attribution": "tool",
                             "reason": f"必需断言引用的步骤 {a.get('at')} 在用例中不存在，断言无法执行"})
        elif a["result"] == "not_run":
            # 关联步骤因前面失败/阻塞而未执行：结论已由那一步决定，这里如实列出"尚未验证"的检查
            findings.append({"kind": "assertion", "id": a["id"], "result": "not_verified", "attribution": None,
                             "reason": a.get("reason")})
    for c in cleanup or []:
        if c["outcome"] not in PASSED and c.get("required", True):
            # 清理没完成：账号/车辆数据可能没恢复，整体不能判通过
            findings.append({"kind": "cleanup", "step": c["step_id"], "result": "blocked",
                             "attribution": c.get("attribution") or "tool",
                             "reason": f"必需清理未完成（{c.get('reason') or c['outcome']}），测试数据可能未恢复，需人工核对"})
    results = {f["result"] for f in findings}
    decided = [a for a in assertions if not a["advisory"] and a["result"] in ("pass", "fail")]
    if not decided and not results & {"failed", "blocked"}:
        findings.append({"kind": "case", "result": "pending", "attribution": "requirement_unclear",
                         "reason": "没有任何非建议类断言得到确定判定，不能判通过"})
        results.add("pending")
    if "not_verified" in results and not results & {"failed", "blocked", "pending"}:
        # 理论上不会出现（没执行的断言一定对应一个未通过的步骤），兜底：有必需检查没执行就不能判通过
        findings.append({"kind": "case", "result": "pending", "attribution": "tool",
                         "reason": "存在未执行的必需断言，不能判通过"})
        results.add("pending")
    if alerts:
        return _final("blocked", "environment", findings, suggestions, steps, alerts, assertions)
    for v in ("failed", "blocked", "pending"):
        if v in results:
            attr = next(f["attribution"] for f in findings if f["result"] == v)
            return _final(v, attr, findings, suggestions, steps, alerts, assertions)
    return _final("passed", None, findings, suggestions, steps, alerts, assertions)


def _final(verdict: str, attribution, findings: list, suggestions: list, steps: list,
           alerts: Optional[list] = None, assertions: Optional[list] = None) -> dict:
    first = next(({"step": s["step_id"], **s["first_failure"]} for s in steps if s.get("first_failure")), None)
    alerts = alerts or []
    return {"verdict": verdict, "label": LABEL[verdict], "attribution": attribution,
            "attribution_label": ATTR_LABEL.get(attribution), "findings": findings,
            "suggestions": [{"id": s["id"], "desc": s["desc"], "reason": s.get("reason")} for s in suggestions],
            "recovered": any(s["outcome"] == "passed_after_recovery" for s in steps),
            "adapted": any(s["outcome"] == "passed_after_adaptation" or s.get("adapted") for s in steps),
            "first_anomaly": first,
            # 人工接管信号：独立于产品结论，任何消费方都应先看这一项
            "alerts": alerts, "action_required": alerts[0]["action_required"] if alerts else None,
            # 产品层面：有确定失败即为 failed（即使结论因安全停止被提升为阻塞，也不丢）
            "product_failed": any(f["result"] == "failed" and f.get("attribution") == "product" for f in findings),
            "not_verified": [{"id": a["id"], "at": a.get("at"), "reason": a.get("reason")} for a in assertions or []
                             if not a["advisory"] and a["result"] == "not_run"],
            "failure_signature": sorted({f"{f['kind']}:{f.get('step') or f.get('id')}" for f in findings
                                         if f["result"] == "failed"})}
