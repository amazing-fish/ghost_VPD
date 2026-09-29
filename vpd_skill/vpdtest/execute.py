"""执行契约：每一步 = 执行前观测 → 定位 → 安全检查 → 下发 → 执行后观测与后置条件判定。

三层结果分别记录：dispatch.sent（命令已发出）、dispatch.executor_ok（执行器返回成功）、
post.met（预期业务状态已出现）。重试与恢复会追加新的 attempt / recovery 记录，不修改首次失败。
恢复只允许：重新观察、关闭"已评审的良性中断"、对幂等步骤重发；异常中断和未知中断不会被自动关闭。
"""
from __future__ import annotations

import re
import time

from .devices import DeviceResult
from .layout import key as layout_key
from .layout import resolve_scope
from .locate import resolve
from .util import now_iso

ENV_ERRORS = {"screen_locked", "screen_off", "device_offline", "app_missing", "tool_missing", "transport_unavailable"}
STATUS_MAP = {
    "observation_failed": ("blocked", "environment", "观测查询失败（设备连接/通道异常），未执行动作"),
    "target_unresolved": ("blocked", "tool", "目标未能可靠定位（已重新观察）"),
    "target_ambiguous": ("blocked", "tool", "目标匹配到多个元素，拒绝盲点"),
    "target_partially_visible": ("blocked", "tool", "目标只露出一小部分（被裁剪/遮挡或已滑到底），不点边缘"),
    "policy_denied": ("blocked", "tool", "动作被安全策略拒绝"),
    "unobservable": ("blocked", "tool", "动作后无法取得可判定的观测（无 UI 树或解析失败）"),
    "budget_exhausted": ("blocked", "tool", "动作预算耗尽"),
    "invalid_step": ("blocked", "tool", "步骤定义无效"),
    "interrupted": ("blocked", "requirement_unclear", "出现未评审或非良性的中断，未自动处理，需确认其性质"),
    "anomaly": ("fail_candidate", "product", "出现异常中断或疑似崩溃"),
    "postcondition_not_met": ("fail_candidate", "product", "动作后预期状态未出现"),
    "motion_timeout": ("fail_candidate", "product", "车辆运动超过用例声明的 max_motion_s 仍未观测到停止/结束"
                                                    "（可能卡住、终点不对或被挡路；也可能是现场环境原因，需人工确认）"),
    "monitor_abort": ("fail_candidate", "product", "监视期间出现用例声明的中止信号"),
    "target_disabled": ("blocked", "environment", "目标控件当前不可用（置灰），按前置条件不满足处理，未点击"),
}


def classify(att: dict) -> tuple:
    st = att["status"]
    if st == "device_error":
        ec = att.get("error_class")
        return ("blocked", "environment" if ec in ENV_ERRORS else "tool",
                f"设备/执行器错误 {ec}: {(att.get('dispatch') or {}).get('error') or ''}".strip())
    return STATUS_MAP.get(st, ("blocked", "tool", f"未知状态 {st}"))


