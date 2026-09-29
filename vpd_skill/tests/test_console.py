"""控制台：JPEG 帧切分、进度汇总、HTTP 鉴权边界、手动操作的回退规则、截图回退。全部离线（假设备）。"""
import http.client
import json
import os
import struct
import threading
import time
from pathlib import Path

import pytest

from vpdtest.console import progress
from vpdtest.console.control import Control
from vpdtest.console.frames import (FrameHub, SessionUnavailable, SnapshotSource, UitestStreamSource, jpeg_size,
                                    split_frames)
from vpdtest.console.server import Console, is_loopback, serve
from vpdtest.devices import DeviceResult
from vpdtest.devices.harmony_hdc import HdcDevice
from vpdtest.observe import RunStore
from vpdtest.util import append_jsonl, write_json


def fake_jpeg(w=660, h=1424, app_payload=b"", body=b"\x12\x34\xff\x00\x56") -> bytes:
    """结构合法的最小 JPEG：SOI + APP1(可含嵌套 FFD8/FFD9) + SOF0 + SOS + 熵编码数据 + EOI。"""
    app1 = b"\xff\xe1" + struct.pack(">H", len(app_payload) + 2) + app_payload
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    sos = b"\xff\xda" + struct.pack(">HB", 8, 1) + b"\x01\x00\x00\x3f\x00"
    return b"\xff\xd8" + app1 + sof + sos + body + b"\xff\xd9"


# ---- 帧切分 ----
def test_split_frames_handles_prefix_nested_markers_and_partial_tail():
    f1 = fake_jpeg(app_payload=b"thumb\xff\xd8\x00\xff\xd9end")  # APP 段里的嵌套标记不能当成帧边界
    f2 = fake_jpeg(320, 712)
    buf = bytearray(b'{"result":true}\n' + f1 + f2 + f2[:10])
    frames, rest = split_frames(buf)
    assert frames == [f1, f2]
    assert bytes(rest) == f2[:10]
    frames, rest = split_frames(rest + f2[10:])
    assert frames == [f2] and not rest
    assert jpeg_size(f1) == (660, 1424) and jpeg_size(f2) == (320, 712)


def test_split_frames_real_encoder_output():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    ok, enc = cv2.imencode(".jpg", np.full((24, 40, 3), 127, np.uint8))
    data = enc.tobytes()
    frames, rest = split_frames(bytearray(data + data))
    assert frames == [data, data] and not rest
    assert jpeg_size(data) == (40, 24)


def test_hub_wait_and_stats():
    hub = FrameHub()
    assert hub.latest() == (0, None)
    threading.Timer(0.05, hub.push, args=(fake_jpeg(),)).start()
    seq, frame = hub.wait(0, timeout=2)
    assert seq == 1 and frame and hub.stats()["frame_size"] == [660, 1424] and hub.stats()["status"] == "live"


# ---- 进度 ----
def _events(d: Path, *evs):
    for e in evs:
        append_jsonl(d / "events.jsonl", {"ts": "t", **e})


