"""把 run.json / exploration.json 渲染为人可读的 Markdown 报告。报告只读取记录，不重新判定。"""
from __future__ import annotations

OUTCOME = {"passed": "通过", "passed_after_adaptation": "通过（定位适配）", "passed_after_recovery": "通过（经恢复）",
           "fail_candidate": "失败候选", "blocked": "阻塞", "not_run": "未执行"}
RESULT = {"pass": "通过", "fail": "失败", "pending": "待确认", "undeterminable": "不可判定", "not_run": "未执行",
          "failed": "失败", "blocked": "阻塞", "not_verified": "未验证"}


def _esc(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def _layout_line(lay: dict) -> str:
    p, d = lay.get("profile") or {}, lay.get("display") or {}
    if not p.get("width_vp"):
        return "未知（未取得像素密度）"
    form = {"bar": "直板", "foldable": "折叠屏"}.get(d.get("device_form"), d.get("device_form") or "?")
    seen = "、".join(f"`{k}`" for k in (lay.get("seen") or {})) or "无"
    return (f"{form}，{p['window_px'][0]}×{p['window_px'][1]} px ÷ 密度 {p['density']} = {p['width_vp']} vp 宽"
            f"（{p['width_class']}，{'宽屏' if p.get('wide') else '窄屏'}，折叠状态 {_esc(d.get('fold_status'))}）；"
            f"运行中出现的形态：{seen}")


def render_run(run: dict) -> str:
    v, c = run["verdict"], run["case"]
    lvl = "离线回放（替身设备，不代表真实产品行为）" if run["validation_level"] == "offline_replay" else "真机"
    out = [f"# 测试报告：{c['id']} {c.get('title') or ''}", ""]
    for al in v.get("alerts") or []:
        out.append(f"> **需要人工接管：{_esc(al['action_required'])}**（{_esc(al['kind'])}，触发：{_esc(al.get('trigger'))}）\n")
    out += [f"**结论：{v['label']}**" + (f"　问题归属：{v['attribution_label']}" if v.get("attribution_label") else "")
            + ("　（另有产品失败，见下文）" if v.get("alerts") and v.get("product_failed") else ""), ""]
    if run.get("goal"):
        out.append(f"> 测试目标（原文）：{_esc(run['goal'])}\n")
    if v.get("product_status_stale"):
        out.append(f"> {v['product_status_stale']['note']}\n")
    ki = v.get("known_issue")
    if ki:
        out.append(f"> 已知缺陷用例（{_esc(ki.get('ref'))}）：{ki['note']}；预期失败特征 `{ki.get('expected_signature')}`，"
                   f"本次 `{v.get('failure_signature')}`\n")
    if run.get("informal"):
        out.append(f"> 本次为非正式运行（用例状态 `{c.get('status')}` 或探索资产审核未有效），本结论仅供参考。\n")
    review = run.get("asset_review")
    if review:
        record = review.get("record") or {}
        out.append(f"> 资产审核：`{review['state']}`；使用入口 `{review['use']}`；"
                   f"审核人 {_esc(record.get('reviewer'))}，时间 {_esc(record.get('ts'))}，"
                   f"依据 {_esc(record.get('basis'))}。审核不代替产品判据确认或本次动作授权。\n")
    if v.get("recovered") or v.get("adapted"):
        out.append("> 本次执行包含" + "、".join(x for x, f in (("执行恢复", v.get("recovered")), ("定位适配", v.get("adapted"))) if f)
                   + "，首次异常与处理过程见下文，未改变任何断言。\n")
    out += ["| 项 | 值 |", "|---|---|",
            f"| 运行 | `{run['run_id']}` |", f"| 验证级别 | {lvl} |",
            f"| 设备 | {_esc(run['device'].get('model'))} / {_esc(run['device'].get('os'))} (`{run['device_spec']}`) |",
            f"| APP | `{run['app']['bundle']}` {_esc(run['app'].get('version'))} |",
            f"| 窗口形态 | {_layout_line(run.get('layout') or {})} |",
            f"| 用例版本 | v{c.get('version')} hash `{c['hash']}` |",
            f"| policy / 经验 overlay | {run['policy']['version']} / v{run['overlay']['version']}"
            + (f"（试验 {run['overlay']['trial']}）" if run["overlay"].get("trial") else "") + " |",
            f"| 动作数 | {run['actions']} |", ""]

    out += ["## 判定依据与发现", ""]
    for f in [f for f in v["findings"] if f["result"] != "not_verified"] or [
            {"result": "pass", "kind": "全部断言", "reason": "非建议类断言均在已确认判据下通过"}]:
        out.append(f"- [{RESULT.get(f['result'], f['result'])}] {_esc(f.get('step') or f.get('id') or f.get('kind'))}："
                   f"{_esc(f.get('reason'))}" + (f"（归属：{f['attribution']}）" if f.get("attribution") else ""))
    if v.get("not_verified"):
        out += ["", "## 尚未验证的检查（关联步骤没有执行到，不能据本次结论推断它们成立或不成立）", ""]
        out += [f"- `{x['id']}`（步骤 `{_esc(x.get('at'))}`）：{_esc(x.get('reason'))}" for x in v["not_verified"]]
    bad_clean = [c for c in run.get("cleanup", []) if not str(c["outcome"]).startswith("passed")]
    if run.get("cleanup"):
        out += ["", "## 清理", "", "- " + ("全部完成" if not bad_clean else "**未完成**：" + "、".join(
            f"`{c['step_id']}` {OUTCOME.get(c['outcome'], c['outcome'])}{'' if c.get('required', True) else '（非必需）'}"
            f"：{_esc(c.get('reason'))}" for c in bad_clean) + "。测试数据可能未恢复，请人工核对")]
    if not run["preflight"]["ok"]:
        for ch in run["preflight"]["checks"]:
            if not ch["ok"]:
                out.append(f"  - 前置检查 `{ch['check']}` 未通过：{_esc(ch.get('error_class'))} {_esc(ch.get('value'))}")
    if not run["env"]["ok"]:
        for u in run["env"]["unmet"]:
            hint = {"layout": "换用满足该形态的设备或窗口状态", "secret": "设置对应环境变量后重跑，不要写进用例",
                    "authorize": "确认后由人在命令行显式授权"}.get(u["key"].split(".")[0], "可用 --env 声明")
            out.append(f"  - 环境要求 `{u['key']}` 需为 `{u['required']}`，实际 `{u['actual']}`（{hint}）")

    if run.get("measurements"):
        out += ["", "## 读数", "", "| 步骤 | 名称 | 值 | 原文 |", "|---|---|---|---|"]
        out += [f"| {m['step']} | {_esc(m.get('name'))} | {_esc(m.get('value'))} {_esc(m.get('unit') or '')} | {_esc(m.get('raw'))} |"
                for m in run["measurements"]]
    if run.get("authorized"):
        sup = (run.get("supervision") or {})
        out += ["", f"> 本次运行由人授权的副作用：{'、'.join(run['authorized'])}"
                + (f"；在场监护人：{_esc(sup.get('supervisor'))}" if sup.get("supervised_granted") else "")
                + "。不可逆账号变更/升级/支付/清数据任何授权下都禁止。"]
    for s in run.get("safety_stops", []):
        head = "已确认停止" if s.get("confirmed_stopped") else "**未能确认已停止，需现场人工接管**"
        out += ["", f"## 安全停止（{_esc(s['trigger'])}）：{head}", "",
                f"- 运动自 `{_esc(s.get('armed_since'))}` 起 {_esc(s.get('elapsed_s'))}s；首次失败见下文，安全停止不改变它"]
        out += [f"- `{x['id']}` {x.get('action', '')} → {x.get('outcome') or x.get('skipped')}" for x in s["steps"]]
    for st in run["steps"]:
        for m in _step_monitors(st):
            out += ["", f"## 监视时间线：{st['step_id']}（{m['samples']} 次采样，{m['duration_s']}s，结束于 `{m['stopped_by']}`）", "",
                    f"- 状态序列：{' → '.join(m['states'])}",
                    "- 规则命中：" + ("、".join(f"{k}×{h['count']}（首次 t={h['first_t']}s「{_esc(h['text'])}」）"
                                        for k, h in m["hits"].items()) or "无"), "",
                    "| t(s) | 状态 | 新出现的文字 | 命中 | 观测 |", "|---|---|---|---|---|"]
            out += [f"| {e['t']} | {e['state']} | {_esc('；'.join(e.get('new_texts', [])[:6]))} | {_esc(','.join(e.get('hits', [])))} "
                    f"| {e['obs']} |" for e in m["timeline"] if e.get("new_texts") or e.get("hits") or e is m["timeline"][0]]

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
    if run.get("ui_alerts"):
        out += ["", "## 页面内的失败/异常提示（产品问题候选，需人工确认并反馈）", "",
                "| 时间 | 观测 | 文字 |", "|---|---|---|"]
        out += [f"| {_esc(u['ts'])} | {u['obs']} | {_esc('；'.join(u['texts']))} |" for u in run["ui_alerts"]]
    snap ={i["id"]: i for i in run.get("knowledge_snapshot") or []}
    if run.get("knowledge_used") or snap:
        f = run.get("facts") or {}
        out += ["", "## 用到的知识与适用性", "",
                f"本次事实：APP `{_esc(f.get('app'))}` 版本 `{_esc(f.get('app_version'))}`，形态 `{_esc(f.get('device_form'))}`"
                + "".join(f"，{k}=`{_esc(x)}`" for k, x in f.items() if k not in ("app", "app_version", "device_form")), "",
                "| 知识 | 用途 | 用例声明的适用判断 | 执行器按事实判定 |", "|---|---|---|---|"]
        seen = set()
        for k in run.get("knowledge_used") or []:
            kid = k.get("id")
            seen.add(kid)
            s = snap.get(kid) or {}
            out.append(f"| {_esc(kid)} | {_esc(k.get('use'))} | {_esc(k.get('applicability'))}：{_esc(k.get('rationale'))} "
                       f"| {_esc(s.get('status') or '（未作判据）')} {_esc((s.get('applicability') or {}).get('status') or '')} |")
        for kid, s in snap.items():
            if kid not in seen:
                out.append(f"| {kid} | 判据 | （未声明） | {_esc(s.get('status'))} {_esc((s.get('applicability') or {}).get('status') or '')} |")
    if v.get("suggestions"):
        out += ["", "## UI/UX 建议（不构成缺陷结论）", ""] + [f"- {s['id']} {s['desc']}：{s['reason']}" for s in v["suggestions"]]
    if run.get("experience_candidates"):
        out += ["", "## 本次产生的经验候选", ""] + [f"- `{x}`（候选，需 `experience validate` 通过后才会生效）"
                                                   for x in run["experience_candidates"]]
    out += ["", "原始证据：`events.jsonl`（追加写）与 `obs/*/`（独占创建，不会被恢复动作覆盖）。", ""]
    return "\n".join(out)


def _step_monitors(step: dict) -> list:
    out = [step["monitor"]] if step.get("monitor") else []
    for s in step.get("sub_steps", []):
        out += _step_monitors(s)
    return out


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
    sup = [s for s in exp["skipped"] if s.get("supervised")]
    if sup:
        out += ["", "## 控车/运动入口（自动探索不点，交给有人监护的 act 单步或回归用例）", ""]
        out += [f"- `{s['state']}`「{_esc(s['label'])}」（{s['deny_class']}）" for s in sup]
    out += ["", "## 被策略跳过的动作", ""] + [f"- `{s['state']}`「{_esc(s['label'])}」：{_esc(s['reason'])}"
                                            for s in exp["skipped"] if not s.get("supervised")]
    out += ["", "## 异常 / 未知", ""] + ([f"- {_esc(a)}" for a in exp["anomalies"]] or ["- 无"])
    return "\n".join(out) + "\n"
