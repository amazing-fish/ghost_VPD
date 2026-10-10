"""Agent 单步会话的三个对象：

- 原始会话（runs/session-<name>/）：events.jsonl 按顺序记下每一次观察、动作请求、被拒、执行结果、失败、
  重试、恢复与中断；steps.jsonl 保存每个动作的完整执行记录（定位、复核、policy、下发、后置观测）。
  只追加，不修改。请求有、结果没有 → 视为中断（进程被杀/超时），不当作"没发生"。
- 候选用例（case from-session）：从会话中提炼可复测的步骤，每一步保留到原始事件与观测的关联；
  被排除的动作必须写明理由，失败/效果未知的动作不会静默消失。目的、判据、清理由 Agent 补齐后 lint。
- 本次结果：候选用例用 run 执行后的 run.json / report.md（产品结论与工具/环境/知识限制分开记录）。

现场选择器（--select）只覆盖有 UI 树的页面：text= / text~= / id= / xpath=。地图等自绘区域（XComponent）、
无树页面、纯视觉定位不在覆盖范围内，此时会得到 not_found / skipped_no_tree 诊断而不是瞎点。
"""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from .tree import UiTree
from .util import now_iso, read_json, read_jsonl

SELECT_RE = re.compile(r"^(text~|text|id|xpath)=(.+)$", re.S)
REPLAYABLE = ("tap", "input", "back", "home", "swipe", "launch", "resume", "stop_app", "wait", "monitor")


def parse_select(spec: str, within: Optional[str] = None, side_effect: str = "navigation",
                 allow_input: bool = False) -> dict:
    """--select 'text=重命名' → 内联目标。多匹配/不可见/部分可见由 locate 诊断，这里不挑第一个。"""
    m = SELECT_RE.match((spec or "").strip())
    if not m:
        raise ValueError("--select 须为 text=<文字> / text~=<包含的文字> / id=<id> / xpath=<表达式>")
    kind, value = m.group(1), m.group(2)
    strat = {"kind": "text", "value": value, "exact": False} if kind == "text~" else {"kind": kind, "value": value}
    if within:
        wk, _, wv = within.partition("=")
        if wk not in ("type", "id", "pane") or not wv:
            raise ValueError("--within 须为 type=<控件类型> / id=<容器 id> / pane=first|last|0|1")
        strat["within"] = {wk: (int(wv) if wk == "pane" and wv.lstrip("-").isdigit() else wv)}
    target = {"label": f"select:{spec}", "strategies": [strat], "side_effect": side_effect, "origin": "agent_select"}
    if allow_input:
        target["allow_input"] = True
    return target


