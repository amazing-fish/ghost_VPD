"""步骤结果与错误归因的共用定义。

只依赖标准库，供 dispatch / unlock / sysdialog / conditions / readrefs / execute / verdict 共用，
避免这些下层模块为了几个小工具反向依赖 verdict。
"""
from __future__ import annotations

from pathlib import Path

PASSED = ("passed", "passed_after_adaptation", "passed_after_recovery")
ENVIRONMENT_ERRORS = frozenset({"device_offline", "transport_unavailable", "screen_off", "screen_locked"})


def error_attribution(error_class) -> str:
    return "environment" if error_class in ENVIRONMENT_ERRORS else "tool"


def stale(obs) -> bool:
    """观测缺失、被标记为不新鲜或树已过时：原始树只作现场证据，不能用于判定。"""
    return not obs or obs.get("fresh") is False or (obs.get("tree") or {}).get("status") == "stale"


def observation_tree_relative(observed: dict, obs_id: str) -> str:
    """Only the two collector-owned filenames in this observation are selectable."""
    import re
    if (not isinstance(obs_id, str) or not re.fullmatch(r"obs-[0-9]{4,}", obs_id)
            or int(obs_id[4:]) < 1 or obs_id != f"obs-{int(obs_id[4:]):04d}"):
        raise ValueError("invalid observation id")
    prefix = f"obs/{obs_id}/"
    if not isinstance(observed, dict) or ("tree" in observed and not isinstance(observed["tree"], dict)):
        raise ValueError("observation and tree metadata must be objects")
    record = observed.get("tree", {})
    value = record.get("path", prefix + "tree.json")  # Legacy records omitted path.
    if value not in (prefix + "tree.json", prefix + "tree_recheck.json"):
        raise ValueError("tree.path must select this observation's collected tree")
    return value


def _local_evidence_path(root: Path, relative: str) -> Path:
    """Reject symlinks/junctions before reading selected evidence."""
    import stat
    path = Path(root).absolute() / relative
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError("evidence links are not allowed")
        if current.exists() and getattr(current.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("evidence reparse points are not allowed")
    return path


def load_observation(store_dir: Path, obs_id: str, *, allow_legacy_tree: bool = False):
    """Load the selected tree, never silently substitute the initial empty tree."""
    from .tree import UiTree
    from .util import read_json
    # Validate the observation component before reading its metadata.
    observation_tree_relative({}, obs_id)
    directory = _local_evidence_path(store_dir, f"obs/{obs_id}/observation.json")
    observed = read_json(directory, {}) if allow_legacy_tree else read_json(directory)
    try:
        relative = observation_tree_relative(observed, obs_id)
    except ValueError as exc:
        return {**(observed if isinstance(observed, dict) else {}),
                "tree": {"status": "error", "error_class": "tree_collection_error", "error": str(exc)}}, None
    usable = not stale(observed) and (observed.get("tree") or {}).get("status") not in ("error", "unavailable", "not_collected")
    if not observed:
        usable = True
    if not usable:
        return observed, None
    try:
        path = _local_evidence_path(store_dir, relative)
        if not path.is_file():
            raise ValueError("selected observation tree is missing")
        return observed, UiTree(read_json(path))
    except (OSError, ValueError) as exc:
        return {**observed, "tree": {**(observed.get("tree") or {}), "status": "error",
                                    "error_class": "tree_collection_error", "error": str(exc)}}, None


def collection_error(obs: dict) -> dict:
    """只识别采集器明确报告的错误；空树、过时和无法识别前台不等于查询故障。"""
    obs = obs or {}
    tree = obs.get("tree") or {}
    foreground = obs.get("foreground_error") or (obs.get("page") or {}).get("observation_error")
    queries = [q for q in obs.get("foreground_query_errors") or []
               if q.get("error_class") not in (None, "foreground_unknown", "foreground_changed")]
    ec = foreground if foreground not in (None, "foreground_unknown", "foreground_changed") else None
    if queries:
        detail = {k: queries[0].get(k) for k in ("error_class", "error", "raw")}
    elif ec:
        detail = {"error_class": ec, "error": (obs.get("foreground_error_detail") or {}).get("error")}
    elif tree.get("status") == "error" or tree.get("captured_status") == "error":
        detail = {"error_class": tree.get("error_class") or "tree_collection_error", "error": tree.get("error")}
    else:
        return {}
    return {**detail, "stage": "observation", "attribution": error_attribution(detail["error_class"]),
            "evidence": {"obs": obs.get("obs_id"), "ts": obs.get("ts"), "tree": tree,
                         "foreground_query_errors": queries,
                         "foreground_error": foreground, "foreground_error_detail": obs.get("foreground_error_detail")}}


def execution_error(step: dict, stage: str = "step") -> dict:
    """提取最终失败，不把已恢复的首次错误重新升为本次错误。原始记录仍留在 run。"""
    subs = step.get("sub_steps") or []
    if subs:
        failed = next((s for s in reversed(subs) if s.get("outcome") not in PASSED), None)
        if failed:
            return {**execution_error(failed, stage), "parent_step": step.get("step_id")}
    att = (step.get("attempts") or [{}])[-1]
    rec = (step.get("recoveries") or [{}])[-1]
    post = rec.get("post") if rec.get("after_attempt") == att.get("n") else None
    dispatch = att.get("dispatch") or {}
    detail = step.get("execution_error") or (post or {}).get("execution_error") or att.get("execution_error") or {}
    status = step.get("final_status") or att.get("status")
    return {"stage": detail.get("stage") or ("dispatch" if status == "device_error" else
                                             "stop_verification" if status == "stop_not_confirmed" else stage),
            "error_class": detail.get("error_class") or att.get("error_class") or dispatch.get("error_class") or status,
            "error": detail.get("error") or dispatch.get("error"),
            "evidence": {"step": step.get("step_id"), "obs_before": att.get("obs_before"),
                         "obs_after": step.get("final_obs") or att.get("obs_after"),
                         "dispatch": dispatch, "stop_check": att.get("stop_check"),
                         "collection": detail.get("evidence"), "post": post or att.get("post")}}
