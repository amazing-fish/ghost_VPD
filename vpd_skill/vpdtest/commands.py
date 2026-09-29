"""CLI 子命令实现。每个函数返回可 JSON 序列化的 dict，由 cli.py 输出到 stdout。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from . import __version__, cases as case_mod, search as search_mod
from .execute import Executor
from .experience import ExperienceStore
from .explore import explore
from .knowledge import KnowledgeStore, import_repo_selectors
from .observe import RunStore
from .runner import run_case
from .session import dispatched, effect_unknown, from_session, next_seq, parse_select, summarize, trail
from .templates import crop
from .util import append_jsonl, now_iso, read_json, read_jsonl, write_json
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


def _session_facts(store, dev, app) -> None:
    """每个会话记一次环境事实（只读查询）：生成用例与判断知识适用性时要知道是哪个版本、什么屏幕形态。"""
    if any(e.get("event") == "session_facts" for e in read_jsonl(store.events_path)):
        return
    disp = dev.display_info() if hasattr(dev, "display_info") else {}
    store.event("session_facts", facts={"app": app.id, "app_version": dev.app_version(app.bundle),
                                        "device_form": (disp or {}).get("device_form"), "device": dev.spec,
                                        "validation_level": "offline_replay" if dev.kind == "replay" else "real_device"})


def cmd_observe(ws: Workspace, a) -> dict:
    app = ws.app(a.app)
    store = _session(ws, a.session)
    dev = ws.device(a.device, state_path=store.dir / "replay_state.json")
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    _session_facts(store, dev, app)
    obs, tree = ctx.observe(a.label or "observe")
    store.event("agent_observe", obs_id=obs["obs_id"], label=a.label or "observe", state=obs["page"]["state"])
    clickable = []
    if tree is not None and tree.status == "ok":
        for n in tree.nodes:
            vis = tree.visibility(n) if (n.visible and n.clickable) else None
            if vis and vis["center"]:  # 中心取可见部分；被列表裁掉的条目不列出
                texts = tree.subtree_texts(n)
                pol = ctx.policy.check_tap(tree, vis["center"], "tree", None, app.states.deny_for_page(obs["page"]))
                clickable.append({"label": (texts[0] if texts else "")[:30], "type": n.type, "id": n.id or None,
                                  "center": list(vis["center"]),
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
    _session_facts(store, dev, app)
    seq = next_seq(store)
    step_id = a.step_id or f"A{seq}"
    # 先记请求再执行：进程中途被杀时，会话里仍能看到"请求了什么、没有结果"
    request = {"action": a.action, "select": a.select, "within": a.within, "target": a.target, "point": a.point,
               "basis": a.basis, "side_effect": a.side_effect, "allow_input": a.allow_input, "text": a.text,
               "expect": a.expect, "expect_text": a.expect_text, "timeout": a.timeout, "duration": a.duration,
               "direction": getattr(a, "direction", None), "authorize": sorted(set(a.authorize or [])),
               "supervisor": bool(a.supervisor)}
    store.event("act_request", seq=seq, step_id=step_id, request={k: v for k, v in request.items() if v not in (None, [], False)})
    try:
        step = _act_step(ctx, store, a, step_id)
    except (ValueError, PermissionError) as e:
        store.event("act_rejected", seq=seq, reason=str(e))
        raise
    try:
        res = Executor(ctx).run_step(step, "session")
    except Exception as e:  # 工具缺陷：保留在会话里，现场状态未知
        store.event("act_error", seq=seq, reason=f"{type(e).__name__}: {e}")
        raise
    first, last = (res.get("attempts") or [{}])[0], (res.get("attempts") or [{}])[-1]
    append_jsonl(store.dir / "steps.jsonl", {"seq": seq, "ts": now_iso(), "step": step, "result": res})
    store.event("act_result", seq=seq, outcome=res["outcome"], effect_unknown=effect_unknown(res),
                dispatched=dispatched(res), obs_before=first.get("obs_before"),
                obs_after=res.get("final_obs") or last.get("obs_after"))
    return {"session": store.run_id, "seq": seq, "step_id": step_id, "result": res,
            "diagnosis": diagnose(res)}


def _act_step(ctx, store, a, step_id: str) -> dict:
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
    step = {"id": step_id, "action": a.action, "retry": 0, "timeout_s": a.timeout, "expect_state": a.expect}
    if a.expect_text:
        step["expect_text"] = a.expect_text
    if a.duration:
        step["duration_s"] = a.duration
    if getattr(a, "direction", None):
        step["direction"] = a.direction
    if a.action == "monitor":
        step.update(interval_s=a.interval, until_state=a.expect,
                    watch=dict(kv.split("=", 1) for kv in a.watch or []), stop_on=[])
    if a.action in ("tap", "input"):
        given = [x for x in (a.select, a.target, a.point) if x]
        if len(given) != 1:
            raise ValueError("tap/input 需要且只能给一种目标：--select（现场选择器）/ --target（targets.json）/ --point")
        if a.select:
            step["target"] = parse_select(a.select, a.within, a.side_effect, a.allow_input)
            step["revalidate"] = True  # 下发前复核：页面在观察与下发之间变了就不下发
        elif a.point:
            if not a.basis:
                raise ValueError("--point 必须同时给出 --basis（依据，例如截图中看到的文字/图标）")
            win = _last_window(store)
            if not win:
                raise ValueError("--point 需要先在同一 session 内 observe：坐标只对观测时的窗口尺寸有效")
            step["point"] = [int(v) for v in a.point.split(",")]
            step["point_window"] = win  # 执行前窗口尺寸若已变化（旋转/折叠/分屏），执行器会丢弃该坐标
            step["target"] = {"label": f"agent:{a.basis}", "strategies": [], "side_effect": a.side_effect}
            if a.allow_input:
                step["target"]["allow_input"] = True
            store.event("agent_proposal", point=step["point"], basis=a.basis, window=win, side_effect=a.side_effect)
        else:
            step["target"] = a.target
        step["text"] = a.text
    return step


def diagnose(res: dict) -> Optional[dict]:
    """定位/复核失败时给 Agent 的诊断：多匹配的候选、不可见/被裁剪的数量、复核前后的节点。"""
    last = (res.get("attempts") or [{}])[-1]
    st = last.get("status")
    if st in (None, "ok"):
        return None
    out = {"status": st}
    loc = last.get("locate") or {}
    tries = loc.get("attempts") or []
    if st == "target_ambiguous":
        out["candidates"] = next((t.get("candidates") for t in tries if t.get("status") == "ambiguous"), [])
        out["hint"] = "匹配到多个节点，执行器不挑：用 --within 限定容器/分栏，或改用 id=/xpath= 选择器"
    elif st == "target_partially_visible":
        t = next((t for t in tries if t.get("status") == "partially_visible"), {})
        out.update(visible_fraction=t.get("visible_fraction"), bounds=t.get("bounds"), visible=t.get("visible"),
                   hint="目标只露出一部分（被列表裁剪/遮挡）：先滑动让它完整出现，不点边缘")
    elif st == "target_unresolved":
        out["offscreen"] = sum(t.get("offscreen", 0) for t in tries)
        out["tree_status"] = loc.get("tree_status")
        out["hint"] = ("树里有但在可视区外：先 act swipe 再试" if out["offscreen"] else
                       "当前 UI 树里没有：看截图确认文字；地图等自绘区域（XComponent）不在 UI 树里，选择器无法覆盖")
    elif st == "target_stale":
        out["revalidate"] = last.get("revalidate")
        out["hint"] = "观察与下发之间页面变了：重新 observe 后再决定"
    elif st == "foreground_changed":
        out["foreground"] = last.get("foreground_check")
        out["hint"] = "前台不是被测 APP：可能用户正在用手机，停止操作并报告，不要拉起 APP 抢前台"
    elif st == "policy_denied":
        out["policy"] = last.get("policy") or last.get("policy_context")
    return out


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
            "action_required": v.get("action_required"), "product_failed": v.get("product_failed"),
            "not_verified": [x["id"] for x in v.get("not_verified") or []], "known_issue": v.get("known_issue"),
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
                "issues": case_mod.lint(case, ws.app(case["app"]), kn, ws.policy())}
    if a.case_cmd == "from-exploration":
        exp = case_mod.load_exploration(ws, a.run)
        case = case_mod.from_exploration(exp, a.transitions, a.new_id, exp["app"])
        if ws.case_path(a.new_id).exists():
            raise FileExistsError(f"用例 {a.new_id} 已存在")
        write_json(ws.case_path(a.new_id), case)
        return {"case": a.new_id, "path": str(ws.case_path(a.new_id)),
                "issues": case_mod.lint(case, ws.app(case["app"]), kn, ws.policy())}
    if a.case_cmd == "from-session":
        sdir = ws.runs_dir / f"session-{a.session}"
        if not (sdir / "events.jsonl").exists():
            raise FileNotFoundError(f"没有会话 {sdir}")
        if ws.case_path(a.new_id).exists():
            raise FileExistsError(f"用例 {a.new_id} 已存在")
        case = from_session(sdir, a.new_id, ws.app(a.app), a.goal, a.drop, a.title)
        write_json(ws.case_path(a.new_id), case)
        src = case["sources"][0]
        return {"case": a.new_id, "path": str(ws.case_path(a.new_id)), "steps": len(case["steps"]),
                "excluded": src["excluded"], "rejected": len(src["rejected"]), "incomplete": src["incomplete"],
                "issues": case_mod.lint(case, ws.app(case["app"]), kn, ws.policy()),
                "next": "补齐 purpose / preconditions / assertions（oracle_refs 须独立于本会话）/ cleanup / knowledge_used 后 case lint"}
    if a.case_cmd == "promote":
        case = case_mod.promote(ws, a.case, a.to, kn, a.reviewer, a.allow_offline, a.known_issue)
        return {"case": a.case, "status": case["status"], "product_status": case.get("product_status"),
                "history": case["history"][-1]}
    raise ValueError(a.case_cmd)


def cmd_session(ws: Workspace, a) -> dict:
    sdir = ws.runs_dir / f"session-{a.session}"
    if not (sdir / "events.jsonl").exists():
        raise FileNotFoundError(f"没有会话 {sdir}")
    return summarize(trail(sdir))


def cmd_knowledge(ws: Workspace, a) -> dict:
    kn = KnowledgeStore(ws.root)
    if a.k_cmd == "search":
        facts = dict(kv.split("=", 1) for kv in a.env or [])
        if a.device:  # 只读查询：版本与屏幕形态，不发任何输入
            app = ws.app(a.app)
            dev = ws.device(a.device)
            facts.update(app_version=dev.app_version(app.bundle),
                         device_form=(dev.display_info() or {}).get("device_form"))
        for k in ("app_version", "device_form"):
            if getattr(a, k, None):
                facts[k] = getattr(a, k)
        return search_mod.search(ws, " ".join(a.query), a.app, a.role, facts, a.limit)
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
