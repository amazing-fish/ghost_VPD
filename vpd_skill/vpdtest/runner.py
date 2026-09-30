"""回归执行：前置检查 → 执行步骤 → 清理 → 断言判定 → 报告 → 经验候选。"""
from __future__ import annotations

from typing import Optional

from . import __version__
from . import layout as layout_mod
from . import safety as safety_mod
from .cases import content_hash
from .execute import Executor
from .experience import ExperienceStore
from .knowledge import KnowledgeStore
from .observe import RunStore
from .params import Params
from .report import render_run
from .tree import UiTree
from .util import now_iso, read_json, read_jsonl, write_json
from .verdict import PASSED, case_verdict, evaluate_assertion
from .workspace import RunContext, Workspace


def _env_check(case: dict, facts: dict) -> dict:
    unmet = [{"key": k, "required": v, "actual": facts.get(k)}
             for k, v in case.get("env_requirements", {}).items() if facts.get(k) != v]
    return {"ok": not unmet, "facts": facts, "required": case.get("env_requirements", {}), "unmet": unmet}


def run_facts(app, pre: dict, declared: dict) -> dict:
    """知识适用性比较用的事实：执行器实际查到的优先；--env 声明只补充查不到的项，不能覆盖实测值
    （否则声明一个旧版本号就能让版本范围不符的判据"适用"，得出假通过）。"""
    disp = pre.get("display") or {}
    measured = {"app": app.id, "app_version": pre.get("app_version"), "device_form": disp.get("device_form")}
    out = {k: v for k, v in (declared or {}).items() if v not in (None, "")}
    out.update({k: v for k, v in measured.items() if v not in (None, "")})
    for k in measured:
        out.setdefault(k, None)
    return out


