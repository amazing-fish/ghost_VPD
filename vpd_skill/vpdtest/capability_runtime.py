"""Shared runtime primitives for L2/L3 atomic capabilities and declarative capability specs.

Centralizes observation/classification, challenge & benign-dialog interception,
unlock/launch convergence, bounded poll loops, bounded edge-to-edge scrolling,
and declarative capability metadata so atomic capabilities (login, switch-vehicle,
enter-vpd, future P1 capabilities) and Executor preconditions share one contract.

Layer rule: this module sits below `execute`/`runner`/`cases`/`verdict` and may
only import standard library + lower/sibling foundation modules (`experience`,
`locate`, `observe`, `outcomes`, `unlock`, `workspace`).
"""
from __future__ import annotations

import math
import os
import re
import time
from typing import Callable, Optional

from .locate import resolve
from .observe import RunStore
from .outcomes import error_attribution
from .unlock import unlock_device
from .workspace import RunContext

SECRET_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
VIN_TAIL_RE = re.compile(r"^[A-Za-z0-9]{1,17}$")
_PARAM_PLACEHOLDER_RE = re.compile(r"^\$\{(?:var|param):([A-Za-z0-9_.-]+)\}$")

LOGGED_IN_STATES = frozenset({"qk_vpd_home", "qk_car_tab", "qk_discover_tab"})
LOGIN_BLOCK_TEXTS = frozenset({"欢迎登录华为乾崑", "本机号码一键登录", "其他登录方式", "登录/注册", "密码登录"})
LOGGED_OUT_KINDS = frozenset({"logged_out_my", "one_click", "method_select", "password_form"})
FATAL_DISPATCH_STATUSES = frozenset({"budget_exhausted", "motion_timeout", "safety_hold"})

CAPABILITY_SPECS: dict[str, dict] = {
    "logged_in": {
        "apps": ("qiankun_hm",),
        "required_params": (),
        "allowed_params": ("account_secret", "password_secret", "timeout_s"),
        "max_timeout_s": 60.0,
        "success_statuses": frozenset({"logged_in"}),
    },
    "vehicle_vin": {
        "apps": ("qiankun_hm",),
        "required_params": ("vin_tail",),
        "allowed_params": ("vin_tail", "timeout_s"),
        "max_timeout_s": 60.0,
        "success_statuses": frozenset({"switched"}),
    },
    "vpd_ready": {
        "apps": ("qiankun_hm",),
        "required_params": (),
        "allowed_params": ("vin_tail", "auto_login", "account_secret", "password_secret", "timeout_s"),
        "max_timeout_s": 120.0,
        "success_statuses": frozenset({"ready"}),
    },
}

_STEP_META_KEYS = frozenset({
    "id", "action", "desc", "critical", "oracle_refs", "when", "retry",
    "_safety", "_condition_guards", "_condition_bindings", "_selected_condition",
    "_flow_params", "_flow_raw", "_lint_cycle",
})


def validate_secret_key(name: Optional[str], default: str, flag: str) -> str:
    key = default if name is None else str(name)
    if not SECRET_KEY_RE.fullmatch(key):
        raise ValueError(f"{flag} 只接受环境变量/secrets 键名（大写字母、数字、下划线，如 {default}），不接受明文凭据")
    return key


def validate_vin_tail(vin_tail: Optional[str]) -> str:
    val = "" if vin_tail is None else str(vin_tail).strip()
    if not VIN_TAIL_RE.fullmatch(val):
        raise ValueError("--vin-tail 须为 1-17 位字母数字车架号尾号（例如 000228）")
    return val


def _is_valid_bounded_number(val, *, max_s: float) -> bool:
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return False
    if isinstance(val, float) and not math.isfinite(val):
        return False
    return 0 < val <= max_s


def validate_timeout(timeout_s: float, *, max_s: float) -> float:
    if not _is_valid_bounded_number(timeout_s, max_s=max_s):
        raise ValueError(f"timeout_s 须为 (0, {int(max_s)}] 秒内的有限数字")
    return float(timeout_s)


