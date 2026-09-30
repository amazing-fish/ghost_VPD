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


_WIN_ROW_RE = re.compile(r"^(\S+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(-?\d+)\s+\d+\s+\[\s*-?\d+\s+-?\d+\s+(\d+)\s+(\d+)\s*\]")


def parse_windows(dump: str) -> Optional[list]:
    """解析 `hidumper -s WindowManagerService -a -a` 的窗口表，返回正在显示的窗口名（ZOrd≠-1 且有面积）。

    名称被截断到 20 个字符（如 SCBBannerNotificatio）。认不出表头时返回 None：格式不明不能当成"没有弹层"。
    """
    if "WindowName" not in (dump or ""):
        return None
    shown = []
    for line in dump.splitlines():
        m = _WIN_ROW_RE.match(line.strip())
        if m and m.group(2) != "-1" and int(m.group(3)) > 0 and int(m.group(4)) > 0:
            shown.append(m.group(1))
    return shown


def find_pin_pad(tree_raw: dict) -> Optional[dict]:
    """锁屏数字键盘：0-9 各恰好一个可点击容器，且 1-9 排成 3 列 × 3 行、0 在 8 的正下方。认不准 → None（不猜着点）。"""
    from ..tree import UiTree
    t = UiTree(tree_raw)
    keys = {}
    for n in t.nodes:
        if n.text in "0123456789" and len(n.text or "") == 1 and t.shown(n):
            h = t.clickable_container(n)
            if not h.clickable:
                continue
            if n.text in keys and keys[n.text].bounds != h.bounds:
                return None  # 同一数字出现两个可点击位置（比如通知里的数字按钮）：不认
            keys[n.text] = h
    if sorted(keys) != list("0123456789"):
        return None
    cx = {d: (keys[d].bounds[0] + keys[d].bounds[2]) // 2 for d in keys}
    cy = {d: (keys[d].bounds[1] + keys[d].bounds[3]) // 2 for d in keys}
    tol = max(8, (keys["1"].bounds[2] - keys["1"].bounds[0]) // 4)
    rows, cols = ("123", "456", "789"), ("147", "258", "369")
    grid_ok = all(abs(cy[r[0]] - cy[d]) <= tol for r in rows for d in r) \
        and all(abs(cx[c[0]] - cx[d]) <= tol for c in cols for d in c) \
        and abs(cx["0"] - cx["8"]) <= tol and cy["0"] > cy["8"] and cy["4"] > cy["1"] and cx["2"] > cx["1"]
    return {d: (cx[d], cy[d]) for d in keys} if grid_ok else None


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


def unlock_swipe_points(disp: dict) -> Optional[tuple]:
    """锁屏上滑起止点按当前显示尺寸比例算（直板/折叠展开/横屏都适用）：水平居中，从 84% 高滑到 32% 高。
    比例取自 Mate 80（1320×2848）上验证过的 660,2400→660,900。尺寸拿不到 → None，由调用方停下。"""
    w, h = (disp or {}).get("width_px"), (disp or {}).get("height_px")
    if not w or not h:
        return None
    return int(w * 0.5), int(h * 0.843), int(w * 0.5), int(h * 0.316)


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
            r = subprocess.run(cmd, capture_output=True, timeout=timeout or self.timeout_s)
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
        return self._pull(remote, Path(local_path), "screenshot_pull") if r.ok else r

    # ---- 动作 ----
    def tap(self, x: int, y: int) -> DeviceResult:
        return self._shell("uitest", "uiInput", "click", int(x), int(y), op="tap")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, speed: int = 2000) -> DeviceResult:
        return self._shell("uitest", "uiInput", "swipe", int(x1), int(y1), int(x2), int(y2), int(speed), op="swipe")

    def input_text(self, x: int, y: int, text: str) -> DeviceResult:
        if re.search(r"\s", text):
            return DeviceResult("input_text", False, False, "unsupported", "v0.1 的 uiInput inputText 不支持含空白的文本")
        if not re.fullmatch(r"[A-Za-z0-9_.@+\-一-鿿]*", text):
            # hdc shell 的参数会经过设备端 shell：含 $ ; & | ' " 等字符的文本（例如密码）用单引号包起来，
            # 避免被当成 shell 语法。设备端 shell 的转义行为未在真机上逐字符验证
            text = "'" + text.replace("'", "'\\''") + "'"
        return self._shell("uitest", "uiInput", "inputText", int(x), int(y), text, op="input_text")

    def key(self, name: str) -> DeviceResult:
        if name not in KEYS:
            return DeviceResult("key", False, False, "unsupported", f"不支持的按键 {name}")
        return self._shell("uitest", "uiInput", "keyEvent", KEYS[name], op=f"key_{name}")

    def screen_locked(self) -> Optional[bool]:
        """三态：锁屏/熄屏 → True；亮屏且已解锁 → False；查询失败或输出认不出 → None。
        锁屏时 aa dump 里没有 FOREGROUND mission，不能据此当成桌面。"""
        power = self._shell("hidumper", "-s", "3301", "-a", "-a", op="power_state")
        state = parse_kv(power.raw, "Current State") if power.ok else None
        if state is None:
            return None
        if state != "AWAKE":
            return True
        lock = self._shell("hidumper", "-s", "ScreenlockService", "-a", "-all", op="lock_state")
        return {"true": True, "false": False}.get(parse_kv(lock.raw, "screenLocked") if lock.ok else None)

    def wake_unlock(self, pin: Optional[str]) -> dict:
        """唤醒并解锁：先上滑（人脸/无密码会直接解开），仍锁着才输一次 PIN。

        每个会发出手势/按键的步骤之前都要 screen_locked() 明确为 True；查询失败（None）就停下，返回
        indeterminate，不发手势——已解锁的手机上一次上滑会划到用户正在用的应用里。
        只输一次：连续输错会锁机，重试由调用方（有防重入标记）决定。锁屏的全量布局只在内存里解析
        （通知内容会出现在里面），不落盘；PIN 和按键坐标都不进返回值。
        """
        def unknown(stage: str, **kw) -> dict:
            return {"status": "indeterminate", "stage": stage, **kw,
                    "reason": f"{stage}时查不到锁屏状态（hidumper 失败或输出认不出），未继续发手势：请人工确认屏幕状态"}

        locked = self.screen_locked()
        if locked is False:
            return {"status": "unlocked", "method": "already"}
        if locked is None:
            return unknown("唤醒前")
        self._shell("power-shell", "wakeup", op="wakeup")
        time.sleep(1.0)
        locked = self.screen_locked()
        if locked is False:
            return {"status": "unlocked", "method": "wake"}
        if locked is None:
            return unknown("上滑前")
        swipe = unlock_swipe_points(self.display_info())
        if swipe is None:
            return {"status": "indeterminate", "stage": "上滑前",
                    "reason": "查不到屏幕尺寸，无法计算上滑位置，未发手势：请人工解锁"}
        self._shell("uitest", "uiInput", "swipe", *swipe, 3000, op="unlock_swipe")
        time.sleep(1.5)
        locked = self.screen_locked()
        if locked is False:
            return {"status": "unlocked", "method": "swipe"}
        if locked is None:
            return unknown("输入 PIN 前")
        if not pin:
            return {"status": "locked", "reason": "需要 PIN：config.local.json secrets.LOCK_PIN 或 VPD_SECRET_LOCK_PIN 未设置"}
        remote = f"/data/local/tmp/vpd_{uuid.uuid4().hex[:8]}.json"
        r = self._shell("uitest", "dumpLayout", "-p", remote, op="lock_dump", timeout=30)
        raw = self._shell("cat", remote, op="lock_dump_read").raw if r.ok else ""
        self._shell("rm", "-f", remote, op="cleanup")
        try:
            pad = find_pin_pad(json.loads(raw)) if raw else None
        except ValueError:
            pad = None
        if pad is None:
            return {"status": "locked", "reason": "没认出锁屏数字键盘（0-9 各一个、3 列排布），未输入"}
        for ch in pin:
            if not self.tap(*pad[ch]).ok:
                # 输到一半：已输的位数不确定，按"输过一次 PIN"处理，由调用方写防重入标记
                return unknown("输入 PIN 中", pin_entered=True)
            time.sleep(0.25)
        time.sleep(1.5)
        locked = self.screen_locked()
        if locked is False:
            return {"status": "unlocked", "method": "pin"}
        if locked is None:
            return unknown("输完 PIN 后", pin_entered=True)
        return {"status": "pin_rejected", "reason": "输完 PIN 仍锁屏：不再重试，请人工核对"}

    def shown_windows(self) -> Optional[list]:
        """当前显示的窗口名；查询失败或格式认不出 → None。只取窗口名与层级，不取窗口内容。"""
        r = self._shell("hidumper", "-s", "WindowManagerService", "-a", "-a", op="window_list")
        return parse_windows(r.raw) if r.ok else None

    def clear_text(self, x: int, y: int, count: int) -> DeviceResult:
        """真机 uiInput inputText 是追加：先点中输入框、光标移到末尾（MoveEnd=2082），再按 count 次退格（Del=2055）。"""
        r = self.tap(x, y)
        if not r.ok:
            return r
        r = self._shell("uitest", "uiInput", "keyEvent", "2082", op="key_move_end")
        if not r.ok:
            # 光标位置未知：此时退格会删掉中间的字，后续输入得到错值。不删，报错（输入框已被点过）
            return DeviceResult("clear_text", True, False, r.error_class or "command_failed",
                                f"MoveEnd 失败，未删除：{r.error or ''}".strip(), r.raw, r.duration_ms)
        for _ in range(max(0, int(count))):
            r = self._shell("uitest", "uiInput", "keyEvent", "2055", op="key_del")
            if not r.ok:
                return r
        return DeviceResult("clear_text", True, True, None, None, f"deleted {count}")

    def launch(self, bundle: str, ability: str) -> DeviceResult:
        r = self._shell("aa", "start", "-a", ability, "-b", bundle, op="launch")
        if r.ok and "success" not in r.raw.lower():
            r.ok, r.error_class, r.error = False, "command_failed", r.raw.strip()[:300]
        return r

    def stop(self, bundle: str) -> DeviceResult:
        return self._shell("aa", "force-stop", bundle, op="stop")

    def env_facts(self) -> dict:
        return {}
