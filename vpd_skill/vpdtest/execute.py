"""执行契约：每一步 = 执行前观测 → 定位 → 安全检查 → 下发 → 执行后观测与后置条件判定。

三层结果分别记录：dispatch.sent（命令已发出）、dispatch.executor_ok（执行器返回成功）、
post.met（预期业务状态已出现）。重试与恢复会追加新的 attempt / recovery 记录，不修改首次失败。
恢复只允许：重新观察、关闭"已评审的良性中断"、对幂等步骤重发；异常中断和未知中断不会被自动关闭。
"""
from __future__ import annotations

import re
import time
from typing import Optional

from .cases import execution_structure_issues, iter_case_steps
from .devices import LAUNCHER, DeviceResult
from .dispatch import auxiliary_dispatches, dispatch_effect_unknown
from .layout import key as layout_key
from .layout import resolve_scope
from .locate import resolve
from .session import relative_swipe_options, swipe_options_error
from .util import now_iso
from .verdict import collection_error, error_attribution

ENV_ERRORS = {"screen_locked", "screen_off", "device_offline", "app_missing", "tool_missing", "transport_unavailable"}
STATUS_MAP = {
    "transient_unavailable": ("blocked", "tool", "短提示帧源未就绪，触发动作未下发"),
    "transient_capture_error": ("error", "tool", "短提示证据持久化失败；保留触发动作下发记录，不自动重试"),
    "read_source_unknown": ("pending", "requirement_unclear", "读取来源资格不足，未执行依赖输入"),
    "input_required": ("blocked", "environment", "所选输入缺少有效外部值，未定位或输入"),
    "clear_not_confirmed": ("error", "tool", "清空后目标仍显示非提示文字，未追加输入；请核对当前字段"),
    "preparation_error": ("error", "tool", "操作前弹窗处置失败"),
    "condition_false": ("skipped", "requirement_unclear", "当前独立证据明确不满足操作条件，未执行"),
    "condition_unknown": ("pending", "requirement_unclear", "操作条件未知，未执行"),
    "condition_error": ("error", "tool", "操作条件所需证据采集失败"),
    "observation_failed": ("error", "environment", "观测查询失败，请依据采集错误及下发记录核对现场"),
    "target_unresolved": ("blocked", "tool", "目标未能可靠定位（已重新观察）"),
    "scroll_scope_unavailable": ("blocked", "tool", "当前UI树不可用，无法核验指定滚动范围，未下发"),
    "scroll_container_missing": ("blocked", "tool", "指定范围内没有可见纵向滚动容器，未回退窗口滑动"),
    "target_ambiguous": ("blocked", "tool", "目标匹配到多个元素，拒绝盲点"),
    "target_partially_visible": ("blocked", "tool", "目标只露出一小部分（被裁剪/遮挡或已滑到底），不点边缘"),
    "policy_denied": ("blocked", "tool", "动作被安全策略拒绝"),
    "unobservable": ("blocked", "tool", "动作后无法取得可判定的观测（无 UI 树或解析失败）"),
    "budget_exhausted": ("blocked", "tool", "动作预算耗尽"),
    "safety_hold": ("blocked", "environment", "安全停止未确认，执行暂停，需人工接管"),
    "invalid_step": ("blocked", "tool", "步骤定义无效"),
    "interrupted": ("blocked", "requirement_unclear", "出现未评审或非良性的中断，未自动处理，需确认其性质"),
    "anomaly": ("fail_candidate", "product", "出现异常中断或疑似崩溃"),
    "postcondition_not_met": ("fail_candidate", "product", "动作后预期状态未出现"),
    "stop_not_confirmed": ("error", "tool", "stop_app 后进程仍存活（或存活状态查询失败）：停止未生效，清理不能算完成"),
    "foreground_unknown": ("blocked", "environment",
                           "用例声明 require_launcher 但 stop_app 后前台查询失败：恢复期望无法确认（区别于停止成功）"),
    "foreground_not_restored": ("blocked", "environment",
                                "用例声明 require_launcher 且停止前 APP 在前台，stop_app 后前台却不是桌面：清理不符合声明的恢复期望；"
                                "其他应用占屏本身是环境现象（真机 S5：WeLink 接管前台），未声明时只记录不判失败"),
    "motion_timeout": ("fail_candidate", "product", "车辆运动超过用例声明的 max_motion_s 仍未观测到停止/结束"
                                                    "（可能卡住、终点不对或被挡路；也可能是现场环境原因，需人工确认）"),
    "monitor_abort": ("fail_candidate", "product", "监视期间出现用例声明的中止信号"),
    "target_disabled": ("blocked", "environment", "目标控件当前不可用（置灰），按前置条件不满足处理，未点击"),
    "target_stale": ("blocked", "tool", "执行前复核发现目标已变化（页面刷新/弹窗/滚动/被替换），未下发"),
    "foreground_changed": ("blocked", "environment", "下发前前台已不是被测 APP（被切走或被其他应用覆盖），未下发"),
    "screen_locked": ("blocked", "environment", "自动解锁未完成，按能力结果阻塞"),
    "target_obscured": ("blocked", "tool", "落点最顶层触摸节点不属于目标，重新定位后仍被遮挡，未下发"),
    "state_indeterminate": ("blocked", "environment", "下发前查不到锁屏/前台状态（查询失败），未下发；需要人确认手机状态后重跑"),
}
# 这些动作会作用到"当前前台"：前台不是被测 APP 时不下发，避免点到/输入到用户正在用的其他应用
FOREGROUND_BOUND = ("tap", "input", "swipe", "back", "home")


def classify(att: dict) -> tuple:
    st = att["status"]
    error = att.get("execution_error") or (att.get("post") or {}).get("execution_error") or (att.get("locate") or {}).get("execution_error") or {}
    if st == "preparation_error":
        return "error", error.get("attribution", "tool"), att.get("reason", "操作前弹窗处置失败")
    if st in ("condition_false", "condition_unknown", "condition_error", "read_source_unknown"):
        outcome, attribution, reason = STATUS_MAP[st]
        return outcome, error.get("attribution", attribution), att.get("reason", reason)
    if st in ("observation_failed", "unobservable", "foreground_unknown", "state_indeterminate", "target_stale", "target_unresolved", "stop_not_confirmed") and error:
        return "error", error["attribution"], "所需观测采集失败：" + str(error["error_class"])
    if st == "input_required":
        return "blocked", "environment", att["reason"]
    if st == "invalid_step" and att.get("reason"):
        return "blocked", "tool", att["reason"]
    if st == "device_error":
        ec = error.get("error_class") or att.get("error_class")
        return ("blocked" if ec in {"screen_locked", "screen_off"} else "error", error_attribution(ec),
                f"设备/执行器错误 {ec}: {error.get('error') or (att.get('dispatch') or {}).get('error') or ''}".strip())
    return STATUS_MAP.get(st, ("blocked", "tool", f"未知状态 {st}"))


