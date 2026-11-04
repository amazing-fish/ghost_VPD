"""Action-bound visual evidence for short notifications; missing frames never prove absence.

Reuse the existing uitest frame stream, rather than serial Observer polling. Frames
stay in bounded memory until the target foreground is checked again. No device
input is sent by this module; the existing Dispatcher owns the trigger.
"""
from __future__ import annotations

import hashlib
import math
import threading
import time

from .console.frames import FrameHub, UitestStreamSource, jpeg_size
from .util import new_id, now_iso, write_json

MAX_FRAMES = 120
MAX_BYTES = 12 * 1024 * 1024


def options_error(step, app=None):
    if "capture_transient" not in step:
        return None
    value = step["capture_transient"]
    if step.get("action") != "tap" or not isinstance(value, dict):
        return "capture_transient 仅支持tap，值须为对象"
    if set(value) - {"post_s", "ready_timeout_s"}:
        return "capture_transient 仅支持post_s与ready_timeout_s"
    for key, default in (("post_s", 3), ("ready_timeout_s", 5)):
        v = value.get(key, default)
        if type(v) not in (int, float) or not math.isfinite(v) or not 0.05 <= v <= 10:
            return f"capture_transient.{key}须为0.05..10秒的有限数值"
    if app is not None:
        target = step.get("target")
        target = app.targets.get(target) if isinstance(target, str) else target
        if not isinstance(target, dict) or target.get("side_effect") not in ("navigation", "user_data"):
            return "短提示采集仅支持明确声明navigation/user_data的目标，不用于运动或车控"
    return None


def make_source(device, hub):
    if device.kind != "hdc":
        raise RuntimeError("此设备未提供短提示帧源；离线替身须显式提供合成帧源")
    return UitestStreamSource(device.serial, hub, max_failures=1)


class EvidenceHub(FrameHub):
    def __init__(self, capture):
        super().__init__()
        self.capture = capture

    def push(self, data):
        super().push(data)
        self.capture.frame(data)

    def set_status(self, status, error=None):
        super().set_status(status, error)
        if status in ("error", "stalled"):
            self.capture.problem(status, error)


