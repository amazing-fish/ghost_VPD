"""通用小工具：JSON 读写、ID、哈希与时间。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
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
    os.replace(tmp, p)
    return p


def append_jsonl(path, record: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_jsonl_recoverable(path) -> tuple:
    """读取 JSONL，只容忍末行被截断（进程被杀时写了一半）：丢弃末行并返回 (records, dropped)。
    dropped 为 None 表示没有截断，否则是 {"tail": "<末行前 120 字符>"}，由调用方留证。
    中间行损坏仍然抛 json.JSONDecodeError——不静默吞掉非末行的数据损坏。"""
    p = Path(path)
    if not p.exists():
        return [], None
    lines = p.read_text(encoding="utf-8").splitlines()
    out, dropped = [], None
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            if i == len(lines) - 1:
                dropped = {"tail": line[:120]}
            else:
                raise
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
