"""判定契约。

断言结果：pass / fail / error（明确执行或取证错误）/ pending（判据待确认或主观项）/ undeterminable（证据不足）/ not_run。
用例结论：passed 通过 / failed 失败 / error 执行错误 / blocked 阻塞 / pending 待确认，另记独立产品结论及问题归属。
只有"确定性证据 + 已确认判据"才能得出通过或失败；模型意见（visual_review）永远只进入建议。
"""
from __future__ import annotations

from typing import Callable, Optional

from .tree import UiTree

RANK = {"error": 0, "failed": 1, "blocked": 2, "pending": 3, "passed": 4}
LABEL = {"passed": "通过", "not_applicable": "不涉及", "failed": "失败", "error": "执行错误", "blocked": "阻塞", "pending": "待确认"}
ATTR_LABEL = {"product": "产品", "tool": "工具", "environment": "环境", "requirement_unclear": "需求不明确"}
PASSED = ("passed", "passed_after_adaptation", "passed_after_recovery")
GENERIC_CRASH = "K-GEN-001"


def error_attribution(error_class) -> str:
    return "environment" if error_class in {"device_offline", "transport_unavailable", "screen_off", "screen_locked"} else "tool"


def collection_error(obs: dict) -> dict:
    """只识别采集器明确报告的错误；空树、过时和无法识别前台不等于查询故障。"""
    obs = obs or {}
    tree = obs.get("tree") or {}
    foreground = obs.get("foreground_error") or (obs.get("page") or {}).get("observation_error")
    queries = [q for q in obs.get("foreground_query_errors") or []
               if q.get("error_class") not in (None, "foreground_unknown", "foreground_changed")]
    ec = foreground if foreground not in (None, "foreground_unknown", "foreground_changed") else None
    if queries:
        detail = {k: queries[0].get(k) for k in ("error_class", "error", "raw")}
    elif ec:
        detail = {"error_class": ec, "error": (obs.get("foreground_error_detail") or {}).get("error")}
    elif tree.get("status") == "error" or tree.get("captured_status") == "error":
        detail = {"error_class": tree.get("error_class") or "tree_collection_error", "error": tree.get("error")}
    else:
        return {}
    return {**detail, "stage": "observation", "attribution": error_attribution(detail["error_class"]),
            "evidence": {"obs": obs.get("obs_id"), "ts": obs.get("ts"), "tree": tree,
                         "foreground_query_errors": queries,
                         "foreground_error": foreground, "foreground_error_detail": obs.get("foreground_error_detail")}}


def execution_error(step: dict, stage: str = "step") -> dict:
    """提取最终失败，不把已恢复的首次错误重新升为本次错误。原始记录仍留在 run。"""
    subs = step.get("sub_steps") or []
    if subs:
        failed = next((s for s in reversed(subs) if s.get("outcome") not in PASSED), None)
        if failed:
            return {**execution_error(failed, stage), "parent_step": step.get("step_id")}
    att = (step.get("attempts") or [{}])[-1]
    rec = (step.get("recoveries") or [{}])[-1]
    post = rec.get("post") if rec.get("after_attempt") == att.get("n") else None
    dispatch = att.get("dispatch") or {}
    detail = step.get("execution_error") or (post or {}).get("execution_error") or att.get("execution_error") or {}
    status = step.get("final_status") or att.get("status")
    return {"stage": detail.get("stage") or ("dispatch" if status == "device_error" else
                                             "stop_verification" if status == "stop_not_confirmed" else stage),
            "error_class": detail.get("error_class") or att.get("error_class") or dispatch.get("error_class") or status,
            "error": detail.get("error") or dispatch.get("error"),
            "evidence": {"step": step.get("step_id"), "obs_before": att.get("obs_before"),
                         "obs_after": step.get("final_obs") or att.get("obs_after"),
                         "dispatch": dispatch, "stop_check": att.get("stop_check"),
                         "collection": detail.get("evidence"), "post": post or att.get("post")}}


