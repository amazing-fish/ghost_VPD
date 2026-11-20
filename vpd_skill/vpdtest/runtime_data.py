"""Explicit run-only scalar fixtures; never selectors, policy, or measured facts."""
from copy import deepcopy
import re
import unicodedata

REF = re.compile(r"\$\{(?:var|secret):([A-Za-z0-9_.-]+)\}")


def declarations(case):
    return {k: v for k, v in (case.get("test_data") or {}).items()
            if isinstance(v, dict) and "runtime" in v}


def structure_issues(case, app):
    declared = declarations(case)
    if not declared:
        return []
    issues, allowed = [], set()
    for key, spec in declared.items():
        if set(spec) != {"runtime"} or spec.get("runtime") is not True or not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
            issues.append({"field": f"test_data.{key}", "problem": '运行参数须声明为 {"runtime": true}'})
    for i, step in enumerate(case.get("steps", [])):
        if step.get("action") == "input":
            allowed.add(("steps", i, "text"))
    for i, assertion in enumerate(case.get("assertions", [])):
        field = {"target_text_equals": "expected", "text_present": "text", "text_absent": "text"}.get(assertion.get("type"))
        if field and (assertion.get("type") == "target_text_equals" or assertion.get("exact") is True):
            allowed.add(("assertions", i, field))
    used = set()

    def walk(value, path):
        if isinstance(value, str):
            found = set(REF.findall(value)) & declared.keys()
            if found:
                used.update(found)
                if path not in allowed or len(found) != 1 or value != "${var:" + next(iter(found)) + "}":
                    issues.append({"field": ".".join(map(str, path)), "problem": "运行参数仅支持顶层 input.text 或文字等值断言的完整 ${var:name} 引用"})
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(k, path + ("<key>",))
                walk(v, path + (k,))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, path + (i,))
    walk(case, ())
    # No indirect use via targets, flows, recipes, signatures or interrupts.
    walk(app.targets, ("app", "targets"))
    walk(app.flows, ("app", "flows"))
    walk(app.states.recipes, ("app", "recipes"))
    walk(app.states.states, ("app", "states"))
    walk(app.states.interrupts, ("app", "interrupts"))
    for key in declared.keys() - used:
        issues.append({"field": f"test_data.{key}", "problem": "运行参数没有受支持的消费位置"})
    return issues


def prepare(case, values=None):
    """Validate before device creation. Values are declarations, not independent evidence."""
    declared = declarations(case)
    values = {} if values is None else values
    if not isinstance(values, dict) or set(values) != set(declared):
        raise ValueError("运行参数须完整且仅包含声明的字段：" + ", ".join(sorted(declared)))
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 512 or "${" in value or any(unicodedata.category(c) in {"Cc", "Cf"} for c in value):
            raise ValueError(f"运行参数 {key} 须为非空、无模板或控制字符的字符串（最多512字符）")
    return dict(values)


def bind_assertion(assertion, values):
    out = deepcopy(assertion)
    field = "expected" if out.get("type") == "target_text_equals" else "text"
    for key, value in values.items():
        if out.get(field) == "${var:" + key + "}":
            out[field] = value
    return out
