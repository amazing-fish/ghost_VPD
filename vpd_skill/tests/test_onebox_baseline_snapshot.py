"""The imported baseline is traceable source data, never automatic acceptance."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

BASELINES = Path(__file__).resolve().parents[2] / "docs" / "baselines"
SNAPSHOT = json.loads((BASELINES / "onebox-9month-20261026.json").read_text(encoding="utf-8"))
MAPPING = json.loads((BASELINES / "onebox-9month-20261026-mapping.json").read_text(encoding="utf-8"))


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def raw_row(row):
    comment = next(c for c in SNAPSHOT["comments"] if c["url"] == row["source_comment"])
    return "".join(comment["body"].splitlines(keepends=True)[row["source_line_start"] - 1:row["source_line_end"]])


def test_all_original_comments_and_row_spans_are_bound_to_the_preserved_text():
    assert SNAPSHOT["source"]["body_sha256"] == digest(SNAPSHOT["source"]["body"])
    assert len(SNAPSHOT["comments"]) == 9
    assert len({c["url"] for c in SNAPSHOT["comments"]}) == 9
    assert len({c["id"] for c in SNAPSHOT["comments"]}) == 9
    for comment in SNAPSHOT["comments"]:
        assert comment["url"].startswith("https://github.com/amazing-fish/ghost_VPD/issues/102#issuecomment-")
        assert comment["body_sha256"] == digest(comment["body"])
    for row in SNAPSHOT["rows"]:
        raw = raw_row(row)
        assert row["text_sha256"] == digest(raw)
        header = re.match(r"\*\*([A-Z]+-\d+)\s+([^*]+)\*\*（(L\d)）", raw)
        assert header.groups() == (row["id"], row["name"], row["level"])


def test_151_unique_ids_and_144_names_match_each_reported_group_and_level():
    rows = SNAPSHOT["rows"]
    counts = {"UI": 43, "RT": 33, "CC": 21, "CP": 17, "OP": 13, "PW": 9, "LK": 9, "DM": 6}
    expected = {f"{prefix}-{n:02d}" for prefix, count in counts.items() for n in range(1, count + 1)}
    assert len(rows) == 151 and {r["id"] for r in rows} == expected
    assert len({r["name"] for r in rows}) == 144
    assert Counter(r["level"] for r in rows) == {"L0": 60, "L1": 40, "L2": 35, "L3": 16}
    assert Counter(r["group"] for r in rows) == SNAPSHOT["summary"]["by_group"]
    assert Counter(r["name"] for r in rows)["用例遍历"] == 8


def test_source_gaps_and_suspended_rows_are_preserved_instead_of_filled_in():
    rows = {r["id"]: raw_row(r) for r in SNAPSHOT["rows"]}
    assert [rid for rid, raw in rows.items() if "- 预期结果：" not in raw] == ["RT-21"]
    assert all("暂停执行" in rows[rid] for rid in ["RT-24", "RT-25", "RT-26"])
    assert "第四点没了" in rows["UI-18"]
    assert all("标签暂不支持同步" in rows[rid] for rid in ["DM-01", "DM-03"])
    assert "人工结构化转写" in SNAPSHOT["comments"][-1]["body"]
    assert "本地" in SNAPSHOT["comments"][-1]["body"]


def test_mapping_includes_every_base_asset_without_claiming_unreviewed_coverage():
    inventory = MAPPING["asset_inventory"]
    assert inventory["all_case_count"] == len(inventory["cases"]) == 41
    qk = {c["id"] for c in inventory["cases"] if c["id"].startswith("TC-QK-")}
    assert len(qk) == inventory["qiankun_case_count"] == 32
    assert inventory["qiankun_status_counts"] == {"draft": 28, "exploration_record": 4}
    mapped = {a["case_id"] for a in MAPPING["mapped_assets"]}
    unmapped = set(MAPPING["unmapped_qiankun_case_ids"])
    assert mapped == {f"TC-QK-{n}" for n in range(240, 250)}
    assert not mapped & unmapped and mapped | unmapped == qk
    assert len(MAPPING["out_of_app_case_ids"]) == 9
    row_ids = {r["id"] for r in SNAPSHOT["rows"]}
    for asset in MAPPING["mapped_assets"]:
        assert asset["new_automated_case_count"] == 0
        assert asset["asset_source_commit"] == MAPPING["asset_source_commit"]
        assert all(r["baseline_row"] in row_ids and r["formal_full_row_accepted"] is False
                   for r in asset["relationships"])
    assert MAPPING["formal_full_rows_accepted_by_this_package"] == 0
    assert MAPPING["new_automated_cases_added_by_this_package"] == 0


def test_city_query_is_not_equated_with_same_named_parking_query():
    assets = {a["case_id"]: a for a in MAPPING["mapped_assets"]}
    city_search = next(r for r in assets["TC-QK-245"]["relationships"] if r["baseline_row"] == "OP-04")
    park_search = assets["TC-QK-246"]["relationships"]
    assert city_search["relationship"] == "partial_candidate"
    assert len(park_search) == 1 and park_search[0]["baseline_row"] == "OP-04"
    assert park_search[0]["relationship"] == "not_equivalent"
    assert "搜索城市信息" in raw_row(next(r for r in SNAPSHOT["rows"] if r["id"] == "OP-04"))


def test_tag_chain_pending_delivery_is_not_original_entry_or_full_cross_device_editing():
    current = next(a for a in MAPPING["mapped_assets"] if a["case_id"] == "TC-QK-242")
    assert current["case_version_at_base"] == 4
    assert [r["baseline_row"] for r in current["relationships"]] == ["UI-34"]
    pending = MAPPING["pending_delivery_at_base"]
    assert pending["version"] == 6 and pending["code_status"] == "not_in_main_at_asset_source_commit"
    links = {r["baseline_row"]: r for r in pending["relationships_after_delivery_candidate"]}
    assert links["RT-04"]["relationship"] == "partial_candidate"
    assert links["UI-34"]["relationship"] == "partial_candidate"
    assert links["DM-02"]["relationship"] == "not_equivalent"
    assert all(r["formal_full_row_accepted"] is False for r in links.values())
