"""车辆运动的运行时安全：运动计时（看门狗）、安全停止序列、用例 safety 声明检查。

控车不是禁止项，但必须满足三件事：
1. 有人在场监护（run/act 的 --supervisor），自动探索永远不带运动授权；
2. 用例声明 safety.safe_stop（暂停/退出等安全停止步骤）和 max_motion_s（运动时长上限）；
3. 运行时一旦"运动已触发"后出现关键失败、异常、超时或用例结束仍未停，立刻执行安全停止，
   并把它单独记录为 safety_stop —— 不覆盖、不掩盖首次失败。

"运动已触发"只表示点击了 side_effect=vehicle_motion 的控件并下发成功，不代表车真的在动；
是否在动以观测到的页面状态为准（stopped_states 表示已停/已暂停/已结束）。
"""
from __future__ import annotations

from typing import Optional

from .util import now_iso


def problems(safety: Optional[dict], app, policy) -> list:
    """用例 safety 声明的静态检查（lint 与运行前都用）。"""
    out = []
    if not safety:
        return [{"field": "safety", "problem": "申请 vehicle_motion 的用例必须声明 safety（safe_stop / max_motion_s / stopped_states）"}]
    steps = safety.get("safe_stop") or []
    if not steps:
        out.append({"field": "safety.safe_stop", "problem": "缺少安全停止序列"})
    for i, s in enumerate(steps):
        if s.get("action") not in ("tap", "back", "resume", "wait"):
            out.append({"field": f"safety.safe_stop[{i}]", "problem": f"安全停止只允许 tap/back/resume/wait，不允许 {s.get('action')}"})
        if s.get("action") == "tap":
            t = app.targets.get(s.get("target")) if isinstance(s.get("target"), str) else None
            if t is None:
                out.append({"field": f"safety.safe_stop[{i}]", "problem": f"目标 {s.get('target')} 未登记（安全停止不能用临时目标）"})
            elif t.get("side_effect") != "safe_stop":
                out.append({"field": f"safety.safe_stop[{i}]", "problem": f"目标 {s.get('target')} 的 side_effect 不是 safe_stop"})
    cap = float(policy.supervision.get("max_motion_s", 600))
    m = safety.get("max_motion_s")
    if not isinstance(m, (int, float)) or m <= 0 or m > cap:
        out.append({"field": "safety.max_motion_s", "problem": f"须为 0 < x <= {cap} 的秒数"})
    for k in ("stopped_states", "moving_states"):
        for s in safety.get(k, []):
            if s not in app.states.states:
                out.append({"field": f"safety.{k}", "problem": f"状态 {s} 未在 states.json 声明"})
    both = set(safety.get("stopped_states", [])) & set(safety.get("moving_states", []))
    if both:
        out.append({"field": "safety.moving_states", "problem": f"{sorted(both)} 不能既是已停又是运动中"})
    if not safety.get("stopped_states"):
        out.append({"field": "safety.stopped_states", "problem": "需声明哪些状态表示已停/已暂停/已结束，用于确认安全停止生效"})
    return out


