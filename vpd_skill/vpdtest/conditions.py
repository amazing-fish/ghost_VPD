"""Small, evidence-bound conditions shared by operation selection and assertion guards.

A condition has explicit positive and counter evidence. Failure to find positive
proof is unknown, never counter proof. Declarations such as --env are not read.
All current providers are page-derived and expire on a possibly dispatched action.
"""
from __future__ import annotations

import math
import re
from json import JSONDecodeError

KINDS = {"page", "account", "vehicle", "task"}
CHECKS = {"text_present", "text_present_any", "target_present", "state_is"}
REQUIRED_SCOPE = {"page": set(), "account": {"account"}, "vehicle": {"vehicle"}, "task": {"vehicle", "task"}}


def definitions(case: dict, app):
    """Merge valid containers; preserve malformed input for structural validation."""
    registered, local = app.meta.get("conditions", {}), case.get("conditions", {})
    if not isinstance(registered, dict):
        return registered
    if not isinstance(local, dict):
        return local
    return {**registered, **local}


def _tree_target_problems(ref, app, field):
    from .safety import TREE_KINDS
    target = app.targets.get(ref) if isinstance(ref, str) and ref else None
    strategies = target.get("strategies") if isinstance(target, dict) else None
    if (not isinstance(strategies, list) or not strategies
            or any(not isinstance(strategy, dict) or strategy.get("kind") not in TREE_KINDS
                   or not isinstance(strategy.get("value"), str) or not strategy["value"].strip()
                   for strategy in strategies)):
        return [{"field": field, "problem": "条件目标须引用已登记且仅含非空 text/id/xpath 策略的树目标"}]
    return []


def _check_problems(check, app, field) -> list:
    issues = []
    def bad(message):
        issues.append({"field": field, "problem": message})
    if not isinstance(check, dict) or not isinstance(check.get("type"), str) or check["type"] not in CHECKS:
        bad("条件证据只允许 text_present/text_present_any/target_present/state_is 独立肯定型检查")
        return issues
    typ = check["type"]
    allowed = {"type", "text", "exact"} if typ == "text_present" else {
        "text_present_any": {"type", "any"}, "target_present": {"type", "target"}, "state_is": {"type", "state"}}[typ]
    if set(check) - allowed:
        bad(f"条件检查不支持字段 {sorted(set(check) - allowed)}")
    if typ in {"text_present", "state_is"}:
        value = check.get("text" if typ == "text_present" else "state")
        if not isinstance(value, str) or not value:
            bad("条件检查须提供明确的非空文本/状态")
    if "exact" in check and type(check["exact"]) is not bool:
        bad("exact 须为布尔值")
    if typ == "text_present_any" and (not isinstance(check.get("any"), list) or not check["any"]
                                      or not all(isinstance(t, str) and t for t in check["any"])):
        bad("text_present_any.any 须为非空文本列表")
    if typ == "target_present":
        issues += _tree_target_problems(check.get("target"), app, field)
    if typ == "state_is" and isinstance(check.get("state"), str) and check["state"] not in getattr(getattr(app, "states", None), "states", {}):
        bad(f"条件状态 {check['state']!r} 未在 states.json 登记")
    return issues


