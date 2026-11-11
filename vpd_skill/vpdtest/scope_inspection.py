"""Read-only, offline candidate-scope diagnostics, never calibration or an Oracle.

inspect_scope(app, spec, observation, tree) is pure (tree may be UiTree/raw JSON).
inspect_saved_scope(app, spec, evidence_dir, obs_id) reads only fixed files below
one explicitly selected run/session directory. Only collector-owned tree names
in the same observation can be selected; arbitrary JSON paths are never followed. Neither function creates targets, cases, expectations, or permissions.
Hashes bind the bytes read now, not original capture authenticity or same-frame
provenance. Saved fresh=True refers only to collection time, never applicability
now. The caller must not use Workspace's local configuration/device facilities.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import struct
import zlib

from .case_templates import TEMPLATE_VERSION, TEMPLATES, _text, scope_shape, validate_static_scope_target
from .locate import resolve
from .tree import UiTree

OBS_ID = re.compile(r"obs-[0-9]{4,12}\Z")
LIMITS = {"observation.json": 4 * 1024**2, "tree.json": 32 * 1024**2, "tree_recheck.json": 32 * 1024**2,
          "screen.png": 32 * 1024**2, "run.json": 32 * 1024**2, "events.jsonl": 32 * 1024**2}
CAVEATS = [
    "仅离线结构诊断；不生成用例、业务预期、定位登记、生产校准或设备执行许可。",
    "fresh 仅表示历史采集时的记录，不表示当前仍可用。",
    "本次哈希仅绑定实际读取字节，不认证原始同帧、来源真实性或防伪。",
    "树中完整可见不等于像素级视觉验收；专用容器的真实业务角色仍须人工核对。",
    "不推断隐藏/虚拟化标签、地图或后端完整集合；不从 UI 反填 expected。",
]


def validate_inspection_spec(app, spec):
    if (not isinstance(spec, dict) or not {"template_id", "candidate_target"} <= set(spec)
            or set(spec) - {"template_id", "candidate_target", "scope_shape"}):
        raise ValueError("scope 诊断输入须包含 template_id/candidate_target，仅可选加 scope_shape")
    if not isinstance(spec["template_id"], str) or spec["template_id"] not in TEMPLATES:
        raise ValueError("scope 诊断只支持 TC-QK-240/241/243")
    scope_shape(spec["template_id"], spec)
    _text(app.bundle, "app.bundle")
    if app.id != "qiankun_hm":
        raise ValueError("显示 scope 诊断只属于 qiankun_hm")
    target = spec["candidate_target"]
    if not isinstance(target, dict) or set(target) - {"side_effect", "forbid_tap", "strategies", "label", "source"}:
        raise ValueError("candidate_target 不允许校准/status/expected/secret/动态/fallback 等额外字段")
    xpath = validate_static_scope_target(target)
    for key in ("label", "source"):
        if key in target:
            _text(target[key], f"candidate_target.{key}")
    for strategy in target["strategies"]:
        if "source" in strategy:
            _text(strategy["source"], "candidate_target.strategy.source")
    return deepcopy(target), xpath


def _base(app, spec):
    return {"schema": 2, "template_version": TEMPLATE_VERSION,
            "scope_shape": scope_shape(spec["template_id"], spec),
            "supported_shapes": ["text_container", "text_leaf"] if spec["template_id"] == "TC-QK-240" else ["text_container"],
            "mode": "offline_diagnostic", "template_id": spec["template_id"],
            "app": app.id, "bundle": app.bundle, "semantic_role": "requires_human_review",
            "current_applicability": "not_checked", "production_calibration": "not_granted",
            "diagnostic_status": "inspected", "inspection_errors": [], "issues": [],
            "localization": {"status": "unavailable"}, "shape": {"compatible": None, "evidence_usable": False},
            "caveats": list(CAVEATS)}


def _issue(result, code, field, message, *, error=False, missing=False):
    row = {"code": code, "field": field, "message": message}
    result["issues"].append(row)
    if error:
        result["inspection_errors"].append(deepcopy(row))
        result["diagnostic_status"] = "conflict"
    elif missing and not result["inspection_errors"]:
        result["diagnostic_status"] = "incomplete"


def _record(value):
    return value if isinstance(value, dict) else {}


def _observation(app, observation, result):
    if not isinstance(observation, dict):
        _issue(result, "invalid_observation", "observation", "观测必须是 JSON 对象", error=True)
        return {}
    try:
        _json_data(observation)
    except ValueError as exc:
        _issue(result, "invalid_observation", "observation", str(exc), error=True)
        return {}
    obs = observation
    for key in ("tree", "screenshot", "scope", "page", "layout"):
        if key in obs and not isinstance(obs[key], dict):
            _issue(result, "invalid_observation_field", key, "字段必须是对象", error=True)
    for key in ("obs_id", "ts", "device", "source", "foreground", "foreground_after"):
        if not isinstance(obs.get(key), str) or not obs[key].strip():
            _issue(result, "missing_observation_identity", key, "缺少有效历史采集身份/时间字段", missing=True)
    if isinstance(obs.get("obs_id"), str) and not OBS_ID.fullmatch(obs["obs_id"]):
        _issue(result, "invalid_observation_id", "obs_id", "观测 ID 不是规范 obs-数字", error=True)
    if obs.get("source") is not None and obs.get("source") not in ("hdc", "replay"):
        _issue(result, "invalid_source", "source", "历史采集来源仅支持 hdc/replay", error=True)
    if isinstance(obs.get("ts"), str):
        try:
            if datetime.fromisoformat(obs["ts"].replace("Z", "+00:00")).tzinfo is None:
                raise ValueError
        except ValueError:
            _issue(result, "invalid_capture_time", "ts", "采集时间须为带时区的 ISO 时间", error=True)
    for key in ("foreground", "foreground_before", "foreground_after", "foreground_before_shot"):
        if key in obs and obs[key] != app.bundle:
            _issue(result, "foreground_conflict", key, "采集前台与指定 APP bundle 不一致或未知", error=True)
    scoped_app = _record(obs.get("scope")).get("app")
    if scoped_app is not None and scoped_app != app.id:
        _issue(result, "app_conflict", "scope.app", "历史观测 app 与指定 APP 不一致", error=True)
    for key, expected in (("app", app.id), ("bundle", app.bundle)):
        if key in obs and obs[key] != expected:
            _issue(result, "app_conflict", key, "历史观测身份与指定 APP 不一致", error=True)
    for key in ("fresh", "process_alive"):
        if key in obs and type(obs[key]) is not bool:
            _issue(result, "invalid_collection_flag", key, "历史采集标记必须是布尔值", error=True)
    if obs.get("fresh") is not True:
        _issue(result, "collection_not_fresh", "fresh", "历史采集未明确记录 fresh=true", missing=True)
    if obs.get("process_alive") is not True:
        _issue(result, "process_not_confirmed", "process_alive", "历史采集未确认目标进程存活", missing=True)
    for key in ("foreground_error", "foreground_query_errors", "process_error"):
        if obs.get(key):
            _issue(result, "collection_error", key, "历史记录包含采集错误，不能作为完整一致证据", error=True)
    if _record(obs.get("page")).get("observation_error"):
        _issue(result, "collection_error", "page.observation_error", "历史页面识别记录采集错误", error=True)
    result["historical_observation"] = {key: deepcopy(obs.get(key)) for key in
        ("obs_id", "ts", "device", "source", "foreground", "foreground_before", "foreground_after",
         "fresh", "process_alive", "scope", "layout", "page")}
    result["historical_observation"]["freshness_scope"] = "collection_time_only"
    result["visual_evidence"] = {"status": "not_loaded", "recorded_status": _record(obs.get("screenshot")).get("status"),
                                  "pixel_review": "not_performed"}
    return obs


def _make_tree(raw):
    if isinstance(raw, UiTree):
        raw = raw.raw  # A new tree avoids mutating caller-owned XML/visibility caches.
    _json_data(raw)
    if not isinstance(raw, dict):
        raise ValueError("UI 树须为对象")
    stack = [(raw, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 200 or count > 100000 or not isinstance(node, dict):
            raise ValueError("UI 树节点/深度无效或超出诊断上限")
        attrs = node.get("attributes", {})
        children = node.get("children", [])
        if not isinstance(attrs, dict) or not isinstance(children, list):
            raise ValueError("UI 树 attributes/children 结构错误")
        if any(not isinstance(k, str) or not isinstance(v, (str, bool, int, float))
               or isinstance(v, float) and not math.isfinite(v) for k, v in attrs.items()):
            raise ValueError("UI 树属性必须是标量，不能含对象/空值")
        stack.extend((child, depth + 1) for child in children)
    return UiTree(deepcopy(raw))


def _node_evidence(tree, node, scope=None):
    bounds, visible = node.bounds, tree.visible_rect(node)
    contained = None
    if scope is not None:
        box = scope.bounds
        contained = bool(box and bounds and box[0] <= bounds[0] < bounds[2] <= box[2]
                         and box[1] <= bounds[1] < bounds[3] <= box[3])
    return {"node": node.brief(), "visible_rect": list(visible) if visible else None,
            "visible_lineage": tree.visible_lineage(node), "fully_visible": bool(bounds and visible == bounds
             and tree.visible_lineage(node)), "inside_scope": contained}


def inspect_scope(app, spec, observation, tree):
    """Pure, non-executable diagnostics. Invalid candidate specs raise ValueError."""
    target, xpath = validate_inspection_spec(app, spec)
    result = _base(app, spec)
    obs = _observation(app, observation, result)
    if tree is None:
        _issue(result, "tree_missing", "tree", "缺少 UI 树，无法诊断结构", missing=True)
        return result
    try:
        tree = _make_tree(tree)
    except (ValueError, TypeError, RecursionError) as exc:
        _issue(result, "invalid_tree", "tree", str(exc), error=True)
        return result
    recorded = _record(obs.get("tree")).get("status")
    recorded_nodes = _record(obs.get("tree")).get("nodes")
    if recorded_nodes is not None and (type(recorded_nodes) is not int or recorded_nodes != len(tree.nodes)):
        _issue(result, "tree_node_count_conflict", "tree.nodes", "采集记录节点数与实际树不一致", error=True)
    if recorded != tree.status:
        _issue(result, "tree_status_conflict", "tree.status", "采集状态与本次树结构不一致", error=True)
    if recorded != "ok":
        _issue(result, "tree_not_collected_ok", "tree.status", "历史树不是有效采集状态，仅列原始结构诊断", missing=True)
    try:
        # Reuse the established locator. No screenshot, device, dynamic selector,
        # overlay, fallback, or filesystem lookup is possible for this schema.
        located = resolve("offline_candidate", target, tree, None, Path("."))
        raw_hits = tree.xpath(xpath)  # Includes hidden nodes omitted by generic id lookup.
    except Exception as exc:  # lxml raises distinct selector/tag errors; no fallback.
        _issue(result, "localization_error", "candidate_target", f"定位错误: {type(exc).__name__}", error=True)
        return result
    result["localization"] = {"status": "missing", "locator_status": located["status"],
                              "match_count": len(raw_hits), "candidates": [_node_evidence(tree, n) for n in raw_hits]}
    # Never return locator point coordinates or a runnable target/action payload.
    if not raw_hits:
        _issue(result, "scope_missing", "candidate_target", "当前树没有匹配候选容器")
        return result
    if len(raw_hits) > 1:
        result["localization"]["status"] = "ambiguous"
        _issue(result, "scope_ambiguous", "candidate_target", "候选容器匹配多个节点，不选择其中一个")
        return result
    scope = raw_hits[0]
    evidence = _node_evidence(tree, scope)
    status = "unique"
    if not scope.visible or not evidence["visible_lineage"]:
        status = "hidden"
    elif not evidence["fully_visible"]:
        status = "clipped"
    result["localization"]["status"] = status
    # Match the template's XPath 1.0 normalize-space(@text) count exactly:
    # only XML whitespace is removed. Python str.strip() also removes NBSP and
    # other Unicode spaces and could hide extra template-shape text nodes.
    # This changes only offline diagnostics, not strict_visible_text semantics.
    nodes = [node for node in tree.nodes if tree.is_within(node, scope) and node.text.strip(" \t\r\n")]
    descendants = [node for node in nodes if node.idx != scope.idx]
    text_evidence = [_node_evidence(tree, node, scope) for node in nodes]
    problems = []
    if scope.parent is None:
        problems.append("root_scope")
    shape = scope_shape(spec["template_id"], spec)
    if shape == "text_leaf":
        if scope.type != "Text":
            problems.append("requires_text_node")
        if scope.children:
            problems.append("requires_leaf_without_children")
        if not scope.text.strip(" \t\r\n"):
            problems.append("requires_nonempty_own_text")
    elif scope.text.strip(" \t\r\n"):
        problems.append("scope_has_own_text")
    if status != "unique":
        problems.append("scope_not_fully_visible")
    if any(not item["inside_scope"] or not item["fully_visible"] for item in text_evidence):
        problems.append("text_hidden_clipped_or_outside_scope")
    if shape == "text_container" and spec["template_id"] in ("TC-QK-240", "TC-QK-241") and len(descendants) != 1:
        problems.append("requires_one_nonempty_text_descendant")
    if shape == "text_container" and not descendants:
        problems.append("no_nonempty_text_descendants")
    texts = [node.text for node in descendants]
    duplicates = sorted(text for text, count in Counter(texts).items() if count > 1)
    if spec["template_id"] == "TC-QK-243":
        if duplicates:
            problems.append("duplicate_labels")
        if len(texts) > 32:
            problems.append("template_label_limit_exceeded")
        result["observed_labels"] = {"values": texts, "unique_values": sorted(set(texts)), "duplicates": duplicates,
                                     "meaning": "observed_only_not_expected_set"}
        result["caveats"].append("243 无序精确集合是未确认提案；观察到的标签不能证明应有集合。")
    if spec["template_id"] == "TC-QK-241":
        result["legacy_count_format"] = {"matches": bool(len(texts) == 1 and re.fullmatch(r"共 [1-9][0-9]* 个", texts[0])),
                                          "meaning": "legacy_script_constraint_not_confirmed_general_requirement"}
        result["caveats"].append("241 的 ASCII 空格格式来自旧脚本限制，不是已确认的普适产品规范。")
    result["shape"] = {"compatible": not problems, "evidence_usable": False, "basis": "exposed_tree_structure_only",
                       "problems": problems, "nonempty_text_descendants": len(descendants), "text_nodes": text_evidence,
                       "text_count_basis": "xpath_1_0_normalize_space",
                       "description_used_as_text": False}
    for problem in problems:
        _issue(result, problem, "shape", "候选范围结构需核对，不能自动登记或补造预期")
    return result


def _json_data(value):
    """Reject non-JSON/cyclic values and unpaired Unicode before diagnostics echo."""
    stack, active, count = [(value, False)], set(), 0
    while stack:
        item, leaving = stack.pop()
        if leaving:
            active.remove(id(item))
            continue
        count += 1
        if count > 2000000:
            raise ValueError("JSON 结构超过诊断上限")
        if isinstance(item, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise ValueError("JSON 含未配对 Unicode 代理字符")
        elif isinstance(item, (dict, list)):
            if id(item) in active:
                raise ValueError("JSON 结构不能循环引用")
            active.add(id(item))
            stack.append((item, True))
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError("JSON 对象键必须是字符串")
                stack.extend((key, False) for key in item)
                stack.extend((child, False) for child in item.values())
            else:
                stack.extend((child, False) for child in item)
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("JSON 含非有限数字")
        elif item is not None and not isinstance(item, (int, bool)):
            raise ValueError("值不是 JSON 数据类型")


def _decode(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("JSON 含重复键")
            out[key] = value
        return out
    def constant(_):
        raise ValueError("JSON 含非有限数字")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("JSON 含溢出非有限数字")
        return number
    value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs,
                       parse_constant=constant, parse_float=finite_float)
    _json_data(value)
    return value


def _local_path(path, field):
    value = os.fspath(path)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{field} 必须是本机文件路径")
    normalized = value.replace("\\", "/")
    if normalized.startswith("//") or PureWindowsPath(value).drive.startswith("\\\\"):
        raise ValueError(f"{field} 不允许 UNC/网络或设备命名空间路径")
    if os.name != "nt" and PureWindowsPath(value).drive:
        raise ValueError(f"{field} 不允许当前平台上的外来驱动器路径")
    for part in PureWindowsPath(value).parts:
        name = part.rstrip(" .").split(".", 1)[0].split(":", 1)[0].upper()
        if name in {"CON", "PRN", "AUX", "NUL", "CLOCK$"} or re.fullmatch(r"(?:COM|LPT)[1-9]", name):
            raise ValueError(f"{field} 不允许设备别名")
    return Path(value)


def read_scope_input(path):
    """Read an explicitly supplied candidate JSON, never configuration or secrets."""
    local = Path(os.path.abspath(_local_path(path, "input")))
    data = _safe_bytes(local.parent, local.name, 1024**2)
    try:
        return _decode(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError(f"候选 JSON 无效: {exc}") from exc


def _safe_bytes(root, rel, limit):
    """Open a fixed file without reading through symlink/reparse races.

    POSIX walks directories by pinned descriptors with O_NOFOLLOW. Windows pins
    the opened file and verifies its final kernel path before reading any bytes.
    Unknown platforms fail closed; no device/network API or writes are involved.
    """
    path = root / rel
    fd = None
    directories = []
    try:
        if os.name == "posix" and os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            directory = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            directories.append(directory)
            for part in (*root.parts[1:], *rel.split("/")[:-1]):
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                directories.append(directory)
            fd = os.open(rel.split("/")[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        elif os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            fd = os.open(path, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
            final_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
            final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            final_path.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            size = final_path(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
            if not size or size >= len(buffer):
                raise ValueError("不能核实已打开证据文件的最终路径")
            actual = buffer.value
            if actual.startswith("\\\\?\\UNC\\"):
                actual = "\\\\" + actual[8:]
            elif actual.startswith("\\\\?\\"):
                actual = actual[4:]
            if os.path.normcase(actual) != os.path.normcase(str(path)):
                raise ValueError("证据句柄最终路径不匹配，拒绝符号链接/重解析逃逸")
        else:
            raise ValueError("当前平台缺少安全离线证据读取机制")
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
            raise ValueError("证据必须是普通、非重解析文件")
        if before.st_size > limit:
            raise ValueError("证据文件超过离线诊断大小上限")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
        if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError("读取期间证据文件变化或超出大小上限")
        return data
    finally:
        if fd is not None:
            os.close(fd)
        for directory in reversed(directories):
            os.close(directory)


def _is_link(path):
    # Windows junctions/reparse points are not always reported as is_symlink().
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _read(root, rel, files, result):
    """Fixed caller-constructed relative names only; reject links and special files.

    Paths below the selected directory are never discovered from JSON. Hash the
    same byte snapshot parsed below. This is not a concurrent-writer attestation.
    """
    path = root
    try:
        for part in rel.split("/"):
            path = path / part
            if _is_link(path):
                raise ValueError("不允许证据路径中的符号链接")
            if not path.resolve().is_relative_to(root):
                raise ValueError("证据路径逃逸显式目录")
        data = _safe_bytes(root, rel, LIMITS[path.name])
        files[rel] = {"status": "read", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        return data
    except FileNotFoundError:
        files[rel] = {"status": "missing"}
    except (OSError, ValueError) as exc:
        files[rel] = {"status": "rejected"}
        _issue(result, "evidence_file_rejected", rel, str(exc), error=True)
    return None


def _json_file(root, rel, files, result):
    data = _read(root, rel, files, result)
    if data is None:
        return None
    try:
        value = _decode(data)
        if not isinstance(value, dict):
            raise ValueError("JSON 顶层必须是对象")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        _issue(result, "invalid_json", rel, str(exc), error=True)
        return None


def _pointer(result, record, expected, field):
    value = record.get("path")
    if value is None:
        if record.get("status") == "ok":
            _issue(result, "missing_evidence_pointer", field, "采集成功记录缺少文件引用", missing=True)
        return
    # Exact portable name also rejects traversal, cross-obs, drive/UNC, absolute,
    # alternate separators and normalization tricks without following them.
    if not isinstance(value, str) or value != expected:
        _issue(result, "evidence_path_conflict", field, "引用必须精确指向本次 obs 的固定相对文件名", error=True)


def _compare(result, field, actual, expected, *, required=True):
    if actual is None:
        if required:
            _issue(result, "missing_identity", field, "身份字段未记录，不能独立核对", missing=True)
    elif actual != expected:
        _issue(result, "identity_conflict", field, "历史证据身份字段不一致", error=True)


def _device_identity(result, spec, obs, field):
    if spec is None:
        _issue(result, "missing_device_spec", field, "缺少有效设备描述", missing=True)
        return
    if not isinstance(spec, str) or ":" not in spec:
        _issue(result, "invalid_device_spec", field, "设备描述格式无效", error=True)
        return
    kind, serial = spec.split(":", 1)
    if not serial or kind not in {"hdc", "replay"}:
        _issue(result, "invalid_device_spec", field, "设备描述来源或身份不受支持", error=True)
        return
    _compare(result, field + ".source", kind, obs.get("source"))
    # Replay serials are generated from the world filename, not an OS-local path.
    identity = serial if kind == "hdc" else "replay-" + PureWindowsPath(serial).stem
    _compare(result, field + ".serial", identity, obs.get("device"))


def _identity(app, root, obs, run, headers, result):
    if run is None and not headers:
        _issue(result, "identity_missing", "run.json/events.jsonl", "缺少 run.json 或 session_open，不能绑定 APP/设备身份", missing=True)
    if run is not None:
        _compare(result, "run.run_id", run.get("run_id"), root.name)
        meta, device = _record(run.get("app")), _record(run.get("device"))
        for key, expected in (("id", app.id), ("bundle", app.bundle)):
            _compare(result, "run.app." + key, meta.get(key), expected)
        _compare(result, "run.device.serial", device.get("serial"), obs.get("device"))
        _compare(result, "run.device.kind", device.get("kind"), obs.get("source"))
        _device_identity(result, run.get("device_spec"), obs, "run.device_spec")
        _compare(result, "run.validation_level", run.get("validation_level"),
                 "offline_replay" if obs.get("source") == "replay" else "real_device")
        for key in ("run_id", "session", "session_id"):
            if key in obs:
                _compare(result, "observation." + key, obs[key], root.name)
        result["historical_environment"] = {"source": "run.json", "run_id": run.get("run_id"),
            "started": run.get("started"), "finished": run.get("finished"), "app_version": meta.get("version"),
            "validation_level": run.get("validation_level"), "device": deepcopy(device),
            "current_applicability": "not_checked"}
    declared_spec = run.get("device_spec") if run else None
    declared_version = _record(run.get("app")).get("version") if run else None
    if run is not None:
        # The current producer repeats measured identity/version in these fields.
        # Check known declarations for contradictions, without demanding old
        # evidence formats supply absent optional metadata or business facts.
        for field in ("facts", "oracle_facts", "preflight"):
            extra = _record(run.get(field))
            if extra.get("app") is not None:
                _compare(result, "run." + field + ".app", extra["app"], app.id)
            version = extra.get("app_version")
            if version is not None and declared_version is not None:
                _compare(result, "run." + field + ".app_version", version, declared_version)
            elif declared_version is None:
                declared_version = version
        for key, expected in (("serial", obs.get("device")), ("kind", obs.get("source"))):
            extra_device = _record(_record(run.get("preflight")).get("device"))
            if key in extra_device:
                _compare(result, "run.preflight.device." + key, extra_device[key], expected)
    for index, header in enumerate(headers):
        prefix = f"session_open[{index}]"
        identity = _record(header.get("identity"))
        this_spec = identity.get("device")
        if declared_spec is not None and this_spec is not None:
            _compare(result, prefix + ".device_declared_spec", this_spec, declared_spec)
        elif declared_spec is None:
            declared_spec = this_spec
        for key, expected in (("app", app.id), ("bundle", app.bundle)):
            _compare(result, prefix + "." + key, identity.get(key), expected)
        _device_identity(result, identity.get("device"), obs, prefix + ".device")
        _compare(result, prefix + ".validation_level", identity.get("validation_level"),
                 "offline_replay" if obs.get("source") == "replay" else "real_device")
        for key in ("run_id", "session", "session_id"):
            for record, field in ((header, prefix), (identity, prefix + ".identity"), (obs, "observation")):
                if key in record:
                    _compare(result, field + "." + key, record[key], root.name)
        facts = _record(header.get("facts"))
        this_version = facts.get("app_version")
        if declared_version is not None and this_version is not None:
            _compare(result, prefix + ".app_version", this_version, declared_version)
        elif declared_version is None:
            declared_version = this_version
        for key in ("app", "bundle", "device", "validation_level"):
            if key in facts:
                _compare(result, prefix + ".facts." + key, facts[key], identity.get(key))
        if run is None and index == 0:
            result["historical_environment"] = {"source": "events.jsonl:session_open", "session": root.name,
                "session_id_source": "explicit_evidence_directory", "ts": header.get("ts"),
                "app_version": facts.get("app_version"), "validation_level": identity.get("validation_level"),
                "device": identity.get("device"), "current_applicability": "not_checked"}
    if run is not None or headers:
        observed_version = _record(obs.get("scope")).get("app_version")
        version = declared_version
        if observed_version is not None and version is not None:
            _compare(result, "scope.app_version", observed_version, version)


def _png_valid(data):
    """Verify container/CRCs and decode these exact bytes, without opening a path."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset, chunks, dimensions = 8, [], None
    while offset < len(data):
        if len(data) - offset < 12:
            return False
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(data):
            return False
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", data[offset + 8 + size:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            return False
        if not chunks:
            if kind != b"IHDR" or size != 13:
                return False
            dimensions = struct.unpack(">II", payload[:8])
            if min(dimensions) < 1 or max(dimensions) > 32768 or dimensions[0] * dimensions[1] > 32 * 1024**2:
                return False
        elif kind == b"IHDR":
            return False
        chunks.append(kind)
        offset = end
        if kind == b"IEND":
            if size or offset != len(data):
                return False
            break
    if not chunks or chunks[-1] != b"IEND" or b"IDAT" not in chunks:
        return False
    import cv2
    import numpy as np
    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    return decoded is not None and tuple(reversed(decoded.shape[:2])) == dimensions