class TransientCapture:
    def __init__(self, ctx, step, attempt, *, source_factory=None):
        self.ctx, self.step_id, self.attempt = ctx, step["id"], attempt
        self.options = step["capture_transient"]
        self.post_s = float(self.options.get("post_s", 3))
        self.ready_timeout_s = float(self.options.get("ready_timeout_s", 5))
        self.factory = source_factory or make_source
        self.id = new_id("capture")
        self.dir = ctx.store.dir / "transient" / self.id
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.closed = False
        self.source = None
        self.hub = None
        self.latest_valid = False
        self.frames, self.observations, self.problems = [], [], []
        self.bytes = self.dropped = 0
        self.trigger = None
        self.started = time.monotonic()
        self.summary = {"capture_id": self.id, "step_id": self.step_id, "attempt": attempt,
                        "source": "uitest_stream", "status": "starting", "started_at": now_iso(),
                        "post_s": self.post_s, "negative_evidence": False,
                        "frame_time_basis": "host_monotonic_arrival_not_device_exposure",
                        "note": "变化驱动帧流不保证连续无缺口；未捕获不等于未提示；图片须结合语义检查"}

    def problem(self, kind, detail, *, force=False):
        with self.lock:
            if (not self.closed or force) and len(self.problems) < 30:
                self.problems.append({"kind": kind, "detail": str(detail or "")[:500],
                                      "t": round(time.monotonic() - self.started, 6)})

    def frame(self, data):
        with self.lock:
            if self.closed:
                return
            stamp = time.monotonic()
            if self.trigger and stamp > self.trigger["clock"] + self.post_s:
                return
            if jpeg_size(data) is None or not data.endswith(b"\xff\xd9"):
                self.latest_valid = False
                self.problem("invalid_frame", "帧不是完整JPEG")
                return
            self.latest_valid = True
            record = {"clock": stamp, "data": bytes(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "phase": "after_trigger" if self.trigger is not None else "baseline"}
            if self.trigger is None:
                # Keep only the latest pre-trigger baseline. It cannot prove a response.
                self.frames[:] = [record]
                self.bytes = len(data)
            elif len(self.frames) >= MAX_FRAMES or self.bytes + len(data) > MAX_BYTES:
                self.dropped += 1
                return
            else:
                self.frames.append(record)
                self.bytes += len(data)
            self.ready.set()

    def start(self):
        fg = self.ctx.device.foreground()
        self.summary["foreground_before"] = fg
        if fg != self.ctx.app.bundle or not self.ctx.policy.screenshot_allowed(fg):
            self.summary.update(status="withheld", reason="前台未知或不属于本次被测APP，未开启帧源")
            return False
        try:
            self.hub = EvidenceHub(self)
            self.source = self.factory(self.ctx.device, self.hub)
            self.source.start()
            if not self.ready.wait(self.ready_timeout_s):
                self.summary.update(status="unavailable", reason="触发前未取得合格首帧，未下发触发动作")
                return False
        except Exception as exc:
            self.problem("source_start_error", exc)
            self.summary.update(status="unavailable", reason="帧源不可用，未下发触发动作")
            return False
        self.summary["status"] = "armed"
        self.ctx.store.event("transient_armed", capture_id=self.id, step=self.step_id)
        return True

    def mark_trigger(self):
        """Called only immediately before the final, approved physical tap."""
        with self.lock:
            if self.trigger is not None:
                raise RuntimeError("同一采集窗口不能绑定第二次提交")
            thread = getattr(self.source, "_th", None)
            live = thread is None or thread.is_alive()
            self.summary["source_at_dispatch"] = {"status": self.hub.status if self.hub else None,
                                                   "thread_live": live, "latest_frame_valid": self.latest_valid}
            if self.closed or not self.latest_valid or not live or self.hub is None or self.hub.status == "error":
                self.summary.update(status="unavailable", reason="最终下发前帧源已失败或停止，触发动作未下发")
                return False
            self.trigger = {"clock": time.monotonic(), "at": now_iso(),
                            "action_epoch_before": self.ctx.actions, "sent": None, "executor_ok": None}
        self.ctx.store.event("transient_trigger", capture_id=self.id, step=self.step_id,
                             action_epoch_before=self.trigger["action_epoch_before"])
        return True

    def receipt(self, result):
        with self.lock:
            self.trigger.update(sent=result.sent, executor_ok=result.ok, error_class=result.error_class)

    def observation(self, obs, tree):
        with self.lock:
            if self.closed or not self.trigger:
                return
            stamp = time.monotonic()
            if stamp > self.trigger["clock"] + self.post_s:
                return
            # Record only references. Verdict reloads the original complete tree,
            # checks freshness and scope, and never trusts the 60-item summary.
            self.observations.append({"obs": obs["obs_id"], "t": round(stamp - self.trigger["clock"], 6),
                                      "action_epoch": obs.get("action_epoch")})

    def finish(self):
        if self.trigger:
            remaining = self.trigger["clock"] + self.post_s - time.monotonic()
            if remaining > 0:
                threading.Event().wait(remaining)
        # The collection window is over before stopping the producer. The real
        # stream loop reports socket shutdown as an error; this is not a capture
        # failure. Keep all earlier problems and explicitly record stop failures.
        with self.lock:
            self.closed = True
        if self.source is not None:
            try:
                self.source.stop()
                thread = getattr(self.source, "_th", None)
                if thread is not None and thread.is_alive():
                    self.problem("source_stop_unconfirmed", "帧源线程尚未退出", force=True)
            except Exception as exc:
                self.problem("source_stop_error", exc, force=True)
        with self.lock:
            self.closed = True
            fg = self.ctx.device.foreground()
            self.summary["foreground_after"] = fg
            allowed = fg == self.ctx.app.bundle and self.ctx.policy.screenshot_allowed(fg)
            self.dir.mkdir(parents=True, exist_ok=False)
            saved = []
            if allowed and self.summary.get("foreground_before") == fg:
                for i, f in enumerate(self.frames):
                    dest = self.dir / f"frame-{i:04d}.jpeg"
                    with dest.open("xb") as h:
                        h.write(f["data"])
                    saved.append({"path": self.ctx.store.rel(dest), "sha256": f["sha256"],
                                  "t": round(f["clock"] - (self.trigger or {}).get("clock", self.started), 6),
                                  "phase": f["phase"]})
                if self.trigger:
                    self.summary["status"] = "captured" if any(f["phase"] == "after_trigger" for f in saved) else "gap"
                elif self.summary["status"] == "armed":
                    self.summary["status"] = "not_triggered"
            else:
                self.summary.update(status="withheld", reason="窗口末查前台未知或变化，帧未落盘；不采用此窗口作判据")
            clocks = [f["clock"] for f in self.frames if f["phase"] == "after_trigger"]
            start = self.trigger["clock"] if self.trigger else self.started
            end = start + self.post_s if self.trigger else time.monotonic()
            gaps = [b-a for a,b in zip([start]+clocks, clocks+[end])]
            trigger = {k:v for k,v in (self.trigger or {}).items() if k != "clock"}
            self.summary.update(frames=saved, observations=self.observations if allowed else [],
                                trigger=trigger or None, problems=self.problems, dropped_frames=self.dropped,
                                largest_observed_gap_s=round(max(gaps, default=0), 6), ended_at=now_iso(),
                                manifest=self.ctx.store.rel(self.dir / "manifest.json"))
            self.frames.clear()
            write_json(self.dir / "manifest.json", self.summary, exclusive=True)
        self.ctx.store.event("transient_done", capture_id=self.id, step=self.step_id,
                             status=self.summary["status"], manifest=self.summary["manifest"], frames=len(saved))
        return self.summary


def seen(assertion, step, load, bundle):
    """Positive complete-tree evidence can prove seen. Visual-only/absence stays unknown."""
    import re
    pattern = re.compile(assertion["pattern"])
    captures = [a["transient"] for a in step.get("attempts", []) if a.get("transient")]
    matches = []
    for cap in captures:
        trigger = cap.get("trigger") or {}
        if cap.get("status") not in ("captured", "gap") or trigger.get("sent") is not True:
            continue
        epoch = trigger.get("action_epoch_before", -2) + 1
        for entry in cap.get("observations", []):
            if not isinstance(entry.get("t"), (int, float)) or not 0 <= entry["t"] <= cap["post_s"]:
                continue
            obs, tree = load(entry["obs"])
            if (obs.get("fresh") is not True or obs.get("foreground") != bundle
                    or obs.get("action_epoch") != epoch or entry.get("action_epoch") != epoch
                    or (obs.get("tree") or {}).get("status") != "ok" or tree is None):
                continue
            for text in tree.visible_texts():
                if pattern.search(text):
                    matches.append({"obs": entry["obs"], "text": text, "capture_id": cap["capture_id"]})
    ev = {"captures": captures, "matches": matches, "negative_evidence": False}
    if len({m["obs"] for m in matches}) >= assertion.get("min_count", 1):
        return bool(assertion.get("expected", True)), ev
    errors = [p for cap in captures for p in cap.get("problems", [])
              if p.get("kind") in ("error", "source_start_error", "source_stop_error", "source_stop_unconfirmed", "invalid_frame")]
    if errors:
        return None, {**ev, "execution_error": {"stage": "transient_capture", "attribution": "tool",
                                               "error_class": "transient_capture_error", "error": errors},
                      "reason": "短提示窗口有明确采集错误，不能判断为产品无提示"}
    return None, {**ev, "reason": "短提示未取得肯定文字证据；查绑定帧进行视觉核验。漏采、静止帧、图像识别缺口或采集失败均不证明无提示"}
