"""用例 / 用例集 / 探索进度：只读 runs/<id>/events.jsonl 与 run.json，不改动任何证据。

运行中的判断依据是事件：有 run_start/suite_start 而没有对应的结束事件即为进行中；
最后一条事件超过 stale_s 没有更新时标为 stale（进程可能已被中断），不猜测结论。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

KINDS = {"run": "用例", "suite": "用例集", "explore": "探索", "session": "单步会话", "console": "控制台", "demo": "演示"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}$")
TAIL_BYTES = 256 * 1024


def valid_id(run_id: str) -> bool:
    return bool(run_id and _ID_RE.match(run_id) and ".." not in run_id)


def _lines(path: Path, tail_bytes: int = TAIL_BYTES) -> tuple:
    """返回 (首行事件, 尾部事件列表)。只读文件尾部，长时间运行的 events.jsonl 也不会整读。"""
    if not path.exists():
        return None, []
    with path.open("rb") as f:
        first = f.readline()
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        chunk = f.read()
    raw = chunk.decode("utf-8", "replace").splitlines()
    if size > tail_bytes and raw:
        raw = raw[1:]  # 第一行可能被截断
    events = []
    for line in raw:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    try:
        head = json.loads(first.decode("utf-8", "replace")) if first.strip() else None
    except ValueError:
        head = None
    return head, events


def _kind(run_id: str) -> str:
    return run_id.split("-", 1)[0]


def summarize(run_dir: Path, now: Optional[float] = None, stale_s: float = 120.0) -> dict:
    run_id = run_dir.name
    ev_path = run_dir / "events.jsonl"
    head, events = _lines(ev_path)
    kind = _kind(run_id)
    mtime = ev_path.stat().st_mtime if ev_path.exists() else run_dir.stat().st_mtime
    out = {"run_id": run_id, "kind": kind, "kind_label": KINDS.get(kind, kind), "updated": mtime,
           "events": len(events), "last_event": events[-1]["event"] if events else None}
    names = [e.get("event") for e in events]
    if kind == "suite":
        start = head if head and head.get("event") == "suite_start" else {}
        done = [e for e in events if e.get("event") == "case_done"]
        cur = next((e for e in reversed(events) if e.get("event") == "case_start"), None)
        verdicts: dict = {}
        for e in done:
            verdicts[e.get("verdict")] = verdicts.get(e.get("verdict"), 0) + 1
        out.update(title=start.get("suite"), total=start.get("total"), done=len(done), verdicts=verdicts,
                   current=cur and {"case": cur.get("case"), "device": cur.get("device"), "index": cur.get("index")},
                   finished="suite_end" in names)
    elif kind == "run":
        start = head if head and head.get("event") == "run_start" else {}
        plan = start.get("steps") or []
        done = [e.get("step") for e in events if e.get("event") == "step_done"]
        last = next((e for e in reversed(events) if e.get("event") in ("step_start", "step_done")), None)
        end = next((e for e in reversed(events) if e.get("event") == "run_end"), None)
        out.update(title=start.get("case"), case_title=start.get("title"), total=len(plan) or None,
                   done=len(done), current=last.get("step") if last and last.get("event") == "step_start" else None,
                   last_outcome=next((e.get("outcome") for e in reversed(events) if e.get("event") == "step_done"), None),
                   finished=end is not None, verdict=end and end.get("verdict"))
        motion = next((e for e in reversed(events) if e.get("event", "").startswith("motion_")), None)
        out["motion"] = motion and motion["event"]
    else:
        obs = next((e for e in reversed(events) if e.get("event") == "observe"), None)
        out.update(title=None, last_state=obs and obs.get("state"),
                   finished=any(n in ("explore_end", "console_end") for n in names))
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
    _, events = _lines(d / "events.jsonl")
    out["tail"] = events[-tail:]
    rj = d / "run.json"
    if rj.exists():
        try:
            run = json.loads(rj.read_text(encoding="utf-8"))
            out["steps"] = [{"step_id": s.get("step_id"), "action": s.get("action"), "outcome": s.get("outcome"),
                             "critical": s.get("critical"), "attempts": len(s.get("attempts") or []),
                             "last_status": ((s.get("attempts") or [{}])[-1]).get("status")}
                            for s in run.get("steps", [])]
            out["assertions"] = [{k: a.get(k) for k in ("id", "result", "reason")} for a in run.get("assertions", [])]
            out["verdict_detail"] = run.get("verdict")
        except (ValueError, OSError):
            pass
    rp = d / "report.md"
    out["report"] = str(rp) if rp.exists() else None
    return out
