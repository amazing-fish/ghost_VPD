"""物理动作的唯一调用入口：声明前置状态、复核、下发、记录执行器结果。"""
from __future__ import annotations

from .devices import LAUNCHER, DeviceResult
from .verdict import error_attribution


class Dispatcher:
    def __init__(self, device, store=None, ctx=None):
        self.device, self.store, self.ctx = device, store, ctx

    def app_conditions(self, action):
        # 显式启动/拉回作用于指定 bundle，可在已委派任务中切换前台。
        # 触摸、输入等仍必须在目标 APP 内复核。
        if action in ("launch", "resume"):
            return {"screen_locked": False, "recover_unlock": True}
        allowed = [self.ctx.app.bundle]
        if action in ("back", "home"):
            allowed.append(LAUNCHER)
        return {"screen_locked": False, "foreground": allowed, "recover_unlock": True}

    def app_state(self, att, fresh=False):
        ok, checks = self._state(self.app_conditions(att["action"]), att)
        att["state_recheck"] = checks
        if "foreground_check" in att:
            att["foreground_check"]["after_revalidate"] = bool(fresh)
        return ok

    def _state(self, conditions, att):
        dev, checks = self.device, {}
        if "screen_locked" in conditions:
            expected, locked = conditions["screen_locked"], dev.screen_locked()
            if locked is True and expected is False and conditions.get("recover_unlock"):
                from .unlock import unlock_device
                result = unlock_device(self.ctx.ws, dev, self.store, dispatcher=self, att=att)
                att["unlock"] = checks["unlock"] = result
                if result["status"] != "unlocked":
                    if result.get("execution_error"):
                        att["execution_error"] = result["execution_error"]
                    status = result.get("error_class")
                    if status not in ("budget_exhausted", "motion_timeout", "safety_hold"):
                        status = "screen_locked" if result["status"] in ("pin_rejected", "missing_secret") else "state_indeterminate"
                    att.update(status=status, error_class=result["status"], action_required=True)
                    return False, checks
                locked = dev.screen_locked()
            checks["screen_locked"] = {"actual": locked, "expected": expected}
            if locked is not expected:
                error = getattr(dev, "screen_query_error", None)
                if locked is None and error:
                    att["execution_error"] = {**error, "stage": "dispatch_recheck",
                                              "attribution": error_attribution(error.get("error_class")),
                                              "evidence": {"query": "screen_locked", "result": locked, "detail": error}}
                att.update(status="screen_locked" if locked is True and expected is False else "state_indeterminate",
                           error_class="state_query_failed" if locked is None else "screen_locked", action_required=True,
                           state_check={"query": "screen_locked", "result": locked, "expected": expected})
                return False, checks
        if "foreground" in conditions:
            fg = dev.foreground()
            error = getattr(dev, "last_query_error", None)
            expected = conditions["foreground"]
            allowed = expected if isinstance(expected, list) else [expected]
            checks["foreground"] = {"actual": fg, "expected": expected, "query_error": error}
            att["foreground_check"] = {"foreground": fg, "expected": expected,
                                       "screen_locked": checks.get("screen_locked", {}).get("actual")}
            if fg is None:
                if error:
                    detail = getattr(dev, "last_query_error_detail", None) or {}
                    att["execution_error"] = {"stage": "dispatch_recheck", "error_class": error,
                                              "error": detail.get("error"), "attribution": error_attribution(error),
                                              "evidence": {"query": "foreground", "detail": detail}}
                att.update(status="state_indeterminate", error_class="state_query_failed" if error else "foreground_unknown", action_required=True,
                           state_check={"query": "foreground", "result": None, "detail": error})
                return False, checks
            if fg not in allowed:
                att["status"] = "foreground_changed"
                return False, checks
        return True, checks

    def send(self, op, *args, preconditions, att=None, tap=None, refresh=None, transient=None):
        """tap 提供树/定位/目标；遮挡时最多重新定位一次。PIN 不携带节点、坐标或值到事件。"""
        att = att if att is not None else {"action": op}
        checks = {}
        if not self._limits(att, checks):
            return self._reject(op, preconditions, checks, att)
        ok, state_checks = self._state(preconditions, att)
        checks.update(state_checks)
        if not ok:
            return self._reject(op, preconditions, checks, att)
        if tap is not None:
            if att.get("unlock") and refresh is not None:
                tap = refresh()
                if tap is None:
                    att.setdefault("status", "target_unresolved")
                    return self._reject(op, preconditions, checks, att)
                ok, state_checks = self._state(preconditions, att)
                checks.update(state_checks)
                if not ok:
                    return self._reject(op, preconditions, checks, att)
            for attempt in range(2):
                check = self._tap_check(tap, checks.get("foreground", {}).get("actual"), op)
                checks["tap"] = check
                checks.setdefault("tap_attempts", []).append(check)
                if check["status"] == "ok":
                    break
                if check["status"] != "target_obscured" or attempt or refresh is None:
                    att["status"] = check["status"]
                    return self._reject(op, preconditions, checks, att)
                tap = refresh()
                if tap is None:
                    att.setdefault("status", "target_unresolved")
                    return self._reject(op, preconditions, checks, att)
                # 新观测后再确认锁屏与前台，不能沿用重新定位前的状态。
                ok, state_checks = self._state(preconditions, att)
                checks.update(state_checks)
                if not ok:
                    return self._reject(op, preconditions, checks, att)
            args = (*tap["loc"]["point"], *args[2:])
            att["locate"] = tap["loc"]
            att["policy"] = check.get("policy")
        # 解锁、重新定位等嵌套操作可能已消耗预算或时间，紧邻设备调用再次复核。
        if not self._limits(att, checks):
            return self._reject(op, preconditions, checks, att)
        condition = att.get("condition")
        records = (condition.get("records") or [condition]) if condition else []
        invalid = []
        if self.ctx is not None:
            for record in records:
                age = self.ctx.clock() - record["clock_s"] if isinstance(record.get("clock_s"), (int, float)) else None
                expired = age is not None and record.get("max_age_s") is not None and age > record["max_age_s"]
                if record.get("action_epoch") != self.ctx.actions or expired:
                    invalid.append({**record, "dispatch_age_s": age, "expired": expired})
        if invalid:
            # 恢复、滚动及事实有效期在最末关口复核每个父/子条件。
            att["condition"] = {**condition, "status": "unknown", "value": None,
                                "reason": "条件已超过事实定义的有效期，须重新取证" if any(r["expired"] for r in invalid)
                                else "选支后发生可能下发的动作，条件须重新取证", "invalidated": invalid}
            att.update(status="condition_unknown", reason=att["condition"]["reason"])
            checks["condition"] = att["condition"]
            return self._reject(op, preconditions, checks, att)
        dev = self.device
        if tap is not None and op in ("tap", "input_text"):
            # Capture the final coordinates after any Dispatcher relocation; a failed/unknown
            # receipt still records the attempted point without claiming its UI effect.
            att["dispatch_coordinates"] = {"system": "px", "point": list(args[:2])}
        if transient is not None:
            if not transient.mark_trigger():
                att.update(status="transient_unavailable", reason=transient.summary.get("reason"))
                return self._reject(op, preconditions, checks, att)
        if op == "tap":
            result = dev.tap(*args)
        elif op == "swipe":
            result = dev.swipe(*args)
        elif op == "key":
            result = dev.key(*args)
        elif op == "input_text":
            result = dev.input_text(*args)
        elif op == "clear_text":
            # 聚合动作只汇总结果；内部物理动作各自计数、检查、记录，外层不重复计数。
            result = dev.clear_text(*args, dispatcher=self, preconditions=preconditions, att=att)
        elif op == "wake":
            result = dev.wake()
        elif op == "launch":
            result = dev.launch(*args)
        elif op == "stop_app":
            result = dev.stop(*args)
        else:
            raise ValueError(f"未知物理动作 {op}")
        if transient is not None:
            transient.receipt(result)
        if result.sent is not False and op != "clear_text" and self.ctx is not None:
            self.ctx.actions += 1
        if not result.ok and result.error_class in ("budget_exhausted", "motion_timeout", "safety_hold"):
            att["status"] = result.error_class
        self._record(op, preconditions, checks, result, att)
        return result

    def _limits(self, att, checks):
        if not self._hold_check(att, checks):
            return False
        ctx, exempt = self.ctx, bool(att.get("_safety"))
        checks["budget"] = {"applicable": ctx is not None, "exempt": exempt,
                            "used": ctx.actions if ctx else None,
                            "max": ctx.policy.max_actions_per_run if ctx else None}
        # 运动超时是锁而不是瞬时判断：超时被发现（含超时后才看到停止）后，非安全停止的动作一律在这里拒绝，
        # 直到安全停止处理完。各条路径不用再各自检查。
        checks["motion"] = {"applicable": ctx is not None, "exempt": exempt,
                            "over": ctx.motion.over() if ctx else False,
                            "locked": ctx.motion.locked() if ctx else False}
        if ctx is not None and not exempt:
            if checks["motion"]["locked"]:
                att.update(status="motion_timeout",
                           motion={"elapsed_s": ctx.motion.elapsed(), "max_s": ctx.motion.max_s,
                                   "locked": ctx.motion.timeout})
                return False
            if ctx.actions >= ctx.policy.max_actions_per_run:
                att["status"] = "budget_exhausted"
                return False
        return True

    def _hold_check(self, att, checks):
        hold = self.ctx.safety_hold if self.ctx is not None else None
        exempt = bool(att.get("_safety"))
        checks["safety_hold"] = {"active": hold is not None, "evidence": hold, "exempt": exempt}
        if hold is not None and not exempt:
            att.update(status="safety_hold", action_required=True)
            return False
        return True

    def held_step(self, att):
        """逻辑步骤也在定位/观察/展开前复用同一暂停判定，不执行清理副作用。"""
        checks = {}
        if not self._hold_check(att, checks):
            return self._reject(att["action"], {}, checks, att)
        return None

    def _tap_check(self, tap, foreground=None, op="tap"):
        tree, loc, target = tap["tree"], tap["loc"], tap["target"]
        if loc["status"] != "found":
            return {"status": "target_unresolved"}
        node = None
        touch = None
        if tree is not None and tree.status == "ok":
            hit = next((a["node"] for a in loc.get("attempts", []) if a.get("status") == "found" and a.get("node")), None)
            node = tree.nodes[hit["idx"]] if hit is not None else tree.node_at(*loc["point"])
            if node is None:
                return {"status": "target_unresolved"}
            touch = tree.touch_check(*loc["point"], node)
            if not touch["allowed"]:
                return {"status": "target_obscured", "blocker": touch["receiver"],
                        "expected": touch["scope"], "touch": touch}
        policy = self.ctx.policy.check_tap(tree, loc["point"], loc["basis"], target,
                                           tap.get("state_deny"), self.ctx.authorized, node=node,
                                           confirmation_sources=self.ctx.app.states.confirmation_sources(
                                               tree, tap.get("target_ref"), target, tap.get("page"), tap.get("layout")),
                                           layout=tap.get("layout") if op == "tap" else None, foreground=foreground)
        return {"status": "ok" if policy["allowed"] else "policy_denied", "policy": policy, "touch": touch}

    def _reject(self, op, preconditions, checks, att):
        result = DeviceResult(op, False, False, att["status"], "前置状态复核未通过，未下发")
        self._record(op, preconditions, checks, result, att)
        return result

    def _record(self, op, preconditions, checks, result, att):
        data = {"op": op, "action": att["action"], "preconditions": preconditions, "checks": checks, "sent": result.sent,
                "aggregate": op == "clear_text",
                "executor_ok": result.ok, "error_class": result.error_class, "error": result.error,
                "duration_ms": result.duration_ms}
        if not result.ok:
            data["raw"] = result.raw
        if att.get("dispatch_coordinates"):
            data["coordinates"] = att["dispatch_coordinates"]
        att["dispatch_recheck"] = {"preconditions": preconditions, "checks": checks}
        if self.store is not None:
            self.store.event("dispatch", step=att.get("step_id"), **data)


