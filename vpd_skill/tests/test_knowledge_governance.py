"""知识治理（Issue #9）：冲突裁决的废弃范围按适用范围（构建/版本）限定；入库数据不得出现
完整车架号或未脱敏手机号（不可逆的尾位标识允许）。隐私测试是防回潮的闸——新增的知识、
用例、参考文档带敏感明文会直接红。"""
import json
import re
import pytest
from pathlib import Path

from vpdtest.knowledge import KnowledgeStore

SKILL = Path(__file__).resolve().parent.parent


def _item(kid, subject, expected, scope=None):
    return {"id": kid, "kind": "product_rule", "subject": subject, "statement": f"判据 {kid}",
            "expected": expected, "applies_to": scope or {}, "source": {"type": "reviewer", "ref": "pytest"}}


def test_resolve_deprecates_only_overlapping_scopes(ws):
    # 同一主题：保留条目覆盖 3.0.3/3.0.4，旧规定范围完全在其内 → 当条，废弃；
    # 3.0.5 是另一条独立规定，与保留条目范围不相交：不是当条，跨范围一并废弃会误杀。
    kn = KnowledgeStore(ws.root)
    for it in [
        _item("K-V-003", "app.x.rule", {"v": "old"}, {"app_version": ["3.0.3", "3.0.4"]}),
        _item("K-V-004", "app.x.rule", {"v": "new"}, {"app_version": ["3.0.3", "3.0.4"]}),
        _item("K-V-005", "app.x.rule", {"v": "other"}, {"app_version": "3.0.5"}),
    ]:
        kn.add(it)
    kept = kn.resolve("app.x.rule", keep="K-V-004", reviewer="r", reason="以新交互为准")
    assert kept["id"] == "K-V-004"
    assert kn.get("K-V-003")["status"] == "deprecated"      # 范围完全在保留条目内 → 冲突当条，废弃
    assert kn.get("K-V-005")["status"] == "unconfirmed"     # 范围不相交：不是当条，保持有效
    assert kept["history"][-1]["deprecated"] == ["K-V-003"]
    # 与 conflicts() 同源：裁决后该主题不再有冲突，3.0.5 在自己的版本上照常可引用
    # （种子数据里其他主题的既有冲突不受影响，留给人工裁决）
    assert "app.x.rule" not in kn.conflicts()
    st = kn.oracle_status(["K-V-005"], facts={"app_version": "3.0.5", "app": "motor_hm"})
    assert st["status"] == "unconfirmed"


def test_resolve_refuses_partial_overlap_deprecation(ws):
    # 部分重叠：当条还覆盖保留条目之外的范围（同一规则同时规定了 3.0.3，裁决却只保留 3.0.4
    # 的新规定；未限定范围的当条同理，"未限定"意味着处处适用）。整条废弃会误杀未被本次裁决
    # 覆盖的范围 → 拒绝，先拆分/调整 applies_to 再裁决。拒绝是原子操作：不废弃任何条目。
    kn = KnowledgeStore(ws.root)
    for it in [
        _item("K-V-003", "app.w.rule", {"v": "old"}, {"app_version": ["3.0.3", "3.0.4"]}),
        _item("K-V-004", "app.w.rule", {"v": "new"}, {"app_version": "3.0.4"}),
        _item("K-V-ANY", "app.w.rule", {"v": "any"}),  # 未限定范围：同样超出保留条目的范围
    ]:
        kn.add(it)
    try:
        kn.resolve("app.w.rule", keep="K-V-004", reviewer="r", reason="3.0.4 以新交互为准")
        raised = None
    except ValueError as e:
        raised = str(e)
    assert raised and "部分重叠" in raised and "K-V-003" in raised
    # 原子：没有任何条目被改状态
    assert kn.get("K-V-003")["status"] == "unconfirmed"
    assert kn.get("K-V-004")["status"] == "unconfirmed"
    assert kn.get("K-V-ANY")["status"] == "unconfirmed"
    # 冲突保持，等人工拆分后重新裁决
    assert "app.w.rule" in kn.conflicts()


# ---- Issue #9：observation 来源的 product_rule 在 search/verdict 两端的 Oracle 资格一致 ----

def _obs_rule(kid, subject, expected):
    return {"id": kid, "kind": "product_rule", "subject": subject, "statement": f"观察规则 {kid}",
            "expected": expected, "applies_to": {},
            "source": {"type": "observation", "ref": "run-20261008-201507-4990"}}


def test_search_and_verdict_agree_on_observation_sourced_rules(ws):
    """观察来源的 product_rule：未确认时两端一致地"不可作判据"；凭依据确认后两端一致地承认
    Oracle 资格。此前 search 端把它永久按现场观察排除，verdict 端却承认确认后的资格——两端漂移。"""
    from vpdtest.search import item_role, search as vpd_search
    kn = KnowledgeStore(ws.root)
    kn.add(_obs_rule("K-OBS-R1", "app.z.entry", {"ok": True}))
    # 未确认：search 按现场观察呈现（不能当产品要求）；verdict 判 unconfirmed（待确认）
    role, note = item_role(kn.get("K-OBS-R1"))
    assert role == "observation" and "确认（需依据）前" in note
    assert kn.oracle_status(["K-OBS-R1"], facts={})["status"] == "unconfirmed"
    row = next(r for g in vpd_search(ws, "app.z.entry")["results"].values() for r in g if r["ref"] == "K-OBS-R1")
    assert row["role"] == "observation" and row["can_be_oracle"] is False
    # 凭依据确认（observation 来源强制要求 --basis）：两端一致承认
    kn.confirm("K-OBS-R1", reviewer="r", basis="run-20261008-201507-4990 的 obs-0003，三个样本一致")
    role2, note2 = item_role(kn.get("K-OBS-R1"))
    assert role2 == "requirement" and "已凭依据确认" in note2
    assert kn.oracle_status(["K-OBS-R1"], facts={})["status"] == "confirmed"
    row2 = next(r for g in vpd_search(ws, "app.z.entry")["results"].values() for r in g if r["ref"] == "K-OBS-R1")
    assert row2["role"] == "requirement" and row2["can_be_oracle"] is True


