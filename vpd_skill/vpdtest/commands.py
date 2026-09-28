"""CLI 子命令实现。每个函数返回可 JSON 序列化的 dict，由 cli.py 输出到 stdout。"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import __version__, cases as case_mod
from .execute import Executor
from .experience import ExperienceStore
from .explore import explore
from .knowledge import KnowledgeStore, import_repo_selectors
from .observe import RunStore
from .runner import run_case
from .templates import crop
from .util import read_json, write_json
from .workspace import RunContext, Workspace


def _session(ws: Workspace, name: str) -> RunStore:
    return RunStore(ws.runs_dir, f"session-{name}", kind="session")


def cmd_doctor(ws: Workspace, a) -> dict:
    out = {"skill_version": __version__, "workspace": str(ws.root), "runs_dir": str(ws.runs_dir),
           "hdc": shutil.which("hdc"), "checks": {}}
    for mod in ("lxml", "cv2", "numpy"):
        try:
            __import__(mod)
            out["checks"][mod] = "ok"
        except ImportError:
            out["checks"][mod] = "missing" + ("（模板定位与回放截图不可用）" if mod in ("cv2", "numpy") else "（必需）")
    if a.device or ws.config.get("device"):
        app = ws.app(a.app or ws.config.get("app", "motor_hm"))
        dev = ws.device(a.device)
        out["preflight"] = dev.preflight(app.bundle)
    return out


def cmd_observe(ws: Workspace, a) -> dict:
    app = ws.app(a.app)
    store = _session(ws, a.session)
    dev = ws.device(a.device, state_path=store.dir / "replay_state.json")
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    obs, tree = ctx.observe(a.label or "observe")
    clickable = []
    if tree is not None and tree.status == "ok":
        for n in tree.nodes:
            if n.visible and n.clickable and n.center:
                texts = tree.subtree_texts(n)
                pol = ctx.policy.check_tap(tree, n.center, "tree", None, app.states.deny_for(obs["page"]["state"]))
                clickable.append({"label": (texts[0] if texts else "")[:30], "center": list(n.center),
                                  "allowed": pol["allowed"], "why": pol["reason"]})
    return {"session": store.run_id, "obs_dir": str(store.dir / "obs" / obs["obs_id"]), "observation": obs,
            "clickable": clickable[:40]}


def cmd_act(ws: Workspace, a) -> dict:
    app = ws.app(a.app)
    store = _session(ws, a.session)
    dev = ws.device(a.device, state_path=store.dir / "replay_state.json")
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    step = {"id": a.step_id or f"A{store._obs_seq + 1}", "action": a.action, "retry": 0,
            "timeout_s": a.timeout, "expect_state": a.expect}
    if a.action in ("tap", "input"):
        if a.point:
            if not a.basis:
                raise ValueError("--point 必须同时给出 --basis（依据，例如截图中看到的文字/图标）")
            step["point"] = [int(v) for v in a.point.split(",")]
            step["target"] = {"label": f"agent:{a.basis}", "strategies": [], "side_effect": "navigation"}
            store.event("agent_proposal", point=step["point"], basis=a.basis)
        else:
            step["target"] = a.target
        step["text"] = a.text
    res = Executor(ctx).run_step(step, "session")
    return {"session": store.run_id, "result": res}


def cmd_run(ws: Workspace, a) -> dict:
    overlay = ExperienceStore(ws.root, ws.load_case(a.case)["app"]).overlay(trial=a.trial) if a.trial else None
    env = dict(kv.split("=", 1) for kv in a.env or [])
    run = run_case(ws, a.case, device_spec=a.device, overlay=overlay, env=env)
    if a.record_replay:
        case_mod.record_replay(ws, a.case, run)
    return _run_brief(run)


def _run_brief(run: dict) -> dict:
    v = run["verdict"]
    return {"run_id": run["run_id"], "case": run["case"]["id"], "verdict": v["verdict"], "label": v["label"],
            "attribution": v["attribution"], "recovered": v["recovered"], "adapted": v["adapted"],
            "first_anomaly": v["first_anomaly"] and {k: v["first_anomaly"].get(k) for k in ("step", "attempt", "status")},
            "findings": [{k: f.get(k) for k in ("result", "step", "id", "reason", "attribution")} for f in v["findings"]],
            "experience_candidates": run["experience_candidates"], "validation_level": run["validation_level"],
            "report": str(Path(run["dir"]) / "report.md")}


def cmd_suite(ws: Workspace, a) -> dict:
    suite = ws.load_suite(a.suite)
    rows = []
    for dev in a.device or [None]:
        for entry in suite["cases"]:
            cid = entry["id"] if isinstance(entry, dict) else entry
            env = entry.get("env", {}) if isinstance(entry, dict) else {}
            rows.append(_run_brief(run_case(ws, cid, device_spec=dev, env=env)))
    return {"suite": a.suite, "results": rows}


def cmd_explore(ws: Workspace, a) -> dict:
    exp = explore(ws, a.app, a.start, a.device, a.budget, a.goal or "", a.prefer)
    return {"run_id": exp["run_id"], "exit": exp["exit"], "coverage": exp["coverage"],
            "transitions": [{k: t[k] for k in ("id", "from", "label", "to", "to_declared")} for t in exp["transitions"]],
            "skipped": len(exp["skipped"]), "anomalies": exp["anomalies"], "report": str(Path(exp["dir"]) / "report.md")}


def cmd_case(ws: Workspace, a) -> dict:
    kn = KnowledgeStore(ws.root)
    if a.case_cmd == "lint":
        case = ws.load_case(a.case)
        return {"case": a.case, "status": case.get("status"), "hash": case_mod.content_hash(case),
                "issues": case_mod.lint(case, ws.app(case["app"]), kn)}
    if a.case_cmd == "from-exploration":
        exp = case_mod.load_exploration(ws, a.run)
        case = case_mod.from_exploration(exp, a.transitions, a.new_id, exp["app"])
        if ws.case_path(a.new_id).exists():
            raise FileExistsError(f"用例 {a.new_id} 已存在")
        write_json(ws.case_path(a.new_id), case)
        return {"case": a.new_id, "path": str(ws.case_path(a.new_id)),
                "issues": case_mod.lint(case, ws.app(case["app"]), kn)}
    if a.case_cmd == "promote":
        case = case_mod.promote(ws, a.case, a.to, kn, a.reviewer, a.allow_offline)
        return {"case": a.case, "status": case["status"], "history": case["history"][-1]}
    raise ValueError(a.case_cmd)


def cmd_knowledge(ws: Workspace, a) -> dict:
    kn = KnowledgeStore(ws.root)
    if a.k_cmd == "list":
        return {"items": [{k: i.get(k) for k in ("id", "kind", "subject", "status", "statement")} for i in kn.items()],
                "conflicts": kn.conflicts()}
    if a.k_cmd == "show":
        return kn.get(a.id) or {"error": f"没有 {a.id}"}
    if a.k_cmd == "add":
        return kn.add(read_json(a.file))
    if a.k_cmd == "confirm":
        return kn.confirm(a.id, a.reviewer, a.basis)
    if a.k_cmd == "resolve":
        return kn.resolve(a.subject, a.keep, a.reviewer, a.reason)
    if a.k_cmd == "import-repo":
        repo = Path(a.repo).resolve()
        out = ws.root / "knowledge" / "repo_selectors" / f"{a.var}.json"
        return import_repo_selectors(repo / a.file, a.var, out, repo)
    raise ValueError(a.k_cmd)


def cmd_experience(ws: Workspace, a) -> dict:
    xs = ExperienceStore(ws.root, a.app)
    if a.x_cmd == "list":
        return {"active": xs.active(), "items": [{k: i.get(k) for k in ("id", "kind", "status", "description", "change")}
                                                 for i in xs.items()]}
    if a.x_cmd == "validate":
        cases = a.cases or [c if isinstance(c, str) else c["id"] for c in ws.load_suite(a.suite)["cases"]]

        def one(cid, dev, overlay):
            return run_case(ws, cid, device_spec=dev, overlay=overlay, propose=False)
        return xs.validate(a.id, cases, a.device, one)
    if a.x_cmd == "adopt":
        return xs.adopt(a.id, a.reviewer)
    if a.x_cmd == "rollback":
        return xs.rollback(a.to_version, a.reason)
    if a.x_cmd == "ledger":
        return {"ledger": xs.ledger()}
    raise ValueError(a.x_cmd)


def cmd_template_crop(ws: Workspace, a) -> dict:
    obs_dir = Path(a.obs_dir)
    shot = obs_dir / "screen.png"
    out = ws.app(a.app).dir / "templates" / f"{a.name}.png"
    crop(shot, [int(v) for v in a.bounds.split(",")], out)
    return {"template": str(out), "source": str(shot), "bounds": a.bounds,
            "next": "在 targets.json 中为目标追加 {kind: template, file: templates/<name>.png, source: <obs>} 并评审"}
