"""Inspect a saved QK-230 search response without evaluating an asset or product rule."""
import argparse
import json
from pathlib import Path

from .outcomes import collection_error, load_observation
from .verdict import _check
from .workspace import Workspace


def _fully_inside(tree, node, row):
    if not node.bounds or not tree.visible_lineage(node) or tree.visible_rect(node) != node.bounds:
        return False
    x1, y1, x2, y2 = node.bounds
    left, top, right, bottom = row.bounds
    return left <= x1 < x2 <= right and top <= y1 < y2 <= bottom


def inspect_name(app, obs_id, obs, tree, name):
    """The supplied name is a comparison value. Its origin is not authenticated here."""
    result = {"obs_id": obs_id, "source": obs.get("source"), "row_observation": "blocked",
              "favorite_identity": "undetermined", "number_requirement": "pending",
              "product_verdict": "not_evaluated"}
    page, layout = obs.get("page") or {}, obs.get("layout") or {}
    if (not name.strip() or obs.get("obs_id") != obs_id or obs.get("fresh") is not True
            or obs.get("source") not in ("hdc", "replay") or obs.get("process_alive") is not True
            or obs.get("foreground") != app.bundle or obs.get("foreground_after") != app.bundle
            or (obs.get("tree") or {}).get("status") != "ok" or tree is None or tree.status != "ok"
            or collection_error(obs) or obs.get("foreground_error") or obs.get("foreground_query_errors")):
        return {**result, "reason": "incomplete_or_inconsistent_observation"}

    if (type(layout.get("pane_count")) is not int or layout["pane_count"] != 1
            or len(layout.get("panes") or []) > 1):
        return {**result, "reason": "single_pane_not_established"}
    identified = app.states.identify(tree, app.bundle, True, layout)
    if any(p.get("state") != "qk_spot_search" or p.get("dialogs") or p.get("interrupts")
           for p in (page, identified)):
        return {**result, "reason": "search_page_not_established"}

    for role, target in (("query", "qk_search_input"), ("row", "qk_search_name_result_row")):
        # Keep the runner's unique, fully visible scope checks; fields can be clipped independently.
        holds, evidence = _check({"type": "text_present", "text": name, "exact": True,
                                  "within": target, "strict_visible_text": role == "query"}, obs, tree, app)
        if holds is not True:
            return {**result, "reason": f"{role}_unresolved", "detail": evidence}
        node = tree.nodes[evidence["within"]["node"]["idx"]]
        if role == "query" and node.text != name:
            return {**result, "reason": "query_value_mismatch"}
        result[role + "_node"] = node.brief()
    # A differently shaped POI row can be outside the registered entity-row XPath.
    # Count it even if clipped/offscreen. A second possible identity blocks this report.
    lists = [n for n in tree.nodes if n.id == "search_list" and tree.visible_lineage(n)]
    if len(lists) != 1 or not tree.is_within(node, lists[0]):
        return {**result, "reason": "result_list_unresolved"}
    if any(name in (n.text, n.description) and tree.visible_lineage(n)
           and tree.is_within(n, lists[0]) and not tree.is_within(n, node) for n in tree.nodes):
        return {**result, "reason": "another_result_may_have_this_name"}
    # The registered selector binds a real title to the actual query. Require its
    # own exact geometry; a clipped optional field must not invalidate this title.
    texts = [n for n in tree.nodes if n.text.strip() and tree.is_within(n, node)]
    titles = [n for n in texts if n.type == "Text" and n.text == name and tree.visible_lineage(n)]
    if len(titles) != 1 or not _fully_inside(tree, titles[0], node):
        return {**result, "reason": "row_title_unresolved"}
    visible = [n.text for n in texts if _fully_inside(tree, n, node)]
    result["row_texts"] = visible
    result["omitted_text_nodes"] = len(texts) - len(visible)
    result["row_observation"] = "observed"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True, help="one existing run/session directory")
    parser.add_argument("--obs-id", required=True)
    parser.add_argument("--name", required=True, help="exact current name obtained outside search")
    args = parser.parse_args()
    app = Workspace(load_local_config=False).app("qiankun_hm")
    try:
        obs, tree = load_observation(args.evidence_dir, args.obs_id)
        result = inspect_name(app, args.obs_id, obs, tree, args.name)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError) as exc:
        parser.exit(2, f"Cannot inspect observation: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["row_observation"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