def _execution_findings(results: list, kind: str, ancestors: tuple = (), cleanup_required=None) -> list:
    """流程完成不消除其非关键子步骤错误；父级复制的错误摘要只投影一次。"""
    findings = []
    for step in results:
        required = step.get("required", True) if kind == "cleanup" and not ancestors else cleanup_required
        nested = _execution_findings(step.get("sub_steps") or [], kind, ancestors + (step["step_id"],), required)
        if nested:
            findings.extend(nested)
        elif step.get("outcome") == "error":
            finding = {"kind": kind, "step": step["step_id"], "result": "error",
                       "attribution": step.get("attribution") or "tool", "reason": step.get("reason"),
                       "mode": step.get("final_status"), **execution_error(step, kind)}
            if step.get("state_diagnostics") is not None:
                finding["evidence"]["state_diagnostics"] = step["state_diagnostics"]
            if ancestors:
                finding.update(parent_step=ancestors[-1], ancestors=list(ancestors))
                finding["evidence"].update(parent_step=ancestors[-1], ancestors=list(ancestors))
            if kind == "cleanup":
                finding["cleanup_required"] = required
            findings.append(finding)
    return findings


def step_oracle_refs(step: dict, inherited=()) -> list:
    """An explicit child rule owns its result; otherwise retain caller context."""
    if step.get("final_status") == "anomaly":
        return [GENERIC_CRASH]
    return list(step.get("oracle_refs") or inherited)


def _result_nodes(results, ancestors=(), inherited=(), oracle_source=None, required=None):
    """Project real results, omitting only explicitly identified propagation copies."""
    for step in results:
        declared = step.get("oracle_refs") or inherited
        source = step["step_id"] if step.get("oracle_refs") else oracle_source
        item_required = step.get("required", True) if not ancestors else required
        children = list(_result_nodes(step.get("sub_steps") or [], ancestors + (step["step_id"],),
                                      declared, source, item_required))
        yield from children
        origin = step.get("failure_source")
        copied = origin and any(child["step_id"] == origin and all(
            child.get(key) == step.get(key) for key in ("outcome", "final_status", "final_obs"))
            for child, *_ in children)
        if not copied:
            refs = step_oracle_refs(step, inherited)
            yield step, ancestors, refs, step["step_id"] if step.get("final_status") == "anomaly" else source, item_required


def _step_findings(steps, knowledge):
    findings = []
    for step, ancestors, refs, source, _ in _result_nodes(steps):
        outcome = step["outcome"]
        base = {"kind": "step", "step": step["step_id"],
                "evidence": {"step": step["step_id"], "obs_after": step.get("final_obs"),
                             "oracle_source": source}}
        if step.get("state_diagnostics") is not None:
            base["evidence"]["state_diagnostics"] = step["state_diagnostics"]
        if ancestors:
            base.update(parent_step=ancestors[-1], ancestors=list(ancestors))
            base["evidence"].update(parent_step=ancestors[-1], ancestors=list(ancestors))
        if outcome == "not_run" and step.get("final_status") == "required_step_skipped":
            base["evidence"]["blocked_by"] = step.get("blocked_by")
            finding = {"result": "pending", "attribution": "requirement_unclear", "reason": step.get("reason")}
        elif outcome in (*PASSED, "not_run", "skipped", "error"):
            continue  # Errors have their own recursive projection and priority.
        elif outcome == "pending":
            base["evidence"]["condition"] = step.get("condition")
            finding = {"result": "pending", "attribution": "requirement_unclear", "reason": step.get("reason")}
        elif outcome == "fail_candidate" and step.get("attribution", "product") == "product":
            oracle = knowledge.oracle_status(refs)
            selected = step.get("oracle_guard") or {}
            if oracle_guards(refs, knowledge) and selected.get("status") != "true":
                base["evidence"]["guard"] = selected
                if selected.get("status") == "error":
                    error = next(x for x in selected.get("records", []) if x["status"] == "error")
                    base["evidence"]["condition_error"] = error.get("evidence")
                    finding = {"result": "error", **{k: error.get(k) for k in
                               ("attribution", "stage", "error_class", "error")}, "reason": selected.get("reason")}
                else:
                    finding = {"result": "pending", "attribution": "requirement_unclear",
                               "reason": selected.get("reason") or "产品步骤判据的场景前置缺少当前独立证据"}
            elif oracle["status"] == "confirmed":
                finding = {"result": "failed", "attribution": "product", "reason": step["reason"],
                           "oracle": oracle, "mode": step.get("final_status")}
            else:
                finding = {"result": "pending", "attribution": "requirement_unclear", "oracle": oracle,
                           "reason": f"{step['reason']}；{oracle['reason']}"}
        else:
            finding = {"result": "blocked", "attribution": step.get("attribution", "tool"),
                       "reason": step.get("reason"), "mode": step.get("final_status")}
        findings.append({**base, **finding})
    return findings


