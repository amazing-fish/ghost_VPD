"""把 run.json / exploration.json 渲染为人可读的 Markdown 报告。报告只读取记录，不重新判定。"""
from __future__ import annotations

OUTCOME = {"passed": "通过", "passed_after_adaptation": "通过（定位适配）", "passed_after_recovery": "通过（经恢复）",
           "fail_candidate": "失败候选", "blocked": "阻塞", "not_run": "未执行"}
RESULT = {"pass": "通过", "fail": "失败", "pending": "待确认", "undeterminable": "不可判定", "not_run": "未执行"}


def _esc(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def render_run(run: dict) -> str:
    v, c = run["verdict"], run["case"]
    lvl = "离线回放（替身设备，不代表真实产品行为）" if run["validation_level"] == "offline_replay" else "真机"
    out = [f"# 测试报告：{c['id']} {c.get('title') or ''}", "",
           f"**结论：{v['label']}**" + (f"　问题归属：{v['attribution_label']}" if v.get("attribution_label") else ""), ""]
    if run.get("informal"):
        out.append(f"> 用例状态为 `{c.get('status')}`，不是正式回归用例，本结论仅供参考。\n")
    if v.get("recovered") or v.get("adapted"):
        out.append("> 本次执行包含" + "、".join(x for x, f in (("执行恢复", v.get("recovered")), ("定位适配", v.get("adapted"))) if f)
                   + "，首次异常与处理过程见下文，未改变任何断言。\n")
    out += ["| 项 | 值 |", "|---|---|",
            f"| 运行 | `{run['run_id']}` |", f"| 验证级别 | {lvl} |",
            f"| 设备 | {_esc(run['device'].get('model'))} / {_esc(run['device'].get('os'))} (`{run['device_spec']}`) |",
            f"| APP | `{run['app']['bundle']}` {_esc(run['app'].get('version'))} |",
            f"| 用例版本 | v{c.get('version')} hash `{c['hash']}` |",
            f"| policy / 经验 overlay | {run['policy']['version']} / v{run['overlay']['version']}"
            + (f"（试验 {run['overlay']['trial']}）" if run["overlay"].get("trial") else "") + " |",
            f"| 动作数 | {run['actions']} |", ""]

    out += ["## 判定依据与发现", ""]
    for f in v["findings"] or [{"result": "pass", "kind": "全部断言", "reason": "非建议类断言均在已确认判据下通过"}]:
        out.append(f"- [{RESULT.get(f['result'], f['result'])}] {_esc(f.get('step') or f.get('id') or f.get('kind'))}："
                   f"{_esc(f.get('reason'))}" + (f"（归属：{f['attribution']}）" if f.get("attribution") else ""))
    if not run["preflight"]["ok"]:
        for ch in run["preflight"]["checks"]:
            if not ch["ok"]:
                out.append(f"  - 前置检查 `{ch['check']}` 未通过：{_esc(ch.get('error_class'))} {_esc(ch.get('value'))}")
    if not run["env"]["ok"]:
        for u in run["env"]["unmet"]:
            out.append(f"  - 环境要求 `{u['key']}` 需为 `{u['required']}`，实际 `{u['actual']}`（可用 --env 声明）")

    fa = v.get("first_anomaly")
    out += ["", "## 首次异常", ""]
    if fa:
        out.append(f"- 步骤 `{fa['step']}` 第 {fa['attempt']} 次尝试：`{fa['status']}`，"
                   f"执行前 `{fa.get('obs_before')}`，执行后 `{fa.get('obs_after')}`")
        d = fa.get("detail", {})
        if d.get("post"):
            out.append(f"  - 预期 `{d['post'].get('expected')}`，观测 `{d['post'].get('observed')}`，"
                       f"中断 {[i['id'] for i in d['post'].get('interrupts') or []]}")
        if d.get("locate"):
            for a in d["locate"].get("attempts", []):
                out.append(f"  - 定位策略 #{a.get('index')} {a['kind']} `{_esc(a.get('value'))}` → {a.get('status')}"
                           + (f"（{a.get('matches')} 个匹配）" if a.get("matches") is not None else ""))
        if d.get("dispatch") and not d["dispatch"].get("executor_ok"):
            out.append(f"  - 执行器错误 `{d['dispatch'].get('error_class')}`：{_esc(d['dispatch'].get('error'))}")
    else:
        out.append("- 无")

    out += ["", "## 步骤", "", "| 步骤 | 动作 | 结果 | 尝试 | 恢复 | 定位 | 最终观测 |", "|---|---|---|---|---|---|---|"]
    for s in run["steps"] + run.get("cleanup", []):
        locs = [a["locate"] for a in s.get("attempts", []) if a.get("locate")]
        loc = locs[-1] if locs else {}
        how = f"{loc.get('strategy_kind')}#{loc.get('strategy_index')}" if loc.get("status") == "found" else (loc.get("status") or "")
        recs = "; ".join(f"{r['action']}:{'已执行' if r['performed'] else '未执行'}" for r in s.get("recoveries", []))
        out.append(f"| {s['step_id']} | {s['action']} | {OUTCOME.get(s['outcome'], s['outcome'])} | {len(s.get('attempts', []))} "
                   f"| {_esc(recs)} | {_esc(how)} | {_esc(s.get('final_obs'))} |")

    out += ["", "## 断言", "", "| 断言 | 结果 | 判据 | 证据 | 说明 |", "|---|---|---|---|---|"]
    for a in run["assertions"]:
        orc = a.get("oracle", {})
        refs = ", ".join(f"{i['id']}({i['status']})" for i in orc.get("items", [])) or ", ".join(a.get("oracle_refs", []))
        ev = a.get("evidence", {})
        out.append(f"| {a['id']}{'（建议）' if a['advisory'] else ''} {_esc(a['desc'])} | {RESULT[a['result']]} | {_esc(refs)} "
                   f"| {_esc(ev.get('obs'))} {_esc(ev.get('screenshot') or '')} | {_esc(a.get('reason') or '')} |")
    if v.get("suggestions"):
        out += ["", "## UI/UX 建议（不构成缺陷结论）", ""] + [f"- {s['id']} {s['desc']}：{s['reason']}" for s in v["suggestions"]]
    if run.get("experience_candidates"):
        out += ["", "## 本次产生的经验候选", ""] + [f"- `{x}`（候选，需 `experience validate` 通过后才会生效）"
                                                   for x in run["experience_candidates"]]
    out += ["", "原始证据：`events.jsonl`（追加写）与 `obs/*/`（独占创建，不会被恢复动作覆盖）。", ""]
    return "\n".join(out)


def render_exploration(exp: dict) -> str:
    cov = exp["coverage"]
    out = [f"# 探索记录：{exp['run_id']}", "", f"- 目标：{exp.get('goal') or '（未填写）'}",
           f"- 起点：`{exp['start']}`，退出原因：`{exp['exit']['reason']}`，动作数 {exp['actions']}/{exp['budget']}",
           f"- 验证级别：{'离线回放' if exp['validation_level'] == 'offline_replay' else '真机'}", "",
           "## 覆盖（仅基于本次实际观察与已声明状态模型）", "",
           f"- 已声明状态 {len(cov['declared'])} 个，本次到达 {len(cov['visited_declared'])} 个：{cov['visited_declared']}",
           f"- 未到达的已声明状态：{cov['unvisited_declared']}",
           f"- 观察到的未声明页面签名：{cov['unknown_states']}", f"- {cov['disclaimer']}", "",
           "## 迁移", "", "| ID | 从 | 动作 | 到 | 证据 |", "|---|---|---|---|---|"]
    for t in exp["transitions"]:
        out.append(f"| {t['id']} | {t['from']} | 点击「{_esc(t['label'])}」 | {t['to']}{'' if t['to_declared'] else '（未声明）'} "
                   f"| {t['obs_before']} → {t['obs_after']} |")
    out += ["", "## 被策略跳过的动作", ""] + [f"- `{s['state']}`「{_esc(s['label'])}」：{_esc(s['reason'])}" for s in exp["skipped"]]
    out += ["", "## 异常 / 未知", ""] + ([f"- {_esc(a)}" for a in exp["anomalies"]] or ["- 无"])
    return "\n".join(out) + "\n"
