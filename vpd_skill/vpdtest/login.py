"""可复用的快速原子登录能力（当前支持 qiankun_hm）。

将多步手动 act 登录收敛为单次受控命令（理解界面后目标 <=10s 完成）：
- 所有物理动作经 Dispatcher 下发，保留锁屏/前台复核、遮挡校验、policy 门禁与事件审计；
- 凭据仅接受环境变量/本地配置键名（默认 QIANKUN_PHONE / QIANKUN_PASSWORD），严禁明文入参或落盘；
- 中间表单流转跳过截图（screenshot=False）以压缩 HDC I/O，仅在终态或人工接管时采证；
- 遇密码保险箱「手动输入」优先关闭再输入（避免清空已填账号），遇 SelectOverlay/自动填充遮挡自动 back 一次重试；
- 命中短信验证码、安全码或状态不确定时立即原地停下返回 action_required=True，绝不 stop_app 清现场。
"""
from __future__ import annotations

import math
import os
import re
import time
from typing import Optional

from .experience import ExperienceStore
from .locate import resolve
from .observe import RunStore
from .outcomes import error_attribution
from .unlock import unlock_device
from .workspace import RunContext

_SECRET_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DEFAULT_ACCOUNT_SECRET = "QIANKUN_PHONE"
_DEFAULT_PASSWORD_SECRET = "QIANKUN_PASSWORD"
_LOGGED_IN_STATES = frozenset({"qk_vpd_home", "qk_car_tab", "qk_discover_tab"})
_LOGIN_BLOCK_TEXTS = frozenset({"欢迎登录华为乾崑", "本机号码一键登录", "其他登录方式", "登录/注册", "密码登录"})


def _validate_secret_key(name: Optional[str], default: str, flag: str) -> str:
    key = default if name is None else str(name)
    if not _SECRET_KEY_RE.fullmatch(key):
        raise ValueError(f"{flag} 只接受环境变量/secrets 键名（大写字母、数字、下划线，如 {default}），不接受明文凭据")
    return key


def _resolve_secret(ws, key: str) -> Optional[str]:
    val = os.environ.get(f"VPD_SECRET_{key}")
    if val is None:
        val = (ws.config.get("secrets") or {}).get(key)
    if val is None:
        return None
    s = str(val)
    return s if s else None


def _visible_texts(tree) -> set[str]:
    if tree is None or tree.status != "ok":
        return set()
    out = set()
    for n in tree.nodes:
        t = (n.text or "").strip()
        if t and tree.visible_rect(n) and tree.visible_lineage(n):
            out.add(t)
    return out


def _resolve_target(app, target_ref, tree, obs):
    tid, target = app.target(target_ref)
    loc = resolve(tid, target, tree, None, app.dir, layout=obs.get("layout"))
    return tid, target, loc


def _hit_node(tree, loc):
    if tree is None or tree.status != "ok" or loc.get("status") != "found":
        return None
    hit = next((a.get("node") for a in loc.get("attempts", []) if a.get("status") == "found" and a.get("node")), None)
    if hit is not None and 0 <= hit["idx"] < len(tree.nodes):
        return tree.nodes[hit["idx"]]
    return tree.node_at(*loc["point"]) if loc.get("point") else None


def _has_select_overlay(app, tree, obs) -> bool:
    if tree is None or tree.status != "ok":
        return False
    if "qk_login_system_selection_here" in app.targets:
        _, _, loc = _resolve_target(app, "qk_login_system_selection_here", tree, obs)
        if loc.get("status") == "found":
            return True
    return any(n.type == "SelectOverlay" and tree.visible_rect(n) and tree.visible_lineage(n) for n in tree.nodes)