def _stale(obs) -> bool:
    return not obs or obs.get("fresh") is False or (obs.get("tree") or {}).get("status") == "stale"


def _check(a: dict, obs: dict, tree: UiTree, app) -> tuple:
    """返回 (holds: bool|None, evidence: dict)。None 表示证据不足。"""
    t = a["type"]
    ev = {"obs": obs["obs_id"], "tree": obs.get("tree", {}).get("path"), "screenshot": obs.get("screenshot", {}).get("path")}
    if _stale(obs):
        return None, {**ev, "reason": "观测已过时，原始树仅作现场证据，不能用于产品判定"}
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
    entries, gaps, errors = [], [], []
    def usable(obs):
        if _stale(obs):
            return False
        if a["type"] == "state_seen":
            return (obs.get("page") or {}).get("state") not in (None, "undetermined", "ambiguous", "unknown")
        return (obs.get("tree") or {}).get("status") == "ok"

    if mons:
        for m in mons:
            for e in m["timeline"]:
                obs, _ = load(e["obs"])
                error = collection_error(obs)
                if error:
                    errors.append(error)
                elif not usable(obs):
                    gaps.append(e["obs"])
                else:
                    entries.append(e)
    else:
        obs, _ = load(step["final_obs"])
        error = collection_error(obs)
        if error:
            return None, {"execution_error": error, "reason": "所需终态证据采集失败"}
        if not usable(obs):
            return None, {"reason": "终态观测缺失、不可用或已过时，不能判断出现或未出现"}
        entries = [{"t": None, "obs": obs["obs_id"], "state": obs["page"]["state"],
                    "new_texts": obs.get("texts", [])}]
    if a["type"] == "state_seen":
        states = [e["state"] for e in entries]
        seen = any(st == a["state"] or a["state"] in st.split("+") for st in states)
        ev = {"states": states, "expected_state": a["state"], "seen": seen}
    else:
        rx = re.compile(a["pattern"])
        samples = [{"t": e["t"], "obs": e["obs"], "text": x} for e in entries for x in e.get("new_texts", [])
                   if rx.search(x)]
        seen = len({s["obs"] for s in samples}) >= int(a.get("min_count", 1))
        ev = {"pattern": a["pattern"], "seen": seen, "matches": samples[:10],
              "windows": len(mons), "source": "monitor_timeline" if mons else "final_obs"}
    if errors:
        ev["collection_errors"] = errors
        if not seen:
            return None, {**ev, "execution_error": errors[-1], "reason": "监视窗口存在采集错误，不能据缺失样本判断未出现"}
    if gaps:
        ev["stale_samples"] = gaps
        if not seen:
            return None, {**ev, "reason": "监视窗口含过时观测，不能把未观测到当作未出现"}
    return seen == expected, ev


