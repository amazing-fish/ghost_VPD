"""执行契约：每一步 = 执行前观测 → 定位 → 安全检查 → 下发 → 执行后观测与后置条件判定。

三层结果分别记录：dispatch.sent（命令已发出）、dispatch.executor_ok（执行器返回成功）、
post.met（预期业务状态已出现）。重试与恢复会追加新的 attempt / recovery 记录，不修改首次失败。
恢复只允许：重新观察、关闭"已评审的良性中断"、对幂等步骤重发；异常中断和未知中断不会被自动关闭。
"""
from __future__ import annotations

import time

from .devices import DeviceResult
from .locate import resolve
from .util import now_iso

ENV_ERRORS = {"screen_locked", "screen_off", "device_offline", "app_missing", "tool_missing"}
STATUS_MAP = {
    "target_unresolved": ("blocked", "tool", "目标未能可靠定位（已重新观察）"),
    "target_ambiguous": ("blocked", "tool", "目标匹配到多个元素，拒绝盲点"),
    "policy_denied": ("blocked", "tool", "动作被安全策略拒绝"),
    "unobservable": ("blocked", "tool", "动作后无法取得可判定的观测（无 UI 树或解析失败）"),
    "budget_exhausted": ("blocked", "tool", "动作预算耗尽"),
    "invalid_step": ("blocked", "tool", "步骤定义无效"),
    "interrupted": ("blocked", "requirement_unclear", "出现未评审或非良性的中断，未自动处理，需确认其性质"),
    "anomaly": ("fail_candidate", "product", "出现异常中断或疑似崩溃"),
    "postcondition_not_met": ("fail_candidate", "product", "动作后预期状态未出现"),
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
        max_retry = min(int(step.get("retry", 1)), 2)
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
        if ctx.actions >= ctx.policy.max_actions_per_run:
            att["status"] = "budget_exhausted"
            return att
        obs, tree = ctx.observe(f"{step['id']}#{n}:before")
        att.update(obs_before=obs["obs_id"], state_before=obs["page"]["state"])
        res = None
        if kind == "tap":
            tid, target = ctx.app.target(step["target"])
            loc, obs, tree = self._locate(tid, target, obs, tree, step)
            att["locate"] = loc
            if loc["status"] != "found":
                att["status"] = "target_ambiguous" if loc["status"] == "ambiguous" else "target_unresolved"
                return att
            pol = ctx.policy.check_tap(tree, loc["point"], loc["basis"], target,
                                       ctx.app.states.deny_for(obs["page"]["state"]))
            att["policy"] = pol
            if not pol["allowed"]:
                att["status"] = "policy_denied"
                return att
            res = ctx.device.tap(*loc["point"])
        elif kind == "launch":
            res = ctx.device.launch(ctx.app.bundle, ctx.app.ability)
        elif kind in ("back", "home"):
            res = ctx.device.key(kind)
        elif kind == "swipe":
            res = self._swipe(step.get("direction", "up"))
        elif kind == "stop_app":
            res = ctx.device.stop(ctx.app.bundle)
        elif kind == "input":
            tid, target = ctx.app.target(step["target"])
            pol = ctx.policy.check_input(step.get("text", ""), target)
            att["policy"] = pol
            if not pol["allowed"]:
                att["status"] = "policy_denied"
                return att
            loc, obs, tree = self._locate(tid, target, obs, tree, step)
            att["locate"] = loc
            if loc["status"] != "found":
                att["status"] = "target_unresolved"
                return att
            res = ctx.device.input_text(*loc["point"], step.get("text", ""))
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
        return att

    # ---- 定位（可含声明过的滚动查找）----
    def _locate(self, tid, target, obs, tree, step) -> tuple:
        ctx = self.ctx
        pref = ctx.overlay.locator_pref(tid) if ctx.overlay else None
        point = step.get("point") if ctx.mode == "agent" else None
        loc = resolve(tid, target, tree, ctx.observer.screenshot_abs(obs), ctx.app.dir, pref, point)
        scroll = target.get("scroll")
        swipes = 0
        while loc["status"] == "not_found" and scroll and swipes < int(scroll.get("max", 3)):
            swipes += 1
            r = self._swipe(scroll.get("direction", "up"))
            ctx.actions += 1
            ctx.store.event("scroll_search", target=tid, n=swipes, ok=r.ok)
            obs, tree = ctx.observe(f"{step['id']}:scroll{swipes}")
            loc = resolve(tid, target, tree, ctx.observer.screenshot_abs(obs), ctx.app.dir, pref, point)
        if swipes:
            loc["scroll_swipes"] = swipes
        loc["obs"] = obs["obs_id"]
        return loc, obs, tree

    def _swipe(self, direction: str) -> DeviceResult:
        size = self.ctx.observer.last_size
        if not size:
            return DeviceResult("swipe", False, False, "screen_size_unknown", "未知屏幕尺寸，无法计算滑动坐标")
        w, h = size
        y1, y2 = (int(h * 0.7), int(h * 0.3)) if direction == "up" else (int(h * 0.3), int(h * 0.7))
        return self.ctx.device.swipe(w // 2, y1, w // 2, y2)

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
            elapsed = round(time.monotonic() - t0, 2)
            if crash or anomalies:
                status = "anomaly"
                break
            if met:
                status = "ok"
                break
            if interval <= 0 or elapsed >= timeout:
                if page.get("interrupts"):
                    status = "interrupted"
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
        if st == "target_unresolved":
            time.sleep(min(1.0, ctx.device.poll_interval))
            return {"action": "reobserve_and_retry", "performed": True, "reason": "等待后重新观察并完整重试定位"}
        if st == "device_error" and att.get("error_class") in ("timeout", "command_failed"):
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


def _failure_detail(att: dict) -> dict:
    d = {"status": att["status"]}
    for k in ("error_class", "policy", "dispatch"):
        if att.get(k):
            d[k] = att[k]
    if att.get("locate"):
        d["locate"] = {k: att["locate"].get(k) for k in ("status", "target", "attempts")}
    if att.get("post"):
        d["post"] = {k: att["post"].get(k) for k in ("expected", "observed", "interrupts", "suspected_crash")}
    return d