class MotionGuard:
    """运动看门狗。armed 表示"运动类控件已点击且尚未观测到停止状态"。"""

    def __init__(self, safety: Optional[dict], clock, store):
        self.safety = safety or {}
        self.clock, self.store = clock, store
        self.max_s = float(self.safety.get("max_motion_s", 0) or 0)
        self.stopped = set(self.safety.get("stopped_states", []))
        self.moving = set(self.safety.get("moving_states", []))
        self.armed: Optional[dict] = None
        self.history: list = []
        self.breaches = 0  # 超过 max_motion_s 之后才观测到停止的次数：解除计时不能抹掉超时
        self.timeout: Optional[dict] = None  # 超时锁：一旦发生就锁住，只有安全停止处理完（观测确认已停）才解开

    def locked(self) -> bool:
        """超时一旦发生（仍在运动且已超时，或超时之后才观测到停止）就锁住，Dispatcher 据此统一拒绝
        所有非安全停止的动作。看到停止状态只解除计时，不解锁：停车之后再下发动作可能把车重新启动。"""
        if self.timeout is None and self.over():
            self._lock("still_moving")
        return self.timeout is not None

    def _lock(self, how: str) -> None:
        if self.timeout is None:
            self.timeout = {"how": how, "step": self.armed and self.armed["step"], "after_s": self.elapsed(),
                            "max_s": self.max_s, "ts": now_iso()}
            self.store.event("motion_locked", **self.timeout)

    def release(self, trigger: str) -> None:
        """安全停止已被观测确认：解锁，之后的动作才能继续下发。breaches 不清零，超时仍记在已报告的步骤上。"""
        if self.timeout is not None:
            self.store.event("motion_unlocked", trigger=trigger, locked_since=self.timeout["ts"])
            self.history.append({"event": "unlocked", "trigger": trigger, "ts": now_iso()})
            self.timeout = None

    def arm(self, step_id: str, label: str, state_at_arm: Optional[str] = None) -> None:
        if self.armed is None:
            self.armed = {"step": step_id, "label": label, "t0": self.clock(), "ts": now_iso(),
                          "state_at_arm": state_at_arm, "left_arm_state": False}
            self.history.append({"event": "armed", "step": step_id, "label": label, "ts": now_iso()})
            self.store.event("motion_armed", step=step_id, label=label, max_motion_s=self.max_s)

    def elapsed(self) -> Optional[float]:
        return round(self.clock() - self.armed["t0"], 2) if self.armed else None

    def over(self) -> bool:
        return bool(self.armed and self.max_s and self.elapsed() > self.max_s)

    def on_observation(self, obs: dict) -> None:
        page = obs.get("page") or {}
        parts = set(page.get("active") or [page.get("state")])
        if not self.armed:
            seen = parts & self.moving
            if seen:
                # 没点运动按钮却观测到"运动中"（例如退后台再拉回时车自己恢复行驶）：同样开始计时，失败/超时要安全停止
                self.arm("observed", f"state:{sorted(seen)[0]}", page.get("state"))
                self.armed["left_arm_state"] = True
                self.history[-1]["observed_in"] = obs.get("obs_id")
            return
        arm_state = self.armed["state_at_arm"]
        # 点击"开始泊车"后第一帧往往还是原页面：原页面本身若也在 stopped_states 里，不能据此认为已停。
        # 只有"到了与点击时不同的停止状态"，或"离开过原页面后再出现停止状态"才解除
        if page.get("state") not in ("out_of_app", "undetermined") and arm_state not in parts and not parts & self.stopped:
            self.armed["left_arm_state"] = True
        if parts & self.stopped and (self.armed["left_arm_state"] or arm_state not in parts):
            late = self.over()
            self.history.append({"event": "stopped_observed", "state": page.get("state"), "obs": obs["obs_id"],
                                 "after_s": self.elapsed(), "ts": now_iso(), **({"late": True} if late else {})})
            self.store.event("motion_stopped_observed", state=page.get("state"), obs=obs["obs_id"], after_s=self.elapsed(),
                             late=late)
            if late:
                self.breaches += 1
                self._lock("late_stop")
            self.armed = None
        elif page.get("state") == "out_of_app":
            # 运动中 APP 退到后台/被杀：记录，不自动判错（这正是要观察的场景之一），计时继续
            self.store.event("motion_app_left", obs=obs["obs_id"], after_s=self.elapsed())

    def breached_since(self, mark: int) -> bool:
        """mark 之后是否超时：仍在运动且已超时，或超时之后才观测到停止。"""
        return self.over() or self.breaches > mark


def safe_stop(executor, trigger: str, detail: Optional[dict] = None) -> dict:
    """执行用例声明的安全停止序列。逐步尽力执行（某个按钮不在了就继续下一步），
    最后以观测状态确认是否已停；未确认时报告里要求人工立即接管。"""
    ctx = executor.ctx
    guard = ctx.motion
    rec = {"trigger": trigger, "detail": detail or {}, "ts": now_iso(), "armed_since": guard.armed and guard.armed["step"],
           "elapsed_s": guard.elapsed(), "steps": []}
    ctx.store.event("safety_stop_start", trigger=trigger, elapsed_s=rec["elapsed_s"])
    seq = list(guard.safety.get("safe_stop", []))
    if ctx.last_obs and (ctx.last_obs.get("page") or {}).get("state") == "out_of_app":
        seq = [{"action": "resume", "desc": "APP 在后台，先拉回前台再停止"}] + seq
    for i, s in enumerate(seq):
        if guard.armed is None:
            rec["steps"].append({"id": f"SAFE{i + 1}", "skipped": "已观测到停止状态"})
            continue
        r = executor.run_step({"retry": 1, "timeout_s": 6, **s, "id": f"SAFE{i + 1}", "critical": False,
                               "_safety": True}, "safety")
        rec["steps"].append({"id": r["step_id"], "action": r["action"], "outcome": r["outcome"],
                             "reason": r.get("reason"), "final_obs": r.get("final_obs")})
    if guard.armed is not None:
        obs, _ = ctx.observe("safety:confirm")
        rec["confirm_obs"] = obs["obs_id"]
    rec["confirmed_stopped"] = guard.armed is None
    if rec["confirmed_stopped"]:
        guard.release(trigger)
    else:
        rec["action_required"] = "未能通过观测确认车辆已停止：请现场监护人立即人工接管"
        if ctx.safety_hold is None:
            ctx.safety_hold = {"trigger": trigger, "ts": now_iso(), "reason": rec["action_required"]}
            ctx.store.event("safety_hold", **ctx.safety_hold)
    ctx.store.event("safety_stop_end", confirmed=rec["confirmed_stopped"])
    return rec
