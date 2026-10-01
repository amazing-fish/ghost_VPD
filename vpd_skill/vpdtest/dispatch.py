"""物理动作的唯一调用入口：声明前置状态、复核、下发、记录执行器结果。"""
from __future__ import annotations

from .devices import DeviceResult

LAUNCHER = "com.ohos.sceneboard"


class Dispatcher:
    def __init__(self, device, store=None, ctx=None):
        self.device, self.store, self.ctx = device, store, ctx

    def app_conditions(self, action):
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
                result = unlock_device(self.ctx.ws, dev, self.store)
                att["unlock"] = checks["unlock"] = result
                if result["status"] != "unlocked":
                    att.update(status="screen_locked" if result["status"] in ("pin_rejected", "missing_secret")
                               else "state_indeterminate", error_class=result["status"], action_required=True)
                    return False, checks
                locked = dev.screen_locked()
            checks["screen_locked"] = {"actual": locked, "expected": expected}
            if locked is not expected:
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
                att.update(status="state_indeterminate", error_class="state_query_failed" if error else "foreground_unknown", action_required=True,
                           state_check={"query": "foreground", "result": None, "detail": error})
                return False, checks
            if fg not in allowed:
                att["status"] = "foreground_changed"
                return False, checks
        return True, checks

    def send(self, op, *args, preconditions, att=None, tap=None, refresh=None):
        """tap 提供树/定位/目标；遮挡时最多重新定位一次。PIN 不携带节点、坐标或值到事件。"""
        att = att if att is not None else {"action": op}
        ok, checks = self._state(preconditions, att)
        if not ok:
            return self._reject(op, preconditions, checks, att)
        if tap is not None:
            if att.get("unlock") and refresh is not None:
                tap = refresh()
                if tap is None:
                    att["status"] = "target_unresolved"
                    return self._reject(op, preconditions, checks, att)
                ok, state_checks = self._state(preconditions, att)
                checks.update(state_checks)
                if not ok:
                    return self._reject(op, preconditions, checks, att)
            for attempt in range(2):
                check = self._tap_check(tap)
                checks["tap"] = check
                checks.setdefault("tap_attempts", []).append(check)
                if check["status"] == "ok":
                    break
                if check["status"] != "target_obscured" or attempt or refresh is None:
                    att["status"] = check["status"]
                    return self._reject(op, preconditions, checks, att)
                tap = refresh()
                if tap is None:
                    att["status"] = "target_unresolved"
                    return self._reject(op, preconditions, checks, att)
                # 新观测后再确认锁屏与前台，不能沿用重新定位前的状态。
                ok, state_checks = self._state(preconditions, att)
                checks.update(state_checks)
                if not ok:
                    return self._reject(op, preconditions, checks, att)
            args = (*tap["loc"]["point"], *args[2:])
            att["locate"] = tap["loc"]
            att["policy"] = check.get("policy")
        dev = self.device
        if op == "tap":
            result = dev.tap(*args)
        elif op == "swipe":
            result = dev.swipe(*args)
        elif op == "key":
            result = dev.key(*args)
        elif op == "input_text":
            result = dev.input_text(*args)
        elif op == "clear_text":
            result = dev.clear_text(*args, dispatcher=self, preconditions=preconditions)
        elif op == "wake":
            result = dev.wake()
        else:
            raise ValueError(f"未知物理动作 {op}")
        self._record(op, preconditions, checks, result, att)
        return result

    def _tap_check(self, tap):
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
                                           tap.get("state_deny"), self.ctx.authorized, node=node)
        return {"status": "ok" if policy["allowed"] else "policy_denied", "policy": policy, "touch": touch}

    def _reject(self, op, preconditions, checks, att):
        result = DeviceResult(op, False, False, att["status"], "前置状态复核未通过，未下发")
        self._record(op, preconditions, checks, result, att)
        return result

    def _record(self, op, preconditions, checks, result, att):
        data = {"op": op, "action": att["action"], "preconditions": preconditions, "checks": checks, "sent": result.sent,
                "executor_ok": result.ok, "error_class": result.error_class, "error": result.error,
                "duration_ms": result.duration_ms}
        att["dispatch_recheck"] = {"preconditions": preconditions, "checks": checks}
        if self.store is not None:
            self.store.event("dispatch", step=att.get("step_id"), **data)


def clear_text(device, x, y, count, dispatcher, preconditions):
    """每个按键都经过关口；中途拒绝仍保留已经下发的事实。"""
    sent = False
    for op, args in [("tap", (x, y)), ("key", ("move_end",))] + [("key", ("delete",))] * max(0, int(count)):
        result = dispatcher.send(op, *args, preconditions=preconditions)
        sent = sent or result.sent
        if not result.ok:
            return DeviceResult("clear_text", sent, False, result.error_class,
                                ("MoveEnd 失败：" if args == ("move_end",) else "") + (result.error or "前置状态失败"))
    return DeviceResult("clear_text", sent, True)
