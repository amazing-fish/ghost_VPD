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
                      parse_select, summarize, trail, session_lock, step_options)
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
    from .unlock import unlock_device
    dev = ws.device(a.device)
    store = RunStore(ws.runs_dir, kind="unlock")
    return unlock_device(ws, dev, store, getattr(a, "lock_secret", None))


def cmd_sysdiag(ws: Workspace, a) -> dict:
    """只读整屏诊断：窗口表 + 整屏布局树 + 截图（APP 白名单） + 已登记系统弹窗比对。
    用途：登记新系统弹窗时取证；排查"preflight 全绿但屏幕被盖住"的现场。
    --dismiss 时对命中且已评审（class=benign, review=reviewed）的弹窗执行原子能力关闭。"""
    from .sysdialog import diagnose_cover, load_dialogs, resolve_screen_cover
    app = ws.app(a.app)
    dev = ws.device(a.device)
    store = RunStore(ws.runs_dir, kind="sysdiag")
    store.event("sysdiag_start", device=dev.spec)
    oid, d = store.next_obs_dir()
    diag = diagnose_cover(dev, store, d, load_dialogs(ws.root))
    out = {"run": store.run_id, "device": dev.spec, "obs": oid,
           **{k: v for k, v in diag.items() if not k.startswith("_")}}
    policy = ws.policy()
    fg = dev.foreground()
    fg_after = fg
    if not policy.screenshot_allowed(fg):
        out["screenshot"] = {"status": "withheld", "reason": f"前台 {fg} 不在截图 APP 白名单中"}
    else:
        r = dev.screenshot(d / "screen.png")
        fg_after = dev.foreground()
        if r.ok and (d / "screen.png").exists():
            if not policy.screenshot_allowed(fg_after):
                (d / "screen.png").unlink()
                out["screenshot"] = {"status": "withheld", "reason": f"末查前台 {fg_after} 不在 APP 白名单中，已删除截图"}
            else:
                out["screenshot"] = {"status": "ok", "path": store.rel(d / "screen.png")}
        else:
            out["screenshot"] = {"status": "error", "error_class": r.error_class, "error": r.error}
    out.update(foreground=fg, foreground_after=fg_after)
    if a.dismiss and diag.get("matched"):
        out["dismiss"] = resolve_screen_cover(ws, dev, store)
    write_json(d / "observation.json", {k: v for k, v in out.items() if not k.startswith("_")})
    store.event("sysdiag_end", matched=diag.get("matched"), screenshot=out["screenshot"]["status"])
    return out


# 会话头：第一次 observe/act 时写入并固定；之后每次都与它比较，APP 或设备不同就拒绝
SESSION_SCHEMA = 2
# act_request 只记这些字段（白名单）：新加的命令行参数默认不落盘，输入文字只记长度
REQUEST_FIELDS = ("action", "select", "within", "target", "point", "basis", "side_effect", "allow_input", "text_len", "clear",
                  "expect", "expect_text", "timeout", "duration", "interval", "watch", "direction", "authorize",
                  "supervisor")


def _open_session(ws, a):
    """observe/act 的共同入口：取会话、设备、上下文，写入或核对会话头，加载参数。"""
    app = ws.app(a.app)
    store = _session(ws, a.session)
    dev = ws.device(a.device, state_path=store.dir / "replay_state.json")
    ctx = RunContext(ws, app, dev, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    _session_params(ws, ctx, store)
    ident = {"app": app.id, "bundle": app.bundle, "device": dev.spec,
             "validation_level": "offline_replay" if dev.kind == "replay" else "real_device"}
    # 会话只从 trail() 读：身份核对（旧格式按第一条 session_facts）、没有会话头的旧会话拒绝续写都在里面
    try:
        tr = trail(store.dir, expect={k: ident[k] for k in IDENTITY_KEYS})
    except SessionMismatch as e:
        store.event("session_mismatch_rejected", diff=e.diff)
        raise
    # 动作预算按会话累计：否则每条 act 从 0 计数，max_actions_per_run 永远拦不住
    ctx.actions = tr["actions"]
    if tr["identity"] is None:
        disp = dev.display_info() if hasattr(dev, "display_info") else {}
        store.event("session_open", schema=SESSION_SCHEMA, identity=ident,
                    facts={**ident, "app_version": dev.app_version(app.bundle),
                           "device_form": (disp or {}).get("device_form")})
    return app, store, dev, ctx


def _secret_names(ws) -> set:
    names = {k[len("VPD_SECRET_"):] for k, v in os.environ.items() if k.startswith("VPD_SECRET_") and v}
    return names | {k for k, v in (ws.config.get("secrets") or {}).items() if v}


def _session_params(ws, ctx, store) -> None:
    """加载外置密钥引用，仅用于参数和可复用输入分类；不修改或扣留现场证据。"""
    names = _secret_names(ws)
    if names:
        p = Params({n: {"secret": n} for n in names}, ws.config.get("secrets"), store.run_id)
        # 同名环境值与配置值都参与可复用输入分类，避免将外置参数硬编码进生成用例
        p.add_secret_values([v for k, v in os.environ.items() if k.startswith("VPD_SECRET_") and v]
                            + [v for v in (ws.config.get("secrets") or {}).values() if v])
        ctx.params = p


def cmd_observe(ws: Workspace, a) -> dict:
    with session_lock(ws.runs_dir / f"session-{a.session}"):
        return _cmd_observe(ws, a)


def _cmd_observe(ws: Workspace, a) -> dict:
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
    with session_lock(ws.runs_dir / f"session-{a.session}"):
        return _cmd_act(ws, a)


def _cmd_act(ws: Workspace, a) -> dict:
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
           "interval": a.interval if a.action == "monitor" else None,
           "watch": dict(kv.split("=", 1) for kv in a.watch or []) if a.action == "monitor" else None,
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
    rec = {"seq": seq, "ts": now_iso(), "step": _scrub_step(step), "result": _scrub_input(res)}
    append_jsonl(store.dir / "steps.jsonl", {**rec, **({"input_value": a.text} if keep else {})})
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
    step = {"id": step_id, "action": a.action, "retry": 0, "timeout_s": a.timeout,
            **step_options(a.action, {"expect": a.expect, "expect_text": a.expect_text,
                                     "duration": a.duration, "interval": a.interval,
                                     "watch": dict(kv.split("=", 1) for kv in a.watch or [])})}
    if getattr(a, "direction", None):
        step["direction"] = a.direction
    if a.action == "swipe" and a.within:
        # 在指定容器里滑：弹出的下拉/面板和背后页面都是可滚动容器时，默认会滑到面积最大的那个
        k, _, v = a.within.partition("=")
        step["within"] = {k: v}
    if a.action == "monitor":
        step["stop_on"] = []
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
    # 生成资产时外置参数仍需重新绑定；原文树与事件取证不受此分类影响
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
            "report": str(Path(run["dir"]) / "report.md"),
            "informal": run.get("informal", run["case"].get("status") != "regression"),
            "asset_review": run.get("asset_review")}


