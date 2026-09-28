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


def classify_output(text: str) -> Optional[str]:
    """hdc 在失败时常常仍返回 0，必须看输出。返回错误类别或 None。"""
    low = (text or "").lower()
    if any(m in low for m in _LOCKED_MARKERS):
        return "screen_locked"
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


class HdcDevice:
    kind = "hdc"

    def __init__(self, serial: Optional[str] = None, hdc: str = "hdc", timeout_s: float = 20.0):
        self.hdc, self.timeout_s = hdc, timeout_s
        self.serial = serial or self._first_target()
        self.poll_interval = 1.0
        self.spec = f"hdc:{self.serial}"

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
    def info(self) -> dict:
        out = {"serial": self.serial, "kind": self.kind}
        for key, prop in (("model", "const.product.model"), ("os", "const.ohos.fullname"),
                          ("software", "const.product.software.version")):
            r = self._shell("param", "get", prop, op="param_get")
            out[key] = r.raw.strip() if r.ok else None
        return out

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
        return {"ok": all(c["ok"] for c in checks), "checks": checks, "device": self.info(), "app_version": ver}

    def foreground(self) -> Optional[str]:
        r = self._shell("aa", "dump", "-l", op="aa_dump")
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
        return self._shell("uitest", "uiInput", "inputText", int(x), int(y), text, op="input_text")

    def key(self, name: str) -> DeviceResult:
        if name not in KEYS:
            return DeviceResult("key", False, False, "unsupported", f"不支持的按键 {name}")
        return self._shell("uitest", "uiInput", "keyEvent", KEYS[name], op=f"key_{name}")

    def launch(self, bundle: str, ability: str) -> DeviceResult:
        r = self._shell("aa", "start", "-a", ability, "-b", bundle, op="launch")
        if r.ok and "success" not in r.raw.lower():
            r.ok, r.error_class, r.error = False, "command_failed", r.raw.strip()[:300]
        return r

    def stop(self, bundle: str) -> DeviceResult:
        return self._shell("aa", "force-stop", bundle, op="stop")

    def env_facts(self) -> dict:
        return {}
