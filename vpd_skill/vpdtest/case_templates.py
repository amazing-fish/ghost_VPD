"""Freeze three proposed display templates into new, literal, unreviewed cases.

Public API: ``build_case(app, spec) -> dict`` is a pure preview. It never reads or
writes a case, creates a directory/lock, accesses a device, or approves anything.
The caller owns exclusive creation of a NEW case ID after validation.

Input schema (unknown keys are rejected)::

    {"template_id": "TC-QK-240", "new_case_id": "TC-QK-240-SITE-A",
     "scope_target": "registered_name_container",
     "scope_source": {"source": "device_observation", "ref": "evidence/name.json"},
     "expected_source": {"source": "test_data", "ref": "fixtures/site-a.json#name"},
     "expected_name": "独立数据中的园区名"}

241 has no expected value: it proposes the existing positive-integer display
rule, NOT agreement with a map total. 243 uses ``expected_floors: ["L1", "B1"]``
instead of expected_name; the list is a nonempty, unordered set of exact labels.
expected_source.source is requirement, test_data, or reviewer, never the tested
observation. All references are declarations for review, not externally verified
facts. They cannot use runtime/secret/read substitution or the scope evidence ref.

The current app.targets entry must have side_effect=read_only, forbid_tap=true,
no scroll/input/secret, exactly one static id or restricted structural XPath
strategy, and this registration (not supplied by spec)::

    "template_scope": {
        "role": "parking_name",  # parking_count or floor_labels for 241/243
        "shape": "text_container",
        "calibration": {"status": "verified", "source": "device_observation",
                        "ref": "evidence/name.json", "validation_level": "real_device"}}

Real registration also requires status=calibrated_on_device. Synthetic fixtures
require status=synthetic and source=synthetic_fixture; neither claims real-device
coverage. No production target is registered by this module. A missing registration
means calibration is still required. Structural XPath permits only element paths
and positive positional predicates, not text predicates, functions, or parameters.
This deliberately small selector subset may be extended after separate calibration.

Scope contract: a dedicated, fully exposed text container, not the page/root or
a mixed card. Name/count contain one text-bearing descendant; floor labels each
occur once, with no other nonempty text-bearing descendants. Existing text_present
within checks use strict_visible_text=true: only real @text labels can match,
all nonempty text nodes need exact full visibility and bounds contained by the
same scope (including nested clipping/hidden ancestors, without fraction rounding);
a generated static XPath detects extra/missing/duplicate labels without a new
verdict engine. This is a proposed exposed-UI-tree set check, not proof of the
complete map/backend set, hidden/virtualized items, or arbitrary visual rendering.
The old confirmed L1/B1 case and Oracle are neither copied nor widened.

All business steps are raw read / zero-duration wait with retry=0, no navigation,
launch, switch-car, input, or stop. Runner's existing preflight unlock and system
interrupt handling remain separate potential side effects; this is NOT a promise
that every real run performs no device action. Human review and fresh replay are
still required, and the three K-QK-TPL-* Oracles are proposals, not confirmations.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .util import sha256_obj

TEMPLATE_VERSION = 1
TEMPLATES = {
    "TC-QK-240": {"role": "parking_name", "title": "停车场名称显示", "oracle": "K-QK-TPL-240"},
    "TC-QK-241": {"role": "parking_count", "title": "车位数量正整数显示", "oracle": "K-QK-TPL-241"},
    "TC-QK-243": {"role": "floor_labels", "title": "楼层标签集合显示", "oracle": "K-QK-TPL-243"},
}
CASE_ID = re.compile(r"TC-[A-Z0-9]+(?:-[A-Z0-9]+)*\Z")
STRUCTURAL_XPATH = re.compile(r"(?:(?://?)(?:[A-Za-z_][A-Za-z0-9_.-]*|\*)(?:\[[1-9][0-9]*\])?)+\Z")
OBSERVATION_REF = re.compile(r"(?:\b(?:obs-\d+|run-\d|session-)|(?:^|/)runs/)", re.I)


def _text(value, field: str, limit: int = 512) -> str:
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > limit or any(ord(c) < 32 or ord(c) == 127
                                       or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in value)
            or "${" in value or "{run}" in value):
        raise ValueError(f"{field} 必须是非空、有限长度的字面字符串，不允许模板/控制字符")
    return value


def validate_case_id(value) -> str:
    """Portable new-ID syntax; no paths, dots, device names, or source overwrite."""
    value = _text(value, "new_case_id", 128)
    if not CASE_ID.fullmatch(value) or value in TEMPLATES:
        raise ValueError("new_case_id 须为新的规范 TC- 大写字母/数字/连字符 ID，不能覆盖来源用例")
    return value


def _source(value, field: str, *, expected=False) -> dict:
    if not isinstance(value, dict) or set(value) != {"source", "ref"}:
        raise ValueError(f"{field} 必须且只能包含 source/ref")
    result = {k: _text(value[k], f"{field}.{k}") for k in ("source", "ref")}
    if expected and (result["source"] not in {"requirement", "test_data", "reviewer"}
                     or OBSERVATION_REF.search(result["ref"])):
        raise ValueError("expected_source 必须是独立要求/测试数据/评审依据，不能引用被测观测")
    return result


def _literal(value: str) -> str:
    """XPath 1.0 string literal, including labels with both quote characters."""
    if "'" not in value:
        return "'" + value + "'"
    if '"' not in value:
        return '"' + value + '"'
    return "concat(" + ', "\'", '.join("'" + part + "'" for part in value.split("'")) + ")"


def _scope(app, spec, role):
    name = _text(spec.get("scope_target"), "scope_target", 128)
    target = app.targets.get(name)
    if not isinstance(target, dict):
        raise ValueError("scope_target 必须是当前 app.targets 已登记的目标；缺失时待校准")
    if (target.get("side_effect") != "read_only" or target.get("forbid_tap") is not True
            or any(target.get(k) for k in ("scroll", "allow_input", "secret"))):
        raise ValueError("scope_target 必须 read_only+forbid_tap，不允许滚动/输入/secret")
    registration = target.get("template_scope")
    if (not isinstance(registration, dict) or registration.get("role") != role
            or registration.get("shape") != "text_container"):
        raise ValueError("scope_target 缺少匹配角色的专用 text_container 登记；仍待校准")
    cal = registration.get("calibration")
    if not isinstance(cal, dict) or cal.get("status") != "verified":
        raise ValueError("scope_target 校准证据尚未核实；不能由本次输入自封已校准")
    calibration_source = _source({k: cal.get(k) for k in ("source", "ref")}, "scope calibration")
    level = cal.get("validation_level")
    if (level == "synthetic" and (target.get("status") != "synthetic"
                                 or cal.get("source") != "synthetic_fixture")):
        raise ValueError("synthetic 校准必须明确登记合成来源与 synthetic 状态")
    if (level == "real_device" and (target.get("status") != "calibrated_on_device"
                                   or cal.get("source") != "device_observation")):
        raise ValueError("real_device 需要已登记的设备校准及独立设备观测来源")
    if level not in {"synthetic", "real_device"}:
        raise ValueError("校准须明确 synthetic 或 real_device，不能省略证据级别")
    supplied = _source(spec.get("scope_source"), "scope_source")
    if supplied != calibration_source:
        raise ValueError("scope_source 与当前登记的校准来源不匹配")
    strategies = target.get("strategies")
    if not isinstance(strategies, list) or len(strategies) != 1 or not isinstance(strategies[0], dict):
        raise ValueError("scope_target 只支持一个静态树策略，不能回退到其他容器")
    strategy = strategies[0]
    if set(strategy) - {"kind", "value", "source"}:
        raise ValueError("scope 策略不能含 when/region/within 或其他动态选项")
    value = _text(strategy.get("value"), "scope strategy value", 2048)
    if strategy.get("kind") == "id":
        literal = _literal(value)
        xpath = f"//*[@id={literal} or @key={literal}]"
    elif strategy.get("kind") == "xpath" and STRUCTURAL_XPATH.fullmatch(value):
        xpath = value
    else:
        raise ValueError("scope 只支持静态 id 或无文本锚点的受限结构 XPath；不得用被测值定位")
    return name, target, {**calibration_source, "status": "verified", "validation_level": level}, supplied, xpath


def _target(xpath: str) -> dict:
    return {"label": "冻结显示断言（代码构造）", "side_effect": "read_only", "forbid_tap": True,
            "strategies": [{"kind": "xpath", "value": xpath}],
            "source": "case_templates v1: fixed structural check; values are escaped XPath literals"}


def build_case(app, spec: dict) -> dict:
    """Validate independent inputs and return an entirely new draft, without I/O."""
    if not isinstance(spec, dict):
        raise ValueError("模板输入须为 JSON 对象")
    allowed = {"template_id", "new_case_id", "scope_target", "scope_source", "expected_source",
               "expected_name", "expected_floors"}
    if set(spec) - allowed:
        raise ValueError(f"模板输入含未支持字段：{sorted(set(spec) - allowed)}")
    template_id = _text(spec.get("template_id"), "template_id")
    if template_id not in TEMPLATES:
        raise ValueError("未知模板：只支持 TC-QK-240/241/243")
    if app.id != "qiankun_hm":
        raise ValueError("这些显示母版只属于 qiankun_hm")
    template = TEMPLATES[template_id]
    case_id = validate_case_id(spec.get("new_case_id"))
    expected_source = _source(spec.get("expected_source"), "expected_source", expected=True)
    scope, target, cal, scope_source, xpath = _scope(app, spec, template["role"])
    if expected_source["ref"] == scope_source["ref"]:
        raise ValueError("独立预期不能引用同一被测/定位观测来源")
    values = {}
    if template_id == "TC-QK-240":
        values["expected_name"] = _text(spec.get("expected_name"), "expected_name", 256)
        if "expected_floors" in spec:
            raise ValueError("240 只接收 expected_name")
    elif template_id == "TC-QK-243":
        floors = spec.get("expected_floors")
        if not isinstance(floors, list) or not 1 <= len(floors) <= 32:
            raise ValueError("expected_floors 须为1至32个独立预期标签；空集合不能自动改为NA")
        floors = [_text(value, "expected_floors label", 64) for value in floors]
        if len(set(floors)) != len(floors) or "expected_name" in spec:
            raise ValueError("243 只接收无重复的 expected_floors")
        values["expected_floors"] = sorted(floors)  # Set semantics, canonical freezing.
    elif "expected_name" in spec or "expected_floors" in spec:
        raise ValueError("241 只核正整数显示，不接受名称/楼层或地图总数预期")

    oracle = template["oracle"]
    assertions = []
    def assertion(aid, typ, **kwargs):
        if typ in ("text_present", "text_present_any"):
            kwargs["strict_visible_text"] = True
        assertions.append({"id": aid, "at": "S_DISPLAY", "type": typ, "oracle_refs": [oracle], **kwargs})

    # Independent scope establishes the read location even when the displayed value
    # is wrong. Expected values occur only in assertions, never the scope selector.
    nodes = ".//*[normalize-space(@text) != '']"
    predicates = ["not(normalize-space(@text))"]
    if template_id == "TC-QK-240":
        assertion("A_NAME", "text_present", text=values["expected_name"], exact=True, within=scope,
                  desc="独立预期的完整名称出现在同一专用容器")
        predicates += [f"count({nodes}) = 1",
                       f"count({nodes}[@text={_literal(values['expected_name'])}]) = 1"]
    elif template_id == "TC-QK-241":
        assertion("A_COUNT_VISIBLE", "text_present_any", any=["共 "], within=scope,
                  desc="完整可见的同一专用数量容器中显示数量文案")
        digits = "substring(@text, 3, string-length(@text) - 4)"
        valid = ("starts-with(@text, '共 ') and substring(@text, string-length(@text) - 1) = ' 个'"
                 f" and string-length({digits}) > 0 and translate({digits}, '0123456789', '') = ''"
                 f" and substring({digits}, 1, 1) != '0'")
        predicates += [f"count({nodes}) = 1", f"count({nodes}[{valid}]) = 1"]
    else:
        floors = values["expected_floors"]
        predicates.append(f"count({nodes}) = {len(floors)}")
        for i, floor in enumerate(floors, 1):
            assertion(f"A_FLOOR_{i}", "text_present", text=floor, exact=True, within=scope,
                      desc="独立预期楼层标签在同一完整可见容器内")
            predicates.append(f"count({nodes}[@text={_literal(floor)}]) = 1")
    assertion("A_DISPLAY_SHAPE", "target_present", target=_target(f"({xpath})[{' and '.join(predicates)}]"),
              desc="专用容器的完整暴露text标签满足数量/格式/无重复约束（提案范围）")

    assertion("A_PAGE", "state_is", state="qk_vpd_home",
              desc="读取同一帧必须仍是VPD首页；页面签名不证明车辆/园区身份或地图完整性")

    frozen_inputs = {"template_id": template_id, "template_version": TEMPLATE_VERSION,
                     "scope_target": scope, "scope_source": scope_source, "expected_source": expected_source,
                     "scope_target_hash": sha256_obj(target), **values}
    return deepcopy({
        "id": case_id, "version": 1, "status": "draft", "app": app.id,
        "title": template["title"] + " · 冻结实例 " + case_id,
        "goal": template["title"],
        "purpose": "独立预期与本次显示比对；通用业务范围为待确认提案，不继承旧车辆/园区/版本的审核或产品通过。",
        "origin": {"type": "template", "template_id": template_id, "template_version": TEMPLATE_VERSION,
                   "input_hash": sha256_obj(frozen_inputs), "inputs": frozen_inputs,
                   "calibration": cal, "source_verification": "declared_registration_only_requires_human_review"},
        "preconditions": ["人工准备并核实当前车辆/园区及VPD首页，本例不启动APP、切车或操作车辆。",
                          "设备已解锁且页面无待处置弹窗；既有Runner预检/打断机制仍可能动作，运行前须另行核对。",
                          "scope已独立登记为完整暴露的专用text容器；版本/build属于校准证据，不是固定业务前提。"],
        "env_requirements": {}, "test_data": frozen_inputs, "test_data_none": False, "authorizations": [],
        "steps": [{"id": "S_DISPLAY", "action": "read", "target": scope, "read_scope": "self",
                   "measure": "display_raw", "retry": 0, "timeout_s": 0, "critical": True,
                   "oracle_refs": [oracle], "desc": "只从独立定位容器读取实际原文，不从实际值生成预期"}],
        "assertions": assertions,
        "cleanup": [{"id": "C_OBSERVE", "action": "wait", "duration_s": 0, "retry": 0,
                     "timeout_s": 0, "critical": True, "desc": "只读终态观察；无业务状态修改需要恢复"}],
        "knowledge_used": [{"id": oracle, "use": "通用显示提案，须独立确认", "applicability": "unknown",
                            "rationale": "不沿用旧K-QK-IDX规则的版本/场景确认；此冻结实例须分别审核和重放"}],
        "sources": [{"type": "template", "ref": template_id, "version": TEMPLATE_VERSION},
                    {"type": "expected_data", **expected_source, "verification": "declared_requires_review"},
                    {"type": "scope_calibration", **scope_source, "validation_level": cal["validation_level"],
                     "verification": "registered_evidence_requires_review"}],
    })
