"""用例生命周期：exploration_record → candidate → replay_verified → regression。

- 探索路径转候选时必须补齐：目的、前置条件、测试数据、步骤、独立判定依据、清理、来源证据。
- 判定依据必须是独立的知识条目；不能用"探索时看到了什么"来证明"应该是什么"。
- replay_verified 需要当前内容版本（content_hash）下至少 2 次来自不同运行的通过重放；
  regression 还需评审人、全部判据已确认，默认要求至少 1 次真机重放。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .knowledge import ACTIVE, item_status
from .safety import untreed_strategies
from .util import now_iso, read_json, sha256_obj, write_json

REQUIRED = ("purpose", "preconditions", "test_data", "steps", "assertions", "cleanup", "sources")
CONTENT_KEYS = ("app", "preconditions", "env_requirements", "test_data", "steps", "assertions", "cleanup")
# 只在声明时计入，避免改变既有用例的内容哈希。授权与安全停止决定实际会产生哪些物理副作用，改了就要重新重放
OPTIONAL_CONTENT_KEYS = ("layout_requirements", "authorizations", "safety")
TREE_KINDS = ("text", "id", "xpath")
# Issue #14：obs 编号属于单次运行（重放后编号会变）。裸引用会让判定证据链指向不存在的观测
# （真机上出现过：rationale 引 obs-0010/0012，而本 run 实际是 obs-0007/0008）。
# 但「run 目录名/会话名 + obs 编号」是可解析引用：锚点目录确实留存了该观测时证据链可追溯，允许。
# (?<![A-Za-z0-9_-]) 排除复合标识里的片段——K-QK-OBS-201 是知识 ID（稳定标识），不是观测编号。
OBS_REF_RE = re.compile(r"(?<![A-Za-z0-9_-])obs-\d{3,6}\b", re.IGNORECASE)
RUN_ANCHOR_RE = re.compile(r"\b(run-\d{8}-\d{6}-[0-9a-z]+|session-[0-9a-z_-]+)", re.IGNORECASE)
NEXT = {"exploration_record": "candidate", "draft": "candidate", "candidate": "replay_verified",
        "replay_verified": "regression"}


def unresolvable_obs_refs(text: str, runs_dir: Optional[Path] = None) -> list:
    """rationale 里的 obs 引用哪些不可解析（check-refs）。

    - 无 run/session 锚点 → 不可解析（裸编号，重放后必过期）；
    - 有锚点且给出 runs_dir（正常 lint 都给）→ 锚点目录必须真实存在且包含该观测，否则不可解析
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


def dependency_hash(case: dict, app, knowledge) -> str:
    """用例引用的外部定义的指纹：content_hash 只覆盖用例文件本身，但目标选择器、展开后的 flow/recipe 步骤、
    页面状态/打断定义、判据条目的陈述和预期改了，同一份用例就测了另一件事，旧重放不再作数。
    有效判据的确认状态、历史不计入（确认之后不必重新重放）；废弃/无效使旧重放失效。"""
    steps, targets, oracles = [], {}, set()

    def want(t):
        if isinstance(t, str) and t in app.targets:
            targets[t] = app.targets[t]

    for s, _, where in iter_case_steps(case, app):
        steps.append([where, {k: v for k, v in s.items() if k != "_lint_cycle"}])
        want(s.get("target"))
        oracles.update(s.get("oracle_refs") or [])
    for a in case.get("assertions", []):
        want(a.get("target"))
        oracles.update(a.get("oracle_refs") or [])
    facts = {}
    for kid in sorted(oracles):
        item = knowledge.get(kid) or {}
        facts[kid] = {k: item.get(k) for k in ("statement", "expected", "applies_to")}
        if item_status(item) not in ACTIVE:
            facts[kid]["inactive"] = True
    return sha256_obj({"steps": steps, "targets": targets, "states": app.states.states,
                       "interrupts": app.states.interrupts, "oracles": facts})[:12]