def cmd_suite(ws: Workspace, a) -> dict:
    suite = ws.load_suite(a.suite)
    devices = a.device or [None]
    case_ids = [e["id"] if isinstance(e, dict) else e for e in suite["cases"]]
    case_mod.preflight_batch(ws, case_ids)  # 全成员先过闸，不能前几条已执行后才拒绝
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
            # 授权与 run 同一语义：run_case 只授予用例自己申请的部分，监护人缺失时该用例阻塞
            row = _run_brief(run_case(ws, cid, device_spec=dev, env=env, authorize=a.authorize,
                                      supervisor=a.supervisor, require_asset_review=True))
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
    if a.case_cmd == "review":
        case = ws.load_case(a.case)
        return {"case": a.case, "status": case.get("status"), "asset_review": case_mod.review_status(ws, case, kn)}
    if a.case_cmd == "approve":
        case = case_mod.approve(ws, a.case, kn, a.reviewer, a.basis)
        return {"case": a.case, "status": case.get("status"), "asset_review": case_mod.review_status(ws, case, kn)}
    if a.case_cmd == "lint":
        case = ws.load_case(a.case)
        return {"case": a.case, "status": case.get("status"), "hash": case_mod.content_hash(case),
                "issues": case_mod.lint(case, ws.app(case["app"]), kn, ws.policy(), runs_dir=ws.runs_dir),
                "asset_review": case_mod.review_status(ws, case, kn)}
    if a.case_cmd == "from-exploration":
        exp = case_mod.load_exploration(ws, a.run)
        case = case_mod.from_exploration(exp, a.transitions, a.new_id, exp["app"])
        if ws.case_path(a.new_id).exists():
            raise FileExistsError(f"用例 {a.new_id} 已存在")
        write_json(ws.case_path(a.new_id), case)
        return {"case": a.new_id, "path": str(ws.case_path(a.new_id)),
                "issues": case_mod.lint(case, ws.app(case["app"]), kn, ws.policy(), runs_dir=ws.runs_dir)}
    if a.case_cmd == "from-session":
        sdir = ws.runs_dir / f"session-{a.session}"
        if not (sdir / "events.jsonl").exists():
            raise FileNotFoundError(f"没有会话 {sdir}")
        if ws.case_path(a.new_id).exists():
            raise FileExistsError(f"用例 {a.new_id} 已存在")
        # 读会话记录要持会话锁：会话进行中生成用例不能读到写了一半的记录（与 observe/act 同一互斥）
        with session_lock(sdir):
            case = from_session(sdir, a.new_id, ws.app(a.app), a.goal, a.drop, a.title)
        write_json(ws.case_path(a.new_id), case)
        src = case["sources"][0]
        return {"case": a.new_id, "path": str(ws.case_path(a.new_id)), "steps": len(case["steps"]),
                "excluded": src["excluded"], "rejected": len(src["rejected"]), "incomplete": src["incomplete"],
                "issues": case_mod.lint(case, ws.app(case["app"]), kn, ws.policy(), runs_dir=ws.runs_dir),
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

        case_mod.preflight_batch(ws, cases)

        def one(cid, dev, overlay):
            return run_case(ws, cid, device_spec=dev, overlay=overlay, propose=False, require_asset_review=True)
        return xs.validate(a.id, cases, a.device, one)
    if a.x_cmd == "adopt":
        return xs.adopt(a.id, a.reviewer)
    if a.x_cmd == "rollback":
        return xs.rollback(a.to_version, a.reason)
    if a.x_cmd == "ledger":
        return {"ledger": xs.ledger()}
    raise ValueError(a.x_cmd)


def cmd_console(ws: Workspace, a) -> dict:
    """阻塞运行；启动后先打印一行 {"console": url}，退出时再输出汇总。

    控制台的手动操作是人工旁路，不经过 Dispatcher / policy / 授权 / 证据链（见 console/control.py）。
    它写独立的 kind="console" 运行目录，console_input 事件不与 Agent 的 act 判定混在同一条用例里。
    Agent 的设备动作只能走 act/run/explore。"""
    import json as _json
    import sys

    from .console.server import Console, is_loopback, serve
    if not is_loopback(a.host):  # 先校验，避免连上设备、起了帧流才报错
        raise ValueError(f"控制台只允许绑定 127.0.0.1 或 localhost，拒绝 {a.host}")
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
        print(_json.dumps({"console": url, "port": port, "device": spec, "input": console.allow_input,
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
