"""Published October source is traceable; a crosswalk never grants acceptance."""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

BASELINES = Path(__file__).resolve().parents[2] / "docs" / "baselines"
CURRENT_PATH = BASELINES / "onebox-10month-20261027.json"
PREVIOUS_PATH = BASELINES / "onebox-9month-20261026.json"
CURRENT = json.loads(CURRENT_PATH.read_text(encoding="utf-8"))
PREVIOUS = json.loads(PREVIOUS_PATH.read_text(encoding="utf-8"))
DELTA = json.loads((BASELINES / "onebox-10month-20261027-delta.json").read_text(encoding="utf-8"))


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def raw_row(snapshot, row):
    comment = next(c for c in snapshot["comments"] if c["url"] == row["source_comment"])
    return "".join(comment["body"].splitlines(keepends=True)[row["source_line_start"] - 1:row["source_line_end"]])


def fields(raw):
    return {m[1]: m[2] for line in raw.splitlines() if (m := re.match(r"^- ([^：]+)：(.*)", line))}


def keyed_rows(snapshot):
    return {(r["group"], r["name"], fields(raw_row(snapshot, r))["场景"]): r for r in snapshot["rows"]}


def test_complete_published_text_and_each_indexed_span_remain_traceable():
    assert CURRENT["baseline_id"] == "onebox-10month-20261027"
    assert CURRENT["source"]["issue_number"] == 106
    assert CURRENT["source"]["body_sha256"] == digest(CURRENT["source"]["body"])
    comments = CURRENT["comments"]
    assert [c["id"] for c in comments] == [
        6431475347, 6431475672, 6431475989, 6431476317,
        6431476640, 6431476932, 6431477203, 6431477521,
    ]
    assert len({c["node_id"] for c in comments}) == 8
    for comment in comments:
        assert comment["url"] == f"https://github.com/amazing-fish/ghost_VPD/issues/106#issuecomment-{comment['id']}"
        assert comment["author"] == "amazing-fish"
        assert comment["created_at"] == comment["updated_at"]
        assert comment["body_sha256"] == digest(comment["body"])
    headers = [(c["url"], i + 1) for c in comments for i, line in enumerate(c["body"].splitlines())
               if re.match(r"^\*\*OCT-[A-Z]{2}-\d{2}\s", line)]
    assert {(r["source_comment"], r["source_line_start"]) for r in CURRENT["rows"]} == set(headers)
    assert len(headers) == 150
    for row in CURRENT["rows"]:
        raw = raw_row(CURRENT, row)
        assert row["text_sha256"] == digest(raw)
        header = re.match(r"\*\*(OCT-[A-Z]{2}-\d{2})\s+([^*]+)\*\*（(L[0-3])）", raw)
        assert header.groups() == (row["id"], row["name"], row["level"])


def test_october_counts_and_literal_automation_marks_do_not_become_coverage():
    rows = CURRENT["rows"]
    groups = {"UI": 41, "RT": 34, "CC": 21, "CP": 17, "OP": 13, "PW": 9, "LK": 9, "DM": 6}
    assert {r["id"] for r in rows} == {f"OCT-{group}-{n:02d}" for group, count in groups.items()
                                        for n in range(1, count + 1)}
    assert len(rows) == len({r["id"] for r in rows}) == 150
    assert len({r["name"] for r in rows}) == 143
    assert Counter(r["name"] for r in rows)["用例遍历"] == 8
    assert Counter(r["level"] for r in rows) == {"L0": 59, "L1": 42, "L2": 37, "L3": 12}
    assert Counter(r["group"] for r in rows) == CURRENT["summary"]["by_group"]
    marks = Counter()
    for row in rows:
        original = fields(raw_row(CURRENT, row))
        assert ("automation_mark" in row) == ("自动化" in original)
        if "自动化" in original:
            assert row["automation_mark"] == original["自动化"]
            marks[row["automation_mark"]] += 1
    assert marks == {"√": 20, "×": 19, "-": 24}
    assert sum(marks.values()) == CURRENT["summary"]["automation_marks"]["published_nonempty_rows"] == 63
    assert sum("automation_mark" not in r for r in rows) == 87
    assert CURRENT["summary"]["automation_marks"]["not_published_rows"] == 87
    assert "platform_result_values_not_published" in {g["kind"] for g in CURRENT["source_gaps"]}