def iter_case_steps(case: dict, app):
    """步骤、清理、安全停止和引用的 flow/recipe 共用来源；保留可定位的字段路径。"""
    def walk(step, parent, where, ancestry=()):
        action = step.get("action")
        children, key = [], None
        if action == "flow":
            key = (action, step.get("flow"))
            children = (app.flows.get(key[1]) or {}).get("steps", [])
        elif action == "ensure_state":
            key = (action, step.get("state"))
            children = app.states.recipes.get(key[1], [])
        if key is not None and key in ancestry:
            yield {**step, "_lint_cycle": True}, parent, where
            return
        yield step, parent, where
        for i, sub in enumerate(children):
            label = f"{where}→{action}:{key[1]}.{sub.get('id', f'S{i + 1}')}"
            yield from walk(sub, step, label, ancestry + (key,))
    sources = [("steps", case.get("steps", [])), ("cleanup", case.get("cleanup", [])),
               ("safe_stop", (case.get("safety") or {}).get("safe_stop", []))]
    for source, steps in sources:
        prefix = {"steps": "S", "cleanup": "C", "safe_stop": "SAFE"}[source]
        for i, step in enumerate(steps):
            yield from walk(step, None, f"{source}.{step.get('id', f'{prefix}{i + 1}')}")


def hypothesis_targets(case: dict, app) -> list:
    """用例（含流程/recipe 展开）会用到的 status=hypothesis 目标：原工程没有、未经真机校准的控件。"""
    used = []
    for s, _, _ in iter_case_steps(case, app):
        t = s.get("target")
        if isinstance(t, str) and (app.targets.get(t) or {}).get("status") == "hypothesis" and t not in used:
            used.append(t)
    for a in case.get("assertions", []):
        t = a.get("target")
        if isinstance(t, str) and (app.targets.get(t) or {}).get("status") == "hypothesis" and t not in used:
            used.append(t)
    return used


def _lint_effects(case: dict, app, policy) -> list:
    """副作用相关检查：申请的授权要覆盖实际会点的目标；运动类必须有安全声明；改用户数据必须有清理；
    密钥只能以引用形式写入 secret 输入框；流程与监视参数完整。"""
    import re
    issues = []
    requested = {a["effect"] if isinstance(a, dict) else a for a in case.get("authorizations", [])}
    authorizable = policy.authorizable if policy else set()
    secrets = {k for k, v in (case.get("test_data") or {}).items() if isinstance(v, dict) and "secret" in v}
    for s, parent, where in iter_case_steps(case, app):
        if s.get("action") == "flow":
            spec = app.flows.get(s.get("flow"))
            if spec is None:
                issues.append({"field": where, "problem": f"flows.json 中没有流程 {s.get('flow')}"})
            else:
                if spec.get("status") == "falsified_on_device":
                    issues.append({"field": where, "problem": f"流程 {s['flow']} 已被真机证伪：{spec.get('status_note', '')[:80]}"})
                miss = [p for p in spec.get("params", {}) if p not in (s.get("params") or {})]
                if miss:
                    issues.append({"field": where, "problem": f"流程 {s['flow']} 缺少参数 {miss}"})
        t = s.get("target")
        target = app.targets.get(t) if isinstance(t, str) else (t if isinstance(t, dict) else None)
        eff = (target or {}).get("side_effect")
        if s.get("action") in ("tap", "input") and eff in authorizable and eff not in requested:
            verb = "点击" if s.get("action") == "tap" else "输入到"
            issues.append({"field": where, "problem": f"会{verb} side_effect={eff} 的目标 {t}，但用例 authorizations 未申请 {eff}"})
        if s.get("action") == "tap" and eff == "safe_stop" and untreed_strategies(target):
            issues.append({"field": where, "problem": f"safe_stop 目标 {t} 含非 UI 树策略 {untreed_strategies(target)}，运行时无树会拒绝点击"})
        if s.get("action") == "input" and target is not None:
            text = str(s.get("text", ""))
            pm = re.fullmatch(r"\$\{param:([A-Za-z0-9_]+)\}", text)
            if parent and pm:  # 流程子步骤：按调用方传入的参数值检查
                text = str((parent.get("params") or {}).get(pm.group(1), text))
            ref = re.fullmatch(r"\$\{var:([A-Za-z0-9_]+)\}", text)
            if target.get("secret") and not (ref and ref.group(1) in secrets):
                issues.append({"field": where, "problem": "敏感输入框只能写 ${var:x}，且 x 须在 test_data 中声明为 {\"secret\": NAME}"})
            if ref and ref.group(1) in secrets and not target.get("secret"):
                issues.append({"field": where, "problem": "密钥引用只能写入声明了 secret 的输入框"})
        if s.get("action") == "monitor" and policy:
            cap = float(policy.supervision.get("max_monitor_s", 600))
            if float(s.get("duration_s", 30)) > cap:
                issues.append({"field": where, "problem": f"监视时长超过 policy 上限 {cap}s"})
            for k, v in (s.get("watch") or {}).items():
                try:
                    re.compile(v)
                except re.error as e:
                    issues.append({"field": where, "problem": f"watch.{k} 不是合法正则：{e}"})
            for k in set(s.get("stop_on", [])) | set(s.get("abort_on", [])):
                if k not in (s.get("watch") or {}):
                    issues.append({"field": where, "problem": f"stop_on/abort_on 引用了未定义的 watch 规则 {k}"})
    if "vehicle_motion" in requested and policy:
        from .safety import problems
        issues += problems(case.get("safety"), app, policy)
    if "user_data" in requested and not case.get("cleanup"):
        issues.append({"field": "cleanup", "problem": "申请了 user_data（收藏/重命名/标签）的用例必须有清理步骤，恢复账号数据"})
    if policy:
        bad = [r for r in requested if r not in authorizable]
        if bad:
            issues.append({"field": "authorizations", "problem": f"{bad} 不可授权（policy 硬禁止）或不是已知副作用类"})
    return issues


