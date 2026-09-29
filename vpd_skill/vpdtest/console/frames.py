"""投屏帧源：uitest Captures 帧流（主）与 snapshot_display 轮询（回退），帧只保存在内存。

2026-09-29 Mate 80 Pro Max（1320×2848）真机实测：
  - uitest Captures startCaptureScreen：约 11.8 fps，帧为 660×1424 JPEG（约 75~93KB）
  - snapshot_display -f x.jpeg + file recv：约 1.6 fps，帧为 1320×2848
  - 帧流运行期间 uitest dumpLayout 与点击正常；uitest screenCap 失败（执行器截图已回退 snapshot_display）
帧流通过 hmdriver2 1.4.3（MIT）部署到设备的 uitest agent 与 8012 端口通信，属于该库的非公开接口，
因此只在这里使用、失败时回退，不影响执行器。
"""
from __future__ import annotations

import json
import os
import socket
import struct
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

MAX_BUFFER = 8 * 1024 * 1024  # 帧流缓冲上限：超过说明数据不是预期的 JPEG 流，丢弃重来


class SessionUnavailable(Exception):
    """请求尚未发出（会话不存在/正在建立）：调用方可以安全地改走 hdc。"""


def jpeg_size(data: bytes) -> Optional[tuple]:
    """从 SOF 段读取 JPEG 宽高；不是合法 JPEG 时返回 None。"""
    i, n = 2, len(data)
    if data[:2] != b"\xff\xd8":
        return None
    while i + 9 <= n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def _frame_end(buf: bytes, start: int) -> int:
    """从 start（FFD8 处）按段长度走到 SOS，再在熵编码数据里找 EOI。返回 EOI 之后的位置，数据不全返回 -1。

    不能直接找第一个 FFD9：APP 段（EXIF 缩略图等）里可能出现嵌套的 FFD8/FFD9。
    熵编码数据中 0xFF 后跟 0x00（填充）或 RST，不会出现 FFD9，所以 SOS 之后第一个 FFD9 就是 EOI。"""
    i, n = start + 2, len(buf)
    while True:
        if i + 4 > n:
            return -1
        if buf[i] != 0xFF:
            return -2  # 段结构不合法
        marker = buf[i + 1]
        if marker == 0xFF:  # 填充字节
            i += 1
            continue
        seg = struct.unpack(">H", buf[i + 2:i + 4])[0]
        if marker == 0xDA:  # SOS
            e = buf.find(b"\xff\xd9", i + 2 + seg)
            return -1 if e == -1 else e + 2
        i += 2 + seg


def split_frames(buf: bytearray) -> tuple:
    """把帧流缓冲切成完整 JPEG 列表与剩余字节。FFD8 之前的字节（例如开流的 JSON 应答）丢弃。"""
    frames = []
    while True:
        s = buf.find(b"\xff\xd8")
        if s == -1:
            return frames, bytearray(buf[-1:] if buf[-1:] == b"\xff" else b"")
        end = _frame_end(buf, s)
        if end == -1:
            return frames, bytearray(buf[s:])
        if end == -2:  # 坏段：跳过这个起始标记继续找
            buf = buf[s + 2:]
            continue
        frames.append(bytes(buf[s:end]))
        buf = buf[end:]


class FrameHub:
    """最新一帧 + 统计；多个 MJPEG 客户端等待同一个条件变量。"""

    def __init__(self):
        self._cond = threading.Condition()
        self.frame: Optional[bytes] = None
        self.seq = 0
        self.size: Optional[tuple] = None
        self.source = None
        self.status = "starting"
        self.error: Optional[str] = None
        self._stamps: list = []
        self.last_ts: Optional[float] = None

    def push(self, data: bytes) -> None:
        with self._cond:
            self.frame, self.seq, self.last_ts = data, self.seq + 1, time.time()
            self.size = jpeg_size(data) or self.size
            self._stamps = [t for t in self._stamps if self.last_ts - t < 2.0] + [self.last_ts]
            self.status, self.error = "live", None
            self._cond.notify_all()

    def set_status(self, status: str, error: Optional[str] = None) -> None:
        with self._cond:
            self.status, self.error = status, error
            self._cond.notify_all()

    def latest(self) -> tuple:
        with self._cond:
            return self.seq, self.frame

    def wait(self, after_seq: int, timeout: float) -> tuple:
        with self._cond:
            self._cond.wait_for(lambda: self.seq != after_seq, timeout)
            return self.seq, self.frame

    def stats(self) -> dict:
        now = time.time()
        recent = [t for t in self._stamps if now - t < 2.0]
        return {"source": self.source, "status": self.status, "error": self.error, "seq": self.seq,
                "fps": round(len(recent) / 2.0, 1), "frame_size": list(self.size) if self.size else None,
                "frame_age_s": round(now - self.last_ts, 1) if self.last_ts else None}


