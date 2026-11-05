"""Read-only inventory and the existing promotion gates; no new maturity or coverage rules."""
from collections import Counter
import re

from . import cases as C
from .knowledge import KnowledgeStore, item_status


SCOPE = {"missing_sources": "only_explicitly_requested_ids", "runtime_applicability": "not_checked",
         "evidence_packages": "not_checked", "coverage": "not_assessed"}


def _review(info):
    binding = (info.get("record") or {}).get("binding")
    current = info["current"]
    available = isinstance(binding, dict)
    return {k: info[k] for k in ("required", "valid", "state", "origin_signals")} | {
        "binding_available": available,
        "changed_binding_fields": sorted(k for k, value in current.items()
                                         if available and binding.get(k) != value),
        "unexpected_binding_field_count": len(set(binding) - set(current)) if available else 0,
        "current": current}


def _one(ws, cid, knowledge, policy, to, reviewer, allow_offline, known_issue):
    path = ws.case_path(cid)
    row = {"case": cid, "source": "present" if path.is_file() else "missing", "status": None}
    if row["source"] == "missing":
        return row
    stage = "source"
    try:
        case = ws.load_case(cid)
        if not isinstance(case, dict) or not isinstance(case.get("app"), str):
            raise ValueError("资产须为对象并声明字符串 app")
        status = case.get("status", "draft")
        row.update(app=case["app"], status=status if isinstance(status, str) else None,
                   scope=dict(SCOPE))
        stage = "app"
        app = ws.app(case["app"])
        stage = "review"
        review = C.review_status(ws, case, knowledge, app=app, policy=policy)
        row["review"] = _review(review)
        stage = "definition"
        row["issues"] = C.lint(case, app, knowledge, policy, runs_dir=ws.runs_dir)
        stage = "replay"
        basis = C.replay_basis(case, review["current"]["dependency_hash"])
        row["replay"] = {k: basis[k] for k in
                         ("ok", "why", "hash", "count", "replays", "ignored", "coverage", "product", "signature")
                         if k in basis}
        product = case.get("product_status")
        row["recorded_product_status"] = ({k: product[k] for k in ("status", "issue", "signature", "since_hash")
                                           if k in product} if isinstance(product, dict) else None)
        row["recorded_validation_levels"] = dict(sorted(Counter(
            r.get("validation_level") if isinstance(r.get("validation_level"), str) else "unspecified"
            for r in case.get("replays", [])).items()))
        stage = "references"
        refs, _, _ = C._condition_references(case, app, knowledge)
        row["oracle_references"] = [
            {"id": kid, "stored_status": item_status(item) if item is not None else "missing",
             "kind": item.get("kind") if item is not None else None}
            for kid in sorted(refs) for item in [knowledge.get(kid)]]
        stage = "promotion"
        target = to if to is not None else C.NEXT.get(row["status"])
        if to is None and row["status"] == "regression":
            row["promotion"] = {"to": None, "status": "no_next_stage"}
        else:
            try:
                preview = C.promote(ws, cid, target, knowledge, reviewer, allow_offline,
                                    known_issue, dry_run=True)
                row["promotion"] = {"to": target, "status": "eligible", "preview": preview}
            except PermissionError as exc:
                row["promotion"] = {"to": target, "status": "blocked", "reason": str(exc)}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        # Invalid/unavailable source data is visible; unexpected implementation errors still propagate.
        row["error"] = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
    return row


def readiness(ws, case_ids=None, *, to=None, reviewer=None, allow_offline=False, known_issue=None):
    """Directory enumeration cannot discover unrequested missing assets or certify field evidence."""
    directory = ws.root / "cases"
    explicit = bool(case_ids)
    ids = list(dict.fromkeys(case_ids)) if explicit else sorted(p.stem for p in directory.glob("*.json"))
    if any(not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", cid)
           or cid in (".", "..") for cid in ids):
        raise ValueError("用例 ID 不能是路径，须为字母/数字开头的稳定 ID")
    knowledge, policy = KnowledgeStore(ws.root), ws.policy()
    rows = [_one(ws, cid, knowledge, policy, to, reviewer, allow_offline, known_issue) for cid in ids]
    source = Counter(r["source"] for r in rows)
    errors = sum("error" in r for r in rows)
    summary = {"requested": len(ids), "source": dict(sorted(source.items())), "analysis_errors": errors,
               "maturity": dict(sorted(Counter(r["status"] for r in rows if isinstance(r["status"], str)).items())),
               "review": dict(sorted(Counter(r["review"]["state"] for r in rows if "review" in r).items())),
               "promotion": dict(sorted(Counter(r["promotion"]["status"] for r in rows if "promotion" in r).items()))}
    return {"schema": 1, "scope": {**SCOPE, "selection": "explicit_ids" if explicit else "directory",
                                   "source_directory_exists": directory.is_dir()},
            "promotion_options": {"to": to, "reviewer_supplied": bool(reviewer),
                                  "allow_offline": allow_offline, "known_issue_supplied": bool(known_issue)},
            "incomplete": bool(errors or source["missing"] or not directory.is_dir()),
            "summary": summary, "results": rows}