class Executor:
    def __init__(self, ctx):
        self.ctx = ctx

    @staticmethod
    def _when_guards(step):
        return [*step.get("_condition_guards", []), *([step["when"]] if step.get("when") is not None else [])]

    def _operation_condition(self, step, obs, tree):
        from .tree import UiTree
        from .util import read_json
        from .verdict import select_applicability
        ctx = self.ctx
        def load(oid):
            directory = ctx.store.dir / "obs" / oid
            observed = read_json(directory / "observation.json")
            path = directory / "tree.json"
            usable = observed.get("fresh") is not False and (observed.get("tree") or {}).get("status") != "stale"
            return observed, UiTree(read_json(path)) if usable and path.exists() else None
        record = select_applicability(self._when_guards(step), ctx.conditions, ctx.step_results, load, ctx.app, obs, tree)
        if record.get("status") != "error":
            bindings = list(step.get("_condition_bindings", []))
            selected = step.get("_selected_condition") or {}
            bindings += [{"condition": r.get("condition"), "scope": r.get("scope"), "parent": step["id"]}
                         for r in selected.get("records", []) if r.get("scope")]
            for binding in bindings:
                matching = [r for r in record.get("records", []) if r.get("condition") == binding["condition"]]
                if any(r.get("scope") != binding["scope"] for r in matching):
                    record = {**record, "status": "unknown", "reason": "操作选择的身份范围已变化，不能自动切换车辆/任务/账户",
                              "parent_scope": binding}
        return record

    def _check_condition(self, step, obs, tree, phase, att, condition=None):
        """记录当前检查点的条件；各检查时点仍独立取证。"""
        if not self._when_guards(step):
            return True
        condition = self._operation_condition(step, obs, tree) if condition is None else condition
        att["condition"] = condition
        self.ctx.store.event("condition", step=step["id"], phase=phase, condition=condition)
        if condition["status"] == "true":
            return True
        att.update(status="condition_" + condition["status"], obs_after=obs["obs_id"], reason=condition["reason"])
        if condition.get("execution_error"):
            att["execution_error"] = condition["execution_error"]
        return False

    def _child_condition_context(self, step):
        selected = step.get("_selected_condition") or {}
        bindings = list(step.get("_condition_bindings", []))
        if step.get("when") is not None:
            name = step["when"].get("condition")
            record = next((r for r in selected.get("records", []) if r.get("condition") == name), selected)
            bindings.append({"condition": name, "scope": record.get("scope"), "parent": step["id"]})
        return {"_condition_guards": self._when_guards(step), "_condition_bindings": bindings}

    @staticmethod
    def _required_skip(base, child, children):
        """A required capability did not execute; do not start its dependants."""
        return {**base, "outcome": "pending", "attribution": "requirement_unclear",
                "reason": f"必需子步骤 {child['step_id']} 的操作条件不满足，未执行；停止后续子步骤",
                "final_status": "condition_unknown", "condition": child.get("condition"),
                "sub_steps": children, "attempts": [a for item in children for a in item["attempts"]],
                "adapted": any(item.get("adapted") for item in children),
                "recoveries": [r for item in children for r in item.get("recoveries", [])],
                "first_failure": next((item["first_failure"] for item in children if item.get("first_failure")), None),
                "final_obs": child.get("final_obs")}

    # ---- 对外入口 ----
    def run_step(self, step: dict, case_id: str) -> dict:
        """Operation selection never substitutes for an executed product check."""
        ctx, condition = self.ctx, None
        from .readrefs import has_read_ref, resolve_text, structure_issues as read_reference_issues
        # Invalid references fail before observation. Selected inputs with missing
        # data cannot trigger positioning, clearing, or dialog recovery.
        if has_read_ref(step) and not ctx.safety_hold:
            declared = getattr(ctx, "read_case", None)
            main = (declared or {}).get("steps", [])
            original = next((s for s in main if s.get("id") == step.get("id")), None)
            problems = read_reference_issues({"steps": [step]}, ctx.app, sources=False)
            if declared:
                problems += read_reference_issues(declared, ctx.app)
            if (not declared or original is None or original.get("action") != "input"
                    or original.get("text") != step.get("text") or step.get("_safety")
                    or step.get("_flow_params") is not None or problems):
                result = {"step_id": step["id"], "action": step["action"], "critical": True,
                          "outcome": "blocked", "attribution": "tool", "final_status": "invalid_step",
                          "reason": "read 引用只允许当前用例声明的顶层输入文本", "attempts": [], "recoveries": [],
                          "adapted": False, "oracle_refs": step.get("oracle_refs", [])}
                ctx.step_results[step["id"]] = result
                return result
        conditional_step = bool(self._when_guards(step))
        # 保留既有暂停关口的优先级：未知条件不能触发取证或绕过安全停止。
        if self._when_guards(step) and not (ctx.safety_hold and not step.get("_safety")):
            obs, tree = ctx.observe(f"{step['id']}:when")
            condition = self._operation_condition(step, obs, tree)
            ctx.store.event("condition", step=step["id"], phase="when", condition=condition)
            if condition["status"] != "true":
                error = condition.get("execution_error") or {}
                result = {"step_id": step["id"], "action": step["action"],
                          "critical": True if conditional_step and condition["status"] != "false" else bool(step.get("critical", True)),
                          "oracle_refs": step.get("oracle_refs", []), "attempts": [], "recoveries": [], "adapted": False,
                          "outcome": {"false": "skipped", "unknown": "pending", "error": "error"}[condition["status"]],
                          "final_status": "condition_" + condition["status"], "final_obs": obs["obs_id"],
                          "condition": condition, "reason": condition["reason"],
                          "attribution": error.get("attribution", "requirement_unclear")}
                if error:
                    result["execution_error"] = error
                ctx.step_results[step["id"]] = result
                return result
        # A false operation condition does not need a value. Once selected,
        # missing reads stop before primitive preparation or any device action.
        if has_read_ref(step) and not ctx.safety_hold:
            binding = resolve_text(step["text"], ctx.step_results,
                                   subst=(lambda text: ctx.params.subst(text)) if ctx.params else None)
            ctx.read_inputs[step["id"]] = binding
            if binding["status"] != "available":
                result = {"step_id": step["id"], "action": step["action"], "critical": True if conditional_step else bool(step.get("critical", True)),
                          "outcome": "error" if binding["status"] == "error" else "pending",
                          "attribution": binding.get("execution_error", {}).get("attribution", "requirement_unclear"),
                          "final_status": "read_source_" + binding["status"], "reason": binding["reason"],
                          "attempts": [], "recoveries": [], "adapted": False,
                          "oracle_refs": step.get("oracle_refs", []), "read_sources": binding["sources"]}
                if binding.get("execution_error"):
                    result["execution_error"] = binding["execution_error"]
                ctx.step_results[step["id"]] = result
                return result
        # 可选的是分支选择；内联动作与复合能力一旦选中，都必须完成。
        selected = {**step, "_selected_condition": condition}
        if conditional_step:
            selected["critical"] = True
        result = self._run_step(selected, case_id)
        if condition is not None:
            result.setdefault("condition", condition)
            if result.get("outcome") in ("pending", "error", "skipped"):
                def incomplete(items):
                    for item in items:
                        if (item.get("condition") or {}).get("status") not in (None, "true"):
                            yield item["condition"]
                        yield from incomplete(item.get("sub_steps") or [])
                final_condition = next(incomplete(result.get("sub_steps") or []), None)
                if final_condition:
                    result.update(condition=final_condition, condition_at_entry=condition)
        ctx.step_results[step["id"]] = result
        return result

    def _run_step(self, step: dict, case_id: str) -> dict:
        from .conditions import validate_input_obstruction
        att = {"n": 1, "action": step["action"], "step_id": step["id"], "_safety": bool(step.get("_safety"))}
        held = self.ctx.dispatcher.held_step(att)
        if held is not None:
            att["dispatch"] = {"sent": held.sent, "executor_ok": held.ok, "error_class": held.error_class}
            return self._summarize(step, [att], [])
        # 直接执行 flow/recipe 的调用方也先检查整段定义，不能先执行有效子步骤再发现后面的错误。
        issues = execution_structure_issues({"steps": [step]}, self.ctx.app, read_sources=False)
        for child, _, where in iter_case_steps({"steps": [step]}, self.ctx.app):
            issues += [{**x, "field": where + "." + x["field"]}
                       for x in validate_input_obstruction(child, self.ctx.conditions)]
        if issues:
            att.update(status="invalid_step", reason=f"执行结构无效：{issues}")
            return self._summarize(step, [att], [])
        if step["action"] == "ensure_state":
            return self._ensure_state(step, case_id)
        if step["action"] == "flow":
            return self._flow(step, case_id)
        # 监视是一段连续观测，整体重跑会重复计时，不重试
        max_retry = 0 if step["action"] == "monitor" or "capture_transient" in step else min(int(step.get("retry", 1)), 2)
        attempts, recoveries, n, obstruction_used = [], [], 0, False
        while True:
            n += 1
            att = self._attempt(step, n, case_id)
            att["step_id"] = step["id"]
            attempts.append(att)
            self.ctx.store.event("attempt", step=step["id"], n=n, status=att["status"])
            if att["status"] == "ok":
                break
            if step.get("dismiss_obstruction_when") and att["status"] == "target_obscured" and not obstruction_used:
                obstruction_used = True
                rec = self._recover_input_obstruction(step, att, case_id)
            elif n <= max_retry and not obstruction_used:
                rec = self._recover(step, att, case_id)
            else:
                break
            rec["after_attempt"] = n
            recoveries.append(rec)
            self.ctx.store.event("recovery", step=step["id"], after_attempt=n,
                                 **{k: v for k, v in rec.items() if k in (
                                     "action", "performed", "reason", "interrupt", "action_required", "effect_unknown")})
            if not rec["performed"] or rec.get("retry_original") is False or (rec.get("post") or {}).get("status") == "ok":
                break
        return self._summarize(step, attempts, recoveries)

    # ---- 单次尝试 ----
    def _attempt(self, step: dict, n: int, case_id: str) -> dict:
        if "capture_transient" not in step:
            return self._attempt_plain(step, n, case_id)
        from .transient import TransientCapture
        ctx = self.ctx
        # Do not start collection for a held or motion-limited attempt.
        if ctx.safety_hold or ctx.motion.locked() or ctx.actions >= ctx.policy.max_actions_per_run:
            return self._attempt_plain(step, n, case_id)
        capture = TransientCapture(ctx, step, n)
        previous = getattr(ctx, "transient_capture", None)
        try:
            if not capture.start():
                result = {"n": n, "ts": now_iso(), "action": step["action"], "step_id": step["id"],
                          "status": "transient_unavailable", "reason": capture.summary.get("reason")}
            else:
                ctx.transient_capture = capture
                result = self._attempt_plain(step, n, case_id)
        finally:
            ctx.transient_capture = previous
            try:
                summary = capture.finish()
            except Exception as exc:
                # A failed evidence write must not discard or repeat the tap.
                summary = {**capture.summary, "status": "error", "frames": [],
                           "error": f"{type(exc).__name__}: {exc}"}
                if 'result' in locals() and result.get("status") == "ok":
                    result.update(status="transient_capture_error", execution_error={
                        "stage": "transient_capture", "attribution": "tool",
                        "error_class": "transient_capture_error", "error": summary["error"]})
        result["transient"] = summary
        return result

    def _attempt_plain(self, step: dict, n: int, case_id: str) -> dict:
        ctx, kind = self.ctx, step["action"]
        att = {"n": n, "ts": now_iso(), "action": kind, "step_id": step["id"], "_safety": bool(step.get("_safety"))}
        error = swipe_options_error(step)
        if error:
            att.update(status="invalid_step", reason=error)
            return att
        safety = bool(step.get("_safety"))  # 安全停止步骤：不受动作预算与运动超时拦截
        if not safety and ctx.motion.locked():
            att.update(status="motion_timeout", motion={"elapsed_s": ctx.motion.elapsed(), "max_s": ctx.motion.max_s,
                                                        "locked": ctx.motion.timeout})
            return att
        mark = ctx.motion.breaches  # 本次尝试之后才发生的超时（含超时后才观测到停止）都算本步骤的
        att["motion_mark"] = mark
        if kind == "monitor":
            return self._monitor(step, att, mark)
        if kind in FOREGROUND_BOUND and ctx.device.screen_locked() is True:
            if not self._foreground_ok(att):
                return att
        obs, tree = ctx.observe(f"{step['id']}#{n}:before")
        att.update(obs_before=obs["obs_id"], state_before=obs["page"]["state"])
        if not self._check_condition(step, obs, tree, "before_interrupts", att):
            return att
        if kind not in ("launch", "stop_app", "wait"):
            obs, tree, dismissed = self._clear_interrupts(obs, tree, f"{step['id']}#{n}:before", safety=safety,
                                                        condition_step=step if self._when_guards(step) else None)
            if dismissed:
                att["dismissed"] = dismissed
                att["state_before"] = obs["page"]["state"]
                if dismissed[-1].get("gate") == "preparation_error":
                    error = dismissed[-1]["execution_error"]
                    att.update(status="preparation_error", execution_error=error, error_class=error["error_class"],
                               obs_after=obs["obs_id"], reason="操作前弹窗处置失败：" + str(error["error_class"]))
                    return att
                if str(dismissed[-1].get("gate", "")).startswith("condition_"):
                    condition = dismissed[-1]["condition"]
                    att.update(condition=condition, status=dismissed[-1]["gate"], obs_after=obs["obs_id"],
                               reason=condition["reason"])
                    if condition.get("execution_error"):
                        att["execution_error"] = condition["execution_error"]
                    return att
                gate_status = dismissed[-1].get("gate")
                if gate_status in ("budget_exhausted", "motion_timeout", "safety_hold") or (
                        self._when_guards(step) and gate_status in STATUS_MAP and STATUS_MAP[gate_status][0] == "blocked"):
                    att["status"] = gate_status
                    return att
        error = collection_error(obs)
        if error and kind in ("tap", "input", "swipe", "read"):
            # 观测本身失败时不定位、不下发：否则会把连接问题记成"目标找不到"
            att.update(status="observation_failed", error_class=error["error_class"],
                       execution_error={**error, "stage": "observe_before"})
            return att
        if not self._check_condition(step, obs, tree, "attempt", att):
            return att
        res = None
        if kind == "read":
            if step.get("target") is None and step.get("read_scope") == "page":
                loc = {"status": "found", "target": None, "point": None, "basis": "tree"}
            else:
                tid, target = self._target(step)
                loc, obs, tree = self._locate(tid, target, obs, tree, step)
            att["locate"] = loc
            if loc.get("condition"):
                att["condition"] = loc["condition"]
            if loc["status"] != "found":
                att["status"] = _unresolved(loc)
                from .readrefs import used_sources
                if step["id"] in used_sources(getattr(ctx, "read_case", None) or {}):
                    if loc["status"] == "error" and not loc.get("execution_error"):
                        att.update(status="observation_failed", obs_after=obs["obs_id"],
                                   execution_error={"attribution": "tool", "stage": "read_source",
                                                    "error_class": "read_source_locator_error", "error": loc.get("attempts")})
                    elif loc["status"] in ("not_found", "ambiguous", "partially_visible") and not loc.get("execution_error"):
                        reason = "读取来源目标未唯一、完整可见地定位"
                        att.update(status="read_source_unknown", obs_after=obs["obs_id"], reason=reason,
                                   read_binding={"status": "unavailable", "obs": obs["obs_id"], "reason": reason})
                return att
            if self._when_guards(step):
                previous = att.get("condition") or {}
                records = previous.get("records") or [previous]
                expired = any(r.get("max_age_s") is not None and ctx.clock() - r.get("clock_s", 0) > r["max_age_s"] for r in records)
                if any(r.get("action_epoch") != ctx.actions for r in records) or expired:
                    condition = {**previous, "status": "unknown", "reason": "读取定位后条件已因动作或有效期失效，须重新取证"}
                else:
                    condition = self._operation_condition(step, obs, tree)
                if not self._check_condition(step, obs, tree, "read", att, condition):
                    return att
            att["measurement"] = _measure(tree, loc, step)
            from .readrefs import qualify, used_sources
            att["read_binding"] = qualify(tree, loc, step, obs, att["measurement"])
            ctx.store.event("measure", step=step["id"], **{k: v for k, v in att["measurement"].items() if k != "raw"})
            att.update(status="ok" if att["measurement"]["value"] is not None else "postcondition_not_met",
                       obs_after=obs["obs_id"])
            if (step["id"] in used_sources(getattr(ctx, "read_case", None) or {})
                    and att["read_binding"]["status"] != "available"):
                att.update(status="read_source_unknown", reason=att["read_binding"]["reason"])
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
            if loc.get("condition"):
                att["condition"] = loc["condition"]
            if loc["status"] != "found":
                att["status"] = _unresolved(loc)
                return att
            if step.get("revalidate"):
                loc, obs, tree = self._revalidate(tid, target, loc, step, n, att)
                if att.get("status"):
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
                loc = {**loc, "point": self._point_in(loc, step, n, tree), "container_point": True}
                att["locate"] = loc
            pol = ctx.policy.check_tap(tree, loc["point"], loc["basis"], target,
                                       ctx.app.states.deny_for_page(obs["page"]), ctx.authorized,
                                       node=_tree_node(tree, loc),
                                       confirmation_sources=ctx.app.states.confirmation_sources(
                                           tree, tid, target, obs["page"], obs.get("layout")),
                                       layout=obs.get("layout"), foreground=obs.get("foreground"))
            att["policy"] = pol
            if not pol["allowed"]:
                att["status"] = "policy_denied"
                return att
            att["resolved"] = _resolved(loc, obs)
            if not self._foreground_ok(att, step.get("revalidate")):
                return att
            res = self._send_target("tap", loc, obs, tree, target, tid, step, att)
            if att.get("status"):
                return att
            if res.sent is not False and target.get("side_effect") == "vehicle_motion":
                # 命令已发出就挂看门狗，成败不论：HDC 超时/失败时车可能已收到命令，
                # 不能因为执行器没确认成功就不监护——停止要由后续观测确认，而不是由下发结果推断。
                # 唯一入口：vehicle_motion 的挂载只在这里（观测到运动状态时由 MotionGuard 自己挂）
                ctx.motion.arm(step["id"], pol.get("label") or tid, obs["page"]["state"])
        elif kind == "launch":
            res = ctx.dispatcher.send("launch", ctx.app.bundle, ctx.app.ability,
                                      preconditions=ctx.dispatcher.app_conditions(kind), att=att)
            if att.get("status"):
                return att
        elif kind == "resume":
            # 从后台拉回：与 launch 同一条命令；区别在于判定"回到原页面"而不是"冷启动"
            att["process_alive_before"] = ctx.device.process_alive(ctx.app.bundle)
            res = ctx.dispatcher.send("launch", ctx.app.bundle, ctx.app.ability,
                                      preconditions=ctx.dispatcher.app_conditions(kind), att=att)
            if att.get("status"):
                return att
        elif kind == "wait" and step.get("duration_s"):
            dur = min(float(step["duration_s"]), float(ctx.policy.supervision.get("max_monitor_s", 600)))
            waited = 0.0
            while waited < dur and not ctx.motion.over():
                ctx.sleep(min(1.0, dur - waited))
                waited += min(1.0, dur - waited)
            att["waited_s"] = round(waited, 2)
        elif kind in ("back", "home"):
            if step.get("_obstructed_input") and not self._obstruction_matches(step["_obstructed_input"],
                    step["when"], obs, tree):
                att.update(status="target_obscured", reason="当前接收者不是已声明的遮挡容器，未返回")
                return att
            if not self._foreground_ok(att):
                return att
            res = ctx.dispatcher.send("key", kind, preconditions=ctx.dispatcher.app_conditions(kind), att=att)
            if att.get("status"):
                return att
        elif kind == "swipe":
            if not self._foreground_ok(att):
                return att
            if step.get("start") is not None:
                res, att["swipe"] = self._relative_swipe(step, att)
            elif step.get("random"):
                res, att["swipe"] = self._random_swipe(tree, obs.get("layout"), step, att)
            else:
                res, att["swipe"] = self._swipe(step.get("direction", "up"), tree, obs.get("layout"), step.get("within"), att)
        elif kind == "stop_app":
            res = ctx.dispatcher.send("stop_app", ctx.app.bundle, preconditions={}, att=att)
        elif kind == "input":
            tid, target = self._target(step)
            raw = step.get("text", "")
            from .readrefs import has_read_ref
            binding = getattr(ctx, "read_inputs", {}).get(step["id"]) if has_read_ref(raw) else None
            text = binding["text"] if binding else ctx.params.subst(raw, step.get("_flow_params")) if ctx.params else raw
            if binding:
                att["read_sources"] = binding["sources"]
            if not isinstance(text, str) or not text.strip() or (not binding and re.search(r"\$\{(?:var|param|secret|read):[^}]*\}", text)):
                att.update(status="input_required", reason="所选输入缺值或引用未解析；保留当前页面，补齐外部输入后重新观察重入",
                           input={"ref": raw, "available": False},
                           action_required=f"请补齐外部输入 {raw}，然后重新运行并观察当前页面；不要从历史步骤号续发")
                return att
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
            if loc.get("condition"):
                att["condition"] = loc["condition"]
            if loc["status"] != "found":
                att["status"] = _unresolved(loc)
                return att
            if step.get("revalidate"):
                loc, obs, tree = self._revalidate(tid, target, loc, step, n, att)
                if att.get("status"):
                    return att
            if not self._inline_input_ok(tid, target, loc, obs, tree, att):
                return att
            att["input"] = {"ref": raw, "secret": is_ref, "length": len(text)}  # 只记引用与长度，不记值
            att["resolved"] = _resolved(loc, obs)
            if not self._foreground_ok(att, step.get("revalidate")):
                return att
            if step.get("clear"):
                # 真机输入是追加：先删掉框里已有的文字（按当前树里的长度 + 余量），再输入
                node = _tree_node(tree, loc)
                n_del = len((node.text if node is not None else "") or "") + 2
                cr = self._send_target("clear_text", loc, obs, tree, target, tid, step, att, n_del)
                att["clear"] = {"deleted": n_del, "sent": cr.sent, "ok": cr.ok, "error": cr.error}
                ctx.store.event("clear_text", step=step["id"], n=n, deleted=n_del, ok=cr.ok, sent=cr.sent)
                if not cr.ok:
                    # 输入框已被点过、可能删了一部分：记为已下发未确认，会话/判定都按效果未知处理
                    att["dispatch"] = {"op": cr.op, "sent": cr.sent, "executor_ok": False, "error_class": cr.error_class,
                                       "error": cr.error, "raw": cr.raw, "duration_ms": cr.duration_ms}
                    att.update(status=cr.error_class if cr.error_class in STATUS_MAP else "device_error", error_class=cr.error_class)
                    return att
                # 点进输入框会弹键盘、触发布局变化：清空后重新观察并定位（不滚动），按最新的树输入，不沿用清空前的坐标
                obs, tree = ctx.observe(f"{step['id']}#{n}:after-clear", screenshot=False)
                loc, obs, tree = self._locate(tid, target, obs, tree, step, scroll_ok=False)
                att["clear"]["relocate"] = {"obs": obs["obs_id"], "status": loc["status"], "point": loc.get("point")}
                if loc["status"] != "found":
                    # 清空已下发、文字未输入：保留部分执行事实，不整步重复清空/输入。
                    att["dispatch"] = {"op": cr.op, "sent": cr.sent, "executor_ok": cr.ok, "error_class": None,
                                       "error": "清空后输入框无法重新定位，文字未输入", "duration_ms": cr.duration_ms}
                    att.update(status=_unresolved(loc), obs_after=obs["obs_id"])
                    return att
                att["locate"] = {**att["locate"], "after_clear": loc}
                if not self._inline_input_ok(tid, target, loc, obs, tree, att):
                    return att
                att["resolved"] = _resolved(loc, obs)
            res = self._send_target("input_text", loc, obs, tree, target, tid, step, att, text)
            if att.get("status"):
                if att["status"] in ("target_obscured", "clear_not_confirmed"):
                    att["dispatch"] = {"op": res.op, "sent": res.sent, "executor_ok": res.ok,
                                       "error_class": res.error_class, "error": res.error,
                                       "duration_ms": res.duration_ms}
                return att
        elif kind != "wait":
            att["status"] = "invalid_step"
            return att
        if res is not None:
            att["dispatch"] = {"op": res.op, "sent": res.sent, "executor_ok": res.ok, "error_class": res.error_class,
                               "error": res.error, "duration_ms": res.duration_ms}
            if not res.ok:
                att["dispatch"]["raw"] = res.raw
            if not res.sent and res.error_class in STATUS_MAP:
                att.update(status=res.error_class, error_class=res.error_class)
                return att
            if not res.ok:
                o, _ = ctx.observe(f"{step['id']}#{n}:after-error")
                att.update(status="device_error", error_class=res.error_class, obs_after=o["obs_id"])
                return att
        post = self._post(step, n, case_id, was_in_app=att["state_before"] != "out_of_app", mark=mark)
        stop_sc = post.pop("stop_check", None)
        att.update(post=post, obs_after=post["obs_id"], status=post["status"])
        if stop_sc is not None:
            att["stop_check"] = stop_sc  # Issue #15：停止断言证据挂在尝试上，随失败记录落盘
        if kind == "resume":
            # 后台期间进程被回收时，拉起就是冷启动：页面是否恢复由 expect_state 判定，这里只记录事实
            att["resume"] = {"process_alive_before": att.get("process_alive_before"),
                             "cold_start": att.get("process_alive_before") is False}
        return att

    # ---- 监视：一段时间内连续观测（运动中信息、TOR/接管提示、异常显示）----
    def _monitor(self, step: dict, att: dict, mark: int = 0) -> dict:
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
        # 进程存活取自每次观测本身（页面识别只在退出 APP 时带这个字段）；起点沿用上一步最后一次观测
        prev_texts, prev_alive, states, first_obs = set(), (ctx.last_obs or {}).get("process_alive"), [], None
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
            alive = obs.get("process_alive")
            died = prev_alive is True and alive is False
            anomalies = [i["id"] for i in page.get("interrupts", []) if i["class"] == "anomaly"]
            blocking = [i["id"] for i in page.get("interrupts", []) if i["class"] == "blocking"]
            if until_gone and any(until_gone.search(x) for x in texts):
                gone_seen = True
            error = collection_error(obs)
            errors += 1 if error else 0
            if error:
                att["execution_error"] = {**error, "stage": "monitor"}
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
            if alive is not None:
                prev_alive = alive
            if died and not step.get("allow_process_exit"):
                status, stopped_by = "anomaly", "process_died"
            elif anomalies:
                status, stopped_by = "anomaly", f"interrupt:{anomalies[0]}"
            elif blocking:
                status, stopped_by = "interrupted", f"interrupt:{blocking[0]}"
            elif set(fired) & abort_on:
                status, stopped_by = "monitor_abort", f"abort_on:{sorted(set(fired) & abort_on)[0]}"
            elif ctx.motion.breached_since(mark):
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
        if errors and (errors == len(timeline) or status == "postcondition_not_met"):
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
    def _locate(self, tid, target, obs, tree, step, scroll_ok: bool = True) -> tuple:
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
        scroll = target.get("scroll") if scroll_ok else None
        swipes, trail = 0, []
        while loc["status"] in ("not_found", "partially_visible") and scroll and swipes < int(scroll.get("max", 3)):
            # 滚动查找也是手势：和正式下发一样先确认锁屏与前台，查不到/不对就停，不滑
            gate = {"action": "swipe", "step_id": step.get("id"), "_safety": bool(step.get("_safety"))}
            if self._when_guards(step):
                condition = self._operation_condition(step, obs, tree)
                ctx.store.event("condition", step=step["id"], phase="locate_scroll", condition=condition)
                gate["condition"] = condition
                loc["condition"] = condition
                if condition["status"] != "true":
                    loc.update(status="blocked_state", gate={"status": "condition_" + condition["status"]},
                               execution_error=condition.get("execution_error"))
                    break
            if not self._foreground_ok(gate):
                loc = {**loc, "status": "blocked_state", "gate": {k: gate.get(k) for k in (
                    "status", "error_class", "foreground_check", "state_check", "action_required") if k in gate},
                       "execution_error": gate.get("execution_error")}
                break
            swipes += 1
            before = tree.fingerprint() if (tree is not None and tree.status == "ok") else None
            r, info = self._swipe(scroll.get("direction", "up"), tree, lay, scroll.get("within"), gate)
            info["sent"] = r.sent
            obs, tree = ctx.observe(f"{step['id']}:scroll{swipes}")
            lay = obs.get("layout")
            after = tree.fingerprint() if (tree is not None and tree.status == "ok") else None
            info.update(n=swipes, ok=r.ok, error=r.error, error_class=r.error_class, raw=r.raw if not r.ok else None,
                        content_changed=before != after if before else None)
            trail.append(info)
            ctx.store.event("scroll_search", target=tid, **info)
            if not r.ok and str(gate.get("status", "")).startswith("condition_"):
                loc.update(status="blocked_state", gate={"status": gate["status"]}, condition=gate["condition"],
                           execution_error=gate.get("execution_error"))
                break
            if r.error_class in ("budget_exhausted", "motion_timeout", "safety_hold"):
                loc = {**loc, "status": "blocked_state", "gate": {"status": r.error_class}}
                break
            if not r.ok and r.error_class in STATUS_MAP and STATUS_MAP[r.error_class][0] == "blocked":
                loc = {**loc, "status": "blocked_state", "gate": {"status": r.error_class},
                       "execution_error": gate.get("execution_error")}
                break
            loc = resolve(tid, target, tree, ctx.observer.screenshot_abs(obs), ctx.app.dir, pref, point, lay)
            if not r.ok:
                loc["execution_error"] = gate.get("execution_error") or {
                    "stage": "locate_scroll", "error_class": r.error_class, "error": r.error,
                    "attribution": error_attribution(r.error_class), "evidence": {"obs": obs["obs_id"], "dispatch": info}}
                loc["status"] = "error"
            if not r.ok or (before and before == after):
                if r.ok:
                    loc["scroll_end"] = True  # 滑动后内容没有变化：已到底或滑动未生效，不再继续
                break
        if swipes:
            loc["scroll_swipes"], loc["scroll"] = swipes, trail
        if note:
            loc["point_rejected"] = note
        loc["obs"] = obs["obs_id"]
        if loc["status"] != "found" and collection_error(obs):
            loc["execution_error"] = {**collection_error(obs), "stage": "locate"}
        loc["layout_key"] = layout_key(lay)
        return loc, obs, tree

    def _foreground_ok(self, att: dict, fresh: bool = False) -> bool:
        if att["action"] not in FOREGROUND_BOUND:
            return True
        return self.ctx.dispatcher.app_state(att, fresh)

    def _send_target(self, op, loc, obs, tree, target, tid, step, att, *extra):
        ctx = self.ctx
        def recheck_condition(o, t):
            if not self._when_guards(step):
                return True
            previous = att.get("condition") or {}
            # 辅助滚动/自动解锁之后先停止原操作；清空输入是已计划的子序列，
            # 有独立 after-clear 观测时重新取证，不能复用清空前条件。
            records = previous.get("records") or [previous]
            if any(r.get("action_epoch") != ctx.actions for r in records) and not (op == "input_text" and (att.get("clear") or {}).get("ok")):
                return True  # Dispatcher 的最终 epoch 门禁保留失效原因。
            return self._check_condition(step, o, t, "dispatch", att)
        if not recheck_condition(obs, tree):
            return DeviceResult(op, False, False, att["status"], att["reason"])
        if op == "input_text" and (att.get("clear") or {}).get("ok") is True:
            if not self._cleared_input_ok(target, loc, obs, tree, att, "after_clear"):
                return DeviceResult(op, False, False, att["status"], att["execution_error"]["error"])
        def pack(loc, obs, tree):
            return {"loc": loc, "tree": tree, "target": target, "target_ref": tid, "page": obs["page"],
                    "state_deny": ctx.app.states.deny_for_page(obs["page"]), "layout": obs.get("layout")}
        def refresh():
            o, t = ctx.observe(f"{step['id']}:dispatch-relocate", screenshot=False)
            l = resolve(tid, target, t, ctx.observer.screenshot_abs(o), ctx.app.dir,
                        None, step.get("point"), o.get("layout"))
            if l["status"] != "found":
                if collection_error(o):
                    att["execution_error"] = {**collection_error(o), "stage": "dispatch_relocate"}
                return None
            if step.get("at") or step.get("random_point"):
                l = {**l, "point": self._point_in(l, step, 1, t), "container_point": True}
            att["resolved"] = _resolved(l, o)
            if not recheck_condition(o, t):
                return None
            if op == "input_text" and (att.get("clear") or {}).get("ok") is True:
                if not self._cleared_input_ok(target, l, o, t, att, "dispatch_refresh"):
                    return None
            return pack(l, o, t)
        capture = getattr(ctx, "transient_capture", None)
        return ctx.dispatcher.send(op, *loc["point"], *extra, preconditions={**ctx.dispatcher.app_conditions(att["action"]), "target_subtree": True}, att=att, tap=pack(loc, obs, tree), refresh=refresh,
                                   transient=capture if capture is not None and op == "tap" and capture.step_id == step["id"] else None)

    def _cleared_input_ok(self, target, loc, obs, tree, att, phase):
        """Reject known non-hint text, without treating unknown or empty UI text as an empty value."""
        check = {"phase": phase, "obs": obs.get("obs_id"), "status": "unknown", "verified": None,
                 "source": "unavailable"}
        att["clear"].setdefault("value_checks", []).append(check)
        def unknown(reason):
            check["reason"] = reason
            return True  # This bounded check does not change unobservable/secret input contracts.
        if target.get("secret") or (att.get("input") or {}).get("secret"):
            return unknown("secret_not_read")
        if tree is None or tree.status != "ok" or obs.get("fresh") is False or collection_error(obs):
            return unknown("observation_unusable")
        node = _tree_node(tree, loc)
        if node is None or node.type not in ("TextInput", "TextArea", "SearchField") or not tree.shown(node):
            return unknown("not_direct_editable")
        attrs = node.attrs
        if any(k in attrs and str(attrs[k]).lower() not in ("false", "0", "")
               for k in ("password", "isPassword")):
            return unknown("masked_not_read")
        check["node"] = {"idx": node.idx, "type": node.type}
        if (str(attrs.get("accessibilityGroup", "false")).lower() not in ("false", "0", "")
                or any(k in attrs and (not isinstance(attrs[k], str) or attrs[k])
                       for k in ("accessibilityText", "accessibilityCustomComponentType"))
                or any(n.idx != node.idx and tree.is_within(n, node) and tree.shown(n)
                       and (n.text.strip() or n.description.strip()) for n in tree.nodes)):
            return unknown("aggregated_or_custom_content")
        if "text" not in attrs or not isinstance(attrs["text"], str):
            return unknown("text_attribute_unavailable")
        text = attrs["text"]
        check.update(source="ui_tree.attributes.text", text_length=len(text))
        prompts = {k: attrs[k] for k in ("hint", "placeholder") if k in attrs}
        if not prompts or any(not isinstance(value, str) for value in prompts.values()):
            return unknown("independent_prompt_unavailable")
        check["prompt_sources"] = list(prompts)
        if len(set(prompts.values())) > 1:
            return unknown("conflicting_prompt_sources")
        if "originalText" in attrs and (not isinstance(attrs["originalText"], str)
                                        or attrs["originalText"] != text):
            return unknown("conflicting_text_sources")
        if not text.strip() or text.strip() in (value.strip() for value in prompts.values()):
            return unknown("empty_or_prompt_text")
        check.update(status="non_hint_text", verified=False, reason="non_hint_text_observed")
        error = STATUS_MAP["clear_not_confirmed"][2]
        att.update(status="clear_not_confirmed", obs_after=obs.get("obs_id"), action_required=True,
                   execution_error={"stage": "input_clear_verification", "error_class": "clear_not_confirmed",
                                    "error": error, "attribution": "tool", "evidence": check})
        return False

    def _clear_interrupts(self, obs: dict, tree, label: str, limit: int = 3, safety: bool = False,
                          condition_step=None) -> tuple:
        """弹窗是常态（资源包下载、版本更新、权限提示……）：页面上只有"已评审的良性中断"时，
        按声明的关闭方式（dismiss_target / dismiss_text）点掉再观察，最多连关 limit 个；
        同时出现异常/阻塞/未评审中断时一个都不动，交给后续判定。每次关闭都记事件，弹窗画面的观测保留。"""
        ctx, done = self.ctx, []
        def retain_unlock(record, gate):
            if gate.get("unlock"):
                record["unlock"] = gate["unlock"]
                record["action_required"] = gate.get("action_required") or gate["unlock"].get("action_required")
                error = gate.get("execution_error") or {}
                evidence = error.get("evidence") or {}
                if "sent" in evidence and evidence["sent"] is not False:
                    record.update(dismiss_sent=record.get("sent", False), sent=evidence["sent"])
                elif ctx.actions != obs.get("action_epoch"):
                    record.update(dismiss_sent=record.get("sent", False), sent=True)
        for k in range(limit):
            ints = obs["page"].get("interrupts") or []
            if any(not (i["class"] == "benign" and i["review"] == "reviewed") for i in ints):
                break
            if ints:
                iid = ints[0]["id"]
                spec = ctx.app.states.interrupts[iid]
                if spec.get("dismiss_target"):
                    tid, target = ctx.app.target(spec["dismiss_target"])
                else:
                    tid, target = f"dismiss:{iid}", {"desc": f"关闭 {iid}", "side_effect": "dismiss_dialog",
                                                     "strategies": [{"kind": "text", "value": spec["dismiss_text"]}]}
            else:
                # 没登记的弹窗（文案变体、新弹窗）：按结构认出 Dialog，里面有"推迟/拒绝"语义的按钮就点掉，
                # 弹窗文字和截图留在事件里，事后补登记；没有这类按钮的弹窗（例如被测的编辑框）不动
                fb = self._unregistered_dialog_button(obs, tree)
                if fb is None:
                    break
                iid, spec = f"unregistered_dialog:{fb['texts'][0] if fb['texts'] else '?'}", {"settle_s": 1.0}
                tid, target = f"dismiss:{iid}", {"desc": f"关闭未登记弹窗 {fb['texts'][:3]}",
                                                 "side_effect": "dismiss_dialog",
                                                 "strategies": [{"kind": "text", "value": fb["button"]}]}
            loc = resolve(tid, target, tree, ctx.observer.screenshot_abs(obs), ctx.app.dir, None, None, obs.get("layout"))
            rec = {"interrupt": iid, "obs": obs["obs_id"], "target": tid, "locate": loc["status"]}
            if loc["status"] != "found":
                done.append({**rec, "ok": False})
                break
            gate = {"action": "tap", "step_id": label, "_safety": safety}
            dismiss_step = {"id": label}
            if condition_step is not None and self._when_guards(condition_step):
                condition = self._operation_condition(condition_step, obs, tree)
                ctx.store.event("condition", step=condition_step["id"], phase="interrupt_dismiss", condition=condition)
                gate["condition"] = condition
                if condition["status"] != "true":
                    done.append({**rec, "ok": False, "sent": False, "gate": "condition_" + condition["status"],
                                 "condition": condition})
                    break
                dismiss_step.update(**self._child_condition_context(condition_step), _selected_condition=condition)
            if not self._foreground_ok(gate):
                failed = {**rec, "ok": False, "sent": False, "gate": gate.get("status")}
                if condition_step is not None and gate.get("execution_error"):
                    failed.update(gate="preparation_error", execution_error=gate["execution_error"])
                if condition_step is not None:
                    retain_unlock(failed, gate)
                done.append(failed)
                break
            r = self._send_target("tap", loc, obs, tree, target, tid, dismiss_step, gate)
            rec.update(ok=r.ok, sent=r.sent, point=list(loc["point"]), gate=gate.get("status"))
            if gate.get("condition"):
                rec["condition"] = gate["condition"]
            if condition_step is not None and not r.ok:
                receipt = {"op": r.op, "sent": r.sent, "executor_ok": r.ok, "error_class": r.error_class,
                           "error": r.error, "raw": r.raw, "duration_ms": r.duration_ms}
                rec["dispatch"] = receipt
                if gate.get("execution_error") or gate.get("status") in (None, "device_error"):
                    error = gate.get("execution_error") or {
                        "stage": "prepare_interrupt", "error_class": r.error_class, "error": r.error,
                        "attribution": error_attribution(r.error_class), "evidence": {"obs": obs["obs_id"], "dispatch": receipt}}
                    rec.update(gate="preparation_error", execution_error=error)
                retain_unlock(rec, gate)
            ctx.store.event("interrupt_dismissed", at=label, **rec)
            done.append(rec)
            if not r.ok:
                break
            ctx.sleep(float(spec.get("settle_s", 1.0)))
            obs, tree = ctx.observe(f"{label}:dismissed{k + 1}")
        return obs, tree, done

    def _unregistered_dialog_button(self, obs: dict, tree) -> Optional[dict]:
        policy = self.ctx.app.states.data.get("dialog_policy") or {}
        safe = policy.get("safe_dismiss_text") or []
        for d in reversed(obs["page"].get("dialogs") or []):  # 最上层的先处理
            if d.get("interrupt") is None:
                btn = next((t for t in d["texts"] if t in safe), None)
                return {"texts": d["texts"], "button": btn} if btn else None
        return None

    def _inline_input_ok(self, tid, target, loc, obs, tree, att) -> bool:
        """输入框的副作用检查。登记目标按声明过权限门槛；task 类和现场选择器还须核验当前 UI 语义，
        不能仅凭登记或可编辑值取得默认任务许可。重命名框 user_data、账号框 account_state 仍需显式授权。"""
        ctx = self.ctx
        if not tid.startswith("inline:") and target.get("side_effect") not in ctx.policy.task_side_effects:
            denied = ctx.policy.check_effect(target, ctx.authorized)
            if denied:
                att["policy_context"] = denied
                att["status"] = "policy_denied"
            return denied is None
        pol = ctx.policy.check_tap(tree, loc["point"], loc["basis"], target,
                                   ctx.app.states.deny_for_page(obs["page"]), ctx.authorized,
                                   node=_tree_node(tree, loc),
                                   confirmation_sources=ctx.app.states.confirmation_sources(
                                       tree, tid, target, obs["page"], obs.get("layout")))
        att["policy_context"] = pol
        if not pol["allowed"]:
            att["status"] = "policy_denied"
        return pol["allowed"]

    def _revalidate(self, tid, target, loc: dict, step: dict, n: int, att: dict) -> tuple:
        """执行前复核：下发前重新取一次 UI 树（不截图）并重新解析同一目标，与执行前观测里的解析结果比较。
        节点不见了、变成多个、文字/类型/ID 变了、或位置移动超过窗口的 3%，都判 target_stale 且不下发。
        通过后用复核时的树做 policy 检查与下发（点击点来自最新的树）。"""
        ctx = self.ctx
        if loc.get("strategy_kind") not in ("text", "id", "xpath"):
            # 模板/坐标没有可比较的节点：复核无意义，如实记为跳过（覆盖范围只限有 UI 树的定位）
            att["revalidate"] = {"result": "skipped", "reason": f"定位依据 {loc.get('strategy_kind')} 不是 UI 树节点"}
            return loc, ctx.last_obs, ctx.last_tree
        obs2, tree2 = ctx.observe(f"{step['id']}#{n}:predispatch", screenshot=False)
        loc2 = resolve(tid, target, tree2, None, ctx.app.dir, None, None, obs2.get("layout"))
        before, after = _found_node(loc), _found_node(loc2)
        rec = {"obs": obs2["obs_id"], "status": loc2["status"], "before": before, "after": after}
        if obs2["page"].get("observation_error") or loc2["status"] != "found" or before is None or after is None:
            rec["result"] = "gone" if loc2["status"] == "not_found" else (loc2["status"] if loc2["status"] != "found"
                                                                           else "not_comparable")
        else:
            win = (obs2.get("layout") or {}).get("window_px") or [1, 1]
            (x1, y1), (x2, y2) = loc["point"], loc2["point"]
            drift = max(abs(x1 - x2) / max(1, win[0]), abs(y1 - y2) / max(1, win[1]))
            same = all(before.get(k) == after.get(k) for k in ("type", "text", "id"))
            rec.update(drift=round(drift, 4), same_node=same)
            rec["result"] = "same" if (same and drift <= 0.03) else ("moved" if same else "changed")
        att["revalidate"] = rec
        ctx.store.event("revalidate", step=step["id"], n=n, result=rec["result"], obs=obs2["obs_id"])
        if rec["result"] != "same":
            att.update(status="target_stale", obs_after=obs2["obs_id"])
            error = collection_error(obs2)
            if error:
                att["execution_error"] = {**error, "stage": "revalidate"}
            return loc, obs2, tree2
        loc2["obs"], loc2["revalidated_from"] = obs2["obs_id"], loc.get("obs")
        att["locate"] = {**loc, "revalidated": loc2}
        return loc2, obs2, tree2

    def _target(self, step: dict) -> tuple:
        """取目标并代入参数（流程参数 ${param:x}、用例数据 ${var:x}）。"""
        tid, target = self.ctx.app.target(step["target"])
        if self.ctx.params is not None:
            from .params import with_params
            target = with_params(target, self.ctx.params, step.get("_flow_params"))
        return tid, target

    def _point_in(self, loc: dict, step: dict, n: int, tree=None) -> list:
        """容器内取点：at=[fx, fy] 固定比例；random_point=true 按种子随机（每次尝试不同，重试时换一个点）。
        随机点必须真的落在容器上：地图常被底部面板/悬浮按钮盖住一部分，落在别的控件上的候选点丢掉换下一个。"""
        import random
        rect = loc.get("visible") or loc.get("bounds")

        def at(fx, fy):
            return [int(rect[0] + (rect[2] - rect[0]) * fx), int(rect[1] + (rect[3] - rect[1]) * fy)]
        if not step.get("random_point"):
            return at(*step["at"])
        rng = random.Random(f"{step.get('seed', self.ctx.seed)}:{step['id']}:{n}")
        hit = _found_node(loc)
        box = tree.nodes[hit["idx"]] if (tree is not None and hit and hit["idx"] < len(tree.nodes)) else None
        first = None
        for _ in range(40):
            pt = at(rng.uniform(0.25, 0.75), rng.uniform(0.25, 0.75))
            first = first or pt
            if box is None:
                return pt
            # 触摸会穿透透明浮层；只有被别的可点击控件（面板、tab、按钮）接走的点才换
            if tree.touch_check(*pt, box)["allowed"]:
                return pt
        return first

    def _relative_swipe(self, step, att):
        """A current, unique known scroll surface; arbitrary pixel/surface proposals stay unsupported."""
        ctx = self.ctx
        options = relative_swipe_options(step)
        within = step["within"]
        if isinstance(within, str):
            key, _, value = within.partition("=")
            within = {key: value}
        info = {"relative": True, **options, "within": within, "basis": "current_scroll_container"}
        def reject(status, reason):
            att.update(status=status, reason=reason)
            return DeviceResult("swipe", False, False, status, reason), info
        # The earlier foreground check may have unlocked. Never reuse its old geometry.
        obs, tree = ctx.observe(f"{step['id']}:relative-predispatch", screenshot=False)
        info.update(obs=obs["obs_id"], window_rect=(obs.get("layout") or {}).get("window_rect"),
                    window_source=(obs.get("layout") or {}).get("source"))
        error = collection_error(obs)
        if error:
            att["execution_error"] = {**error, "stage": "relative_swipe"}
            return reject("observation_failed", "相对手势前观测失败")
        if tree is None or tree.status != "ok":
            return reject("target_unresolved", "相对手势需要当前可用UI树和已知滚动容器")
        if obs.get("fresh") is False or not info["window_rect"]:
            return reject("target_stale", "相对手势的当前窗口或观测无效")
        if obs["page"].get("interrupts"):
            return reject("target_obscured", "手势复核时出现中断，未下发；须重新核对当前页面")
        if not self._check_condition(step, obs, tree, "relative_swipe", att):
            return DeviceResult("swipe", False, False, att["status"], att.get("reason")), info
        scopes = resolve_scope(within, tree, obs.get("layout")) or []
        if len(scopes) != 1:
            return reject("target_ambiguous" if scopes else "target_unresolved", "相对手势范围须匹配唯一当前容器")
        scope = scopes[0]
        if scope.parent is None or scope.type not in ("Scroll", "List", "Grid") or not scope.scrollable:
            return reject("target_unresolved", "范围不是可核验的Scroll/List/Grid滚动容器")
        if not tree.enabled_state(scope)[0]:
            return reject("target_disabled", "滚动容器当前不可用")
        area = tree.visible_rect(scope)
        if not area:
            return reject("target_unresolved", "滚动容器当前不可见")
        def point(fractions):
            return [round(area[i] + (area[i + 2] - area[i] - 1) * fractions[i]) for i in (0, 1)]
        p1, p2 = point(options["start"]), point(options["end"])
        info.update(area=list(area), container=scope.brief(), **{"from": p1, "to": p2})
        if p1 == p2:
            return reject("invalid_step", "相对手势换算后为零位移，未下发")
        # A line crosses a finite set of axis-aligned visibility boundaries. Check
        # every interval, not a fixed sparse sampling that can miss a thin overlay.
        ts = {0.0, 1.0}
        for node in tree.nodes:
            rect = tree.visible_rect(node)
            if not rect:
                continue
            for axis in (0, 1):
                delta = p2[axis] - p1[axis]
                if delta:
                    for edge in (rect[axis], rect[axis + 2]):
                        t = (edge - p1[axis]) / delta
                        if 0 < t < 1:
                            ts.add(t)
        ordered = sorted(ts)
        samples = sorted(ts | {(a + b) / 2 for a, b in zip(ordered, ordered[1:])})
        checks, checked = [], set()
        target = {"label": "relative_scroll", "side_effect": "navigation"}
        for t in samples:
            pt = [p1[i] + (p2[i] - p1[i]) * t for i in (0, 1)]
            touch = tree.touch_check(*pt, scope)
            if not touch["allowed"]:
                info["touch"] = touch
                return reject("target_obscured", "手势路径存在不属于滚动容器的接收者")
            node = tree.node_at(*pt)
            if node is None or not tree.is_within(node, scope):
                return reject("target_obscured", "手势路径离开当前滚动容器或被其他表面覆盖")
            identity = (node.idx, (touch.get("receiver") or {}).get("idx"))
            # Visible text can overlay a different clickable receiver (e.g. a
            # Slider). Ownership within the scope does not establish its effect.
            nodes = {node.idx: node}
            if identity[1] is not None:
                nodes[identity[1]] = tree.nodes[identity[1]]
            # clickable=False does not make a Slider or a self-drawn surface
            # safe to drag. A text sibling can hide both from node_at(). Check
            # each crossed interval before deduping policy nodes, including a
            # thin control in the middle of an otherwise unchanged text node.
            for candidate in tree.nodes:
                rect = tree.visible_rect(candidate)
                if not rect or not (rect[0] <= pt[0] < rect[2] and rect[1] <= pt[1] < rect[3]):
                    continue
                if candidate.type in ("XComponent", "SurfaceView", "TextureView", "Web", "WebView", "Canvas"):
                    info["unverified_surface"] = candidate.brief()
                    return reject("target_unresolved", "手势路径包含未校准表面，不能按普通容器滚动下发")
                if candidate.type in ctx.policy.deny_node_types:
                    nodes[candidate.idx] = candidate
            for current in nodes.values():
                if current.idx in checked:
                    continue
                checked.add(current.idx)
                policy = ctx.policy.check_tap(tree, pt, "tree", target,
                                              ctx.app.states.deny_for_page(obs["page"]), ctx.authorized,
                                              node=current)
                if not policy["allowed"]:
                    att["policy"] = policy
                    info["policy_node"] = current.brief()
                    return reject("policy_denied", "手势路径节点或实际接收者未通过策略：" + policy["reason"])
            checks.append({"node": node.idx, "receiver": (touch.get("receiver") or {}).get("idx")})
        info["path_checks"] = list({(c["node"], c["receiver"]): c for c in checks}.values())
        ctx.store.event("relative_swipe", step=step["id"], **info)
        # A lock appearing after current geometry must reject, never unlock and
        # then dispatch coordinates from before that physical action.
        return ctx.dispatcher.send("swipe", *p1, *p2, options["speed"],
                                   preconditions={**ctx.dispatcher.app_conditions("swipe"), "recover_unlock": False},
                                   att=att), info

    def _random_swipe(self, tree, lay, step: dict, att=None) -> tuple:
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
        return self.ctx.dispatcher.send("swipe", *p1, *p2, preconditions=self.ctx.dispatcher.app_conditions("swipe"), att=att), info

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
        return self._run_children(step, case_id, spec["steps"], base,
                                  {"_flow_params": local, "_flow_raw": step.get("params", {})})

    def _run_children(self, step, case_id, children, base, child_fields=None):
        """flow/recipe 共用执行与归约，保留各自的原因和读数投影。"""
        flow = base["action"] == "flow"
        subs = []
        for i, sub in enumerate(children):
            s = self.run_step({"critical": True, "retry": 1, **sub, "id": f"{step['id']}.{i + 1}",
                               **(child_fields or {}),
                               "_safety": bool(step.get("_safety")), **self._child_condition_context(step)}, case_id)
            subs.append(s)
            if s["outcome"] == "skipped" and s.get("critical", True):
                return self._required_skip(base, s, subs)
            if s["outcome"] != "skipped" and not s["outcome"].startswith("passed") and s.get("critical", True):
                return {**base, "outcome": s["outcome"], "attribution": s.get("attribution"),
                        "reason": (f"流程 {step['flow']} 第 {i + 1} 步（{sub.get('desc', sub['action'])}）未通过：{s.get('reason')}" if flow
                                   else f"前置状态 {step['state']} 未到达：{s.get('reason')}"),
                        "sub_steps": subs, "attempts": [a for x in subs for a in x["attempts"]],
                        "failure_source": s.get("failure_source") or s["step_id"],
                        "first_failure": s.get("first_failure"), "final_obs": s.get("final_obs"),
                        "final_status": s.get("final_status"), **({"measurement": s.get("measurement")} if flow else {})}
        order = ["passed", "passed_after_adaptation", "passed_after_recovery"]
        outcome = ("skipped" if subs and all(s["outcome"] == "skipped" for s in subs) else
                   "pending" if any(s["outcome"] == "pending" or (s["outcome"] == "skipped" and s.get("critical", True)) for s in subs) else
                   max((s["outcome"] for s in subs if s["outcome"] in order), key=order.index, default="passed"))
        if outcome == "pending":
            base.update(reason="复合操作有条件未知或必需子步骤未执行，不能确认完成", final_status="condition_unknown")
        return {**base, "outcome": outcome, "sub_steps": subs, "attempts": [a for x in subs for a in x["attempts"]],
                "adapted": any(s["adapted"] for s in subs),
                "first_failure": next((s["first_failure"] for s in subs if s.get("first_failure")), None),
                "recoveries": [r for s in subs for r in s["recoveries"]], "final_obs": subs[-1].get("final_obs"),
                **({"measurement": next((s["measurement"] for s in reversed(subs) if s.get("measurement")), None)} if flow else {})}

    def _swipe(self, direction: str, tree=None, lay=None, within=None, att=None) -> tuple:
        """在可滚动容器内滑动：起止点取容器可见区域的 75%/25% 高度，而不是整屏比例。
        显式范围必须可核验且含可见滚动容器；仅未限定范围时允许窗口回退。"""
        info, rect = {"direction": direction}, None
        if within and (tree is None or tree.status != "ok"):
            return DeviceResult("swipe", False, False, "scroll_scope_unavailable",
                                "当前UI树不可用，无法核验指定滚动范围"), {**info, "container": None}
        if tree is not None and tree.status == "ok":
            scope = resolve_scope(within, tree, lay)
            if scope is not None and not scope:
                return DeviceResult("swipe", False, False, "scroll_scope_missing",
                                    f"滚动范围 {within} 在当前页面不存在"), {**info, "container": None}
            node = tree.scroll_container(scope)
            if node is not None:
                rect = tree.visible_rect(node)
                info["container"] = {"type": node.type, "id": node.id, "visible": list(rect)}
            elif scope is not None:
                return DeviceResult("swipe", False, False, "scroll_container_missing",
                                    "指定范围内没有可见纵向滚动容器，未回退窗口滑动"), {**info, "container": None}
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
        return self.ctx.dispatcher.send("swipe", x, y1, x, y2, preconditions=self.ctx.dispatcher.app_conditions("swipe"), att=att), info

    # ---- 后置条件 ----
    def _state_diagnostics(self, expect, obs, tree):
        """Explain declared require_text evidence, not state matching or environment causes."""
        spec = self.ctx.app.states.states.get(expect) if isinstance(expect, str) else None
        if not isinstance(spec, dict):
            return None  # Builtins and undeclared states have no registered signature to explain.
        keys = ("require_text", "require_any_text", "require_text_pattern", "require_selected_text",
                "forbid_text", "forbid_text_pattern")
        signature = {key: list(spec[key]) for key in keys if key in spec}
        page, meta = obs.get("page") or {}, obs.get("tree") or {}
        available = bool(tree is not None and tree.status == "ok" and meta.get("status") == "ok"
                         and obs.get("fresh") is not False and not collection_error(obs))
        required = list(spec.get("require_text") or [])
        texts = set(tree.visible_texts()) if available else set()
        return {"diag_only": True, "expected": expect, "signature": signature, "obs": obs["obs_id"],
                "observed": {key: page.get(key) for key in ("state", "candidates", "active", "panes")},
                "tree": {"status": meta.get("status"), "path": meta.get("path")},
                "require_text": {"scope": "whole_window_visible_text", "available": available,
                                 "required": required,
                                 "present": [text for text in required if text in texts] if available else None,
                                 "missing": [text for text in required if text not in texts] if available else None}}

    def _post(self, step: dict, n: int, case_id: str, was_in_app: bool = True, mark: int = 0) -> dict:
        ctx = self.ctx
        expect = step.get("expect_state")
        default = float(step.get("timeout_s", 8))
        timeout = ctx.overlay.wait_for(f"{case_id}:{step['id']}", default, ctx.policy.max_wait_s) \
            if ctx.overlay else min(default, ctx.policy.max_wait_s)
        interval = ctx.device.poll_interval
        t0, polls, post_dismissed, stop_check = time.monotonic(), 0, [], None
        while True:
            polls += 1
            obs, tree = ctx.observe(f"{step['id']}#{n}:after")
            obs, tree, dismissed = self._clear_interrupts(obs, tree, f"{step['id']}#{n}:after", safety=bool(step.get("_safety")))
            post_dismissed += dismissed
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
            if step["action"] == "stop_app":
                # Issue #15：停止是独立断言，不能随"无后置条件即通过"混过去。两级分开验证：
                # (1) 停止成功（无条件）：进程必须确认结束——存活状态查不到/仍存活 → stop_not_confirmed；
                # (2) 恢复初始前台（仅当用例显式声明 require_launcher 且有可靠前置事实）：停止前我们在
                #     前台（停的是我们的屏），停止后理应回桌面——前台查询失败 foreground_unknown、
                #     前台被其他应用占据 foreground_not_restored。
                # 未声明 require_launcher 时前台事实只记录进 stop_check 供人工复核，不据此判定：真机上
                # 其他应用（如 WeLink）接管前台是环境现象、与产品无关，不应自动造成产品失败。
                # 停止前就不在前台（no-op 停止，比如从未拉起过）时即使声明了也无可推导的前置事实：
                # 记为 unverifiable，不判失败。
                fg = obs.get("foreground")
                # 状态识别在 in_app/undetermined 分支不带 process_alive：以观测记录的查询结果为准
                alive = page.get("process_alive", obs.get("process_alive"))
                ended = alive is False
                require_launcher = bool(step.get("require_launcher"))
                stop_check = {"process_alive": alive, "foreground": fg,
                              "process_ended": ended, "foreground_restored": fg == LAUNCHER,
                              "require_launcher": require_launcher,
                              "foreground_error": obs.get("foreground_error"),
                              "process_error": obs.get("process_error")}
                if fg not in (None, LAUNCHER):
                    stop_check["foreign_foreground"] = fg
                if not ended:
                    if interval <= 0 or elapsed >= timeout:
                        status = "stop_not_confirmed"
                        stop_check["timeout_s"] = timeout
                        break
                    # 进程尚未确认结束：带着最新 stop_check 继续轮询
                elif require_launcher:
                    if not was_in_app:
                        # 声明了恢复期望但停止前 APP 不在前台：没有可靠前置事实，无从判定
                        stop_check["require_launcher_unverifiable"] = "停止前 APP 不在前台，无从推导恢复期望"
                    elif fg is None:
                        status = "foreground_unknown"
                        break
                    elif fg != LAUNCHER:
                        status = "foreground_not_restored"
                        break
                    # 进程结束且前台回桌面：正常通过
                # 未声明 require_launcher：ended 即通过，前台事实留在 stop_check 供人工复核
            if crash or anomalies:
                status = "anomaly"
                break
            if dismissed and dismissed[-1].get("gate") in ("budget_exhausted", "motion_timeout", "safety_hold"):
                status = dismissed[-1]["gate"]
                break
            if not step.get("_safety") and ctx.motion.breached_since(mark):
                # 等待后置条件期间运动超时：即使随后看到了停止状态或后置条件成立，也不能当作正常完成
                status = "motion_timeout"
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
        post = {"obs_id": obs["obs_id"], "expected": expect, "observed": page["state"], "met": status == "ok",
                "execution_error": ({**obs["process_error"], "stage": "stop_verification",
                                     "attribution": error_attribution(obs["process_error"].get("error_class")),
                                     "evidence": {"obs": obs["obs_id"], "process_error": obs["process_error"]}}
                                    if status == "stop_not_confirmed" and obs.get("process_error") else collection_error(obs)),
                "status": status, "interrupts": page.get("interrupts", []), "suspected_crash": crash,
                "dialogs": page.get("dialogs", []),
                "anomalies": anomalies, "elapsed_s": elapsed, "timeout_s": timeout, "polls": polls,
                "dismissed": post_dismissed, "stop_check": stop_check}
        if status != "ok":
            diagnostic = self._state_diagnostics(expect, obs, tree)
            if diagnostic is not None:
                post["state_diagnostics"] = diagnostic
        return post

    # ---- 恢复 ----
    def _effect_unknown(self, att: dict, *, status=None, post=None) -> bool:
        """已知失败不等于执行未知；只取物理证据和明确不可判的观测。"""
        status = status or att["status"]
        post = (att.get("post") or {}) if post is None else post
        ambiguous = status in {"unobservable", "observation_failed",
                               "foreground_unknown", "state_indeterminate"}
        if status == "interrupted":
            ambiguous = post.get("observed") in (None, "undetermined", "unknown", "ambiguous")
        if status == "stop_not_confirmed":
            ambiguous = (post.get("stop_check") or att.get("stop_check") or {}).get("process_alive") is None
        if status == "device_error" and att.get("dispatch") is None:
            ambiguous = True  # 不能把缺失的下发记录当作可靠未发。
        if status not in ("ok", "device_error") and status not in STATUS_MAP:
            ambiguous = True
        return bool(att.get("effect_unknown") or dispatch_effect_unknown(att) or ambiguous)

    def _repeat_allowed(self, step: dict, att: dict) -> bool:
        """只在可靠未下发，或明确低风险幂等时重放整个逻辑步骤。

        clear 与 input 是同一步；后一次未下发不能抹掉先前的清空。
        back/随机点选的旧幂等声明只用于已观察到后置未成立，不能用于丢回执后的盲重发。
        """
        if any(item["sent"] is not False and (item["sent"] is not True or item.get("ok") is not True
                                              or item.get("verified") is False)
               for item in auxiliary_dispatches(att)):
            return False
        clear = att.get("clear")
        if clear and clear.get("sent") is not False:
            return False
        dispatch = att.get("dispatch")
        if dispatch is None:
            return att["status"] != "device_error"
        sent = dispatch.get("sent")
        if sent is False:
            return True
        if sent is not True or step.get("idempotent") is not True:
            return False
        kind = step["action"]
        if kind == "input":  # inputText 是追加，不能凭声明成为幂等。
            return False
        if kind == "tap":
            _, target = self._target(step)
            if target.get("side_effect") not in ("navigation", "dismiss_dialog"):
                return False
            return not step.get("random_point") or att["status"] == "postcondition_not_met"
        # 返回可能先收键盘后退页面；只保留有后置观测依据的旧重试。
        return kind in ("back", "home", "launch", "resume", "stop_app") and att["status"] == "postcondition_not_met"

    def _recover(self, step: dict, att: dict, case_id: str) -> dict:
        ctx, st = self.ctx, att["status"]
        recoverable = {"interrupted", "target_unresolved", "target_partially_visible", "target_stale",
                       "observation_failed", "device_error", "unobservable", "postcondition_not_met"}
        if st in recoverable - {"interrupted", "unobservable"} and not self._repeat_allowed(step, att):
            # 已下发后的异常不是未执行；维持原状态/首次失败，交接现场而不重复原动作。
            att["effect_unknown"] = self._effect_unknown(att)
            att["action_required"] = True
            return {"action": "none", "performed": False, "action_required": True,
                    "effect_unknown": att["effect_unknown"],
                    "reason": "原步骤已下发、部分执行或下发事实不明确，不能安全重放；请核对现场后人工接管"}
        if st == "interrupted":
            ints = att["post"]["interrupts"]
            bad = [i for i in ints if not (i["class"] == "benign" and i["review"] == "reviewed")]
            if bad:
                return {"action": "none", "performed": False, "interrupt": bad[0]["id"],
                        "reason": f"中断 {bad[0]['id']} 未评审或非良性（{bad[0]['class']}/{bad[0]['review']}），不自动关闭"}
            spec = ctx.app.states.interrupts[ints[0]["id"]]
            dt = spec.get("dismiss_target") or {"label": f"关闭{ints[0]['id']}", "side_effect": "dismiss_dialog",
                                                "strategies": [{"kind": "text", "value": spec["dismiss_text"]}]}
            sub = {"id": f"{step['id']}.dismiss", "action": "tap", "target": dt,
                   "expect_state": step.get("expect_state"), "timeout_s": step.get("timeout_s", 8),
                   "_safety": bool(step.get("_safety"))}
            r = self._attempt(sub, 1, case_id)
            rec = {"action": "dismiss_interrupt", "performed": "dispatch" in r, "interrupt": ints[0]["id"],
                   "reason": "关闭已评审的良性中断后重新判定原步骤后置条件", "attempt": r,
                   "post": r.get("post"), "status": r["status"]}
            completed = r["status"] in ("ok", "postcondition_not_met")
            dispatches = list(auxiliary_dispatches(r))
            if r.get("dispatch"):
                dispatches.append({**r["dispatch"], "ok": r["dispatch"].get("executor_ok")})
            self._retain_recovery_dispatches(att, rec, dispatches, verified=completed)
            return self._after_recovery(step, att, rec, case_id) if completed else {
                **rec, "retry_original": False}
        if st in ("target_unresolved", "target_partially_visible", "target_stale"):
            time.sleep(min(1.0, ctx.device.poll_interval))
            return {"action": "reobserve_and_retry", "performed": True, "reason": "等待后重新观察并完整重试定位"}
        if st == "observation_failed":
            time.sleep(min(3.0, 3 * ctx.device.poll_interval))  # hdc 通道重建通常几秒内恢复
            return {"action": "reobserve_and_retry", "performed": True, "reason": "观测查询失败，等待后重新观察"}
        if st == "device_error" and att.get("error_class") in ("timeout", "command_failed", "transport_unavailable"):
            return {"action": "redispatch", "performed": True,
                    "reason": "可靠未下发或明确低风险幂等步骤，允许在既有预算内重试"}
        if st == "unobservable":
            # 动作后取不到可判定的观测：先整屏诊断覆盖物。命中已登记且已评审的良性系统弹窗
            #（system_dialogs.json）时用原子能力关掉再重试；锁屏/未知覆盖物不属于允许的恢复范围。
            # 2026-10-08 真机：USB 连接方式弹窗以此路径两次阻塞 S1 后补的能力。
            from .sysdialog import resolve_screen_cover
            res = resolve_screen_cover(ctx.ws, ctx.device, ctx.store, dispatcher=ctx.dispatcher,
                                       dialogs=ctx.observer.dialogs)
            dispatches = res.get("dispatches", [res["dismiss"]] if "dismiss" in res else [])
            rec = {"action": "dismiss_system_dialog" if "dismiss" in res else "none",
                   "performed": any(d.get("sent") is not False for d in dispatches),
                   "dialog": res["diagnosis"]["matched"], "capability": res,
                   "reason": f"unobservable：整屏覆盖诊断 {res['status']}（{res['reason']}）",
                   "retry_original": False}
            if res.get("execution_error"):
                att["execution_error"] = res["execution_error"]
            self._retain_recovery_dispatches(att, rec, dispatches,
                                             verified=res["status"] == "dismissed")
            if res["status"] == "dismissed":
                rec["reason"] = f"关闭已评审的良性系统弹窗 {res['diagnosis']['matched']}，重新判定原步骤后置条件"
                return self._after_recovery(step, att, rec, case_id)
            return rec
        if st == "postcondition_not_met" and step.get("idempotent") is True:
            return {"action": "redispatch", "performed": True, "reason": "步骤声明为幂等，允许重发一次"}
        return {"action": "none", "performed": False, "reason": f"{st} 不属于允许的恢复范围"}

    def _obstruction_matches(self, input_step, guard, obs, tree) -> bool:
        """The named positive target must own the actual receiver, not merely coexist with it."""
        if tree is None or tree.status != "ok" or collection_error(obs):
            return False
        provider = self.ctx.conditions[guard["condition"]]["true"]
        tid, target = self.ctx.app.target(provider["target"])
        menu = resolve(tid, target, tree, None, self.ctx.app.dir, layout=obs.get("layout"))
        tid, target = self._target(input_step)
        field = resolve(tid, target, tree, None, self.ctx.app.dir, layout=obs.get("layout"))
        owner, expected = _tree_node(tree, menu), _tree_node(tree, field)
        if menu.get("status") != "found" or field.get("status") != "found" or owner is None or expected is None:
            return False
        touch = tree.touch_check(*field["point"], expected)
        receiver = touch.get("receiver") or {}
        idx = receiver.get("idx")
        return touch.get("allowed") is False and idx == owner.idx

    def _recover_input_obstruction(self, step, att, case_id):
        """One explicit menu dismissal, only before either clear or input has been sent."""
        rec = {"action": "dismiss_input_obstruction", "performed": False, "retry_original": False,
               "reason": "原输入并非可靠零下发，或当前遮挡不满足显式恢复条件"}
        receipt = att.get("dispatch") or {}
        if receipt.get("sent") is not False or not self._repeat_allowed(step, att):
            return rec
        ctx, guard = self.ctx, step["dismiss_obstruction_when"]
        back = {"id": step["id"] + ".dismiss_obstruction", "action": "back", "retry": 0,
                "expect_state": "@in_app", "timeout_s": 0, "when": guard,
                "_obstructed_input": step, **self._child_condition_context(step)}
        # Internal recovery is retained under its parent attempt, not registered as a
        # user-referenceable step which could collide with a caller's step ID.
        result = self._run_step(back, case_id)
        rec.update(attempt=result, performed=any(a.get("dispatch", {}).get("sent") is not False
                                               for a in result.get("attempts", []) if "dispatch" in a),
                   reason="仅关闭当前命中显式条件且实际遮挡输入框的容器；关闭后独立复核")
        dispatches = [d for a in result.get("attempts", []) for d in auxiliary_dispatches(a)]
        dispatches += [{**a["dispatch"], "ok": a["dispatch"].get("executor_ok")}
                       for a in result.get("attempts", []) if a.get("dispatch")]
        verified = False
        if result.get("outcome") in ("passed", "passed_after_recovery", "passed_after_adaptation"):
            obs, tree = ctx.observe(back["id"] + ":verify", screenshot=False)
            after = {**back, "when": {**guard, "equals": False}, "_selected_condition": result.get("condition")}
            condition = self._operation_condition(after, obs, tree)
            ctx.store.event("condition", step=back["id"], phase="obstruction_gone", condition=condition)
            rec["condition_after"] = condition
            verified = condition["status"] == "true"
            if verified:
                rec.update(retry_original=True, status="dismissed", reason="已确认声明菜单消失；重新观测和定位后仅重试原输入一次")
            else:
                rec["post"] = {"status": "target_obscured" if condition["status"] == "false" else "condition_" + condition["status"], "obs_id": obs["obs_id"],
                               "execution_error": condition.get("execution_error")}
                att["reason"] = "遮挡关闭后的独立反证未成立，停止原输入"
        else:
            final = (result.get("attempts") or [{}])[-1]
            status = result.get("final_status") or final.get("status") or "condition_unknown"
            error = result.get("execution_error")
            if status == "device_error" and not error:
                error = {"stage": "input_obstruction_recovery", "attribution": error_attribution(final.get("error_class")),
                         "error_class": final.get("error_class"), "error": (final.get("dispatch") or {}).get("error"),
                         "evidence": {"attempt": final}}
            rec["post"] = {"status": "target_obscured" if status == "condition_false" else status,
                           "obs_id": result.get("final_obs"), "execution_error": error}
        self._retain_recovery_dispatches(att, rec, dispatches, verified=verified)
        return rec

    def _retain_recovery_dispatches(self, att: dict, rec: dict, dispatches: list, *, verified: bool):
        """恢复手势属于同一步；原步骤没有dispatch也不能丢掉恢复的未知效果。"""
        records = [{**item, "verified": verified} for item in dispatches if "sent" in item]
        if not records:
            return
        att.setdefault("recovery_dispatches", []).extend(records)
        if any(item["sent"] is not False and (item["sent"] is not True or item.get("ok") is not True
                                              or not verified) for item in records):
            att.update(effect_unknown=True, action_required=True)
            rec.update(effect_unknown=True, action_required=True, retry_original=False)
            rec["reason"] += "；恢复动作已下发或发送事实不明确，关闭效果未确认，需人工核对现场"

    def _after_recovery(self, step: dict, att: dict, rec: dict, case_id: str) -> dict:
        """修复覆盖物后只核验原后置；不把关闭弹窗当成原动作可重复的证明。"""
        rec["after_attempt"] = att["n"]
        post = self._post(step, att["n"], case_id,
                          was_in_app=att.get("state_before") != "out_of_app", mark=att.get("motion_mark", 0))
        rec.update(post=post, status=post["status"], retry_original=self._repeat_allowed(step, att))
        if post["status"] != "ok" and rec["retry_original"] is False:
            att.update(effect_unknown=self._effect_unknown(att, status=post["status"], post=post), action_required=True)
            rec.update(effect_unknown=att["effect_unknown"], action_required=True)
            rec["reason"] += "；原步骤已下发或部分执行，不重放，需人工核对现场"
        return rec

    # ---- 汇总 ----
    def _summarize(self, step: dict, attempts: list, recoveries: list) -> dict:
        last, last_rec = attempts[-1], (recoveries[-1] if recoveries else None)
        retained = None
        if last["status"] in ("condition_false", "condition_unknown") and len(attempts) > 1:
            previous = self._summarize(step, attempts[:-1], recoveries)
            if previous["outcome"] in ("error", "fail_candidate"):
                if previous["outcome"] == "error":
                    from .verdict import execution_error
                    previous["execution_error"] = execution_error(previous)
                retained = previous
        if last_rec and last_rec.get("after_attempt") != last["n"]:
            last_rec = None  # 旧恢复不能覆盖后来重试的错误或观测。
        rec_ok = bool(last_rec and last_rec.get("status") == "ok")
        ok = last["status"] == "ok" or rec_ok
        final_post = (last_rec.get("post") or {}) if last_rec else {}
        final_status = final_post.get("status") or last["status"]
        # 单次 act、cleanup 和耗尽预算的最后尝试同样需要交接，不能依赖进入恢复分支。
        if not ok and (not self._repeat_allowed(step, last) or dispatch_effect_unknown(last)):
            last["action_required"] = True
            last["effect_unknown"] = self._effect_unknown(last, status=final_status,
                                                          post=final_post or last.get("post") or {})
        adapted = any((a.get("locate") or {}).get("adapted") for a in attempts)
        first_bad = next((a for a in attempts if a["status"] not in ("ok", "condition_false", "condition_unknown")), None)
        out = {"step_id": step["id"], "action": step["action"], "critical": bool(step.get("critical", True)),
               "oracle_refs": step.get("oracle_refs", []), "attempts": attempts, "recoveries": recoveries,
               "adapted": adapted}
        if any(a.get("effect_unknown") for a in attempts):
            out["effect_unknown"] = True
        if not ok and any(a.get("action_required") for a in attempts):
            out["action_required"] = next((a["action_required"] for a in reversed(attempts)
                                           if isinstance(a.get("action_required"), str)),
                                          "原步骤已下发或部分执行，恢复未重放原动作；请核对现场后人工接管")
        dismissed = [d for a in attempts for d in (a.get("dismissed") or []) + ((a.get("post") or {}).get("dismissed") or [])]
        if dismissed:
            out["dismissed"] = dismissed
        if last.get("measurement"):
            out["measurement"] = last["measurement"]
        for field in ("read_binding", "read_sources"):
            if field in last:
                out[field] = last[field]
        if last.get("condition"):
            out["condition"] = last["condition"]
        if last.get("monitor"):
            out["monitor"] = last["monitor"]
        if last.get("resume"):
            out["resume"] = last["resume"]
        if ok:
            out["outcome"] = "passed_after_recovery" if first_bad else ("passed_after_adaptation" if adapted else "passed")
            out["final_obs"] = last_rec["post"]["obs_id"] if (rec_ok and last["status"] != "ok") else last.get("obs_after")
        else:
            effective = {**last, "status": final_status,
                         "execution_error": final_post.get("execution_error") or (last.get("post") or {}).get("execution_error")
                         or last.get("execution_error") or (last.get("locate") or {}).get("execution_error")}
            out["outcome"], out["attribution"], out["reason"] = classify(effective)
            diagnostic = (final_post or last.get("post") or {}).get("state_diagnostics")
            if diagnostic is not None:
                # Recovery owns its final observation; first_failure keeps the original evidence.
                out["state_diagnostics"] = diagnostic
                anchors = diagnostic["require_text"]
                detail = ("require_text无法核对" if not anchors["available"] else
                          f"require_text已见{anchors['present']}，未见{anchors['missing']}" if anchors["required"] else
                          "未声明require_text锚点")
                out["reason"] += (f"；状态诊断（仅文本证据，非环境证明）：当前{diagnostic['observed']['state']}，"
                                  f"{detail}；观测{diagnostic['obs']}")
            if out["outcome"] == "skipped" and any(item["sent"] is not False for item in auxiliary_dispatches(effective)):
                out.update(outcome="pending", attribution="tool",
                           reason="后续操作条件不满足；辅助动作已经或可能执行，保留部分执行并交接")
            if out["outcome"] == "skipped" and effective.get("clear") and (effective["clear"].get("sent") is not False):
                out.update(outcome="pending", attribution="tool",
                           reason="后续操作条件不满足；清空已经或可能执行，保留部分执行并交接")
            if effective.get("execution_error"):
                out["execution_error"] = effective["execution_error"]
            out["final_obs"] = final_post.get("obs_id") or last.get("obs_after") or last.get("obs_before")
            out["final_status"] = final_status
        if first_bad:
            out["first_failure"] = {"attempt": first_bad["n"], "status": first_bad["status"],
                                    "obs_before": first_bad.get("obs_before"), "obs_after": first_bad.get("obs_after"),
                                    "detail": _failure_detail(first_bad)}
        if last["status"] == "preparation_error" and len(attempts) > 1 and out.get("execution_error"):
            error = out["execution_error"]
            out["execution_error"] = {**error, "evidence": {
                **(error.get("evidence") or {}), "prior_attempt_failures": [
                    {"attempt": a["n"], "status": a["status"], "detail": _failure_detail(a)}
                    for a in attempts[:-1] if a["status"] != "ok"]}}
        if retained:
            # 原错不能被选支清除；最新尝试的不明副作用、交接和辅助动作仍须完整汇总。
            out = {**retained, **out, **{key: retained[key] for key in
                   ("outcome", "attribution", "final_obs", "final_status", "execution_error") if key in retained},
                   "reason": (retained.get("reason") or "原步骤未完成") + "；后续条件变化不构成原步骤恢复"}
        return out

    # ---- 到达已知状态 ----
    def _ensure_state(self, step: dict, case_id: str) -> dict:
        ctx, target = self.ctx, step["state"]
        base = {"step_id": step["id"], "action": "ensure_state", "critical": True, "oracle_refs": step.get("oracle_refs", []),
                "recoveries": [], "adapted": False}
        obs, tree = ctx.observe(f"{step['id']}:check")
        def rejected(condition, dismissed=()):
            error = condition.get("execution_error") or {}
            outcome = {"false": "skipped", "unknown": "pending", "error": "error"}[condition["status"]]
            if outcome == "skipped" and any(item.get("sent") is not False for item in dismissed):
                outcome = "pending"
            result = {**base, "outcome": outcome, "attribution": error.get("attribution", "requirement_unclear"),
                    "attempts": [], "reason": condition["reason"], "condition": condition,
                    "execution_error": error, "final_status": "condition_" + condition["status"],
                    "final_obs": obs["obs_id"], "dismissed": list(dismissed)}
            if dispatch_effect_unknown({"dismissed": list(dismissed)}):
                result.update(effect_unknown=True, action_required="辅助弹窗动作已经或可能下发，效果未确认；请核对现场后人工接管")
            return result
        if self._when_guards(step):
            condition = self._operation_condition(step, obs, tree)
            ctx.store.event("condition", step=step["id"], phase="before_interrupts", condition=condition)
            if condition["status"] != "true":
                return rejected(condition)
        obs, tree, dismissed = self._clear_interrupts(obs, tree, f"{step['id']}:check", safety=bool(step.get("_safety")),
                                                    condition_step=step if self._when_guards(step) else None)
        if dismissed and dismissed[-1].get("gate") == "preparation_error":
            error = dismissed[-1]["execution_error"]
            result = {**base, "outcome": "error", "attribution": error["attribution"], "attempts": [],
                      "reason": "操作前弹窗处置失败：" + str(error["error_class"]), "execution_error": error,
                      "final_status": "preparation_error", "final_obs": obs["obs_id"], "dismissed": dismissed,
                      "first_failure": {"attempt": 1, "status": "preparation_error", "obs_before": obs["obs_id"],
                                        "detail": {"execution_error": error}}}
            if dispatch_effect_unknown({"dismissed": dismissed}):
                result.update(effect_unknown=True, action_required="辅助弹窗动作已经或可能下发，效果未确认；请核对现场后人工接管")
            return result
        if dismissed and str(dismissed[-1].get("gate", "")).startswith("condition_"):
            return rejected(dismissed[-1]["condition"], dismissed)
        if dismissed and (dismissed[-1].get("gate") in ("budget_exhausted", "motion_timeout", "safety_hold") or (
                self._when_guards(step) and dismissed[-1].get("gate") in STATUS_MAP and STATUS_MAP[dismissed[-1]["gate"]][0] == "blocked")):
            status = dismissed[-1]["gate"]
            return {**base, "outcome": "blocked", "attribution": STATUS_MAP[status][1], "attempts": [],
                    "reason": STATUS_MAP[status][2], "final_status": status, "final_obs": obs["obs_id"], "dismissed": dismissed}
        if self._when_guards(step):
            condition = self._operation_condition(step, obs, tree)
            ctx.store.event("condition", step=step["id"], phase="ensure_state", condition=condition)
            if condition["status"] != "true":
                return rejected(condition, dismissed)
        if ctx.app.states.is_state(obs["page"], target) and not obs["page"].get("interrupts"):
            return {**base, "outcome": "passed", "attempts": [], "note": "已处于目标状态", "final_obs": obs["obs_id"],
                    "dismissed": dismissed}
        recipe = ctx.app.states.recipes.get(target)
        if not recipe:
            error = collection_error(obs)
            if error:
                return {**base, "outcome": "error", "attribution": error["attribution"], "attempts": [],
                        "reason": "到达状态所需观测采集失败", "execution_error": error,
                        "final_status": "observation_failed", "final_obs": obs["obs_id"]}
            return {**base, "outcome": "blocked", "attribution": "tool", "attempts": [],
                    "reason": f"没有到达状态 {target} 的 recipe", "final_obs": obs["obs_id"]}
        # 到达目标是 ensure_state 的保证，不要求每个 recipe 手写末尾检查。
        # 复用普通步骤的条件、观测和结果契约；必需子步骤未完成时不会执行此检查。
        terminal = {"action": "wait", "duration_s": 0, "expect_state": target,
                    "timeout_s": 0, "retry": 0, "critical": True}
        return self._run_children(step, case_id, [*recipe, terminal], base)


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