def resolve_secret(ws, key: str) -> Optional[str]:
    val = os.environ.get(f"VPD_SECRET_{key}")
    if val is None:
        val = (ws.config.get("secrets") or {}).get(key)
    if val is None:
        return None
    s = str(val)
    return s if s else None


def needs_human(rec: dict) -> bool:
    return bool(rec.get("action_required")) or rec.get("status") == "state_indeterminate"


def visible_texts(tree) -> set[str]:
    if tree is None or tree.status != "ok":
        return set()
    out = set()
    for n in tree.nodes:
        t = (n.text or "").strip()
        if t and tree.visible_rect(n) and tree.visible_lineage(n):
            out.add(t)
    return out


def resolve_target(app, target_ref, tree, obs) -> tuple[str, dict, dict]:
    tid, target = app.target(target_ref)
    loc = resolve(tid, target, tree, None, app.dir, layout=obs.get("layout"))
    return tid, target, loc


def hit_node(tree, loc):
    if tree is None or tree.status != "ok" or loc.get("status") != "found":
        return None
    hit = next((a.get("node") for a in loc.get("attempts", []) if a.get("status") == "found" and a.get("node")), None)
    if hit is not None and 0 <= hit["idx"] < len(tree.nodes):
        return tree.nodes[hit["idx"]]
    return tree.node_at(*loc["point"]) if loc.get("point") else None


def read_target_text(app, target_ref: str, tree, obs) -> Optional[str]:
    if tree is None or tree.status != "ok" or target_ref not in app.targets:
        return None
    _, _, loc = resolve_target(app, target_ref, tree, obs)
    if loc.get("status") != "found":
        return None
    node = hit_node(tree, loc)
    if node is None:
        return None
    if (node.text or "").strip():
        return node.text.strip()
    texts = [t.strip() for t in tree.subtree_texts(node) if (t or "").strip()]
    return texts[0] if texts else None


def row_card_texts(tree, hit_node_obj) -> tuple[str, ...]:
    if tree is None or hit_node_obj is None:
        return ()
    holder = tree.clickable_container(hit_node_obj)
    if holder is None or holder.parent is None:
        holder = hit_node_obj
    texts = tuple(t.strip() for t in tree.subtree_texts(holder) if (t or "").strip())
    return texts or ((hit_node_obj.text or "").strip(),)


def swipe_points(tree, observer, direction: str = "up") -> Optional[tuple[int, int, int, int]]:
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
    y1, y2 = (bottom, top) if direction == "up" else (top, bottom)
    return x, y1, x, y2


def classify_qiankun_screen(app, obs, tree) -> tuple[str, Optional[dict]]:
    if obs.get("foreground") != app.bundle:
        return "out_of_app", None
    if tree is None or tree.status != "ok":
        return "unobservable", None
    texts = visible_texts(tree)
    if "请输入安全码" in texts:
        return "safe_code", None
    if "身份验证" in texts or "输入验证码" in texts or any(t.startswith("验证码已发至") for t in texts):
        return "sms_verify", None
    for intr in (obs.get("page") or {}).get("interrupts") or []:
        iid = intr.get("id") if isinstance(intr, dict) else intr
        if not isinstance(iid, str):
            continue
        spec = app.states.interrupts.get(iid) or {}
        if spec.get("class") == "benign" and spec.get("review") == "reviewed" and spec.get("dismiss_text"):
            return "benign_dialog", {"name": iid, "dismiss_text": spec["dismiss_text"]}
    _, _, pwd_loc = resolve_target(app, "qk_pwd_input", tree, obs)
    if pwd_loc.get("status") == "found":
        return "password_form", None
    if "密码登录" in texts:
        return "method_select", None
    if "其他登录方式" in texts:
        return "one_click", None
    if "登录/注册" in texts:
        return "logged_out_my", None
    state = (obs.get("page") or {}).get("state")
    if state in LOGGED_IN_STATES and not (texts & LOGIN_BLOCK_TEXTS):
        return "logged_in", None
    if ("我的车辆" in texts or {"爱车", "ADS", "我的"} <= texts) and not (texts & LOGIN_BLOCK_TEXTS):
        return "logged_in", None
    return "unknown", None


