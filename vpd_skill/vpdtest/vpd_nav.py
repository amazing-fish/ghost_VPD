"""可复用的快速原子导航能力（switch-vehicle / enter-vpd，当前支持 qiankun_hm）。

将多步手动 act 选车与进 VPD 收敛为单次受控能力调用，并列于 unlock 与 login：
- 所有物理动作经 Dispatcher 下发，保留锁屏/前台复核、遮挡校验、policy 门禁与事件审计；
- switch-vehicle 涉及 vehicle_selection 副作用，强制校验 --authorize vehicle_selection；
- enter-vpd 所属 vpd_entry 随任务策略许可，无需逐次询问（仅当显式传入 --vin-tail 时要求 vehicle_selection）；
- 链式调用 login / switch-vehicle 时共享顶层 RunContext 动作计数，受同一 max_actions_per_run 预算约束；
- 中间页跳转跳过截图（screenshot=False）以压缩 HDC I/O，仅在终态或人工接管时采证。
"""
from __future__ import annotations

import copy
import time
from typing import Optional

from .capability_runtime import (
    LOGGED_OUT_KINDS,
    CapabilityRuntime,
    hit_node,
    needs_human,
    read_target_text,
    resolve_target,
    row_card_texts,
    validate_timeout,
    validate_vin_tail,
    visible_texts,
)
from .login import _classify_screen, login_app
from .observe import RunStore
from .workspace import RunContext


def _build_vin_target(app, vin_tail: str) -> tuple[str, dict]:
    raw = copy.deepcopy(app.targets["qk_car_by_vin"])
    for st in raw.get("strategies") or []:
        if isinstance(st.get("value"), str):
            st["value"] = st["value"].replace("${param:vin_tail}", vin_tail)
    return "qk_car_by_vin", raw


def _ensure_car_tab(rt: CapabilityRuntime, obs, tree, timeout_s: float) -> tuple[dict, object, Optional[dict]]:
    """从任意已登录页快速收敛至爱车页或车辆入口；超时必回传最新观测并终止。"""
    app = rt.app
    deadline = time.monotonic() + min(timeout_s, 10.0)
    for hop in range(5):
        halted = rt.check_challenge(obs, tree, check_logged_out=True, logged_out_reason="当前处于未登录状态，需要登录后再继续")
        if halted is not None:
            return obs, tree, halted
        kind, detail = rt.classify(app, obs, tree)
        if kind == "benign_dialog" and detail:
            ok, rec = rt.dismiss_benign_dialog(f"dismiss_{detail['name']}", detail, tree, obs)
            if not ok:
                return obs, tree, rt.finish("blocked", reason=f"良性弹窗 {detail['name']} 关闭失败：{rec['status']}",
                                            action_required=needs_human(rec), final_shot=True)
        else:
            page = obs.get("page") or {}
            if app.states.is_state(page, "qk_car_tab"):
                return obs, tree, None
            _, _, car_sel_loc = resolve_target(app, "qk_car_select", tree, obs)
            if car_sel_loc.get("status") == "found":
                return obs, tree, None
            _, _, tab_loc = resolve_target(app, "qk_tab_car", tree, obs)
            if tab_loc.get("status") == "found":
                ok, rec = rt.send_tap(f"tap_car_tab_{hop + 1}", "qk_tab_car", tree, obs)
                if not ok:
                    return obs, tree, rt.finish("blocked", reason=f"切换爱车页失败：{rec['status']}",
                                                action_required=needs_human(rec), final_shot=True)
            else:
                ok, rec = rt.send_back(f"back_to_car_{hop + 1}")
                if not ok:
                    return obs, tree, rt.finish("blocked", reason=f"返回上一页失败：{rec['status']}",
                                                action_required=needs_human(rec), final_shot=True)
        rt.sleep(0.35)
        obs, tree = rt.observe_fast(f"{rt.prefix}_car_nav_{hop + 1}")
        halted = rt.check_challenge(obs, tree, check_logged_out=True, logged_out_reason="当前处于未登录状态,需要登录后再继续")
        if halted is not None:
            return obs, tree, halted
        page = obs.get("page") or {}
        if app.states.is_state(page, "qk_car_tab"):
            return obs, tree, None
        _, _, car_sel_loc = resolve_target(app, "qk_car_select", tree, obs)
        if car_sel_loc.get("status") == "found":
            return obs, tree, None
        if time.monotonic() >= deadline:
            return obs, tree, rt.finish("timeout", reason="切换至爱车页超时", action_required=True, final_shot=True)
    return obs, tree, rt.finish("timeout", reason="未能切换至爱车页", action_required=True, final_shot=True)