def validate_definitions(defs: dict, app) -> list:
    if not isinstance(defs, dict):
        return [{"field": "conditions", "problem": "conditions 须为命名条件定义对象"}]
    issues = []
    for name, spec in defs.items():
        prefix = f"conditions.{name}"
        if not isinstance(name, str) or not name.strip() or not isinstance(spec, dict):
            issues.append({"field": prefix, "problem": "条件须有非空名称与对象定义"})
            continue
        if set(spec) - {"kind", "true", "false", "scope", "max_age_s"}:
            issues.append({"field": prefix, "problem": f"条件定义不支持字段 {sorted(set(spec) - {'kind', 'true', 'false', 'scope', 'max_age_s'})}"})
        kind = spec.get("kind")
        if not isinstance(kind, str) or kind not in KINDS:
            issues.append({"field": prefix + ".kind", "problem": "kind 须为 page/account/vehicle/task"})
        for side in ("true", "false"):
            issues += _check_problems(spec.get(side), app, prefix + "." + side)
        scope = spec.get("scope", {})
        if not isinstance(scope, dict):
            issues.append({"field": prefix + ".scope", "problem": "scope 须为身份目标引用对象"})
        else:
            for key in REQUIRED_SCOPE.get(kind if isinstance(kind, str) else "", set()) - set(scope):
                issues.append({"field": prefix + ".scope", "problem": f"{spec.get('kind')} 条件须绑定当前 {key} 身份目标"})
            for key, target in scope.items():
                if key not in {"vehicle", "task", "account"} or not isinstance(target, str) or target not in app.targets:
                    issues.append({"field": prefix + ".scope." + str(key), "problem": "身份须由已登记 UI 目标读取"})
                else:
                    issues += _tree_target_problems(target, app, prefix + ".scope." + str(key))
        if "max_age_s" in spec:
            age = spec["max_age_s"]
            if type(age) not in (int, float) or not math.isfinite(age) or age <= 0:
                issues.append({"field": prefix + ".max_age_s", "problem": "有效期须为正有限秒数；不设置统一默认超时"})
    return issues


def validate_guard(guard: dict, defs: dict, step_ids: set | None = None) -> list:
    if not isinstance(defs, dict):
        return [{"field": "guard", "problem": "conditions 容器无效，无法解析条件引用"}]
    if not isinstance(guard, dict):
        return [{"field": "guard", "problem": "guard/when 须为命名条件引用对象"}]
    issues = []
    if set(guard) - {"condition", "equals", "at"}:
        issues.append({"field": "guard", "problem": "guard/when 只支持 condition/equals/at"})
    if not isinstance(guard.get("condition"), str) or guard["condition"] not in defs:
        issues.append({"field": "guard.condition", "problem": f"条件 {guard.get('condition')!r} 未定义"})
    if type(guard.get("equals")) is not bool:
        issues.append({"field": "guard.equals", "problem": "equals 须为布尔值"})
    if "at" in guard and (not isinstance(guard["at"], str) or not guard["at"] or
                          (step_ids is not None and guard["at"] not in step_ids)):
        issues.append({"field": "guard.at", "problem": f"at={guard.get('at')!r} 须引用已定义步骤的终态观测"})
    return issues


def validate_input_obstruction(step: dict, defs: dict) -> list:
    """One current, positively identified container may authorize one input-only back recovery."""
    field = "dismiss_obstruction_when"
    if field not in step:
        return []
    guard = step[field]
    issues = [{**item, "field": field + "." + item["field"]} for item in validate_guard(guard, defs)]
    if step.get("action") != "input":
        issues.append({"field": field, "problem": "仅 input 支持遮挡恢复"})
    if not isinstance(guard, dict) or guard.get("equals") is not True or "at" in guard:
        issues.append({"field": field, "problem": "须为当前观测的 equals:true 条件，不支持历史 at"})
    name = guard.get("condition") if isinstance(guard, dict) else None
    spec = defs.get(name, {}) if isinstance(defs, dict) and isinstance(name, str) else {}
    positive = spec.get("true") if isinstance(spec, dict) else None
    if not isinstance(positive, dict) or positive.get("type") != "target_present":
        issues.append({"field": field, "problem": "正证须以 target_present 标识实际遮挡容器"})
    return issues


def _visible_evidence(tree, node) -> bool:
    """Only a complete, explicitly visible current-tree node may prove a condition."""
    return bool(node is not None and node.parent is not None and node.area
                and tree.visible_rect(node) == node.bounds and tree.visible_lineage(node))


