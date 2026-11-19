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


def _other_possible_title(tree, results, title, name):
    others = [n for n in tree.nodes if name in (n.text, n.description) and tree.visible_lineage(n)
              and tree.is_within(n, results) and n.idx != title.idx]
    if not others:
        return False
    # Both entity and generic POI cards in the exported trees have a title in
    # their first Column. Only their known metadata/route slots can be excluded.
    cards = ("//Row[count(Column[1]/__Common__[1]/Text)=1]"
             "[(count(*)=3 and *[1][self::Image] and *[2][self::Column] and *[3][self::Column])"
             " or (count(*)=2 and *[1][self::Column] and *[2][self::Stack])]")
    slots = ("/Column[1]/Row/Text", "/Column[1]/Row/Row/Text",
             "/Column[2][Stack and count(Text)=1]/Text[@text='路线']",
             "/Stack/Column[Stack and count(Text)=1]/Text[@text='路线']")
    known = {n.idx for n in tree.xpath(" | ".join(cards + slot + "[not(*)]" for slot in slots))}
    return any(n.idx not in known or n.text != name for n in others)


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
    # The registered selector binds a real title to the actual query. Require its
    # own exact geometry; a clipped optional field must not invalidate this title.
    texts = [n for n in tree.nodes if n.text.strip() and tree.is_within(n, node)]
    titles = [n for n in tree.xpath("//Row/Column[1]/__Common__[1]/Text")
              if tree.is_within(n, node) and tree.visible_lineage(n)]
    if (len(titles) != 1 or tree.ancestors(titles[0])[2].idx != node.idx
            or titles[0].text != name or not _fully_inside(tree, titles[0], node)):
        return {**result, "reason": "row_title_unresolved"}
    if _other_possible_title(tree, lists[0], titles[0], name):
        return {**result, "reason": "another_result_may_have_this_name"}
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
