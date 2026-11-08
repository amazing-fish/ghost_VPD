"""Harmony 真机适配器：直接调用 hdc / uitest / aa / bm。

复用：命令词汇与 app/utils/hdc_util.py、hmdriver2 1.4.3 一致（dumpLayout、uiInput、aa start、
bm dump、hidumper）。适配：参数用列表传递（不拼 shell 字符串）、每条命令有超时、hdc 退出码
恒为 0 时按输出内容判失败、dumpLayout 限定 -b <bundle> 避免采集其他 APP 窗口内容。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from . import LAUNCHER, DeviceResult

KEYS = {"back": "Back", "home": "Home", "move_end": "2082", "delete": "2055"}
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


def foreground_from_evidence(aa_dump: str, windows: Optional[list]) -> Optional[str]:
    """前台判定：aa dump 的 #FOREGROUND 任务为准；OpenHarmony 6.x（Mate 80 6.1.1.120 实测）
    桌面前台时任务列表里没有 #FOREGROUND——用窗口表兜底：正在显示的窗口全部属于 SCB*
    （sceneboard：桌面/壁纸/状态栏/导航条）时判为桌面前台（LAUNCHER）。
    窗口表里有非 SCB 窗口却对不上任何 FOREGROUND 任务、或窗口表查不到 → None（不猜）。"""
    fg = parse_foreground(aa_dump)
    if fg is None and windows and all(w.startswith("SCB") for w in windows):
        return LAUNCHER
    return fg


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
    """锁屏数字键盘：0-9 各恰好一个可点击容器，且 1-9 排成 3 列 × 3 行、0 在 8 的正下方。认不准 → None（不猜着点）。
    数字取 text，text 为空时取 originalText：Pura X Max（OpenHarmony 6.1.1.135）锁屏键盘的 text 全空、数字只在
    originalText 里（2026-10-01 真机 dumpLayout）；Mate 80 上 text 就是数字。"""
    from ..tree import UiTree
    t = UiTree(tree_raw)
    keys = {}
    for n in t.nodes:
        digit = n.text or str(n.attrs.get("originalText", "") or "")
        if len(digit) == 1 and digit in "0123456789" and t.shown(n):
            h = t.clickable_container(n)
            if not h.clickable:
                continue
            if digit in keys and keys[digit].bounds != h.bounds:
                return None  # 同一数字出现两个可点击位置（比如通知里的数字按钮）：不认
            keys[digit] = h
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


BLANK_PIN_LAYOUT = "standard_3x4_blank"


def find_blank_pin_pad(tree_raw: dict) -> Optional[dict]:
    """仅供逐设备显式配置使用：完整无标签 3×4 等大格；不从部分标签推断数字。"""
    from ..tree import UiTree
    t = UiTree(tree_raw)
    cells = [n for n in t.nodes if n.clickable]
    # 不过滤坏格后挑选 12 个，也不把重叠/嵌套按钮按 bounds 去重。
    if len(cells) != 12 or not t.nodes or t.nodes[0].bounds != t.window_rect():
        return None
    # 标签路径没形成完整键盘，不代表当前界面无标签；独立数字也可能属于另一套布局。
    for node in t.nodes:
        if t.shown(node) and any(re.fullmatch(r"[0-9]", str(node.attrs.get(field, "")).strip())
                                 for field in ("text", "originalText")):
            return None
    for cell in cells:
        current = cell
        while current is not None:
            if not current.visible or not current.enabled:
                return None
            current = t.nodes[current.parent] if current.parent is not None else None
        if cell.bounds[0] < 0 or cell.bounds[1] < 0 or t.visible_rect(cell) != cell.bounds:
            return None  # 越界、裁剪或模态遮挡均不得按完整格处理。
        pending = [cell.idx]
        while pending:
            node = t.nodes[pending.pop()]
            values = (node.attrs.get(field) for field in ("text", "originalText", "description", "id"))
            if any(value is not None and (not isinstance(value, str) or value.strip()) for value in values):
                return None
            pending.extend(node.children)
        receiver = t.touch_target(*cell.center)
        if receiver is None or not t.is_within(receiver, cell):
            return None
    boxes = [n.bounds for n in cells]
    sizes = {(b[2] - b[0], b[3] - b[1]) for b in boxes}
    xs, ys = sorted({b[0] for b in boxes}), sorted({b[1] for b in boxes})
    if len(sizes) != 1 or len(xs) != 3 or len(ys) != 4:
        return None
    width, height = next(iter(sizes))
    if xs[1] - xs[0] != xs[2] - xs[1] or xs[1] - xs[0] < width \
            or len({ys[i + 1] - ys[i] for i in range(3)}) != 1 or ys[1] - ys[0] < height:
        return None
    positions = {(n.bounds[0], n.bounds[1]): n for n in cells}
    if len(positions) != 12 or set(positions) != {(x, y) for x in xs for y in ys}:
        return None
    ordered = [positions[(x, y)].center for y in ys[:3] for x in xs]
    return {**dict(zip("123456789", ordered)), "0": positions[(xs[1], ys[3])].center}


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
    if fold_status == "unknown":
        return "bar", "无 const.window.foldscreen.type 且折叠状态 UNKNOWN（直板机真机观测）"
    return None, "无法判断"


class HdcDevice:
    kind = "hdc"

    def __init__(self, serial: Optional[str] = None, hdc: str = "hdc", timeout_s: float = 20.0,
                 pin_pad_layout: Optional[str] = None):
        self.hdc, self.timeout_s = hdc, timeout_s
        # 自动发现首设备不能继承显式设备的几何配置。
        explicit_serial = bool(serial and serial.strip() and serial == serial.strip()
                               and not any(ch in serial for ch in "*?[]"))
        self.pin_pad_layout = pin_pad_layout if explicit_serial and pin_pad_layout == BLANK_PIN_LAYOUT else None
        self.pin_pad_source = None
        self.target_discovery_error = None
        self.serial = serial or self._first_target()
        self.poll_interval = 1.0
        self.spec = f"hdc:{self.serial}"
        self.last_query_error = None

    # ---- 基础执行 ----
    def _run(self, args: list, op: str, timeout: Optional[float] = None, with_target: bool = True) -> DeviceResult:
        cmd = [self.hdc] + (["-t", self.serial] if with_target and self.serial else []) + args
        t0 = time.monotonic()
        try:
            # PIN values belong to the host unlock code, never to HDC subprocesses.
            r = subprocess.run(cmd, capture_output=True, timeout=timeout or self.timeout_s,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                               env={k: v for k, v in os.environ.items()
                                    if not (k.upper() == "VPD_SECRET_LOCK_PIN" or
                                            k.upper().startswith("VPD_SECRET_LOCK_PIN_"))})
        except FileNotFoundError:
            return DeviceResult(op, False, False, "tool_missing", f"找不到 {self.hdc}，请确认已加入 PATH")
        except subprocess.TimeoutExpired as e:
            raw = "".join(v.decode("utf-8", "replace") if isinstance(v, bytes) else (v or "")
                          for v in (e.stdout, e.stderr))
            return DeviceResult(op, True, False, "timeout", f"{op} 超过 {e.timeout}s 未返回",
                                raw=raw, duration_ms=int((time.monotonic() - t0) * 1000))
        except OSError as e:
            return DeviceResult(op, None, False, "tool_execution_error", str(e),
                                duration_ms=int((time.monotonic() - t0) * 1000))
        out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
        ms = int((time.monotonic() - t0) * 1000)
        err = classify_output(out) or ("command_failed" if r.returncode != 0 else None)
        return DeviceResult(op, True, err is None, err, out.strip()[:300] if err else None, out, ms)

    def _shell(self, *args, op: str, timeout: Optional[float] = None) -> DeviceResult:
        return self._run(["shell", *[str(a) for a in args]], op, timeout)

    def _first_target(self) -> Optional[str]:
        r = self._run(["list", "targets"], "list_targets", with_target=False)
        self.target_discovery_error = (None if r.ok else {
            "stage": "device_discovery", "op": r.op, "sent": r.sent, "executor_ok": r.ok,
            "error_class": r.error_class or "device_discovery_error", "error": r.error,
            "raw": r.raw, "duration_ms": r.duration_ms})
        lines = [ln.strip() for ln in r.raw.splitlines() if ln.strip() and "Empty" not in ln]
        return lines[0] if r.ok and lines else None

    # ---- 状态采集 ----
    def _param(self, prop: str) -> Optional[str]:
        r = self._shell("param", "get", prop, op="param_get")
        errors = getattr(self, "param_query_errors", {})
        errors.pop(prop, None)
        # 参数不存在是该查询的有效结果，不能与命令执行失败合并为同一个 None。
        absent = bool(re.fullmatch(r"Get parameter\s+.+?\s+fail!", r.raw.strip(), re.IGNORECASE))
        if not r.ok and not (r.error_class == "command_failed" and absent):
            errors[prop] = {"error_class": r.error_class, "error": r.error, "raw": r.raw,
                            "query": prop}
        self.param_query_errors = errors
        v = r.raw.strip() if r.ok else ""
        return None if (not v or "fail" in v.lower()) else v  # 不存在的参数输出 "Get parameter ... fail!"

    def info(self) -> dict:
        out = {"serial": self.serial, "kind": self.kind}
        errors = {}
        for key, prop in (("model", "const.product.model"), ("name", "const.product.name"),
                          ("os", "const.ohos.fullname"), ("software", "const.product.software.version")):
            out[key] = self._param(prop)
            error = getattr(self, "param_query_errors", {}).get(prop)
            if error:
                errors[prop] = error
        if errors:
            out["query_errors"] = errors  # 身份补充元数据失败留证，不改变独立的产品/动作判定。
        return out

    def display_info(self) -> dict:
        r = self._shell("hidumper", "-s", "DisplayManagerService", "-a", "-a", op="display_dump")
        if not r.ok:
            return {"source": "hidumper DisplayManagerService", "error_class": r.error_class, "error": r.error,
                    "raw": r.raw, "density": None,
                    "execution_error": {"error_class": r.error_class, "error": r.error, "raw": r.raw,
                                        "query": "display_dump"}}
        d = parse_display(r.raw)
        fold_param = self._param("const.window.foldscreen.type")
        error = getattr(self, "param_query_errors", {}).get("const.window.foldscreen.type")
        d["device_form"], d["device_form_basis"] = infer_device_form(fold_param, d.get("fold_status"))
        if error:
            d["execution_error"] = error
            if d.get("fold_status") not in ("expanded", "folded", "half_folded"):
                d["device_form"], d["device_form_basis"] = None, "折叠属性查询失败，无独立证据确认设备形态"
        d.update(foldscreen_type=fold_param, source="hidumper DisplayManagerService")
        return d

    def app_version(self, bundle: str) -> Optional[str]:
        r = self._shell("bm", "dump", "-n", bundle, op="bm_dump")
        # bm 的通用空结果错误同时覆盖缺包、权限及服务失败，不能凭这条文案证明 APP 不存在。
        # 保留查询错误；独立缺包证据与官方返回来源见 tests/test_result_app_query_ambiguity.py。
        self.version_query_error = None if r.ok else {"error_class": r.error_class, "error": r.error, "raw": r.raw}
        return parse_version(r.raw) if r.ok else None

    def preflight(self, bundle: str) -> dict:
        checks = []
        if not self.serial:
            error = self.target_discovery_error
            checks.append({"check": "device_online", "ok": False,
                           "error_class": error["error_class"] if error else "device_offline",
                           "detail": (error.get("error") or "hdc list targets 执行失败，设备列表未知") if error else "hdc list targets 没有设备",
                           **({"execution_error": error} if error else {})})
            return {"ok": False, "checks": checks, "device": {"serial": None}}
        power = self._shell("hidumper", "-s", "3301", "-a", "-a", op="power_state")
        checks.append({"check": "device_online", "ok": power.error_class != "device_offline",
                       "error_class": power.error_class if power.error_class == "device_offline" else None,
                       "detail": (power.error or "")[:120]})
        state = parse_kv(power.raw, "Current State")
        checks.append({"check": "screen_awake", "ok": power.ok and state == "AWAKE", "value": state,
                       "error_class": None if state == "AWAKE" else "screen_off",
                       **({"execution_error": {"error_class": power.error_class, "error": power.error, "raw": power.raw}}
                          if not power.ok else {})})
        lock = self._shell("hidumper", "-s", "ScreenlockService", "-a", "-all", op="lock_state")
        locked = parse_kv(lock.raw, "screenLocked")
        checks.append({"check": "screen_unlocked", "ok": lock.ok and locked == "false", "value": locked,
                       "error_class": None if locked == "false" else "screen_locked",
                       **({"execution_error": {"error_class": lock.error_class, "error": lock.error, "raw": lock.raw}}
                          if not lock.ok else {})})
        ver = self.app_version(bundle)
        checks.append({"check": "app_installed", "ok": ver is not None, "value": ver,
                       "error_class": None if ver else "app_missing",
                       **({"execution_error": self.version_query_error} if self.version_query_error else {})})
        info, display = self.info(), self.display_info()
        display_error = display.get("execution_error")
        checks.append({"check": "display_info", "ok": not display_error,
                       "error_class": display_error.get("error_class") if display_error else None,
                       **({"execution_error": display_error} if display_error else {})})
        return {"ok": all(c["ok"] for c in checks), "checks": checks, "device": info, "app_version": ver,
                "display": display}

    def foreground(self) -> Optional[str]:
        """查询失败时返回 None 并在 last_query_error 记录原因，避免把"查不到"当成"不在 APP 内"。"""
        r = self._shell("aa", "dump", "-l", op="aa_dump")
        self.last_query_error = None if r.ok else (r.error_class or "command_failed")
        self.last_query_error_detail = None if r.ok else {"error": r.error, "raw": r.raw}
        if not r.ok:
            return None
        foreground = parse_foreground(r.raw)
        if foreground is not None:
            return foreground
        windows = self.shown_windows()
        error = getattr(self, "window_query_error", None)
        if windows is None and error:
            self.last_query_error = error["error_class"]
            self.last_query_error_detail = error
        return foreground_from_evidence(r.raw, windows)

    def process_alive(self, bundle: str) -> Optional[bool]:
        r = self._shell("pidof", bundle, op="pidof")
        # pidof 未命中可以非零退出且无输出；显式错误输出不能冒充 PID 或进程已退出。
        queried = r.sent and (r.error_class is None or (r.error_class == "command_failed" and not r.raw.strip()))
        self.process_query_error = (None if queried else
                                    {"error_class": r.error_class, "error": r.error, "raw": r.raw})
        return bool(r.raw.strip()) if queried else None

    def _pull(self, remote: str, local: Path, op: str) -> DeviceResult:
        r = self._run(["file", "recv", remote, str(local)], op)
        self._shell("rm", "-f", remote, op="cleanup")
        if r.ok and not local.exists():
            r.ok, r.error_class, r.error = False, "transfer_failed", "file recv 未生成本地文件"
        return r

    def dump_tree(self, bundle: str, local_path) -> DeviceResult:
        """拉取布局树并解析。原树先落到系统临时目录（运行目录之外），解析成功也只把结果放在
        DeviceResult.data 里返回，不写 local_path：最终路径由调用方按原文落盘。
        解析失败时返回真实错误；临时目录在 finally 里清理。"""
        remote = f"/data/local/tmp/vpd_{uuid.uuid4().hex[:8]}.json"
        local = Path(local_path)
        r = self._shell("uitest", "dumpLayout", "-b", bundle, "-p", remote, op="dump_tree", timeout=30)
        if not r.ok:
            return r
        tmp_dir = Path(tempfile.mkdtemp(prefix="vpd_tree_"))
        try:
            p = self._pull(remote, tmp_dir / "tree.json", "dump_tree_pull")
            if not p.ok:
                return p
            try:
                r.data["tree"] = json.loads((tmp_dir / "tree.json").read_text(encoding="utf-8"))
            except (ValueError, OSError) as e:
                r.ok, r.error_class, r.error = False, "tree_parse_error", str(e)
            return r
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def dump_screen(self, local_path) -> DeviceResult:
        """非 bundle 作用域的整屏布局树（同锁屏键盘的 lock_dump 采集方式）。
        只用于系统弹层诊断：前台报为被测 APP 但按 bundle 取的树只有空根时，
        用它确认盖在屏幕上的到底是什么窗口。树文件落在观测目录里作证据，
        与 bundle 树一样保留采集原文，由调用方写盘。"""
        remote = f"/data/local/tmp/vpd_{uuid.uuid4().hex[:8]}.json"
        local = Path(local_path)
        r = self._shell("uitest", "dumpLayout", "-p", remote, op="screen_dump", timeout=30)
        if not r.ok:
            return r
        p = self._pull(remote, local, "screen_dump_pull")
        if not p.ok:
            return p
        try:
            p.data["tree"] = json.loads(local.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            p.ok, p.error_class, p.error = False, "tree_parse_error", str(e)
        return p

    def screenshot(self, local_path) -> DeviceResult:
        remote = f"/data/local/tmp/vpd_{uuid.uuid4().hex[:8]}.png"
        r = self._shell("uitest", "screenCap", "-p", remote, op="screenshot", timeout=30)
        if r.ok and "saved" not in r.raw.lower():
            # 2026-09-29 真机：投屏帧流（uitest Captures）运行期间 screenCap 输出
            # "ScreenCap failed: Failed to get display pixelMap"，不含上面的成功标记
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
        """snapshot_display 整屏截图（jpeg 约 70KB，png 约 1.7MB，1320×2848 真机实测）。
        控制台投屏的 snapshot_display 回退帧源用；执行器判定仍走 screenshot（uitest screenCap）。"""
        remote = f"/data/local/tmp/vpd_{uuid.uuid4().hex[:8]}.{fmt}"
        r = self._shell("snapshot_display", "-f", remote, "-t", fmt, op="snapshot", timeout=30)
        if r.ok and "success" not in r.raw.lower():
            r.ok, r.error_class, r.error = False, "command_failed", r.raw.strip()[:300]
        return self._pull(remote, Path(local_path), "snapshot_pull") if r.ok else r

    # ---- 动作 ----
    def tap(self, x: int, y: int) -> DeviceResult:
        return self._shell("uitest", "uiInput", "click", int(x), int(y), op="tap")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, speed: int = 2000) -> DeviceResult:
        if type(speed) is not int or not 200 <= speed <= 40000:
            return DeviceResult("swipe", False, False, "invalid_step", "swipe速度须为200..40000整数px/s")
        if (int(x1), int(y1)) == (int(x2), int(y2)):
            return DeviceResult("swipe", False, False, "invalid_step", "swipe起止点相同，未下发")
        return self._shell("uitest", "uiInput", "swipe", int(x1), int(y1), int(x2), int(y2), int(speed), op="swipe")

    def input_text(self, x: int, y: int, text: str) -> DeviceResult:
        if re.search(r"\s", text):
            return DeviceResult("input_text", False, False, "unsupported", "v0.1 的 uiInput inputText 不支持含空白的文本")
        if not re.fullmatch(r"[A-Za-z0-9_.@+\-一-鿿]*", text):
            # hdc shell 的参数会经过设备端 shell：含 $ ; & | ' " 等字符的文本（例如密码）用单引号包起来，
            # 避免被当成 shell 语法。设备端 shell 的转义行为未在真机上逐字符验证
            text = "'" + text.replace("'", "'\\''") + "'"
        return self._shell("uitest", "uiInput", "inputText", int(x), int(y), text, op="input_text")

    def long_press(self, x: int, y: int) -> DeviceResult:
        """控制台人工长按（uiInput longClick）；Agent 的动作不走这里。"""
        return self._shell("uitest", "uiInput", "longClick", int(x), int(y), op="long_press")

    def type_focused(self, text: str) -> DeviceResult:
        """向当前已聚焦的输入框输入（uiInput text），不先点击坐标。控制台人工输入用。
        转义规则与 input_text 一致：含空白不支持；含 shell 元字符时单引号包裹。"""
        if re.search(r"\s", text):
            return DeviceResult("type_focused", False, False, "unsupported", "v0.1 的 uiInput 文本输入不支持含空白的文本")
        if not re.fullmatch(r"[A-Za-z0-9_.@+\-一-鿿]*", text):
            text = "'" + text.replace("'", "'\\''") + "'"
        return self._shell("uitest", "uiInput", "text", text, op="type_focused")

    def key(self, name: str) -> DeviceResult:
        if name not in KEYS:
            return DeviceResult("key", False, False, "unsupported", f"不支持的按键 {name}")
        return self._shell("uitest", "uiInput", "keyEvent", KEYS[name], op=f"key_{name}")

    def screen_locked(self, tries: int = 3) -> Optional[bool]:
        """三态：锁屏/熄屏 → True；亮屏且已解锁 → False；查询失败或输出认不出 → None。
        锁屏时 aa dump 里没有 FOREGROUND mission，不能据此当成桌面。
        查询是只读的：hidumper 偶发空输出（2026-10-01 真机运行中出现，单独连查 20 次复现不了），
        间隔 0.5s 重查，连续 tries 次都认不出才返回 None。"""
        error = None
        self.screen_query_error = None
        for i in range(max(1, tries)):
            v = self._screen_locked_once()
            if v is not None:
                self.screen_query_error = None
                return v
            error = self.screen_query_error or error
            if i + 1 < tries:
                time.sleep(0.5)
        self.screen_query_error = error
        return None

    def _screen_locked_once(self) -> Optional[bool]:
        power = self._shell("hidumper", "-s", "3301", "-a", "-a", op="power_state")
        self.screen_query_error = None if power.ok else {"error_class": power.error_class, "error": power.error, "raw": power.raw}
        state = parse_kv(power.raw, "Current State") if power.ok else None
        if state is None:
            return None
        if state != "AWAKE":
            return True
        lock = self._shell("hidumper", "-s", "ScreenlockService", "-a", "-all", op="lock_state")
        self.screen_query_error = None if lock.ok else {"error_class": lock.error_class, "error": lock.error, "raw": lock.raw}
        return {"true": True, "false": False}.get(parse_kv(lock.raw, "screenLocked") if lock.ok else None)

    def wake(self) -> DeviceResult:
        return self._shell("power-shell", "wakeup", op="wakeup")

    def screen_off_override(self) -> Optional[int]:
        """当前的熄屏时间覆盖值（ms）。锁屏状态下系统自己会设 10000ms（2026-09-29 真机观测），没有覆盖时为 None。
        控制台 --keep-awake 用：进入时记录原值，退出时恢复。"""
        r = self._shell("hidumper", "-s", "3301", "-a", "-a", op="power_state")
        m = re.search(r"OverrideTimeout=(\d+)ms", r.raw) if r.ok else None
        return int(m.group(1)) if m else None

    def set_screen_off(self, ms: Optional[int]) -> DeviceResult:
        """覆盖熄屏时间；ms 为 None 时撤销覆盖（power-shell timeout -o / -r）。不解锁。"""
        if ms is None:
            return self._shell("power-shell", "timeout", "-r", op="screen_off_restore")
        return self._shell("power-shell", "timeout", "-o", int(ms), op="screen_off_override")

    def lock_pin_pad(self):
        remote = f"/data/local/tmp/vpd_{uuid.uuid4().hex[:8]}.json"
        self.pin_pad_query_error = None
        self.pin_pad_source = None
        r = self._shell("uitest", "dumpLayout", "-p", remote, op="lock_dump", timeout=30)
        if r.ok:
            r = self._shell("cat", remote, op="lock_dump_read")
        self._shell("rm", "-f", remote, op="cleanup")
        if not r.ok:
            self.pin_pad_query_error = {"op": r.op, "error_class": r.error_class or "command_failed",
                                        "error": r.error, "raw": r.raw}
            return None
        try:
            tree = json.loads(r.raw)
        except ValueError as error:
            self.pin_pad_query_error = {"op": r.op, "error_class": "tree_parse_error",
                                        "error": str(error), "raw": r.raw}
            return None
        pad = find_pin_pad(tree)
        if pad is not None:
            self.pin_pad_source = "labels"
        elif self.pin_pad_layout == BLANK_PIN_LAYOUT:
            pad = find_blank_pin_pad(tree)
            if pad is not None:
                self.pin_pad_source = "configured_geometry"
        return pad

    def wake_unlock(self, pin, dispatcher=None):
        from ..unlock import wake_unlock
        return wake_unlock(self, pin, dispatcher, sleep=time.sleep)

    def shown_windows(self) -> Optional[list]:
        """当前显示的窗口名；查询失败或格式认不出 → None。只取窗口名与层级，不取窗口内容。"""
        r = self._shell("hidumper", "-s", "WindowManagerService", "-a", "-a", op="window_list")
        self.window_query_error = (None if r.ok else {"op": r.op, "error_class": r.error_class or "command_failed",
                                                     "error": r.error, "raw": r.raw})
        return parse_windows(r.raw) if r.ok else None

    def clear_text(self, x, y, count, dispatcher, preconditions, att=None):
        from ..dispatch import clear_text
        return clear_text(self, x, y, count, dispatcher, preconditions, att)

    def launch(self, bundle: str, ability: str) -> DeviceResult:
        r = self._shell("aa", "start", "-a", ability, "-b", bundle, op="launch")
        if r.ok and "success" not in r.raw.lower():
            r.ok, r.error_class, r.error = False, "command_failed", r.raw.strip()[:300]
        return r

    def stop(self, bundle: str) -> DeviceResult:
        return self._shell("aa", "force-stop", bundle, op="stop")

    def env_facts(self) -> dict:
        return {}