def test_resolve_without_scope_deprecates_all_others(ws):
    # 保留条目未限定范围时，同主题其余有效条目全部是当事方（原语义）
    kn = KnowledgeStore(ws.root)
    for it in [_item("K-A-001", "app.y.rule", {"v": 1}), _item("K-A-002", "app.y.rule", {"v": 2})]:
        kn.add(it)
    kn.resolve("app.y.rule", keep="K-A-002", reviewer="r", reason="以新为准")
    assert kn.get("K-A-001")["status"] == "deprecated"
    assert kn.get("K-A-002")["status"] == "unconfirmed"


# ---- 隐私治理：入库数据与参考文档的敏感明文防回潮 ----
VIN_RE = re.compile(r"(?<![A-Za-z0-9.])[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9.])")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _data_files():
    for sub in ("data", "references"):
        for p in (SKILL / sub).rglob("*"):
            if p.suffix in (".json", ".md") and p.is_file():
                yield p


def test_no_full_vin_in_committed_data():
    hits = []
    for p in _data_files():
        for m in VIN_RE.finditer(p.read_text("utf-8")):
            tok = m.group(0)
            # 车架号特征：17 位、不含 I/O/Q、字母数字混合；纯数字时间戳/群号不算
            if any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok):
                hits.append(f"{p.relative_to(SKILL)}:{tok}")
    assert not hits, f"发现疑似完整车架号，须脱敏为尾位（不可逆标识允许）：{hits}"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: scan raw text instead")
        result[key] = value
    return result


def _phone_hits(text, suffix):
    """Exclude only schema-1 engine-manifest SHA-256 values, not arbitrary hex.

    Keys, notes, other fields, malformed JSON and duplicate keys remain scanned.
    """
    if suffix == ".json":
        try:
            data = json.loads(text, object_pairs_hook=_unique_object)
            if isinstance(data, dict):
                records = [data.get("asset_review")]
                if isinstance(data.get("history"), list):
                    records.extend(data["history"])
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    snap = record.get("engine_snapshot")
                    if not isinstance(snap, dict) or type(snap.get("schema")) is not int or snap["schema"] != 1:
                        continue
                    files = snap.get("files")
                    if not isinstance(files, dict):
                        continue
                    for path, digest in list(files.items()):
                        if (re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py", path)
                                and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
                            files[path] = "<sha256>"
                text = json.dumps(data, ensure_ascii=False)
        except (ValueError, TypeError):
            pass  # Fall back to raw scan; never hide data behind invalid JSON.
    return [m.group(0) for m in PHONE_RE.finditer(text)]


def test_no_raw_phone_number_in_committed_data():
    hits = []
    for p in _data_files():
        hits.extend(f"{p.relative_to(SKILL)}:{phone}" for phone in _phone_hits(p.read_text("utf-8"), p.suffix))
    assert not hits, f"发现未脱敏手机号（151****3183 形式允许）：{hits}"


PHONE = "152" + "64986892"  # Synthetic phone-shaped substring in the observed tree.py digest.
DIGEST = "6da" + PHONE + "f2cb680bafeb035b8c27f2278a9c52e56a455a8f195f093078"


def _manifest(digest=DIGEST, path="tree.py", schema=1):
    return {"engine_snapshot": {"schema": schema, "files": {path: digest}}}


@pytest.mark.parametrize("location", ["asset_review", "history"])
def test_phone_scanner_accepts_only_manifest_sha256(location):
    record = _manifest()
    data = {location: [record] if location == "history" else record}
    assert _phone_hits(json.dumps(data), ".json") == []


@pytest.mark.parametrize("data", [
    {"note": DIGEST},
    {"engine_snapshot": _manifest()["engine_snapshot"]},
    {"asset_review": _manifest(PHONE)},
    {"asset_review": _manifest(DIGEST + "a")},
    {"asset_review": _manifest(DIGEST[:-1])},
    {"asset_review": _manifest(DIGEST.replace("f", "z", 1))},
    {"asset_review": _manifest(schema=True)},
    {"asset_review": _manifest(schema=2)},
    {"asset_review": _manifest(path="../tree.py")},
    {"asset_review": _manifest(path="note.txt")},
    {"asset_review": _manifest(path=PHONE + ".py")},
    {"asset_review": {**_manifest(), "note": PHONE}},
    {"asset_review": _manifest(), "phone": PHONE},
    {"asset_review": {"engine_snapshot": {"schema": 1, "files": {"tree.py": DIGEST}, "note": PHONE}}},
])
def test_phone_scanner_keeps_real_or_ambiguous_sensitive_values(data):
    assert PHONE in _phone_hits(json.dumps(data), ".json")


@pytest.mark.parametrize("suffix,text", [
    (".md", DIGEST),
    (".md", "contact: " + PHONE),
    (".json", '{"contact": "' + PHONE + '", "contact": "masked"}'),
    (".json", '{"contact": "' + PHONE + '"'),
    (".json", json.dumps({"contact": "a" + PHONE + "b"})),
])
def test_phone_scanner_never_exempts_free_text_or_invalid_json(suffix, text):
    assert PHONE in _phone_hits(text, suffix)


def test_phone_scanner_allows_existing_masked_phone():
    assert _phone_hits("151****3183", ".md") == []