def normalize_capability_spec(entry) -> Optional[dict]:
    """Normalize a capability precondition or step declaration without mutating input."""
    if isinstance(entry, str):
        name = entry.strip()
        return {"capability": name} if name else None
    if isinstance(entry, dict):
        if "action" in entry and entry.get("action") == "capability":
            return {k: v for k, v in entry.items() if k not in _STEP_META_KEYS}
        return dict(entry)
    return None


def validate_capability_spec(entry, app_id: str, field: str) -> list[dict]:
    """Validate a capability precondition or action=capability step declaration."""
    spec = normalize_capability_spec(entry)
    if spec is None:
        return [{"field": field, "problem": "能力声明须为非空能力名字符串或对象"}]
    cap = spec.get("capability")
    if not isinstance(cap, str) or cap not in CAPABILITY_SPECS:
        return [{"field": field, "problem": f"未知原子能力 {cap!r}，当前支持 {sorted(CAPABILITY_SPECS)}"}]
    meta = CAPABILITY_SPECS[cap]
    issues = []
    if app_id not in meta["apps"]:
        issues.append({"field": field, "problem": f"原子能力 {cap} 当前仅支持 {list(meta['apps'])}，不支持 {app_id}"})
    unknown_keys = sorted(set(spec) - {"capability"} - set(meta["allowed_params"]))
    if unknown_keys:
        issues.append({"field": field, "problem": f"原子能力 {cap} 不支持参数 {unknown_keys}"})
    for req in meta["required_params"]:
        if req not in spec or spec[req] in (None, ""):
            issues.append({"field": f"{field}.{req}", "problem": f"原子能力 {cap} 缺少必需参数 {req}"})
    if "vin_tail" in spec and spec["vin_tail"] is not None:
        raw_vin = str(spec["vin_tail"]).strip()
        if not (_PARAM_PLACEHOLDER_RE.fullmatch(raw_vin) or VIN_TAIL_RE.fullmatch(raw_vin)):
            issues.append({"field": f"{field}.vin_tail", "problem": "vin_tail 须为 1-17 位字母数字或 ${var:x}/${param:x} 引用"})
    for key_field in ("account_secret", "password_secret"):
        if key_field in spec and spec[key_field] is not None:
            val = spec[key_field]
            if not isinstance(val, str) or not SECRET_KEY_RE.fullmatch(val):
                issues.append({"field": f"{field}.{key_field}",
                               "problem": f"{key_field} 只接受大写字母/数字/下划线键名，不接受明文凭据"})
    if "auto_login" in spec and type(spec["auto_login"]) is not bool:
        issues.append({"field": f"{field}.auto_login", "problem": "auto_login 须为布尔值"})
    if "timeout_s" in spec:
        ts = spec["timeout_s"]
        if not _is_valid_bounded_number(ts, max_s=meta["max_timeout_s"]):
            issues.append({"field": f"{field}.timeout_s",
                           "problem": f"timeout_s 须为 (0, {int(meta['max_timeout_s'])}] 秒内的有限数字"})
    return issues


def capability_required_authorizations(spec: dict) -> tuple[str, ...]:
    """Return all side-effect authorizations required by a normalized capability spec."""
    cap = spec.get("capability")
    if cap == "logged_in":
        return ("account_state",)
    if cap == "vehicle_vin":
        return ("vehicle_selection",)
    if cap == "vpd_ready":
        effects = ["vpd_entry"]
        if spec.get("vin_tail") not in (None, ""):
            effects.append("vehicle_selection")
        if spec.get("auto_login") is True:
            effects.append("account_state")
        return tuple(effects)
    return ()