def inspect_saved_scope(app, spec, evidence_dir, obs_id):
    """Inspect fixed saved files without config, device, governance or any writes."""
    validate_inspection_spec(app, spec)  # Reject before file access.
    if not isinstance(obs_id, str) or not OBS_ID.fullmatch(obs_id):
        raise ValueError("obs_id 必须是 obs- 加4至12位数字，不能是路径")
    result = _base(app, spec)
    files = {}
    result["evidence_files"] = files
    root = _local_path(evidence_dir, "evidence_dir").absolute()
    # Inspect each component before resolution, including Windows junctions.
    # Never intentionally resolve a reparse point into a remote/local escape.
    current = Path(root.anchor)
    try:
        for part in root.parts[1:]:
            current = current / part
            if _is_link(current):
                raise ValueError("显式证据目录及祖先不能是符号链接/重解析点")
        root = root.resolve()
    except (OSError, ValueError) as exc:
        _issue(result, "evidence_directory_rejected", "evidence_dir", str(exc), error=True)
        return result
    if not root.is_dir():
        _issue(result, "evidence_directory_missing", "evidence_dir", "显式证据目录不存在", missing=True)
        return result
    prefix = f"obs/{obs_id}/"
    obs = _json_file(root, prefix + "observation.json", files, result)
    from .outcomes import observation_tree_relative
    try:
        tree_rel = observation_tree_relative(obs or {}, obs_id)
    except ValueError as exc:
        tree_rel = None
        _issue(result, "evidence_path_conflict", "tree.path", str(exc), error=True)
    tree = _json_file(root, tree_rel, files, result) if tree_rel else None
    screen = _read(root, prefix + "screen.png", files, result)
    run = _json_file(root, "run.json", files, result)
    events = _read(root, "events.jsonl", files, result)
    headers = []
    if events is not None:
        try:
            for line in events.splitlines():
                if not line.strip():
                    continue
                event = _decode(line)
                if not isinstance(event, dict):
                    raise ValueError("事件必须是对象")
                if event.get("event") == "session_open":
                    headers.append(event)
        except (ValueError, UnicodeError, RecursionError) as exc:
            _issue(result, "invalid_events", "events.jsonl", str(exc), error=True)
    if obs is None:
        _issue(result, "observation_missing", prefix + "observation.json", "缺少可解析观测，不能绑定证据", missing=True)
        return result
    structural = inspect_scope(app, spec, obs, tree)
    for key, value in structural.items():
        if key not in {"diagnostic_status", "inspection_errors", "issues"}:
            result[key] = value
    for row in structural["issues"]:
        _issue(result, **{k: row[k] for k in ("code", "field", "message")},
               error=row in structural["inspection_errors"], missing=structural["diagnostic_status"] == "incomplete")
    _compare(result, "observation.obs_id", obs.get("obs_id"), obs_id)
    _identity(app, root, obs, run, headers, result)
    for key, rel in (("tree", tree_rel), ("screenshot", prefix + "screen.png")):
        record = _record(obs.get(key))
        _pointer(result, record, rel, key + ".path")
        claimed = record.get("sha256")
        actual = files.get(rel, {}).get("sha256")
        if claimed is not None and (not isinstance(claimed, str) or not re.fullmatch(r"(?:[0-9a-f]{16}|[0-9a-f]{64})", claimed)
                                    or actual is not None and not actual.startswith(claimed)):
            _issue(result, "recorded_hash_conflict", key + ".sha256", "记录哈希与本次文件字节不一致或格式无效", error=True)
    if screen is None:
        result["visual_evidence"]["status"] = "missing"
        _issue(result, "visual_evidence_missing", prefix + "screen.png", "缺少截图：仅能诊断 UI 树，视觉证据不完整", missing=True)
    else:
        result["visual_evidence"]["status"] = "present_unreviewed"
        if not _png_valid(screen):
            result["visual_evidence"]["status"] = "invalid"
            _issue(result, "invalid_screenshot", prefix + "screen.png", "PNG 结构、校验和或像素解码无效", error=True)
        if _record(obs.get("screenshot")).get("status") != "ok":
            _issue(result, "screenshot_status_conflict", "screenshot.status", "存在截图字节但采集记录未声明成功", error=True)
    if result["inspection_errors"] or result["diagnostic_status"] == "incomplete":
        result["shape"]["evidence_usable"] = False
    else:
        result["shape"]["evidence_usable"] = True
    return result
