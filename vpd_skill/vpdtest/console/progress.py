"""用例 / 用例集 / 探索进度：只读 runs/<id>/events.jsonl 与 run.json，不改动任何证据。

进度按事件累计（首次整读、之后验证旧字节并增量解析新记录），不因文件变大只看尾部而倒退。
运行中的判断依据是事件：有 run_start/suite_start 而没有对应的结束事件即为进行中；
最后一条事件超过 stale_s 没有更新时标为 stale（进程可能已被中断），不猜测结论。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
from pathlib import Path

KINDS = {"run": "用例", "suite": "用例集", "explore": "探索", "session": "单步会话", "console": "控制台", "demo": "演示"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}$")
TAIL_KEEP = 60
SCAN_CACHE_LIMIT = 256
_READ_CHUNK = 1 << 20


def valid_id(run_id: str) -> bool:
    return bool(run_id and _ID_RE.match(run_id) and ".." not in run_id)


def _kind(run_id: str) -> str:
    return run_id.split("-", 1)[0]


class _Scan:
    """累计已验证的进度；每轮校验全部旧字节，只增量解析匹配前缀后的新记录。"""

    def __init__(self):
        self.offset, self.pending = 0, b""
        self.digest = None
        self.identity, self.mtime_ns, self.invalid_lines = None, None, 0
        self.missing = False
        self.head = None
        self.count = 0
        self.steps_done, self.last_step_event, self.last_outcome = 0, None, None
        self.cases_done, self.verdicts, self.current_case = 0, {}, None
        self.motion, self.last_obs_state, self.ended = None, None, {}
        self.tail: list = []

    def copy(self) -> _Scan:
        candidate = _Scan()
        candidate.__dict__.update(self.__dict__)
        # 解析只替换事件引用；共享只读事件，复制会被追加或更新的容器。
        candidate.tail = self.tail.copy()
        candidate.verdicts = self.verdicts.copy()
        candidate.ended = self.ended.copy()
        return candidate

    def consume(self, chunk: bytes) -> None:
        self.offset += len(chunk)
        data = self.pending + chunk
        # 先按物理 CR/LF 分行再解码；CRLF 即使跨块/轮询，空段也可忽略。
        lines = re.split(br"[\r\n]", data)
        self.pending = lines.pop()  # 最后一段可能是正在写入的半行
        for line in lines:
            if not line.strip():
                continue
            try:
                event = _load_record(line.decode("utf-8"))
            except (ValueError, RecursionError):
                self.invalid_lines += 1
                continue
            if _valid_event(event):
                self.feed(event)
            else:
                self.invalid_lines += 1

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


def _remember_scan(key: str, scan: _Scan) -> None:
    """调用方持有锁；字典插入顺序用于保留最近读取的有限记录。"""
    _SCANS.pop(key, None)
    _SCANS[key] = scan
    if len(_SCANS) > SCAN_CACHE_LIMIT:
        del _SCANS[next(iter(_SCANS))]


def _load_record(raw) -> object:
    record = json.loads(raw)
    # Python 默认接受 NaN/Infinity 和孤立代理码；浏览器 JSON / UTF-8 不接受。
    # 在单条证据的读取边界检查，避免一个坏值破坏整个 HTTP 应答。
    json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return record


def _valid_event(e) -> bool:
    if not isinstance(e, dict) or not isinstance(e.get("event"), str) or not e["event"]:
        return False
    if not _text_fields(e, ("case", "title", "step", "outcome", "verdict", "state", "suite", "device")):
        return False
    if (e["event"] == "run_start" and e.get("steps") is not None
            and (not isinstance(e["steps"], list) or not all(isinstance(s, (str, dict)) for s in e["steps"]))):
        return False
    return not (e["event"] == "suite_start" and e.get("total") is not None
                and (type(e["total"]) is not int or e["total"] < 0))


def _signature(info) -> tuple:
    # atime 会因只读扫描改变，不参与稳定性检查；ctime 仅作辅助，不用于跳过内容验证。
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_chunks(stream, remaining: int):
    """只读取打开时的长度，不追逐持续追加的 EOF；提前 EOF 不是完整快照。"""
    while remaining:
        chunk = stream.read(min(_READ_CHUNK, remaining))
        if not chunk:
            raise OSError("events changed during read")
        remaining -= len(chunk)
        yield chunk


def _scan(path: Path) -> _Scan:
    key = str(path.absolute())  # 不解析证据文件的符号链接；I/O 异常由调用方隔离。
    with _SCANS_LOCK:  # 控制台是多线程服务：同一文件的校验与增量解析串行进行
        cached = _SCANS.get(key)
        try:
            # 预检稳定的非普通文件，避免无写端 FIFO 的阻塞；不把路径 stat 当内容身份。
            if not stat.S_ISREG(path.stat().st_mode):
                raise OSError("events must be a regular file")
            stream = path.open("rb")
        except FileNotFoundError:
            sc = _Scan()
            sc.missing = True
            _remember_scan(key, sc)
            return sc
        with stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise OSError("events must be a regular file")
            initial = _signature(opened)
            identity, size = initial[:2], initial[2]
            digest = hashlib.sha256()
            reuse = False
            if cached is not None and cached.identity == identity and size >= cached.offset:
                # 即使长度、mtime、ctime 都未变，也检查全部已读原始字节（含 pending）。
                for chunk in _read_chunks(stream, cached.offset):
                    digest.update(chunk)
                reuse = digest.digest() == cached.digest
            if reuse:
                sc = cached.copy()
            else:
                stream.seek(0)
                digest = hashlib.sha256()
                sc = _Scan()
            for chunk in _read_chunks(stream, size - sc.offset):
                digest.update(chunk)
                sc.consume(chunk)
            # 同一描述符负责定长读取与校验；路径替换、截断、追加等可见变化一律报未知。
            if _signature(os.fstat(stream.fileno())) != initial or _signature(path.stat()) != initial:
                raise OSError("events changed during read")
        sc.identity, sc.mtime_ns, sc.digest = identity, initial[3], digest.digest()
        if reuse:
            # 失败时仅丢弃候选，保留上次已验证进度；成功追加保留既有 _Scan 对象身份。
            cached.__dict__ = sc.__dict__
            sc = cached
        _remember_scan(key, sc)
        return sc


def _read_run(path: Path) -> dict:
    """只验证本读取器要消费的形状，不升级历史证据或替执行器判定。"""
    run = _load_record(path.read_text(encoding="utf-8"))
    if not isinstance(run, dict):
        raise TypeError("run record must be an object")
    for key in ("verdict", "asset_review"):
        if run.get(key) is not None and not isinstance(run[key], dict):
            raise ValueError("run metadata must be an object")
    for key in ("steps", "cleanup", "assertions"):
        rows = run.get(key, [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("run rows must be objects")
    verdict = run.get("verdict") or {}
    if (not _text_fields(run, ("validation_level",))
            or not _text_fields(verdict, ("verdict", "label", "attribution", "attribution_label"))
            or not _text_fields(run.get("asset_review") or {}, ("state",))):
        raise ValueError("display labels must be text")
    if verdict.get("coverage") is not None and not isinstance(verdict["coverage"], dict):
        raise ValueError("coverage must be an object")
    for key in ("verified", "not_applicable", "unverified"):
        rows = (verdict.get("coverage") or {}).get(key)
        if rows is not None and not isinstance(rows, list):
            raise ValueError("coverage rows must be lists")
    # 校验操作行的递归形状，避免某个坏 cleanup 隐藏整份运行。
    for row in run.get("steps", []) + run.get("cleanup", []):
        _step_detail(row)
    for assertion in run.get("assertions", []):
        if not _text_fields(assertion, ("id", "result", "reason")):
            raise ValueError("assertion labels must be text")
        _check_condition(assertion.get("guard"))
    return run


def _text_fields(record, keys) -> bool:
    return all(record.get(key) is None or isinstance(record[key], str) for key in keys)


def _exists(path: Path) -> bool:
    # Python 3.14 Path.exists() 会吞掉全部 OSError；这里仅把真正缺失当作可选文件缺省。
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _check_condition(record) -> None:
    if record is None:
        return
    if not isinstance(record, dict):
        raise TypeError("condition must be an object")
    rows = record.get("records")
    if rows is not None and (not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows)):
        raise ValueError("condition records must be objects")
    if not _text_fields(record, ("status",)):
        raise ValueError("condition status must be text")
    for row in rows if rows is not None else [record]:
        if not _text_fields(row, ("condition", "reason", "source", "obs", "ts")):
            raise ValueError("condition labels must be text")


def _read_problem(out: dict, source: str, reason: str, count: int | None = None) -> None:
    problem = {"source": source, "reason": reason}
    if count is not None:
        problem["count"] = count
    if problem not in out.setdefault("read_errors", []):
        out["read_errors"].append(problem)
    out.update(state="unknown", progress_reliable=False)


def summarize(run_dir: Path, now: float | None = None, stale_s: float = 120.0) -> dict:
    run_id = run_dir.name
    ev_path = run_dir / "events.jsonl"
    unreadable = False
    try:
        sc = _scan(ev_path)
    except OSError:
        unreadable, sc = True, _Scan()
    head = sc.head or {}
    kind = _kind(run_id)
    try:
        mtime = ev_path.stat().st_mtime if _exists(ev_path) else run_dir.stat().st_mtime
    except OSError:
        mtime = None
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
    run_error = None
    if kind == "run" and out.get("finished"):
        try:
            if _exists(run_json):
                run = _read_run(run_json)
                v = run.get("verdict") or {}
                out.update(verdict=v.get("verdict"), verdict_label=v.get("label"), attribution=v.get("attribution"))
                out.update({key: run[key] for key in ("validation_level", "informal", "asset_review") if key in run})
        except (ValueError, TypeError, RecursionError):
            run_error = "invalid_record"
        except OSError:
            run_error = "unreadable"
    now = time.time() if now is None else now
    old = mtime is None or now - mtime > stale_s
    if not out.get("finished"):
        out["state"] = "stale" if old and kind in ("run", "suite", "explore") else "running"
    else:
        out["state"] = "finished"
    if kind == "session" or (kind == "console" and not out.get("finished")):
        # 单步会话没有结束事件；控制台被强杀时也写不出 console_end：按最近更新时间区分
        out["state"] = "idle" if old else "active"
    if unreadable or sc.missing:
        _read_problem(out, "events.jsonl", "unreadable" if unreadable else "missing")
    if sc.invalid_lines:
        _read_problem(out, "events.jsonl", "invalid_record", sc.invalid_lines)
    if run_error:
        _read_problem(out, "run.json", run_error)
    return out


def list_runs(runs_dir: Path, limit: int | None = 40) -> list:
    """最近记录；limit=None 为显式历史读取，不承诺目录扫描期间的原子快照。"""
    runs_dir = Path(runs_dir)
    if not _exists(runs_dir):
        return []
    dirs = []
    for d in runs_dir.iterdir():
        if valid_id(d.name) and _kind(d.name) in KINDS:
            try:
                if not stat.S_ISDIR(d.stat().st_mode):
                    continue
                ev = d / "events.jsonl"
                stamp = ev.stat().st_mtime if _exists(ev) else d.stat().st_mtime
            except OSError:
                stamp = 0.0
            dirs.append((stamp, d))
    dirs.sort(key=lambda x: (x[0], x[1].name), reverse=True)
    # 先读旧记录，让全历史读取后仍能缓存最新运行供实时监控复用。
    rows = [summarize(d) for _, d in reversed(dirs[:limit])]
    return list(reversed(rows))


def detail(runs_dir: Path, run_id: str, tail: int = 60) -> dict | None:
    if not valid_id(run_id):
        return None
    d = Path(runs_dir) / run_id
    try:
        if not stat.S_ISDIR(d.stat().st_mode):
            return None
    except FileNotFoundError:
        return None
    except OSError:
        pass  # 保留已选择的条目，下面返回可诊断的读取失败而非让请求崩溃。
    out = summarize(d)
    try:
        out["tail"] = list(_scan(d / "events.jsonl").tail[-tail:])
    except OSError:
        out["tail"] = []
        _read_problem(out, "events.jsonl", "unreadable")
    rj = d / "run.json"
    try:
        if _exists(rj):
            run = _read_run(rj)
            out["steps"] = [_step_detail(s) for s in run.get("steps", [])]
            out["cleanup"] = [_step_detail(s) for s in run.get("cleanup", [])]
            out["assertions"] = [{k: a[k] for k in ("id", "at", "type", "result", "reason", "verified", "guard",
                                                    "advisory", "oracle_refs", "evidence") if k in a}
                                 for a in run.get("assertions", [])]
            out["verdict_detail"] = run.get("verdict")
            out["coverage"] = (run.get("verdict") or {}).get("coverage")
    except (ValueError, TypeError, RecursionError):
        _read_problem(out, "run.json", "invalid_record")
    except OSError:
        _read_problem(out, "run.json", "unreadable")
    rp = d / "report.md"
    try:
        out["report"] = str(rp) if _exists(rp) else None
    except OSError:
        out["report"] = None
        _read_problem(out, "report.md", "unreadable")
    return out


def _step_detail(step: dict) -> dict:
    if not isinstance(step, dict):
        raise TypeError("step must be an object")
    attempts, sub_steps = step.get("attempts") or [], step.get("sub_steps") or []
    if not isinstance(attempts, list) or not all(isinstance(a, dict) for a in attempts):
        raise ValueError("attempts must be objects")
    if not isinstance(sub_steps, list):
        raise TypeError("sub_steps must be a list")
    if not _text_fields(step, ("step_id", "action", "outcome")):
        raise ValueError("step labels must be text")
    _check_condition(step.get("condition"))
    row = {"step_id": step.get("step_id"), "action": step.get("action"), "outcome": step.get("outcome"),
           "critical": step.get("critical"), "attempts": len(step.get("attempts") or []),
           "last_status": ((step.get("attempts") or [{}])[-1]).get("status")}
    for key in ("condition", "reason", "final_obs"):
        if key in step:
            row[key] = step[key]
    if step.get("sub_steps"):
        row["sub_steps"] = [_step_detail(s) for s in step["sub_steps"]]
    return row