def _target_evidence(ref, obs, tree, app, stage):
    from .locate import resolve
    evidence = {"target": ref, "locate": "unavailable"}
    if tree is None or tree.status != "ok":
        return None, evidence, None
    try:
        tid, target = app.target(ref)
        located = resolve(tid, target, tree, None, app.dir, layout=obs.get("layout"))
    except (TypeError, ValueError) as exc:
        error = {"stage": stage, "attribution": "tool", "error_class": "condition_locator_error",
                 "error": str(exc), "evidence": evidence}
        return None, evidence, error
    evidence.update(locate=located.get("status"), attempts=located.get("attempts", []))
    if located.get("status") == "error":
        error = located.get("execution_error") or {
            "stage": stage, "attribution": "tool", "error_class": "condition_locator_error",
            "error": located.get("attempts"), "evidence": evidence}
        return None, evidence, error
    if located.get("status") != "found":
        return None, evidence, None
    hit = next((a.get("node") for a in located.get("attempts", []) if a.get("status") == "found"), None)
    index = hit.get("idx") if isinstance(hit, dict) else None
    node = tree.nodes[index] if type(index) is int and 0 <= index < len(tree.nodes) else None
    evidence["node"] = hit
    if not _visible_evidence(tree, node):
        evidence["reason"] = "条件目标不是非根、完整可见且无隐藏祖先的当前树节点"
        return None, evidence, None
    return node, evidence, None


def _state_evidence(check, obs, tree, app):
    page, sid = obs.get("page") or {}, check["state"]
    evidence = {"obs": obs.get("obs_id"), "observed_state": page.get("state")}
    if page.get("state") in (None, "unknown", "ambiguous", "undetermined", "out_of_app"):
        return None, evidence
    if sid not in [page.get("state"), *(page.get("active") or [])]:
        return False, evidence
    model = app.states
    spec = model.states[sid]
    if not any(spec.get(key) for key in ("require_text", "require_any_text", "require_text_pattern",
                                        "require_selected_text", "require_target")):
        return None, {**evidence, "reason": "条件状态缺少独立肯定签名"}
    if tree is None or tree.status != "ok":
        return None, {**evidence, "reason": "条件状态所需UI树不可用"}
    if spec.get("require_target"):
        _, located, error = _target_evidence(spec["require_target"], obs, tree, app, "condition_evidence")
        evidence["anchor"] = located
        if error:
            return None, {**evidence, "execution_error": error}
    layout = obs.get("layout") or {}
    panes, reported = layout.get("panes") or [], page.get("panes") or []
    scopes = [None]
    if len(panes) > 1:
        current = {p.get("node_idx") for p in panes if type(p.get("node_idx")) is int
                   and 0 < p["node_idx"] < len(tree.nodes)}
        known = {p.get("node_idx") for p in reported if type(p.get("node_idx")) is int}
        if len(current) != len(panes) or len(reported) != len(panes) or known != current:
            return None, {**evidence, "reason": "条件状态分栏归属与当前布局不一致"}
        scopes = [[tree.nodes[p["node_idx"]]] for p in reported if sid in (p.get("candidates") or [])]
    elif reported:
        return None, {**evidence, "reason": "条件状态分栏缺少当前布局证明"}
    try:
        matched = any(model._matches(spec, tree, scope, layout,
                                    node_filter=lambda n: _visible_evidence(tree, n)) for scope in scopes)
    except (TypeError, ValueError, re.error) as exc:
        error = {"stage": "condition_evidence", "attribution": "tool", "error_class": "condition_state_error",
                 "error": str(exc), "evidence": evidence}
        return None, {**evidence, "execution_error": error}
    return (True if matched else None), {**evidence, "signature_proven": matched}


