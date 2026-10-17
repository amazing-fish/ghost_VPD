"""可复用的设备解锁能力；设备到 secret 名由本地配置指定。"""
from __future__ import annotations

import os
import re

from .dispatch import Dispatcher
from .devices.harmony_hdc import unlock_swipe_points
from .util import now_iso, sha256_obj, write_json
from .verdict import error_attribution


def _query_failure(device):
    error = getattr(device, "screen_query_error", None)
    return ({"execution_error": {**error, "stage": "unlock_query", "attribution": error_attribution(error.get("error_class")),
                                 "evidence": {"query": "screen_locked", "detail": error}}} if error else {})


def _command_failure(r, stage, att=None):
    if (att or {}).get("execution_error"):
        return {"execution_error": att["execution_error"]}
    if r.error_class not in {"timeout", "command_failed", "transport_unavailable", "device_offline", "tool_missing", "tool_execution_error"}:
        return {}
    return {"execution_error": {"stage": stage, "error_class": r.error_class, "error": r.error,
                                "attribution": error_attribution(r.error_class),
                                "evidence": {"sent": r.sent, "executor_ok": r.ok, "raw": r.raw}}}


def wake_unlock(device, pin, dispatcher=None, sleep=None, att=None):
    dispatcher = dispatcher or Dispatcher(device)
    if sleep is None:
        import time
        sleep = time.sleep if device.kind != "replay" else lambda _: None
    evidence, entered, last_action = [], 0, {}
    def action(name):
        nonlocal last_action
        last_action = {"action": name, "step_id": "unlock", "_safety": bool((att or {}).get("_safety"))}
        return last_action

    def result(status, **kw):
        return {"status": status, "evidence": evidence, "entered_digits": entered,
                **({"pin_entered": True} if entered else {}), **kw}

    locked = device.screen_locked()
    evidence.append({"stage": "initial", "screen_locked": locked})
    if locked is False:
        return result("unlocked", method="already")
    if locked is None:
        return result("indeterminate", reason="查询不到当前设备和屏幕状态，未继续操作", **_query_failure(device))
    r = dispatcher.send("wake", preconditions={"screen_locked": True}, att=action("wake"))
    if not r.ok:
        return result("indeterminate", error_class=r.error_class, reason=f"唤醒未完成：{r.error_class}", **_command_failure(r, "unlock_wake", last_action))
    sleep(1.0)
    locked = device.screen_locked()
    evidence.append({"stage": "awake", "screen_locked": locked})
    if locked is False:
        return result("unlocked", method="wake")
    if locked is None:
        return result("indeterminate", reason="唤醒后查询不到锁屏状态", **_query_failure(device))
    swipe = unlock_swipe_points(device.display_info())
    if swipe is None:
        return result("indeterminate", reason="查询不到显示尺寸，未上滑")
    r = dispatcher.send("swipe", *swipe, 3000, preconditions={"screen_locked": True}, att=action("swipe"))
    if not r.ok:
        return result("indeterminate", error_class=r.error_class, reason=f"上滑未完成：{r.error_class}", **_command_failure(r, "unlock_swipe", last_action))
    sleep(1.5)
    locked = device.screen_locked()
    evidence.append({"stage": "swiped", "screen_locked": locked})
    if locked is False:
        return result("unlocked", method="swipe")
    if locked is None:
        return result("indeterminate", reason="上滑后查询不到锁屏状态", **_query_failure(device))
    if not pin:
        return result("missing_secret", reason="当前设备需要 PIN，未配置对应的 secret 映射或值")
    pad = device.lock_pin_pad()
    if pad is None:
        return result("indeterminate", reason="未能识别锁屏数字键盘，未输入")
    for ch in pin:
        r = dispatcher.send("tap", *pad[ch], preconditions={"screen_locked": True},
                            att=action("pin_digit"))
        if r.sent:
            entered += 1
        if not r.ok:
            return result("indeterminate", error_class=r.error_class,
                          **_command_failure(r, "unlock_input", last_action),
                          reason=f"PIN 输入前锁屏复核或执行器失败，停止输入：{r.error_class}")
        sleep(0.25)
    sleep(1.5)
    locked = device.screen_locked()
    evidence.append({"stage": "pin_complete", "screen_locked": locked})
    if locked is False:
        return result("unlocked", method="pin")
    return result("indeterminate" if locked is None else "pin_rejected", reason="输入一次 PIN 后未确认解锁，不重试",
                  **(_query_failure(device) if locked is None else {}))


def unlock_device(ws, device, store=None, secret_name=None, dispatcher=None, att=None):
    """CLI、回归与动作恢复共用；一次失败/未知的 PIN 尝试阻止自动重入。"""
    mapping = ws.config.get("unlock_secrets") or {}
    key = secret_name or mapping.get(device.spec)
    if key is not None and not re.fullmatch(r"LOCK_PIN(_[A-Z0-9]+)?", str(key)):
        raise ValueError("PIN secret 名只接受 LOCK_PIN 或 LOCK_PIN_<名称>，传入值不回显")
    pin = (os.environ.get(f"VPD_SECRET_{key}") or (ws.config.get("secrets") or {}).get(key)) if key else None
    guard = ws.runs_dir / f"unlock-guard-{sha256_obj(device.spec)[:12]}.json"
    if store:
        store.event("capability_start", capability="unlock", device=device.spec)
    locked = device.screen_locked()
    if locked is False:
        res = {"status": "unlocked", "method": "already", "evidence": [{"screen_locked": False}]}
    elif locked is None:
        res = {"status": "indeterminate", "reason": "查询不到当前屏幕状态，未继续操作",
               "evidence": [{"screen_locked": None}], **_query_failure(device)}
    elif pin is not None and not re.fullmatch(r"[0-9]{4,16}", str(pin)):
        res = {"status": "missing_secret", "reason": "设备 PIN 配置不是 4-16 位数字，未输入", "evidence": []}
    elif guard.exists():
        res = {"status": "pin_rejected", "reason": "该设备有未确认的 PIN 尝试，停止自动重入",
               "guard": str(guard), "evidence": [{"screen_locked": locked}]}
    else:
        guarded, acquired = pin is not None, False
        if guarded:
            try:
                write_json(guard, {"ts": now_iso(), "device": device.spec, "status": "in_progress"}, exclusive=True)
                acquired = True
            except FileExistsError:
                res = {"status": "indeterminate", "reason": "该设备正在解锁或上次尝试未完成", "evidence": []}
        if not guarded or acquired:
            res = wake_unlock(device, str(pin) if pin is not None else None,
                              dispatcher or Dispatcher(device, store), att=att)
            if acquired:
                if res["status"] == "unlocked" or not res.get("pin_entered"):
                    guard.unlink()
                else:
                    write_json(guard, {"ts": now_iso(), "device": device.spec, "status": res["status"]})
    res = {"device": device.spec, **res, "action_required": res["status"] != "unlocked"}
    if store:
        store.event("capability_result", capability="unlock", **res)
    return res
