"""Harmony 真机适配器：直接调用 hdc / uitest / aa / bm。

复用：命令词汇与 app/utils/hdc_util.py、hmdriver2 1.4.3 一致（dumpLayout、uiInput、aa start、
bm dump、hidumper）。适配：参数用列表传递（不拼 shell 字符串）、每条命令有超时、hdc 退出码
恒为 0 时按输出内容判失败、dumpLayout 限定 -b <bundle> 避免采集其他 APP 窗口内容。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from . import DeviceResult

KEYS = {"back": "Back", "home": "Home"}
_FAIL_MARKERS = ("[fail]", "error:", "error code:")
_LOCKED_MARKERS = ("10106102", "screen is locked")
# 2026-09-29 真机运行中出现：hdc 通道重建期间所有命令都返回该错误，几秒后自行恢复
_TRANSPORT_MARKERS = ("e000004", "communication channel is being established")


def classify_output(text: str) -> Optional[str]:
    """hdc 在失败时常常仍返回 0，必须看输出。返回错误类别或 None。"""
    low = (text or "").lower()
    if any(m in low for m in _LOCKED_MARKERS):
        return "screen_locked"
    if any(m in low for m in _TRANSPORT_MARKERS):
        return "transport_unavailable"
    if "not match target" in low or "connect-key" in low:
        return "device_offline"
    if any(m in low for m in _FAIL_MARKERS):
        return "command_failed"
    return None


def parse_foreground(dump: str) -> Optional[str]:
    """解析 `aa dump -l`：返回 state #FOREGROUND 的 mission 对应的 bundle name。"""
    bundle = None
    for line in (dump or "").splitlines():
        s = line.strip()
        m = re.match(r"bundle name \[(.*?)\]", s)
        if m:
            bundle = m.group(1)
        elif re.match(r"state #FOREGROUND\b", s) and bundle:
            return bundle
        elif s.startswith("Mission ID"):
            bundle = None
    return None


def parse_kv(dump: str, key: str) -> Optional[str]:
    m = re.search(rf"\b{re.escape(key)}\s*:?\s+(\w+)", dump or "")
    return m.group(1) if m else None


def parse_version(dump: str) -> Optional[str]:
    for v in re.findall(r'"versionName":\s*"([^"]*)"', dump or ""):
        if v:
            return v
    return None


_SECTION_RE = re.compile(r"^-+\s*(Screen|Display) ID:\s*(\d+)\s*-+$")
_KV_RE = re.compile(r"^([A-Za-z][\w<>, ]*?):?\s{2,}(.+?)\s*,?\s*$")
_FOLD_MAP = {"EXPAND": "expanded", "FOLDED": "folded", "HALF_FOLD": "half_folded", "UNKNOWN": "unknown"}


def parse_display(dump: str) -> dict:
    """解析 `hidumper -s DisplayManagerService -a -a`，只读 Screen ID / Display ID 段（该输出的其他段含
    后台进程列表，不读取、不保存）。字段按 2026-09-29 Mate 80 Pro Max（直板）真机输出校准；
    折叠屏上的多屏与折叠状态字段未经真机验证。"""
    sections, cur = {}, None
    for raw in (dump or "").splitlines():
        line = raw.strip()
        m = _SECTION_RE.match(line)
        if m:
            cur = sections.setdefault(f"{m.group(1).lower()}:{m.group(2)}", {})
            continue
        if line.startswith("-----"):  # 其他分隔行结束当前段
            cur = None
            continue
        if cur is None:
            continue
        kv = _KV_RE.match(line)
        if kv and kv.group(1) not in cur:  # 同段内重复的键取第一次出现
            cur[kv.group(1).strip()] = kv.group(2).strip()
    disp = sections.get("display:0", {})
    screens = [v for k, v in sections.items() if k.startswith("screen:")]
    screen = next((s for s in screens if disp.get("Width") and
                   (s.get("Bounds<L,T,W,H>") or "").replace(" ", "").split(",")[2:4] == [disp["Width"], disp.get("Height")]),
                  screens[0] if screens else {})

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    w = num(disp.get("Width")) or num(screen.get("Width"))
    h = num(disp.get("Height")) or num(screen.get("Height"))
    density = num(screen.get("VirtualPixelRatio")) or num(screen.get("Density"))
    fold_raw = screen.get("DisplayFoldStatus") or screen.get("PhysicalFoldStatus")
    return {"width_px": int(w) if w else None, "height_px": int(h) if h else None, "density": density,
            "rotation": screen.get("Rotation"), "fold_status": _FOLD_MAP.get(fold_raw, fold_raw.lower() if fold_raw else None),
            "fold_status_raw": fold_raw, "screens": len(screens)}