@contextmanager
def session_lock(session_dir: Path, timeout_s: float = 30):
    """跨进程互斥完整会话操作；进程退出由 OS 释放，锁文件无需删除或猜测陈旧性。"""
    session_dir.mkdir(parents=True, exist_ok=True)
    with (session_dir / ".session.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("同一会话正在执行另一个操作，等待会话锁超时")
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def next_seq(store) -> int:
    return 1 + max((it["seq"] for it in trail(store.dir)["items"] if it.get("seq") is not None), default=0)


class SessionMismatch(PermissionError):
    def __init__(self, msg: str, diff: dict):
        super().__init__(msg)
        self.diff = diff


# 这些事件表示"已经向设备发出了手势/按键"：动作是否执行以它们为准，不看最后是结果还是报错
SENT_EVENTS = ("dispatch", "clear_text", "scroll_search")
IDENTITY_KEYS = ("app", "device", "validation_level")


def _sent(e: dict) -> bool:
    if e.get("sent") is not None:
        return bool(e["sent"])
    # 旧会话没有 sent 字段：clear_text 事件出现即已点过输入框；scroll_search 按 ok 判断
    return bool(e.get("ok")) if e.get("event") == "scroll_search" else True


def trail(session_dir: Path, expect: Optional[dict] = None) -> dict:
    """读取会话的唯一入口：按事件顺序还原 agent_observe / act（请求 + 结果）/ rejected / error / incomplete，
    并取出会话身份（session_open；旧格式按第一条 session_facts）。

    - expect 给出时逐项与会话身份比较，不一致抛 SessionMismatch；会话里已有动作/观测却没有身份也拒绝。
    - 每个请求是否已下发，以请求与结束事件之间的 dispatch/clear_text/scroll_search 为准（act 是单进程、
      顺序执行，中间的事件都属于这个请求）。下发后才报错的 act_error 是"效果未知"，不是被拒。
    """
    events = read_jsonl(session_dir / "events.jsonl")
    records = {r["seq"]: r for r in read_jsonl(session_dir / "steps.jsonl")}
    items, open_req, cur = [], {}, None
    identity, secret_typed, used, actions = None, False, False, 0
    counted = set()  # 已有下发事件计入预算的请求
    for e in events:
        ev = e.get("event")
        if ev == "dispatch" and e.get("sent") and e.get("op") != "clear_text":
            actions += 1  # 与 Dispatcher 同一计数口径：已下发的物理动作，清空输入是聚合动作不单独计
            if cur in open_req:
                counted.add(cur)
        if ev == "agent_observe":
            used = True
            items.append({"kind": "observe", "obs": e.get("obs_id"), "label": e.get("label"), "ts": e["ts"],
                          "state": e.get("state")})
        elif ev == "act_request":
            used = True
            cur = e["seq"]
            open_req[cur] = {"kind": "act", "seq": cur, "step_id": e.get("step_id"), "ts": e["ts"],
                             "request": e.get("request", {}), "dispatched": False}
            items.append(open_req[cur])
        elif ev in SENT_EVENTS and cur in open_req and _sent(e):
            open_req[cur]["dispatched"] = True
        elif ev == "secret_typed":
            secret_typed = True
        elif ev in ("act_rejected", "act_error"):
            it = open_req.pop(e["seq"], None)
            if it is not None:
                it.update(kind="rejected" if ev == "act_rejected" else "error", reason=e.get("reason"))
                if ev == "act_error" and it["dispatched"]:
                    it["effect_unknown"] = True  # 手势已发出后才出错：现场可能已经变了
        elif ev == "act_result":
            it = open_req.pop(e["seq"], None)
            if it is not None:
                rec = records.get(e["seq"], {})
                it.update(outcome=e.get("outcome"), effect_unknown=e.get("effect_unknown", False),
                          obs_before=e.get("obs_before"), obs_after=e.get("obs_after"),
                          dispatched=bool(e.get("dispatched")) or it["dispatched"], record=rec.get("result"),
                          input_value=rec.get("input_value"))
        elif ev in ("session_open", "session_facts"):  # session_facts：会话头之前的旧格式
            if identity is None:
                f = e.get("facts")
                identity = e.get("identity") or ({k: f.get(k) for k in IDENTITY_KEYS} if f else None)
            items.append({"kind": "facts", "facts": e.get("facts"), "ts": e["ts"], "identity": e.get("identity")})
    for it in open_req.values():
        # 进程在下发途中被杀时 dispatch 事件可能来不及写：没有结果的请求一律按效果未知处理，
        # 预算上也按已下发保守计入（命令可能已到设备）；已有下发事件的请求不重复加
        if it["seq"] not in counted:
            actions += 1
        it.update(kind="incomplete", effect_unknown=True,
                  reason="有请求没有结果（进程中断/超时）：现场可能已经发生变化")
    tr = {"session": session_dir.name, "items": items, "identity": identity, "secret_typed": secret_typed,
          "actions": actions}
    if expect:
        if identity is None:
            if used:
                raise PermissionError(f"会话 {session_dir.name} 没有会话头，无法确认 APP/设备：请换一个新的 --session 名")
        else:
            diff = {k: {"session": identity.get(k), "now": v} for k, v in expect.items() if identity.get(k) != v}
            if diff:
                raise SessionMismatch(f"会话 {session_dir.name} 已固定为 {identity}，本次 {diff} 不一致："
                                      "同一会话不能混用不同 APP/设备，请换一个 --session 名", diff)
    return tr


def summarize(tr: dict) -> dict:
    rows = []
    for it in tr["items"]:
        if it["kind"] == "act" or it["kind"] in ("rejected", "error", "incomplete"):
            req = it.get("request", {})
            rows.append({"seq": it.get("seq"), "step": it.get("step_id"), "kind": it["kind"], "action": req.get("action"),
                         "select": req.get("select"), "target": req.get("target"), "point": req.get("point"),
                         "outcome": it.get("outcome"), "dispatched": it.get("dispatched"),
                         "effect_unknown": it.get("effect_unknown"),
                         "obs": [it.get("obs_before"), it.get("obs_after")], "reason": it.get("reason")})
        elif it["kind"] == "observe":
            rows.append({"kind": "observe", "obs": it["obs"], "label": it.get("label"), "state": it.get("state")})
    return {"session": tr["session"], "identity": tr["identity"], "trail": rows,
            "facts": next((it["facts"] for it in tr["items"] if it["kind"] == "facts"), None)}


def effect_unknown(result: dict) -> bool:
    """命令已发出但执行器没有确认成功（超时/报错）：现场可能已经变化，不能当作"什么都没发生"。"""
    return any((a.get("dispatch") or {}).get("sent") and not (a.get("dispatch") or {}).get("executor_ok")
               for a in result.get("attempts", []))


def dispatched(result: dict) -> bool:
    return any((a.get("dispatch") or {}).get("sent") for a in result.get("attempts", []))


def _derive_select(session_dir: Path, obs_id: Optional[str], point) -> Optional[dict]:
    """会话里是坐标点击：按点击前的 UI 树找点击点所在的可点击控件，唯一的文字/ID 才能推出选择器（需评审）。"""
    if not obs_id or not point:
        return None
    if isinstance(point, str):  # 请求里保存的是命令行原文 "x,y"
        point = [int(v) for v in point.split(",")]
    tp = session_dir / "obs" / obs_id / "tree.json"
    if not tp.exists():
        return None
    tree = UiTree(read_json(tp))
    node = tree.node_at(*point)
    if node is None:
        return None
    holder = tree.clickable_container(node)
    for text in tree.subtree_texts(holder):
        hits = tree.find_text(text)
        if len(hits) == 1:
            return {"kind": "text", "value": text, "derived_from_point": True}, hits[0].brief()
    if holder.id and len(tree.find_id(holder.id)) == 1:
        return {"kind": "id", "value": holder.id, "derived_from_point": True}, holder.brief()
    return None


def _parse_drops(drops: list) -> dict:
    out = {}
    for d in drops or []:
        key, sep, reason = d.partition("=")
        if not sep or not reason.strip():
            raise ValueError(f"--drop {d}：必须写理由，格式 A4=理由 或 A4-A7=理由")
        lo, _, hi = key.partition("-")
        m1, m2 = re.fullmatch(r"A(\d+)", lo), re.fullmatch(r"A(\d+)", hi or lo)
        if not (m1 and m2):
            raise ValueError(f"--drop {d}：步骤须写成 A<序号>")
        for i in range(int(m1.group(1)), int(m2.group(1)) + 1):
            out[i] = reason.strip()
    return out


def from_session(session_dir: Path, case_id: str, app, goal: Optional[str], drops: Optional[list] = None,
                 title: Optional[str] = None) -> dict:
    # 会话身份与 --app 不一致就拒绝：观测和选择器来自会话里的 APP，换个 APP 标签会生成对错包重放的用例
    tr = trail(session_dir, expect={"app": app.id})
    dropmap = _parse_drops(drops)
    facts = next((it["facts"] for it in tr["items"] if it["kind"] == "facts"), None) or {}
    steps, excluded, rejected, incomplete, unknown = [], [], [], [], []
    obs, used_effects, granted, test_data = [], set(), set(), {}
    for it in tr["items"]:
        seq = it.get("seq")
        if it["kind"] in ("rejected", "error") and not it.get("dispatched"):
            # 只有没下发的才算被拒：它们没有改变现场，不进步骤
            rejected.append({"seq": seq, "step": it.get("step_id"), "kind": it["kind"], "reason": it.get("reason"),
                             "request": it.get("request"), "dispatched": False})
            continue
        if it["kind"] not in ("act", "incomplete", "error"):
            continue
        req = it.get("request", {})
        eff_unknown = bool(it.get("effect_unknown"))
        if seq in dropmap:
            excluded.append({"seq": seq, "step": it.get("step_id"), "action": req.get("action"),
                             "outcome": it.get("outcome") or it["kind"], "reason": dropmap[seq],
                             "effect_unknown": eff_unknown, "obs": [it.get("obs_before"), it.get("obs_after")]})
            continue
        if it["kind"] == "incomplete":
            incomplete.append({"seq": seq, "step": it.get("step_id"), "action": req.get("action")})
        if eff_unknown:
            unknown.append({"seq": seq, "step": it.get("step_id"), "kind": it["kind"], "action": req.get("action"),
                            "reason": it.get("reason")})
        step = _to_case_step(it, req, session_dir, test_data, facts)
        steps.append(step)
        obs += [o for o in (it.get("obs_before"), it.get("obs_after")) if o]
        eff = (step.get("target") or {}).get("side_effect") if isinstance(step.get("target"), dict) else None
        if eff and eff != "navigation":
            used_effects.add(eff)
        granted |= set(req.get("authorize") or [])
    if not steps:
        raise ValueError("会话中没有可转换的动作（全部被排除或被拒）")
    first_state = next((it.get("state") for it in tr["items"] if it["kind"] == "observe"), None)
    return {
        "id": case_id, "version": 1, "status": "exploration_record", "app": app.id,
        "title": title or f"[会话生成] {goal or session_dir.name}", "goal": goal,
        "purpose": "", "preconditions": [f"APP 处于 {first_state}（会话首次观察）"] if first_state else [],
        "env_requirements": {}, "test_data": test_data, "test_data_none": not test_data,
        "authorizations": sorted(used_effects | granted),
        "steps": steps, "assertions": [], "cleanup": [], "knowledge_used": [],
        "sources": [{"type": "session", "run": session_dir.name, "validation_level": facts.get("validation_level"),
                     "facts": facts, "included": [s["from_session"]["seq"] for s in steps], "excluded": excluded,
                     "rejected": rejected, "incomplete": incomplete, "effect_unknown": unknown, "obs": obs,
                     "trail": f"runs/{session_dir.name}/events.jsonl"}],
        "history": [{"ts": now_iso(), "to": "exploration_record",
                     "why": "由会话生成：待补齐目的、判据（独立来源）、清理与 knowledge_used"}]}


def step_options(action: str, request: dict) -> dict:
    """现场执行和会话固化共用的参数语义；monitor 预期是结束条件。"""
    names = {"expect": "until_state" if action == "monitor" else "expect_state",
             "expect_text": "until_text" if action == "monitor" else "expect_text",
             "duration": "duration_s", "interval": "interval_s", "watch": "watch"}
    return {field: request[key] for key, field in names.items() if request.get(key)}


def _to_case_step(it: dict, req: dict, session_dir: Path, test_data: dict, facts: dict) -> dict:
    seq, action = it["seq"], req.get("action")
    rec = it.get("record") or {}
    last = (rec.get("attempts") or [{}])[-1]
    step = {"id": f"S{seq}", "action": action, "timeout_s": req.get("timeout", 8), "retry": 1, "critical": True,
            "oracle_refs": [],
            "from_session": {"session": session_dir.name, "seq": seq, "step": it.get("step_id"),
                             "outcome": it.get("outcome") or it["kind"], "effect_unknown": bool(it.get("effect_unknown")),
                             "obs_before": it.get("obs_before"), "obs_after": it.get("obs_after")}}
    step.update(step_options(action, req))
    if action == "swipe" and req.get("direction"):
        step["direction"] = req["direction"]
    if action == "swipe" and req.get("within"):
        k, _, v = req["within"].partition("=")
        step["within"] = {k: v}
    if action in ("tap", "input"):
        derived_node = None
        if req.get("select"):
            target = parse_select(req["select"], req.get("within"), req.get("side_effect") or "navigation",
                                  bool(req.get("allow_input")))
        elif req.get("target"):
            target = req["target"]
        else:
            derived = _derive_select(session_dir, it.get("obs_before"), req.get("point"))
            strat, derived_node = derived if derived else (None, None)
            target = {"label": f"from_point:{req.get('basis')}", "strategies": [strat] if strat else [],
                      "side_effect": req.get("side_effect") or "navigation", "origin": "derived_from_point"}
            if req.get("allow_input"):
                target["allow_input"] = True
            step["point_basis"] = req.get("basis")
        if isinstance(target, dict):
            # 取实际下发的那次尝试的解析结果；没有下发过（多匹配/被拒/中断）就没有 node，lint 会拦下
            sent = next((a for a in reversed(rec.get("attempts") or [])
                         if a.get("resolved") and (a.get("dispatch") or {}).get("sent")), None)
            resolved = (sent or {}).get("resolved") or {}
            node = resolved.get("node")
            basis = "resolved"
            if node is None and target.get("origin") == "derived_from_point" and sent:
                # 坐标点击没有"解析节点"：用反推选择器时唯一命中的节点作证据（点击前的树），并标明是反推的
                node, basis = derived_node, "derived_from_point"
            target["observed"] = {"session": session_dir.name, "seq": seq, "obs": resolved.get("obs") or it.get("obs_before"),
                                  "outcome": it.get("outcome") or it["kind"], "node": node, "node_basis": basis,
                                  "dispatched": bool(sent),
                                  "layout_key": resolved.get("layout_key"), "app_version": facts.get("app_version"),
                                  "device_form": facts.get("device_form"),
                                  "validation_level": facts.get("validation_level")}
            step["revalidate"] = True
        step["target"] = target
        if action == "input":
            name = f"input_{seq}"
            # 会话只在"允许输入、目标非敏感、不等于已配置密钥"时保留原文；否则留空待人补（lint 会指出）
            if it.get("input_value") is not None:
                test_data[name] = it["input_value"]
            else:
                test_data[name] = {"missing_from_session": True, "length": req.get("text_len")}
            step["text"] = f"${{var:{name}}}"
            if req.get("clear"):
                step["clear"] = True
    return step