def test_crosswalk_binds_both_snapshots_and_all_149_distinct_row_identities():
    for key, path in (("previous_baseline", PREVIOUS_PATH), ("current_baseline", CURRENT_PATH)):
        assert DELTA[key]["snapshot"] == path.name
        assert DELTA[key]["snapshot_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    old, new = keyed_rows(PREVIOUS), keyed_rows(CURRENT)
    assert len(old) == 151 and len(new) == 150
    assert len(old.keys() & new.keys()) == 149
    matches = DELTA["matches"]
    assert len(matches) == len({m["previous_row"] for m in matches}) == len({m["current_row"] for m in matches}) == 149
    assert {(m["group"], m["name"], m["scenario"]) for m in matches} == old.keys() & new.keys()
    for match in matches:
        key = match["group"], match["name"], match["scenario"]
        assert (match["previous_row"], match["current_row"]) == (old[key]["id"], new[key]["id"])
        assert match["previous_text_sha256"] == old[key]["text_sha256"]
        assert match["current_text_sha256"] == new[key]["text_sha256"]
    assert DELTA["summary"] == {"common_rows": 149, "unchanged_rows": 144, "changed_rows": 5,
                                "added_rows": 1, "removed_rows": 2, "common_unique_names": 142}


def test_five_changes_and_added_removed_rows_are_derived_from_preserved_fields():
    compare = ["level", "场景", "预置条件", "测试步骤", "预期结果", "备注"]
    assert DELTA["comparison"]["fields"] == compare
    old = {r["id"]: r for r in PREVIOUS["rows"]}
    new = {r["id"]: r for r in CURRENT["rows"]}
    for match in DELTA["matches"]:
        a, b = old[match["previous_row"]], new[match["current_row"]]
        av = {"level": a["level"], **fields(raw_row(PREVIOUS, a))}
        bv = {"level": b["level"], **fields(raw_row(CURRENT, b))}
        assert match["changed_fields"] == {f: {"previous": av.get(f), "current": bv.get(f)}
                                            for f in compare if av.get(f) != bv.get(f)}
    changed = {m["current_row"] for m in DELTA["matches"] if m["changed_fields"]}
    assert changed == {"OCT-UI-37", "OCT-RT-18", "OCT-RT-25", "OCT-RT-26", "OCT-RT-27"}
    assert [r["current_row"] for r in DELTA["added"]] == ["OCT-RT-16"]
    assert {r["previous_row"] for r in DELTA["removed"]} == {"UI-38", "UI-39"}
    assert all(r["retirement_authorized"] is False for r in DELTA["removed"])
    # A new current row and retained old rows must each have a complete partition,
    # even when repeated names or shifted UI/RT numbering would hide an omission.
    assert {m["previous_row"] for m in DELTA["matches"]} | {r["previous_row"] for r in DELTA["removed"]} == set(old)
    assert {m["current_row"] for m in DELTA["matches"]} | {r["current_row"] for r in DELTA["added"]} == set(new)


def test_incomplete_rules_and_removed_pause_notes_are_not_silently_resolved():
    rows = {r["id"]: raw_row(CURRENT, r) for r in CURRENT["rows"]}
    assert [rid for rid, raw in rows.items() if "- 预期结果：" not in raw] == ["OCT-RT-22"]
    assert all("确认一下小字" in rows[rid] for rid in ("OCT-RT-33", "OCT-RT-34"))
    assert "第四点没了" in rows["OCT-UI-18"]
    assert all("标签暂不支持同步" in rows[rid] for rid in ("OCT-DM-01", "OCT-DM-03"))
    assert fields(rows["OCT-UI-37"])["预期结果"] == "按顺序显示收藏点和推荐点"
    notes = {item["kind"]: item for item in DELTA["pending_source_decisions"]}
    note = notes["pause_note_removed_from_source"]
    old = {r["id"]: raw_row(PREVIOUS, r) for r in PREVIOUS["rows"]}
    assert all("暂停执行" in old[rid] for rid in note["previous_rows"])
    assert all("暂停执行" not in rows[rid] for rid in note["current_rows"])
    assert set(notes) == {"pause_note_removed_from_source", "removed_rows_retirement_unconfirmed",
                          "list_order_contract_unconfirmed"}
    assert DELTA["formal_full_rows_accepted_by_this_package"] == 0
    assert DELTA["new_automated_cases_added_by_this_package"] == 0
    assert DELTA["runtime_consumption"].startswith("documentation_only")
