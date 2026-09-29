"""控制台 HTTP 服务（标准库 ThreadingHTTPServer）。

安全边界：只允许绑定回环地址；每次启动生成随机 token（只出现在启动时打印的 URL 里，不写入日志与事件）；
校验 Host 头（防 DNS rebinding）；POST 还要求 X-VPD-Token 请求头、JSON 类型与同源 Origin（防跨站请求）。
没有用户体系：拿到 URL 的本机用户都能看屏幕、操作手机，URL 不要外传。
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from . import progress
from .control import Control
from .frames import FrameHub, SnapshotSource, UitestStreamSource

PAGE = Path(__file__).with_name("page.html")
MAX_BODY = 4096
BOUNDARY = "vpdframe"


ALLOWED_HOSTS = ("127.0.0.1", "localhost")


class _Server(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 允许两个进程绑同一端口（请求会随机落到任一进程）：关掉，端口被占用时直接报错
    allow_reuse_address = False


def is_loopback(host: str) -> bool:
    """只接受 127.0.0.1 与 localhost：与 Host 头校验的白名单一致（127.0.0.2 等虽是回环，但请求会被 421 拒绝）。"""
    return host in ALLOWED_HOSTS


class Console:
    """设备侧状态：帧源、手动操作、熄屏覆盖。device 为 None 时只提供进度面板。"""

    def __init__(self, device, store, runs_dir: Path, source: str = "auto", allow_input: bool = True,
                 max_fps: float = 12.0, keep_awake_min: Optional[float] = None):
        self.device, self.store, self.runs_dir = device, store, Path(runs_dir)
        self.hub = FrameHub()
        self.max_fps = max_fps
        self.allow_input = allow_input and device is not None
        self.display = None
        self.src = None
        self.fast = None
        self.fallback_reason = None
        self._lock = threading.Lock()
        self._closed = False
        self.keep_awake = {"requested_min": keep_awake_min, "previous_override_ms": None, "applied": False}
        if device is not None:
            self.display = device.display_info()
            size = (self.display.get("width_px"), self.display.get("height_px"))
            self.control = Control(device, store, size=size if all(size) else None)
            self._start_source(source)
            if keep_awake_min:
                self._keep_awake(keep_awake_min)
        else:
            self.control = None
            self.hub.set_status("no_device")

    def _start_source(self, source: str) -> None:
        if source in ("auto", "stream") and getattr(self.device, "kind", None) == "hdc":
            try:
                import hmdriver2  # noqa: F401  可选依赖
            except ImportError:
                self.fallback_reason = "未安装 hmdriver2==1.4.3，使用 snapshot_display 轮询"
            else:
                self.src = UitestStreamSource(self.device.serial, self.hub, on_give_up=self._fallback)
                self.fast = self.control.fast = self.src
                self.src.start()
                self.store.event("console_source", source=self.src.name)
                return
        self.src = SnapshotSource(self.device, self.hub, self.store.dir / "tmp")
        self.src.start()
        self.store.event("console_source", source=self.src.name, reason=self.fallback_reason)

    def _fallback(self, reason: str) -> None:
        with self._lock:
            self.fallback_reason = f"帧流连续失败，改用 snapshot_display：{reason}"
            self.fast = self.control.fast = None
            self.src = SnapshotSource(self.device, self.hub, self.store.dir / "tmp")
            self.src.start()
        self.store.event("console_source", source=self.src.name, reason=self.fallback_reason)

    def _keep_awake(self, minutes: float) -> None:
        prev = self.device.screen_off_override()
        r = self.device.set_screen_off(int(minutes * 60 * 1000))
        self.keep_awake.update(previous_override_ms=prev, applied=r.ok, error=None if r.ok else r.error)
        self.store.event("console_keep_awake", minutes=minutes, previous_override_ms=prev, ok=r.ok)

    def status(self) -> dict:
        d = self.display or {}
        return {"device": getattr(self.device, "spec", None), "frames": self.hub.stats(),
                "fallback_reason": self.fallback_reason, "input": self.allow_input,
                "display": {k: d.get(k) for k in ("width_px", "height_px", "density", "device_form", "fold_status")},
                "keep_awake": self.keep_awake, "console_run": self.store.run_id}

    @staticmethod
    def _aspect_mismatch(frame_size, size) -> bool:
        """画面与换算用尺寸的宽高比差 5% 以上（旋转、折叠/展开后）。帧流是半分辨率，所以比较比例而不是像素。"""
        if not frame_size or not size or not all(frame_size) or not all(size):
            return False
        a, b = frame_size[0] / frame_size[1], size[0] / size[1]
        return abs(a - b) / b > 0.05

    def _check_geometry(self) -> Optional[str]:
        """坐标类操作前确认换算尺寸仍与当前画面一致；不一致先重读显示参数，仍不一致就拒绝，不按旧尺寸猜。"""
        frame = self.hub.size
        if not self._aspect_mismatch(frame, self.control.size):
            return None
        self.display = self.device.display_info()
        size = (self.display.get("width_px"), self.display.get("height_px"))
        if all(size):
            self.control.size = tuple(size)
        if self._aspect_mismatch(frame, self.control.size):
            return f"画面 {frame[0]}×{frame[1]} 与设备尺寸 {self.control.size} 的方向/比例不一致（旋转或折叠中），已拒绝，稍后重试"
        self.store.event("console_display_refresh", size=list(self.control.size), frame=list(frame))
        return None

    def input(self, cmd: dict) -> dict:
        with self._lock:  # 串行执行，避免两次操作交错
            if cmd.get("op") in ("tap", "long_press", "swipe"):
                why = self._check_geometry()
                if why:
                    rec = {"op": cmd.get("op"), "ok": False, "error_class": "display_mismatch", "error": why}
                    self.store.event("console_input", **rec)
                    return rec
            return self.control.handle(cmd)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.src:
            self.src.stop()
        if self.keep_awake.get("applied"):
            # 恢复启动前的值（锁屏时系统自己会设 10000ms），而不是简单撤销覆盖
            prev = self.keep_awake.get("previous_override_ms")
            r = self.device.set_screen_off(prev)
            self.store.event("console_keep_awake_restore", previous_override_ms=prev, ok=r.ok)


def make_handler(console: Console, token: str, port_ref: dict):
    page_tpl = PAGE.read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "vpdtest-console"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 默认日志会带出含 token 的查询串
            return

        # ---- 校验 ----
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip()
            return host in {f"{h}:{port_ref['port']}" for h in ALLOWED_HOSTS}

        def _token_ok(self, allow_query: bool) -> bool:
            got = self.headers.get("X-VPD-Token") or ""
            if not got and allow_query:
                got = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
            return bool(got) and hmac.compare_digest(got.encode(), token.encode())

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True  # 非浏览器客户端（curl 等）不带 Origin；token 请求头仍是必需的
            return origin in {f"http://{h}:{port_ref['port']}" for h in ALLOWED_HOSTS}

        # ---- 输出 ----
        def _headers(self, code: int, ctype: str, length: Optional[int] = None, nonce: Optional[str] = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            csp = "default-src 'none'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            if nonce:
                csp += f"; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'"
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self._headers(code, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _deny(self, code: int, msg: str) -> None:
            self._json(code, {"error": msg})

        # ---- 路由 ----
        def do_GET(self):
            if not self._host_ok():
                return self._deny(421, "Host 不被允许")
            path = urlparse(self.path).path
            if not self._token_ok(allow_query=True):
                return self._deny(401, "缺少或错误的 token（使用启动时打印的 URL）")
            if path == "/":
                nonce = secrets.token_urlsafe(12)
                body = page_tpl.replace("{{NONCE}}", nonce).encode("utf-8")
                self._headers(200, "text/html; charset=utf-8", len(body), nonce=nonce)
                return self.wfile.write(body)
            if path == "/api/status":
                return self._json(200, console.status())
            if path == "/api/runs":
                return self._json(200, {"runs": progress.list_runs(console.runs_dir)})
            if path.startswith("/api/runs/"):
                d = progress.detail(console.runs_dir, path[len("/api/runs/"):])
                return self._json(200, d) if d else self._deny(404, "没有这个运行")
            if path == "/frame.jpg":
                seq, frame = console.hub.latest()
                if not frame:
                    return self._deny(503, "还没有画面")
                self._headers(200, "image/jpeg", len(frame))
                return self.wfile.write(frame)
            if path == "/stream":
                return self._stream()
            return self._deny(404, "未知路径")

        def do_POST(self):
            if not self._host_ok():
                return self._deny(421, "Host 不被允许")
            if not self._token_ok(allow_query=False) or not self._origin_ok():
                return self._deny(403, "POST 需要 X-VPD-Token 请求头且同源")
            if urlparse(self.path).path != "/api/input":
                return self._deny(404, "未知路径")
            if not console.allow_input:
                return self._deny(403, "控制台以只看模式启动（--view-only）或未连接设备")
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._deny(415, "需要 application/json")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._deny(413, "请求体为空或过大")
            try:
                cmd = json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._deny(400, "请求体不是 JSON")
            if not isinstance(cmd, dict):
                return self._deny(400, "请求体须为对象")
            res = console.input(cmd)
            code = 200 if res.get("ok") else {"bad_request": 400, "display_mismatch": 409}.get(res.get("error_class"), 502)
            return self._json(code, res)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            seq, gap = -1, 1.0 / max(console.max_fps, 0.5)
            try:
                while not port_ref["stopping"].is_set():
                    seq, frame = console.hub.wait(seq, timeout=5.0)
                    if not frame:
                        continue
                    self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n"
                                     .encode("ascii") + frame + b"\r\n")
                    self.wfile.flush()
                    time.sleep(gap)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

    return Handler


def serve(console: Console, host: str = "127.0.0.1", port: int = 8765, duration: Optional[float] = None,
          on_ready=None, stop: Optional[threading.Event] = None) -> dict:
    """阻塞到 Ctrl+C、duration 秒或 stop 被置位。on_ready(url, port) 在开始监听后调用。"""
    if not is_loopback(host):
        raise ValueError(f"控制台只允许绑定 127.0.0.1 或 localhost，拒绝 {host}：没有用户鉴权，不能暴露到网络")
    token = secrets.token_urlsafe(24)
    stop = stop or threading.Event()
    port_ref = {"port": port, "stopping": stop}
    try:
        httpd = _Server((host, port), make_handler(console, token, port_ref))
    except Exception:
        console.close()  # 端口被占用等：帧源已启动、熄屏覆盖可能已生效，必须收尾再抛出
        raise
    httpd.daemon_threads = True
    port_ref["port"] = httpd.server_address[1]
    url = f"http://{host}:{port_ref['port']}/?token={token}"
    console.store.event("console_start", host=host, port=port_ref["port"], device=getattr(console.device, "spec", None),
                        input=console.allow_input)
    th = threading.Thread(target=httpd.serve_forever, name="console-http", daemon=True)
    th.start()
    if on_ready:
        on_ready(url, port_ref["port"])
    t0 = time.monotonic()
    try:
        while not stop.wait(0.5):  # 短间隔等待：Windows 上 Ctrl+C 才能及时打断
            if duration is not None and time.monotonic() - t0 >= duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.shutdown()
        httpd.server_close()
        console.close()
        console.store.event("console_end", uptime_s=round(time.monotonic() - t0, 1), frames=console.hub.seq)
    return {"console_run": console.store.run_id, "uptime_s": round(time.monotonic() - t0, 1),
            "frames": console.hub.seq, "source": console.hub.source, "fallback_reason": console.fallback_reason}
