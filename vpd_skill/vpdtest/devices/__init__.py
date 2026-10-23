"""设备适配器。所有适配器实现同一组方法，每个动作返回 DeviceResult，不吞异常。

区分三层结果：
  sent        命令是否已发出（进程启动 / 回放接受）
  ok          执行器是否报告成功（exit code + 输出内容双重判断）
  业务状态    不在这里判断，由 execute.py 在动作后重新观察确认
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 系统桌面包名（平台常量）：dispatch 前置检查、replay 初始/停止态、execute 停止恢复判定共用；
# 定义在设备层，dispatch 与 replay 都从这里取，避免字面重复。
LAUNCHER = "com.ohos.sceneboard"


@dataclass
class DeviceResult:
    op: str
    sent: bool
    ok: bool
    error_class: Optional[str] = None  # screen_locked / device_offline / timeout / command_failed / ...
    error: Optional[str] = None
    raw: str = ""
    duration_ms: int = 0
    data: dict = field(default_factory=dict)


def create_device(spec: str, state_path=None, pin_pad_layout=None):
    """spec: "hdc:<serial>" 真实 Harmony 设备；"replay:<world.json>" 离线回放替身。"""
    kind, _, arg = spec.partition(":")
    if kind == "hdc":
        from .harmony_hdc import HdcDevice
        return HdcDevice(arg or None, pin_pad_layout=pin_pad_layout)
    if kind == "replay":
        from .replay import ReplayDevice
        return ReplayDevice(arg, state_path=state_path)
    raise ValueError(f"不支持的设备描述: {spec}（v0.1 支持 hdc:<serial> / replay:<world.json>）")
