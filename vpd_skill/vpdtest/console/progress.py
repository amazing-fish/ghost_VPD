"""用例 / 用例集 / 探索进度：只读 runs/<id>/events.jsonl 与 run.json，不改动任何证据。

进度按事件累计（首次整读、之后增量读新追加的部分），不因文件变大只看尾部而倒退。
运行中的判断依据是事件：有 run_start/suite_start 而没有对应的结束事件即为进行中；
最后一条事件超过 stale_s 没有更新时标为 stale（进程可能已被中断），不猜测结论。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

KINDS = {"run": "用例", "suite": "用例集", "explore": "探索", "session": "单步会话", "console": "控制台", "demo": "演示"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}$")
TAIL_KEEP = 60
_READ_CHUNK = 1 << 20


def valid_id(run_id: str) -> bool:
    return bool(run_id and _ID_RE.match(run_id) and ".." not in run_id)


def _kind(run_id: str) -> str:
    return run_id.split("-", 1)[0]


class _Scan:
    """一条运行的累计进度。首次读完整个 events.jsonl，之后只读新追加的字节，
    所以长时间运行（监视采样很多）也不会因为只看尾部而让已完成数倒退。"""

    def __init__(self):
        self.offset, self.pending = 0, b""
        self.head = None
        self.count = 0
        self.steps_done, self.last_step_event, self.last_outcome = 0, None, None
        self.cases_done, self.verdicts, self.current_case = 0, {}, None
        self.motion, self.last_obs_state, self.ended = None, None, {}
        self.tail: list = []

    def feed(self, e: dict) -> None:
        ev = e.get("event") or ""
        self.count += 1
        if self.head is None:
            self.head = e
        if ev in ("step_start", "step_done"):
            self.last_step_event = e
            if ev == "step_done":
                self.steps_done += 1
                self.last_outcome = e.get("outcome")
        elif ev == "case_start":
            self.current_case = e
        elif ev == "case_done":
            self.cases_done += 1
            self.verdicts[e.get("verdict")] = self.verdicts.get(e.get("verdict"), 0) + 1
        elif ev == "observe":
            self.last_obs_state = e.get("state")
        elif ev.startswith("motion_"):
            self.motion = ev
        if ev in ("run_end", "suite_end", "explore_end", "console_end"):
            self.ended[ev] = e
        self.tail.append(e)
        if len(self.tail) > TAIL_KEEP:
            del self.tail[0]


_SCANS: dict = {}
_SCANS_LOCK = threading.Lock()


def _scan(path: Path) -> _Scan:
    key = str(path.resolve())
    with _SCANS_LOCK:  # 控制台是多线程服务：同一文件的增量读取串行进行
        sc = _SCANS.get(key)
        size = path.stat().st_size if path.exists() else 0
        if sc is None or size < sc.offset:  # 首次读取，或文件被替换/截断：从头来
            sc = _SCANS[key] = _Scan()
        if size > sc.offset:
            with path.open("rb") as f:
                f.seek(sc.offset)
                while True:
                    chunk = f.read(_READ_CHUNK)
                    if not chunk:
                        break
                    sc.offset += len(chunk)
                    data = sc.pending + chunk
                    lines = data.split(b"\n")
                    sc.pending = lines.pop()  # 最后一段可能是正在写入的半行
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            sc.feed(json.loads(line.decode("utf-8", "replace")))
                        except ValueError:
                            continue
        return sc


def summarize(run_dir: Path, now: Optional[float] = None, stale_s: float = 120.0) -> dict:
    run_id = run_dir.name
    ev_path = run_dir / "events.jsonl"
    sc = _scan(ev_path)
    head = sc.head or {}
    kind = _kind(run_id)
    mtime = ev_path.stat().st_mtime if ev_path.exists() else run_dir.stat().st_mtime
    out = {"run_id": run_id, "kind": kind, "kind_label": KINDS.get(kind, kind), "updated": mtime,
           "events": sc.count, "last_event": sc.tail[-1].get("event") if sc.tail else None}
    if kind == "suite":
        start = head if head.get("event") == "suite_start" else {}
        cur = sc.current_case
        out.update(title=start.get("suite"), total=start.get("total"), done=sc.cases_done, verdicts=dict(sc.verdicts),
                   current=cur and {"case": cur.get("case"), "device": cur.get("device"), "index": cur.get("index")},
                   finished="suite_end" in sc.ended)
    elif kind == "run":
        start = head if head.get("event") == "run_start" else {}
        plan = start.get("steps") or []
        last, end = sc.last_step_event, sc.ended.get("run_end")
        out.update(title=start.get("case"), case_title=start.get("title"), total=len(plan) or None,
                   done=sc.steps_done, current=last.get("step") if last and last.get("event") == "step_start" else None,
                   last_outcome=sc.last_outcome, finished=end is not None, verdict=end and end.get("verdict"),
                   motion=sc.motion)
    else:
        out.update(title=None, last_state=sc.last_obs_state,
                   finished=any(k in sc.ended for k in ("explore_end", "console_end")))
    run_json = run_dir / "run.json"
    if kind == "run" and out.get("finished") and run_json.exists():
        try:
            v = json.loads(run_json.read_text(encoding="utf-8")).get("verdict") or {}
            out.update(verdict=v.get("verdict"), verdict_label=v.get("label"), attribution=v.get("attribution"))
        except (ValueError, OSError):
            pass
    now = now or time.time()
    if not out.get("finished"):
        out["state"] = "stale" if now - mtime > stale_s and kind in ("run", "suite", "explore") else "running"
    else:
        out["state"] = "finished"
    if kind == "session" or (kind == "console" and not out.get("finished")):
        # 单步会话没有结束事件；控制台被强杀时也写不出 console_end：按最近更新时间区分
        out["state"] = "idle" if now - mtime > stale_s else "active"
    return out


def list_runs(runs_dir: Path, limit: int = 40) -> list:
    runs_dir = Path(runs_dir)
    if not runs_dir.exists():
        return []
    dirs = []
    for d in runs_dir.iterdir():
        if d.is_dir() and valid_id(d.name) and _kind(d.name) in KINDS:
            ev = d / "events.jsonl"
            dirs.append(((ev.stat().st_mtime if ev.exists() else d.stat().st_mtime), d))
    dirs.sort(key=lambda x: x[0], reverse=True)
    return [summarize(d) for _, d in dirs[:limit]]


def detail(runs_dir: Path, run_id: str, tail: int = 60) -> Optional[dict]:
    if not valid_id(run_id):
        return None
    d = Path(runs_dir) / run_id
    if not d.is_dir():
        return None
    out = summarize(d)
    out["tail"] = list(_scan(d / "events.jsonl").tail[-tail:])
    rj = d / "run.json"
    if rj.exists():
        try:
            run = json.loads(rj.read_text(encoding="utf-8"))
            out["steps"] = [_step_detail(s) for s in run.get("steps", [])]
            out["cleanup"] = [_step_detail(s) for s in run.get("cleanup", [])]
            out["assertions"] = [{k: a[k] for k in ("id", "at", "type", "result", "reason", "verified", "guard",
                                                    "advisory", "oracle_refs", "evidence") if k in a}
                                 for a in run.get("assertions", [])]
            out["verdict_detail"] = run.get("verdict")
            out["coverage"] = (run.get("verdict") or {}).get("coverage")
        except (ValueError, OSError):
            pass
    rp = d / "report.md"
    out["report"] = str(rp) if rp.exists() else None
    return out


def _step_detail(step: dict) -> dict:
    row = {"step_id": step.get("step_id"), "action": step.get("action"), "outcome": step.get("outcome"),
           "critical": step.get("critical"), "attempts": len(step.get("attempts") or []),
           "last_status": ((step.get("attempts") or [{}])[-1]).get("status")}
    for key in ("condition", "reason", "final_obs"):
        if key in step:
            row[key] = step[key]
    if step.get("sub_steps"):
        row["sub_steps"] = [_step_detail(s) for s in step["sub_steps"]]
    return row
