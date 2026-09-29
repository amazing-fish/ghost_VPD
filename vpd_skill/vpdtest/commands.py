"""CLI 子命令实现。每个函数返回可 JSON 序列化的 dict，由 cli.py 输出到 stdout。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

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
    optional = {"cv2": "（模板定位与回放截图不可用）", "numpy": "（模板定位与回放截图不可用）",
                "hmdriver2": "（console 投屏回退为 snapshot_display 轮询，约 1.6 fps）"}
    for mod in ("lxml", "cv2", "numpy", "hmdriver2"):
        try:
            __import__(mod)
            out["checks"][mod] = "ok"
        except ImportError:
            out["checks"][mod] = "missing" + optional.get(mod, "（必需）")
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
            vis = tree.visibility(n) if (n.visible and n.clickable) else None
            if vis and vis["center"]:  # 中心取可见部分；被列表裁掉的条目不列出
                texts = tree.subtree_texts(n)
                pol = ctx.policy.check_tap(tree, vis["center"], "tree", None, app.states.deny_for_page(obs["page"]))
                clickable.append({"label": (texts[0] if texts else "")[:30], "center": list(vis["center"]),
                                  "visible_fraction": vis["fraction"], "allowed": pol["allowed"], "why": pol["reason"]})
    return {"session": store.run_id, "obs_dir": str(store.dir / "obs" / obs["obs_id"]), "observation": obs,
            "clickable": clickable[:40]}


def _last_window(store) -> Optional[list]:
    for d in sorted((store.dir / "obs").iterdir(), reverse=True):
        obs = read_json(d / "observation.json", {})
        win = (obs.get("layout") or {}).get("window_px")
        if win:
            return win
    return None


def cmd_act(ws: Workspace, a) -> dict:
    app = ws.app(a.app)
    store = _session(ws, a.session)
    dev = ws.device(a.device, state_path=store.dir / "replay_state.json")
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    # 单步授权：由人看过当前观测后给出。supervised 类（控车/运动）必须写明在场监护人；
    # act 是一次性进程，没有跨步骤的运动看门狗，停车由监护人或下一条 act（safe_stop 目标）完成
    granted = set(a.authorize or [])
    auth = ctx.policy.check_authorizations(sorted(granted), granted, a.supervisor, {"safe_stop": ["human"]})
    if auth["not_grantable"]:
        raise ValueError(f"不可授权：{auth['not_grantable']}（policy 硬禁止）")
    if auth["missing_supervisor"]:
        raise ValueError(f"{auth['missing_supervisor']} 需要在场监护人：加 --supervisor <姓名/工号>")
    ctx.authorized, ctx.supervisor = granted, a.supervisor
    store.event("authorization", requested=sorted(granted), granted=sorted(granted), supervisor=bool(a.supervisor))
    step = {"id": a.step_id or f"A{store._obs_seq + 1}", "action": a.action, "retry": 0,
            "timeout_s": a.timeout, "expect_state": a.expect}
    if a.duration:
        step["duration_s"] = a.duration
    if a.action == "monitor":
        step.update(interval_s=a.interval, until_state=a.expect,
                    watch=dict(kv.split("=", 1) for kv in a.watch or []), stop_on=[])
    if a.action in ("tap", "input"):
        if a.point:
            if not a.basis:
                raise ValueError("--point 必须同时给出 --basis（依据，例如截图中看到的文字/图标）")
            win = _last_window(store)
            if not win:
                raise ValueError("--point 需要先在同一 session 内 observe：坐标只对观测时的窗口尺寸有效")
            step["point"] = [int(v) for v in a.point.split(",")]
            step["point_window"] = win  # 执行前窗口尺寸若已变化（旋转/折叠/分屏），执行器会丢弃该坐标
            step["target"] = {"label": f"agent:{a.basis}", "strategies": [], "side_effect": a.side_effect}
            store.event("agent_proposal", point=step["point"], basis=a.basis, window=win, side_effect=a.side_effect)
        else:
            step["target"] = a.target
        step["text"] = a.text
    res = Executor(ctx).run_step(step, "session")
    return {"session": store.run_id, "result": res}


def cmd_run(ws: Workspace, a) -> dict:
    overlay = ExperienceStore(ws.root, ws.load_case(a.case)["app"]).overlay(trial=a.trial) if a.trial else None
    env = dict(kv.split("=", 1) for kv in a.env or [])
    run = run_case(ws, a.case, device_spec=a.device, overlay=overlay, env=env, authorize=a.authorize,
                   supervisor=a.supervisor)
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
    devices = a.device or [None]
    # 用例集进度写在自己的 events.jsonl（runs/suite-*），每条用例仍是独立的 run-* 目录
    sstore = RunStore(ws.runs_dir, kind="suite")
    sstore.event("suite_start", suite=a.suite, total=len(suite["cases"]) * len(devices),
                 cases=[e["id"] if isinstance(e, dict) else e for e in suite["cases"]], devices=devices)
    rows = []
    for dev in devices:
        for entry in suite["cases"]:
            cid = entry["id"] if isinstance(entry, dict) else entry
            env = entry.get("env", {}) if isinstance(entry, dict) else {}
            sstore.event("case_start", case=cid, device=dev, index=len(rows) + 1)
            row = _run_brief(run_case(ws, cid, device_spec=dev, env=env))
            sstore.event("case_done", case=cid, device=dev, run_id=row["run_id"], verdict=row["verdict"])
            rows.append(row)
    sstore.event("suite_end", verdicts=[r["verdict"] for r in rows])
    return {"suite": a.suite, "suite_run": sstore.run_id, "results": rows}


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
                "issues": case_mod.lint(case, ws.app(case["app"]), kn, ws.policy())}
    if a.case_cmd == "from-exploration":
        exp = case_mod.load_exploration(ws, a.run)
        case = case_mod.from_exploration(exp, a.transitions, a.new_id, exp["app"])
        if ws.case_path(a.new_id).exists():
            raise FileExistsError(f"用例 {a.new_id} 已存在")
        write_json(ws.case_path(a.new_id), case)
        return {"case": a.new_id, "path": str(ws.case_path(a.new_id)),
                "issues": case_mod.lint(case, ws.app(case["app"]), kn, ws.policy())}
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


def cmd_console(ws: Workspace, a) -> dict:
    """阻塞运行；启动后先打印一行 {"console": url}，退出时再输出汇总。"""
    import json
    import sys

    from .console.server import Console, is_loopback, serve
    if not is_loopback(a.host):  # 先校验，避免连上设备、起了帧流才报错
        raise ValueError(f"控制台只允许绑定 IPv4 回环地址（127.0.0.1 / localhost），拒绝 {a.host}")
    spec = None if a.no_device else (a.device or ws.config.get("device"))
    if spec and not spec.startswith("hdc:"):
        raise ValueError("控制台只支持真机 hdc:<serial>；回放 world 请用 run/observe")
    device = ws.device(spec) if spec else None
    if device is not None and not device.serial:
        raise ValueError("hdc list targets 没有设备")
    store = RunStore(ws.runs_dir, kind="console")
    console = Console(device, store, ws.runs_dir, source=a.source, allow_input=not a.view_only,
                      max_fps=a.max_fps, keep_awake_min=a.keep_awake)

    def ready(url, port):
        print(json.dumps({"console": url, "port": port, "device": spec, "input": console.allow_input,
                          "note": "URL 含访问 token，只在本机浏览器打开，不要外传"}, ensure_ascii=False), flush=True)
        sys.stdout.flush()

    return serve(console, a.host, a.port, a.duration, on_ready=ready)


def cmd_template_crop(ws: Workspace, a) -> dict:
    obs_dir = Path(a.obs_dir)
    shot = obs_dir / "screen.png"
    out = ws.app(a.app).dir / "templates" / f"{a.name}.png"
    crop(shot, [int(v) for v in a.bounds.split(",")], out)
    return {"template": str(out), "source": str(shot), "bounds": a.bounds,
            "next": "在 targets.json 中为目标追加 {kind: template, file: templates/<name>.png, source: <obs>} 并评审"}