def _tree_node(tree, loc: dict):
    """定位命中的树节点（容器内取点的步骤不用：点击点不在节点中心，按点反查更准确）。"""
    hit = _found_node(loc)
    if hit is None or tree is None or loc.get("container_point") or loc.get("basis") != "tree":
        return None
    return tree.nodes[hit["idx"]] if hit["idx"] < len(tree.nodes) else None


def _found_node(loc: dict) -> Optional[dict]:
    hit = next((a for a in loc.get("attempts", []) if a.get("status") == "found" and a.get("node")), None)
    return hit["node"] if hit else None


def _resolved(loc: dict, obs: dict) -> dict:
    """执行证据：这次实际点在哪里、依据哪个节点、在什么屏幕形态下。用例里不写坐标，但证据里必须记下。"""
    lay = obs.get("layout") or {}
    return {"point": loc.get("point"), "coordinate_system": "px", "node": _found_node(loc), "bounds": loc.get("bounds"), "visible": loc.get("visible"),
            "visible_fraction": loc.get("visible_fraction"), "strategy": loc.get("strategy_kind"),
            "strategy_index": loc.get("strategy_index"), "container_point": bool(loc.get("container_point")),
            "obs": obs.get("obs_id"), "window_px": lay.get("window_px"), "layout_key": layout_key(lay),
            "density": lay.get("density"), "device_form": lay.get("device_form"), "panes": len(lay.get("panes") or [])}


