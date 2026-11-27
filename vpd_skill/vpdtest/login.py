"""可复用的快速原子登录能力（当前支持 qiankun_hm）。

将多步手动 act 登录收敛为单次受控命令（理解界面后目标 <=10s 完成）：
- 所有物理动作经 Dispatcher 下发，保留锁屏/前台复核、遮挡校验、policy 门禁与事件审计；
- 凭据仅接受环境变量/本地配置键名（默认 QIANKUN_PHONE / QIANKUN_PASSWORD），严禁明文入参或落盘；
- 中间表单流转跳过截图（screenshot=False）以压缩 HDC I/O，仅在终态或人工接管时采证；
- 遇密码保险箱「手动输入」优先关闭再输入（避免清空已填账号），遇 SelectOverlay/自动填充遮挡自动 back 一次重试；
- 命中短信验证码、安全码或状态不确定时立即原地停下返回 action_required=True，绝不 stop_app 清现场。
"""
from __future__ import annotations

import time
from typing import Optional

from .capability_runtime import (
    CapabilityRuntime,
    classify_qiankun_screen,
    hit_node,
    needs_human,
    resolve_secret,
    resolve_target,
    validate_secret_key,
    validate_timeout,
    visible_texts,
)
from .observe import RunStore
from .workspace import RunContext

_DEFAULT_ACCOUNT_SECRET = "QIANKUN_PHONE"
_DEFAULT_PASSWORD_SECRET = "QIANKUN_PASSWORD"

_visible_texts = visible_texts
_resolve_target = resolve_target
_hit_node = hit_node
_classify_screen = classify_qiankun_screen


def _has_select_overlay(app, tree, obs) -> bool:
    if tree is None or tree.status != "ok":
        return False
    if "qk_login_system_selection_here" in app.targets:
        _, _, loc = _resolve_target(app, "qk_login_system_selection_here", tree, obs)
        if loc.get("status") == "found":
            return True
    return any(n.type == "SelectOverlay" and tree.visible_rect(n) and tree.visible_lineage(n) for n in tree.nodes)