class CapabilityRuntime:
    """Shared execution runtime for L2/L3 atomic capabilities."""

    def __init__(self, ws, app, device, store: RunStore, ctx: RunContext, *,
                 capability: str, prefix: str, sleep, classify: Callable, t0: float):
        self.ws = ws
        self.app = app
        self.device = device
        self.store = store
        self.ctx = ctx
        self.dispatcher = ctx.dispatcher
        self.observer = ctx.observer
        self.capability = capability
        self.prefix = prefix
        self.sleep = sleep
        self.classify = classify
        self.t0 = t0
        self.actions_start = ctx.actions
        self.last_obs_epoch = ctx.actions
        self.last_obs_id: Optional[str] = None
        self.steps: list[dict] = []

    @classmethod
    def open(cls, ws, app, device, *, kind: str, capability: str, prefix: str, granted: set[str],
             store: Optional[RunStore] = None, sleep=None, _ctx: Optional[RunContext] = None,
             extra_start: Optional[dict] = None, classify: Optional[Callable] = None,
             t0: Optional[float] = None) -> "CapabilityRuntime":
        if sleep is None:
            sleep = time.sleep if device.kind != "replay" else (lambda _: None)
        if _ctx is None:
            from .experience import ExperienceStore
            store = store or RunStore(ws.runs_dir, kind=kind)
            store.event("capability_start", capability=capability, app=app.id, device=device.spec,
                        **(extra_start or {}))
            ctx = RunContext(ws, app, device, store, ExperienceStore(ws.root, app.id).overlay(), mode="agent")
            ctx.policy.check_grants(granted)
            ctx.authorized = ctx.policy.effective_grants(granted)
        else:
            ctx = _ctx
            store = ctx.store
            store.event("capability_start", capability=capability, app=app.id, device=device.spec,
                        shared_budget=True, **(extra_start or {}))
        return cls(
            ws, app, device, store, ctx,
            capability=capability, prefix=prefix, sleep=sleep,
            classify=classify or classify_qiankun_screen,
            t0=time.monotonic() if t0 is None else t0,
        )

    def observe_fast(self, label: str) -> tuple[dict, object]:
        obs_out, tree_out = self.ctx.observe(label, screenshot=False)
        self.last_obs_epoch = self.ctx.actions
        self.last_obs_id = obs_out.get("obs_id")
        return obs_out, tree_out

    def finish(self, status: str, *, reason: Optional[str] = None, action_required: bool = False,
               final_shot: bool = False, obs_id: Optional[str] = None,
               fields: Optional[dict] = None, event_fields: Optional[dict] = None,
               extra: Optional[dict] = None) -> dict:
        final_obs = obs_id if obs_id is not None else self.last_obs_id
        shot_obs = None
        if final_shot:
            obs_f, _ = self.ctx.observe(f"{self.capability}_{status}", screenshot=True)
            shot_obs = obs_f["obs_id"]
            self.last_obs_id = shot_obs
            final_obs = shot_obs
        elapsed = round(time.monotonic() - self.t0, 2)
        used = self.ctx.actions - self.actions_start
        out = {
            "run": self.store.run_id,
            "app": self.app.id,
            "device": self.device.spec,
            "status": status,
            **(fields or {}),
            **({"reason": reason} if reason else {}),
            "action_required": action_required,
            "actions": used,
            "elapsed_s": elapsed,
            **({"final_obs": final_obs} if final_obs else {}),
            **({"final_shot_obs": shot_obs} if shot_obs else {}),
            "steps": self.steps,
            **(extra or {}),
        }
        self.store.event(
            "capability_end",
            capability=self.capability,
            status=status,
            **(event_fields if event_fields is not None else (fields or {})),
            actions=used,
            elapsed_s=elapsed,
            action_required=action_required,
        )
        return out

    def send_tap(self, step_name: str, target_ref, tree, obs, *,
                 allow_fallback_text: Optional[str] = None,
                 fallback_side_effect: str = "account_state") -> tuple[bool, dict]:
        tid, target, loc = resolve_target(self.app, target_ref, tree, obs)
        if loc.get("status") != "found" and allow_fallback_text:
            fb = {
                "label": f"{self.prefix}:{step_name}",
                "side_effect": fallback_side_effect,
                "strategies": [{"kind": "text", "value": allow_fallback_text}],
            }
            tid, target, loc = resolve_target(self.app, fb, tree, obs)
        if loc.get("status") != "found":
            rec = {"step": step_name, "target": tid, "status": loc.get("status"), "action_required": False}
            self.steps.append(rec)
            return False, rec
        att = {
            "action": "tap",
            "step_id": f"{self.prefix}_{step_name}",
            "dispatch_coordinates": {"system": "px", "point": loc["point"]},
        }
        tap = {
            "tree": tree,
            "loc": loc,
            "target": target,
            "target_ref": target_ref if isinstance(target_ref, str) else None,
            "page": obs.get("page"),
            "layout": obs.get("layout"),
        }
        res = self.dispatcher.send("tap", *loc["point"], preconditions=self.dispatcher.app_conditions("tap"),
                                   att=att, tap=tap)
        rec = {
            "step": step_name,
            "target": tid,
            "status": "ok" if res.ok else (att.get("status") or res.error_class or "failed"),
            "sent": res.sent,
            "action_required": bool(att.get("action_required")),
        }
        if att.get("execution_error"):
            rec["execution_error"] = att["execution_error"]
        self.steps.append(rec)
        return bool(res.ok), rec

    def send_back(self, step_name: str) -> tuple[bool, dict]:
        att = {"action": "back", "step_id": f"{self.prefix}_{step_name}"}
        res = self.dispatcher.send("key", "back", preconditions=self.dispatcher.app_conditions("back"), att=att)
        rec = {
            "step": step_name,
            "action": "back",
            "status": "ok" if res.ok else (att.get("status") or res.error_class or "failed"),
            "sent": res.sent,
            "action_required": bool(att.get("action_required")),
        }
        if att.get("execution_error"):
            rec["execution_error"] = att["execution_error"]
        self.steps.append(rec)
        return bool(res.ok), rec

    def send_swipe(self, step_name: str, tree, direction: str = "up") -> tuple[bool, dict]:
        pts = swipe_points(tree, self.observer, direction=direction)
        if not pts:
            rec = {"step": step_name, "action": "swipe", "status": "screen_size_unknown", "action_required": False}
            self.steps.append(rec)
            return False, rec
        att = {"action": "swipe", "step_id": f"{self.prefix}_{step_name}"}
        res = self.dispatcher.send("swipe", *pts, preconditions=self.dispatcher.app_conditions("swipe"), att=att)
        rec = {
            "step": step_name,
            "action": "swipe",
            "status": "ok" if res.ok else (att.get("status") or res.error_class or "failed"),
            "sent": res.sent,
            "action_required": bool(att.get("action_required")),
        }
        if att.get("execution_error"):
            rec["execution_error"] = att["execution_error"]
        self.steps.append(rec)
        return bool(res.ok), rec

    def dismiss_benign_dialog(self, step_name: str, detail: dict, tree, obs) -> tuple[bool, dict]:
        fb = {
            "label": f"dismiss:{detail['name']}",
            "side_effect": "dismiss_dialog",
            "strategies": [{"kind": "text", "value": detail["dismiss_text"]}],
        }
        return self.send_tap(step_name, fb, tree, obs)

    def check_challenge(self, obs, tree, *, stage: Optional[str] = None,
                        check_logged_out: bool = False,
                        logged_out_reason: Optional[str] = None) -> Optional[dict]:
        """Stop immediately and preserve foreground screen on human challenges."""
        kind, _ = self.classify(self.app, obs, tree)
        prefix = f"{stage}后" if stage else ""
        if kind == "safe_code":
            return self.finish("safe_code_required", reason=f"{prefix}命中安全码输入页，已原地保留现场等待人工处理",
                               action_required=True, final_shot=True)
        if kind == "sms_verify":
            return self.finish("sms_required", reason=f"{prefix}命中短信验证码页，已原地保留现场等待人工处理",
                               action_required=True, final_shot=True)
        if check_logged_out and kind in LOGGED_OUT_KINDS:
            return self.finish("login_required", reason=logged_out_reason or "当前处于未登录状态，需要登录后再继续",
                               action_required=True, final_shot=True)
        return None

    def unlock_and_bring_to_foreground(self, timeout_s: float, *, query_error_reason: str,
                                       replay_launch_polls: int = 4) -> tuple[Optional[dict], Optional[object], Optional[dict]]:
        locked = self.device.screen_locked()
        if locked is None:
            err = getattr(self.device, "screen_query_error", None)
            extra = ({"execution_error": {**err, "stage": f"{self.prefix}_unlock_query",
                                          "attribution": error_attribution(err.get("error_class"))}}
                     if err else None)
            return None, None, self.finish("indeterminate", reason=query_error_reason,
                                           action_required=True, extra=extra)
        if locked is True:
            ul = unlock_device(self.ws, self.device, self.store, dispatcher=self.dispatcher)
            self.steps.append({"step": "unlock", "status": ul["status"]})
            if ul["status"] != "unlocked":
                return None, None, self.finish(
                    "indeterminate" if ul["status"] != "pin_rejected" else "screen_locked",
                    reason=ul.get("reason", "自动解锁未完成"),
                    action_required=True,
                )

        obs, tree = self.observe_fast(f"{self.prefix}_initial")
        kind, _ = self.classify(self.app, obs, tree)
        if kind == "out_of_app":
            att = {"action": "launch", "step_id": f"{self.prefix}_launch"}
            lres = self.dispatcher.send("launch", self.app.bundle, self.app.ability,
                                        preconditions=self.dispatcher.app_conditions("launch"), att=att)
            self.steps.append({"step": "launch", "status": "ok" if lres.ok else (att.get("status") or lres.error_class)})
            if not lres.ok:
                return None, None, self.finish("indeterminate", reason="拉起被测 APP 失败", action_required=True)
            deadline = time.monotonic() + min(8.0, timeout_s)
            poll_n = 0
            while True:
                self.sleep(0.4)
                poll_n += 1
                obs, tree = self.observe_fast(f"{self.prefix}_after_launch")
                kind, _ = self.classify(self.app, obs, tree)
                if kind not in ("out_of_app", "unobservable", "unknown") or time.monotonic() >= deadline or (
                        self.device.kind == "replay" and poll_n >= replay_launch_polls):
                    break
        return obs, tree, None

    def poll_until(self, label_prefix: str, timeout_s: float, interval_s: float, evaluator: Callable, *,
                   replay_max_polls: int = 3, handle_benign_dialogs: bool = True,
                   dismiss_prefix: str = "post_dismiss", check_challenges: bool = True,
                   challenge_stage: Optional[str] = None,
                   on_timeout: Optional[Callable] = None) -> dict:
        """Poll observations until evaluator returns a terminal dict, a challenge hits, or timeout expires."""
        deadline = time.monotonic() + timeout_s
        poll_n = 0
        while True:
            self.sleep(interval_s)
            poll_n += 1
            obs, tree = self.observe_fast(f"{label_prefix}_{poll_n}")
            kind, detail = self.classify(self.app, obs, tree)
            if check_challenges:
                halted = self.check_challenge(obs, tree, stage=challenge_stage)
                if halted is not None:
                    return halted
            deadline_reached = time.monotonic() >= deadline or (
                self.device.kind == "replay" and poll_n >= replay_max_polls
            )
            if handle_benign_dialogs and kind == "benign_dialog" and detail:
                if not deadline_reached:
                    ok_d, rec_d = self.dismiss_benign_dialog(f"{dismiss_prefix}_{detail['name']}", detail, tree, obs)
                    if not ok_d and (needs_human(rec_d) or rec_d.get("status") in FATAL_DISPATCH_STATUSES):
                        return self.finish("blocked", reason=f"关闭弹窗 {detail['name']} 失败：{rec_d['status']}",
                                           action_required=True, final_shot=True)
            else:
                res = evaluator(obs, tree, kind, detail, poll_n)
                if res is not None:
                    return res
            if deadline_reached:
                if on_timeout is not None:
                    return on_timeout(obs, tree, kind, detail)
                return self.finish("timeout", reason=f"{timeout_s}s 内未达到预期终态（当前：{kind}）",
                                   action_required=True, final_shot=True)

    @staticmethod
    def _usable_tree(obs: Optional[dict], tree) -> bool:
        return bool(
            obs and obs.get("fresh") is not False
            and not (obs.get("page") or {}).get("observation_error")
            and tree is not None and getattr(tree, "status", None) == "ok"
        )

    def scroll_to_edge(self, direction: str, *, step_prefix: str, obs_prefix: str,
                       max_swipes: int = 6, settle_unchanged: int = 2, interval_s: float = 0.35,
                       sig_fn: Optional[Callable] = None, stage: str = "滚动列表",
                       obs=None, tree=None) -> tuple[bool, dict, object, Optional[dict]]:
        """Swipe in `direction` until `sig_fn(tree)` stays unchanged `settle_unchanged` times."""
        sig_fn = sig_fn or visible_texts
        if obs is None or tree is None:
            obs, tree = self.observe_fast(f"{obs_prefix}_0")
        halted = self.check_challenge(obs, tree)
        if halted is not None:
            return False, obs, tree, halted
        sig = sig_fn(tree) if self._usable_tree(obs, tree) else None
        unchanged = 0
        for swipe_i in range(max_swipes):
            ok, rec = self.send_swipe(f"{step_prefix}_{swipe_i + 1}", tree, direction=direction)
            if not ok:
                return False, obs, tree, self.finish("blocked", reason=f"{stage}失败：{rec['status']}",
                                                     action_required=needs_human(rec), final_shot=True)
            self.sleep(interval_s)
            obs, tree = self.observe_fast(f"{obs_prefix}_{swipe_i + 1}")
            halted = self.check_challenge(obs, tree, stage=stage)
            if halted is not None:
                return False, obs, tree, halted
            if not self._usable_tree(obs, tree):
                unchanged = 0
                sig = None
                continue
            nxt = sig_fn(tree)
            if sig is not None and nxt == sig:
                unchanged += 1
                if unchanged >= settle_unchanged:
                    return True, obs, tree, None
            else:
                unchanged = 0
                sig = nxt
        return False, obs, tree, None

    def bounded_scroll_scan(self, direction: str, *, step_prefix: str, obs_prefix: str,
                            on_page: Callable, obs: dict, tree,
                            max_swipes: int = 6, settle_unchanged: int = 2, interval_s: float = 0.35,
                            sig_fn: Optional[Callable] = None,
                            stage: str = "滚动扫描") -> tuple[bool, dict, object, Optional[dict]]:
        """Scan pages across bounded swipes, checking `on_page` before swipes and at edge settle."""
        sig_fn = sig_fn or visible_texts
        halted = self.check_challenge(obs, tree)
        if halted is not None:
            return False, obs, tree, halted
        seen_sig = sig_fn(tree) if self._usable_tree(obs, tree) else None
        unchanged = 0
        for swipe_i in range(max_swipes + 1):
            early = on_page(obs, tree, swipe_i)
            if early is not None:
                return False, obs, tree, early
            if swipe_i == max_swipes:
                break
            ok, rec = self.send_swipe(f"{step_prefix}_{swipe_i + 1}", tree, direction=direction)
            if not ok:
                return False, obs, tree, self.finish("blocked", reason=f"{stage}失败：{rec['status']}",
                                                     action_required=needs_human(rec), final_shot=True)
            self.sleep(interval_s)
            obs, tree = self.observe_fast(f"{obs_prefix}_{swipe_i + 1}")
            halted = self.check_challenge(obs, tree, stage=stage)
            if halted is not None:
                return False, obs, tree, halted
            if not self._usable_tree(obs, tree):
                unchanged = 0
                seen_sig = None
                continue
            new_sig = sig_fn(tree)
            if seen_sig is not None and new_sig == seen_sig:
                unchanged += 1
                if unchanged >= settle_unchanged:
                    end_res = on_page(obs, tree, swipe_i + 1)
                    if end_res is not None:
                        return True, obs, tree, end_res
                    return True, obs, tree, None
            else:
                unchanged = 0
                seen_sig = new_sig
        return False, obs, tree, None
