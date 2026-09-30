"""CLI 子命令实现。每个函数返回可 JSON 序列化的 dict，由 cli.py 输出到 stdout。"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

from . import __version__, cases as case_mod, search as search_mod
from .execute import Executor
from .experience import ExperienceStore
from .explore import explore
from .knowledge import KnowledgeStore, import_repo_selectors
from .observe import RunStore
from .params import Params
from .runner import run_case
from .session import (IDENTITY_KEYS, SessionMismatch, dispatched, effect_unknown, from_session, next_seq,
                      parse_select, summarize, trail)
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


def cmd_unlock(ws: Workspace, a) -> dict:
    """唤醒并解锁设备。PIN 只从 secrets.LOCK_PIN / VPD_SECRET_LOCK_PIN 读，不接受命令行传入，也不回显。
    防重入标记在可能输入 PIN 之前就以独占方式创建：进程中途被杀或两个进程同时解锁，标记都已在，
    下一次一律不再输入，直到人删除标记（连续输错会锁机）。只有确认已解锁、或确认没输过 PIN 才删掉。"""
    dev = ws.device(a.device)
    guard = ws.runs_dir / "unlock_guard.json"
    if guard.exists():
        return {"status": "refused", "action_required": True, "guard": str(guard),
                "reason": "上次 PIN 被拒，已停止自动解锁：请人工解锁并核对 PIN 后删除该文件"}
    pin = os.environ.get("VPD_SECRET_LOCK_PIN") or (ws.config.get("secrets") or {}).get("LOCK_PIN")
    if pin is not None and not re.fullmatch(r"\d{4,16}", str(pin)):
        return {"status": "refused", "action_required": True, "reason": "LOCK_PIN 不是 4-16 位数字，未输入"}
    if not hasattr(dev, "wake_unlock"):
        return {"status": "unsupported", "device": dev.spec}
    if pin is not None:
        try:
            write_json(guard, {"ts": now_iso(), "device": dev.spec, "status": "in_progress",
                               "reason": "正在自动解锁；进程若中途退出，本文件保留，需人工确认屏幕状态后删除"}, exclusive=True)
        except FileExistsError:  # 另一个进程刚开始解锁
            return {"status": "refused", "action_required": True, "guard": str(guard),
                    "reason": "另一个解锁进程正在进行或刚中途退出：请人工确认屏幕状态后删除该文件"}
    res = dev.wake_unlock(str(pin) if pin is not None else None)  # 抛异常时输没输 PIN 不确定：标记保留
    if pin is not None:
        if res["status"] == "unlocked" or (res["status"] != "pin_rejected" and not res.get("pin_entered")):
            guard.unlink()
        else:
            # 输过 PIN 却不能确认解开（被拒/输到一半/之后查不到状态）：保留标记，停止自动输入
            write_json(guard, {"ts": now_iso(), "device": dev.spec, "status": res["status"], "reason": res.get("reason")})
    return {"device": dev.spec, **res, "action_required": res["status"] != "unlocked"}


# 会话头：第一次 observe/act 时写入并固定；之后每次都与它比较，APP 或设备不同就拒绝
SESSION_SCHEMA = 2
# act_request 只记这些字段（白名单）：新加的命令行参数默认不落盘，输入文字只记长度
REQUEST_FIELDS = ("action", "select", "within", "target", "point", "basis", "side_effect", "allow_input", "text_len", "clear",
                  "expect", "expect_text", "timeout", "duration", "direction", "authorize", "supervisor")


def _open_session(ws, a):
    """observe/act 的共同入口：取会话、设备、上下文，写入或核对会话头，挂上脱敏。"""
    app = ws.app(a.app)
    store = _session(ws, a.session)
    dev = ws.device(a.device, state_path=store.dir / "replay_state.json")
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    _session_redaction(ws, ctx, store)
    ident = {"app": app.id, "bundle": app.bundle, "device": dev.spec,
             "validation_level": "offline_replay" if dev.kind == "replay" else "real_device"}
    # 会话只从 trail() 读：身份核对（旧格式按第一条 session_facts）、没有会话头的旧会话拒绝续写都在里面
    try:
        tr = trail(store.dir, expect={k: ident[k] for k in IDENTITY_KEYS})
    except SessionMismatch as e:
        store.event("session_mismatch_rejected", diff=e.diff)
        raise
    # 每条 act 是新进程：会话里输入过密钥（剪贴板可能留有原文）要沿用到后续观测
    ctx.observer.secret_typed = tr["secret_typed"]
    if tr["identity"] is None:
        disp = dev.display_info() if hasattr(dev, "display_info") else {}
        store.event("session_open", schema=SESSION_SCHEMA, identity=ident,
                    facts={**ident, "app_version": dev.app_version(app.bundle),
                           "device_form": (disp or {}).get("device_form")},
                    redaction={"secret_names": len(_secret_names(ws)), "fragments_min": 6})
    return app, store, dev, ctx


def _secret_names(ws) -> set:
    names = {k[len("VPD_SECRET_"):] for k, v in os.environ.items() if k.startswith("VPD_SECRET_") and v}
    return names | {k for k, v in (ws.config.get("secrets") or {}).items() if v}


def _session_redaction(ws, ctx, store) -> None:
    """会话没有用例参数：按已配置的密钥值（VPD_SECRET_* / config.local.json secrets）脱敏 UI 树与事件，
    页面上出现这些值时不截图——和 run 的脱敏规则一致。"""
    names = _secret_names(ws)
    if names:
        p = Params({n: {"secret": n} for n in names}, ws.config.get("secrets"), store.run_id)
        ctx.params = ctx.observer.params = p
        store.redactor = p.redact


def cmd_observe(ws: Workspace, a) -> dict:
    app, store, dev, ctx = _open_session(ws, a)
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
            "ui_alerts": obs.get("ui_alerts"), "clickable": clickable[:40]}


def _last_window(store) -> Optional[list]:
    for d in sorted((store.dir / "obs").iterdir(), reverse=True):
        obs = read_json(d / "observation.json", {})
        win = (obs.get("layout") or {}).get("window_px")
        if win:
            return win
    return None


def cmd_act(ws: Workspace, a) -> dict:
    app, store, dev, ctx = _open_session(ws, a)
    seq = next_seq(store)
    step_id = a.step_id or f"A{seq}"
    # 先记请求再执行：进程中途被杀时，会话里仍能看到"请求了什么、没有结果"
    # 输入文字不进请求事件（policy 还没检查，可能是误输入的密码），连摘要也不记：只记长度。
    # 允许输入、目标非敏感、且不等于任何已配置密钥时，才在执行记录里保留原文（供复测）
    raw = {"action": a.action, "select": a.select, "within": a.within, "target": a.target, "point": a.point,
           "basis": a.basis, "side_effect": a.side_effect, "allow_input": a.allow_input,
           "text_len": len(a.text) if a.text is not None else None, "clear": a.clear, "expect": a.expect,
           "expect_text": a.expect_text, "timeout": a.timeout, "duration": a.duration,
           "direction": getattr(a, "direction", None), "authorize": sorted(set(a.authorize or [])),
           "supervisor": bool(a.supervisor)}
    request = {k: raw[k] for k in REQUEST_FIELDS if raw.get(k) not in (None, [], False)}
    store.event("act_request", seq=seq, step_id=step_id, request=request)
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
    keep = _input_value_keepable(ctx, step, res)
    append_jsonl(store.dir / "steps.jsonl", {"seq": seq, "ts": now_iso(), "step": _scrub_step(step),
                                             "result": _scrub_input(res),
                                             **({"input_value": a.text} if keep else {})})
    store.event("act_result", seq=seq, outcome=res["outcome"], effect_unknown=effect_unknown(res),
                dispatched=dispatched(res), obs_before=first.get("obs_before"),
                obs_after=res.get("final_obs") or last.get("obs_after"))
    diag = diagnose(res)
    # cli 只看顶层 action_required 决定退出码 2：诊断里要求人工接管的，提到顶层
    return {"session": store.run_id, "seq": seq, "step_id": step_id, "result": _scrub_input(res),
            "input_value_recorded": keep if step.get("action") == "input" else None, "diagnosis": diag,
            **({"action_required": diag["action_required"]} if (diag or {}).get("action_required") else {})}


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
    if a.action == "swipe" and a.within:
        # 在指定容器里滑：弹出的下拉/面板和背后页面都是可滚动容器时，默认会滑到面积最大的那个
        k, _, v = a.within.partition("=")
        step["within"] = {k: v}
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
        if a.clear:
            step["clear"] = True
    return step


def _input_value_keepable(ctx, step: dict, res: dict) -> bool:
    if step.get("action") != "input" or not step.get("text"):
        return False
    _, target = ctx.app.target(step["target"])
    allowed = any((a.get("policy") or {}).get("allowed") for a in res.get("attempts", []))
    # ctx.params 由 _session_redaction 按全部已配置密钥建立；包含密钥或其片段就不记原文
    return allowed and not target.get("secret") and not (ctx.params and ctx.params.is_sensitive(step["text"]))


def _scrub_step(step: dict) -> dict:
    return {**step, "text": None, "text_len": len(step["text"])} if step.get("text") else step


def _scrub_input(res: dict) -> dict:
    """执行记录里的 input.ref 是原文（act 没有 ${var:x} 引用）：换成长度。"""
    atts = []
    for a in res.get("attempts", []):
        if (a.get("input") or {}).get("ref") is not None:
            a = {**a, "input": {**a["input"], "ref": None}}
        atts.append(a)
    return {**res, "attempts": atts}


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
        out["state_before"] = last.get("state_before")
        if last.get("state_before") == "out_of_app" or all(t.get("status") == "skipped_no_tree" for t in tries):
            out["hint"] = "没有被测 APP 的 UI 树（APP 不在前台/被系统弹层遮住）：先 observe 确认前台，必要时 act launch"
        elif out["offscreen"]:
            out["hint"] = "树里有但在可视区外：先 act swipe 再试"
        else:
            out["hint"] = ("当前 UI 树里没有：看截图确认文字；地图等自绘区域（XComponent）不在 UI 树里，选择器无法覆盖；"
                           "通知横幅盖住的部分也会从树里消失，几秒后重新 observe 再试")
    elif st == "target_stale":
        out["revalidate"] = last.get("revalidate")
        out["hint"] = "观察与下发之间页面变了：重新 observe 后再决定"
    elif st == "foreground_changed":
        out["foreground"] = last.get("foreground_check")
        out["hint"] = "前台不是被测 APP：可能用户正在用手机，停止操作并报告，不要拉起 APP 抢前台"
    elif st == "policy_denied":
        out["policy"] = last.get("policy") or last.get("policy_context")
    elif st in ("state_indeterminate", "screen_locked"):
        gate = (last.get("locate") or {}).get("gate") or last
        out.update(state_check=gate.get("state_check"), foreground_check=gate.get("foreground_check"),
                   action_required=True,
                   hint="查不到锁屏/前台状态，没有发任何手势：先人工确认手机状态（必要时 vpdtest unlock），再重新 observe"
                   if st == "state_indeterminate" else "手机锁屏/熄屏，没有下发：vpdtest unlock 后重新 observe")
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
        for k in ("app_version", "device_form"):
            if getattr(a, k, None):
                facts[k] = getattr(a, k)
        if a.device:  # 只读查询：版本与屏幕形态，不发任何输入；实测值优先于声明
            app = ws.app(a.app)
            dev = ws.device(a.device)
            measured = {"app_version": dev.app_version(app.bundle),
                        "device_form": (dev.display_info() or {}).get("device_form")}
            facts.update({k: v for k, v in measured.items() if v})
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