def _unresolved(loc: dict) -> str:
    if loc.get("execution_error"):
        return "observation_failed"
    if loc["status"] == "blocked_state":  # 滚动查找前的锁屏/前台确认没过：沿用那次确认的状态（未滑动）
        return loc["gate"]["status"]
    return {"ambiguous": "target_ambiguous", "partially_visible": "target_partially_visible"}.get(
        loc["status"], "target_unresolved")


def _failure_detail(att: dict) -> dict:
    d = {"status": att["status"]}
    for k in ("error_class", "policy", "dispatch", "dispatch_recheck", "motion", "clear", "effect_unknown", "action_required"):
        if att.get(k):
            d[k] = att[k]
    if att.get("monitor"):
        d["monitor"] = {k: att["monitor"].get(k) for k in ("stopped_by", "samples", "states", "hits")}
    if att.get("locate"):
        d["locate"] = {k: att["locate"].get(k) for k in ("status", "target", "attempts")}
    if att.get("post"):
        d["post"] = {k: att["post"].get(k) for k in ("expected", "observed", "interrupts", "suspected_crash")}
        if att["post"].get("state_diagnostics") is not None:
            d["post"]["state_diagnostics"] = att["post"]["state_diagnostics"]
    if att.get("stop_check"):
        # Issue #15：停止断言两级证据随失败记录落盘（进程是否结束 / 前台是否恢复）
        d["stop_check"] = att["stop_check"]
    return d