def test_progress_run_suite_explore_and_stale(tmp_path):
    runs = tmp_path / "runs"
    r1 = runs / "run-20260929-010101-aaaa"
    r1.mkdir(parents=True)
    _events(r1, {"event": "run_start", "case": "TC-X", "title": "样例", "steps": ["S1", "S2", "S3"]},
            {"event": "step_start", "step": "S1"}, {"event": "step_done", "step": "S1", "outcome": "passed"},
            {"event": "step_start", "step": "S2"}, {"event": "motion_armed", "step": "S2"})
    s = progress.summarize(r1)
    assert (s["kind"], s["total"], s["done"], s["current"], s["state"], s["motion"]) == \
        ("run", 3, 1, "S2", "running", "motion_armed")
    old = time.time() - 600
    os.utime(r1 / "events.jsonl", (old, old))
    assert progress.summarize(r1)["state"] == "stale"  # 没有 run_end 且长时间不更新：不猜结论

    _events(r1, {"event": "step_done", "step": "S2", "outcome": "failed"}, {"event": "run_end", "verdict": "failed"})
    write_json(r1 / "run.json", {"verdict": {"verdict": "failed", "label": "失败", "attribution": "product"},
                                 "steps": [{"step_id": "S1", "action": "tap", "outcome": "passed",
                                            "attempts": [{"status": "ok"}]}], "assertions": []})
    s = progress.summarize(r1)
    assert (s["state"], s["verdict"], s["current"], s["attribution"]) == ("finished", "failed", None, "product")

    su = runs / "suite-20260929-020202-bbbb"
    su.mkdir()
    _events(su, {"event": "suite_start", "suite": "smoke", "total": 3},
            {"event": "case_start", "case": "A", "index": 1}, {"event": "case_done", "case": "A", "verdict": "passed"},
            {"event": "case_start", "case": "B", "index": 2})
    s = progress.summarize(su)
    assert (s["title"], s["total"], s["done"], s["current"]["case"], s["verdicts"], s["state"]) == \
        ("smoke", 3, 1, "B", {"passed": 1}, "running")

    ex = runs / "explore-20260929-030303-cccc"
    ex.mkdir()
    _events(ex, {"event": "explore_start", "start": "home"}, {"event": "observe", "state": "home"},
            {"event": "explore_end", "exit": "budget_exhausted"})
    assert progress.summarize(ex)["last_state"] == "home" and progress.summarize(ex)["state"] == "finished"

    co = runs / "console-20260929-035959-eeee"
    co.mkdir()
    _events(co, {"event": "console_start", "port": 1})
    assert progress.summarize(co)["state"] == "active"
    _events(co, {"event": "console_end", "uptime_s": 1})
    assert progress.summarize(co)["state"] == "finished"   # 已退出的控制台不再显示"活动中"
    old = time.time() - 900
    os.utime(co / "events.jsonl", (old, old))
    os.utime(co, (old, old))

    (runs / "mirror-probe").mkdir()  # 非运行目录不列出
    ids = [r["run_id"] for r in progress.list_runs(runs)]
    assert ids[0] == "explore-20260929-030303-cccc" and "mirror-probe" not in ids and len(ids) == 4
    assert ids[-1] == co.name  # 按事件文件更新时间倒序
    d = progress.detail(runs, r1.name)
    assert d["steps"][0] == {"step_id": "S1", "action": "tap", "outcome": "passed", "critical": None,
                             "attempts": 1, "last_status": "ok"}
    for bad in ("..", "../x", "run-1/../../etc", "", "a" * 200):
        assert progress.detail(runs, bad) is None


def test_progress_counts_survive_large_event_files_and_read_incrementally(tmp_path):
    d = tmp_path / "runs" / "run-20260929-040404-dddd"
    d.mkdir(parents=True)
    _events(d, {"event": "run_start", "case": "TC-L", "steps": ["S1", "S2"]},
            {"event": "step_start", "step": "S1"}, {"event": "step_done", "step": "S1", "outcome": "passed"},
            {"event": "step_start", "step": "S2"}, {"event": "motion_armed", "step": "S2"})
    # 长时间监视：大量采样事件把前面的步骤事件挤出任何固定大小的尾部窗口（约 1.3MB）
    _events(d, *[{"event": "observe", "state": "x", "pad": "p" * 400} for _ in range(3000)])
    s = progress.summarize(d)
    assert (s["title"], s["total"], s["done"], s["current"], s["motion"], s["events"]) == \
        ("TC-L", 2, 1, "S2", "motion_armed", 3005)   # 已完成数不倒退
    size = (d / "events.jsonl").stat().st_size
    with (d / "events.jsonl").open("ab") as f:       # 写到一半的行不计入，写完后再计入
        f.write(b'{"ts": "t", "event": "step_do')
    assert progress.summarize(d)["events"] == 3005
    with (d / "events.jsonl").open("ab") as f:
        f.write(b'ne", "step": "S2", "outcome": "passed"}\n')
    s = progress.summarize(d)
    assert (s["done"], s["current"], s["events"]) == (2, None, 3006)
    assert progress._SCANS[str((d / "events.jsonl").resolve())].offset > size   # 增量读取，不重读整个文件
    (d / "events.jsonl").write_text('{"ts": "t", "event": "run_start", "case": "TC-N", "steps": ["S1"]}\n',
                                    encoding="utf-8")
    s = progress.summarize(d)
    assert (s["title"], s["done"], s["events"]) == ("TC-N", 0, 1)   # 文件被替换（变小）：从头重读