def select_applicability(guards, definitions, steps, load, app, obs, tree) -> dict:
    from .conditions import evaluate_guard
    records = []
    for guard in guards:
        record = evaluate_guard(guard, definitions, steps, load, app, obs, tree)
        if record not in records:
            records.append(record)
    if not records:
        return {}
    expectations = {}
    for record in records:
        if isinstance(record.get("condition"), str) and type(record.get("equals")) is bool:
            expectations.setdefault(record["condition"], set()).add(record["equals"])
    if any(len(values) > 1 for values in expectations.values()) and not any(r["status"] == "error" for r in records):
        return {"status": "unknown", "records": records, "reason": "同一条件的声明要求互相矛盾，不能据此选择不涉及"}
    statuses = {r["status"] for r in records}
    status = next((x for x in ("error", "unknown", "false") if x in statuses), "true")
    primary = next((r for r in records if r["status"] == status), records[0])
    return {**primary, "status": status, "records": records,
            "reason": "；".join(str(r.get("reason", "")) for r in records)}


def oracle_guards(refs, knowledge) -> list:
    guards = []
    for ref in refs:
        requirements = (knowledge.get(ref) or {}).get("condition_requirements") or {}
        if not isinstance(requirements, dict) or any(not isinstance(name, str) or type(value) is not bool
                                                     for name, value in requirements.items()):
            raise ValueError(f"判据 {ref} 的 condition_requirements 须为条件名到布尔值的映射")
        guards.extend({"condition": name, "equals": value} for name, value in requirements.items())
    return guards


def evaluate_assertion(a: dict, steps: dict, load: Callable, knowledge, app, conditions=None) -> dict:
    """先选择适用断言，再评价预先声明的响应；不从响应反推前置。"""
    definitions = conditions if conditions is not None else getattr(app, "meta", {}).get("conditions", {})
    out = {"id": a["id"], "type": a["type"], "desc": a.get("desc", ""),
           "advisory": bool(a.get("advisory")), "oracle_refs": a.get("oracle_refs", []),
           "severity": a.get("severity", "major"), "at": a.get("at"), "verified": False,
           "applicable": None, "facts": dict(knowledge.facts or {})}
    step = steps.get(a.get("at"))
    guards = ([a["guard"]] if "guard" in a else []) + oracle_guards(a.get("oracle_refs", []), knowledge)
    # 旧 read 资产以 measurement 为独立证据，不强加从未需要的 UI 观测。
    if not guards and a["type"] == "value_present" and step is not None and step["outcome"] in PASSED:
        result = _evaluate_applicable(a, steps, load, knowledge, app)
        return {**out, **result, "applicable": True, "verified": not out["advisory"] and result["result"] in ("pass", "fail")}
    if step is None or step["outcome"] not in (*PASSED, "skipped", "pending") or not step.get("final_obs"):
        return {**out, "result": "not_run", "reason": f"关联步骤 {a.get('at')} 未成功执行"}
    obs, tree = load(step["final_obs"])
    selected = select_applicability(guards, definitions, steps, load, app, obs, tree)
    if selected:
        records, status = selected["records"], selected["status"]
        out["guard"] = selected
        if status == "error":
            error = next(r for r in records if r["status"] == "error")
            return {**out, **{k: error.get(k) for k in ("attribution", "stage", "error_class", "error", "evidence")},
                    "result": "error", "reason": selected["reason"]}
        if status == "unknown":
            return {**out, "result": "pending", "reason": selected["reason"]}
        if status == "false":
            return {**out, "applicable": False, "result": "not_applicable", "reason": selected["reason"]}
    if step["outcome"] not in PASSED:
        return {**out, "result": "not_run", "reason": f"关联步骤 {a.get('at')} 未成功执行，操作选支不代替产品断言"}
    result = _evaluate_applicable(a, steps, load, knowledge, app)
    return {**out, **result, "applicable": True, "verified": not out["advisory"] and result["result"] in ("pass", "fail")}


