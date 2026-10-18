"""回归执行：前置检查 → 执行步骤 → 清理 → 断言判定 → 报告 → 经验候选。"""
from __future__ import annotations

from typing import Optional

from . import __version__
from . import layout as layout_mod
from . import safety as safety_mod
from .cases import (
    content_hash,
    dependency_hash,
    iter_case_steps,
    require_review,
    review_context,
    review_status,
)
from .conditions import definitions, validate_definitions, validate_guard
from .execute import Executor
from .experience import ExperienceStore
from .knowledge import KnowledgeStore
from .observe import RunStore
from .params import Params
from .report import render_run
from .session import swipe_options_error
from .tree import UiTree
from .util import now_iso, read_json, read_jsonl, write_json
from .verdict import (
    PASSED,
    case_verdict,
    evaluate_assertion,
    oracle_guards,
    select_applicability,
)
from .workspace import RunContext, Workspace


def _env_check(case: dict, facts: dict, pre_layout: dict) -> dict:
    unmet = [{"key": k, "required": v, "actual": facts.get(k)}
             for k, v in case.get("env_requirements", {}).items() if facts.get(k) != v]
    unmet += layout_mod.check_requirements(case.get("layout_requirements", {}), pre_layout)
    return {"ok": not unmet, "facts": facts, "required": case.get("env_requirements", {}), "unmet": unmet,
            "layout": {"required": case.get("layout_requirements", {}), "display_profile": pre_layout}}


def run_facts(app, pre: dict, declared: dict) -> dict:
    """调度环境投影：身份、版本、形态必须实测；业务声明只参与既有整例门禁。
    产品判据另绑定 oracle_facts，不能把 --env 当当前观测。"""
    disp = pre.get("display") or {}
    measured = {"app": app.id, "app_version": pre.get("app_version"), "device_form": disp.get("device_form")}
    out = {k: v for k, v in (declared or {}).items() if v not in (None, "")}
    out.update({k: v if v not in (None, "") else None for k, v in measured.items()})
    return out


