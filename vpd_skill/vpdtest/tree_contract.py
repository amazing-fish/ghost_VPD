"""Recognize a producer document wrapper, never normalize unknown widget flags.

Contract source: OpenHarmony testfwk_arkxtest 7.0, commit
85f4f79861c42e91d7eb8f83b707743f7b968bbf, DumpUiHierarchy / UiAttr.
This recognizes a serialization profile, not the installed binary's version.
"""
import re

from .util import sha256_obj

PROFILE = "openharmony-uitest-aggregate-26"
PRODUCER = "hdc-uitest-dumpLayout-bundle"
AGGREGATE_FIELDS = frozenset((
    "accessibilityId", "id", "text", "key", "description", "type", "bounds", "enabled", "focused",
    "selected", "clickable", "longClickable", "scrollable", "checkable", "checked", "hostWindowId",
    "displayId", "origBounds", "zIndex", "opacity", "backgroundColor", "backgroundImage", "blur",
    "hitTestBehavior", "clip", "originalText",
))
_RECT = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def _schema_reason(raw, bundle):
    if not isinstance(bundle, str) or not bundle:
        return "bundle_missing"
    if not isinstance(raw, dict) or set(raw) != {"attributes", "children"}:
        return "document_shape_mismatch"
    attrs = raw.get("attributes")
    if not isinstance(attrs, dict) or set(attrs) != AGGREGATE_FIELDS:
        return "aggregate_fields_mismatch"
    if any(attrs[key] != "" for key in AGGREGATE_FIELDS - {"bounds"}):
        return "aggregate_nonempty_attribute"
    rect = _RECT.fullmatch(attrs["bounds"]) if isinstance(attrs["bounds"], str) else None
    if not rect:
        return "aggregate_bounds_invalid"
    left, top, right, bottom = map(int, rect.groups())
    # The producer initializes window-union bounds at zero. Child widget bounds
    # are not the window bounds, so do not invent a child-bounding-box equality.
    if left > 0 or top > 0 or right <= left or bottom <= top or right < 0 or bottom < 0:
        return "aggregate_bounds_invalid"
    children = raw["children"]
    if not isinstance(children, list) or not children:
        return "window_children_missing"
    for child in children:
        if (not isinstance(child, dict) or set(child) != {"attributes", "children"}
                or not isinstance(child["children"], list) or not isinstance(child["attributes"], dict)):
            return "window_shape_mismatch"
        window = child["attributes"]
        if window.get("bundleName") != bundle:
            return "window_bundle_mismatch"
        if (not isinstance(window.get("hierarchy"), str) or not window["hierarchy"]
                or not isinstance(window.get("type"), str) or not window["type"]
                or not (AGGREGATE_FIELDS | {"visible", "hashcode", "hint", "abilityName", "pagePath"}) <= set(window)):
            return "window_widget_contract_missing"
    return None


def bundle_dump_origin(raw, bundle):
    """Record our command/source and candidate schema; this is NOT authorization.

    No deployed vendor build has been bound to the audited upstream serializer.
    A shape match cannot prove the root lacks input ownership on an unknown build.
    """
    reason = _schema_reason(raw, bundle)
    return {"producer": PRODUCER, "profile": PROFILE, "bundle": bundle,
            "status": "unverified", "schema_status": "matched" if reason is None else "mismatch",
            "reason": reason or "producer_build_unverified", "producer_build": "unknown",
            "may_exclude_root": False, "raw_sha256": sha256_obj(raw)}


def document_root_evidence(raw, origin):
    """Diagnostic only. Never turns unknown root flags into false or grants a bypass."""
    if (not isinstance(origin, dict) or origin.get("producer") != PRODUCER
            or origin.get("profile") != PROFILE):
        return {"status": "unverified", "reason": "producer_not_verified", "may_exclude_root": False}
    reason = _schema_reason(raw, origin.get("bundle"))
    schema_status = "matched" if reason is None else "mismatch"
    if origin.get("raw_sha256") != sha256_obj(raw):
        reason = "raw_fingerprint_mismatch"
    return {"status": "unverified", "schema_status": schema_status,
            "reason": reason or "producer_build_unverified", "producer_build": "unknown",
            "may_exclude_root": False, "producer": PRODUCER, "profile": PROFILE,
            "raw_sha256": sha256_obj(raw)}