def _evaluate_applicable(a: dict, steps: dict, load: Callable, knowledge, app) -> dict:
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
        return {**out, "result": "pass" if holds else "fail", "observed_holds": holds}
    if a["type"] in ("text_seen", "state_seen"):
        # 时间窗口类：在 monitor 时间线里找（运动中信息、TOR 接管提示、异常显示往往一闪而过，终态里看不到）
        holds, ev = _seen(a, step, load)
        oracle = knowledge.oracle_status(a.get("oracle_refs", []))
        out.update(evidence=ev, oracle=oracle)
        if holds is None:
            if ev.get("execution_error"):
                return {**out, **ev["execution_error"], "result": "error", "reason": ev["reason"]}
            return {**out, "result": "undeterminable", "reason": ev.get("reason", "证据不足以判定")}
        if oracle["status"] != "confirmed":
            return {**out, "result": "pending", "observed_holds": holds,
                    "reason": f"观测{'符合' if holds else '不符合'}预期，但{oracle['reason']}"}
        return {**out, "result": "pass" if holds else "fail", "observed_holds": holds}
    obs, tree = load(step["final_obs"])
    if a["type"] == "visual_review":
        shot = obs.get("screenshot") or {}
        capture_error = {}
        if shot.get("status") == "error":
            capture_error = shot
        elif shot.get("status") == "withheld":
            capture_error = shot.get("capture_error") or {}
        if capture_error:
            error_class = capture_error.get("error_class") or "screenshot_capture_error"
            return {**out, "result": "error", "attribution": error_attribution(error_class),
                    "stage": "screenshot", "error_class": error_class, "error": capture_error.get("error"),
                    "evidence": {"obs": obs["obs_id"], "ts": obs.get("ts"), "screenshot": shot},
                    "reason": "视觉检查所需截图采集失败，不能完成该检查"}
        if shot.get("status") != "ok" or _stale(obs):
            error = collection_error(obs)
            evidence = error.get("evidence") or {}
            foreground = evidence.get("foreground_error")
            if error and (evidence.get("foreground_query_errors") or
                          foreground not in (None, "foreground_unknown", "foreground_changed")):
                return {**out, **error, "result": "error", "reason": "前台查询失败使视觉输入缺失或失效，不能完成该检查"}
        return {**out, "result": "pending", "evidence": {"obs": obs["obs_id"], "screenshot": obs.get("screenshot", {}).get("path")},
                "reason": "主观/视觉检查：只输出建议，需人工或经确认的规则转化为确定性断言", "prompt": a.get("prompt")}
    holds, ev = _check(a, obs, tree, app)
    oracle = knowledge.oracle_status(a.get("oracle_refs", []))
    out.update(evidence=ev, oracle=oracle)
    if holds is None:
        error = collection_error(obs)
        if error:
            return {**out, **error, "result": "error", "reason": "所需观测采集失败，不能给出产品结论"}
        return {**out, "result": "undeterminable", "reason": ev.get("reason", "证据不足以判定")}
    if oracle["status"] != "confirmed":
        return {**out, "result": "pending", "observed_holds": holds,
                "reason": f"观测{'符合' if holds else '不符合'}预期，但{oracle['reason']}"}
    return {**out, "result": "pass" if holds else "fail", "observed_holds": holds}