def run_case(ws: Workspace, case_id: Optional[str] = None, case: Optional[dict] = None, device_spec: Optional[str] = None,
             overlay=None, env: Optional[dict] = None, run_id: Optional[str] = None, propose: bool = True,
             authorize: Optional[list] = None, supervisor: Optional[str] = None, require_asset_review: bool = False) -> dict:
    case = case or ws.load_case(case_id)
    app = ws.app(case["app"])
    # 定义错误先拒绝，不能先为无法执行的手势解锁或关闭系统弹窗。
    for step, _, where in iter_case_steps(case, app):
        error = swipe_options_error(step)
        if error:
            raise ValueError(f"{where}: {error}")
    condition_defs = definitions(case, app)
    condition_issues = validate_definitions(condition_defs, app)
    step_ids = {s["id"] for s in case.get("steps", [])}
    for assertion in case.get("assertions", []):
        if "guard" in assertion:
            condition_issues += validate_guard(assertion["guard"], condition_defs, step_ids)
    for child, _, where in iter_case_steps(case, app):
        if "when" in child:
            condition_issues += [{**x, "field": where + ".when." + x.get("field", "")} for x in
                                 validate_guard(child["when"], condition_defs, step_ids)]
    if condition_issues:
        raise ValueError(f"条件定义无效：{condition_issues}")
    knowledge, policy = KnowledgeStore(ws.root), ws.policy()
    for item in case.get("assertions", []) + [s for s, _, _ in iter_case_steps(case, app)]:
        oracle_guards(item.get("oracle_refs", []), knowledge)  # 定义错误在任何设备操作之前拒绝。
    context = review_context(ws, app)
    asset_review = (require_review if require_asset_review else review_status)(ws, case, knowledge, app, policy, context)
    store = RunStore(ws.runs_dir, run_id, kind="run")
    spec = device_spec or ws.config.get("device")
    device = ws.device(spec, state_path=store.dir / "replay_state.json")
    xstore = ExperienceStore(ws.root, app.id)
    overlay = overlay or xstore.overlay()
    ctx = RunContext(ws, app, device, store, overlay, mode="regression", policy=policy,
                     layout_cfg=context["layout"], dialogs=context["system_dialogs"].get("dialogs") or {})
    ctx.conditions = condition_defs
    # 使用审核检查的同一输入快照；前置检查后再绑定实测事实
    started = now_iso()
    # steps/title 供控制台进度面板显示总步数与标题；只记步骤 id 与动作名，不含目标/文字等内容
    store.event("run_start", case=case["id"], title=case.get("title"), device=device.spec,
                overlay=overlay.describe(),
                steps=[{"id": s["id"], "action": s["action"]} for s in case.get("steps", [])])

    pre = device.preflight(app.bundle)
    device_facts = device.env_facts()
    declared = {**device_facts, **(env or {})}
    facts = run_facts(app, pre, declared)
    pre_layout = layout_mod.profile(None, pre.get("display"), ctx.observer.layout_cfg)
    envc = _env_check(case, facts, pre_layout)
    screen_checks = {"screen_awake", "screen_unlocked"}
    if envc["ok"] and not any(c.get("execution_error") for c in pre["checks"]) and any(c["check"] in screen_checks and not c["ok"] for c in pre["checks"]) and all(
            c["ok"] for c in pre["checks"] if c["check"] not in screen_checks):
        from .unlock import unlock_device
        before_checks = [dict(c) for c in pre["checks"]]
        unlocked = unlock_device(ws, device, store, dispatcher=ctx.dispatcher)
        if unlocked["status"] == "unlocked":
            pre = device.preflight(app.bundle)
            facts = run_facts(app, pre, declared)
            pre_layout = layout_mod.profile(None, pre.get("display"), ctx.observer.layout_cfg)
            envc = _env_check(case, facts, pre_layout)
        else:
            pre["checks"].append({"check": "unlock_capability", "ok": False,
                                  "error_class": unlocked["status"], "value": unlocked.get("reason"),
                                  **({"execution_error": unlocked["execution_error"]} if unlocked.get("execution_error") else {})})
        pre["unlock"] = unlocked
        store.event("preflight_before_unlock", checks=before_checks)

    # 系统弹窗检查：锁屏/前台/安装检查看不见盖在屏幕上的系统弹窗（2026-10-08 真机：USB 连接方式弹窗
    # preflight 全绿却两次阻塞 S1）。先按窗口名快查已登记弹窗，命中才做整屏诊断并用原子能力关闭；
    # 未命中/查询失败不阻塞，动作后的 unobservable 恢复会用完整诊断兜底。
    if pre.get("ok") and envc["ok"]:
        from .sysdialog import preflight_dialog_check
        pre["system_dialog"] = preflight_dialog_check(ws, device, store, dispatcher=ctx.dispatcher,
                                                     dialogs=ctx.observer.dialogs)

    store.event("preflight", ok=pre["ok"], checks=pre["checks"])
    ctx.observer.display = pre.get("display")  # 复用前置检查读到的显示参数，不重复查询
    # 调度兼容声明与判据事实分开。CLI --env 无时间、观测和身份，不能升级成实测。
    # Replay world 的环境仅是显式标记的合成设备事实，绝不冒充真机。
    oracle_facts = run_facts(app, pre, device_facts if device.kind == "replay" else {})
    ctx.facts = oracle_facts
    knowledge.bind(oracle_facts)
    # 参数与密钥：密钥只从环境变量/本地配置读取；缺失时判环境阻塞，不向输入框写空值
    params = Params(case.get("test_data"), ws.config.get("secrets"), store.run_id)
    ctx.params = params
    unmet = [{"key": f"secret.{n}", "required": f"环境变量 VPD_SECRET_{n}", "actual": "未设置"}
             for n in params.preflight_missing(case, app)]
    # task 类来自任务策略；其他 gated/supervised 类必须显式授予，硬禁止类无法授予。
    # supervised 类（车辆运动/车控）还要有在场监护人，vehicle_motion 还要有安全停止声明
    requested = [a["effect"] if isinstance(a, dict) else a for a in case.get("authorizations", [])]
    auth = ctx.policy.check_authorizations(requested, set(authorize or []), supervisor, case.get("safety"))
    unmet += [{"key": f"authorize.{a}", "required": "本次运行 --authorize " + a, "actual": "未授权"} for a in auth["missing"]]
    unmet += [{"key": f"authorize.{a}", "required": "不可授权（policy 硬禁止）", "actual": "用例申请"} for a in auth["not_grantable"]]
    if auth["missing_supervisor"]:
        unmet.append({"key": "supervisor", "required": f"--supervisor <在场监护人>（{'/'.join(auth['missing_supervisor'])} 需要有人在车旁监护）",
                      "actual": "未指定"})
    if "vehicle_motion" in requested:
        unmet += [{"key": p["field"], "required": p["problem"], "actual": "用例未满足"}
                  for p in safety_mod.problems(case.get("safety"), app, ctx.policy)]
    if unmet:
        envc = {**envc, "ok": False, "unmet": envc["unmet"] + unmet}
    ctx.authorized = ctx.policy.effective_grants(set(requested) & set(authorize or []))
    ctx.supervisor = supervisor
    ctx.motion = safety_mod.MotionGuard(case.get("safety"), ctx.clock, store)
    grant_sources = ctx.policy.grant_sources(ctx.authorized)
    store.event("authorization", requested=requested, granted=sorted(ctx.authorized), grant_sources=grant_sources,
                supervisor=bool(supervisor))
    steps, cleanup, safety_stops = [], [], []
    if pre["ok"] and envc["ok"]:
        ex = Executor(ctx)
        halted = None
        for st in case["steps"]:
            if halted:
                blocked = {"step_id": st["id"], "action": st["action"], "outcome": "not_run",
                              "critical": st.get("critical", True), "attempts": [], "recoveries": [], "adapted": False,
                              "oracle_refs": st.get("oracle_refs", [])}
                if halted["outcome"] == "skipped":
                    blocked.update(final_status="required_step_skipped", blocked_by=halted["step_id"],
                                   reason="必需前置步骤因操作条件未满足而未执行，后继步骤停止")
                steps.append(blocked)
                continue
            store.event("step_start", step=st["id"], action=st["action"])
            res = ex.run_step(st, case["id"])
            steps.append(res)
            store.event("step_done", step=st["id"], outcome=res["outcome"])
            if res["outcome"] not in PASSED and res["critical"]:
                halted = res
                if ctx.motion.armed or ctx.motion.locked():
                    # 运动中出现首次关键失败：先安全停止，再清理。首次失败原样保留在该步骤记录里
                    safety_stops.append(safety_mod.safe_stop(ex, "critical_step_failed",
                                                             {"step": st["id"], "status": res.get("final_status")}))
        # 超时锁住后不管 stop_at_end 都要走安全停止：它确认已停、解锁之后，清理才能继续下发
        if ctx.motion.locked() or (ctx.motion.armed and (case.get("safety") or {}).get("stop_at_end", True)):
            safety_stops.append(safety_mod.safe_stop(ex, "motion_timeout" if ctx.motion.locked()
                                                     else "case_end_still_moving"))
        for i, c in enumerate(case.get("cleanup", [])):
            res = ex.run_step({"critical": False, "retry": 0, **c, "id": c.get("id", f"C{i + 1}")}, case["id"])
            if res.get("final_status") == "safety_hold":
                res.update(outcome="not_run", reason="安全停止未确认，清理暂停，需人工接管")
            res["required"] = c.get("required", True)  # 默认必需：清理没完成就不能判通过
            cleanup.append(res)
            store.event("cleanup_done", step=res["step_id"], outcome=res["outcome"], required=res["required"])
    else:
        ctx.observe("preflight-blocked")
        steps = [{"step_id": s["id"], "action": s["action"], "outcome": "not_run", "critical": s.get("critical", True),
                  "attempts": [], "recoveries": [], "adapted": False, "oracle_refs": s.get("oracle_refs", [])}
                 for s in case["steps"]]

    def load(obs_id: str):
        d = store.dir / "obs" / obs_id
        tp = d / "tree.json"
        obs = read_json(d / "observation.json")
        usable = obs.get("fresh") is not False and (obs.get("tree") or {}).get("status") != "stale"
        return obs, (UiTree(read_json(tp)) if usable and tp.exists() else None)

    by_id = {s["step_id"]: s for s in steps}
    def bind_step_scenes(items):
        for result in items:
            guards = oracle_guards(result.get("oracle_refs", []), knowledge)
            if guards and result.get("final_obs"):
                observed, tree = load(result["final_obs"])
                result["oracle_guard"] = select_applicability(guards, condition_defs, by_id, load, app, observed, tree)
            bind_step_scenes(result.get("sub_steps") or [])
    bind_step_scenes(steps)

    assertions = [evaluate_assertion(a, by_id, load, knowledge, app, condition_defs) for a in case.get("assertions", [])]
    for assertion in assertions:
        if assertion.get("guard"):
            store.event("assertion_selected", assertion=assertion["id"], guard=assertion["guard"],
                        result=assertion["result"], verified=assertion["verified"])
    verdict = case_verdict(pre, envc, steps, assertions, knowledge, safety_stops, cleanup,
                           step_ids={s["id"] for s in case["steps"]})
    known = case.get("product_status") or {}
    if known.get("status") == "known_failure" and known.get("since_hash") != content_hash(case):
        verdict["product_status_stale"] = {"since_hash": known.get("since_hash"), "content_hash": content_hash(case),
                                           "note": "用例内容已变更，需要重新晋级"}
    elif known.get("status") == "known_failure":
        # 已知缺陷用例：结论照常判定，另外说明缺陷是否仍按同一特征复现（不把失败改成通过）
        sig = verdict.get("failure_signature")
        reproduced = bool(verdict.get("product_failed") and sig == known.get("signature"))
        verdict["known_issue"] = {"ref": known.get("issue"), "expected_signature": known.get("signature"),
                                  "reproduced": reproduced,
                                  "note": "产品失败仍按同一特征复现（执行结果另列）" if reproduced
                                  else "未按已知特征复现：可能已修复、路径变化或本次被其他问题挡住，需人工核对"}
    refs = sorted({r for x in case.get("assertions", []) + case["steps"] for r in x.get("oracle_refs", [])})
    run = {"run_id": store.run_id, "skill_version": __version__, "started": started, "finished": now_iso(),
           "validation_level": "offline_replay" if device.kind == "replay" else "real_device",
           "case": {"id": case["id"], "title": case.get("title"), "version": case.get("version"),
                    "status": case.get("status"), "hash": content_hash(case),
                    "deps": dependency_hash(case, app, knowledge), "sources": case.get("sources", [])},
           "informal": case.get("status") != "regression" or (asset_review["required"] and not asset_review["valid"]),
           "asset_review": {**asset_review, "use": "batch" if require_asset_review else "single"},
           "app": {"id": app.id, "bundle": app.bundle, "version": pre.get("app_version")},
           "device": pre.get("device") or device.info(), "device_spec": device.spec,
           "policy": {"version": ctx.policy.version, "hash": ctx.policy.hash}, "overlay": overlay.describe(),
           "layout": {"display": pre.get("display"), "profile": pre_layout,
                      "seen": ctx.observer.layouts_seen},
           "env": envc, "preflight": pre, "steps": steps, "cleanup": cleanup, "assertions": assertions,
           "knowledge_snapshot": knowledge.oracle_status(refs)["items"] if refs else [],
           "facts": facts, "oracle_facts": knowledge.facts,
           "declarations": {"source": "cli_env", "values": env or {}, "measured": False},
           "condition_definitions": condition_defs, "knowledge_used": case.get("knowledge_used", []), "goal": case.get("goal"),
           "verdict": verdict, "actions": ctx.actions, "seed": ctx.seed,
           "test_data": params.public(), "authorized": sorted(ctx.authorized), "authorization_sources": grant_sources,
           "supervision": {"supervisor": supervisor,
                           "supervised_granted": sorted(ctx.authorized & ctx.policy.supervised_side_effects),
                           "safety": case.get("safety")},
           "motion": ctx.motion.history, "safety_stops": safety_stops, "safety_hold": ctx.safety_hold,
           "measurements": [{"step": s["step_id"], **{k: v for k, v in s["measurement"].items()}}
                            for s in steps if s.get("measurement")]}
    # 运行中所有观测里出现过的页面内失败提示（ui_alert 事件），按时间列出
    alerts = []
    for d in sorted((store.dir / "obs").iterdir()):
        o = read_json(d / "observation.json", {})
        if o.get("ui_alerts"):
            alerts.append({"obs": o["obs_id"], "ts": o["ts"], "texts": o["ui_alerts"]["texts"]})
    run["ui_alerts"] = alerts
    # 系统弹窗能力的每次介入（preflight 快查关闭/恢复关闭）都列进报告，含诊断证据位置
    run["system_dialogs"] = [{"ts": e["ts"], "status": e.get("status"), "reason": e.get("reason"),
                              "dialog": (e.get("diagnosis") or {}).get("matched"),
                              "screen_tree": (e.get("diagnosis") or {}).get("screen_tree")}
                             for e in read_jsonl(store.events_path)
                             if e.get("event") == "capability_result" and e.get("capability") == "system_dialog"]
    run["experience_candidates"] = [c["id"] for c in xstore.propose_from_run(run)] if propose else []
    write_json(store.dir / "run.json", run)
    (store.dir / "report.md").write_text(render_run(run), encoding="utf-8")
    store.event("run_end", verdict=verdict["verdict"])
    run["dir"] = str(store.dir)
    return run