# ---- 假设备 ----
class FakeDevice:
    kind, serial, spec = "fake", "FAKE", "fake:FAKE"

    def __init__(self):
        self.calls, self.override = [], 10000
        self.wh = (1320, 2848)        # 显示参数报告的尺寸
        self.frame_wh = (1320, 2848)  # 画面实际尺寸（旋转时先于显示参数变化）

    def display_info(self):
        return {"width_px": self.wh[0], "height_px": self.wh[1], "density": 3.375, "device_form": "bar"}

    def snapshot(self, local_path, fmt="jpeg"):
        Path(local_path).write_bytes(fake_jpeg(*self.frame_wh))
        time.sleep(0.01)
        return DeviceResult("snapshot", True, True)

    def _rec(self, *call):
        self.calls.append(call)
        return DeviceResult(call[0], True, True)

    def tap(self, x, y): return self._rec("tap", x, y)
    def long_press(self, x, y): return self._rec("long_press", x, y)
    def swipe(self, x1, y1, x2, y2, speed=2000): return self._rec("swipe", x1, y1, x2, y2, speed)
    def key(self, name): return self._rec("key", name)
    def wakeup(self): return self._rec("wakeup")
    def type_focused(self, text): return self._rec("type_focused", text)
    def screen_off_override(self): return self.override

    def set_screen_off(self, ms):
        self.override = ms
        return self._rec("set_screen_off", ms)


class FakeSession:
    def __init__(self, exc=None):
        self.exc, self.calls = exc, []

    def invoke(self, api, args):
        self.calls.append((api, args))
        if self.exc:
            raise self.exc
        return {"result": None}


def test_control_maps_coordinates_and_logs_without_text(tmp_path):
    dev, store = FakeDevice(), RunStore(tmp_path / "runs", kind="console")
    c = Control(dev, store, size=(1320, 2848))
    assert c.handle({"op": "tap", "x": 0.5, "y": 1.0})["ok"]
    assert dev.calls[-1] == ("tap", 660, 2847)  # 边界取最后一个像素
    r = c.handle({"op": "swipe", "x1": 0.5, "y1": 0.8, "x2": 0.5, "y2": 0.2, "duration_s": 0.3})
    assert r["ok"] and dev.calls[-1][:5] == ("swipe", 660, 2278, 660, 569) and 200 <= dev.calls[-1][5] <= 40000
    assert c.handle({"op": "text", "text": "A1-secret"})["ok"]
    assert not c.handle({"op": "key", "key": "power"})["ok"]          # 电源键会锁屏：不提供
    assert c.handle({"op": "tap", "x": 1.5, "y": 0})["error_class"] == "bad_request"
    log = (store.dir / "events.jsonl").read_text(encoding="utf-8")
    assert "A1-secret" not in log and '"text_len": 9' in log


def test_control_session_fallback_only_when_not_sent(tmp_path):
    dev, store = FakeDevice(), RunStore(tmp_path / "runs", kind="console")
    c = Control(dev, store, fast=FakeSession(), size=(1320, 2848))
    assert c.handle({"op": "tap", "x": 0.1, "y": 0.1})["via"] == "uitest_session" and not dev.calls
    c.fast = FakeSession(SessionUnavailable("未建立"))                 # 请求没发出：改走 hdc
    r = c.handle({"op": "tap", "x": 0.1, "y": 0.1})
    assert r["via"] == "hdc" and dev.calls == [("tap", 132, 284)]
    c.fast = FakeSession(RuntimeError("timeout after send"))           # 可能已经点下：不重发
    r = c.handle({"op": "tap", "x": 0.1, "y": 0.1})
    assert not r["ok"] and r["error_class"] == "session_error" and len(dev.calls) == 1


def test_snapshot_source_keeps_frames_in_memory(tmp_path):
    hub, dev = FrameHub(), FakeDevice()
    src = SnapshotSource(dev, hub, tmp_path / "tmp", interval=0.05)
    src.start()
    seq, frame = hub.wait(0, timeout=3)
    src.stop()
    assert seq >= 1 and jpeg_size(frame) == (1320, 2848) and hub.source == "snapshot_display"
    assert list((tmp_path / "tmp").iterdir()) == []  # 临时文件读入内存后删除