class Executor:
    def __init__(self, ctx):
        self.ctx = ctx

    # ---- 对外入口 ----
    def run_step(self, step: dict, case_id: str) -> dict:
        if step["action"] == "ensure_state":
            return self._ensure_state(step, case_id)
        if step["action"] == "flow":
            return self._flow(step, case_id)
        # 监视是一段连续观测，整体重跑会重复计时，不重试
        max_retry = 0 if step["action"] == "monitor" else min(int(step.get("retry", 1)), 2)
        attempts, recoveries, n = [], [], 0
        while True:
            n += 1
            att = self._attempt(step, n, case_id)
            att["step_id"] = step["id"]
            attempts.append(att)
            self.ctx.store.event("attempt", step=step["id"], n=n, status=att["status"])
            if att["status"] == "ok" or n > max_retry:
                break
            rec = self._recover(step, att, case_id)
            recoveries.append(rec)
            self.ctx.store.event("recovery", step=step["id"], after_attempt=n,
                                 **{k: v for k, v in rec.items() if k in ("action", "performed", "reason", "interrupt")})
            if not rec["performed"] or (rec.get("post") or {}).get("status") == "ok":
                break
        return self._summarize(step, attempts, recoveries)

    # ---- 单次尝试 ----
    def _attempt(self, step: dict, n: int, case_id: str) -> dict:
        ctx, kind = self.ctx, step["action"]
        att = {"n": n, "ts": now_iso(), "action": kind}
        safety = bool(step.get("_safety"))  # 安全停止步骤：不受动作预算与运动超时拦截
        if not safety and ctx.motion.over():
            att.update(status="motion_timeout", motion={"elapsed_s": ctx.motion.elapsed(), "max_s": ctx.motion.max_s})
            return att
        if not safety and kind != "monitor" and ctx.actions >= ctx.policy.max_actions_per_run:
            att["status"] = "budget_exhausted"
            return att
        if kind == "monitor":
            return self._monitor(step, att)
        obs, tree = ctx.observe(f"{step['id']}#{n}:before")
        att.update(obs_before=obs["obs_id"], state_before=obs["page"]["state"])
        if obs["page"].get("observation_error") and kind in ("tap", "input", "swipe"):
            # 观测本身失败时不定位、不下发：否则会把连接问题记成"目标找不到"
            att.update(status="observation_failed", error_class=obs["page"]["observation_error"])
            return att
        res = None
        if kind == "read":
            if step.get("target") is None and step.get("read_scope") == "page":
                loc = {"status": "found", "target": None, "point": None, "basis": "tree"}
            else:
                tid, target = self._target(step)
                loc, obs, tree = self._locate(tid, target, obs, tree, step)
            att["locate"] = loc
            if loc["status"] != "found":
                att["status"] = _unresolved(loc)
                return att
            att["measurement"] = _measure(tree, loc, step)
            ctx.store.event("measure", step=step["id"], **{k: v for k, v in att["measurement"].items() if k != "raw"})
            att.update(status="ok" if att["measurement"]["value"] is not None else "postcondition_not_met",
                       obs_after=obs["obs_id"])
            return att
        if step.get("skip_if_absent") and kind in ("tap", "input"):
            # 可选步骤（例如"出现安全码输入框才输入"）：本页没有该文字就跳过，不算失败
            if not any(re.search(step["skip_if_absent"], x) for x in obs.get("texts") or []):
                att.update(status="ok", skipped=f"本页没有匹配「{step['skip_if_absent']}」的文字，跳过", obs_after=obs["obs_id"])
                return att
        if kind == "tap":
            tid, target = self._target(step)
            loc, obs, tree = self._locate(tid, target, obs, tree, step)
            att["locate"] = loc
            if loc["status"] != "found":
                att["status"] = _unresolved(loc)
                return att
            if step.get("require_enabled"):
                # 点之前先确认控件可用（例如「开始」置灰表示当前不可激活）：不可用就不点，按前置条件不满足处理
                node = next((tree.nodes[x["node"]["idx"]] for x in loc.get("attempts", [])
                             if x.get("status") == "found" and x.get("node")), None) if tree is not None else None
                enabled, ev = tree.enabled_state(node, target.get("enabled_by")) if node is not None else (None, {})
                att["enabled_check"] = ev
                if enabled is not True:
                    att.update(status="target_disabled" if enabled is False else "target_unresolved",
                               obs_after=obs["obs_id"])
                    return att
            if step.get("at") or step.get("random_point"):
                # 在目标容器内按比例取点（例如在地图上点一个车位）：比例相对可见区域现算，不写像素
                loc = {**loc, "point": self._point_in(loc, step, n), "container_point": True}
                att["locate"] = loc
            pol = ctx.policy.check_tap(tree, loc["point"], loc["basis"], target,
                                       ctx.app.states.deny_for_page(obs["page"]), ctx.authorized)
            att["policy"] = pol
            if not pol["allowed"]:
                att["status"] = "policy_denied"
                return att
            res = ctx.device.tap(*loc["point"])
            if res.ok and target.get("side_effect") == "vehicle_motion":
                # 先挂上看门狗再做后置观测：后置观测里出现的停止状态会按规则解除
                ctx.motion.arm(step["id"], pol.get("label") or tid, obs["page"]["state"])
        elif kind == "launch":
            res = ctx.device.launch(ctx.app.bundle, ctx.app.ability)
        elif kind == "resume":
            # 从后台拉回：与 launch 同一条命令；区别在于判定"回到原页面"而不是"冷启动"
            att["process_alive_before"] = ctx.device.process_alive(ctx.app.bundle)
            res = ctx.device.launch(ctx.app.bundle, ctx.app.ability)
        elif kind == "wait" and step.get("duration_s"):
            dur = min(float(step["duration_s"]), float(ctx.policy.supervision.get("max_monitor_s", 600)))
            waited = 0.0
            while waited < dur and not ctx.motion.over():
                ctx.sleep(min(1.0, dur - waited))
                waited += min(1.0, dur - waited)
            att["waited_s"] = round(waited, 2)
        elif kind in ("back", "home"):
            res = ctx.device.key(kind)
        elif kind == "swipe" and step.get("random"):
            res, att["swipe"] = self._random_swipe(tree, obs.get("layout"), step)
        elif kind == "swipe":
            res, att["swipe"] = self._swipe(step.get("direction", "up"), tree, obs.get("layout"), step.get("within"))
        elif kind == "stop_app":
            res = ctx.device.stop(ctx.app.bundle)
        elif kind == "input":
            tid, target = self._target(step)
            raw = step.get("text", "")
            # 流程内 ${param:x}：看调用方传入的原始值是不是密钥引用（${var:secret}），是则按密钥处理
            m = re.fullmatch(r"\$\{param:([A-Za-z0-9_.-]+)\}", str(raw))
            origin = (step.get("_flow_raw") or {}).get(m.group(1)) if m else raw
            is_ref = bool(ctx.params and ctx.params.is_secret_ref(origin))
            pol = ctx.policy.check_input(raw, target, is_ref)
            att["policy"] = pol
            if not pol["allowed"]:
                att["status"] = "policy_denied"
                return att
            loc, obs, tree = self._locate(tid, target, obs, tree, step)
            att["locate"] = loc
            if loc["status"] != "found":
                att["status"] = _unresolved(loc)
                return att
            text = ctx.params.subst(raw, step.get("_flow_params")) if ctx.params else raw
            att["input"] = {"ref": raw, "secret": is_ref, "length": len(text)}  # 只记引用与长度，不记值
            res = ctx.device.input_text(*loc["point"], text)
        elif kind != "wait":
            att["status"] = "invalid_step"
            return att
        if res is not None:
            ctx.actions += 1
            att["dispatch"] = {"op": res.op, "sent": res.sent, "executor_ok": res.ok, "error_class": res.error_class,
                               "error": res.error, "duration_ms": res.duration_ms}
            ctx.store.event("dispatch", step=step["id"], n=n, **att["dispatch"])
            if not res.ok:
                o, _ = ctx.observe(f"{step['id']}#{n}:after-error")
                att.update(status="device_error", error_class=res.error_class, obs_after=o["obs_id"])
                return att
        post = self._post(step, n, case_id, was_in_app=att["state_before"] != "out_of_app")
        att.update(post=post, obs_after=post["obs_id"], status=post["status"])
        if kind == "resume":
            # 后台期间进程被回收时，拉起就是冷启动：页面是否恢复由 expect_state 判定，这里只记录事实
            att["resume"] = {"process_alive_before": att.get("process_alive_before"),
                             "cold_start": att.get("process_alive_before") is False}
        return att

    # ---- 监视：一段时间内连续观测（运动中信息、TOR/接管提示、异常显示）----
    def _monitor(self, step: dict, att: dict) -> dict:
        """不下发动作，按间隔连续观测，记录时间线：状态序列、新出现的文字、watch 规则命中、中断。
        结束条件：until_state 出现 / stop_on 规则命中（正常结束）/ abort_on 命中或崩溃（失败，交由运行器安全停止）/
        运动超时 / 达到 duration_s。真机上每次采样本身要几秒，实际间隔以时间线里的 t 为准。"""
        import re
        ctx = self.ctx
        cap = float(ctx.policy.supervision.get("max_monitor_s", 600))
        duration = min(float(step.get("duration_s", 30)), cap)
        interval = max(0.5, float(step.get("interval_s", 2)))
        watch = {k: re.compile(v) for k, v in (step.get("watch") or {}).items()}
        stop_on, abort_on = set(step.get("stop_on", [])), set(step.get("abort_on", []))
        until = step.get("until_state")
        until_text = re.compile(step["until_text"]) if step.get("until_text") else None
        until_gone = re.compile(step["until_text_gone"]) if step.get("until_text_gone") else None
        until_any = until or until_text or until_gone
        shots = step.get("screenshots", True)
        t0, timeline, hits = ctx.clock(), [], {}
        prev_texts, prev_alive, states, first_obs = set(), None, [], None
        stopped_by, status, errors, gone_seen = None, None, 0, False
        for _ in range(int(duration / interval) + 2):
            t = round(ctx.clock() - t0, 2)
            obs, _tree = ctx.observe(f"{step['id']}:t{t}", screenshot=shots)
            first_obs = first_obs or obs["obs_id"]
            page = obs["page"]
            texts = set(obs.get("texts") or [])
            new = sorted(texts - prev_texts) if page["state"] != "out_of_app" else []
            fired = [k for k, rx in watch.items() if any(rx.search(x) for x in new)]
            for k in fired:
                hits.setdefault(k, {"count": 0, "first_t": t, "first_obs": obs["obs_id"],
                                    "text": next(x for x in new if watch[k].search(x))})
                hits[k]["count"] += 1
            died = prev_alive is True and page.get("process_alive") is False
            anomalies = [i["id"] for i in page.get("interrupts", []) if i["class"] == "anomaly"]
            blocking = [i["id"] for i in page.get("interrupts", []) if i["class"] == "blocking"]
            if until_gone and any(until_gone.search(x) for x in texts):
                gone_seen = True
            errors += 1 if page.get("observation_error") else 0
            entry = {"t": t, "obs": obs["obs_id"], "state": page["state"]}
            if new:
                entry["new_texts"] = new[:50]
            if fired:
                entry["hits"] = fired
            if page.get("interrupts"):
                entry["interrupts"] = [i["id"] for i in page["interrupts"]]
            if died:
                entry["process_died"] = True
            if ctx.motion.armed:
                entry["motion_s"] = ctx.motion.elapsed()
            timeline.append(entry)
            if not states or states[-1] != page["state"]:
                states.append(page["state"])
            if page["state"] != "out_of_app":
                prev_texts = texts
            if page.get("process_alive") is not None:
                prev_alive = page["process_alive"]
            if died and not step.get("allow_process_exit"):
                status, stopped_by = "anomaly", "process_died"
            elif anomalies:
                status, stopped_by = "anomaly", f"interrupt:{anomalies[0]}"
            elif blocking:
                status, stopped_by = "interrupted", f"interrupt:{blocking[0]}"
            elif set(fired) & abort_on:
                status, stopped_by = "monitor_abort", f"abort_on:{sorted(set(fired) & abort_on)[0]}"
            elif ctx.motion.over():
                status, stopped_by = "motion_timeout", "motion_timeout"
            elif until and ctx.app.states.is_state(page, until):
                status, stopped_by = "ok", f"until_state:{until}"
            elif until_text and any(until_text.search(x) for x in texts):
                status, stopped_by = "ok", f"until_text:{until_text.pattern}"
            elif until_gone and gone_seen and page["state"] not in ("out_of_app", "undetermined") and \
                    not any(until_gone.search(x) for x in texts):
                # 先出现过再消失才算结束：点击后第一帧文字可能还没出来，不能当成"已消失"
                status, stopped_by = "ok", f"until_text_gone:{until_gone.pattern}"
            elif set(fired) & stop_on:
                status, stopped_by = "ok", f"stop_on:{sorted(set(fired) & stop_on)[0]}"
            elif ctx.clock() - t0 + interval > duration:
                status = "postcondition_not_met" if until_any else "ok"
                stopped_by = "duration"
            if status:
                break
            ctx.sleep(interval)
        if status is None:
            status, stopped_by = ("postcondition_not_met" if until_any else "ok"), "sample_cap"
        if errors and errors == len(timeline):
            status = "observation_failed"
        att.update(status=status, obs_before=first_obs, state_before=timeline[0]["state"] if timeline else None,
                   obs_after=timeline[-1]["obs"] if timeline else None,
                   monitor={"duration_s": round(ctx.clock() - t0, 2), "samples": len(timeline), "stopped_by": stopped_by,
                            "until_state": until, "hits": hits, "states": states, "timeline": timeline,
                            "observation_errors": errors})
        ctx.store.event("monitor_done", step=step["id"], stopped_by=stopped_by, samples=len(timeline),
                        hits={k: v["count"] for k, v in hits.items()})
        return att

    # ---- 定位（可含声明过的滚动查找）----
    def _locate(self, tid, target, obs, tree, step) -> tuple:
        ctx = self.ctx
        lay = obs.get("layout")
        pref = ctx.overlay.locator_pref(tid, layout_key(lay)) if ctx.overlay else None
        point, note = (step.get("point") if ctx.mode == "agent" else None), None
        if point is not None and step.get("point_window") and lay and lay.get("window_px") \
                and list(step["point_window"]) != list(lay["window_px"]):
            # 像素坐标只在提出它的那个窗口尺寸下有意义；旋转/折叠/分屏后必须重新观察再提议
            note = f"坐标来自 {step['point_window']} 的窗口，当前窗口为 {lay['window_px']}，已丢弃"
            point = None
        loc = resolve(tid, target, tree, ctx.observer.screenshot_abs(obs), ctx.app.dir, pref, point, lay)
        scroll = target.get("scroll")
        swipes, trail = 0, []
        while loc["status"] in ("not_found", "partially_visible") and scroll and swipes < int(scroll.get("max", 3)):
            swipes += 1
            before = tree.fingerprint() if (tree is not None and tree.status == "ok") else None
            r, info = self._swipe(scroll.get("direction", "up"), tree, lay, scroll.get("within"))
            if r.sent:
                ctx.actions += 1
            obs, tree = ctx.observe(f"{step['id']}:scroll{swipes}")
            lay = obs.get("layout")
            after = tree.fingerprint() if (tree is not None and tree.status == "ok") else None
            info.update(n=swipes, ok=r.ok, error=r.error, content_changed=before != after if before else None)
            trail.append(info)
            ctx.store.event("scroll_search", target=tid, **info)
            loc = resolve(tid, target, tree, ctx.observer.screenshot_abs(obs), ctx.app.dir, pref, point, lay)
            if not r.ok or (before and before == after):
                if r.ok:
                    loc["scroll_end"] = True  # 滑动后内容没有变化：已到底或滑动未生效，不再继续
                break
        if swipes:
            loc["scroll_swipes"], loc["scroll"] = swipes, trail
        if note:
            loc["point_rejected"] = note
        loc["obs"] = obs["obs_id"]
        loc["layout_key"] = layout_key(lay)
        return loc, obs, tree

    def _target(self, step: dict) -> tuple:
        """取目标并代入参数（流程参数 ${param:x}、用例数据 ${var:x}）。"""
        tid, target = self.ctx.app.target(step["target"])
        if self.ctx.params is not None:
            from .params import with_params
            target = with_params(target, self.ctx.params, step.get("_flow_params"))
        return tid, target

    def _point_in(self, loc: dict, step: dict, n: int) -> list:
        """容器内取点：at=[fx, fy] 固定比例；random_point=true 按种子随机（每次尝试不同，重试时换一个点）。"""
        import random
        rect = loc.get("visible") or loc.get("bounds")
        if step.get("random_point"):
            rng = random.Random(f"{step.get('seed', self.ctx.seed)}:{step['id']}:{n}")
            fx, fy = rng.uniform(0.25, 0.75), rng.uniform(0.25, 0.75)
        else:
            fx, fy = step["at"]
        return [int(rect[0] + (rect[2] - rect[0]) * fx), int(rect[1] + (rect[3] - rect[1]) * fy)]

    def _random_swipe(self, tree, lay, step: dict) -> tuple:
        """可复现的随机滑动（如"随机拖动地图"）：种子记入证据，重跑时用同一种子得到同样的轨迹；
        起止点都在容器可见区域内，按比例取，不写像素。"""
        import random
        seed = step.get("seed", self.ctx.seed)
        rng = random.Random(f"{seed}:{step['id']}")
        info = {"random": True, "seed": seed}
        rect = None
        if tree is not None and tree.status == "ok":
            scope = resolve_scope(step.get("within"), tree, lay)
            if scope is not None and not scope:
                return DeviceResult("swipe", False, False, "scroll_scope_missing",
                                    f"滑动范围 {step['within']} 在当前页面不存在"), info
            rect = tree.visible_rect(scope[0]) if scope else tree.window_rect()
        if not rect:
            return DeviceResult("swipe", False, False, "screen_size_unknown", "未知滑动区域"), info
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        fx1, fy1 = rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)
        fx2, fy2 = min(0.85, max(0.15, fx1 + rng.uniform(-0.3, 0.3))), min(0.85, max(0.15, fy1 + rng.uniform(-0.3, 0.3)))
        p1 = [rect[0] + int(w * fx1), rect[1] + int(h * fy1)]
        p2 = [rect[0] + int(w * fx2), rect[1] + int(h * fy2)]
        info.update(area=list(rect), fractions=[round(fx1, 3), round(fy1, 3), round(fx2, 3), round(fy2, 3)],
                    **{"from": p1, "to": p2})
        return self.ctx.device.swipe(*p1, *p2), info

    def _flow(self, step: dict, case_id: str) -> dict:
        """流程 = 带参数的原子能力序列（flows.json）。展开后逐步执行，每个子步骤仍走完整的观测/定位/策略/后置条件。"""
        ctx = self.ctx
        base = {"step_id": step["id"], "action": "flow", "flow": step.get("flow"), "critical": step.get("critical", True),
                "oracle_refs": step.get("oracle_refs", []), "recoveries": [], "adapted": False}
        spec = ctx.app.flows.get(step.get("flow"))
        if not spec:
            return {**base, "outcome": "blocked", "attribution": "tool", "attempts": [],
                    "reason": f"flows.json 中没有流程 {step.get('flow')}"}
        local = ctx.params.subst(dict(step.get("params", {}))) if ctx.params else dict(step.get("params", {}))
        missing = [p for p in spec.get("params", {}) if p not in local]
        if missing:
            return {**base, "outcome": "blocked", "attribution": "tool", "attempts": [], "reason": f"流程缺少参数 {missing}"}
        subs = []
        for i, sub in enumerate(spec["steps"]):
            s = self.run_step({"critical": True, "retry": 1, **sub, "id": f"{step['id']}.{i + 1}",
                               "_flow_params": local, "_flow_raw": step.get("params", {})}, case_id)
            subs.append(s)
            if not s["outcome"].startswith("passed") and s.get("critical", True):
                return {**base, "outcome": s["outcome"], "attribution": s.get("attribution"),
                        "reason": f"流程 {step['flow']} 第 {i + 1} 步（{sub.get('desc', sub['action'])}）未通过：{s.get('reason')}",
                        "sub_steps": subs, "attempts": [a for x in subs for a in x["attempts"]],
                        "first_failure": s.get("first_failure"), "final_obs": s.get("final_obs"),
                        "final_status": s.get("final_status"), "measurement": s.get("measurement")}
        order = ["passed", "passed_after_adaptation", "passed_after_recovery"]
        outcome = max((s["outcome"] for s in subs if s["outcome"] in order), key=order.index, default="passed")
        meas = next((s["measurement"] for s in reversed(subs) if s.get("measurement")), None)
        return {**base, "outcome": outcome, "sub_steps": subs, "attempts": [a for x in subs for a in x["attempts"]],
                "adapted": any(s["adapted"] for s in subs),
                "first_failure": next((s["first_failure"] for s in subs if s.get("first_failure")), None),
                "recoveries": [r for s in subs for r in s["recoveries"]], "final_obs": subs[-1].get("final_obs"),
                "measurement": meas}

    def _swipe(self, direction: str, tree=None, lay=None, within=None) -> tuple:
        """在可滚动容器内滑动：起止点取容器可见区域的 75%/25% 高度，而不是整屏比例。
        折叠屏分栏时列表可能只在左栏，按整屏中线滑会滑到右栏上。"""
        info, rect = {"direction": direction}, None
        if tree is not None and tree.status == "ok":
            scope = resolve_scope(within, tree, lay)
            if scope is not None and not scope:
                return DeviceResult("swipe", False, False, "scroll_scope_missing",
                                    f"滚动范围 {within} 在当前页面不存在"), {**info, "container": None}
            node = tree.scroll_container(scope)
            if node is not None:
                rect = tree.visible_rect(node)
                info["container"] = {"type": node.type, "id": node.id, "visible": list(rect)}
            else:
                rect = tree.window_rect()
                info["container"] = None
                info["fallback"] = "window"
        elif self.ctx.observer.last_window:
            rect, info["fallback"] = self.ctx.observer.last_window, "last_window"
        if not rect:
            return DeviceResult("swipe", False, False, "screen_size_unknown", "未知窗口尺寸，无法计算滑动坐标"), info
        x = (rect[0] + rect[2]) // 2
        top, bottom = rect[1] + int((rect[3] - rect[1]) * 0.25), rect[1] + int((rect[3] - rect[1]) * 0.75)
        y1, y2 = (bottom, top) if direction == "up" else (top, bottom)
        info["from"], info["to"] = [x, y1], [x, y2]
        return self.ctx.device.swipe(x, y1, x, y2), info

    # ---- 后置条件 ----
    def _post(self, step: dict, n: int, case_id: str, was_in_app: bool = True) -> dict:
        ctx = self.ctx
        expect = step.get("expect_state")
        default = float(step.get("timeout_s", 8))
        timeout = ctx.overlay.wait_for(f"{case_id}:{step['id']}", default, ctx.policy.max_wait_s) \
            if ctx.overlay else min(default, ctx.policy.max_wait_s)
        interval = ctx.device.poll_interval
        t0, polls = time.monotonic(), 0
        while True:
            polls += 1
            obs, tree = ctx.observe(f"{step['id']}#{n}:after")
            page = obs["page"]
            # 动作前在 APP 内、动作后进程不在且不是主动结束/返回桌面 → 疑似崩溃
            crash = (was_in_app and page["state"] == "out_of_app" and page.get("process_alive") is False
                     and step["action"] not in ("stop_app", "home"))
            anomalies = [i for i in page.get("interrupts", []) if i["class"] == "anomaly"]
            met = expect is None or (ctx.app.states.is_state(page, expect) and not page.get("interrupts"))
            if met and step.get("expect_text"):
                # 状态签名之外的补充后置条件：可见文字里要有匹配项（例如点地图后出现带「收藏」的车位卡片）
                met = any(re.search(step["expect_text"], x) for x in obs.get("texts") or [])
            elapsed = round(time.monotonic() - t0, 2)
            if crash or anomalies:
                status = "anomaly"
                break
            if met:
                status = "ok"
                break
            if any(i["class"] == "blocking" for i in page.get("interrupts", [])):
                status = "interrupted"  # 需要人工处理的中断（例如安全码）：不等超时，也不自动处理
                break
            if interval <= 0 or elapsed >= timeout:
                if page.get("interrupts"):
                    status = "interrupted"
                elif page.get("observation_error"):
                    status = "observation_failed"
                elif page["state"] == "undetermined":
                    status = "unobservable"
                else:
                    status = "postcondition_not_met"
                break
            time.sleep(interval)
        return {"obs_id": obs["obs_id"], "expected": expect, "observed": page["state"], "met": status == "ok",
                "status": status, "interrupts": page.get("interrupts", []), "suspected_crash": crash,
                "anomalies": anomalies, "elapsed_s": elapsed, "timeout_s": timeout, "polls": polls}

    # ---- 恢复 ----
    def _recover(self, step: dict, att: dict, case_id: str) -> dict:
        ctx, st = self.ctx, att["status"]
        if st == "interrupted":
            ints = att["post"]["interrupts"]
            bad = [i for i in ints if not (i["class"] == "benign" and i["review"] == "reviewed")]
            if bad:
                return {"action": "none", "performed": False, "interrupt": bad[0]["id"],
                        "reason": f"中断 {bad[0]['id']} 未评审或非良性（{bad[0]['class']}/{bad[0]['review']}），不自动关闭"}
            spec = ctx.app.states.interrupts[ints[0]["id"]]
            sub = {"id": f"{step['id']}.dismiss", "action": "tap", "target": spec["dismiss_target"],
                   "expect_state": step.get("expect_state"), "timeout_s": step.get("timeout_s", 8)}
            r = self._attempt(sub, 1, case_id)
            return {"action": "dismiss_interrupt", "performed": "dispatch" in r, "interrupt": ints[0]["id"],
                    "reason": "关闭已评审的良性中断后重新判定后置条件", "attempt": r, "post": r.get("post"),
                    "status": r["status"]}
        if st in ("target_unresolved", "target_partially_visible"):
            time.sleep(min(1.0, ctx.device.poll_interval))
            return {"action": "reobserve_and_retry", "performed": True, "reason": "等待后重新观察并完整重试定位"}
        if st == "observation_failed":
            time.sleep(min(3.0, 3 * ctx.device.poll_interval))  # hdc 通道重建通常几秒内恢复
            return {"action": "reobserve_and_retry", "performed": True, "reason": "观测查询失败，等待后重新观察"}
        if st == "device_error" and att.get("error_class") in ("timeout", "command_failed", "transport_unavailable"):
            return {"action": "redispatch", "performed": True, "reason": "执行器瞬时错误，重发"}
        if st == "postcondition_not_met" and step.get("idempotent"):
            return {"action": "redispatch", "performed": True, "reason": "步骤声明为幂等，允许重发一次"}
        return {"action": "none", "performed": False, "reason": f"{st} 不属于允许的恢复范围"}

    # ---- 汇总 ----
    def _summarize(self, step: dict, attempts: list, recoveries: list) -> dict:
        last, last_rec = attempts[-1], (recoveries[-1] if recoveries else None)
        rec_ok = bool(last_rec and last_rec.get("status") == "ok")
        ok = last["status"] == "ok" or rec_ok
        adapted = any((a.get("locate") or {}).get("adapted") for a in attempts)
        first_bad = next((a for a in attempts if a["status"] != "ok"), None)
        out = {"step_id": step["id"], "action": step["action"], "critical": bool(step.get("critical", True)),
               "oracle_refs": step.get("oracle_refs", []), "attempts": attempts, "recoveries": recoveries,
               "adapted": adapted}
        if last.get("measurement"):
            out["measurement"] = last["measurement"]
        if last.get("monitor"):
            out["monitor"] = last["monitor"]
        if last.get("resume"):
            out["resume"] = last["resume"]
        if ok:
            out["outcome"] = "passed_after_recovery" if first_bad else ("passed_after_adaptation" if adapted else "passed")
            out["final_obs"] = last_rec["post"]["obs_id"] if (rec_ok and last["status"] != "ok") else last.get("obs_after")
        else:
            out["outcome"], out["attribution"], out["reason"] = classify(last)
            out["final_obs"] = last.get("obs_after") or last.get("obs_before")
            out["final_status"] = last["status"]
        if first_bad:
            out["first_failure"] = {"attempt": first_bad["n"], "status": first_bad["status"],
                                    "obs_before": first_bad.get("obs_before"), "obs_after": first_bad.get("obs_after"),
                                    "detail": _failure_detail(first_bad)}
        return out

    # ---- 到达已知状态 ----
    def _ensure_state(self, step: dict, case_id: str) -> dict:
        ctx, target = self.ctx, step["state"]
        obs, _ = ctx.observe(f"{step['id']}:check")
        base = {"step_id": step["id"], "action": "ensure_state", "critical": True, "oracle_refs": [],
                "recoveries": [], "adapted": False}
        if ctx.app.states.is_state(obs["page"], target) and not obs["page"].get("interrupts"):
            return {**base, "outcome": "passed", "attempts": [], "note": "已处于目标状态", "final_obs": obs["obs_id"]}
        recipe = ctx.app.states.recipes.get(target)
        if not recipe:
            return {**base, "outcome": "blocked", "attribution": "tool", "attempts": [],
                    "reason": f"没有到达状态 {target} 的 recipe", "final_obs": obs["obs_id"]}
        subs = []
        for i, sub in enumerate(recipe):
            s = self.run_step({"critical": True, "retry": 1, **sub, "id": f"{step['id']}.{i + 1}"}, case_id)
            subs.append(s)
            if not s["outcome"].startswith("passed"):
                return {**base, "outcome": s["outcome"], "attribution": s.get("attribution"),
                        "reason": f"前置状态 {target} 未到达：{s.get('reason')}", "sub_steps": subs,
                        "attempts": [a for x in subs for a in x["attempts"]], "first_failure": s.get("first_failure"),
                        "final_obs": s.get("final_obs"), "final_status": s.get("final_status")}
        order = ["passed", "passed_after_adaptation", "passed_after_recovery"]
        outcome = max((s["outcome"] for s in subs), key=order.index)
        ff = next((s["first_failure"] for s in subs if s.get("first_failure")), None)
        return {**base, "outcome": outcome, "sub_steps": subs, "attempts": [a for x in subs for a in x["attempts"]],
                "adapted": any(s["adapted"] for s in subs), "first_failure": ff,
                "recoveries": [r for s in subs for r in s["recoveries"]], "final_obs": subs[-1].get("final_obs")}