def case_verdict(preflight: dict, env_check: dict, steps: list, assertions: list, knowledge,
                 safety_stops: Optional[list] = None, cleanup: Optional[list] = None,
                 step_ids: Optional[set] = None) -> dict:
    """step_ids：用例声明的全部步骤 ID，用来区分"断言引用了不存在的步骤"（用例缺陷）和"步骤未执行"。"""
    findings, suggestions, alerts = [], [], []
    def recovery_alerts(results):
        for result in results:
            subs = result.get("sub_steps") or []
            if subs:
                yield from recovery_alerts(subs)
            elif result.get("action_required"):
                yield {"kind": "step_recovery", "trigger": result["step_id"],
                       "action_required": result["action_required"]}
    for s in safety_stops or []:
        if not s.get("confirmed_stopped"):
            # 安全停止没能被观测确认：现场安全问题。它单独成为 alert 并决定结论为阻塞，产品失败仍保留在 findings 里
            alerts.append({"kind": "safety_stop", "trigger": s["trigger"], "action_required": s.get("action_required")
                           or "未能通过观测确认车辆已停止：请现场监护人立即人工接管"})
            findings.append({"kind": "safety_stop", "result": "blocked", "attribution": "environment",
                             "reason": f"安全停止（触发：{s['trigger']}）未能通过观测确认车辆已停止，需现场人工接管"})
    alerts.extend(recovery_alerts(steps + (cleanup or [])))
    if not preflight.get("ok"):
        unlock = preflight.get("unlock") or {}
        if unlock and unlock.get("status") != "unlocked":
            status = unlock.get("status")
            message = {"missing_secret": "本机缺少该设备的有效 PIN 映射或值，请人工核对配置",
                       "pin_rejected": "PIN 被拒，已停止输入，请人工核对 PIN，避免锁机"}.get(
                           status, "解锁状态不可识别，请人工核对：" + (unlock.get("reason") or "原因未知"))
            alerts.append({"kind": "unlock", "status": status, "action_required": message,
                           "reason": unlock.get("reason")})
        dialog = preflight.get("system_dialog") or {}
        if dialog.get("action_required"):
            alerts.append({"kind": "system_dialog", "status": dialog.get("status"),
                           "action_required": "系统弹窗处置可能已执行但未确认关闭，请核对现场后继续",
                           "reason": dialog.get("reason")})
        bad = [c for c in preflight.get("checks", []) if not c["ok"]]
        errors = [c for c in bad if c.get("execution_error") or c.get("error_class") in {
            "device_offline", "transport_unavailable", "tool_missing", "timeout", "command_failed", "tool_execution_error"}]
        result = "error" if errors else "blocked"
        attr = error_attribution((errors[0].get("execution_error") or errors[0]).get("error_class")) if errors else "environment"
        return _final(result, attr, [{"kind": "preflight", "result": result, "attribution": attr,
                                                  "stage": "preflight", "error_class": (errors[0].get("execution_error") or errors[0]).get("error_class") if errors else None,
                                                  "error": (errors[0].get("execution_error") or errors[0]).get("error") if errors else None,
                                                  "evidence": {"checks": bad},
                                                  "reason": "前置检查未通过：" + "、".join(
                                                      f"{c['check']}({c.get('error_class')})" for c in bad),
                                                  "detail": bad}], [], steps, alerts=alerts, assertions=assertions)
    if not env_check.get("ok"):
        return _final("blocked", "environment", [{"kind": "env_requirement", "result": "blocked",
                                                  "attribution": "environment",
                                                  "reason": "环境要求未满足：" + "、".join(
                                                      f"{u['key']}={u['actual']}（需 {u['required']}）" for u in env_check["unmet"]),
                                                  "detail": env_check["unmet"]}], [], steps, assertions=assertions)
    findings.extend(_execution_findings(steps, "step"))
    findings.extend(_execution_findings(cleanup or [], "cleanup"))
    findings.extend(_step_findings(steps, knowledge))
    for a in assertions:
        if a["result"] == "not_applicable" and not a["advisory"]:
            findings.append({"kind": "assertion", "id": a["id"], "result": "not_applicable", "attribution": None,
                             "reason": a.get("reason"), "evidence": a.get("guard")})
            continue
        if a["result"] == "error":
            findings.append({"kind": "assertion", "id": a["id"], "result": "error", "advisory": a["advisory"],
                             **{k: a.get(k) for k in ("attribution", "reason", "stage", "error_class", "error", "evidence")}})
            continue
        if a["advisory"]:
            if a["result"] in ("pending", "fail", "undeterminable"):
                suggestions.append(a)
            continue
        if a["result"] == "fail":
            findings.append({"kind": "assertion", "id": a["id"], "result": "failed", "attribution": "product",
                             "reason": a.get("desc"), "mode": _assertion_mode(a)})
        elif a["result"] in ("pending", "undeterminable"):
            findings.append({"kind": "assertion", "id": a["id"], "result": "pending",
                             "attribution": "requirement_unclear" if a["result"] == "pending" else "tool",
                             "reason": a.get("reason")})
        elif a["result"] == "not_run" and step_ids is not None and a.get("at") not in step_ids:
            # 引用了不存在的步骤（拼写错误或步骤被删）：这是用例缺陷，不能静默忽略
            findings.append({"kind": "assertion", "id": a["id"], "result": "error", "attribution": "tool",
                             "stage": "assertion", "error_class": "invalid_assertion_step", "error": None,
                             "evidence": {"at": a.get("at")}, "reason": f"必需断言引用的步骤 {a.get('at')} 在用例中不存在，断言无法执行"})
        elif a["result"] == "not_run":
            # 关联步骤因前面失败/阻塞而未执行：结论已由那一步决定，这里如实列出"尚未验证"的检查
            findings.append({"kind": "assertion", "id": a["id"], "result": "not_verified", "attribution": None,
                             "reason": a.get("reason")})
    for c, ancestors, _, _, required in _result_nodes(cleanup or []):
        if c["outcome"] == "error":
            continue  # Includes optional cleanup; errors remain independent.
        finding = {"kind": "cleanup", "step": c["step_id"], "cleanup_required": required,
                   "evidence": {"step": c["step_id"], "obs_after": c.get("final_obs")}}
        if c.get("state_diagnostics") is not None:
            finding["evidence"]["state_diagnostics"] = c["state_diagnostics"]
        if ancestors:
            finding.update(parent_step=ancestors[-1], ancestors=list(ancestors))
            finding["evidence"].update(parent_step=ancestors[-1], ancestors=list(ancestors))
        if c["outcome"] == "pending" and ancestors:
            # Preserve existing unknown-branch evidence even in optional cleanup.
            findings.append({**finding, "kind": "step", "result": "pending", "attribution": "requirement_unclear",
                             "reason": c.get("reason"), "evidence": {**finding["evidence"], "condition": c.get("condition")}})
        if c["outcome"] == "not_run" and c.get("final_status") == "safety_hold":
            findings.append({**finding, "result": "not_verified", "attribution": None, "reason": c.get("reason")})
        elif c["outcome"] not in PASSED and required:
            findings.append({**finding, "result": "blocked", "attribution": c.get("attribution") or "tool",
                             "reason": f"必需清理未完成（{c.get('reason') or c['outcome']}），测试数据可能未恢复，需人工核对"})
    results = {f["result"] for f in findings}
    decided = [a for a in assertions if not a["advisory"] and a["result"] in ("pass", "fail")]
    required = [a for a in assertions if not a["advisory"]]
    all_inapplicable = bool(required) and all(a["result"] == "not_applicable" for a in required)
    if not decided and not all_inapplicable and not results & {"error", "failed", "blocked"}:
        findings.append({"kind": "case", "result": "pending", "attribution": "requirement_unclear",
                         "reason": "没有任何非建议类断言得到确定判定，不能判通过"})
        results.add("pending")
    if "not_verified" in results and not results & {"error", "failed", "blocked", "pending"}:
        # 理论上不会出现（没执行的断言一定对应一个未通过的步骤），兜底：有必需检查没执行就不能判通过
        findings.append({"kind": "case", "result": "pending", "attribution": "tool",
                         "reason": "存在未执行的必需断言，不能判通过"})
        results.add("pending")
    if any(a["kind"] != "step_recovery" for a in alerts):
        return _final("blocked", "environment", findings, suggestions, steps, alerts, assertions)
    for v in ("error", "failed", "blocked", "pending"):
        if v in results:
            attr = next(f["attribution"] for f in findings if f["result"] == v)
            return _final(v, attr, findings, suggestions, steps, alerts, assertions)
    if alerts:
        # 恢复交接不改写已有产品/工具/需求归因，但没有其它结论时也不能冒充通过。
        return _final("blocked", "tool", findings, suggestions, steps, alerts, assertions)
    return _final("not_applicable" if all_inapplicable else "passed", None, findings, suggestions, steps, alerts, assertions)