class SnapshotSource:
    """snapshot_display 轮询。临时文件写在控制台会话目录，读入内存后立即删除。"""

    name = "snapshot_display"

    def __init__(self, device, hub: FrameHub, tmp_dir: Path, interval: float = 0.0):
        self.device, self.hub, self.tmp_dir, self.interval = device, hub, Path(tmp_dir), interval
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None

    def start(self) -> None:
        self.hub.source = self.name
        self._th = threading.Thread(target=self._loop, name="snapshot-source", daemon=True)
        self._th.start()

    def _loop(self) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        while not self._stop.is_set():
            fd, tmp = tempfile.mkstemp(dir=str(self.tmp_dir), suffix=".jpeg")
            os.close(fd)
            os.unlink(tmp)
            r = self.device.snapshot(tmp, fmt="jpeg")
            try:
                if r.ok:
                    self.hub.push(Path(tmp).read_bytes())
                else:
                    self.hub.set_status("error", f"{r.error_class}: {(r.error or '')[:160]}")
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            self._stop.wait(self.interval if r.ok else 2.0)

    def stop(self) -> None:
        self._stop.set()
        if self._th:
            self._th.join(timeout=35)


class UitestStreamSource:
    """uitest Captures 帧流。连续失败 max_failures 次后调用 on_give_up（通常切换到 snapshot 回退）。"""

    name = "uitest_stream"

    def __init__(self, serial: str, hub: FrameHub, on_give_up: Optional[Callable[[str], None]] = None,
                 stall_s: float = 5.0, max_failures: int = 3):
        self.serial, self.hub, self.on_give_up = serial, hub, on_give_up
        self.stall_s, self.max_failures = stall_s, max_failures
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None
        self._client = None
        self._sock: Optional[socket.socket] = None
        self._ctl_lock = threading.Lock()
        self.restarts = 0
        self.retry_s = 2.0

    def start(self) -> None:
        self.hub.source = self.name
        self._th = threading.Thread(target=self._loop, name="uitest-stream", daemon=True)
        self._th.start()

    @staticmethod
    def _msg(api: str) -> bytes:
        return json.dumps({"module": "com.ohos.devicetest.hypiumApiHelper", "method": "Captures",
                           "params": {"api": api, "args": []},
                           "request_id": datetime.now().strftime("%Y%m%d%H%M%S%f")}).encode() + b"\n"

    def invoke(self, api: str, args: list):
        """在同一个 uitest 会话里调用 hypium Driver 接口（点击/滑动不必每次起 hdc 进程）。
        会话不可用时抛异常，由调用方回退到 hdc uiInput。"""
        if not self._ctl_lock.acquire(timeout=1.0):  # 正在（重新）建立会话：不等，直接回退
            raise SessionUnavailable("uitest 会话正在建立")
        try:
            client = self._client
            if client is None or client.sock is None:
                raise SessionUnavailable("uitest 会话不可用")
            try:
                return client.invoke(api, args=args)
            except Exception as e:  # 请求可能已经发出：不让调用方重发
                raise RuntimeError(f"{type(e).__name__}: {str(e)[:160]}") from e
        finally:
            self._ctl_lock.release()

    def _open(self) -> None:
        import logging
        from hmdriver2._client import HmClient  # 可选依赖：缺失时由调用方回退
        # hmdriver2 默认把每条请求（含输入的文字）以 DEBUG 打到 stderr
        logging.getLogger("hmdriver2").setLevel(logging.WARNING)
        with self._ctl_lock:
            self._client = HmClient(self.serial)
            self._client.start()  # 部署 agent、（重新）启动 uitest 守护进程、建立端口转发与控制会话
        self._sock = socket.create_connection(("127.0.0.1", self._client.local_port), timeout=10)
        self._sock.sendall(self._msg("startCaptureScreen"))

    def _close(self) -> None:
        if self._sock:
            try:
                self._sock.sendall(self._msg("stopCaptureScreen"))
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        with self._ctl_lock:
            if self._client:
                try:
                    self._client.release()
                except Exception:  # 释放端口转发失败不影响退出
                    pass
                self._client = None

    def _pump(self) -> None:
        buf = bytearray()
        self._sock.settimeout(self.stall_s)
        while not self._stop.is_set():
            try:
                chunk = self._sock.recv(1 << 20)
            except socket.timeout:
                # 熄屏/锁屏时帧流可能停止输出：保持连接，状态标为 stalled，不当成错误
                self.hub.set_status("stalled", f"{self.stall_s:.0f}s 没有新帧（熄屏或画面静止）")
                continue
            if not chunk:
                raise ConnectionError("帧流连接被设备端关闭")
            buf += chunk
            frames, buf = split_frames(buf)
            for f in frames:
                self.hub.push(f)
            if len(buf) > MAX_BUFFER:
                buf = bytearray()

    def _loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            seq0 = self.hub.seq
            try:
                self._open()
                self._pump()
            except Exception as e:  # 设备断开、agent 启动失败、hmdriver2 缺失等
                # 只有这一轮确实收到过帧才算恢复健康；连上后立刻断开不清零，否则永远到不了回退
                failures = (0 if self.hub.seq > seq0 else failures) + 1
                self.hub.set_status("error", f"{type(e).__name__}: {str(e)[:160]}")
            finally:
                self._close()
            if self._stop.is_set():
                break
            if failures >= self.max_failures:
                if self.on_give_up:
                    self.on_give_up(self.hub.error or "帧流连续失败")
                return
            self.restarts += 1
            self._stop.wait(self.retry_s)

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._th:
            self._th.join(timeout=10)