def lint(case: dict, app, knowledge, policy=None, runs_dir: Optional[Path] = None) -> list:
    """runs_dir 用于 check-refs：核对 rationale 里「run/会话 + obs」引用是否真的可解析到留存证据。"""
    issues = []
    for k in REQUIRED:
        v = case.get(k)
        if v is None or v == [] or v == "" or (k == "test_data" and v == {} and not case.get("test_data_none")):
            issues.append({"field": k, "problem": "缺失或为空" + ("（无测试数据请写 test_data_none: true）" if k == "test_data" else "")})
    step_ids = {s.get("id") for s in case.get("steps", [])}
    for k, v in (case.get("test_data") or {}).items():
        if isinstance(v, dict) and v.get("missing_from_session"):
            issues.append({"field": f"test_data.{k}", "problem": "会话没有保留这次输入的原文（不允许输入/敏感框/疑似密钥）："
                                                                 "非敏感数据请直接填值，账号密码改写为 {\"secret\": NAME}"})
    for s, _, where in iter_case_steps(case, app):
        if s.get("_lint_cycle"):
            issues.append({"field": where, "problem": "flow/recipe 存在递归引用，不能展开执行"})
        if s["action"] in ("tap", "input"):
            t = s.get("target")
            if isinstance(t, dict):
                # 现场选择器：只用 UI 树策略、且附有"在哪次观测中解析成功"的来源，才可以直接留在用例里
                kinds = {st.get("kind") for st in t.get("strategies", [])}
                if not kinds:
                    issues.append({"field": where, "problem": "内联目标没有定位策略（会话里是坐标点击且无法"
                                                                           "从 UI 树推出选择器）：请补文本/id/xpath 选择器或模板"})
                elif not kinds <= set(TREE_KINDS) or not t.get("observed"):
                    issues.append({"field": where, "problem": "内联目标需只含 text/id/xpath 策略并附 observed 来源"
                                                                           "（会话观测），否则请登记到 targets.json 并评审"})
                elif not (t["observed"].get("node") and t["observed"].get("dispatched")):
                    # 只要求"选择器在现场解析到了唯一节点并实际下发过"，不要求该步通过（缺陷复现步骤本身可能失败）
                    issues.append({"field": where, "problem": f"内联选择器在会话中没有解析成功并下发过"
                                                                           f"（会话结果 {t['observed'].get('outcome')}）：重新观察后"
                                                                           "用能唯一定位的选择器再做一次，或写明理由排除该步"})
            elif not isinstance(t, str) or t not in app.targets:
                issues.append({"field": where, "problem": f"目标 {t} 未登记"})
        if s.get("expect_state_unresolved"):
            issues.append({"field": where, "problem": "终点是未声明状态，缺少可判定的后置条件"})
        if s.get("point") is not None or (isinstance(s.get("target"), dict) and any(
                st.get("kind") == "point" for st in s["target"].get("strategies", []))):
            issues.append({"field": where, "problem": "用例中不能写像素坐标：屏幕尺寸/密度/折叠状态不同坐标就失效，"
                                                                   "请登记为 targets.json 中的文本/xpath/id/模板目标"})
        if s["action"] == "ensure_state" and s.get("state") not in app.states.recipes:
            issues.append({"field": where, "problem": f"没有到达 {s.get('state')} 的 recipe"})
    from .layout import REQUIREMENT_KEYS
    for k in case.get("layout_requirements", {}):
        if k not in REQUIREMENT_KEYS:
            issues.append({"field": "layout_requirements", "problem": f"{k} 不能在启动前判定，可用 {list(REQUIREMENT_KEYS)}；"
                                                                     "是否分栏请写成步骤后的断言"})
    issues += _lint_effects(case, app, policy)
    hyp = hypothesis_targets(case, app)
    if hyp:
        issues.append({"field": "targets", "problem": f"用到未经真机校准的假设目标 {hyp}（status=hypothesis）："
                                                      "先在真机上校准选择器并改为 calibrated_on_device，才能晋级"})
    real = [a for a in case.get("assertions", []) if not a.get("advisory")]
    if not real and case.get("assertions"):
        issues.append({"field": "assertions", "problem": "没有非建议类断言"})
    for a in case.get("assertions", []):
        if a.get("at") not in step_ids:
            issues.append({"field": f"assertions.{a.get('id')}", "problem": f"at={a.get('at')} 不是用例中的步骤 ID"})
    for src in case.get("sources", []):
        for r in src.get("rejected") or []:
            if r.get("dispatched"):
                issues.append({"field": f"sources.rejected.A{r.get('seq')}",
                               "problem": "已下发过手势/按键的动作被记成了被拒：现场可能已改变，须作为效果未知的步骤保留，"
                                          "或用 --drop 写明理由排除"})
    for s, _, where in iter_case_steps(case, app):
        fs = s.get("from_session") or {}
        if fs.get("effect_unknown") and not s.get("effect_reviewed"):
            issues.append({"field": where, "problem": f"会话里这一步已下发但结果未确认（{fs.get('outcome')}）："
                                                                   "看 obs_after 确认实际效果后写 effect_reviewed，"
                                                                   "或用 --drop 写明理由排除"})
    if any(s.get("type") == "session" for s in case.get("sources", [])) and not case.get("knowledge_used"):
        issues.append({"field": "knowledge_used", "problem": "由会话生成的用例须说明用到了哪些知识、是否适用、怎样映射到动作"
                                                             "（没有可用知识也要写明，例如 [{\"id\": null, \"note\": \"...\"}]）"})
    # Issue #14：支持可解析的 run/session + obs 证据引用，不能一刀切禁止 obs 编号——
    # 不可解析的引用（裸编号、锚点目录未留存该观测）才拦。稳定引用 = 知识 ID / 步骤 ID，
    # 或「run 目录名/会话名 + obs 编号」且证据确实还在。
    for k in case.get("knowledge_used") or []:
        if not k:
            continue
        bad = unresolvable_obs_refs(str(k.get("rationale") or ""), runs_dir)
        if bad:
            issues.append({"field": "knowledge_used", "problem":
                f"knowledge_used[{k.get('id')}] 的 rationale 引用了不可解析的 obs 证据（{'；'.join(bad)}）："
                "观测编号属于单次运行、重放后会过期。请改引稳定标识（知识 ID / 步骤 ID），"
                "或写成「run 目录名/会话名 + obs 编号」并确保该目录留存了对应观测"})
    for a in real:
        if not a.get("oracle_refs"):
            issues.append({"field": f"assertions.{a.get('id')}", "problem": "缺少 oracle_refs（需求/已确认用例/产品规则）"})
        for r in a.get("oracle_refs", []):
            it = knowledge.get(r)
            if it is None:
                issues.append({"field": f"assertions.{a.get('id')}", "problem": f"判据 {r} 不存在"})
            elif item_status(it) not in ACTIVE:
                issues.append({"field": f"assertions.{a.get('id')}",
                               "problem": f"判据 {r} 的状态 {it.get('status')!r} 已废弃或无效，不能作判据"})
            elif it["source"].get("type") == "observation" and it["source"].get("run") in {
                    s.get("run") for s in case.get("sources", [])}:
                issues.append({"field": f"assertions.{a.get('id')}", "problem": f"判据 {r} 来自本用例的探索观察，不独立"})
    return issues