def _check_evidence(check, obs, tree, app):
    """Condition proof is stricter than historical whole-page product assertions."""
    typ = check["type"]
    if typ == "state_is":
        return _state_evidence(check, obs, tree, app)
    evidence = {"obs": obs.get("obs_id")}
    if tree is None or tree.status != "ok":
        return None, {**evidence, "reason": "条件所需UI树不可用"}
    if typ == "target_present":
        node, located, error = _target_evidence(check["target"], obs, tree, app, "condition_evidence")
        evidence["locate"] = located
        if error:
            return None, {**evidence, "execution_error": error}
        # Ambiguity and partial visibility are missing proof, not counterproof.
        if located["locate"] in ("ambiguous", "partially_visible") or located.get("reason"):
            return None, evidence
        return node is not None, evidence
    visible = [node for node in tree.nodes if _visible_evidence(tree, node)]
    if typ == "text_present":
        text, exact = check["text"], check.get("exact", True)
        hits = [node for node in visible if
                ((text == node.text or text == node.description) if exact else
                 (text in node.text or text in node.description))]
        return bool(hits), {**evidence, "matches": [node.brief() for node in hits[:3]]}
    hits = [text for text in check["any"] for node in visible if text in node.text]
    return bool(hits), {**evidence, "matched": sorted(set(hits))}


def _identity(spec: dict, obs: dict, tree, app) -> tuple:
    scope = dict(obs.get("scope") or {})
    evidence = {}
    if scope.get("app") != app.id or not scope.get("app_version"):
        return scope, evidence, "APP/build 当前实测作用范围缺失或不符", None
    for key, ref in (spec.get("scope") or {}).items():
        node, located, error = _target_evidence(ref, obs, tree, app, "condition_identity")
        evidence[key] = located
        if error:
            return scope, evidence, f"当前 {key} 身份定位解析失败", error
        if node is None:
            return scope, evidence, f"当前 {key} 身份不能唯一、完整可见地读取", None
        value = node.text.strip()
        evidence[key]["value"] = value
        if not value:
            return scope, evidence, f"当前 {key} 身份目标没有可核对文本", None
        scope[key] = value
    return scope, evidence, None, None


