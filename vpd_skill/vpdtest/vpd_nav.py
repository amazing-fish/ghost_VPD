"""可复用的快速原子导航能力（switch-vehicle / enter-vpd，当前支持 qiankun_hm）。

将多步手动 act 选车与进 VPD 收敛为单次受控能力调用，并列于 unlock 与 login：
- 所有物理动作经 Dispatcher 下发，保留锁屏/前台复核、遮挡校验、policy 门禁与事件审计；
- switch-vehicle 涉及 vehicle_selection 副作用，强制校验 --authorize vehicle_selection；
- enter-vpd 所属 vpd_entry 随任务策略许可，无需逐次询问（仅当显式传入 --vin-tail 时要求 vehicle_selection）；
- 中间页跳转跳过截图（screenshot=False）以压缩 HDC I/O，仅在终态或人工接管时采证；
- 遇未登录可返回 login_required（或显式授权 account_state 时自动链式调用 login_app），遇安全码/短信挑战原地保留现场。
"""
from __future__ import annotations

import copy
import math
import re
import time
from typing import Optional

from .experience import ExperienceStore
from .login import _classify_screen, _resolve_target, _visible_texts, login_app
from .observe import RunStore
from .outcomes import error_attribution
from .unlock import unlock_device
from .workspace import RunContext

_VIN_TAIL_RE = re.compile(r"^[A-Za-z0-9]{1,17}$")
_LOGGED_OUT_KINDS = frozenset({"logged_out_my", "one_click", "method_select", "password_form"})


def _validate_vin_tail(vin_tail: Optional[str]) -> str:
    val = "" if vin_tail is None else str(vin_tail).strip()
    if not _VIN_TAIL_RE.fullmatch(val):
        raise ValueError("--vin-tail 须为 1-17 位字母数字车架号尾号（例如 000228）")
    return val


def _validate_timeout(timeout_s: float, *, max_s: float) -> float:
    if (not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool)
            or not math.isfinite(timeout_s) or timeout_s <= 0 or timeout_s > max_s):
        raise ValueError(f"timeout_s 须为 (0, {int(max_s)}] 秒内的有限数字")
    return float(timeout_s)


def _needs_human(rec: dict) -> bool:
    return bool(rec.get("action_required")) or rec.get("status") == "state_indeterminate"


def _build_vin_target(app, vin_tail: str) -> tuple[str, dict]:
    raw = copy.deepcopy(app.targets["qk_car_by_vin"])
    for st in raw.get("strategies") or []:
        if isinstance(st.get("value"), str):
            st["value"] = st["value"].replace("${param:vin_tail}", vin_tail)
    return "qk_car_by_vin", raw


def _read_target_text(app, target_ref: str, tree, obs) -> Optional[str]:
    if tree is None or tree.status != "ok" or target_ref not in app.targets:
        return None
    _, _, loc = _resolve_target(app, target_ref, tree, obs)
    if loc.get("status") != "found":
        return None
    hit = next((a.get("node") for a in loc.get("attempts", []) if a.get("status") == "found" and a.get("node")), None)
    node = tree.nodes[hit["idx"]] if (hit is not None and 0 <= hit["idx"] < len(tree.nodes)) else (
        tree.node_at(*loc["point"]) if loc.get("point") else None
    )
    if node is None:
        return None
    if (node.text or "").strip():
        return node.text.strip()
    texts = [t.strip() for t in tree.subtree_texts(node) if (t or "").strip()]
    return texts[0] if texts else None


def _swipe_up_points(tree, observer) -> Optional[tuple[int, int, int, int]]:
    rect = None
    if tree is not None and tree.status == "ok":
        sc = tree.scroll_container(None)
        rect = tree.visible_rect(sc) if sc is not None else tree.window_rect()
    if not rect:
        rect = getattr(observer, "last_window", None)
    if not rect or rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None
    x = (rect[0] + rect[2]) // 2
    top = rect[1] + int((rect[3] - rect[1]) * 0.25)
    bottom = rect[1] + int((rect[3] - rect[1]) * 0.75)
    return x, bottom, x, top


