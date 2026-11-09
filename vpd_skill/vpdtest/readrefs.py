"""Bounded references to successful, qualified reads in this run.

Read text is data: it never becomes a selector, parameter, or another template.
"""
from __future__ import annotations

import re

READ_REF = re.compile(r"\$\{read:([A-Za-z0-9_-]+)\}")


def has_read_ref(value):
    if isinstance(value, str):
        return "${read:" in value
    if isinstance(value, dict):
        return any(has_read_ref(k) or has_read_ref(v) for k, v in value.items())
    if isinstance(value, list):
        return any(has_read_ref(v) for v in value)
    return False


def references(text):
    """Reject malformed references instead of treating them as literal input."""
    if not isinstance(text, str):
        return [], False
    matches = list(READ_REF.finditer(text))
    return [m.group(1) for m in matches], "${read:" not in READ_REF.sub("", text)



def used_sources(case):
    values = [s.get("text") for s in case.get("steps", []) if s.get("action") == "input"]
    for assertion in case.get("assertions", []):
        values.extend((assertion.get("any") or []) if assertion.get("type") == "text_present_any"
                      and isinstance(assertion.get("any"), list) else [assertion.get("text")])
    return {ref for value in values if isinstance(value, str) for ref in references(value)[0]}


def structure_issues(case, app, *, sources=True):
    from .cases import iter_case_steps
    from .safety import TREE_KINDS
    issues, allowed = [], set()
    main = case.get("steps", [])
    positions = {s["id"]: i for i, s in enumerate(main) if isinstance(s.get("id"), str) and s["id"]}
    declared = {s["id"]: s for s in main if isinstance(s.get("id"), str) and s["id"]}

    def consume(value, field, before):
        refs, valid = references(value)
        if not valid:
            issues.append({"field": field, "problem": "read 引用须为 ${read:顶层read步骤ID}"})
        if sources:
            for ref in refs:
                source = declared.get(ref) or {}
                if (source.get("action") != "read" or source.get("read_scope") != "self"
                        or positions.get(ref, len(main)) >= before):
                    issues.append({"field": field, "problem": f"read:{ref} 须引用更早的顶层 read_scope=self 读取步骤"})
                else:
                    target = source.get("target")
                    target = app.targets.get(target) if isinstance(target, str) else target
                    strategies = target.get("strategies") if isinstance(target, dict) else None
                    if (not isinstance(strategies, list) or not strategies
                            or any(not isinstance(s, dict) or s.get("kind") not in TREE_KINDS
                                   or not isinstance(s.get("value"), str) or not s["value"] for s in strategies)):
                        issues.append({"field": field, "problem": f"read:{ref} 须引用带非空 text/id/xpath 策略的目标"})
                    if "pattern" in source:
                        try:
                            re.compile(source["pattern"])
                        except (TypeError, re.error):
                            issues.append({"field": field, "problem": f"read:{ref} 的 pattern 无效"})
        allowed.add(field)

    def scan(value, field):
        if isinstance(value, dict):
            for key, child in value.items():
                if has_read_ref(key):
                    issues.append({"field": field, "problem": "read 引用不能用于字段名"})
                scan(child, f"{field}.{key}" if field else str(key))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                scan(child, f"{field}.{i}")
        elif has_read_ref(value) and field not in allowed:
            issues.append({"field": field, "problem": "read 引用仅支持顶层 input.text 与终态文本断言的 text/any"})

    for i, step in enumerate(main):
        if step.get("action") == "input" and has_read_ref(step.get("text")):
            consume(step["text"], f"steps.{i}.text", i)
    for i, assertion in enumerate(case.get("assertions", [])):
        if assertion.get("type") in ("text_present", "text_absent") and has_read_ref(assertion.get("text")):
            consume(assertion["text"], f"assertions.{i}.text", (positions.get(assertion["at"], -1) if isinstance(assertion.get("at"), str) else -1))
        elif assertion.get("type") == "text_present_any" and isinstance(assertion.get("any"), list):
            if has_read_ref(assertion["any"]) and any(not isinstance(v, str) or not v for v in assertion["any"]):
                issues.append({"field": f"assertions.{i}.any", "problem": "read 引用的 any 须为非空文本列表"})
            for j, value in enumerate(assertion["any"]):
                if has_read_ref(value):
                    consume(value, f"assertions.{i}.any.{j}", (positions.get(assertion["at"], -1) if isinstance(assertion.get("at"), str) else -1))
    scan(case, "")
    # Shared app definitions cannot smuggle read values into a target or child.
    for step, parent, where in iter_case_steps(case, app):
        if parent is not None and has_read_ref(step):
            issues.append({"field": where, "problem": "flow/recipe 子步骤不支持 read 引用"})
        target = step.get("target")
        if isinstance(target, str) and has_read_ref(app.targets.get(target)):
            issues.append({"field": where + ".target", "problem": "目标定义不支持 read 引用"})
    for assertion in case.get("assertions", []):
        for field in ("target", "within"):
            target = assertion.get(field)
            if isinstance(target, str) and has_read_ref(app.targets.get(target)):
                issues.append({"field": f"assertions.{assertion.get('id')}.{field}", "problem": "目标定义不支持 read 引用"})
    return issues