def _classify_screen(app, obs, tree) -> tuple[str, Optional[dict]]:
    if obs.get("foreground") != app.bundle:
        return "out_of_app", None
    if tree is None or tree.status != "ok":
        return "unobservable", None
    texts = _visible_texts(tree)
    if "请输入安全码" in texts:
        return "safe_code", None
    if "身份验证" in texts or "输入验证码" in texts or any(t.startswith("验证码已发至") for t in texts):
        return "sms_verify", None
    for intr in (obs.get("page") or {}).get("interrupts") or []:
        spec = app.states.interrupts.get(intr) or {}
        if spec.get("class") == "benign" and spec.get("review") == "reviewed" and spec.get("dismiss_text"):
            return "benign_dialog", {"name": intr, "dismiss_text": spec["dismiss_text"]}
    _, _, pwd_loc = _resolve_target(app, "qk_pwd_input", tree, obs)
    if pwd_loc.get("status") == "found":
        return "password_form", None
    if "密码登录" in texts:
        return "method_select", None
    if "其他登录方式" in texts:
        return "one_click", None
    if "登录/注册" in texts:
        return "logged_out_my", None
    state = (obs.get("page") or {}).get("state")
    if state in _LOGGED_IN_STATES and not (texts & _LOGIN_BLOCK_TEXTS):
        return "logged_in", None
    if ("我的车辆" in texts or {"爱车", "ADS", "我的"} <= texts) and not (texts & _LOGIN_BLOCK_TEXTS):
        return "logged_in", None
    return "unknown", None