def _vpd_blocker(app, obs, tree, *, allow_charging_only: bool) -> Optional[tuple[str, str, bool]]:
    texts = visible_texts(tree)
    kind, _ = _classify_screen(app, obs, tree)
    if kind == "safe_code":
        return "safe_code_required", "命中安全码输入页，已原地保留现场等待人工接管", True
    if kind == "sms_verify":
        return "sms_required", "命中短信验证码页，已原地保留现场等待人工接管", True
    if "停车场信息加载失败" in texts:
        return "load_failed", "VPD 页面显示「停车场信息加载失败」", False
    if (tree is not None and tree.status == "ok"
            and not any("正在加载" in t for t in texts)
            and not app.states.is_state(obs.get("page"), "qk_vpd_home")
            and any(n.id == "VPD_HOME_VIEW" and tree.visible_rect(n) and tree.visible_lineage(n) for n in tree.nodes)):
        if "定位权限未开启" in texts:
            return "location_permission_required", "ADS/VPD 页面提示「定位权限未开启」，无法加载泊车代驾首页", True
        if allow_charging_only and "充电服务" in texts:
            return "vpd_unavailable", "当前车辆 ADS 页面仅显示充电服务，未呈现泊车代驾首页入口", False
    return None


def switch_vehicle_app(ws, app, device, *, vin_tail: str, authorize=None,
                       timeout_s: float = 15.0, store: Optional[RunStore] = None,
                       sleep=None, _ctx: Optional[RunContext] = None) -> dict:
    """原子按 VIN 尾号切车能力（当前支持 qiankun_hm）。"""
    t0 = time.monotonic()
    if app.id != "qiankun_hm":
        raise ValueError(f"当前原子选车能力仅支持 qiankun_hm，收到 {app.id}")
    vin = validate_vin_tail(vin_tail)
    timeout = validate_timeout(timeout_s, max_s=60.0)

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

    rt = CapabilityRuntime.open(
        ws, app, device,
        kind="switch_vehicle", capability="switch_vehicle", prefix="sv", granted=granted,
        store=store, sleep=sleep, _ctx=_ctx, extra_start={"vin_tail": vin},
        classify=lambda a, o, t: _classify_screen(a, o, t), t0=t0,
    )

    base_finish = rt.finish

    def finish(status: str, *, reason: Optional[str] = None, current_vehicle: Optional[str] = None,
               action_required: bool = False, final_shot: bool = False, extra: Optional[dict] = None) -> dict:
        fields = {"vin_tail": vin, **({"current_vehicle": current_vehicle} if current_vehicle else {})}
        return base_finish(
            status, reason=reason, action_required=action_required, final_shot=final_shot,
            fields=fields, event_fields={"vin_tail": vin, "current_vehicle": current_vehicle}, extra=extra,
        )

    # Override rt.finish so shared runtime helpers produce switch_vehicle schema (vin_tail + current_vehicle)
    rt.finish = finish  # type: ignore[method-assign]

    obs, tree, halted = rt.unlock_and_bring_to_foreground(
        timeout, query_error_reason="查询不到锁屏状态，未下发导航动作", replay_launch_polls=4,
    )
    if halted is not None:
        return halted

    halted = rt.check_challenge(obs, tree, check_logged_out=True, logged_out_reason="当前处于未登录状态，无法切换车辆")
    if halted is not None:
        return halted

    def _vin_sig(t) -> set[str]:
        all_t = visible_texts(t)
        vins = {x for x in all_t if "车架号" in x}
        return vins if vins else all_t

    if app.states.is_state(obs.get("page"), "qk_vehicle_list"):
        hit_top, obs, tree, halted = rt.scroll_to_edge(
            "down", step_prefix="scroll_top", obs_prefix="sv_scroll_top",
            max_swipes=6, settle_unchanged=2, interval_s=0.35, sig_fn=_vin_sig,
            stage="回滚车辆列表顶部", obs=obs, tree=tree,
        )
        if halted is not None:
            return halted
        if not hit_top:
            return finish("blocked", reason="车辆列表未能回到顶部,无法保证全列表扫描",
                          action_required=True, final_shot=True)
    else:
        obs, tree, halted = _ensure_car_tab(rt, obs, tree, timeout)
        if halted is not None:
            return halted
        if not app.states.is_state(obs.get("page"), "qk_vehicle_list"):
            ok, rec = rt.send_tap("open_vehicle_list", "qk_car_select", tree, obs)
            if not ok:
                return finish("blocked", reason=f"打开车辆列表失败: {rec['status']}",
                              action_required=needs_human(rec), final_shot=True)
            opened = [None, None, None]

            def _wait_list(o, t, k, d, pn):
                if app.states.is_state(o.get("page"), "qk_vehicle_list"):
                    opened[0], opened[1] = o, t
                    return {"_opened": True}
                return None

            res_open = rt.poll_until(
                "sv_wait_list", min(8.0, timeout), 0.35, _wait_list,
                replay_max_polls=3, handle_benign_dialogs=True, dismiss_prefix="list_dismiss",
                challenge_stage="打开车辆列表",
                on_timeout=lambda o, t, k, d: finish("blocked", reason="未能打开车辆列表页面",
                                                     action_required=False, final_shot=True),
            )
            if not res_open.get("_opened"):
                return res_open
            obs, tree = opened[0], opened[1]

    # 全列表扫描确认 VIN 尾号唯一性（需连续两次未变化才确认触底；先判 inconclusive 再判 not_found）
    _, vin_target = _build_vin_target(app, vin)
    scan_state = {"matched_card": None, "match_missing_between": False, "swipes_past_match": 0, "prev_swipe": -1}

    def _scan_page(o, t, swipe_i):
        _, _, loc = resolve_target(app, vin_target, t, o)
        st = loc.get("status")
        if st == "ambiguous":
            return finish("ambiguous", reason=f"车架号尾号 {vin} 在车辆列表中命中多个候选项",
                          action_required=False, final_shot=True)
        if st == "found":
            card = row_card_texts(t, hit_node(t, loc))
            if scan_state["matched_card"] is not None and (
                    card != scan_state["matched_card"] or scan_state["match_missing_between"]):
                return finish("ambiguous", reason=f"车辆列表不同页均命中尾号 {vin},无法唯一确定车辆",
                              action_required=False, final_shot=True)
            scan_state["matched_card"] = card
            scan_state["swipes_past_match"] = 0
        elif scan_state["matched_card"] is not None:
            scan_state["match_missing_between"] = True
        if scan_state["matched_card"] is not None and swipe_i > scan_state["prev_swipe"] and st != "found":
            scan_state["swipes_past_match"] += 1
        scan_state["prev_swipe"] = swipe_i
        return None

    hit_bottom, obs, tree, scan_halted = rt.bounded_scroll_scan(
        "up", step_prefix="scroll_vin", obs_prefix="sv_after_scroll",
        on_page=_scan_page, obs=obs, tree=tree, max_swipes=6, settle_unchanged=2,
        interval_s=0.35, sig_fn=_vin_sig, stage="滚动扫描车辆列表",
    )
    if scan_halted is not None:
        return scan_halted
    if not hit_bottom:
        return finish("inconclusive", reason=f"车辆列表扫描 6 页仍未确认触底,无法确认尾号 {vin} 唯一性",
                      action_required=True, final_shot=True)
    matched_card = scan_state["matched_card"]
    if matched_card is None:
        return finish("not_found", reason=f"车辆列表中未唯一找到尾号 {vin} [not_found]",
                      action_required=False, final_shot=True)

    # 若唯一定位的行不在当前视口，向下回滚直至再次可见且卡片一致
    _, _, loc = resolve_target(app, vin_target, tree, obs)
    if loc.get("status") != "found":
        for back_i in range(max(scan_state["swipes_past_match"], 1) + 2):
            ok, rec = rt.send_swipe(f"scroll_back_{back_i + 1}", tree, direction="down")
            if not ok:
                return finish("blocked", reason=f"回滚定位车辆失败: {rec['status']}",
                              action_required=needs_human(rec), final_shot=True)
            rt.sleep(0.35)
            obs, tree = rt.observe_fast(f"sv_scroll_back_{back_i + 1}")
            halted = rt.check_challenge(obs, tree, stage="回滚定位车辆")
            if halted is not None:
                return halted
            _, _, loc = resolve_target(app, vin_target, tree, obs)
            if loc.get("status") == "ambiguous":
                return finish("ambiguous", reason=f"车架号尾号 {vin} 命中多个候选项",
                              action_required=False, final_shot=True)
            if loc.get("status") == "found":
                card = row_card_texts(tree, hit_node(tree, loc))
                if card != matched_card:
                    return finish("ambiguous", reason=f"回滚后车架号尾号 {vin} 对应的车辆行已变化,无法唯一确定车辆",
                                  action_required=False, final_shot=True)
                break

    if loc.get("status") != "found":
        return finish("not_found", reason=f"回滚后未能重新定位尾号 {vin}", action_required=False, final_shot=True)

    selected_names = [t.strip() for t in matched_card if "车架号" not in t and vin not in t and t.strip()]
    ok, rec = rt.send_tap("select_vin", vin_target, tree, obs)
    if not ok:
        return finish("blocked", reason=f"点选车辆 {vin} 失败: {rec['status']}",
                      action_required=needs_human(rec), final_shot=True)

    def _confirm_car(o, t, k, d, pn):
        if app.states.is_state(o.get("page"), "qk_car_tab"):
            cur = read_target_text(app, "qk_car_select", t, o)
            if bool(cur) and bool(selected_names) and cur.strip() in selected_names:
                return finish("switched", current_vehicle=cur, final_shot=True)
        return None

    def _confirm_timeout(o, t, k, d):
        if app.states.is_state(o.get("page"), "qk_car_tab"):
            cur = read_target_text(app, "qk_car_select", t, o)
            return finish(
                "unconfirmed", current_vehicle=cur,
                reason=f"已回到爱车页但当前车辆回读 [{cur or '空'}] 与所选车辆 {selected_names or vin} 不一致",
                action_required=True, final_shot=True,
            )
        return finish("timeout", reason=f"点选车辆 {vin} 后 {timeout}s 内未回到爱车页",
                      action_required=True, final_shot=True)

    return rt.poll_until(
        "sv_confirm", timeout, 0.35, _confirm_car,
        replay_max_polls=3, handle_benign_dialogs=True, dismiss_prefix="post_dismiss",
        challenge_stage="切换车辆", on_timeout=_confirm_timeout,
    )