def resolve_text(template, steps, *, subst=None):
    """Resolve once; apply ordinary parameters only to original literal pieces."""
    from .outcomes import PASSED
    refs, valid = references(template)
    if not valid:
        return {"status": "unknown", "reason": "read 引用格式无效", "sources": []}
    sources, values = [], {}
    for ref in dict.fromkeys(refs):
        step = steps.get(ref) or {}
        binding = step.get("read_binding") or {}
        evidence = {"step": ref, "obs": step.get("final_obs"), "measurement": step.get("measurement"),
                    "binding": binding}
        sources.append(evidence)
        if step.get("outcome") == "error":
            from .outcomes import execution_error
            error = execution_error(step, "read_source")
            return {"status": "error", "reason": f"读取来源 {ref} 执行或采集失败", "sources": sources,
                    "execution_error": {**error, "attribution": step.get("attribution", "tool")}}
        if (step.get("action") != "read" or step.get("outcome") not in PASSED
                or binding.get("status") != "available" or binding.get("obs") != step.get("final_obs")):
            return {"status": "unknown", "reason": f"读取来源 {ref} 缺失、未成功或不具备可信绑定资格", "sources": sources}
        value = (step.get("measurement") or {}).get("value")
        if not isinstance(value, str) or not value.strip() or binding.get("value") != value:
            return {"status": "unknown", "reason": f"读取来源 {ref} 不是非空文本值", "sources": sources}
        values[ref] = value
    parts, start = [], 0
    for match in READ_REF.finditer(template):
        literal = template[start:match.start()]
        parts.extend((subst(literal) if subst else literal, values[match.group(1)]))
        start = match.end()
    literal = template[start:]
    parts.append(subst(literal) if subst else literal)
    # Unknown ordinary references in original pieces fail before input. The read
    # value may itself contain any ${...} spelling and remains literal data.
    originals = READ_REF.split(template)[::2]
    if subst and any(re.search(r"\$\{(?:var|param|secret|read):[^}]*\}", subst(piece)) for piece in originals):
        return {"status": "unknown", "reason": "输入原模板存在未解析引用", "sources": sources}
    return {"status": "available", "text": "".join(parts), "sources": sources}


def qualify(tree, loc, step, obs, measurement):
    """Leave historical measurements alone; grant only a strict self-read source."""
    out = {"status": "unavailable", "obs": obs.get("obs_id"), "reason": "读取须来自新鲜、唯一树定位的 self 子树"}
    if (step.get("read_scope") != "self" or loc.get("basis") != "tree"
            or loc.get("status") != "found" or not tree or tree.status != "ok"
            or obs.get("fresh") is False or (obs.get("tree") or {}).get("status") == "stale"):
        return out
    hit = next((a.get("node") for a in loc.get("attempts", []) if a.get("status") == "found"), None)
    if not hit or type(hit.get("idx")) is not int or not 0 <= hit["idx"] < len(tree.nodes):
        return out
    node = tree.nodes[hit["idx"]]
    if node.parent is None or tree.visible_rect(node) != node.bounds or not tree.visible_lineage(node):
        return {**out, "reason": "读取来源是根节点、裁剪节点或含隐藏祖先"}
    descendants = [n for n in tree.nodes if tree.in_scope(n, [node]) and (n.text.strip() or n.description.strip())]
    if any(not tree.visible_lineage(n) or tree.visible_rect(n) != n.bounds for n in descendants):
        return {**out, "reason": "读取子树含隐藏或不完整可见的文案"}
    raw = " ".join(tree.subtree_texts(node))
    pattern = step.get("pattern")
    matches = list(re.finditer(pattern, raw)) if pattern else None
    if matches is not None and len(matches) != 1:
        return {**out, "reason": "读取 pattern 未唯一命中", "matches": len(matches)}
    match = matches[0] if matches else None
    value = (match.group(1) if match.groups() else match.group(0)) if match else raw
    if not isinstance(value, str) or not value.strip() or value != measurement.get("value"):
        return {**out, "reason": "读取值缺失、空白或与唯一可见子树不一致"}
    return {"status": "available", "obs": obs.get("obs_id"), "value": value, "node": node.brief()}
