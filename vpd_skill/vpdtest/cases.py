"""用例生命周期：exploration_record → candidate → replay_verified → regression。

- 探索来源晋级和批量运行前须有效资产审核；转候选还须补齐目的、前置、数据、步骤、判据、清理、来源。
- 判定依据必须是独立的知识条目；不能用"探索时看到了什么"来证明"应该是什么"。
- replay_verified 需要当前内容和依赖版本下至少 2 次不同运行的确定、一致重放（失败须登记缺陷）；
  regression 还需评审人、全部判据已确认，默认要求至少 1 次真机重放。
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Optional

from . import review_snapshot
from .knowledge import ACTIVE, item_status
from .safety import TREE_KINDS, untreed_strategies
from .session import relative_scope_target, session_lock, swipe_options_error, swipe_scope_definition_error
from .util import now_iso, read_json, sha256_file, sha256_obj, write_json

REQUIRED = ("purpose", "preconditions", "test_data", "steps", "assertions", "cleanup", "sources")
CONTENT_KEYS = ("app", "preconditions", "env_requirements", "test_data", "steps", "assertions", "cleanup")
# 只在声明时计入，避免改变既有用例的内容哈希。授权与安全停止决定实际会产生哪些物理副作用，改了就要重新重放
OPTIONAL_CONTENT_KEYS = ("layout_requirements", "authorizations", "safety", "conditions")
# Issue #14：obs 编号属于单次运行（重放后编号会变）。裸引用会让判定证据链指向不存在的观测
# （真机上出现过：rationale 引 obs-0010/0012，而本 run 实际是 obs-0007/0008）。
# 但「run 目录名/会话名 + obs 编号」是可解析引用：锚点目录确实留存了该观测时证据链可追溯，允许。
# (?<![A-Za-z0-9_-]) 排除复合标识里的片段——K-QK-OBS-201 是知识 ID（稳定标识），不是观测编号。
OBS_REF_RE = re.compile(r"(?<![A-Za-z0-9_-])obs-\d{3,6}\b", re.IGNORECASE)
RUN_ANCHOR_RE = re.compile(r"\b(run-\d{8}-\d{6}-[0-9a-z]+|session-[0-9a-z_-]+)", re.IGNORECASE)
# Source identities are complete, portable directory names, never text fragments.
SOURCE_ID_RE = re.compile(r'[^/\\:*?"<>|\x00-\x1f\x7f-\x9f]+(?<![. ])')
SOURCE_RUN_RE = re.compile(r"run-[0-9]{8}-[0-9]{6}-[0-9a-z]+")
NEXT = {"exploration_record": "candidate", "draft": "candidate", "candidate": "replay_verified",
        "replay_verified": "regression"}


def unresolvable_obs_refs(text: str, runs_dir: Optional[Path] = None) -> list:
    """rationale 里的 obs 引用哪些不可解析（check-refs）。

    - 无 run/session 锚点 → 不可解析（裸编号，重放后必过期）；
    - 有锚点且显式给出 runs_dir（签发审核/证据诊断）→ 锚点目录必须真实存在且包含该观测，否则不可解析
      （防止「随手加个 run- 前缀」绕过：真机缺陷正是锚点 run 里根本没有被引的那个编号）；
    - 有锚点但没给 runs_dir → 只能核对形式，暂不判不可解析。
    """
    text = text or ""
    problems = []
    # 每个 obs 引用归属其前方最近的锚点（“run-A obs-1；run-B obs-2”各归各），不做全文笛卡尔匹配——
    # 否则错引能在其它 run 留存同名观测时蒙混过关（review P2：跨 run 错配）。
    anchors = [(m.start(), m.group(1).lower()) for m in RUN_ANCHOR_RE.finditer(text)]
    for m in OBS_REF_RE.finditer(text):
        prior = [a for s, a in anchors if s < m.start()]
        if not prior:
            problems.append(f"{m.group(0)}：无 run/session 锚点（引用须写在锚点之后）")
        elif runs_dir is not None and not (runs_dir / prior[-1] / "obs" / m.group(0).lower()).is_dir():
            problems.append(f"{m.group(0)}：锚点 {prior[-1]} 未留存该观测（目录不存在或缺 obs）")
    return problems


def content_hash(case: dict) -> str:
    body = {k: case.get(k) for k in CONTENT_KEYS}
    body.update({k: case[k] for k in OPTIONAL_CONTENT_KEYS if case.get(k)})
    return sha256_obj(body)[:12]


def _condition_references(case: dict, app, knowledge=None, *, strict=False):
    """Actual guards and direct Oracle requirements share one provider selection."""
    from .conditions import definitions
    oracles, names = set(), set()
    def collect_refs(row, where):
        refs = row.get("oracle_refs")
        if strict and refs is not None and (not isinstance(refs, list)
                                           or any(not isinstance(ref, str) or not ref for ref in refs)):
            raise ValueError(f"{where}.oracle_refs 须为列表，元素须为非空知识 ID 字符串")
        oracles.update(refs or [])

    for step, _, where in iter_case_steps(case, app, strict=strict):
        for guard in (step.get("when"), step.get("dismiss_obstruction_when")):
            if isinstance(guard, dict) and isinstance(guard.get("condition"), str):
                names.add(guard["condition"])
        collect_refs(step, where)
    for index, assertion in enumerate(case.get("assertions", [])):
        guard = assertion.get("guard")
        if isinstance(guard, dict) and isinstance(guard.get("condition"), str):
            names.add(guard["condition"])
        collect_refs(assertion, f"assertions.{assertion.get('id', index)}")
    if knowledge is not None:
        for ref in oracles:
            requirements = (knowledge.get(ref) or {}).get("condition_requirements")
            if isinstance(requirements, dict):
                names.update(requirements)
    providers = definitions(case, app)
    selected = {name: providers.get(name) for name in sorted(names)} if isinstance(providers, dict) else providers
    return oracles, names, selected


def _provider_target_refs(value):
    if isinstance(value, dict):
        yield value.get("target")
        if isinstance(value.get("scope"), dict):
            yield from value["scope"].values()
        for child in value.values():
            yield from _provider_target_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _provider_target_refs(child)


def knowledge_references(ws, kid: str) -> dict:
    """Read current case declarations for a confirmation response; never run or rewrite assets."""
    out = {"scope": "workspace_case_declarations", "status": "complete", "cases": [], "errors": [],
           "tests": "not_scanned",
           "note": "当前 cases/*.json 的显式引用，用于选择后续核对范围；运行判定、复验需求和审核状态沿原门禁计算"}
    apps = {}
    try:
        paths = sorted((ws.root / "cases").glob("*.json"))
    except Exception as exc:
        out.update(status="incomplete", errors=[{"case": None, "error": str(exc)}])
        return out
    for path in paths:
        try:
            case = read_json(path)
            app_id = case["app"]
            if app_id not in apps:
                apps[app_id] = ws.app(app_id)
            refs, _, _ = _condition_references(case, apps[app_id], strict=True)
            uses = ["oracle_refs"] if kid in refs else []
            if any(entry and entry.get("id") == kid for entry in case.get("knowledge_used") or []):
                uses.append("knowledge_used")
            if uses:
                out["cases"].append({"case": case.get("id", path.stem), "app": app_id, "uses": uses})
        except Exception as exc:
            out["status"] = "incomplete"
            out["errors"].append({"case": path.stem, "error": str(exc)})
    return out


# 结果语义版本：步骤结果、断言或整例结论的判定规则一旦改变，就把它加 1。
# 它进入 dependency_hash，让旧重放和旧人审显式失效；纯重构、注释和与判定无关的改动不改它。
RESULT_CONTRACT = 7


def dependency_hash(case: dict, app, knowledge) -> str:
    """用例引用的外部定义的指纹：content_hash 只覆盖用例文件本身，但目标选择器、展开后的 flow/recipe 步骤、
    页面状态/打断定义、判据语义或其直接冲突项、被引用模板字节改了，旧重放不再作数。
    有效判据的确认状态、历史不计入（确认之后不必重新重放）；废弃/无效使旧重放失效。
    result_contract 绑定结果语义，旧引擎的阻塞/产品候选不能充当新契约的确定重放。"""
    from .params import Params
    from .readrefs import has_read_ref

    steps, targets, templates = [], {}, {}
    oracles, used_conditions, conditions = _condition_references(case, app, knowledge)
    # Never load secrets or evaluate run-generated data just to fingerprint files.
    data = {k: v for k, v in (case.get("test_data") or {}).items()
            if not (isinstance(v, dict) and ("secret" in v or "template" in v))}
    params = Params(data, {}, "dependency")

    def want(t, local=None, substitute=False):
        if isinstance(t, str) and t in app.targets:
            targets[t] = app.targets[t]
            t = app.targets[t]
        if isinstance(t, dict):
            if substitute:
                t = params.subst(t, local)  # same single substitution as Executor._target
            for strategy in t.get("strategies", []):
                if substitute and "${" in str(strategy.get("kind", "")):
                    raise ValueError("模板定位策略无法静态绑定：请使用固定策略类型或普通静态参数")
                if strategy.get("kind") == "template" and isinstance(strategy.get("file"), str):
                    file = strategy["file"]
                    if substitute and "${" in file:
                        raise ValueError("模板路径无法静态绑定：请使用固定文件名或普通静态参数，不支持 secret/template 生成值")
                    path = (app.dir / file).resolve()
                    templates[file] = sha256_file(path) if path.is_file() else None

    for s, parent, where in iter_case_steps(case, app):
        steps.append([where, {k: v for k, v in s.items() if k != "_lint_cycle"}])
        local = (params.subst(parent.get("params", {})) if parent and parent.get("action") == "flow"
                 else s.get("_flow_params"))
        want(s.get("target"), local, substitute=True)
        want(relative_scope_target(s), local, substitute=True)
    for a in case.get("assertions", []):
        want(a.get("target"))
        want(a.get("within"))
    facts = {}
    for kid, item in sorted(knowledge.dependency_items(oracles).items()):
        keys = ("expected", "applies_to", "condition_requirements")
        if kid in oracles:
            keys += ("statement", "kind", "subject")
        facts[kid] = {k: item.get(k) for k in keys}
        if item_status(item) not in ACTIVE:
            facts[kid]["inactive"] = True
    for provider in conditions.values() if isinstance(conditions, dict) else []:
        for ref in _provider_target_refs(provider):
            want(ref)
    for state in app.states.states.values():
        want(state.get("require_target"))
        want(state.get("navigation_context_target"))
    for interrupt in app.states.interrupts.values():
        want(interrupt.get("dismiss_target"))
        want(interrupt.get("dismiss_target"), substitute=True)  # recovery uses _attempt → _target
    return sha256_obj({"steps": steps, "targets": targets, "states": app.states.states,
                       "interrupts": app.states.interrupts, "oracles": facts, "conditions": conditions,
                       **({"templates": templates} if templates else {}), "result_contract": RESULT_CONTRACT,
                       **({"condition_evidence_contract": 1} if used_conditions else {}),
                       **({"read_references_contract": 1} if has_read_ref(case) else {})})[:12]


# 审核覆盖审阅语义；成熟度/运行记录不使正常晋级或新重放自失效。
REVIEW_METADATA = {"status", "history", "replays", "product_status", "asset_review"}
EXPLORATION_ORIGINS = {"session", "exploration", "template"}


def exploration_signals(case: dict) -> list:
    signals = []
    if case.get("status") == "exploration_record":
        signals.append("status:exploration_record")
    origin = case.get("origin")
    if isinstance(origin, dict) and isinstance(origin.get("type"), str) and origin["type"] in EXPLORATION_ORIGINS:
        signals.append("origin:" + origin["type"])
    for source in case.get("sources") or []:
        if isinstance(source, dict) and isinstance(source.get("type"), str) and source["type"] in EXPLORATION_ORIGINS:
            signals.append("source:" + source["type"])
    for entry in case.get("history") or []:
        if isinstance(entry, dict) and "exploration_record" in (entry.get("from"), entry.get("to")):
            signals.append("history:exploration_record")

    def visit(value):
        if isinstance(value, dict):
            for key in ("from_session", "from_transition"):
                if key in value:
                    signals.append("step:" + key)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit([case.get("steps"), case.get("cleanup"), (case.get("safety") or {}).get("safe_stop")])
    return sorted(set(signals))


def review_context(ws, app) -> dict:
    """检查和执行共用配置快照；模板只绑定文件指纹，运行期间须保持源码/模板不变。"""
    templates_dir = app.dir / "templates"
    templates = {p.relative_to(app.dir).as_posix(): sha256_file(p) for p in sorted(templates_dir.rglob("*")) if p.is_file()}
    return {"app": app.meta, "layout": ws.layout_config(),
            "system_dialogs": read_json(ws.root / "system_dialogs.json", {}), "templates": templates}


def _review_binding(ws, case: dict, app, knowledge, policy, context=None) -> dict:
    """人审绑定的是用例、依赖、策略和配置；引擎源码不在其中。

    结果语义的变化通过 RESULT_CONTRACT 进入 dependency_hash，显式地让旧审核和旧重放失效；
    纯重构、注释或无关模块的改动不再让全部人审过期。引擎文件差异仍由 engine_snapshot 诊断展示。
    """
    context = review_context(ws, app) if context is None else context
    return {"case_id": case["id"], "content_hash": content_hash(case),
            "dependency_hash": dependency_hash(case, app, knowledge),
            "review_hash": sha256_obj({k: v for k, v in case.items() if k not in REVIEW_METADATA}),
            "policy_hash": policy.hash, "context_hash": sha256_obj(context)}


def _complete_review(record) -> bool:
    complete = isinstance(record, dict) and type(record.get("schema")) is int and record["schema"] == 1 and all(
        isinstance(record.get(k), str) and record[k].strip() for k in ("reviewer", "basis", "ts"))
    if complete:
        try:
            complete = datetime.fromisoformat(record["ts"]).utcoffset() is not None
        except ValueError:
            complete = False
    return bool(complete)


def _withdrawal(case: dict, record) -> tuple:
    """Withdrawal persists through relabelling or removal/restoration of an old review.

    A later matching approval event can renew it; this is local audit history, not
    authentication or protection against deliberately rewriting both record and history.
    """
    history = case.get("history") or []
    events = [(i, h) for i, h in enumerate(history)
              if isinstance(h, dict) and h.get("event") == "asset_review_revoked"]
    marked = isinstance(record, dict) and "revocation" in record
    if not events:
        return marked, marked, record.get("revocation") if marked else None
    index, event = events[-1]
    keys = ("schema", "reviewer", "basis", "ts", "binding")
    renewed = isinstance(record, dict) and any(
        isinstance(h, dict) and h.get("event") == "asset_review"
        and all(h.get(k) == record.get(k) for k in keys) for h in history[index + 1:])
    revoked = marked or not renewed
    return True, revoked, ({k: event.get(k) for k in ("reviewer", "basis", "ts")} if revoked else None)


def review_status(ws, case: dict, knowledge=None, app=None, policy=None, context=None) -> dict:
    from .knowledge import KnowledgeStore
    app = app or ws.app(case["app"])
    knowledge = knowledge or KnowledgeStore(ws.root)
    policy = policy or ws.policy()
    signals = exploration_signals(case)
    files = review_snapshot.engine_files(Path(__file__).parent)
    binding = _review_binding(ws, case, app, knowledge, policy, context)
    record = case.get("asset_review")
    complete = _complete_review(record)
    withdrawal_required, revoked, revocation = _withdrawal(case, record)
    lineage_issues = oracle_lineage_issues(case, knowledge)
    required = bool(signals) or withdrawal_required or bool(lineage_issues)
    valid = bool(complete and not revoked and record.get("binding") == binding and not lineage_issues)
    evidence_verification = None
    if complete and record.get("review_kind") == "agent":
        from .review_routing import definition_blocker
        # Approval is portable: recheck its binding and static rules, not the
        # availability of the original workstation's files. New approval still
        # verifies evidence in approve(); explicit review-route diagnoses raw.
        valid = bool(valid and definition_blocker(ws, case, knowledge, app, policy) is None
                     and isinstance(record.get("independent_ref"), str) and record["independent_ref"].strip()
                     and isinstance(record.get("evidence_run"), str)
                     and re.fullmatch(r"run-[A-Za-z0-9_-]+", record["evidence_run"])
                     and isinstance(record.get("evidence_sha256"), str)
                     and re.fullmatch(r"[0-9a-f]{64}", record["evidence_sha256"]))
        evidence_verification = {"status": "not_checked", "affects_review_validity": False,
                                 "reason": "复用既有审核只核绑定与静态规则，不要求本机原件；review-route可单独诊断原件"}
    state = ("revoked" if revoked else "approved" if valid else
             "stale" if complete else "pending" if required else "not_required")
    # Keep the potentially large manifest in the asset, not every run/suite brief.
    record_view = {k: v for k, v in record.items() if k != "engine_snapshot"} if isinstance(record, dict) else None
    return {"required": required, "valid": valid, "state": state, "origin_signals": signals,
            "current": binding, "record": record_view,
            "engine_changes": review_snapshot.engine_changes(record, files),
            **({"evidence_verification": evidence_verification} if evidence_verification else {}),
            **({"lineage_issues": lineage_issues} if lineage_issues else {}),
            **({"revocation": revocation} if revoked else {})}


def require_review(ws, case: dict, knowledge=None, app=None, policy=None, context=None) -> dict:
    if case.get("status", "draft") not in {*NEXT, "regression"}:
        raise PermissionError(f"用例 {case.get('id')} 状态无效，不能晋级或批量执行")
    info = review_status(ws, case, knowledge, app, policy, context)
    # Provenance metadata is not a replay/review hash input. Recheck current known
    # self-origin even when a previous approval still has a matching binding.
    issues = info.get("lineage_issues")
    if issues:
        raise PermissionError(f"用例判据不独立，不能晋级或批量执行：{issues}")
    if info["required"] and not info["valid"]:
        if info["state"] == "revoked":
            raise PermissionError(f"用例 {case['id']} 的资产审核已撤销；请由人重新审查后执行 case approve "
                                  …9384 tokens truncated…gin": {"type": "exploration"},
            "title": f"[探索生成] {' → '.join([chain[0]['from']] + [t['to'] for t in chain])}",
            "purpose": "", "preconditions": [f"APP 处于 {chain[0]['from']}"], "env_requirements": {},
            "test_data": {}, "steps": steps, "assertions": [], "cleanup": [],
            "sources": [{"type": "exploration", "run": exp["run_id"], "transitions": transitions,
                         "obs": [o for t in chain for o in (t["obs_before"], t["obs_after"])]}],
            "history": [{"ts": now_iso(), "to": "exploration_record", "why": "由探索迁移生成，待补齐目的/判据/清理"}]}


def _replay_difference_fields(left: dict, right: dict) -> list:
    """Only the top-level ingestion timestamp is disposable, not nested evidence.

    Canonical JSON preserves missing/null and bool/number distinctions that Python
    equality would collapse. Diagnostics expose field names, never evidence values.
    """
    return [key for key in sorted((left.keys() | right.keys()) - {"ts"})
            if key not in left or key not in right or sha256_obj(left[key]) != sha256_obj(right[key])]


def _replay_conflict_reason(conflicts: list) -> str:
    details = "; ".join(f"{c['run_id']} ({', '.join(c['fields'])})" for c in conflicts)
    return f"同一 run_id 的重放证据冲突：{details}；不能作为晋级依据，请核对原始运行材料"


@_case_update
def record_replay(ws, case_id: str, run: dict) -> dict:
    case = ws.load_case(case_id)
    v = run["verdict"]
    summary = {
        "run_id": run["run_id"], "verdict": v["verdict"], "hash": run["case"]["hash"], "deps": run["case"].get("deps"),
        "validation_level": run["validation_level"], "device": run["device_spec"], "ts": now_iso(),
        "failure_signature": v.get("failure_signature", []), "alerts": len(v.get("alerts") or []),
        "not_verified": len(v.get("not_verified") or []), "facts": run.get("facts"),
        "oracle_facts": run.get("oracle_facts"),
        **{k: run[k] for k in ("started", "finished") if k in run},
        "assertions": [{k: a[k] for k in ("id", "result", "advisory", "oracle_refs", "oracle", "facts", "guard", "verified")
                        if k in a} for a in run.get("assertions", [])],
        **{k: v[k] for k in ("execution", "product_verdict", "product_failed", "coverage") if k in v}}
    proof = run.get("preflight_evidence")
    if (_empty_preflight_proof(proof)
            and type(run.get("actions")) is int and run["actions"] == 0
            and run.get("cleanup") == [] and run.get("safety_stops") == []
            and "safety_hold" in run and run["safety_hold"] is None
            and isinstance(run.get("steps"), list) and bool(run["steps"])
            and all(s.get("outcome") == "not_run" and s.get("attempts") == []
                    and s.get("recoveries") == [] and not s.get("final_obs") for s in run["steps"])
            and isinstance(run.get("assertions"), list)
            and all(a.get("result") == "not_run" for a in run["assertions"])):
        summary["preflight_evidence"] = proof.copy()
    previous = [r for r in case.get("replays", []) if r["run_id"] == summary["run_id"]]
    compared = summary
    if previous and all(not ({"started", "finished"} & row.keys()) for row in previous):
        # Only a wholly legacy history permits this no-write retry. A mixed
        # history must keep strict comparison, including every present interval.
        compared = {k: v for k, v in summary.items() if k not in ("started", "finished")}
    if previous and all("preflight_evidence" not in row for row in previous):
        # Idempotent legacy retry must not retroactively reclassify recorded runs.
        compared = {k: v for k, v in compared.items() if k != "preflight_evidence"}
    fields = sorted({field for r in previous for field in _replay_difference_fields(r, compared)})
    if fields:
        raise ValueError(_replay_conflict_reason([{"run_id": summary["run_id"], "fields": fields}]))
    if previous:
        return case  # Idempotent retry: retain first position and original bytes.
    case.setdefault("replays", []).append(summary)
    write_json(ws.case_path(case_id), case)
    return case


def has_execution_error(summary: dict) -> bool:
    """读取执行层记录；兼容历史摘要，不把产品失败或正常阻塞推断成工具故障。"""
    execution = summary.get("execution") or {}
    return (summary.get("verdict") == "error" or execution.get("status") == "error"
            or bool(execution.get("errors"))
            or any(f.get("result") == "error" for f in summary.get("findings") or []))


def _empty_preflight_proof(proof) -> bool:
    expected = {"schema": 1, "case_execution_started": False, "dispatch_count": 0,
                "product_observations": 0}
    return (isinstance(proof, dict) and proof == expected
            and all(type(proof[k]) is type(value) for k, value in expected.items()))


def _preflight_only_replay(row: dict) -> bool:
    """Only explicit runner evidence exempts an unexecuted attempt, never missing fields."""
    return (row.get("verdict") == "blocked" and not has_execution_error(row)
            and not row.get("alerts") and not row.get("product_failed")
            and row.get("product_verdict") != "failed"
            and _empty_preflight_proof(row.get("preflight_evidence"))
            and all(a.get("result") == "not_run" for a in row.get("assertions") or [])
            and all(a.get("result") == "not_run" for a in (row.get("coverage") or {}).get("assertions") or []))


def _replay_instant(value):
    """Only timezone-aware source times establish an instant, never array order."""
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value)
        return instant if instant.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _replay_interval(row: dict) -> tuple:
    if not ({"started", "finished"} & row.keys()):
        return None, None, None  # Historical summary: no invented start/finish.
    start, finish = (_replay_instant(row.get(k)) for k in ("started", "finished"))
    if start is None or finish is None:
        return None, None, "started/finished 缺失或不是含时区的有效时间"
    if finish < start:
        return None, None, "finished 早于 started，运行区间倒退"
    return start, finish, None


def _replays_after_resets(candidates: list, resets: list) -> tuple:
    """A new run starts strictly after every reset finishes (no total order needed).

    For a wholly untimed legacy reset only, its valid ingestion time is a
    conservative completion upper bound, not an actual finish. Unknown bounds,
    equal timestamps and overlaps cannot establish a subsequent run.
    """
    if not resets:
        return candidates, {}
    bounds, unbounded, legacy = [], [], []
    for row in resets:
        _, finish, error = _replay_interval(row)
        if finish is None and error is None:
            finish = _replay_instant(row.get("ts"))
            if finish is not None:
                legacy.append(row["run_id"])
        if finish is None:
            unbounded.append(row["run_id"])
        else:
            bounds.append(finish)
    boundary = max(bounds) if bounds and not unbounded else None
    accepted, missing, not_after = [], [], []
    for row in candidates:
        start, _, error = _replay_interval(row)
        if start is None or error:
            missing.append(row["run_id"])
        elif boundary is not None and start > boundary:
            accepted.append(row)
        else:
            not_after.append(row["run_id"])
    return accepted, {"resets": [r["run_id"] for r in resets], "unbounded": unbounded,
                      "legacy_upper_bounds": legacy, "missing_intervals": missing, "not_after": not_after}


def _replay_time_reason(timing: dict) -> str:
    parts = ["须以运行 started 严格晚于全部重置运行的完成上界证明先后；相同时间或重叠不算后续"]
    if timing.get("unbounded"):
        parts.append(f"重置运行缺少有效完成上界 {timing['unbounded']}，须核对原始材料")
    if timing.get("missing_intervals"):
        parts.append(f"候选运行缺少有效时间区间 {timing['missing_intervals']}")
    if timing.get("legacy_upper_bounds"):
        parts.append(f"旧摘要录入 ts 仅用作保守完成上界 {timing['legacy_upper_bounds']}，不是实际 finished")
    return "；".join(parts)


def _assertion_replay_coverage(case: dict, rows: list, history: list | None = None) -> dict:
    """Every branch must have its own current, decisive evidence; aggregate pass is insufficient.

    Historical unconditional assets without assertion summaries retain their original
    interpretation. Matching dependency hashes remain mandatory at the caller.
    Explicit N/A adds no evidence, while an unknown/error/not-run occurrence resets
    that assertion's consecutive verified evidence without changing stored history.
    """
    required = [a for a in case.get("assertions", []) if not a.get("advisory")]
    conditional = bool(case.get("conditions")) or any(a.get("guard") for a in required)
    conditional = conditional or any(s.get("when") for s in case.get("steps", []))
    history = rows if history is None else history
    recorded = any("coverage" in row for row in history)
    if not conditional and not recorded:
        return {"ok": True, "coverage": {}}
    evidence, gaps = {}, []
    for assertion in required:
        aid, candidates, resets = assertion["id"], [], []
        eligible = {row["run_id"] for row in rows}
        all_results = set()
        for row in history:
            if _preflight_only_replay(row):
                continue
            coverage = row.get("coverage") or {}
            actual = next((a for a in coverage.get("assertions") or [] if a.get("id") == aid), None)
            if (actual and actual.get("verified") is True and actual.get("applicable") is True
                    and actual.get("result") in ("pass", "fail") and not has_execution_error(row)):
                if row["run_id"] in eligible:
                    candidates.append(row)
                    all_results.add(actual["result"])
            elif actual and actual.get("result") == "not_applicable" and actual.get("applicable") is False:
                continue
            else:
                resets.append(row)
        verified, timing = _replays_after_resets(candidates, resets)
        evidence[aid] = {"count": len(verified), "runs": [a["run_id"] for a in verified],
                         "results": sorted(all_results)}
        if timing:
            evidence[aid]["timing"] = timing
        if len(verified) < 2:
            gaps.append(f"{aid} 仅 {len(verified)} 次适用且 verified 的确定重放（需至少 2 次；不涉及/未知/异常/未执行不计覆盖）")
            if timing:
                gaps.append(_replay_time_reason(timing))
        elif len(all_results) != 1:
            gaps.append(f"{aid} 的确定断言结果不一致 {sorted(all_results)}")
    return {"ok": not gaps, "coverage": evidence, "why": "；".join(gaps)}


def replay_basis(case: dict, deps: Optional[str] = None) -> dict:
    """用例成熟度（能否稳定复测）与产品状态（是否符合要求）分开判断。
    当前内容版本下，最近一次待确认/阻塞的重放之后至少 2 次来自不同运行、结论确定的重放，
    且该版本下所有确定结论一致：全部通过 → conforming；全部失败且失败特征相同 → known_failure（需登记缺陷单）。
    混合说明不稳定；后续运行须在全部不确定运行完成后开始，不能靠录入顺序推断。
    显式证明零执行/零下发/无产品观测的前置阻塞仅留历史，不计覆盖也不重置。"""
    h = content_hash(case)
    history = case.get("replays", [])
    cur = [r for r in history if r["hash"] == h]
    # deps 给定时，重放也要在同一份依赖定义（目标/状态/判据）下跑的；没记依赖指纹的旧重放无法证明，不计入
    stale = [r for r in cur if deps is not None and r.get("deps") != deps]
    cur = [r for r in cur if r not in stale]
    relevant = {r["run_id"] for r in cur}
    runs, differences = {}, {}
    for row in history:
        run_id = row["run_id"]
        if run_id not in relevant:
            continue
        if run_id not in runs:
            runs[run_id] = row
        else:
            fields = _replay_difference_fields(runs[run_id], row)
            if fields:
                differences.setdefault(run_id, set()).update(fields)
    out = {"hash": h, "replays": list(runs), "count": len(runs)}
    if differences:
        conflicts = [{"run_id": run_id, "fields": sorted(fields)} for run_id, fields in differences.items()]
        return {**out, "ok": False, "conflicts": conflicts, "why": _replay_conflict_reason(conflicts)}
    timing_issues = [{"run_id": row["run_id"], "reason": error} for row in runs.values()
                     if (error := _replay_interval(row)[2])]
    if timing_issues:
        return {**out, "ok": False, "timing_issues": timing_issues,
                "why": "重放运行时间无效：" + "；".join(f"{i['run_id']} ({i['reason']})" for i in timing_issues)}
    if len(runs) < 2:
        why = f"当前内容版本 {h} 下的重放不足 2 次（现有 {len(runs)}）"
        if stale:
            why += f"；另有 {len(stale)} 次重放后目标/状态/判据定义已变（或旧记录没有依赖指纹），不计入，需重新重放"
        return {**out, "ok": False, "why": why}
    if any(r.get("alerts") for r in runs.values()):
        return {**out, "ok": False, "why": "有重放出现人工接管信号（安全停止未确认），不能作为晋级依据"}
    # 待确认/阻塞后重新计独立重放次数；之前的确定结论仍参与全版本一致性核对。
    # 判据确认之前的待确认运行因此不会永久挡住晋级，只要求确认之后重新重放
    order = list(runs.values())  # Preserve stored history, not an execution order.
    decisive = [r for r in order if r["verdict"] in ("passed", "failed") and not has_execution_error(r)]
    resets = [r for r in order if r not in decisive and not _preflight_only_replay(r)]
    rows, timing = _replays_after_resets(decisive, resets)
    out["ignored"] = [{"run_id": r["run_id"], "verdict": r["verdict"],
                       **({"reason": "preflight_only_no_product_evidence"} if _preflight_only_replay(r) else {})}
                      for r in order if r not in rows]
    if timing:
        out["timing"] = timing
    if len(rows) < 2:
        why = f"当前内容版本 {h} 下给出确定结论的重放不足 2 次（现有 {len(rows)}）：重放不足"
        if resets:
            why = (f"当前内容版本 {h} 下不确定重放之后给出确定结论的重放只有 {len(rows)} 次："
                   f"重放不足，需至少 2 次；{_replay_time_reason(timing)}")
        return {**out, "ok": False, "why": why}
    out["replays"] = [r["run_id"] for r in rows]
    coverage = _assertion_replay_coverage(case, rows, order)
    out["coverage"] = coverage["coverage"]
    if not coverage["ok"]:
        return {**out, "ok": False, "why": "断言分支重放不足或不一致：" + coverage["why"]}
    verdicts = {r["verdict"] for r in decisive}  # 早于不确定运行的确定结论也要一致，否则说明不稳定
    if verdicts == {"passed"}:
        return {**out, "ok": True, "product": "conforming", "rows": rows}
    if verdicts == {"failed"}:
        sigs = {tuple(r.get("failure_signature") or []) for r in decisive}
        if len(sigs) == 1 and next(iter(sigs)):
            return {**out, "ok": True, "product": "known_failure", "signature": list(next(iter(sigs))), "rows": rows}
        return {**out, "ok": False, "why": f"失败重放的失败特征不一致 {sorted(sigs)}：还不能稳定复现"}
    return {**out, "ok": False, "why": f"重放结论不一致或不确定 {sorted(verdicts)}：还不能稳定复测"}


@_case_update
def promote(ws, case_id: str, to: str, knowledge, reviewer: Optional[str] = None, allow_offline: bool = False,
            known_issue: Optional[str] = None, *, dry_run: bool = False) -> dict:
    case = ws.load_case(case_id)
    if dry_run:
        case = deepcopy(case)  # A cached/custom workspace must remain read-only too.
    app, policy = ws.app(case["app"]), ws.policy()
    require_review(ws, case, knowledge, app=app, policy=policy)
    cur = case.get("status", "draft")
    if NEXT.get(cur) != to:
        raise PermissionError(f"不能从 {cur} 晋级到 {to}（允许：{cur} → {NEXT.get(cur)}）")
    issues = lint(case, app, knowledge, policy)
    if issues:
        raise PermissionError(f"用例不完整，不能晋级：{issues}")
    h = content_hash(case)
    basis = replay_basis(case, dependency_hash(case, app, knowledge)) if to in ("replay_verified", "regression") else {"replays": []}
    if to in ("replay_verified", "regression"):
        # regression 也按当前内容版本重新核对：replay_verified 之后改过步骤/断言/授权，旧重放不再作数
        if not basis["ok"]:
            raise PermissionError(basis["why"])
        if basis["product"] == "known_failure":
            if not (known_issue or (case.get("product_status") or {}).get("issue")):
                raise PermissionError("重放稳定复现产品失败：请用 --known-issue <缺陷单/Issue> 登记；不要改预期把缺陷变成通过")
            case["product_status"] = {"status": "known_failure", "signature": basis["signature"],
                                      "issue": known_issue or case["product_status"]["issue"], "since_hash": h}
        else:
            case["product_status"] = {"status": "conforming", "since_hash": h}
    if to == "regression":
        if not reviewer:
            raise PermissionError("晋级正式回归用例需要评审人 --reviewer")
        # 判据适用性按计入晋级的重放里实际测到的事实判断（优先真机）
        rows = sorted(basis["rows"], key=lambda r: (r["validation_level"] == "real_device", r["ts"]))
        for assertion in [a for a in case["assertions"] if not a.get("advisory")]:
            aid = assertion["id"]
            candidates = []
            for row in rows:
                actual = next((a for a in (row.get("coverage") or {}).get("assertions") or [] if a.get("id") == aid), None)
                if actual and not (actual.get("verified") is True and actual.get("applicable") is True):
                    continue
                snapshot = next((a for a in row.get("assertions") or [] if a.get("id") == aid), {})
                # New runs bind Oracle facts at assertion time. Never restore a
                # business --env declaration from the legacy flat run facts.
                facts = snapshot.get("facts")
                if facts is None:
                    facts = row.get("oracle_facts")
                if facts is None and "coverage" not in row:
                    facts = row.get("facts")
                candidates.append((row, snapshot, facts or {}))
            if not candidates:
                raise PermissionError(f"断言 {aid} 没有适用且已验证的判据上下文")
            _, snapshot, facts = candidates[-1]
            st = knowledge.oracle_status(assertion["oracle_refs"], facts=facts)
            if st["status"] != "confirmed":
                raise PermissionError(f"断言 {aid} 判据未全部确认：{st['reason']}")
            # The runtime snapshot also records conditional scope and conflicts;
            # a coarse app/build fact map cannot replace that evidence.
            if snapshot.get("oracle") and snapshot["oracle"].get("status") != "confirmed":
                raise PermissionError(f"断言 {aid} 重放时的判据未确认或不适用")
        if not allow_offline and not any(r["validation_level"] == "real_device" for r in rows):
            raise PermissionError("没有真机重放；如确需仅凭离线回放晋级，请显式 --allow-offline 并在评审中说明")
    if dry_run:
        return {"case": case_id, "from": cur, "to": to, "hash": h, "replays": basis["replays"],
                "product_status": case.get("product_status")}
    case["history"] = case.get("history", []) + [{"ts": now_iso(), "from": cur, "to": to, "by": reviewer,
                                                   "hash": h, "replays": basis["replays"],
                                                   "product_status": (case.get("product_status") or {}).get("status")}]
    case["status"] = to
    write_json(ws.case_path(case_id), case)
    return case


def load_exploration(ws, run_id: str) -> dict:
    return read_json(ws.runs_dir / run_id / "exploration.json")