def enter_vpd_app(ws, app, device, *, authorize=None, vin_tail: Optional[str] = None,
                  account_secret: Optional[str] = None, password_secret: Optional[str] = None,
                  timeout_s: float = 25.0, store: Optional[RunStore] = None, sleep=None,
                  _ctx: Optional[RunContext] = None) -> dict:
    """原子进入 VPD 首页能力（当前支持 qiankun_hm；可选顺带按 --vin-tail 切车与按 account_state 自动登录）。"""
    t0 = time.monotonic()
    if app.id != "qiankun_hm":
        raise ValueError(f"当前原子进 VPD 能力仅支持 qiankun_hm，收到 {app.id}")
    timeout = validate_timeout(timeout_s, max_s=120.0)
    vin = validate_vin_tail(vin_tail) if vin_tail is not None else None

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

    rt = CapabilityRuntime.open(
        ws, app, device,
        kind="enter_vpd", capability="enter_vpd", prefix="ev", granted=granted,
        store=store, sleep=sleep, _ctx=_ctx,
        extra_start=({"vin_tail": vin} if vin is not None else None),
        classify=lambda a, o, t: _classify_screen(a, o, t), t0=t0,
    )

    base_finish = rt.finish

    def finish(status: str, *, method: Optional[str] = None, reason: Optional[str] = None,
               park_name: Optional[str] = None, action_required: bool = False,
               final_shot: bool = False, extra: Optional[dict] = None) -> dict:
        fields = {
            **({"method": method} if method else {}),
            **({"park_name": park_name} if park_name else {}),
            **({"vin_tail": vin} if vin else {}),
        }
        return base_finish(
            status, reason=reason, action_required=action_required, final_shot=final_shot,
            fields=fields, event_fields={"method": method, "park_name": park_name}, extra=extra,
        )

    rt.finish = finish  # type: ignore[method-assign]

    obs, tree, halted = rt.unlock_and_bring_to_foreground(
        timeout, query_error_reason="查询不到锁屏状态，未下发导航动作", replay_launch_polls=4,
    )
    if halted is not None:
        return halted

    # 先清理良性弹窗与未登录态（含弹窗关闭后才暴露登录页的场景，共享同一 ctx 预算）
    for _ in range(3):
        halted = rt.check_challenge(obs, tree)
        if halted is not None:
            return halted
        kind, detail = _classify_screen(app, obs, tree)
        if kind == "benign_dialog" and detail:
            ok_d, rec_d = rt.dismiss_benign_dialog(f"pre_dismiss_{detail['name']}", detail, tree, obs)
            if not ok_d:
                return finish("blocked", reason=f"关闭弹窗 {detail['name']} 失败：{rec_d['status']}",
                              action_required=needs_human(rec_d), final_shot=True)
            rt.sleep(0.35)
            obs, tree = rt.observe_fast("ev_after_pre_dismiss")
            continue
        if kind in LOGGED_OUT_KINDS:
            if "account_state" not in granted:
                return finish("login_required", reason="当前处于未登录状态，需先登录（或传入 --authorize account_state）",
                              action_required=True, final_shot=True)
            login_res = login_app(
                ws, app, device, authorize=granted,
                account_secret=account_secret, password_secret=password_secret,
                timeout_s=min(15.0, timeout), store=rt.store, sleep=rt.sleep, _ctx=rt.ctx,
            )
            rt.steps.append({"step": "login", "status": login_res["status"], "method": login_res.get("method"),
                             "actions": login_res.get("actions", 0)})
            if login_res["status"] != "logged_in":
                return finish(
                    login_res["status"],
                    reason=login_res.get("reason", "自动登录未完成"),
                    action_required=bool(login_res.get("action_required")),
                    final_shot=False,
                    extra={"login": login_res},
                )
            obs, tree = rt.observe_fast("ev_after_login")
            continue
        break

    # 若未指定切车且已在 ADS/VPD 视图上：直接检查可用 / 已可见阻塞态；若指定 --vin-tail 则先切车再核对目标车辆可用性
    if vin is None:
        pre_blk = _vpd_blocker(app, obs, tree, allow_charging_only=True)
        if pre_blk is not None:
            st, rsn, req = pre_blk
            return finish(st, reason=rsn, action_required=req, final_shot=True)
        texts = visible_texts(tree)
        if app.states.is_state(obs.get("page"), "qk_vpd_home") and "停车场信息加载失败" not in texts:
            return finish("ready", method="already", park_name=read_target_text(app, "qk_home_park_name", tree, obs),
                          final_shot=False)

    # 如需按 --vin-tail 切车，先执行切车原子能力（共享同一 ctx 预算）
    if vin is not None:
        sw = switch_vehicle_app(ws, app, device, vin_tail=vin, authorize=granted,
                                timeout_s=min(15.0, timeout), store=rt.store, sleep=rt.sleep, _ctx=rt.ctx)
        rt.steps.append({"step": "switch_vehicle", "status": sw["status"], "vin_tail": vin,
                         "current_vehicle": sw.get("current_vehicle")})
        if sw["status"] != "switched":
            return finish(sw["status"], reason=sw.get("reason", "切换目标车辆未完成"),
                          action_required=bool(sw.get("action_required")), final_shot=False,
                          extra={"switch_vehicle": sw})
        obs, tree = rt.observe_fast("ev_after_switch")

    # 导航至爱车页并定位 ADS 入口（找不到时受控上滑最多 3 次）
    obs, tree, halted = _ensure_car_tab(rt, obs, tree, timeout)
    if halted is not None:
        return halted

    ads_loc = None
    for scroll_i in range(4):
        _, _, ads_loc = resolve_target(app, "qk_ads", tree, obs)
        if ads_loc.get("status") == "found":
            break
        if scroll_i == 3:
            break
        ok, rec = rt.send_swipe(f"scroll_ads_{scroll_i + 1}", tree, direction="up")
        if not ok:
            return finish("blocked", reason=f"上滑查找 ADS 入口失败：{rec['status']}",
                          action_required=needs_human(rec), final_shot=True)
        rt.sleep(0.35)
        obs, tree = rt.observe_fast(f"ev_scroll_ads_{scroll_i + 1}")
        halted = rt.check_challenge(obs, tree, stage="上滑查找 ADS 入口")
        if halted is not None:
            return halted

    if not ads_loc or ads_loc.get("status") != "found":
        return finish("blocked", reason="爱车页未找到 ADS 入口（qk_ads）", action_required=False, final_shot=True)

    ok, rec = rt.send_tap("tap_ads", "qk_ads", tree, obs)
    if not ok:
        return finish("blocked", reason=f"点击 ADS 失败：{rec['status']}",
                      action_required=needs_human(rec), final_shot=True)

    # 快速轮询进入 VPD 首页或明确阻塞状态（安全码 / 停车场信息加载失败 / 良性弹窗 / 定位权限未开启 / 仅充电服务）
    def _check_vpd(o, t, k, d, pn):
        blk = _vpd_blocker(app, o, t, allow_charging_only=(pn >= 3))
        if blk is not None:
            st, rsn, req = blk
            return finish(st, reason=rsn, action_required=req, final_shot=True)
        if app.states.is_state(o.get("page"), "qk_vpd_home"):
            return finish("ready", method="entered",
                          park_name=read_target_text(app, "qk_home_park_name", t, o),
                          final_shot=True)
        return None

    return rt.poll_until(
        "ev_poll", timeout, 0.45, _check_vpd,
        replay_max_polls=4, handle_benign_dialogs=True, dismiss_prefix="post_dismiss",
        check_challenges=True, challenge_stage="点击 ADS",
        on_timeout=lambda o, t, k, d: finish("timeout", reason=f"点击 ADS 后 {timeout}s 内未进入 VPD 首页",
                                             action_required=True, final_shot=True),
    )