def infer_device_form(fold_param: Optional[str], fold_status: Optional[str]) -> tuple:
    """直板/折叠是设备属性，只用于记录与分组；布局判断看窗口 vp 宽度。"""
    if fold_param or fold_status in ("expanded", "folded", "half_folded"):
        return "foldable", "const.window.foldscreen.type 存在或折叠状态为已知值"
    if fold_status in (None, "unknown"):
        return "bar", "无 const.window.foldscreen.type 且折叠状态 UNKNOWN（直板机真机观测）"
    return None, "无法判断"


class HdcDevice:
    kind = "hdc"

    def __init__(self, serial: Optional[str] = None, hdc: str = "hdc", timeout_s: float = 20.0):
        self.hdc, self.timeout_s = hdc, timeout_s
        self.serial = serial or self._first_target()
        self.poll_interval = 1.0
        self.spec = f"hdc:{self.serial}"
        self.last_query_error = None

    # ---- 基础执行 ----
    def _run(self, args: list, op: str, timeout: Optional[float] = None, with_target: bool = True) -> DeviceResult:
        cmd = [self.hdc] + (["-t", self.serial] if with_target and self.serial else []) + args
        t0 = time.monotonic()
        try:
            # Windows 上不为 hdc 子进程弹出控制台窗口（控制台常驻或无窗口宿主中运行时尤其明显）
            r = subprocess.run(cmd, capture_output=True, timeout=timeout or self.timeout_s,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except FileNotFoundError:
            return DeviceResult(op, False, False, "tool_missing", f"找不到 {self.hdc}，请确认已加入 PATH")
        except subprocess.TimeoutExpired as e:
            return DeviceResult(op, True, False, "timeout", f"{op} 超过 {e.timeout}s 未返回",
                                duration_ms=int((time.monotonic() - t0) * 1000))
        out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
        ms = int((time.monotonic() - t0) * 1000)
        err = classify_output(out) or ("command_failed" if r.returncode != 0 else None)
        return DeviceResult(op, True, err is None, err, out.strip()[:300] if err else None, out, ms)

    def _shell(self, *args, op: str, timeout: Optional[float] = None) -> DeviceResult:
        return self._run(["shell", *[str(a) for a in args]], op, timeout)

    def _first_target(self) -> Optional[str]:
        r = self._run(["list", "targets"], "list_targets", with_target=False)
        lines = [ln.strip() for ln in r.raw.splitlines() if ln.strip() and "Empty" not in ln]
        return lines[0] if r.ok and lines else None

    # ---- 状态采集 ----
    def _param(self, prop: str) -> Optional[str]:
        r = self._shell("param", "get", prop, op="param_get")
        v = r.raw.strip() if r.ok else ""
        return None if (not v or "fail" in v.lower()) else v  # 不存在的参数输出 "Get parameter ... fail!"

    def info(self) -> dict:
        out = {"serial": self.serial, "kind": self.kind}
        for key, prop in (("model", "const.product.model"), ("name", "const.product.name"),
                          ("os", "const.ohos.fullname"), ("software", "const.product.software.version")):
            out[key] = self._param(prop)
        return out

    def display_info(self) -> dict:
        r = self._shell("hidumper", "-s", "DisplayManagerService", "-a", "-a", op="display_dump")
        if not r.ok:
            return {"source": "hidumper DisplayManagerService", "error_class": r.error_class, "error": r.error,
                    "density": None}
        d = parse_display(r.raw)
        fold_param = self._param("const.window.foldscreen.type")
        d["device_form"], d["device_form_basis"] = infer_device_form(fold_param, d.get("fold_status"))
        d.update(foldscreen_type=fold_param, source="hidumper DisplayManagerService")
        return d

    def app_version(self, bundle: str) -> Optional[str]:
        r = self._shell("bm", "dump", "-n", bundle, op="bm_dump")
        return parse_version(r.raw) if r.ok else None

    def preflight(self, bundle: str) -> dict:
        checks = []
        if not self.serial:
            checks.append({"check": "device_online", "ok": False, "error_class": "device_offline",
                           "detail": "hdc list targets 没有设备"})
            return {"ok": False, "checks": checks, "device": {"serial": None}}
        power = self._shell("hidumper", "-s", "3301", "-a", "-a", op="power_state")
        checks.append({"check": "device_online", "ok": power.error_class != "device_offline",
                       "error_class": power.error_class if power.error_class == "device_offline" else None,
                       "detail": (power.error or "")[:120]})
        state = parse_kv(power.raw, "Current State")
        checks.append({"check": "screen_awake", "ok": state == "AWAKE", "value": state,
                       "error_class": None if state == "AWAKE" else "screen_off"})
        lock = self._shell("hidumper", "-s", "ScreenlockService", "-a", "-all", op="lock_state")
        locked = parse_kv(lock.raw, "screenLocked")
        checks.append({"check": "screen_unlocked", "ok": locked == "false", "value": locked,
                       "error_class": None if locked == "false" else "screen_locked"})
        ver = self.app_version(bundle)
        checks.append({"check": "app_installed", "ok": ver is not None, "value": ver,
                       "error_class": None if ver else "app_missing"})
        return {"ok": all(c["ok"] for c in checks), "checks": checks, "device": self.info(), "app_version": ver,
                "display": self.display_info()}

    def foreground(self) -> Optional[str]:
        """查询失败时返回 None 并在 last_query_error 记录原因，避免把"查不到"当成"不在 APP 内"。"""
        r = self._shell("aa", "dump", "-l", op="aa_dump")
        self.last_query_error = None if r.ok else (r.error_class or "command_failed")
        return parse_foreground(r.raw) if r.ok else None

    def process_alive(self, bundle: str) -> Optional[bool]:
        r = self._shell("pidof", bundle, op="pidof")
        return bool(r.raw.strip()) if r.sent and r.error_class in (None, "command_failed") else None

    def _pull(self, remote: str, local: Path, op: str) -> DeviceResult:
        r = self._run(["file", "recv", remote, str(local)], op)
        self._shell("rm", "-f", remote, op="cleanup")
        if r.ok and not local.exists():
            r.ok, r.error_class, r.error = False, "transfer_failed", "file recv 未生成本地文件"
        return r

    def dump_tree(self, bundle: str, local_path) -> DeviceResult:
        remote = f"/data/local/tmp/vpd_{uuid.uuid4().hex[:8]}.json"
        local = Path(local_path)
        r = self._shell("uitest", "dumpLayout", "-b", bundle, "-p", remote, op="dump_tree", timeout=30)
        if not r.ok:
            return r
        p = self._pull(remote, local, "dump_tree_pull")
        if not p.ok:
            return p
        try:
            r.data["tree"] = json.loads(local.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            r.ok, r.error_class, r.error = False, "tree_parse_error", str(e)
        return r

    def screenshot(self, local_path) -> DeviceResult:
        remote = f"/data/local/tmp/vpd_{uuid.uuid4().hex[:8]}.png"
        r = self._shell("uitest", "screenCap", "-p", remote, op="screenshot", timeout=30)
        if r.ok and "saved" not in r.raw.lower():
            # 2026-09-29 真机：投屏帧流（uitest Captures）运行期间 screenCap 输出
            # "ScreenCap failed: Failed to get display pixelMap"，不含上面的失败标记
            r.ok, r.error_class, r.error = False, "command_failed", r.raw.strip()[:300]
        if r.ok:
            return self._pull(remote, Path(local_path), "screenshot_pull")
        if r.error_class in ("screen_locked", "device_offline", "transport_unavailable", "tool_missing"):
            return r  # 设备侧前提不满足，换一种截图方式也一样失败，保留原始错误类别
        # 回退 snapshot_display（同为整屏截图，帧流运行期间真机实测可用），并记下回退原因
        s = self.snapshot(local_path, fmt="png")
        s.data["fallback_from"] = {"op": "uitest screenCap", "error": r.error}
        return s

    def snapshot(self, local_path, fmt: str = "jpeg") -> DeviceResult:
        """snapshot_display 整屏截图（jpeg 约 70KB，png 约 1.7MB，1320×2848 真机实测）。"""
        remote = f"/data/local/tmp/vpd_{uuid.uuid4().hex[:8]}.{fmt}"
        r = self._shell("snapshot_display", "-f", remote, "-t", fmt, op="snapshot", timeout=30)
        if r.ok and "success" not in r.raw.lower():
            r.ok, r.error_class, r.error = False, "command_failed", r.raw.strip()[:300]
        return self._pull(remote, Path(local_path), "snapshot_pull") if r.ok else r

    # ---- 动作 ----
    def tap(self, x: int, y: int) -> DeviceResult:
        return self._shell("uitest", "uiInput", "click", int(x), int(y), op="tap")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, speed: int = 2000) -> DeviceResult:
        return self._shell("uitest", "uiInput", "swipe", int(x1), int(y1), int(x2), int(y2), int(speed), op="swipe")

    @staticmethod
    def _text_arg(text: str, op: str):
        """返回 (设备端 shell 参数, None) 或 (None, 不支持时的 DeviceResult)。"""
        if re.search(r"\s", text):
            return None, DeviceResult(op, False, False, "unsupported", "v0.1 的 uiInput 文本输入不支持含空白的文本")
        if not re.fullmatch(r"[A-Za-z0-9_.@+\-一-鿿]*", text):
            # hdc shell 的参数会经过设备端 shell：含 $ ; & | ' " 等字符的文本（例如密码）用单引号包起来，
            # 避免被当成 shell 语法。设备端 shell 的转义行为未在真机上逐字符验证
            text = "'" + text.replace("'", "'\\''") + "'"
        return text, None

    def input_text(self, x: int, y: int, text: str) -> DeviceResult:
        arg, bad = self._text_arg(text, "input_text")
        return bad or self._shell("uitest", "uiInput", "inputText", int(x), int(y), arg, op="input_text")

    def type_focused(self, text: str) -> DeviceResult:
        """向当前已聚焦的输入框输入（uiInput text），不先点击坐标。"""
        arg, bad = self._text_arg(text, "type_focused")
        return bad or self._shell("uitest", "uiInput", "text", arg, op="type_focused")

    def long_press(self, x: int, y: int) -> DeviceResult:
        return self._shell("uitest", "uiInput", "longClick", int(x), int(y), op="long_press")

    def key(self, name: str) -> DeviceResult:
        if name not in KEYS:
            return DeviceResult("key", False, False, "unsupported", f"不支持的按键 {name}")
        return self._shell("uitest", "uiInput", "keyEvent", KEYS[name], op=f"key_{name}")

    def wakeup(self) -> DeviceResult:
        """只点亮屏幕，不解锁（锁屏密码不由工具输入）。"""
        return self._shell("power-shell", "wakeup", op="wakeup")

    def screen_off_override(self) -> Optional[int]:
        """当前的熄屏时间覆盖值（ms）。锁屏状态下系统自己会设 10000ms（2026-09-29 真机观测），没有覆盖时为 None。"""
        r = self._shell("hidumper", "-s", "3301", "-a", "-a", op="power_state")
        m = re.search(r"OverrideTimeout=(\d+)ms", r.raw) if r.ok else None
        return int(m.group(1)) if m else None

    def set_screen_off(self, ms: Optional[int]) -> DeviceResult:
        """覆盖熄屏时间；ms 为 None 时撤销覆盖（power-shell timeout -o / -r）。"""
        if ms is None:
            return self._shell("power-shell", "timeout", "-r", op="screen_off_restore")
        return self._shell("power-shell", "timeout", "-o", int(ms), op="screen_off_override")

    def launch(self, bundle: str, ability: str) -> DeviceResult:
        r = self._shell("aa", "start", "-a", ability, "-b", bundle, op="launch")
        if r.ok and "success" not in r.raw.lower():
            r.ok, r.error_class, r.error = False, "command_failed", r.raw.strip()[:300]
        return r

    def stop(self, bundle: str) -> DeviceResult:
        return self._shell("aa", "force-stop", bundle, op="stop")

    def env_facts(self) -> dict:
        return {}