def _final(verdict: str, attribution, findings: list, suggestions: list, steps: list,
           alerts: Optional[list] = None, assertions: Optional[list] = None) -> dict:
    first = next(({"step": s["step_id"], **s["first_failure"]} for s in steps if s.get("first_failure")), None)
    alerts = alerts or []
    not_verified = [{"id": a["id"], "at": a.get("at"), "reason": a.get("reason")} for a in assertions or []
                    if not a["advisory"] and a["result"] == "not_run"]
    not_verified += [{"id": f["step"], "at": f["step"], "kind": "cleanup", "reason": f.get("reason")}
                     for f in findings if f["kind"] == "cleanup" and f["result"] == "not_verified"]
    errors = [f for f in findings if f["result"] == "error"]
    required = [a for a in assertions or [] if not a["advisory"]]
    product_failed = any(f["result"] == "failed" and f.get("attribution") == "product" for f in findings)
    applicable = [a for a in required if a["result"] != "not_applicable"]
    product = ("failed" if product_failed else
               "not_applicable" if required and not applicable else
               "passed" if applicable and all(a["result"] == "pass" for a in applicable)
               and not any(f["result"] == "pending" for f in findings) else "pending")
    coverage_rows = [{"id": a["id"], "result": a["result"], "applicable": a.get("applicable"),
                      "verified": bool(a.get("verified", a["result"] in ("pass", "fail"))),
                      "guard": a.get("guard"), "oracle": a.get("oracle"), "facts": a.get("facts")}
                     for a in required]
    coverage = {"assertions": coverage_rows,
                "verified": [a["id"] for a in coverage_rows if a["verified"]],
                "not_applicable": [a["id"] for a in coverage_rows if a["result"] == "not_applicable"],
                "unverified": [a["id"] for a in coverage_rows if not a["verified"] and a["result"] != "not_applicable"]}
    return {"verdict": verdict, "label": LABEL[verdict], "coverage": coverage,
            "execution": {"status": "error" if errors else ("blocked" if alerts or any(f["result"] == "blocked" for f in findings) else "completed"),
                          "errors": errors},
            "product_verdict": product, "attribution": attribution,
            "attribution_label": ATTR_LABEL.get(attribution), "findings": findings,
            "suggestions": [{"id": s["id"], "desc": s["desc"], "reason": s.get("reason")} for s in suggestions],
            "recovered": any(s["outcome"] == "passed_after_recovery" for s in steps),
            "adapted": any(s["outcome"] == "passed_after_adaptation" or s.get("adapted") for s in steps),
            "first_anomaly": first,
            # 人工接管信号：独立于产品结论，任何消费方都应先看这一项
            "alerts": alerts, "action_required": alerts[0]["action_required"] if alerts else None,
            # 产品层面：有确定失败即为 failed（即使结论因安全停止被提升为阻塞，也不丢）
            "product_failed": any(f["result"] == "failed" and f.get("attribution") == "product" for f in findings),
            "not_verified": not_verified,
            # 失败特征含失败方式（步骤终态 / 断言的观测值）：同一步骤"崩溃"与"后置条件未满足"不是同一个缺陷
            "failure_signature": sorted({f"{f['kind']}:{f.get('step') or f.get('id')}:{f.get('mode')}" for f in findings
                                         if f["result"] == "failed"})}


def _assertion_mode(a: dict) -> str:
    """断言失败方式的稳定特征：观测到的状态 / 实际文字 / 可用性等，没有这些时用断言类型。"""
    ev = a.get("evidence") or {}
    for k in ("observed_state", "actual_text", "enabled", "selected", "seen"):
        if k in ev:
            return f"{a['type']}={ev[k]}"
    return a["type"]
