"""用例生命周期：exploration_record → candidate → replay_verified → regression。

- 探索路径转候选时必须补齐：目的、前置条件、测试数据、步骤、独立判定依据、清理、来源证据。
- 判定依据必须是独立的知识条目；不能用"探索时看到了什么"来证明"应该是什么"。
- replay_verified 需要当前内容版本（content_hash）下至少 2 次来自不同运行的通过重放；
  regression 还需评审人、全部判据已确认，默认要求至少 1 次真机重放。
"""
from __future__ import annotations

from typing import Optional

from .util import now_iso, read_json, sha256_obj, write_json

REQUIRED = ("purpose", "preconditions", "test_data", "steps", "assertions", "cleanup", "sources")
CONTENT_KEYS = ("app", "preconditions", "env_requirements", "test_data", "steps", "assertions", "cleanup")
NEXT = {"exploration_record": "candidate", "draft": "candidate", "candidate": "replay_verified",
        "replay_verified": "regression"}


def content_hash(case: dict) -> str:
    return sha256_obj({k: case.get(k) for k in CONTENT_KEYS})[:12]


def lint(case: dict, app, knowledge) -> list:
    issues = []
    for k in REQUIRED:
        v = case.get(k)
        if v is None or v == [] or v == "" or (k == "test_data" and v == {} and not case.get("test_data_none")):
            issues.append({"field": k, "problem": "缺失或为空" + ("（无测试数据请写 test_data_none: true）" if k == "test_data" else "")})
    for s in case.get("steps", []):
        if s["action"] == "tap":
            t = s.get("target")
            if isinstance(t, dict):
                issues.append({"field": f"steps.{s['id']}", "problem": "使用临时内联目标，需评审后登记到 targets.json"})
            elif t not in app.targets:
                issues.append({"field": f"steps.{s['id']}", "problem": f"目标 {t} 未登记"})
        if s.get("expect_state_unresolved"):
            issues.append({"field": f"steps.{s['id']}", "problem": "终点是未声明状态，缺少可判定的后置条件"})
        if s["action"] == "ensure_state" and s.get("state") not in app.states.recipes:
            issues.append({"field": f"steps.{s['id']}", "problem": f"没有到达 {s.get('state')} 的 recipe"})
    real = [a for a in case.get("assertions", []) if not a.get("advisory")]
    if not real and case.get("assertions"):
        issues.append({"field": "assertions", "problem": "没有非建议类断言"})
    for a in real:
        if not a.get("oracle_refs"):
            issues.append({"field": f"assertions.{a.get('id')}", "problem": "缺少 oracle_refs（需求/已确认用例/产品规则）"})
        for r in a.get("oracle_refs", []):
            it = knowledge.get(r)
            if it is None:
                issues.append({"field": f"assertions.{a.get('id')}", "problem": f"判据 {r} 不存在"})
            elif it["source"].get("type") == "observation" and it["source"].get("run") in {
                    s.get("run") for s in case.get("sources", [])}:
                issues.append({"field": f"assertions.{a.get('id')}", "problem": f"判据 {r} 来自本用例的探索观察，不独立"})
    return issues


def from_exploration(exp: dict, transitions: list, case_id: str, app_id: str) -> dict:
    by_id = {t["id"]: t for t in exp["transitions"]}
    chain = [by_id[t] for t in transitions]
    for a, b in zip(chain, chain[1:]):
        if a["to"] != b["from"]:
            raise ValueError(f"迁移 {a['id']} 的终点 {a['to']} 与 {b['id']} 的起点 {b['from']} 不连续")
    steps = [{"id": "S1", "action": "ensure_state", "state": chain[0]["from"]}]
    for i, t in enumerate(chain, start=2):
        st = {"id": f"S{i}", "action": "tap", "target": t["target"], "timeout_s": 8, "retry": 1, "critical": True,
              "oracle_refs": [], "from_transition": t["id"]}
        if t.get("to_declared"):
            st["expect_state"] = t["to"]
        else:
            st["expect_state_unresolved"] = t["to"]
        steps.append(st)
    return {"id": case_id, "version": 1, "status": "exploration_record", "app": app_id,
            "title": f"[探索生成] {' → '.join([chain[0]['from']] + [t['to'] for t in chain])}",
            "purpose": "", "preconditions": [f"APP 处于 {chain[0]['from']}"], "env_requirements": {},
            "test_data": {}, "steps": steps, "assertions": [], "cleanup": [],
            "sources": [{"type": "exploration", "run": exp["run_id"], "transitions": transitions,
                         "obs": [o for t in chain for o in (t["obs_before"], t["obs_after"])]}],
            "history": [{"ts": now_iso(), "to": "exploration_record", "why": "由探索迁移生成，待补齐目的/判据/清理"}]}


def record_replay(ws, case_id: str, run: dict) -> dict:
    case = ws.load_case(case_id)
    case.setdefault("replays", []).append({
        "run_id": run["run_id"], "verdict": run["verdict"]["verdict"], "hash": run["case"]["hash"],
        "validation_level": run["validation_level"], "device": run["device_spec"], "ts": now_iso()})
    write_json(ws.case_path(case_id), case)
    return case


def promote(ws, case_id: str, to: str, knowledge, reviewer: Optional[str] = None, allow_offline: bool = False) -> dict:
    case, app = ws.load_case(case_id), ws.app(ws.load_case(case_id)["app"])
    cur = case.get("status", "draft")
    if NEXT.get(cur) != to:
        raise PermissionError(f"不能从 {cur} 晋级到 {to}（允许：{cur} → {NEXT.get(cur)}）")
    issues = lint(case, app, knowledge)
    if issues:
        raise PermissionError(f"用例不完整，不能晋级：{issues}")
    h = content_hash(case)
    good = [r for r in case.get("replays", []) if r["hash"] == h and r["verdict"] == "passed"]
    if to == "replay_verified" and len({r["run_id"] for r in good}) < 2:
        raise PermissionError(f"当前内容版本 {h} 下通过的重放不足 2 次（现有 {len(good)}）")
    if to == "regression":
        if not reviewer:
            raise PermissionError("晋级正式回归用例需要评审人 --reviewer")
        refs = [r for a in case["assertions"] if not a.get("advisory") for r in a["oracle_refs"]]
        st = knowledge.oracle_status(refs)
        if st["status"] != "confirmed":
            raise PermissionError(f"判据未全部确认：{st['reason']}")
        if not allow_offline and not any(r["validation_level"] == "real_device" for r in good):
            raise PermissionError("没有真机通过重放；如确需仅凭离线回放晋级，请显式 --allow-offline 并在评审中说明")
    case["history"] = case.get("history", []) + [{"ts": now_iso(), "from": cur, "to": to, "by": reviewer,
                                                   "hash": h, "replays": [r["run_id"] for r in good]}]
    case["status"] = to
    write_json(ws.case_path(case_id), case)
    return case


def load_exploration(ws, run_id: str) -> dict:
    return read_json(ws.runs_dir / run_id / "exploration.json")
