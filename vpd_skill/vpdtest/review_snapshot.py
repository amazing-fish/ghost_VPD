"""Optional engine-file evidence explains a review binding; it never authorizes it."""
from pathlib import Path
import re

from .util import sha256_file, sha256_obj


def engine_files(engine_dir: Path) -> dict:
    """Keep the existing engine_hash input, without caching across inspections."""
    return {p.relative_to(engine_dir).as_posix(): sha256_file(p)
            for p in sorted(engine_dir.rglob("*.py"))}


def capture(files: dict) -> dict:
    return {"schema": 1, "files": dict(files)}


def _valid_files(files) -> bool:
    return isinstance(files, dict) and all(
        isinstance(path, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.py", path)
        and all(part not in ("", ".", "..") for part in path.split("/"))
        and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        for path, digest in files.items())


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason, "added": None, "removed": None,
            "modified": None, "unchanged_count": None}


def engine_changes(record, current_files: dict) -> dict:
    """Compare only a saved manifest consistent with its old engine_hash.

    Relative names are data, never read paths. Self-consistency is not proof of
    reviewer identity, source authenticity, or an original device evidence pack.
    """
    if not isinstance(record, dict) or "engine_snapshot" not in record:
        return _unavailable("snapshot_missing")
    snapshot = record["engine_snapshot"]
    if not isinstance(snapshot, dict):
        return _unavailable("snapshot_invalid")
    if type(snapshot.get("schema")) is not int or snapshot["schema"] != 1:
        return _unavailable("snapshot_schema_unsupported")
    previous = snapshot.get("files")
    if not _valid_files(previous):
        return _unavailable("snapshot_manifest_invalid")
    binding = record.get("binding")
    if not isinstance(binding, dict) or not isinstance(binding.get("engine_hash"), str):
        return _unavailable("snapshot_binding_missing")
    if sha256_obj(previous) != binding["engine_hash"]:
        return _unavailable("snapshot_binding_mismatch")
    if not _valid_files(current_files):
        return _unavailable("current_manifest_invalid")
    old, new = set(previous), set(current_files)
    return {"available": True, "reason": None, "added": sorted(new - old), "removed": sorted(old - new),
            "modified": sorted(path for path in old & new if previous[path] != current_files[path]),
            "unchanged_count": sum(previous[path] == current_files[path] for path in old & new)}
