"""经验与 RSI v0.1：候选记录 → 重放/回归验证 → 采纳（版本化 overlay）→ 可回滚。

- 经验可以自动积累为 candidate；只有 AUTO_ADOPTABLE 类型在验证通过后可自动采纳。
- 产品断言、关键步骤、安全边界不在经验可改范围：case 变更走 cases.promote（需评审），
  policy.json 不会被任何经验读取或修改。
- 验证必须覆盖产生该经验之外的用例，且与基线对比无回退，避免只在单一样例上"变好"。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .util import append_jsonl, new_id, now_iso, read_json, read_jsonl, write_json

AUTO_ADOPTABLE = {"locator_preference", "wait_timeout"}
REVIEW_REQUIRED = {"interrupt_rule", "state_signature", "skill_note"}
FORBIDDEN = {"assertion_change", "step_removal", "policy_change"}


class Overlay:
    def __init__(self, items: list, version: int = 0, trial: Optional[dict] = None):
        self.version, self.trial = version, (trial or {}).get("id")
        self.locator, self.waits = {}, {}
        for it in items + ([trial] if trial else []):
            ch = it["change"]
            if it["kind"] == "locator_preference":
                # 按窗口形态分组：直板窄屏上学到的偏好不带到折叠屏展开态（旧条目无 layout_key 时对所有形态生效）
                self.locator[_pref_key(ch["target"], ch.get("layout_key"))] = ch["prefer_strategy_index"]
            elif it["kind"] == "wait_timeout":
                self.waits[ch["key"]] = float(ch["timeout_s"])

    def locator_pref(self, target_id: str, layout_key: Optional[str] = None) -> Optional[int]:
        if layout_key and _pref_key(target_id, layout_key) in self.locator:
            return self.locator[_pref_key(target_id, layout_key)]
        return self.locator.get(target_id)

    def wait_for(self, key: str, default: float, cap: float) -> float:
        return min(self.waits.get(key, default), cap)

    def describe(self) -> dict:
        return {"version": self.version, "trial": self.trial, "locator": self.locator, "waits": self.waits}


class ExperienceStore:
    def __init__(self, root: Path, app_id: str):
        self.dir = root / "experience" / app_id
        self.items_path, self.active_path = self.dir / "items.json", self.dir / "active.json"
        self.ledger_path = self.dir / "ledger.jsonl"

    def items(self) -> list:
        return read_json(self.items_path, {"items": []})["items"]

    def get(self, xid: str) -> dict:
        for it in self.items():
            if it["id"] == xid:
                return it
        raise ValueError(f"没有经验 {xid}")

    def save_item(self, item: dict) -> None:
        items = [it for it in self.items() if it["id"] != item["id"]] + [item]
        write_json(self.items_path, {"items": sorted(items, key=lambda i: i["id"])})

    def active(self) -> dict:
        return read_json(self.active_path, {"version": 0, "items": []})

    def overlay(self, trial: Optional[str] = None, baseline_only: bool = False) -> Overlay:
        act = self.active()
        items = [self.get(x) for x in act["items"]]
        return Overlay(items, act["version"], None if baseline_only or not trial else self.get(trial))

    def log(self, action: str, **data) -> None:
        append_jsonl(self.ledger_path, {"ts": now_iso(), "action": action, **data})

    def ledger(self) -> list:
        return read_jsonl(self.ledger_path)

    # ---- 候选 ----
    def propose_from_run(self, run: dict) -> list:
        created = []
        for step in run.get("steps", []):
            for att in step.get("attempts", []):
                loc = att.get("locate") or {}
                if loc.get("status") == "found" and loc.get("adapted") and loc.get("strategy_index") is not None:
                    change = {"target": loc["target"], "prefer_strategy_index": loc["strategy_index"],
                              "strategy_kind": loc["strategy_kind"]}
                    if loc.get("layout_key"):
                        change["layout_key"] = loc["layout_key"]
                    created.append(self._upsert("locator_preference", change,
                        f"目标 {loc['target']} 的首选策略失败，第 {loc['strategy_index']} 号策略（{loc['strategy_kind']}）成功"
                        + (f"（窗口形态 {loc['layout_key']}）" if loc.get("layout_key") else ""),
                        run, step, att))
                post = att.get("post") or {}
                if post.get("met") and post.get("timeout_s") and post.get("elapsed_s", 0) > 0.7 * post["timeout_s"]:
                    key = f"{run['case']['id']}:{step['step_id']}"
                    created.append(self._upsert("wait_timeout", {"key": key, "timeout_s": round(post["elapsed_s"] * 1.5, 1)},
                                                f"{key} 的后置条件在超时窗口后段才出现", run, step, att))
        return [c for c in created if c]

    def _upsert(self, kind: str, change: dict, desc: str, run: dict, step: dict, att: dict) -> Optional[dict]:
        ev = {"run_id": run["run_id"], "case": run["case"]["id"], "step": step["step_id"],
              "attempt": att["n"], "obs": att.get("obs_before") or att.get("obs_after")}
        for it in self.items():
            if it["kind"] == kind and it["change"] == change and it["status"] in ("candidate", "validated", "adopted"):
                it["evidence"].append(ev)
                self.save_item(it)
                return None
        item = {"id": new_id("XP"), "kind": kind, "change": change, "description": desc, "status": "candidate",
                "requires_review": kind not in AUTO_ADOPTABLE, "created": now_iso(), "created_by": "auto:run",
                "origin_case": run["case"]["id"], "evidence": [ev], "validations": [], "history": []}
        self.save_item(item)
        self.log("candidate_created", id=item["id"], kind=kind, change=change, evidence=ev)
        return item

    # ---- 验证 / 采纳 / 回滚 ----
    def validate(self, xid: str, case_ids: list, devices: list, run_case: Callable) -> dict:
        """run_case(case_id, device_spec, overlay) -> run dict。基线 = 当前已采纳 overlay；试验 = 基线 + 该候选。"""
        from .verdict import RANK
        item = self.get(xid)
        target, lkey = item["change"].get("target"), item["change"].get("layout_key")
        rows, regress, improved, exercised = [], False, False, set()
        for dev in devices:
            for cid in case_ids:
                base = run_case(cid, dev, self.overlay(baseline_only=True))
                trial = run_case(cid, dev, self.overlay(trial=xid))
                b_rank, t_rank = RANK[base["verdict"]["verdict"]], RANK[trial["verdict"]["verdict"]]
                b_ad, t_ad = _adaptations(base), _adaptations(trial)
                if target and _touches(trial, target, lkey):
                    exercised.add(cid)
                row = {"case": cid, "device": dev, "baseline": base["verdict"]["verdict"], "trial": trial["verdict"]["verdict"],
                       "baseline_run": base["run_id"], "trial_run": trial["run_id"],
                       "baseline_adaptations": b_ad, "trial_adaptations": t_ad}
                if t_rank < b_rank:
                    regress, row["regression"] = True, True
                if t_rank > b_rank or t_ad < b_ad:
                    improved = True
                rows.append(row)
        others = exercised - {item["origin_case"]}
        if regress:
            status, reason = "rejected", "试验组相对基线出现回退"
        elif target and not others:
            status, reason = "candidate", "除来源用例外没有其他用例覆盖该目标，覆盖不足，暂不晋级"
        elif not improved:
            status, reason = "candidate", "与基线相比没有可测量的改进"
        else:
            status, reason = "validated", "无回退，且在来源用例之外得到验证"
        result = {"ts": now_iso(), "status": status, "reason": reason, "cases": case_ids, "devices": devices,
                  "exercised_cases": sorted(exercised), "rows": rows}
        item["validations"].append(result)
        item["history"].append({"ts": now_iso(), "from": item["status"], "to": status, "why": reason})
        item["status"] = status
        self.save_item(item)
        self.log("validated", id=xid, status=status, reason=reason, rows=len(rows))
        return result

    def adopt(self, xid: str, reviewer: Optional[str] = None) -> dict:
        item = self.get(xid)
        if item["kind"] in FORBIDDEN:
            raise PermissionError(f"{item['kind']} 不能通过经验通道变更，请走用例评审")
        if item["status"] != "validated":
            raise PermissionError(f"经验 {xid} 状态为 {item['status']}，只有 validated 才能采纳")
        if item["requires_review"] and not reviewer:
            raise PermissionError(f"{item['kind']} 需要人工评审：请提供 --reviewer")
        act = self.active()
        write_json(self.dir / "snapshots" / f"active_v{act['version']}.json", act)
        act = {"version": act["version"] + 1, "items": act["items"] + [xid], "updated": now_iso()}
        write_json(self.active_path, act)
        item["history"].append({"ts": now_iso(), "from": item["status"], "to": "adopted",
                                "why": f"reviewer={reviewer or 'auto'}", "overlay_version": act["version"]})
        item["status"] = "adopted"
        self.save_item(item)
        self.log("adopted", id=xid, overlay_version=act["version"], reviewer=reviewer or "auto")
        return act

    def rollback(self, to_version: int, reason: str) -> dict:
        snap = self.dir / "snapshots" / f"active_v{to_version}.json"
        if not snap.exists():
            raise ValueError(f"没有 overlay v{to_version} 的快照")
        cur = self.active()
        old = read_json(snap)
        write_json(self.dir / "snapshots" / f"active_v{cur['version']}.json", cur)
        new = {"version": cur["version"] + 1, "items": old["items"], "updated": now_iso(),
               "rolled_back_to": to_version}
        write_json(self.active_path, new)
        for xid in set(cur["items"]) - set(old["items"]):
            it = self.get(xid)
            it["history"].append({"ts": now_iso(), "from": it["status"], "to": "rolled_back", "why": reason})
            it["status"] = "rolled_back"
            self.save_item(it)
        self.log("rolled_back", to_version=to_version, new_version=new["version"], reason=reason)
        return new


def _adaptations(run: dict) -> int:
    return sum(1 for s in run.get("steps", []) for a in s.get("attempts", []) if (a.get("locate") or {}).get("adapted"))


def _touches(run: dict, target: str, layout_key: Optional[str] = None) -> bool:
    """该运行是否真正用到了这条偏好：同一目标，且（偏好限定形态时）在同一窗口形态下定位。"""
    return any((a.get("locate") or {}).get("target") == target
               and (layout_key is None or (a.get("locate") or {}).get("layout_key") == layout_key)
               for s in run.get("steps", []) for a in s.get("attempts", []))


def _pref_key(target: str, layout_key: Optional[str]) -> str:
    return f"{target}@{layout_key}" if layout_key else target
