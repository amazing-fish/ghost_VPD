"""控制台上的手动操作：归一化坐标 → 设备像素；优先走投屏的 uitest 会话（hypium Driver），会话不可用时回退 hdc uiInput。

这些操作等同于人直接在手机上操作，不经过 policy 与授权检查；每次操作写入控制台会话的 events.jsonl
（只记坐标、按键与文字长度，不记文字内容）。Agent 的设备动作只能走 `vpdtest act/run/explore`，
不得调用控制台的输入接口。
"""
from __future__ import annotations

import math
import time
from typing import Optional

from .frames import SessionUnavailable

KEYS_ALLOWED = ("back", "home")   # 电源键会锁屏，不提供；wake 只点亮屏幕
MAX_TEXT = 200


class Control:
    def __init__(self, device, store, fast=None, size: Optional[tuple] = None):
        self.device, self.store, self.fast = device, store, fast
        self.size = tuple(size) if size else None  # 设备像素 (w, h)

    def _px(self, fx, fy) -> tuple:
        if not self.size:
            raise ValueError("未知屏幕尺寸，无法换算坐标")
        fx, fy = float(fx), float(fy)
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
            raise ValueError("坐标须为 [0,1] 内的归一化值")
        w, h = self.size
        return min(int(fx * w), w - 1), min(int(fy * h), h - 1)

    def _via_session(self, api: str, args: list) -> Optional[str]:
        """返回 None 表示应改走 hdc；已发出但失败时抛 RuntimeError，不自动重发（避免重复点击）。"""
        if not self.fast:
            return None
        try:
            self.fast.invoke(api, args)
            return "uitest_session"
        except SessionUnavailable:
            return None
        except Exception as e:
            raise RuntimeError(f"uitest 会话执行 {api} 失败（未重发）：{str(e)[:200]}")

    def handle(self, cmd: dict) -> dict:
        op = cmd.get("op")
        t0 = time.monotonic()
        rec, r, via = {"op": op}, None, None
        try:
            if op in ("tap", "long_press"):
                x, y = self._px(cmd.get("x"), cmd.get("y"))
                rec.update(x=x, y=y)
                via = self._via_session("Driver.click" if op == "tap" else "Driver.longClick", [x, y])
                if not via:
                    r = self.device.tap(x, y) if op == "tap" else self.device.long_press(x, y)
            elif op == "swipe":
                x1, y1 = self._px(cmd.get("x1"), cmd.get("y1"))
                x2, y2 = self._px(cmd.get("x2"), cmd.get("y2"))
                dur = max(0.05, min(float(cmd.get("duration_s", 0.3)), 5.0))
                speed = max(200, min(int(math.hypot(x2 - x1, y2 - y1) / dur), 40000))  # uiInput 速度范围
                rec.update(x1=x1, y1=y1, x2=x2, y2=y2, speed=speed)
                via = self._via_session("Driver.swipe", [x1, y1, x2, y2, speed])
                if not via:
                    r = self.device.swipe(x1, y1, x2, y2, speed)
            elif op == "key":
                name = cmd.get("key")
                if name not in KEYS_ALLOWED:
                    raise ValueError(f"不支持的按键 {name}（可用：{'/'.join(KEYS_ALLOWED)}）")
                rec["key"] = name
                r = self.device.key(name)
            elif op == "wake":
                r = self.device.wakeup()
            elif op == "text":
                text = str(cmd.get("text") or "")
                if not text or len(text) > MAX_TEXT:
                    raise ValueError(f"文字长度须在 1~{MAX_TEXT}")
                rec["text_len"] = len(text)
                r = self.device.type_focused(text)
            else:
                raise ValueError(f"不支持的操作 {op}")
        except (ValueError, TypeError) as e:
            rec.update(ok=False, error_class="bad_request", error=str(e))
        except RuntimeError as e:
            rec.update(via="uitest_session", ok=False, error_class="session_error", error=str(e))
        else:
            if via:
                rec.update(via=via, ok=True)
            else:
                rec.update(via="hdc", ok=r.ok, error_class=r.error_class, error=r.error)
        rec["ms"] = int((time.monotonic() - t0) * 1000)
        self.store.event("console_input", **rec)
        return rec