# ---- HTTP 服务 ----
@pytest.fixture
def served(tmp_path):
    def start(allow_input=True, keep_awake=None):
        dev = FakeDevice()
        store = RunStore(tmp_path / "runs", kind="console")
        con = Console(dev, store, tmp_path / "runs", allow_input=allow_input, keep_awake_min=keep_awake)
        ready, stop, box = threading.Event(), threading.Event(), {}

        def on_ready(url, port):
            box.update(url=url, port=port)
            ready.set()
        th = threading.Thread(target=serve, args=(con, "127.0.0.1", 0), kwargs={"on_ready": on_ready, "stop": stop},
                              daemon=True)
        th.start()
        assert ready.wait(5)
        started.append((stop, th))
        box.update(token=box["url"].split("token=", 1)[1], dev=dev, con=con, store=store)
        return box
    started = []
    yield start
    for stop, th in started:
        stop.set()
        th.join(10)


def req(box, method, path, host=None, headers=None, body=None, stream=False):
    conn = http.client.HTTPConnection("127.0.0.1", box["port"], timeout=5)
    h = {"Host": host or f"127.0.0.1:{box['port']}", **(headers or {})}
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    data = r.read(32) if stream else r.read()  # 流式应答没有长度：只读第一个分段头
    conn.close()
    return r, data


def test_console_auth_boundaries(served):
    box = served()
    tk = box["token"]
    r, body = req(box, "GET", f"/?token={tk}")
    csp = r.getheader("Content-Security-Policy")
    assert r.status == 200 and "nonce-" in csp and "unsafe-inline" not in csp and "{{NONCE}}" not in body.decode()
    assert r.getheader("Referrer-Policy") == "no-referrer" and r.getheader("X-Frame-Options") == "DENY"
    assert req(box, "GET", "/")[0].status == 401
    assert req(box, "GET", "/?token=wrong")[0].status == 401
    assert req(box, "GET", f"/?token={tk}", host="evil.example:80")[0].status == 421   # DNS rebinding
    r, body = req(box, "GET", "/api/status", headers={"X-VPD-Token": tk})
    st = json.loads(body)
    assert r.status == 200 and st["frames"]["source"] == "snapshot_display" and st["input"] is True
    assert req(box, "GET", f"/api/runs?token={tk}")[0].status == 200
    assert req(box, "GET", f"/api/runs/..%2F..%2Fetc?token={tk}")[0].status == 404

    tap = json.dumps({"op": "tap", "x": 0.25, "y": 0.5})
    json_h = {"Content-Type": "application/json"}
    assert req(box, "POST", f"/api/input?token={tk}", headers=json_h, body=tap)[0].status == 403   # token 须在请求头
    bad_origin = {**json_h, "X-VPD-Token": tk, "Origin": "http://evil.example"}
    assert req(box, "POST", "/api/input", headers=bad_origin, body=tap)[0].status == 403
    assert req(box, "POST", "/api/input", headers={"X-VPD-Token": tk, "Content-Type": "text/plain"},
               body=tap)[0].status == 415
    assert not box["dev"].calls
    ok_h = {**json_h, "X-VPD-Token": tk, "Origin": f"http://127.0.0.1:{box['port']}"}
    r, body = req(box, "POST", "/api/input", headers=ok_h, body=tap)
    assert r.status == 200 and json.loads(body)["ok"] and box["dev"].calls == [("tap", 330, 1424)]
    events = (box["store"].dir / "events.jsonl").read_text(encoding="utf-8")
    assert tk not in events and "console_input" in events   # token 不进日志


def test_console_frames_stream_view_only_and_keep_awake(served):
    box = served(allow_input=False, keep_awake=30)
    tk = box["token"]
    deadline = time.time() + 5
    while time.time() < deadline and req(box, "GET", f"/frame.jpg?token={tk}")[0].status != 200:
        time.sleep(0.05)
    r, body = req(box, "GET", f"/frame.jpg?token={tk}")
    assert r.status == 200 and r.getheader("Content-Type") == "image/jpeg" and jpeg_size(body) == (1320, 2848)
    r, chunk = req(box, "GET", f"/stream?token={tk}", stream=True)
    assert r.getheader("Content-Type").startswith("multipart/x-mixed-replace") and chunk.startswith(b"--vpdframe")
    h = {"Content-Type": "application/json", "X-VPD-Token": tk}
    assert req(box, "POST", "/api/input", headers=h, body='{"op":"key","key":"back"}')[0].status == 403
    assert box["dev"].calls == [("set_screen_off", 1800000)]
    box["con"].close()
    assert box["dev"].calls[-1] == ("set_screen_off", 10000)  # 恢复启动前的值，而不是简单撤销