def from_exploration(exp: dict, transitions: list, case_id: str, app_id: str) -> dict:
    by_id = {t["id"]: t for t in exp["transitions"]}
    chain = [by_id[t] for t in transitions]
    for a, b in zip(chain, chain[1:]):
        if a["to"] != b["from"]:
            raise ValueError(f"迁移 {a['id']} 的终点 {a['to']} 与 {b['id']} 的起点 {b['from']} 不连续")
    steps = [{"id": "S1", "action": "ensure_state", "state": chain[0]["from"]}]
    for i, t in enumerate(chain, start=2):
        st = {"id": f"S{i}", "action": "tap", "target": t["target"], "timeout_s": 8, "retry": 1, "critical": True,
              "oracle_refs": [], "from_transition": t["id"]}
        if t.get("to_declared"):
            st["expect_state"] = t["to"]
        else:
            st["expect_state_unresolved"] = t["to"]
        steps.append(st)
    return {"id": case_id, "version": 1, "status": "exploration_record", "app": app_id,
            "title": f"[探索生成] {' → '.join([chain[0]['from']] + [t['to'] for t in chain])}",
            "purpose": "", "preconditions": [f"APP 处于 {chain[0]['from']}"], "env_requirements": {},
            "test_data": {}, "steps": steps, "assertions": [], "cleanup": [],
            "sources": [{"type": "exploration", "run": exp["run_id"], "transitions": transitions,
                         "obs": [o for t in chain for o in (t["obs_before"], t["obs_after"])]}],
            "history": [{"ts": now_iso(), "to": "exploration_record", "why": "由探索迁移生成，待补齐目的/判据/清理"}]}


