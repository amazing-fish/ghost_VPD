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
import math
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from .dispatch import auxiliary_dispatches, dispatch_effect_unknown
from .tree import UiTree
from .util import now_iso, read_json, read_jsonl, read_jsonl_recoverable

SELECT_RE = re.compile(r"^(text~|text|id|xpath)=(.+)$", re.S)
# 结构性导航容器：App 的"壳"，跨构建稳定且容易撞文案（页面标题、新页面控件与 TabBar 的 tab 同名是
# 真机跨版本重放实际发生过的阻塞）。固化时只要点在壳里就主动带上 within 限定；其余容器不主动限定，
# 避免把普通页面布局（Column/Row/List）固进用例造成跨形态脆弱。
CHROME_TYPES = ("TabBar",)


def relative_swipe_options(request: dict) -> dict:
    """Normalize CLI fractions and stored fractions without converting time into speed."""
    def pair(value):
        values = value.split(",") if isinstance(value, str) else value
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise ValueError("start/end 必须是两个0..1相对比例")
        if any(isinstance(v, bool) or not isinstance(v, (str, int, float)) for v in values):
            raise ValueError("start/end 必须是有限数值比例")
        nums = [float(v) for v in values]
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in nums):
            raise ValueError("start/end 比例必须为0..1的有限数值")
        return nums
    start, end = pair(request.get("start")), pair(request.get("end"))
    speed = request.get("speed") if request.get("speed") is not None else 2000
    if type(speed) is not int or not 200 <= speed <= 40000:
        raise ValueError("speed 必须为200..40000的整数，单位px/s")
    if start == end:
        raise ValueError("swipe 起止点不能相同")
    return {"start": start, "end": end, "speed": speed}


def swipe_options_error(request: dict) -> str | None:
    """现场、定义检查和固化共用的滚动/相对手势参数合同。"""
    relative = any(request.get(k) is not None for k in ("start", "end", "speed"))
    if request.get("action") != "swipe":
        return "start/end/speed 仅用于 swipe" if relative else None
    fields = [key for key in ("point", "basis", "point_window", "point_basis", "duration", "duration_s")
              if request.get(key) is not None]
    if fields:
        return (f"swipe 不支持参数 {', '.join(fields)}：像素定点/时长不能作为容器相对手势；"
                "请用 direction/within 滚动，或 start/end/speed/within 相对手势。duration 仅用于 wait/monitor。")
    if not relative:
        return ("swipe direction 仅支持 up/down；其他方向请使用 start/end 相对手势"
                if request.get("direction") not in (None, "up", "down") else None)
    conflicts = [k for k in ("direction", "random", "target", "select", "at", "random_point")
                 if request.get(k) is not None]
    if conflicts:
        return f"swipe 相对手势不能与 {', '.join(conflicts)} 混用"
    within = request.get("within")
    if isinstance(within, str):
        key, sep, value = within.partition("=")
        within = {key: value} if sep and value else None
    if (not isinstance(within, dict) or len(within) != 1 or
            next(iter(within), None) not in ("id", "type") or
            not isinstance(next(iter(within.values()), None), str) or not next(iter(within.values()), "")):
        return "swipe 相对手势须显式 within=id=<id> 或 type=Scroll|List|Grid"
    if "type" in within and within["type"] not in ("Scroll", "List", "Grid"):
        return "swipe 相对手势只支持已知可滚动 Scroll/List/Grid 容器"
    try:
        relative_swipe_options(request)
    except (TypeError, ValueError, OverflowError) as error:
        return f"swipe 参数无效：{error}"
    return None


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
# 不向设备下发手势/按键的请求：monitor 只采样观测、wait 只计时。这类请求被中断时
# 不会有物理动作在途，恢复预算时不按"已下发"保守计入（手势类仍保守计入）。
GESTURELESS_ACTIONS = ("monitor", "wait")
IDENTITY_KEYS = ("app", "device", "validation_level")


def _sent(e: dict) -> bool:
    if "sent" in e:
        return e["sent"] is not False
    # 旧会话没有 sent 字段：clear_text 事件出现即已点过输入框；scroll_search 按 ok 判断
    return bool(e.get("ok")) if e.get("event") == "scroll_search" else True


def trail(session_dir: Path, expect: Optional[dict] = None) -> dict:
    """读取会话的唯一入口：按事件顺序还原 agent_observe / act（请求 + 结果）/ rejected / error / incomplete，
    并取出会话身份（session_open；旧格式按第一条 session_facts）。

    - expect 给出时逐项与会话身份比较，不一致抛 SessionMismatch；会话里已有动作/观测却没有身份也拒绝。
    - 每个请求是否已下发，以请求与结束事件之间的 dispatch/clear_text/scroll_search 为准（act 是单进程、
      顺序执行，中间的事件都属于这个请求）。下发后才报错的 act_error 是"效果未知"，不是被拒。
    - 进程被杀时 events/steps 的末行可能写了一半：丢弃截断的末行并在结果里留证（truncated_tail），
      会话仍可恢复；中间行损坏不是截断，仍然报错。
    """
    events, dropped_events = read_jsonl_recoverable(session_dir / "events.jsonl")
    step_records, dropped_steps = read_jsonl_recoverable(session_dir / "steps.jsonl")
    records = {r["seq"]: r for r in step_records}
    items, open_req, cur = [], {}, None
    identity, used, actions = None, False, 0
    counted = set()  # 已有下发事件计入预算的请求
    for e in events:
        ev = e.get("event")
        if ev == "dispatch" and _sent(e) and e.get("op") != "clear_text":
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
        # 进程在下发途中被杀时 dispatch 事件可能来不及写：没有结果的请求一律按效果未知处理；
        # 手势类请求在预算上按已下发保守计入（命令可能已到设备），monitor/wait 不下发手势，
        # 被中断也不会有动作在途，不计入；已有下发事件的请求不重复加
        if it["seq"] not in counted and (it.get("request") or {}).get("action") not in GESTURELESS_ACTIONS:
            actions += 1
        it.update(kind="incomplete", effect_unknown=True,
                  reason="有请求没有结果（进程中断/超时）：现场可能已经发生变化")
    tr = {"session": session_dir.name, "items": items, "identity": identity,
          "actions": actions}
    dropped = {k: v for k, v in (("events", dropped_events), ("steps", dropped_steps)) if v is not None}
    if dropped:
        # 末行截断已丢弃：留证（哪份文件、截断内容前缀）。被丢弃的行可能是某请求的结束事件，
        # 该请求会以 incomplete 出现并按保守口径计入预算，不会低估。
        tr["truncated_tail"] = dropped
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
    return any(a.get("effect_unknown") or dispatch_effect_unknown(a)
               for a in result.get("attempts", []))


