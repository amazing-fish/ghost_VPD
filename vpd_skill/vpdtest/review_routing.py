"""Conservative review routing, not device authorization or an identity service."""
import re
import json
import hashlib

from .knowledge import NOT_ORACLE_KINDS, item_status
from .util import sha256_obj


def route(ws, case, knowledge, app, policy, evidence_run=None):
    from . import cases as C
    reasons, engineering = [], []
    def result(kind, *extra, **fields):
        return {"route": kind, "reasons": reasons + list(extra), **fields}
    if C._withdrawal(case, case.get("asset_review"))[1]:
        reasons.append("已有审核被撤销，须人工决定是否恢复")
    if C.oracle_lineage_issues(case, knowledge):
        reasons.append("判据与被审资产同源，不能由观察证明预期")
    refs, _, _ = C._condition_references(case, app, knowledge)
    conflicts = {kid for ids in knowledge.conflicts().values() for kid in ids}
    if not refs:
        engineering.append("缺少判据引用，先由 Agent 查找已有独立要求")
    for ref in sorted(refs):
        item = knowledge.get(ref) or {}
        if item_status(item) != "confirmed" or item.get("kind") in NOT_ORACLE_KINDS or ref in conflicts:
            reasons.append(f"判据 {ref} 未确认、无效或冲突")
    if case.get("authorizations"):
        reasons.append("用例声明受控副作用，超出自主导航复核范围")
    # Expand flows/recipes, cleanup and safe-stop, not only top-level steps.
    allowed = {"launch", "stop_app", "back", "home", "resume", "wait", "tap", "swipe", "flow", "ensure_state"}
    try:
        for step, _, where in C.iter_case_steps(case, app, strict=True):
            if step.get("action") not in allowed:
                engineering.append(f"{where}: 动作未纳入当前导航分流器，先由 Agent 分析，不能自动批准")
            if step.get("action") == "tap":
                target = step.get("target")
                target = app.targets.get(target) if isinstance(target, str) else target
                effect = target.get("side_effect") if isinstance(target, dict) else None
                if effect in policy.authorizable:
                    reasons.append(f"{where}: 点击涉及受控副作用 {effect}")
                elif effect != "navigation":
                    engineering.append(f"{where}: 点击目标作用未分类，先由 Agent 补证据而非找用户签字")
    except (ValueError, TypeError, KeyError) as exc:
        engineering.append(f"执行结构无法展开：{exc}")
    if reasons:
        return result("human_required")
    if engineering:
        return result("evidence_needed", *engineering)
    issues = C.lint(case, app, knowledge, policy, runs_dir=ws.runs_dir)
    if issues:
        return result("evidence_needed", "先由 Agent 修复定义或补证据", issues=issues)
    if not evidence_run:
        return result("evidence_needed", "需指定当前定义的真机运行证据；无需因此请求用户签字")
    if not isinstance(evidence_run, str) or not re.fullmatch(r"run-[A-Za-z0-9_-]+", evidence_run):
        return result("evidence_needed", "evidence-run 须为本工作区 run ID，不接受路径")
    path = (ws.runs_dir / evidence_run / "run.json").resolve()
    if not path.is_relative_to(ws.runs_dir.resolve()) or not path.is_file():
        return result("evidence_needed", "运行原件不在本工作区或尚未留存")
    try:
        # Validate and hash exactly the same bytes; never reread an unchecked replacement.
        manifest, captured = {}, {}
        root = path.parent
        def source(relative):
            if relative in captured:
                return captured[relative]
            candidate = (root / relative).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError("证据路径越出运行目录")
            raw = candidate.read_bytes()
            if not raw:
                raise ValueError(f"证据为空：{relative}")
            captured[relative] = raw
            manifest[relative] = hashlib.sha256(raw).hexdigest()
            return raw
        run = json.loads(source("run.json"))
        current = C._review_binding(ws, case, app, knowledge, policy)
        old = run.get("asset_review", {}).get("current", {})
        definition = run.get("case", {})
        if (run.get("run_id") != evidence_run or run.get("validation_level") != "real_device"
                or definition.get("id") != case["id"] or definition.get("hash") != current["content_hash"]
                or definition.get("deps") != current["dependency_hash"]
                or any(old.get(k) != current[k] for k in ("policy_hash", "context_hash"))):
            return result("evidence_needed", "运行不是当前内容/依赖/环境配置的真机证据")
        if run.get("verdict", {}).get("verdict") not in {"passed", "failed"}:
            return result("evidence_needed", "运行尚无确定结论；不得把 pending/blocked/error 变成通过")
        verdict = run["verdict"]
        if (verdict.get("execution", {}).get("status") != "completed" or C.has_execution_error(verdict)
                or verdict.get("alerts") or verdict.get("not_verified")):
            return result("evidence_needed", "执行未完整结束或存在告警/未验证项，先由 Agent 排查")
        actual = {a.get("id"): a for a in run.get("assertions", [])}
        referenced = set()
        def observations(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"obs", "final_obs", "obs_before", "obs_after"} and isinstance(child, str):
                        referenced.add(child)
                    observations(child)
            elif isinstance(value, list):
                for child in value:
                    observations(child)
        observations(run.get("steps", []))
        observations(run.get("cleanup", []))
        for assertion in case.get("assertions", []):
            if assertion.get("advisory"):
                continue
            row = actual.get(assertion.get("id"), {})
            if (row.get("oracle_refs") != assertion.get("oracle_refs")
                    or row.get("verified") is not True or row.get("result") not in {"pass", "fail"}
                    or row.get("oracle", {}).get("status") != "confirmed"):
                return result("evidence_needed", f"断言 {assertion.get('id')} 缺已验证、适用的判据证据")
            ev = row.get("evidence", {})
            oid = ev.get("obs")
            if not isinstance(oid, str) or not re.fullmatch(r"obs-[0-9]+", oid):
                return result("evidence_needed", f"断言 {assertion.get('id')} 缺观测原件引用")
            referenced.add(oid)
            observation = json.loads(source(f"obs/{oid}/observation.json"))
            if (observation.get("obs_id") != oid or observation.get("source") != "hdc"
                    or any(observation.get(k, {}).get("status") != "ok" for k in ("tree", "screenshot"))
                    or any(ev.get(k) != observation.get(k, {}).get("path") for k in ("tree", "screenshot"))):
                return result("evidence_needed", f"断言 {assertion.get('id')} 原始观测不完整或引用不匹配")
            status = knowledge.oracle_status(assertion.get("oracle_refs", []), facts=row.get("facts") or {})
            if status["status"] != "confirmed":
                return result("evidence_needed", f"断言 {assertion.get('id')} 当前适用性未成立：{status['status']}")
        # Bind the observations used by steps/cleanup as well as assertions, and raw event trail.
        for oid in sorted(referenced):
            if not re.fullmatch(r"obs-[0-9]+", oid):
                return result("evidence_needed", "非法观测引用")
            observation = json.loads(source(f"obs/{oid}/observation.json"))
            if observation.get("obs_id") != oid:
                return result("evidence_needed", "观测 ID 与引用不匹配")
            for kind in ("tree", "screenshot"):
                # Pre-action/out-of-app observations may legitimately have no capture.
                # Their metadata is bound; assertion observations above must be complete.
                if observation.get(kind, {}).get("status") != "ok":
                    continue
                relative = observation.get(kind, {}).get("path")
                if (not isinstance(relative, str) or not relative.startswith(f"obs/{oid}/")
                        or not (root / relative).resolve().is_relative_to(root / "obs" / oid)):
                    return result("evidence_needed", f"观测 {oid} 缺少 {kind} 原件路径")
                raw = source(relative)
                if kind == "tree":
                    json.loads(raw)
        for line in source("events.jsonl").splitlines():
            json.loads(line)
        return result("independent_agent", "独立判据已确认且在证据范围适用；仅导航动作；当前真机原件完整",
                      evidence_run=evidence_run, evidence_sha256=sha256_obj(manifest))
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        return result("evidence_needed", f"运行原件不可验证：{exc}")
