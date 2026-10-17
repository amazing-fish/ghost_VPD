"""探索模式：在已声明状态上，逐个尝试允许的可点击元素，记录状态迁移；每次尝试前经 recipe 回到来源状态。

边界：只展开已声明状态；未声明页面只记录签名与证据后返回，不继续深入（可由 Agent 看截图后
提议新增状态签名，经评审再展开）。安全策略拒绝的元素只记录不点击。覆盖只基于本次实际观察。
"""
from __future__ import annotations

from typing import Optional

from .execute import Executor
from .experience import ExperienceStore
from .locate import resolve
from .observe import RunStore
from .report import render_exploration
from .util import write_json
from .workspace import RunContext, Workspace

DISCLAIMER = "覆盖仅相对于 states.json 已声明的状态与本次实际观察，不代表 APP 的完整覆盖"


def _candidates(ctx, tree, page: dict, layout: dict) -> list:
    out, seen = [], set()
    min_vis = (layout or {}).get("min_visible_fraction", 0.5)
    for n in tree.nodes:
        if not (n.visible and n.clickable):
            continue
        vis = tree.visibility(n)
        if not vis["center"] or vis["fraction"] < min_vis:
            continue  # 滚出可视区或只露出边缘：不作为本次候选（记录中的点只作证据，不写入用例）
        texts = [t for t in tree.subtree_texts(n) if t.strip()]
        label = texts[0] if texts else ""
        if label in seen:
            continue
        seen.add(label)
        ref = None
        for tid, t in ctx.app.targets.items():
            loc = resolve(tid, t, tree, None, ctx.app.dir, layout=layout)
            if loc["status"] == "found" and n.bounds[0] <= loc["point"][0] <= n.bounds[2] and \
                    n.bounds[1] <= loc["point"][1] <= n.bounds[3]:
                ref = tid
                break
        pol = ctx.policy.check_tap(tree, vis["center"], "tree", ctx.app.targets.get(ref),
                                   ctx.app.states.deny_for_page(page),
                                   confirmation_sources=ctx.app.states.confirmation_sources(
                                       tree, ref, ctx.app.targets.get(ref), page, layout))
        out.append({"label": label, "point": list(vis["center"]), "target": ref or {
            "label": label, "strategies": [{"kind": "text", "value": label}], "side_effect": "navigation"},
                    "policy": pol, "selected": any(tree.nodes[c].selected for c in n.children) or n.selected})
    return out