def _make_runtime(ws, app, device, *, kind: str, capability: str, granted: set[str],
                  store: Optional[RunStore], sleep, extra_start: Optional[dict] = None):
    if sleep is None:
        sleep = time.sleep if device.kind != "replay" else (lambda _: None)
    store = store or RunStore(ws.runs_dir, kind=kind)
    store.event("capability_start", capability=capability, app=app.id, device=device.spec, **(extra_start or {}))
    ctx = RunContext(ws, app, device, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
    ctx.policy.check_grants(granted)
    ctx.authorized = ctx.policy.effective_grants(granted)
    return store, ctx, ctx.dispatcher, ctx.observer, sleep


def _send_tap(app, dispatcher, steps: list[dict], prefix: str, step_name: str, target_ref, tree, obs) -> tuple[bool, dict]:
    tid, target, loc = _resolve_target(app, target_ref, tree, obs)
    if loc.get("status") != "found":
        rec = {"step": step_name, "target": tid, "status": loc.get("status"), "action_required": False}
        steps.append(rec)
        return False, rec
    att = {"action": "tap", "step_id": f"{prefix}_{step_name}",
           "dispatch_coordinates": {"system": "px", "point": loc["point"]}}
    tap = {"tree": tree, "loc": loc, "target": target, "target_ref": target_ref if isinstance(target_ref, str) else None,
           "page": obs.get("page"), "layout": obs.get("layout")}
    res = dispatcher.send("tap", *loc["point"], preconditions=dispatcher.app_conditions("tap"), att=att, tap=tap)
    rec = {"step": step_name, "target": tid,
           "status": "ok" if res.ok else (att.get("status") or res.error_class or "failed"),
           "sent": res.sent, "action_required": bool(att.get("action_required"))}
    if att.get("execution_error"):
        rec["execution_error"] = att["execution_error"]
    steps.append(rec)
    return bool(res.ok), rec


def _send_back(dispatcher, steps: list[dict], prefix: str, step_name: str) -> tuple[bool, dict]:
    att = {"action": "back", "step_id": f"{prefix}_{step_name}"}
    res = dispatcher.send("key", "back", preconditions=dispatcher.app_conditions("back"), att=att)
    rec = {"step": step_name, "action": "back",
           "status": "ok" if res.ok else (att.get("status") or res.error_class or "failed"),
           "sent": res.sent, "action_required": bool(att.get("action_required"))}
    if att.get("execution_error"):
        rec["execution_error"] = att["execution_error"]
    steps.append(rec)
    return bool(res.ok), rec


def _send_swipe_up(dispatcher, observer, steps: list[dict], prefix: str, step_name: str, tree) -> tuple[bool, dict]:
    pts = _swipe_up_points(tree, observer)
    if not pts:
        rec = {"step": step_name, "action": "swipe", "status": "screen_size_unknown", "action_required": False}
        steps.append(rec)
        return False, rec
    att = {"action": "swipe", "step_id": f"{prefix}_{step_name}"}
    res = dispatcher.send("swipe", *pts, preconditions=dispatcher.app_conditions("swipe"), att=att)
    rec = {"step": step_name, "action": "swipe",
           "status": "ok" if res.ok else (att.get("status") or res.error_class or "failed"),
           "sent": res.sent, "action_required": bool(att.get("action_required"))}
    if att.get("execution_error"):
        rec["execution_error"] = att["execution_error"]
    steps.append(rec)
    return bool(res.ok), rec


def _dismiss_benign_dialog(app, dispatcher, steps: list[dict], prefix: str, step_name: str,
                           detail: dict, tree, obs) -> tuple[bool, dict]:
    fb = {"label": f"dismiss:{detail['name']}", "side_effect": "dismiss_dialog",
          "strategies": [{"kind": "text", "value": detail["dismiss_text"]}]}
    return _send_tap(app, dispatcher, steps, prefix, step_name, fb, tree, obs)


def _unlock_and_bring_to_foreground(ws, app, device, store, dispatcher, observer, steps: list[dict],
                                    prefix: str, timeout_s: float, sleep, finish):
    locked = device.screen_locked()
    if locked is None:
        err = getattr(device, "screen_query_error", None)
        extra = ({"execution_error": {**err, "stage": f"{prefix}_unlock_query",
                                      "attribution": error_attribution(err.get("error_class"))}}
                 if err else None)
        return None, None, finish("indeterminate", reason="查询不到锁屏状态，未下发导航动作",
                                  action_required=True, extra=extra)
    if locked is True:
        ul = unlock_device(ws, device, store, dispatcher=dispatcher)
        steps.append({"step": "unlock", "status": ul["status"]})
        if ul["status"] != "unlocked":
            return None, None, finish("indeterminate" if ul["status"] != "pin_rejected" else "screen_locked",
                                      reason=ul.get("reason", "自动解锁未完成"), action_required=True)

    obs, tree = observer.observe(f"{prefix}_initial", screenshot=False)
    kind, _ = _classify_screen(app, obs, tree)
    if kind == "out_of_app":
        att = {"action": "launch", "step_id": f"{prefix}_launch"}
        lres = dispatcher.send("launch", app.bundle, app.ability,
                               preconditions=dispatcher.app_conditions("launch"), att=att)
        steps.append({"step": "launch", "status": "ok" if lres.ok else (att.get("status") or lres.error_class)})
        if not lres.ok:
            return None, None, finish("indeterminate", reason="拉起被测 APP 失败", action_required=True)
        deadline = time.monotonic() + min(8.0, timeout_s)
        while True:
            sleep(0.4)
            obs, tree = observer.observe(f"{prefix}_after_launch", screenshot=False)
            kind, _ = _classify_screen(app, obs, tree)
            if kind not in ("out_of_app", "unobservable", "unknown") or time.monotonic() >= deadline:
                break
    return obs, tree, None


def _ensure_car_tab(app, dispatcher, observer, steps: list[dict], prefix: str, obs, tree,
                    timeout_s: float, sleep, finish) -> tuple[dict, object, Optional[dict]]:
    """从任意已登录页（探索/我的/VPD首页/未知二级页）快速收敛至爱车页或车辆入口。"""
    deadline = time.monotonic() + min(timeout_s, 10.0)
    for hop in range(5):
        kind, detail = _classify_screen(app, obs, tree)
        if kind == "safe_code":
            return obs, tree, finish("safe_code_required", reason="命中安全码输入页，已原地保留现场等待人工处理",
                                     action_required=True, final_shot=True)
        if kind == "sms_verify":
            return obs, tree, finish("sms_required", reason="命中短信验证码页，已原地保留现场等待人工处理",
                                     action_required=True, final_shot=True)
        if kind in _LOGGED_OUT_KINDS:
            return obs, tree, finish("login_required", reason="当前处于未登录状态，需要登录后再继续",
                                     action_required=True, final_shot=True)
        if kind == "benign_dialog" and detail:
            ok, rec = _dismiss_benign_dialog(app, dispatcher, steps, prefix, f"dismiss_{detail['name']}", detail, tree, obs)
            if not ok:
                return obs, tree, finish("blocked", reason=f"良性弹窗 {detail['name']} 关闭失败：{rec['status']}",
                                         action_required=_needs_human(rec), final_shot=True)
        else:
            page = obs.get("page") or {}
            if app.states.is_state(page, "qk_car_tab"):
                return obs, tree, None
            _, _, car_sel_loc = _resolve_target(app, "qk_car_select", tree, obs)
            if car_sel_loc.get("status") == "found":
                return obs, tree, None
            _, _, tab_loc = _resolve_target(app, "qk_tab_car", tree, obs)
            if tab_loc.get("status") == "found":
                ok, rec = _send_tap(app, dispatcher, steps, prefix, f"tap_car_tab_{hop + 1}", "qk_tab_car", tree, obs)
                if not ok:
                    return obs, tree, finish("blocked", reason=f"切换爱车页失败：{rec['status']}",
                                             action_required=_needs_human(rec), final_shot=True)
            else:
                ok, rec = _send_back(dispatcher, steps, prefix, f"back_to_car_{hop + 1}")
                if not ok:
                    return obs, tree, finish("blocked", reason=f"返回上一页失败：{rec['status']}",
                                             action_required=_needs_human(rec), final_shot=True)
        if time.monotonic() >= deadline:
            break
        sleep(0.35)
        obs, tree = observer.observe(f"{prefix}_car_nav_{hop + 1}", screenshot=False)
    return obs, tree, None


def switch_vehicle_app(ws, app, device, *, vin_tail: str, authorize=None,
                       timeout_s: float = 15.0, store: Optional[RunStore] = None, sleep=None) -> dict:
    """原子按 VIN 尾号切车能力（当前支持 qiankun_hm）。"""
    t0 = time.monotonic()
    if app.id != "qiankun_hm":
        raise ValueError(f"当前原子选车能力仅支持 qiankun_hm，收到 {app.id}")
    vin = _validate_vin_tail(vin_tail)
    timeout = _validate_timeout(timeout_s, max_s=60.0)

    granted = set(authorize or [])
    if "vehicle_selection" not in granted:
        return {
            "app": app.id,
            "device": device.spec,
            "status": "authorization_required",
            "reason": "切车属于 vehicle_selection 副作用，须显式传入 --authorize vehicle_selection",
            "vin_tail": vin,
            "action_required": False,
            "actions": 0,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "steps": [],
        }

    store, ctx, dispatcher, observer, sleep = _make_runtime(
        ws, app, device, kind="switch_vehicle", capability="switch_vehicle",
        granted=granted, store=store, sleep=sleep, extra_start={"vin_tail": vin},
    )
    steps: list[dict] = []

    def finish(status: str, *, reason: Optional[str] = None, current_vehicle: Optional[str] = None,
               action_required: bool = False, final_shot: bool = False, extra: Optional[dict] = None) -> dict:
        final_obs = None
        if final_shot:
            obs_f, _ = observer.observe(f"switch_vehicle_{status}", screenshot=True)
            final_obs = obs_f["obs_id"]
        elapsed = round(time.monotonic() - t0, 2)
        out = {
            "run": store.run_id,
            "app": app.id,
            "device": device.spec,
            "status": status,
            "vin_tail": vin,
            **({"current_vehicle": current_vehicle} if current_vehicle else {}),
            **({"reason": reason} if reason else {}),
            "action_required": action_required,
            "actions": ctx.actions,
            "elapsed_s": elapsed,
            **({"final_obs": final_obs} if final_obs else {}),
            "steps": steps,
            **(extra or {}),
        }
        store.event("capability_end", capability="switch_vehicle", status=status, vin_tail=vin,
                    current_vehicle=current_vehicle, actions=ctx.actions,
                    elapsed_s=elapsed, action_required=action_required)
        return out

    obs, tree, halted = _unlock_and_bring_to_foreground(
        ws, app, device, store, dispatcher, observer, steps, "sv", timeout, sleep, finish
    )
    if halted is not None:
        return halted

    kind, detail = _classify_screen(app, obs, tree)
    if kind == "safe_code":
        return finish("safe_code_required", reason="命中安全码输入页，已原地保留现场等待人工处理",
                      action_required=True, final_shot=True)
    if kind == "sms_verify":
        return finish("sms_required", reason="命中短信验证码页，已原地保留现场等待人工处理",
                      action_required=True, final_shot=True)
    if kind in _LOGGED_OUT_KINDS:
        return finish("login_required", reason="当前处于未登录状态，无法切换车辆",
                      action_required=True, final_shot=True)

    # 若尚未在车辆列表页，先收敛到爱车页再点开车辆列表
    if not app.states.is_state(obs.get("page"), "qk_vehicle_list"):
        obs, tree, halted = _ensure_car_tab(
            app, dispatcher, observer, steps, "sv", obs, tree, timeout, sleep, finish
        )
        if halted is not None:
            return halted
        if not app.states.is_state(obs.get("page"), "qk_vehicle_list"):
            ok, rec = _send_tap(app, dispatcher, steps, "sv", "open_vehicle_list", "qk_car_select", tree, obs)
            if not ok:
                return finish("blocked", reason=f"打开车辆列表失败：{rec['status']}",
                              action_required=_needs_human(rec), final_shot=True)
            list_deadline = time.monotonic() + min(8.0, timeout)
            poll_n = 0
            while True:
                sleep(0.35)
                poll_n += 1
                obs, tree = observer.observe(f"sv_wait_list_{poll_n}", screenshot=False)
                kind, detail = _classify_screen(app, obs, tree)
                if kind == "benign_dialog" and detail:
                    ok_d, rec_d = _dismiss_benign_dialog(app, dispatcher, steps, "sv",
                                                         f"list_dismiss_{detail['name']}", detail, tree, obs)
                    if not ok_d and (_needs_human(rec_d) or rec_d.get("status") in ("budget_exhausted", "motion_timeout", "safety_hold")):
                        return finish("blocked", reason=f"关闭弹窗 {detail['name']} 失败：{rec_d['status']}",
                                      action_required=True, final_shot=True)
                elif app.states.is_state(obs.get("page"), "qk_vehicle_list"):
                    break
                if time.monotonic() >= list_deadline or (device.kind == "replay" and poll_n >= 3):
                    return finish("blocked", reason="未能打开车辆列表页面", action_required=False, final_shot=True)

    # 在车辆列表按 VIN 尾号唯一定位（最多上滑 6 次，页面 VIN 签名无变化早停）
    def _vin_sig(t) -> set[str]:
        all_t = _visible_texts(t)
        vins = {x for x in all_t if "车架号" in x}
        return vins if vins else all_t

    _, vin_target = _build_vin_target(app, vin)
    seen_texts = _vin_sig(tree)
    loc = None
    for scroll_i in range(7):
        _, _, loc = _resolve_target(app, vin_target, tree, obs)
        if loc.get("status") == "found":
            break
        if loc.get("status") == "ambiguous":
            return finish("ambiguous", reason=f"车架号尾号 {vin} 在车辆列表中命中多个候选项",
                          action_required=False, final_shot=True)
        if scroll_i == 6:
            break
        ok, rec = _send_swipe_up(dispatcher, observer, steps, "sv", f"scroll_vin_{scroll_i + 1}", tree)
        if not ok:
            return finish("blocked", reason=f"滚动查找车辆失败：{rec['status']}",
                          action_required=_needs_human(rec), final_shot=True)
        sleep(0.35)
        obs, tree = observer.observe(f"sv_after_scroll_{scroll_i + 1}", screenshot=False)
        new_texts = _vin_sig(tree)
        if new_texts == seen_texts:
            _, _, loc = _resolve_target(app, vin_target, tree, obs)
            break
        seen_texts = new_texts

    if not loc or loc.get("status") != "found":
        status = "ambiguous" if (loc and loc.get("status") == "ambiguous") else "not_found"
        return finish(status, reason=f"车辆列表中未唯一找到尾号 {vin}（{status}）",
                      action_required=False, final_shot=True)

    ok, rec = _send_tap(app, dispatcher, steps, "sv", "select_vin", vin_target, tree, obs)
    if not ok:
        return finish("blocked", reason=f"点选车辆 {vin} 失败：{rec['status']}",
                      action_required=_needs_human(rec), final_shot=True)

    deadline = time.monotonic() + timeout
    poll_n = 0
    while True:
        sleep(0.35)
        poll_n += 1
        obs, tree = observer.observe(f"sv_confirm_{poll_n}", screenshot=False)
        kind, detail = _classify_screen(app, obs, tree)
        if kind == "safe_code":
            return finish("safe_code_required", reason="切换车辆后命中安全码输入页，已原地保留现场",
                          action_required=True, final_shot=True)
        if kind == "benign_dialog" and detail:
            ok_d, rec_d = _dismiss_benign_dialog(app, dispatcher, steps, "sv",
                                                 f"post_dismiss_{detail['name']}", detail, tree, obs)
            if not ok_d and (_needs_human(rec_d) or rec_d.get("status") in ("budget_exhausted", "motion_timeout", "safety_hold")):
                return finish("blocked", reason=f"关闭弹窗 {detail['name']} 失败：{rec_d['status']}",
                              action_required=True, final_shot=True)
        elif app.states.is_state(obs.get("page"), "qk_car_tab"):
            cur = _read_target_text(app, "qk_car_select", tree, obs)
            return finish("switched", current_vehicle=cur, final_shot=True)
        if time.monotonic() >= deadline or (device.kind == "replay" and poll_n >= 3):
            return finish("timeout", reason=f"点选车辆 {vin} 后 {timeout}s 内未回到爱车页",
                          action_required=True, final_shot=True)


def enter_vpd_app(ws, app, device, *, authorize=None, vin_tail: Optional[str] = None,
                  account_secret: Optional[str] = None, password_secret: Optional[str] = None,
                  timeout_s: float = 25.0, store: Optional[RunStore] = None, sleep=None) -> dict:
    """原子进入 VPD 首页能力（当前支持 qiankun_hm；可选顺带按 --vin-tail 切车与按 account_state 自动登录）。"""
    t0 = time.monotonic()
    if app.id != "qiankun_hm":
        raise ValueError(f"当前原子进 VPD 能力仅支持 qiankun_hm，收到 {app.id}")
    timeout = _validate_timeout(timeout_s, max_s=120.0)
    vin = _validate_vin_tail(vin_tail) if vin_tail is not None else None

    granted = set(authorize or [])
    if vin is not None and "vehicle_selection" not in granted:
        return {
            "app": app.id,
            "device": device.spec,
            "status": "authorization_required",
            "reason": "传入 --vin-tail 切车属于 vehicle_selection 副作用，须显式传入 --authorize vehicle_selection",
            "vin_tail": vin,
            "action_required": False,
            "actions": 0,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "steps": [],
        }

    store, ctx, dispatcher, observer, sleep = _make_runtime(
        ws, app, device, kind="enter_vpd", capability="enter_vpd",
        granted=granted, store=store, sleep=sleep,
        extra_start=({"vin_tail": vin} if vin is not None else None),
    )
    steps: list[dict] = []
    sub_actions = 0

    def finish(status: str, *, method: Optional[str] = None, reason: Optional[str] = None,
               park_name: Optional[str] = None, action_required: bool = False,
               final_shot: bool = False, extra: Optional[dict] = None) -> dict:
        final_obs = None
        if final_shot:
            obs_f, _ = observer.observe(f"enter_vpd_{status}", screenshot=True)
            final_obs = obs_f["obs_id"]
        elapsed = round(time.monotonic() - t0, 2)
        total_actions = ctx.actions + sub_actions
        out = {
            "run": store.run_id,
            "app": app.id,
            "device": device.spec,
            "status": status,
            **({"method": method} if method else {}),
            **({"park_name": park_name} if park_name else {}),
            **({"vin_tail": vin} if vin else {}),
            **({"reason": reason} if reason else {}),
            "action_required": action_required,
            "actions": total_actions,
            "elapsed_s": elapsed,
            **({"final_obs": final_obs} if final_obs else {}),
            "steps": steps,
            **(extra or {}),
        }
        store.event("capability_end", capability="enter_vpd", status=status, method=method,
                    park_name=park_name, actions=total_actions,
                    elapsed_s=elapsed, action_required=action_required)
        return out

    obs, tree, halted = _unlock_and_bring_to_foreground(
        ws, app, device, store, dispatcher, observer, steps, "ev", timeout, sleep, finish
    )
    if halted is not None:
        return halted

    # 若命中未登录：授权了 account_state 时自动链式调用 login_app，否则返回 login_required
    kind, detail = _classify_screen(app, obs, tree)
    if kind == "safe_code":
        return finish("safe_code_required", reason="命中安全码输入页，已原地保留现场等待人工处理",
                      action_required=True, final_shot=True)
    if kind == "sms_verify":
        return finish("sms_required", reason="命中短信验证码页，已原地保留现场等待人工处理",
                      action_required=True, final_shot=True)
    if kind in _LOGGED_OUT_KINDS:
        if "account_state" not in granted:
            return finish("login_required", reason="当前处于未登录状态，需先登录（或传入 --authorize account_state）",
                          action_required=True, final_shot=True)
        login_res = login_app(
            ws, app, device, authorize=granted,
            account_secret=account_secret, password_secret=password_secret,
            timeout_s=min(15.0, timeout), store=store, sleep=sleep,
        )
        sub_actions += int(login_res.get("actions") or 0)
        steps.append({"step": "login", "status": login_res["status"], "method": login_res.get("method"),
                      "actions": login_res.get("actions", 0)})
        if login_res["status"] != "logged_in":
            return finish(
                login_res["status"],
                reason=login_res.get("reason", "自动登录未完成"),
                action_required=bool(login_res.get("action_required")),
                final_shot=False,
                extra={"login": login_res},
            )
        obs, tree = observer.observe("ev_after_login", screenshot=False)
        kind, detail = _classify_screen(app, obs, tree)

    if kind == "benign_dialog" and detail:
        ok_d, rec_d = _dismiss_benign_dialog(app, dispatcher, steps, "ev",
                                             f"pre_dismiss_{detail['name']}", detail, tree, obs)
        if not ok_d:
            return finish("blocked", reason=f"关闭弹窗 {detail['name']} 失败：{rec_d['status']}",
                          action_required=_needs_human(rec_d), final_shot=True)
        sleep(0.35)
        obs, tree = observer.observe("ev_after_pre_dismiss", screenshot=False)

    # 未要求切车且已在可用 VPD 首页 → 0 动作秒级返回 already
    texts = _visible_texts(tree)
    if vin is None and app.states.is_state(obs.get("page"), "qk_vpd_home") and "停车场信息加载失败" not in texts:
        return finish("ready", method="already", park_name=_read_target_text(app, "qk_home_park_name", tree, obs),
                      final_shot=False)

    # 如需按 --vin-tail 切车，先执行切车原子能力
    if vin is not None:
        sw = switch_vehicle_app(ws, app, device, vin_tail=vin, authorize=granted,
                                timeout_s=min(15.0, timeout), store=store, sleep=sleep)
        sub_actions += int(sw.get("actions") or 0)
        steps.append({"step": "switch_vehicle", "status": sw["status"], "vin_tail": vin,
                      "current_vehicle": sw.get("current_vehicle")})
        if sw["status"] != "switched":
            return finish(sw["status"], reason=sw.get("reason", "切换目标车辆未完成"),
                          action_required=bool(sw.get("action_required")), final_shot=False,
                          extra={"switch_vehicle": sw})
        obs, tree = observer.observe("ev_after_switch", screenshot=False)

    # 导航至爱车页并定位 ADS 入口（找不到时受控上滑最多 3 次）
    obs, tree, halted = _ensure_car_tab(
        app, dispatcher, observer, steps, "ev", obs, tree, timeout, sleep, finish
    )
    if halted is not None:
        return halted

    ads_loc = None
    for scroll_i in range(4):
        _, _, ads_loc = _resolve_target(app, "qk_ads", tree, obs)
        if ads_loc.get("status") == "found":
            break
        if scroll_i == 3:
            break
        ok, rec = _send_swipe_up(dispatcher, observer, steps, "ev", f"scroll_ads_{scroll_i + 1}", tree)
        if not ok:
            return finish("blocked", reason=f"上滑查找 ADS 入口失败：{rec['status']}",
                          action_required=_needs_human(rec), final_shot=True)
        sleep(0.35)
        obs, tree = observer.observe(f"ev_scroll_ads_{scroll_i + 1}", screenshot=False)

    if not ads_loc or ads_loc.get("status") != "found":
        return finish("blocked", reason="爱车页未找到 ADS 入口（qk_ads）", action_required=False, final_shot=True)

    ok, rec = _send_tap(app, dispatcher, steps, "ev", "tap_ads", "qk_ads", tree, obs)
    if not ok:
        return finish("blocked", reason=f"点击 ADS 失败：{rec['status']}",
                      action_required=_needs_human(rec), final_shot=True)

    # 快速轮询进入 VPD 首页或明确阻塞状态（安全码 / 停车场信息加载失败 / 良性弹窗 / 定位权限未开启 / 仅充电服务）
    deadline = time.monotonic() + timeout
    poll_n = 0
    while True:
        sleep(0.45)
        poll_n += 1
        obs, tree = observer.observe(f"ev_poll_{poll_n}", screenshot=False)
        texts = _visible_texts(tree)
        kind, detail = _classify_screen(app, obs, tree)
        if kind == "safe_code":
            return finish("safe_code_required", reason="进入 VPD 后命中安全码输入页，已原地保留现场等待人工接管",
                          action_required=True, final_shot=True)
        if kind == "sms_verify":
            return finish("sms_required", reason="进入 VPD 后命中短信验证码页，已原地保留现场等待人工接管",
                          action_required=True, final_shot=True)
        if "停车场信息加载失败" in texts:
            return finish("load_failed", reason="VPD 页面显示「停车场信息加载失败」",
                          action_required=False, final_shot=True)
        if kind == "benign_dialog" and detail:
            ok_d, rec_d = _dismiss_benign_dialog(app, dispatcher, steps, "ev",
                                                 f"post_dismiss_{detail['name']}", detail, tree, obs)
            if not ok_d and (_needs_human(rec_d) or rec_d.get("status") in ("budget_exhausted", "motion_timeout", "safety_hold")):
                return finish("blocked", reason=f"进入 VPD 后关闭弹窗 {detail['name']} 失败：{rec_d['status']}",
                              action_required=True, final_shot=True)
        elif app.states.is_state(obs.get("page"), "qk_vpd_home"):
            return finish("ready", method="entered",
                          park_name=_read_target_text(app, "qk_home_park_name", tree, obs),
                          final_shot=True)
        elif (poll_n >= 3 and tree is not None and tree.status == "ok"
              and not any("正在加载" in t for t in texts)
              and any(n.id == "VPD_HOME_VIEW" and tree.visible_rect(n) and tree.visible_lineage(n) for n in tree.nodes)):
            if "定位权限未开启" in texts:
                return finish("location_permission_required", reason="ADS/VPD 页面提示「定位权限未开启」，无法加载泊车代驾首页",
                              action_required=True, final_shot=True)
            if "充电服务" in texts:
                return finish("vpd_unavailable", reason="当前车辆 ADS 页面仅显示充电服务，未呈现泊车代驾首页入口",
                              action_required=False, final_shot=True)
        if time.monotonic() >= deadline or (device.kind == "replay" and poll_n >= 4):
            return finish("timeout", reason=f"点击 ADS 后 {timeout}s 内未进入 VPD 首页",
                          action_required=True, final_shot=True)