def auxiliary_dispatches(att):
    """定位滚动与弹窗处置也是物理动作；缺失的父dispatch不代表这些子动作未发。"""
    return [item for item in ((att.get("locate") or {}).get("scroll") or []) +
            (att.get("dismissed") or []) + ((att.get("post") or {}).get("dismissed") or []) +
            (att.get("recovery_dispatches") or []) if "sent" in item]


def dispatch_effect_unknown(att):
    """物理执行未知与业务后置未达分开；完整clear+input的可靠回执不属于部分执行。"""
    if any(item["sent"] is not False and (item["sent"] is not True or item.get("ok") is not True
                                          or item.get("verified") is False)
           for item in auxiliary_dispatches(att)):
        return True
    dispatch = att.get("dispatch") or {}
    if dispatch and dispatch.get("sent") is not False and (
            dispatch.get("sent") is not True or dispatch.get("executor_ok") is not True):
        return True
    clear = att.get("clear") or {}
    return bool(clear and clear.get("sent") is not False and not (
        clear.get("sent") is True and clear.get("ok") is True and dispatch.get("op") == "input_text"
        and dispatch.get("sent") is True and dispatch.get("executor_ok") is True))


def clear_text(device, x, y, count, dispatcher, preconditions, att=None):
    """每个按键都经过关口；中途拒绝仍保留已经下发的事实。"""
    sent = False
    for op, args in [("tap", (x, y)), ("key", ("move_end",))] + [("key", ("delete",))] * max(0, int(count)):
        sub_att = {"action": "clear_text", "step_id": (att or {}).get("step_id"),
                   "_safety": bool((att or {}).get("_safety"))}
        result = dispatcher.send(op, *args, preconditions=preconditions, att=sub_att)
        if sent is True or result.sent is True:
            sent = True
        elif result.sent is not False:
            sent = None  # 后续未发不能抹掉前一步未知发送事实。
        if not result.ok:
            if att is not None and sub_att.get("execution_error"):
                att["execution_error"] = sub_att["execution_error"]
            return DeviceResult("clear_text", sent, False, result.error_class,
                                ("MoveEnd 失败：" if args == ("move_end",) else "") + (result.error or "前置状态失败"), raw=result.raw)
    return DeviceResult("clear_text", sent, True)
