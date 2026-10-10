"""知识治理（Issue #9）：冲突裁决的废弃范围按适用范围（构建/版本）限定；入库数据不得出现
完整车架号或未脱敏手机号（不可逆的尾位标识允许）。隐私测试是防回潮的闸——新增的知识、
用例、参考文档带敏感明文会直接红。"""
import re
from pathlib import Path

from vpdtest.knowledge import KnowledgeStore

SKILL = Path(__file__).resolve().parent.parent


def _item(kid, subject, expected, scope=None):
    return {"id": kid, "kind": "product_rule", "subject": subject, "statement": f"判据 {kid}",
            "expected": expected, "applies_to": scope or {}, "source": {"type": "reviewer", "ref": "pytest"}}


def test_resolve_deprecates_only_overlapping_scopes(ws):
    # 同一主题：旧规定横跨 3.0.3/3.0.4（列表范围），新规定只改 3.0.4 → 两者有交集、构成冲突；
    # 3.0.5 是另一条独立规定，与本次裁决无关，跨范围一并废弃会误杀。
    kn = KnowledgeStore(ws.root)
    for it in [
        _item("K-V-003", "app.x.rule", {"v": "old"}, {"app_version": ["3.0.3", "3.0.4"]}),
        _item("K-V-004", "app.x.rule", {"v": "new"}, {"app_version": "3.0.4"}),
        _item("K-V-005", "app.x.rule", {"v": "other"}, {"app_version": "3.0.5"}),
        _item("K-V-ANY", "app.x.rule", {"v": "any"}),  # 未限定版本：与谁都可能冲突
    ]:
        kn.add(it)
    kept = kn.resolve("app.x.rule", keep="K-V-004", reviewer="r", reason="3.0.4 以新交互为准")
    assert kept["id"] == "K-V-004"
    assert kn.get("K-V-003")["status"] == "deprecated"      # 与 3.0.4 有交集 → 冲突当条，废弃
    assert kn.get("K-V-ANY")["status"] == "deprecated"      # 未限定范围 → 也是当条
    assert kn.get("K-V-005")["status"] == "unconfirmed"     # 版本不相交：不是当条，保持有效
    assert kept["history"][-1]["deprecated"] == ["K-V-003", "K-V-ANY"]
    # 与 conflicts() 同源：裁决后该主题不再有冲突，3.0.5 在自己的版本上照常可引用
    # （种子数据里其他主题的既有冲突不受影响，留给人工裁决）
    assert "app.x.rule" not in kn.conflicts()
    st = kn.oracle_status(["K-V-005"], facts={"app_version": "3.0.5", "app": "motor_hm"})
    assert st["status"] == "unconfirmed"


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


def test_no_raw_phone_number_in_committed_data():
    hits = []
    for p in _data_files():
        for m in PHONE_RE.finditer(p.read_text("utf-8")):
            hits.append(f"{p.relative_to(SKILL)}:{m.group(0)}")
    assert not hits, f"发现未脱敏手机号（151****3183 形式允许）：{hits}"