def dispatched(result: dict) -> bool:
    # 未知发送事实保守计入会话预算；不能当作可靠未下发。
    return any(any(item["sent"] is not False for item in auxiliary_dispatches(a)) or
               (a.get("dispatch") is not None and a["dispatch"].get("sent") is not False) or
               (a.get("clear") is not None and a["clear"].get("sent") is not False)
               for a in result.get("attempts", []))


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


def _auto_within(session_dir: Path, obs_id: Optional[str], strategy: Optional[dict],
                 point: Optional[list]) -> Optional[dict]:
    """固化时自动推导容器限定（Issue #13）。两个触发条件，都要有会话证据、都只做结构性限定：

    - 响应式：选择器在会话观测树上多匹配（"text=我的" 同时命中标题与 TabBar 的 tab）→ 按实际
      下发的点击点找出被点节点，沿祖先链找能把其余匹配全部排除的最小容器。
    - 主动式：点在结构性导航容器（TabBar 等"壳"）里 → 即使当前唯一也带上限定。真机跨版本重放
      出过这种阻塞：3.0.3 会话里「我的」唯一可点，新构建的页面加了同名标题，重放 target_ambiguous，
      需要人工补 within={type: TabBar}。壳是跨构建稳定的，主动限定不引入布局脆弱性。

    两条规则共用闸门：推导出的 within 必须在该树上把匹配收窄回唯一（含其他同类容器实例），
    否则维持原状，把歧义留给 lint 和评审，绝不猜。不落像素坐标。"""
    from .layout import resolve_scope
    from .locate import _nodes_for
    from .tree import in_box
    if not obs_id or not isinstance(strategy, dict) or not point:
        return None
    if strategy.get("within") or strategy.get("region") or strategy.get("kind") not in ("text", "id"):
        return None
    tp = session_dir / "obs" / obs_id / "tree.json"
    if not tp.exists():
        return None
    tree = UiTree(read_json(tp))
    if tree.status != "ok":
        return None
    try:
        nodes = [n for n in _nodes_for(strategy, tree) if n.visible]
    except Exception:
        return None
    if not nodes:
        return None
    tapped = (next((n for n in nodes if n.bounds and in_box([int(point[0]), int(point[1])], n.bounds)), None)
              if len(nodes) > 1 else nodes[0])
    if tapped is None:
        return None

    def unique_in(within: dict) -> bool:
        scope = resolve_scope(within, tree, None)
        return scope is not None and sum(1 for n in nodes if tree.in_scope(n, scope)) == 1

    others = [n for n in nodes if n.idx != tapped.idx]
    # 首选结构性导航容器（跨构建稳定的"壳"，unique_in 会校验它确实罩得住歧义）
    for ai in tree._ancestor_idx(tapped):
        anc = tree.nodes[ai]
        if anc.type in CHROME_TYPES and unique_in({"type": anc.type}):
            return {"type": anc.type}
    if others:  # 没有壳可用：就近找能把其余匹配全部排除的最小消歧容器
        for ai in tree._ancestor_idx(tapped):
            anc = tree.nodes[ai]
            if not anc.type:
                continue
            if any(o.idx == ai or ai in tree._ancestor_idx(o) for o in others):
                continue  # 该容器罩住了其他匹配：限定不住
            if unique_in({"type": anc.type}):
                return {"type": anc.type}
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
        "origin": {"type": "session"},
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
             "duration": "duration_s", "interval": "interval_s", "watch": "watch", "capture_transient": "capture_transient"}
    return {field: request[key] for key, field in names.items() if request.get(key)}


def _to_case_step(it: dict, req: dict, session_dir: Path, test_data: dict, facts: dict) -> dict:
    seq, action = it["seq"], req.get("action")
    error = swipe_options_error(req)
    if error:
        raise ValueError(f"历史动作 A{seq} 的参数未被兑现，不能固化成普通滚动：{error} "
                         f"请重新采样，或用 --drop A{seq}=理由 显式排除并保留依据。")
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
    if action == "swipe" and req.get("start") is not None:
        step.update(relative_swipe_options(req))
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
            # Issue #13：按会话证据自动推导结构性容器限定——多匹配时消歧（响应式），
            # 点在导航壳里时主动限定（跨构建防撞文案）；推导不出就保持原状，由 lint 与评审兜底
            auto_w = _auto_within(session_dir, resolved.get("obs") or it.get("obs_before"),
                                  (target.get("strategies") or [None])[0], resolved.get("point"))
            if auto_w:
                target["strategies"][0]["within"] = auto_w
                step["from_session"]["auto_within"] = {
                    "within": auto_w,
                    "why": "选择器在会话树上多匹配，按实际下发的点击节点推导容器消歧（结构性限定，非坐标）"}
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