def test_console_input_follows_rotation_or_refuses(tmp_path):
    dev = FakeDevice()
    con = Console(dev, RunStore(tmp_path / "runs", kind="console"), tmp_path / "runs").start()
    try:
        con.hub.wait(0, timeout=3)
        assert con.input({"op": "tap", "x": 0.75, "y": 0.5})["ok"] and dev.calls[-1] == ("tap", 990, 1424)

        def wait_frame(wh):
            deadline = time.time() + 3
            while time.time() < deadline and con.hub.size != wh:
                time.sleep(0.02)
            assert con.hub.size == wh
        dev.frame_wh = (2848, 1320)   # 转成横屏，但显示参数还没更新：拒绝，不按旧尺寸换算
        wait_frame((2848, 1320))
        r = con.input({"op": "tap", "x": 0.75, "y": 0.5})
        assert not r["ok"] and r["error_class"] == "display_mismatch" and dev.calls[-1] == ("tap", 990, 1424)
        assert con.input({"op": "key", "key": "back"})["ok"]   # 不依赖坐标的操作不受影响
        dev.wh = (2848, 1320)         # 显示参数跟上：重读后按新尺寸换算
        r = con.input({"op": "tap", "x": 0.75, "y": 0.5})
        assert r["ok"] and dev.calls[-1] == ("tap", 2136, 660)
        dev.frame_wh = (1424, 660)    # 帧流是半分辨率：只比较宽高比，不因像素不同误拒
        wait_frame((1424, 660))
        assert con.input({"op": "swipe", "x1": 0.5, "y1": 0.5, "x2": 0.1, "y2": 0.5})["ok"]
    finally:
        con.close()


def test_stream_falls_back_when_connections_die_before_any_frame():
    hub, gave_up = FrameHub(), threading.Event()
    src = UitestStreamSource("S", hub, on_give_up=lambda why: gave_up.set(), max_failures=3)
    src.retry_s, opened = 0.01, []
    src._open = lambda: opened.append(1)                        # 每次都能连上……
    src._pump = lambda: (_ for _ in ()).throw(ConnectionError("EOF"))   # ……但一帧没出就断开
    src._close = lambda: None
    src.start()
    assert gave_up.wait(3) and len(opened) == 3                 # 连上即清零的话永远到不了回退


def test_stream_failure_count_resets_only_after_frames():
    hub, gave_up = FrameHub(), threading.Event()
    src = UitestStreamSource("S", hub, on_give_up=lambda why: gave_up.set(), max_failures=2)
    src.retry_s, rounds = 0.01, []

    def pump():
        rounds.append(1)
        if len(rounds) <= 4:
            hub.push(fake_jpeg())                               # 前 4 轮都出过帧：每次断开都算一次恢复
        raise ConnectionError("EOF")
    src._open, src._pump, src._close = (lambda: None), pump, (lambda: None)
    src.start()
    # 第 4 轮出过帧后断开记 1 次，第 5 轮一帧没出再记 1 次 → 连续 2 次才回退（前 4 轮每轮都从 0 重新计）
    assert gave_up.wait(3) and len(rounds) == 5


def _awake_console(tmp_path):
    dev = FakeDevice()
    con = Console(dev, RunStore(tmp_path / "runs", kind="console"), tmp_path / "runs", keep_awake_min=30)
    assert dev.calls == [] and con.src is None   # 构造不产生副作用，资源在 serve() 里 start
    return dev, con


def test_console_cleans_up_when_bind_fails(tmp_path, monkeypatch):
    import vpdtest.console.server as srv
    dev, con = _awake_console(tmp_path)

    def occupied(*a, **k):
        raise OSError(10048, "address already in use")
    monkeypatch.setattr(srv, "_Server", occupied)
    with pytest.raises(OSError):
        serve(con, "127.0.0.1", 8765)
    assert dev.calls == [("set_screen_off", 1800000), ("set_screen_off", 10000)]   # 覆盖过，也恢复了
    assert con._closed and not con.src._th.is_alive()
    monkeypatch.undo()
    assert srv._Server.allow_reuse_address is False   # 端口被占用时报错，而不是和另一个进程共用端口