def record_replay(ws, case_id: str, run: dict) -> dict:
    case = ws.load_case(case_id)
    v = run["verdict"]
    case.setdefault("replays", []).append({
        "run_id": run["run_id"], "verdict": v["verdict"], "hash": run["case"]["hash"], "deps": run["case"].get("deps"),
        "validation_level": run["validation_level"], "device": run["device_spec"], "ts": now_iso(),
        "failure_signature": v.get("failure_signature", []), "alerts": len(v.get("alerts") or []),
        "not_verified": len(v.get("not_verified") or []), "facts": run.get("facts")})
    write_json(ws.case_path(case_id), case)
    return case


def replay_basis(case: dict, deps: Optional[str] = None) -> dict:
    """用例成熟度（能否稳定复测）与产品状态（是否符合要求）分开判断。
    当前内容版本下，最近一次待确认/阻塞的重放之后至少 2 次来自不同运行、结论确定的重放，
    且该版本下所有确定结论一致：全部通过 → conforming；全部失败且失败特征相同 → known_failure（需登记缺陷单）。
    混合说明不稳定；最近的重放是待确认/阻塞，说明现在还不能稳定复测，要在它之后重新攒够 2 次。"""
    h = content_hash(case)
    cur = [r for r in case.get("replays", []) if r["hash"] == h]
    # deps 给定时，重放也要在同一份依赖定义（目标/状态/判据）下跑的；没记依赖指纹的旧重放无法证明，不计入
    stale = [r for r in cur if deps is not None and r.get("deps") != deps]
    cur = [r for r in cur if r not in stale]
    runs = {r["run_id"]: r for r in cur}
    out = {"hash": h, "replays": list(runs), "count": len(runs)}
    if len(runs) < 2:
        why = f"当前内容版本 {h} 下的重放不足 2 次（现有 {len(runs)}）"
        if stale:
            why += f"；另有 {len(stale)} 次重放后目标/状态/判据定义已变（或旧记录没有依赖指纹），不计入，需重新重放"
        return {**out, "ok": False, "why": why}
    if any(r.get("alerts") for r in runs.values()):
        return {**out, "ok": False, "why": "有重放出现人工接管信号（安全停止未确认），不能作为晋级依据"}
    # 待确认/阻塞的重放不给出产品结论。它之前的确定结论不再作数：之后又出现了不能复测的运行。
    # 判据确认之前的待确认运行因此不会永久挡住晋级，只要求确认之后重新重放
    order = list(runs.values())  # 记录顺序即运行顺序
    decisive = [r for r in order if r["verdict"] in ("passed", "failed")]
    last_bad = max((i for i, r in enumerate(order) if r not in decisive), default=-1)
    rows = order[last_bad + 1:]
    out["ignored"] = [{"run_id": r["run_id"], "verdict": r["verdict"]} for r in order[:last_bad + 1]]
    if len(rows) < 2:
        why = f"当前内容版本 {h} 下给出确定结论的重放不足 2 次（现有 {len(rows)}）：重放不足"
        if last_bad >= 0:
            bad = order[last_bad]
            why = (f"当前内容版本 {h} 下最近一次不确定的重放是 {bad['run_id']}（{bad['verdict']}），"
                   f"之后给出确定结论的重放只有 {len(rows)} 次：重放不足，需在它之后至少 2 次")
        return {**out, "ok": False, "why": why}
    out["replays"] = [r["run_id"] for r in rows]
    verdicts = {r["verdict"] for r in decisive}  # 早于不确定运行的确定结论也要一致，否则说明不稳定
    if verdicts == {"passed"}:
        return {**out, "ok": True, "product": "conforming", "rows": rows}
    if verdicts == {"failed"}:
        sigs = {tuple(r.get("failure_signature") or []) for r in rows}
        if len(sigs) == 1 and next(iter(sigs)):
            return {**out, "ok": True, "product": "known_failure", "signature": list(next(iter(sigs))), "rows": rows}
        return {**out, "ok": False, "why": f"失败重放的失败特征不一致 {sorted(sigs)}：还不能稳定复现"}
    return {**out, "ok": False, "why": f"重放结论不一致或不确定 {sorted(verdicts)}：还不能稳定复测"}


