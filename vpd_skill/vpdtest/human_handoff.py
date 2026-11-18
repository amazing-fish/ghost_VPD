"""Local handoff records for explicit human intervention. No transport or credentials."""
from __future__ import annotations

import re
from pathlib import Path

from .util import read_json, sha256_obj, write_json


def _source(result):
    # A formal run has the same identity in run, suite and experience validate.
    nested = result.get("handoff") or {}
    record = result.get("run_id") or result.get("run") or nested.get("run_id")
    if record:
        return {"record": record}
    if result.get("session"):
        return {"record": result["session"],
                **{k: result[k] for k in ("seq", "status") if result.get(k) is not None}}
    return None


def _directory(runs_dir):
    directory = Path(runs_dir) / ".human-handoffs"
    if directory.resolve() != Path(runs_dir).resolve() / directory.name or directory.is_symlink():
        raise ValueError("人工交接目录须位于本工作区的 runs 下")
    return directory


def register_handoffs(runs_dir, command, result, rows):
    """Idempotently record the local source. Never persist the action text or raw evidence."""
    required = [row for row in rows if row.get("action_required")]
    # suite repeats the stopping child's instruction at the top level.
    if command == "suite" and result.get("action_required") and any(
            row["action_required"] == result["action_required"] for row in required if row is not result):
        required = [row for row in required if row is not result]
    notices = []
    for row in required:
        source = _source(row)
        if source is None:
            notices.append({"registration": "unbound", "reason": "缺少源记录，请按原 action_required 人工处理"})
            continue
        event_id = sha256_obj(["human-handoff-v1", source, row["action_required"]])
        record = {"event_id": event_id, "source": source}
        if record in notices:
            continue
        try:
            path = _directory(runs_dir) / (event_id + ".json")
            if path.is_symlink():
                raise ValueError("人工交接记录不能是符号链接")
            if path.exists():
                if read_json(path) != record:
                    raise ValueError("交接记录与原事件不符")
            else:
                # Atomic replacement; concurrent producers of this key write identical bytes.
                write_json(path, record)
        except (OSError, ValueError):
            notices.append({**record, "registration": "unavailable"})
        else:
            notices.append(record)
    return notices


def cmd_handoff(ws, args):
    """Inspect a local event, or claim it once for this workspace's host."""
    event_id = args.event_id
    if not re.fullmatch("[0-9a-f]{64}", event_id):
        raise ValueError("需要已登记的完整 event_id")
    directory = _directory(ws.runs_dir)
    path = directory / (event_id + ".json")
    if path.is_symlink():
        raise ValueError("人工交接记录不能是符号链接")
    record = read_json(path)
    if not isinstance(record, dict) or record.get("event_id") != event_id or not record.get("source"):
        raise ValueError("人工交接记录无效")
    marker = directory / (event_id + ".claimed")
    if args.claim:
        try:
            marker.mkdir()
        except FileExistsError:
            if not marker.is_dir() or marker.is_symlink():
                raise ValueError("人工交接领取记录无效")
            claim = "already_claimed"
        else:
            claim = "acquired"
    else:
        if marker.is_symlink() or (marker.exists() and not marker.is_dir()):
            raise ValueError("人工交接领取记录无效")
        claim = "claimed" if marker.is_dir() else "unclaimed"
    return {"event_id": event_id, "source": record["source"], "claim": claim}