def login_app(ws, app, device, *, authorize=None, account_secret: Optional[str] = None,
              password_secret: Optional[str] = None, timeout_s: float = 15.0,
              store: Optional[RunStore] = None, sleep=None) -> dict:
    """原子密码登录能力（默认 qiankun_hm）。返回结构化结果，不含明文凭据。"""
    t0 = time.monotonic()
    acc_key = _validate_secret_key(account_secret, _DEFAULT_ACCOUNT_SECRET, "--account-secret")
    pwd_key = _validate_secret_key(password_secret, _DEFAULT_PASSWORD_SECRET, "--password-secret")
    if app.id != "qiankun_hm":
        raise ValueError(f"当前原子登录能力仅支持 qiankun_hm，收到 {app.id}")
    if (not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool)
            or not math.isfinite(timeout_s) or timeout_s <= 0 or timeout_s > 60):
        raise ValueError("timeout_s 须在 (0, 60] 秒内")

    granted = set(authorize or [])
    if "account_state" not in granted:
        return {
            "app": app.id,
            "device": device.spec,
            "status": "authorization_required",
            "reason": "登录属于 account_state 副作用，须显式传入 --authorize account_state",
            "action_required": False,
            "actions": 0,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "steps": [],
        }

    if sleep is None:
        sleep = time.sleep if device.kind != "replay" else (lambda _: None)

    store = store or RunStore(ws.runs_dir, kind="login")
    store.event("capability_start", capability="login", app=app.id, device=device.spec,
                account_secret_key=acc_key, password_secret_key=pwd_key)

    ctx = RunContext(ws, app, device, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    ctx.policy.check_grants(granted)
    ctx.authorized = ctx.policy.effective_grants(granted)
    dispatcher = ctx.dispatcher
    observer = ctx.observer
    steps: list[dict] = []
    last_obs_epoch = ctx.actions

    def _needs_human(rec: dict) -> bool:
        return bool(rec.get("action_required")) or rec.get("status") == "state_indeterminate"

    def finish(status: str, *, method: Optional[str] = None, reason: Optional[str] = None,
               action_required: bool = False, final_shot: bool = False, extra: Optional[dict] = None) -> dict:
        final_obs = None
        if final_shot:
            obs_f, _ = observer.observe(f"login_{status}", screenshot=True)
            final_obs = obs_f["obs_id"]
        elapsed = round(time.monotonic() - t0, 2)
        out = {
            "run": store.run_id,
            "app": app.id,
            "device": device.spec,
            "status": status,
            **({"method": method} if method else {}),
            **({"reason": reason} if reason else {}),
            "action_required": action_required,
            "actions": ctx.actions,
            "elapsed_s": elapsed,
            **({"final_obs": final_obs} if final_obs else {}),
            "steps": steps,
            **(extra or {}),
        }
        store.event("capability_end", capability="login", status=status, method=method,
                    actions=ctx.actions, elapsed_s=elapsed, action_required=action_required)
        return out

    def observe_fast(label: str):
        nonlocal last_obs_epoch
        obs_out, tree_out = observer.observe(label, screenshot=False)
        last_obs_epoch = ctx.actions
        return obs_out, tree_out

    def send_tap(step_name: str, target_ref, tree, obs, *, allow_fallback_text: Optional[str] = None) -> tuple[bool, dict]:
        tid, target, loc = _resolve_target(app, target_ref, tree, obs)
        if loc.get("status") != "found" and allow_fallback_text:
            fb = {"label": f"login:{step_name}", "side_effect": "account_state",
                  "strategies": [{"kind": "text", "value": allow_fallback_text}]}
            tid, target, loc = _resolve_target(app, fb, tree, obs)
        if loc.get("status") != "found":
            rec = {"step": step_name, "target": tid, "status": loc.get("status"), "action_required": False}
            steps.append(rec)
            return False, rec
        att = {"action": "tap", "step_id": f"login_{step_name}",
               "dispatch_coordinates": {"system": "px", "point": loc["point"]}}
        tap = {"tree": tree, "loc": loc, "target": target, "target_ref": target_ref if isinstance(target_ref, str) else None,
               "page": obs.get("page"), "layout": obs.get("layout")}
        res = dispatcher.send("tap", *loc["point"], preconditions=dispatcher.app_conditions("tap"), att=att, tap=tap)
        rec = {"step": step_name, "target": tid, "status": "ok" if res.ok else (att.get("status") or res.error_class or "failed"),
               "sent": res.sent, "action_required": bool(att.get("action_required"))}
        if att.get("execution_error"):
            rec["execution_error"] = att["execution_error"]
        steps.append(rec)
        return bool(res.ok), rec

    def send_back(step_name: str) -> tuple[bool, dict]:
        att = {"action": "back", "step_id": f"login_{step_name}"}
        res = dispatcher.send("key", "back", preconditions=dispatcher.app_conditions("back"), att=att)
        rec = {"step": step_name, "action": "back",
               "status": "ok" if res.ok else (att.get("status") or res.error_class or "failed"),
               "sent": res.sent, "action_required": bool(att.get("action_required"))}
        if att.get("execution_error"):
            rec["execution_error"] = att["execution_error"]
        steps.append(rec)
        return bool(res.ok), rec

    def send_secret_input(step_name: str, target_ref: str, secret_val: str, tree, obs) -> tuple[bool, dict, dict, object]:
        """输入敏感凭据（绝不记录原文）；若遇 SelectOverlay 遮挡则自动 back 一次并重试一次。"""
        for attempt in range(2):
            tid, target, loc = _resolve_target(app, target_ref, tree, obs)
            if loc.get("status") != "found":
                rec = {"step": step_name, "target": tid, "status": loc.get("status"),
                       "attempt": attempt + 1, "action_required": False}
                steps.append(rec)
                return False, rec, obs, tree
            node = _hit_node(tree, loc)
            hint = (node.attrs.get("hint") or "") if node else ""
            existing = (node.text or "") if node else ""
            if existing and existing != hint:
                c_att = {"action": "input", "step_id": f"login_{step_name}_clear"}
                c_tap = {"tree": tree, "loc": loc, "target": target, "target_ref": target_ref,
                         "page": obs.get("page"), "layout": obs.get("layout"),
                         "observed_action_epoch": last_obs_epoch}
                c_res = dispatcher.send("clear_text", *loc["point"], len(existing) + 4,
                                        preconditions=dispatcher.app_conditions("input"), att=c_att, tap=c_tap)
                if not c_res.ok:
                    status = c_att.get("status") or c_res.error_class or "clear_failed"
                    if status == "target_obscured" and attempt == 0:
                        send_back(f"{step_name}_dismiss_overlay")
                        sleep(0.25)
                        obs, tree = observe_fast(f"login_{step_name}_after_back")
                        continue
                    rec = {"step": step_name, "target": tid, "status": status, "stage": "clear",
                           "attempt": attempt + 1, "action_required": bool(c_att.get("action_required"))}
                    if c_att.get("execution_error"):
                        rec["execution_error"] = c_att["execution_error"]
                    steps.append(rec)
                    return False, rec, obs, tree
                sleep(0.15)
                obs, tree = observe_fast(f"login_{step_name}_after_clear")
                tid, target, loc = _resolve_target(app, target_ref, tree, obs)
                if loc.get("status") != "found":
                    rec = {"step": step_name, "target": tid, "status": loc.get("status"),
                           "attempt": attempt + 1, "action_required": False}
                    steps.append(rec)
                    return False, rec, obs, tree

            att = {"action": "input", "step_id": f"login_{step_name}",
                   "dispatch_coordinates": {"system": "px", "point": loc["point"]}}
            tap = {"tree": tree, "loc": loc, "target": target, "target_ref": target_ref,
                   "page": obs.get("page"), "layout": obs.get("layout")}
            res = dispatcher.send("input_text", *loc["point"], secret_val,
                                  preconditions=dispatcher.app_conditions("input"), att=att, tap=tap)
            if res.ok:
                rec = {"step": step_name, "target": tid, "status": "ok", "redacted": True,
                       "attempt": attempt + 1, "action_required": False}
                steps.append(rec)
                return True, rec, obs, tree
            status = att.get("status") or res.error_class or "failed"
            if status == "target_obscured" and attempt == 0:
                send_back(f"{step_name}_dismiss_overlay")
                sleep(0.25)
                obs, tree = observe_fast(f"login_{step_name}_retry")
                continue
            rec = {"step": step_name, "target": tid, "status": status, "attempt": attempt + 1,
                   "action_required": bool(att.get("action_required"))}
            if att.get("execution_error"):
                rec["execution_error"] = att["execution_error"]
            steps.append(rec)
            return False, rec, obs, tree
        return False, {"step": step_name, "status": "failed", "action_required": False}, obs, tree

    # 1. 自动解锁（已解锁时零开销跳过）
    locked = device.screen_locked()
    if locked is None:
        err = getattr(device, "screen_query_error", None)
        extra = ({"execution_error": {**err, "stage": "login_unlock_query",
                                      "attribution": error_attribution(err.get("error_class"))}}
                 if err else None)
        return finish("indeterminate", reason="查询不到锁屏状态，未下发登录动作", action_required=True, extra=extra)
    if locked is True:
        ul = unlock_device(ws, device, store, dispatcher=dispatcher)
        steps.append({"step": "unlock", "status": ul["status"]})
        if ul["status"] != "unlocked":
            return finish("indeterminate" if ul["status"] != "pin_rejected" else "screen_locked",
                          reason=ul.get("reason", "自动解锁未完成"), action_required=True)

    # 2. 初始观察；不在前台时拉起被测 APP
    obs, tree = observe_fast("login_initial")
    kind, detail = _classify_screen(app, obs, tree)
    if kind == "out_of_app":
        att = {"action": "launch", "step_id": "login_launch"}
        lres = dispatcher.send("launch", app.bundle, app.ability,
                               preconditions=dispatcher.app_conditions("launch"), att=att)
        steps.append({"step": "launch", "status": "ok" if lres.ok else (att.get("status") or lres.error_class)})
        if not lres.ok:
            return finish("indeterminate", reason="拉起被测 APP 失败", action_required=True)
        deadline = time.monotonic() + min(8.0, timeout_s)
        while True:
            sleep(0.4)
            obs, tree = observe_fast("login_after_launch")
            kind, detail = _classify_screen(app, obs, tree)
            if kind not in ("out_of_app", "unobservable", "unknown") or time.monotonic() >= deadline:
                break

    if kind == "logged_in":
        return finish("logged_in", method="already", final_shot=False)
    if kind == "safe_code":
        return finish("safe_code_required", reason="命中安全码输入页，已原地保留现场等待人工处理",
                      action_required=True, final_shot=True)
    if kind == "sms_verify":
        return finish("sms_required", reason="命中短信验证码页，已原地保留现场等待人工处理",
                      action_required=True, final_shot=True)

    # 3. 读取外置凭据（仅在确实需要登录时强制校验）
    acc_val = _resolve_secret(ws, acc_key)
    pwd_val = _resolve_secret(ws, pwd_key)
    missing = [k for k, v in ((acc_key, acc_val), (pwd_key, pwd_val)) if not v]
    if missing:
        return finish("missing_secret", reason=f"未配置登录外置凭据键：{', '.join(missing)}",
                      action_required=True, extra={"missing_secrets": missing})
    if any(ch.isspace() for ch in acc_val) or any(ch.isspace() for ch in pwd_val):
        return finish("invalid_secret", reason="登录凭据包含空白字符，真机 input 不支持空白", action_required=False)

    # 4. 快速导航到密码表单页（最多 5 跳，处理登录/注册 -> 其他登录方式 -> 密码登录 与良性弹窗）
    for nav_i in range(5):
        if kind == "password_form":
            break
        if kind == "logged_in":
            return finish("logged_in", method="already", final_shot=True)
        if kind == "safe_code":
            return finish("safe_code_required", reason="命中安全码输入页，已原地保留现场",
                          action_required=True, final_shot=True)
        if kind == "sms_verify":
            return finish("sms_required", reason="命中短信验证码页，已原地保留现场",
                          action_required=True, final_shot=True)
        if kind == "benign_dialog" and detail:
            fb = {"label": f"dismiss:{detail['name']}", "side_effect": "dismiss_dialog",
                  "strategies": [{"kind": "text", "value": detail["dismiss_text"]}]}
            ok, rec = send_tap(f"dismiss_{detail['name']}", fb, tree, obs)
            if not ok:
                return finish("blocked", reason=f"良性弹窗 {detail['name']} 关闭失败：{rec['status']}",
                              action_required=_needs_human(rec), final_shot=True)
        elif kind == "logged_out_my":
            ok, rec = send_tap("tap_login_register", "qk_login_register", tree, obs)
            if not ok:
                return finish("blocked", reason=f"点击登录/注册失败：{rec['status']}", action_required=_needs_human(rec))
        elif kind == "one_click":
            ok, rec = send_tap("tap_other_method", "qk_login_other_method", tree, obs, allow_fallback_text="其他登录方式")
            if not ok:
                return finish("blocked", reason=f"点击其他登录方式失败：{rec['status']}", action_required=_needs_human(rec))
        elif kind == "method_select":
            ok, rec = send_tap("tap_pwd_login", "qk_pwd_login", tree, obs, allow_fallback_text="密码登录")
            if not ok:
                return finish("blocked", reason=f"点击密码登录失败：{rec['status']}", action_required=_needs_human(rec))
        else:
            return finish("blocked", reason=f"当前页面无法识别为登录入口或密码表单（{kind}）",
                          action_required=False, final_shot=True)
        sleep(0.35)
        obs, tree = observe_fast(f"login_nav_{nav_i + 1}")
        kind, detail = _classify_screen(app, obs, tree)

    if kind != "password_form":
        if kind == "logged_in":
            return finish("logged_in", method="already", final_shot=True)
        return finish("blocked", reason=f"未能进入密码登录表单（当前：{kind}）", action_required=False, final_shot=True)

    # 5. 账号输入：先点输入框 -> 遇密码保险箱先点「手动输入」（防清空账号）-> 遇 SelectOverlay back -> 输入账号
    ok, rec = send_tap("focus_phone", "qk_number_input", tree, obs)
    if not ok:
        return finish("blocked", reason=f"聚焦手机号输入框失败：{rec['status']}",
                      action_required=_needs_human(rec), final_shot=True)
    sleep(0.25)
    obs, tree = observe_fast("login_after_focus_phone")
    if "手动输入" in _visible_texts(tree):
        ok, rec = send_tap("phone_manual_btn", "qk_manual_btn", tree, obs)
        if not ok:
            return finish("blocked", reason=f"关闭密码保险箱「手动输入」失败：{rec['status']}",
                          action_required=_needs_human(rec), final_shot=True)
        sleep(0.25)
        obs, tree = observe_fast("login_after_phone_manual")
    if _has_select_overlay(app, tree, obs):
        send_back("phone_dismiss_select_overlay")
        sleep(0.25)
        obs, tree = observe_fast("login_after_phone_overlay")
    ok, rec, obs, tree = send_secret_input("input_phone", "qk_number_input", acc_val, tree, obs)
    if not ok:
        return finish("blocked", reason=f"输入手机号失败：{rec['status']}",
                      action_required=_needs_human(rec), final_shot=True)

    # 6. 密码输入：聚焦 -> 处理密码保险箱/SelectOverlay -> 输入密码
    sleep(0.15)
    obs, tree = observe_fast("login_before_pwd")
    ok, rec = send_tap("focus_pwd", "qk_pwd_input", tree, obs)
    if not ok:
        return finish("blocked", reason=f"聚焦密码输入框失败：{rec['status']}",
                      action_required=_needs_human(rec), final_shot=True)
    sleep(0.25)
    obs, tree = observe_fast("login_after_focus_pwd")
    if "手动输入" in _visible_texts(tree):
        ok, rec = send_tap("pwd_manual_btn", "qk_manual_btn", tree, obs)
        if not ok:
            return finish("blocked", reason=f"关闭密码区「手动输入」失败：{rec['status']}",
                          action_required=_needs_human(rec), final_shot=True)
        sleep(0.25)
        obs, tree = observe_fast("login_after_pwd_manual")
    if _has_select_overlay(app, tree, obs):
        send_back("pwd_dismiss_select_overlay")
        sleep(0.25)
        obs, tree = observe_fast("login_after_pwd_overlay")
    ok, rec, obs, tree = send_secret_input("input_pwd", "qk_pwd_input", pwd_val, tree, obs)
    if not ok:
        return finish("blocked", reason=f"输入密码失败：{rec['status']}",
                      action_required=_needs_human(rec), final_shot=True)

    # 7. 收起键盘 -> 勾选未勾选的用户协议 -> 点击登录
    send_back("dismiss_ime")
    sleep(0.25)
    obs, tree = observe_fast("login_before_submit")
    _, _, proto_loc = _resolve_target(app, "qk_protocol_unchecked", tree, obs)
    if proto_loc.get("status") == "found":
        ok, rec = send_tap("check_protocol", "qk_protocol_unchecked", tree, obs)
        if not ok:
            return finish("blocked", reason=f"勾选用户协议失败：{rec['status']}",
                          action_required=_needs_human(rec), final_shot=True)
        sleep(0.15)
        obs, tree = observe_fast("login_after_protocol")
    ok, rec = send_tap("submit_login", "qk_login_btn", tree, obs)
    if not ok:
        return finish("blocked", reason=f"点击登录按钮失败：{rec['status']}",
                      action_required=_needs_human(rec), final_shot=True)

    # 8. 快速轮询登录结果（不固定长 sleep，命中即返回）
    deadline = time.monotonic() + timeout_s
    poll_n = 0
    while True:
        sleep(0.45)
        poll_n += 1
        obs, tree = observe_fast(f"login_poll_{poll_n}")
        kind, detail = _classify_screen(app, obs, tree)
        if kind == "logged_in":
            return finish("logged_in", method="password", final_shot=True)
        if kind == "sms_verify":
            return finish("sms_required", reason="提交密码后命中短信验证码挑战，已原地保留现场等待人工接管",
                          action_required=True, final_shot=True)
        if kind == "safe_code":
            return finish("safe_code_required", reason="提交密码后命中安全码提示，已原地保留现场等待人工接管",
                          action_required=True, final_shot=True)
        if kind == "benign_dialog" and detail:
            fb = {"label": f"dismiss:{detail['name']}", "side_effect": "dismiss_dialog",
                  "strategies": [{"kind": "text", "value": detail["dismiss_text"]}]}
            ok, rec = send_tap(f"post_dismiss_{detail['name']}", fb, tree, obs)
            if not ok and (_needs_human(rec) or rec.get("status") in ("budget_exhausted", "motion_timeout", "safety_hold")):
                return finish("blocked", reason=f"提交登录后关闭良性弹窗 {detail['name']} 失败：{rec['status']}",
                              action_required=True, final_shot=True)
        if time.monotonic() >= deadline or (device.kind == "replay" and poll_n >= 3):
            return finish("timeout", reason=f"提交登录后 {timeout_s}s 内未确认进入已登录页（当前：{kind}）",
                          action_required=True, final_shot=True)