def test_console_cleans_up_when_ready_callback_fails(tmp_path):
    dev, con = _awake_console(tmp_path)

    def broken_stdout(url, port):
        raise BrokenPipeError("stdout closed")
    with pytest.raises(BrokenPipeError):
        serve(con, "127.0.0.1", 0, on_ready=broken_stdout)
    assert dev.calls[-1] == ("set_screen_off", 10000) and con._closed and not con.src._th.is_alive()
    events = (con.store.dir / "events.jsonl").read_text(encoding="utf-8")
    assert "console_end" in events


def test_no_fallback_source_after_close(tmp_path):
    dev = FakeDevice()
    con = Console(dev, RunStore(tmp_path / "runs", kind="console"), tmp_path / "runs")
    con.control = Control(dev, con.store, size=(1320, 2848))
    con.close()
    con._fallback("race: 帧流在 close 之后才到达失败上限")
    assert con.src is None   # 关闭之后不再起轮询线程
    assert con.start() is con and con.src is None   # 关闭后 start 也是空操作


def test_console_refuses_non_loopback_bind(tmp_path):
    assert is_loopback("127.0.0.1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.2") and not is_loopback("::1")
    assert not is_loopback("127.0.0.2")  # 虽是回环，但不在 Host 白名单里，访问会被 421 拒绝
    con = Console(None, RunStore(tmp_path / "runs", kind="console"), tmp_path / "runs")
    with pytest.raises(ValueError):
        serve(con, "0.0.0.0", 0, duration=0)


# ---- 执行器截图回退 ----
def test_screenshot_falls_back_to_snapshot_display_when_stream_holds_screencap(monkeypatch, tmp_path):
    dev = HdcDevice.__new__(HdcDevice)
    dev.hdc, dev.serial, dev.timeout_s, dev.spec = "hdc", "S", 5, "hdc:S"
    seen = []

    def fake_run(args, op, timeout=None, with_target=True):
        seen.append(args)
        if args[:3] == ["shell", "uitest", "screenCap"]:
            return DeviceResult(op, True, True, raw="ScreenCap failed: Failed to get display pixelMap")
        if args[:2] == ["shell", "snapshot_display"]:
            return DeviceResult(op, True, True, raw="success: snapshot display 0 , write to x as png")
        if args[:2] == ["file", "recv"]:
            Path(args[3]).write_bytes(b"png")
            return DeviceResult(op, True, True)
        return DeviceResult(op, True, True)
    monkeypatch.setattr(dev, "_run", fake_run)
    r = dev.screenshot(tmp_path / "screen.png")
    assert r.ok and r.data["fallback_from"]["op"] == "uitest screenCap" and (tmp_path / "screen.png").exists()
    assert any(a[:2] == ["shell", "snapshot_display"] and "-t" in a and "png" in a for a in seen)

    def locked_run(args, op, timeout=None, with_target=True):
        return DeviceResult(op, True, False, "screen_locked", "screen is locked")
    monkeypatch.setattr(dev, "_run", locked_run)
    r = dev.screenshot(tmp_path / "s2.png")
    assert r.error_class == "screen_locked" and "fallback_from" not in r.data  # 设备前提不满足：不换方式重试


def test_observation_records_screenshot_fallback(ws, monkeypatch):
    pytest.importorskip("cv2")
    from conftest import world
    from vpdtest.experience import Overlay
    from vpdtest.workspace import RunContext
    app = ws.app("motor_hm")
    store = RunStore(ws.runs_dir, kind="t")
    dev = ws.device(world("motor_hm_base.json"), state_path=store.dir / "replay_state.json")
    orig = dev.screenshot

    def with_fallback(path):
        r = orig(path)
        r.data["fallback_from"] = {"op": "uitest screenCap", "error": "ScreenCap failed: Failed to get display pixelMap"}
        return r
    monkeypatch.setattr(dev, "screenshot", with_fallback)
    ctx = RunContext(ws, app, dev, store, Overlay([]), mode="regression")
    dev.launch(app.bundle, app.ability)
    obs, _ = ctx.observe("fallback")
    assert obs["screenshot"]["status"] == "ok" and obs["screenshot"]["fallback_from"]["op"] == "uitest screenCap"