def explore(ws: Workspace, app_id: str, start: str, device_spec: Optional[str], budget: int = 30,
            goal: str = "", prefer: Optional[list] = None) -> dict:
    app = ws.app(app_id)
    store = RunStore(ws.runs_dir, kind="explore")
    device = ws.device(device_spec, state_path=store.dir / "replay_state.json")
    ctx = RunContext(ws, app, device, store, ExperienceStore(ws.root, app_id).overlay(), mode="explore")
    ctx.policy.max_actions_per_run = min(budget, ctx.policy.max_actions_per_run)
    ex = Executor(ctx)
    exp = {"run_id": store.run_id, "app": app_id, "start": start, "goal": goal, "budget": budget,
           "validation_level": "offline_replay" if device.kind == "replay" else "real_device",
           "states": {}, "transitions": [], "skipped": [], "anomalies": [], "exit": {}}
    store.event("explore_start", app=app_id, start=start, goal=goal, budget=budget, device=device.spec)
    pre = device.preflight(app.bundle)
    ctx.facts = {"app": app.id, "app_version": pre.get("app_version"),
                 "device_form": (pre.get("display") or {}).get("device_form")}
    tried, frontier, n = set(), [start], 0
    if not pre["ok"]:
        exp["exit"] = {"reason": "preflight_blocked", "detail": [c for c in pre["checks"] if not c["ok"]]}
    while pre["ok"] and frontier:
        state = frontier.pop(0)
        home = ex.run_step({"id": f"R{n}", "action": "ensure_state", "state": state}, "explore")
        if not home["outcome"].startswith("passed"):
            exp["anomalies"].append(f"无法回到已知状态 {state}：{home.get('reason')}")
            exp["exit"] = {"reason": "reset_failed", "state": state}
            break
        obs, tree = ctx.observe(f"explore:{state}:enumerate")
        exp["states"].setdefault(state, {"declared": True, "obs": obs["obs_id"], "texts": obs["texts"][:20]})
        cands = _candidates(ctx, tree, obs["page"], obs.get("layout"))
        cands.sort(key=lambda c: (0 if prefer and any(p in c["label"] for p in prefer) else 1))
        for c in cands:
            key = (state, c["label"])
            if key in tried:
                continue
            tried.add(key)
            if not c["policy"]["allowed"] or not c["label"] or c["selected"]:
                reason = ("当前已选中" if c["selected"] else c["policy"]["reason"] if not c["policy"]["allowed"]
                          else "无文本标识，副作用无法确认；需 Agent 看截图判断后经 act --point --basis 单步执行")
                deny = (c["policy"].get("deny") or {}) if not c["policy"]["allowed"] else {}
                rec = {"state": state, "label": c["label"] or "（无文本控件）", "reason": reason, "point": c["point"]}
                if deny.get("supervised"):
                    # 控车/运动不是禁止项，但无人值守时可能挡路、终点不对卡住：自动探索只记录入口，不点
                    rec.update(supervised=True, deny_class=deny.get("class"),
                               reason="控车/运动入口：自动探索不执行，需有人监护的 act 单步或回归用例（--authorize --supervisor）")
                exp["skipped"].append(rec)
                continue
            if ctx.actions >= ctx.policy.max_actions_per_run:
                exp["exit"] = {"reason": "budget_exhausted"}
                break
            n += 1
            reset = ex.run_step({"id": f"R{n}", "action": "ensure_state", "state": state}, "explore")
            if not reset["outcome"].startswith("passed"):
                exp["exit"] = {"reason": "reset_failed", "state": state}
                break
            step = ex.run_step({"id": f"E{n}", "action": "tap", "target": c["target"], "retry": 0}, "explore")
            att = step["attempts"][-1] if step["attempts"] else {}
            if not (att.get("dispatch") or {}).get("sent") or not att.get("post"):
                # 没点出去（定位失败、被关口拦下）或点后没有观测：不知道会去哪，不能当成迁移写进记录，
                # 否则 from_exploration 会据此生成一个从未验证过的步骤
                exp["skipped"].append({"state": state, "label": c["label"], "target": c["target"],
                                       "reason": f"未下发或无点击后观测：{att.get('status') or step.get('outcome')}"})
                continue
            page = (att.get("post") or {})
            to = page.get("observed") or "unknown"
            obs_after = att.get("obs_after")
            if to in ("unknown", "ambiguous"):
                sig = ctx.last_obs["page"].get("signature")
                to = f"unknown:{sig}"
                exp["states"].setdefault(to, {"declared": False, "obs": obs_after, "texts": ctx.last_obs["texts"][:20]})
            if page.get("interrupts") or page.get("suspected_crash"):
                exp["anomalies"].append(f"{state} 点击「{c['label']}」后：中断 {page.get('interrupts')} 崩溃疑似 {page.get('suspected_crash')}")
            # 分栏时 to 可能是组合状态（如 my_tab+settings）：各部分都已声明也只记录迁移，不作为新的展开起点，
            # 因为 recipe 只能到达单一状态
            declared = to in app.states.states
            exp["transitions"].append({"id": f"T{len(exp['transitions']) + 1}", "from": state, "label": c["label"],
                                       "target": c["target"], "to": to, "to_declared": declared,
                                       "obs_before": att.get("obs_before"), "obs_after": obs_after,
                                       "step_status": att.get("status")})
            if declared and to not in exp["states"] and to not in frontier:
                frontier.append(to)
        if exp["exit"]:
            break
    exp["exit"] = exp["exit"] or {"reason": "frontier_exhausted"}
    exp["actions"] = ctx.actions
    declared = list(app.states.states)
    visited = [s for s in declared if s in exp["states"]]
    exp["coverage"] = {"declared": declared, "visited_declared": visited,
                       "unvisited_declared": [s for s in declared if s not in visited],
                       "unknown_states": [s for s in exp["states"] if s.startswith("unknown:")],
                       "transitions": len(exp["transitions"]), "skipped_by_policy": len(exp["skipped"]),
                       "supervised_entries": [s["label"] for s in exp["skipped"] if s.get("supervised")],
                       "disclaimer": DISCLAIMER}
    write_json(store.dir / "exploration.json", exp)
    (store.dir / "report.md").write_text(render_exploration(exp), encoding="utf-8")
    store.event("explore_end", exit=exp["exit"]["reason"], transitions=len(exp["transitions"]), actions=ctx.actions)
    exp["dir"] = str(store.dir)
    return exp