def _measure(tree, loc: dict, step: dict) -> dict:
    """读取目标所在行/卡片的文本并按 pattern 取值（例如算路距离 "1.2公里"）。只记录，不判定。"""
    import re
    node = tree.node_at(*loc["point"]) if (tree is not None and loc.get("point")) else None
    holder = tree.clickable_container(node) if node else None
    scope = step.get("read_scope", "node")
    if scope == "page":  # 整页可见文字里按 pattern 找（位置未知、只知道格式的读数，例如"1.2公里"）
        texts = tree.visible_texts() if tree is not None else []
    elif scope == "self":  # 只读定位到的节点自己的子树（目标本身就是容器、且不可点击时，不向上扩到整页）
        hit = next((a for a in loc.get("attempts", []) if a.get("status") == "found" and a.get("node")), None)
        found = tree.nodes[hit["node"]["idx"]] if (hit and tree is not None) else node
        texts = tree.subtree_texts(found) if found else []
    else:
        texts = ([node.text] if node and scope == "node" else tree.subtree_texts(holder or node) if node else [])
    raw = " ".join(t for t in texts if t)
    pat = step.get("pattern")
    m = re.search(pat, raw) if pat else None
    if scope == "page":  # 整页文字不整段落盘，只保留命中附近
        raw = raw[max(0, m.start() - 20):m.end() + 20] if m else ""
    value = (m.group(1) if (m and m.groups()) else (m.group(0) if m else None)) if pat else (raw or None)
    return {"name": step.get("measure", step["id"]), "value": value, "raw": raw[:200], "pattern": pat,
            "unit": step.get("unit"), "target": loc.get("target")}


def _unresolved(loc: dict) -> str:
    return {"ambiguous": "target_ambiguous", "partially_visible": "target_partially_visible"}.get(
        loc["status"], "target_unresolved")


def _failure_detail(att: dict) -> dict:
    d = {"status": att["status"]}
    for k in ("error_class", "policy", "dispatch", "motion"):
        if att.get(k):
            d[k] = att[k]
    if att.get("monitor"):
        d["monitor"] = {k: att["monitor"].get(k) for k in ("stopped_by", "samples", "states", "hits")}
    if att.get("locate"):
        d["locate"] = {k: att["locate"].get(k) for k in ("status", "target", "attempts")}
    if att.get("post"):
        d["post"] = {k: att["post"].get(k) for k in ("expected", "observed", "interrupts", "suspected_crash")}
    return d