def evaluate_guard(guard: dict, defs: dict, steps: dict, load, app, obs: dict, tree) -> dict:
    """Evaluate against a target observation, optionally using a same-epoch source.

    status=true/false describes the guard expression, value is the measured fact.
    Unknown, stale, conflict and errors never authorize a skipped product check.
    """
    from .verdict import _stale, collection_error
    referenced = guard if isinstance(guard, dict) else {}
    record = {"condition": referenced.get("condition"), "equals": referenced.get("equals"),
              "status": "unknown", "value": None, "source": "observation", "target_obs": (obs or {}).get("obs_id"),
              "obs": (obs or {}).get("obs_id"), "ts": (obs or {}).get("ts"),
              "scope": dict((obs or {}).get("scope") or {}), "action_epoch": (obs or {}).get("action_epoch")}
    problems = validate_guard(guard, defs)
    name = referenced.get("condition")
    spec = defs.get(name) if isinstance(defs, dict) and isinstance(name, str) else None
    if isinstance(defs, dict) and isinstance(name, str) and name not in defs and name and type(referenced.get("equals")) is bool and not (set(referenced) - {"condition", "equals", "at"}):
        return {**record, "reason": f"条件 {name!r} 尚无当前观测提供者，需继续取证"}
    if isinstance(defs, dict) and isinstance(name, str) and name in defs:
        problems += validate_definitions({name: spec}, app)
    if problems:
        error = {"stage": "condition_definition", "attribution": "tool", "error_class": "invalid_condition",
                 "error": problems, "evidence": {"guard": guard}}
        return {**record, **error, "status": "error", "reason": "条件定义无效", "execution_error": error}
    source, source_tree = obs, tree
    if guard.get("at"):
        step = steps.get(guard["at"]) or {}
        if step.get("outcome") not in ("passed", "passed_after_adaptation", "passed_after_recovery") or not step.get("final_obs"):
            return {**record, "reason": "前置证据来源步骤尚未成功执行", "at": guard["at"]}
        try:
            source, source_tree = load(step["final_obs"])
        except FileNotFoundError:
            return {**record, "reason": "前置来源观测缺失，不能判为不涉及", "at": guard["at"]}
        except (JSONDecodeError, OSError) as exc:
            error = {"stage": "condition", "attribution": "tool", "error_class": "condition_evidence_load_error",
                     "error": str(exc), "evidence": {"obs": step["final_obs"], "at": guard["at"]}}
            return {**record, **error, "status": "error", "reason": "前置来源观测读取或解析失败", "execution_error": error}
    record.update(max_age_s=spec.get("max_age_s"), observation_source=(source or {}).get("source"),
                  obs=(source or {}).get("obs_id"), ts=(source or {}).get("ts"),
                  action_epoch=(source or {}).get("action_epoch"), clock_s=(source or {}).get("clock_s"),
                  scope=dict((source or {}).get("scope") or {}), at=guard.get("at"))
    for item in (source, obs):
        error = collection_error(item or {})
        if error:
            error = {**error, "stage": "condition"}
            return {**record, **error, "status": "error", "reason": "条件所需观测采集失败", "execution_error": error}
    if _stale(source) or _stale(obs):
        return {**record, "reason": "条件观测缺失或已过时"}
    if type(source.get("action_epoch")) is not int or source["action_epoch"] != obs.get("action_epoch"):
        return {**record, "reason": "条件证据已被可能下发的动作失效，须重新取证"}
    clock, current = source.get("clock_s"), obs.get("clock_s")
    if (type(clock) not in (int, float) or type(current) not in (int, float)
            or not math.isfinite(clock) or not math.isfinite(current) or current < clock):
        return {**record, "reason": "条件观测时间缺失或不可比较"}
    if spec.get("max_age_s") is not None and current - clock > spec["max_age_s"]:
        return {**record, "reason": "条件证据已超过该事实定义的有效期", "age_s": current - clock}
    scope, identity, reason, error = _identity(spec, source, source_tree, app)
    record.update(scope=scope, identity=identity, kind=spec["kind"])
    if error:
        return {**record, **error, "status": "error", "reason": reason, "execution_error": error}
    if reason:
        return {**record, "reason": reason}
    target_scope, target_identity, reason, error = _identity(spec, obs, tree, app)
    record["target_scope"] = target_scope
    if error:
        return {**record, **error, "status": "error", "reason": reason, "execution_error": error,
                "target_identity": target_identity}
    if reason or scope != target_scope:
        return {**record, "reason": reason or "条件来源与目标的 APP/build/车辆/任务/账户范围不同", "target_identity": target_identity}
    positive, positive_ev = _check_evidence(spec["true"], source, source_tree, app)
    negative, negative_ev = _check_evidence(spec["false"], source, source_tree, app)
    record["checks"] = {"true": {"holds": positive, "evidence": positive_ev}, "false": {"holds": negative, "evidence": negative_ev}}
    error = positive_ev.get("execution_error") or negative_ev.get("execution_error")
    if error:
        return {**record, **error, "status": "error", "reason": "条件肯定证据定位或解析失败", "execution_error": error}
    if positive is None or negative is None:
        return {**record, "reason": "独立前置证据无法确定"}
    if positive and negative:
        return {**record, "reason": "条件正证与独立反证冲突"}
    if not positive and not negative:
        return {**record, "reason": "既没有满足证据，也没有独立不满足证据"}
    value = bool(positive)
    if source.get("obs_id") != obs.get("obs_id"):
        current_true, true_evidence = _check_evidence(spec["true"], obs, tree, app)
        current_false, false_evidence = _check_evidence(spec["false"], obs, tree, app)
        record["current_checks"] = {"true": {"holds": current_true, "evidence": true_evidence},
                                    "false": {"holds": current_false, "evidence": false_evidence}}
        error = true_evidence.get("execution_error") or false_evidence.get("execution_error")
        if error:
            return {**record, **error, "status": "error", "reason": "当前条件肯定证据定位或解析失败", "execution_error": error}
        if current_true is None or current_false is None:
            return {**record, "reason": "当前条件证据不能确定"}
        if (current_true and current_false) or (value and current_false) or (not value and current_true):
            return {**record, "reason": "当前观测与历史条件证据矛盾，须重新确认"}
    return {**record, "status": "true" if value is guard["equals"] else "false", "value": value,
            "reason": "当前独立证据满足条件" if value is guard["equals"] else "当前独立证据明确不满足条件"}