def promote(ws, case_id: str, to: str, knowledge, reviewer: Optional[str] = None, allow_offline: bool = False,
            known_issue: Optional[str] = None) -> dict:
    case, app = ws.load_case(case_id), ws.app(ws.load_case(case_id)["app"])
    cur = case.get("status", "draft")
    if NEXT.get(cur) != to:
        raise PermissionError(f"不能从 {cur} 晋级到 {to}（允许：{cur} → {NEXT.get(cur)}）")
    issues = lint(case, app, knowledge, ws.policy(), runs_dir=ws.runs_dir)
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
        refs = [r for a in case["assertions"] if not a.get("advisory") for r in a["oracle_refs"]]
        st = knowledge.oracle_status(refs, facts=rows[-1].get("facts") or {})
        if st["status"] != "confirmed":
            raise PermissionError(f"判据未全部确认：{st['reason']}")
        if not allow_offline and not any(r["validation_level"] == "real_device" for r in rows):
            raise PermissionError("没有真机重放；如确需仅凭离线回放晋级，请显式 --allow-offline 并在评审中说明")
    case["history"] = case.get("history", []) + [{"ts": now_iso(), "from": cur, "to": to, "by": reviewer,
                                                   "hash": h, "replays": basis["replays"],
                                                   "product_status": (case.get("product_status") or {}).get("status")}]
    case["status"] = to
    write_json(ws.case_path(case_id), case)
    return case


def load_exploration(ws, run_id: str) -> dict:
    return read_json(ws.runs_dir / run_id / "exploration.json")