def login_app(ws, app, device, *, authorize=None, account_secret: Optional[str] = None,
              password_secret: Optional[str] = None, timeout_s: float = 15.0,
              store: Optional[RunStore] = None, sleep=None, _ctx: Optional[RunContext] = None) -> dict:
    """原子密码登录能力（默认 qiankun_hm）。返回结构化结果，不含明文凭据。"""
    t0 = time.monotonic()
    acc_key = validate_secret_key(account_secret, _DEFAULT_ACCOUNT_SECRET, "--account-secret")
    pwd_key = validate_secret_key(password_secret, _DEFAULT_PASSWORD_SECRET, "--password-secret")
    if app.id != "qiankun_hm":
        raise ValueError(f"当前原子登录能力仅支持 qiankun_hm，收到 {app.id}")
    timeout_s = validate_timeout(timeout_s, max_s=60.0)

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

    rt = CapabilityRuntime.open(
        ws, app, device,
        kind="login", capability="login", prefix="login", granted=granted,
        store=store, sleep=sleep, _ctx=_ctx,
        extra_start={"account_secret_key": acc_key, "password_secret_key": pwd_key},
        classify=lambda a, o, t: _classify_screen(a, o, t),
        t0=t0,
    )

    def finish(status: str, *, method: Optional[str] = None, reason: Optional[str] = None,
               action_required: bool = False, final_shot: bool = False, extra: Optional[dict] = None) -> dict:
        fields = {"method": method} if method else None
        return rt.finish(
            status, reason=reason, action_required=action_required,
            final_shot=final_shot, fields=fields, event_fields={"method": method}, extra=extra,
        )

    def send_secret_input(step_name: str, target_ref: str, secret_val: str, tree, obs) -> tuple[bool, dict, dict, object]:
        """输入敏感凭据（绝不记录原文）；若遇 SelectOverlay 遮挡则自动 back 一次并重试一次。"""
        for attempt in range(2):
            tid, target, loc = _resolve_target(app, target_ref, tree, obs)
            if loc.get("status") != "found":
                rec = {"step": step_name, "target": tid, "status": loc.get("status"),
                       "attempt": attempt + 1, "action_required": False}
                rt.steps.append(rec)
                return False, rec, obs, tree
            node = _hit_node(tree, loc)
            hint = (node.attrs.get("hint") or "") if node else ""
            existing = (node.text or "") if node else ""
            if existing and existing != hint:
                c_att = {"action": "input", "step_id": f"login_{step_name}_clear"}
                c_tap = {"tree": tree, "loc": loc, "target": target, "target_ref": target_ref,
                         "page": obs.get("page"), "layout": obs.get("layout"),
                         "observed_action_epoch": rt.last_obs_epoch}
                c_res = rt.dispatcher.send("clear_text", *loc["point"], len(existing) + 4,
                                           preconditions=rt.dispatcher.app_conditions("input"), att=c_att, tap=c_tap)
                if not c_res.ok:
                    status = c_att.get("status") or c_res.error_class or "clear_failed"
                    if status == "target_obscured" and attempt == 0:
                        rt.send_back(f"{step_name}_dismiss_overlay")
                        rt.sleep(0.25)
                        obs, tree = rt.observe_fast(f"login_{step_name}_after_back")
                        continue
                    rec = {"step": step_name, "target": tid, "status": status, "stage": "clear",
                           "attempt": attempt + 1, "action_required": bool(c_att.get("action_required"))}
                    if c_att.get("execution_error"):
                        rec["execution_error"] = c_att["execution_error"]
                    rt.steps.append(rec)
                    return False, rec, obs, tree
                rt.sleep(0.15)
                obs, tree = rt.observe_fast(f"login_{step_name}_after_clear")
                tid, target, loc = _resolve_target(app, target_ref, tree, obs)
                if loc.get("status") != "found":
                    rec = {"step": step_name, "target": tid, "status": loc.get("status"),
                           "attempt": attempt + 1, "action_required": False}
                    rt.steps.append(rec)
                    return False, rec, obs, tree

            att = {"action": "input", "step_id": f"login_{step_name}",
                   "dispatch_coordinates": {"system": "px", "point": loc["point"]}}
            tap = {"tree": tree, "loc": loc, "target": target, "target_ref": target_ref,
                   "page": obs.get("page"), "layout": obs.get("layout")}
            res = rt.dispatcher.send("input_text", *loc["point"], secret_val,
                                     preconditions=rt.dispatcher.app_conditions("input"), att=att, tap=tap)
            if res.ok:
                rec = {"step": step_name, "target": tid, "status": "ok", "redacted": True,
                       "attempt": attempt + 1, "action_required": False}
                rt.steps.append(rec)
                return True, rec, obs, tree
            status = att.get("status") or res.error_class or "failed"
            if status == "target_obscured" and attempt == 0:
                rt.send_back(f"{step_name}_dismiss_overlay")
                rt.sleep(0.25)
                obs, tree = rt.observe_fast(f"login_{step_name}_retry")
                continue
            rec = {"step": step_name, "target": tid, "status": status, "attempt": attempt + 1,
                   "action_required": bool(att.get("action_required"))}
            if att.get("execution_error"):
                rec["execution_error"] = att["execution_error"]
            rt.steps.append(rec)
            return False, rec, obs, tree
        return False, {"step": step_name, "status": "failed", "action_required": False}, obs, tree

    def fill_credential_field(prefix: str, target_ref: str, secret_val: str, focus_err: str,
                              manual_err: str, input_err: str, tree, obs) -> tuple[bool, Optional[dict], dict, object]:
        ok, rec = rt.send_tap(f"focus_{prefix}", target_ref, tree, obs)
        if not ok:
            return False, finish("blocked", reason=f"{focus_err}：{rec['status']}",
                                 action_required=needs_human(rec), final_shot=True), obs, tree
        rt.sleep(0.25)
        obs, tree = rt.observe_fast(f"login_after_focus_{prefix}")
        if "手动输入" in _visible_texts(tree):
            ok, rec = rt.send_tap(f"{prefix}_manual_btn", "qk_manual_btn", tree, obs)
            if not ok:
                return False, finish("blocked", reason=f"{manual_err}：{rec['status']}",
                                     action_required=needs_human(rec), final_shot=True), obs, tree
            rt.sleep(0.25)
            obs, tree = rt.observe_fast(f"login_after_{prefix}_manual")
        if _has_select_overlay(app, tree, obs):
            rt.send_back(f"{prefix}_dismiss_select_overlay")
            rt.sleep(0.25)
            obs, tree = rt.observe_fast(f"login_after_{prefix}_overlay")
        ok, rec, obs, tree = send_secret_input(f"input_{prefix}", target_ref, secret_val, tree, obs)
        if not ok:
            return False, finish("blocked", reason=f"{input_err}：{rec['status']}",
                                 action_required=needs_human(rec), final_shot=True), obs, tree
        return True, None, obs, tree

    # 1-2. 自动解锁 + 初始观察；不在前台时拉起被测 APP
    obs, tree, halted = rt.unlock_and_bring_to_foreground(
        timeout_s, query_error_reason="查询不到锁屏状态，未下发登录动作", replay_launch_polls=5,
    )
    if halted is not None:
        return halted

    kind, detail = _classify_screen(app, obs, tree)
    if kind == "logged_in":
        return finish("logged_in", method="already", final_shot=False)
    halted = rt.check_challenge(obs, tree)
    if halted is not None:
        return halted

    # 3. 读取外置凭据（仅在确实需要登录时强制校验）
    acc_val = resolve_secret(ws, acc_key)
    pwd_val = resolve_secret(ws, pwd_key)
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
        halted = rt.check_challenge(obs, tree)
        if halted is not None:
            return halted
        if kind == "benign_dialog" and detail:
            ok, rec = rt.dismiss_benign_dialog(f"dismiss_{detail['name']}", detail, tree, obs)
            if not ok:
                return finish("blocked", reason=f"良性弹窗 {detail['name']} 关闭失败：{rec['status']}",
                              action_required=needs_human(rec), final_shot=True)
        elif kind == "logged_out_my":
            ok, rec = rt.send_tap("tap_login_register", "qk_login_register", tree, obs)
            if not ok:
                return finish("blocked", reason=f"点击登录/注册失败：{rec['status']}", action_required=needs_human(rec))
        elif kind == "one_click":
            ok, rec = rt.send_tap("tap_other_method", "qk_login_other_method", tree, obs, allow_fallback_text="其他登录方式")
            if not ok:
                return finish("blocked", reason=f"点击其他登录方式失败：{rec['status']}", action_required=needs_human(rec))
        elif kind == "method_select":
            ok, rec = rt.send_tap("tap_pwd_login", "qk_pwd_login", tree, obs, allow_fallback_text="密码登录")
            if not ok:
                return finish("blocked", reason=f"点击密码登录失败：{rec['status']}", action_required=needs_human(rec))
        else:
            return finish("blocked", reason=f"当前页面无法识别为登录入口或密码表单（{kind}）",
                          action_required=False, final_shot=True)
        rt.sleep(0.35)
        obs, tree = rt.observe_fast(f"login_nav_{nav_i + 1}")
        kind, detail = _classify_screen(app, obs, tree)

    if kind != "password_form":
        if kind == "logged_in":
            return finish("logged_in", method="already", final_shot=True)
        return finish("blocked", reason=f"未能进入密码登录表单（当前：{kind}）", action_required=False, final_shot=True)

    # 5. 账号输入：先点输入框 -> 遇密码保险箱先点「手动输入」（防清空账号）-> 遇 SelectOverlay back -> 输入账号
    ok, halted, obs, tree = fill_credential_field(
        "phone", "qk_number_input", acc_val,
        "聚焦手机号输入框失败", "关闭密码保险箱「手动输入」失败", "输入手机号失败",
        tree, obs,
    )
    if not ok:
        return halted

    # 6. 密码输入：聚焦 -> 处理密码保险箱/SelectOverlay -> 输入密码
    rt.sleep(0.15)
    obs, tree = rt.observe_fast("login_before_pwd")
    ok, halted, obs, tree = fill_credential_field(
        "pwd", "qk_pwd_input", pwd_val,
        "聚焦密码输入框失败", "关闭密码区「手动输入」失败", "输入密码失败",
        tree, obs,
    )
    if not ok:
        return halted

    # 7. 收起键盘 -> 勾选未勾选的用户协议 -> 点击登录
    rt.send_back("dismiss_ime")
    rt.sleep(0.25)
    obs, tree = rt.observe_fast("login_before_submit")
    _, _, proto_loc = _resolve_target(app, "qk_protocol_unchecked", tree, obs)
    if proto_loc.get("status") == "found":
        ok, rec = rt.send_tap("check_protocol", "qk_protocol_unchecked", tree, obs)
        if not ok:
            return finish("blocked", reason=f"勾选用户协议失败：{rec['status']}",
                          action_required=needs_human(rec), final_shot=True)
        rt.sleep(0.15)
        obs, tree = rt.observe_fast("login_after_protocol")
    ok, rec = rt.send_tap("submit_login", "qk_login_btn", tree, obs)
    if not ok:
        return finish("blocked", reason=f"点击登录按钮失败：{rec['status']}",
                      action_required=needs_human(rec), final_shot=True)

    # 8. 快速轮询登录结果（不固定长 sleep，命中即返回）
    return rt.poll_until(
        "login_poll", timeout_s, 0.45,
        lambda o, t, k, d, pn: finish("logged_in", method="password", final_shot=True) if k == "logged_in" else None,
        replay_max_polls=3,
        handle_benign_dialogs=True,
        dismiss_prefix="post_dismiss",
        challenge_stage="提交密码",
        on_timeout=lambda o, t, k, d: finish("timeout", reason=f"提交登录后 {timeout_s}s 内未确认进入已登录页（当前：{k}）",
                                             action_required=True, final_shot=True),
    )