def run_case(ws: Workspace, case_id: Optional[str] = None, case: Optional[dict] = None, device_spec: Optional[str] = None,
             overlay=None, env: Optional[dict] = None, run_id: Optional[str] = None, propose: bool = True,
             authorize: Optional[list] = None, supervisor: Optional[str] = None) -> dict:
    case = case or ws.load_case(case_id)
    app = ws.app(case["app"])
    store = RunStore(ws.runs_dir, run_id, kind="run")
    spec = device_spec or ws.config.get("device")
    device = ws.device(spec, state_path=store.dir / "replay_state.json")
    xstore = ExperienceStore(ws.root, app.id)
    overlay = overlay or xstore.overlay()
    ctx = RunContext(ws, app, device, store, overlay, mode="regression")
    knowledge = KnowledgeStore(ws.root)  # 前置检查后再绑定本次运行事实（APP/版本/形态/声明的环境）
    started = now_iso()
    store.event("run_start", case=case["id"], device=device.spec, overlay=overlay.describe())

    pre = device.preflight(app.bundle)
    store.event("preflight", ok=pre["ok"], checks=pre["checks"])
    ctx.observer.display = pre.get("display")  # 复用前置检查读到的显示参数，不重复查询
    facts = {**device.env_facts(), **(env or {})}
    envc = _env_check(case, facts)
    # 形态要求（例如只适用于折叠屏展开态的用例）：启动前按显示参数判定，不满足记为环境阻塞而不是失败
    pre_layout = layout_mod.profile(None, pre.get("display"), ctx.observer.layout_cfg)
    lay_unmet = layout_mod.check_requirements(case.get("layout_requirements", {}), pre_layout)
    if lay_unmet:
        envc = {**envc, "ok": False, "unmet": envc["unmet"] + lay_unmet}
    envc["layout"] = {"required": case.get("layout_requirements", {}), "display_profile": pre_layout}
    knowledge.bind(run_facts(app, pre, facts))
    # 参数与密钥：密钥只从环境变量/本地配置读取；缺失时判环境阻塞，不向输入框写空值
    params = Params(case.get("test_data"), ws.config.get("secrets"), store.run_id)
    ctx.params = ctx.observer.params = params
    store.redactor = params.redact  # 事件里的设备输出/错误信息也可能带出输入值：写 events.jsonl 前同样脱敏
    unmet = [{"key": f"secret.{n}", "required": f"环境变量 VPD_SECRET_{n}", "actual": "未设置"} for n in params.missing]
    # 授权：用例只能申请 gated/supervised 类副作用，必须由本次运行显式授予（--authorize），硬禁止类无法授予；
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
    ctx.authorized = set(requested) & set(authorize or [])
    ctx.supervisor = supervisor
    ctx.motion = safety_mod.MotionGuard(case.get("safety"), ctx.clock, store)
    store.event("authorization", requested=requested, granted=sorted(ctx.authorized), supervisor=bool(supervisor))
    steps, cleanup, safety_stops = [], [], []
    if pre["ok"] and envc["ok"]:
        ex = Executor(ctx)
        halted = False
        for st in case["steps"]:
            if halted:
                steps.append({"step_id": st["id"], "action": st["action"], "outcome": "not_run",
                              "critical": st.get("critical", True), "attempts": [], "recoveries": [], "adapted": False,
                              "oracle_refs": st.get("oracle_refs", [])})
                continue
            res = ex.run_step(st, case["id"])
            steps.append(res)
            store.event("step_done", step=st["id"], outcome=res["outcome"])
            if res["outcome"] not in PASSED and res["critical"]:
                halted = True
                if ctx.motion.armed:
                    # 运动中出现首次关键失败：先安全停止，再清理。首次失败原样保留在该步骤记录里
                    safety_stops.append(safety_mod.safe_stop(ex, "critical_step_failed",
                                                             {"step": st["id"], "status": res.get("final_status")}))
        if ctx.motion.armed and (case.get("safety") or {}).get("stop_at_end", True):
            safety_stops.append(safety_mod.safe_stop(ex, "case_end_still_moving"))
        for i, c in enumerate(case.get("cleanup", [])):
            res = ex.run_step({"critical": False, "retry": 0, **c, "id": c.get("id", f"C{i + 1}")}, case["id"])
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
        return read_json(d / "observation.json"), (UiTree(read_json(tp)) if tp.exists() else None)

    by_id = {s["step_id"]: s for s in steps}
    assertions = [evaluate_assertion(a, by_id, load, knowledge, app) for a in case.get("assertions", [])]
    verdict = case_verdict(pre, envc, steps, assertions, knowledge, safety_stops, cleanup,
                           step_ids={s["id"] for s in case["steps"]})
    known = case.get("product_status") or {}
    if known.get("status") == "known_failure":
        # 已知缺陷用例：结论照常判定，另外说明缺陷是否仍按同一特征复现（不把失败改成通过）
        sig = verdict.get("failure_signature")
        verdict["known_issue"] = {"ref": known.get("issue"), "expected_signature": known.get("signature"),
                                  "reproduced": verdict["verdict"] == "failed" and sig == known.get("signature"),
                                  "note": "仍按同一特征复现" if (verdict["verdict"] == "failed" and sig == known.get("signature"))
                                  else "未按已知特征复现：可能已修复、路径变化或本次被其他问题挡住，需人工核对"}
    refs = sorted({r for x in case.get("assertions", []) + case["steps"] for r in x.get("oracle_refs", [])})
    run = {"run_id": store.run_id, "skill_version": __version__, "started": started, "finished": now_iso(),
           "validation_level": "offline_replay" if device.kind == "replay" else "real_device",
           "case": {"id": case["id"], "title": case.get("title"), "version": case.get("version"),
                    "status": case.get("status"), "hash": content_hash(case), "sources": case.get("sources", [])},
           "informal": case.get("status") != "regression",
           "app": {"id": app.id, "bundle": app.bundle, "version": pre.get("app_version")},
           "device": pre.get("device") or device.info(), "device_spec": device.spec,
           "policy": {"version": ctx.policy.version, "hash": ctx.policy.hash}, "overlay": overlay.describe(),
           "layout": {"display": pre.get("display"), "profile": pre_layout,
                      "seen": ctx.observer.layouts_seen},
           "env": envc, "preflight": pre, "steps": steps, "cleanup": cleanup, "assertions": assertions,
           "knowledge_snapshot": knowledge.oracle_status(refs)["items"] if refs else [],
           "facts": knowledge.facts, "knowledge_used": case.get("knowledge_used", []), "goal": case.get("goal"),
           "verdict": verdict, "actions": ctx.actions, "seed": ctx.seed,
           "test_data": params.public(), "authorized": sorted(ctx.authorized),
           "supervision": {"supervisor": supervisor,
                           "supervised_granted": sorted(ctx.authorized & ctx.policy.supervised_side_effects),
                           "safety": case.get("safety")},
           "motion": ctx.motion.history, "safety_stops": safety_stops,
           "measurements": [{"step": s["step_id"], **{k: v for k, v in s["measurement"].items()}}
                            for s in steps if s.get("measurement")]}
    # 运行中所有观测里出现过的页面内失败提示（ui_alert 事件），按时间列出
    alerts = []
    for d in sorted((store.dir / "obs").iterdir()):
        o = read_json(d / "observation.json", {})
        if o.get("ui_alerts"):
            alerts.append({"obs": o["obs_id"], "ts": o["ts"], "texts": o["ui_alerts"]["texts"]})
    run["ui_alerts"] = alerts
    # 截图被系统弹层挡下的观测、输入过密钥（剪贴板残留）：都列进报告，由人收尾
    run["system_overlays"] = [{"obs": e["obs_id"], "ts": e["ts"], "windows": e["windows"]}
                              for e in read_jsonl(store.events_path) if e.get("event") == "system_overlay"]
    run["clipboard_may_hold_secret"] = ctx.observer.secret_typed
    run = params.redact(run)
    run["experience_candidates"] = [c["id"] for c in xstore.propose_from_run(run)] if propose else []
    write_json(store.dir / "run.json", run)
    (store.dir / "report.md").write_text(render_run(run), encoding="utf-8")
    store.event("run_end", verdict=verdict["verdict"])
    run["dir"] = str(store.dir)
    return run
