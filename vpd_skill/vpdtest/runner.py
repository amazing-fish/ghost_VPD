"""回归执行：前置检查 → 执行步骤 → 清理 → 断言判定 → 报告 → 经验候选。"""
from __future__ import annotations

from typing import Optional

from . import __version__
from .cases import content_hash
from .execute import Executor
from .experience import ExperienceStore
from .knowledge import KnowledgeStore
from .observe import RunStore
from .report import render_run
from .tree import UiTree
from .util import now_iso, read_json, write_json
from .verdict import PASSED, case_verdict, evaluate_assertion
from .workspace import RunContext, Workspace


def _env_check(case: dict, facts: dict) -> dict:
    unmet = [{"key": k, "required": v, "actual": facts.get(k)}
             for k, v in case.get("env_requirements", {}).items() if facts.get(k) != v]
    return {"ok": not unmet, "facts": facts, "required": case.get("env_requirements", {}), "unmet": unmet}


def run_case(ws: Workspace, case_id: Optional[str] = None, case: Optional[dict] = None, device_spec: Optional[str] = None,
             overlay=None, env: Optional[dict] = None, run_id: Optional[str] = None, propose: bool = True) -> dict:
    case = case or ws.load_case(case_id)
    app = ws.app(case["app"])
    store = RunStore(ws.runs_dir, run_id, kind="run")
    spec = device_spec or ws.config.get("device")
    device = ws.device(spec, state_path=store.dir / "replay_state.json")
    xstore = ExperienceStore(ws.root, app.id)
    overlay = overlay or xstore.overlay()
    ctx = RunContext(ws, app, device, store, overlay, mode="regression")
    knowledge = KnowledgeStore(ws.root)
    started = now_iso()
    store.event("run_start", case=case["id"], device=device.spec, overlay=overlay.describe())

    pre = device.preflight(app.bundle)
    store.event("preflight", ok=pre["ok"], checks=pre["checks"])
    facts = {**device.env_facts(), **(env or {})}
    envc = _env_check(case, facts)
    steps, cleanup = [], []
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
        for i, c in enumerate(case.get("cleanup", [])):
            cleanup.append(ex.run_step({"critical": False, "retry": 0, **c, "id": c.get("id", f"C{i + 1}")}, case["id"]))
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
    verdict = case_verdict(pre, envc, steps, assertions, knowledge)
    refs = sorted({r for x in case.get("assertions", []) + case["steps"] for r in x.get("oracle_refs", [])})
    run = {"run_id": store.run_id, "skill_version": __version__, "started": started, "finished": now_iso(),
           "validation_level": "offline_replay" if device.kind == "replay" else "real_device",
           "case": {"id": case["id"], "title": case.get("title"), "version": case.get("version"),
                    "status": case.get("status"), "hash": content_hash(case), "sources": case.get("sources", [])},
           "informal": case.get("status") != "regression",
           "app": {"id": app.id, "bundle": app.bundle, "version": pre.get("app_version")},
           "device": pre.get("device") or device.info(), "device_spec": device.spec,
           "policy": {"version": ctx.policy.version, "hash": ctx.policy.hash}, "overlay": overlay.describe(),
           "env": envc, "preflight": pre, "steps": steps, "cleanup": cleanup, "assertions": assertions,
           "knowledge_snapshot": knowledge.oracle_status(refs)["items"] if refs else [],
           "verdict": verdict, "actions": ctx.actions}
    run["experience_candidates"] = [c["id"] for c in xstore.propose_from_run(run)] if propose else []
    write_json(store.dir / "run.json", run)
    (store.dir / "report.md").write_text(render_run(run), encoding="utf-8")
    store.event("run_end", verdict=verdict["verdict"])
    run["dir"] = str(store.dir)
    return run
