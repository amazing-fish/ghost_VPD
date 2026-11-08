"""通用小工具：JSON 读写、ID、哈希与时间。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
_MISSING = object()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


def read_json(path, default: Any = _MISSING):
    p = Path(path)
    if not p.exists():
        if default is not _MISSING:
            return default
        raise FileNotFoundError(f"文件不存在: {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data: Any, exclusive: bool = False) -> Path:
    """exclusive=True 用于证据文件：目标已存在即报错，保证原始证据不会被覆盖。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    if exclusive:
        with p.open("x", encoding="utf-8") as f:
            f.write(text)
        return p
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    # Windows can briefly deny replacement while another process holds the file.
    # Retry the same atomic operation, never truncate/fallback; persistent denial still raises.
    for attempt in range(5):
        try:
            os.replace(tmp, p)
            break
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))
    return p


def append_jsonl(path, record: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(record, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
    with p.open("ab+") as f:
        prefix = b""
        if f.tell():
            f.seek(-1, os.SEEK_END)
            if f.read(1) not in (b"\n", b"\r"):
                prefix = b"\n"  # Preserve a complete final record without a line terminator.
        f.write(prefix + data)


def read_jsonl(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_jsonl_recoverable(path) -> tuple:
    """读取 JSONL，只容忍**末行在写入途中被截断**（进程被杀）：丢弃末行并返回 (records, dropped)。
    dropped 为 None 表示没有截断，否则是 {"tail": "<被丢弃内容的取证前缀>"}，由调用方留证。

    截断的两种形态都按截断处理：
    - 末行 JSON 写了一半且没有行终止符（最后一行不以换行结束）；
    - 末尾多字节 UTF-8 字符写了一半（事件文案是中文，ensure_ascii=False 落盘）——按字节读、
      只解码合法前缀，坏字节并入截断留证。
    末行虽然带了换行终止但 JSON 仍然非法 → 不是截断而是损坏，照常抛 json.JSONDecodeError；
    中间行损坏同样抛——不静默吞掉非截断的数据损坏。"""
    p = Path(path)
    if not p.exists():
        return [], None
    data = p.read_bytes()
    utf8_tail = b""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        rest = data[e.start:]
        if b"\n" in rest:  # 坏字节后面还有完整行：中间损坏，不是末尾截断
            raise
        text, utf8_tail = data[:e.start].decode("utf-8"), rest
    lines = text.splitlines()
    terminated = text.endswith("\n") or not text  # 空文件视为完整
    out, dropped = [], None
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            if i == len(lines) - 1 and not terminated:
                dropped = {"tail": (line[:104] + (f"<utf8:{len(utf8_tail)}B>" if utf8_tail else "") + "…")[:120]}
            else:
                raise
    if utf8_tail and dropped is None:
        # 解码层面截掉了坏字节（通常连同它所在的半行）：同样留证
        dropped = {"tail": f"<utf8 截断 {len(utf8_tail)}B>"}
    return out, dropped


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def sha256_obj(obj: Any) -> str:
    return sha256_bytes(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def short_hash(obj: Any, n: int = 12) -> str:
    return sha256_obj(obj)[:n]


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
