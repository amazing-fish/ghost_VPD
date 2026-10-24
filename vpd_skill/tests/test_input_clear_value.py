"""#91: successful key receipts do not prove an editable was emptied (synthetic)."""
import json

import pytest
from test_retry_dispatch_safety import CountingTransport, _context, _counts, _step

from vpdtest.execute import Executor
from vpdtest.experience import ExperienceStore
from vpdtest.observe import RunStore
from vpdtest.params import Params
from vpdtest.util import write_json
from vpdtest.workspace import RunContext


class AppendingTransport(CountingTransport):
    """Model acknowledged partial deletion and HDC's append-style input."""
    partial_clear = True
    cover_after_clear = False
    attr_transform = None

    def key(self, name):
        if name == "delete" and self.partial_clear and len(self.bound.get("name", "")) <= 1:
            return self._physical("key:delete", (name,), lambda: self._res("key_delete", raw="keyEvent"))
        return super().key(name)

    def input_text(self, x, y, text):
        before = self.bound.get("name", "")
        result = super().input_text(x, y, text)
        if result.sent is not False:
            self.bound["name"] = before + text
        return result

    def clear_text(self, *args, **kwargs):
        result = super().clear_text(*args, **kwargs)
        if result.ok and self.cover_after_clear:
            self.world["screens"]["fixture"]["root"]["children"].append({
                "t": "SyntheticCover", "click": True, "b": [40, 600, 1280, 750]})
        return result

    def dump_tree(self, bundle, local_path):
        result = super().dump_tree(bundle, local_path)
        if result.ok:
            box = self.world["screens"]["fixture"]["root"]["children"][2]
            def walk(raw):
                if raw.get("attributes", {}).get("id") == "name":
                    attrs = raw["attributes"]
                    # Model a provider that reports the same value in both sources;
                    # preserve explicitly supplied conflicts, including empty originals.
                    if "originalText" not in box.get("attrs", {}):
                        attrs["originalText"] = attrs.get("text", "")
                    if self.attr_transform:
                        self.attr_transform(attrs)
                for child in raw.get("children", []):
                    walk(child)
            walk(result.data["tree"])
            write_json(local_path, result.data["tree"])
        return result


@pytest.fixture
def clear_rig(ws, tmp_path):
    base = _context(ws, tmp_path)
    device = AppendingTransport(base.device.world_path)
    device.launch(base.app.bundle, base.app.ability)
    device.bound["name"] = "B1层"
    box = device.world["screens"]["fixture"]["root"]["children"][2]
    box["attrs"] = {"hint": "输入查询词"}
    ctx = RunContext(ws, base.app, device, RunStore(ws.runs_dir),
                     ExperienceStore(ws.root, base.app.id).overlay(), mode="agent")
    return ctx, box


def run_input(ctx, **kwargs):
    return Executor(ctx).run_step(_step("input", clear=True, text="A13", **kwargs), "clear-value-fixture")


@pytest.mark.parametrize("node_type", ["TextInput", "TextArea", "SearchField"])
@pytest.mark.parametrize("prompt", [{"hint": "输入查询词"}, {"hint": ""}, {"placeholder": "输入查询词"}])
def test_known_non_hint_text_stops_before_append(clear_rig, node_type, prompt):
    ctx, box = clear_rig
    box["t"], box["attrs"] = node_type, prompt
    result = run_input(ctx, retry=2, idempotent=True)
    assert _counts(ctx.device)["input_text"] == 0, ctx.device.bound
    assert ctx.device.bound["name"] == "B"
    assert _counts(ctx.device)["tap"] == 1
    assert len(result["attempts"]) == 1
    assert result["outcome"] == "error" and result["attribution"] == "tool"
    assert result["final_status"] == "clear_not_confirmed"
    assert result["effect_unknown"] and result["action_required"]
    att = result["attempts"][0]
    assert att["clear"]["sent"] is True and att["clear"]["ok"] is True
    check = att["clear"]["value_checks"][-1]
    assert check["status"] == "non_hint_text" and check["verified"] is False
    assert check["text_length"] == 1 and check["source"] == "ui_tree.attributes.text"
    assert "B" not in json.dumps(check, ensure_ascii=False)


def test_complete_clear_allows_one_append_without_claiming_full_value_verification(clear_rig):
    ctx, _ = clear_rig
    ctx.device.partial_clear = False
    result = run_input(ctx)
    assert result["outcome"] == "passed"
    assert _counts(ctx.device)["input_text"] == 1 and ctx.device.bound["name"] == "A13"
    checks = result["attempts"][0]["clear"]["value_checks"]
    assert all(c["verified"] is None for c in checks)


def test_missing_original_source_does_not_hide_known_non_hint_text(clear_rig):
    ctx, _ = clear_rig
    ctx.device.attr_transform = lambda attrs: attrs.pop("originalText", None)
    result = run_input(ctx)
    assert _counts(ctx.device)["input_text"] == 0 and ctx.device.bound["name"] == "B"
    check = result["attempts"][0]["clear"]["value_checks"][-1]
    assert check["status"] == "non_hint_text" and check["verified"] is False


@pytest.mark.parametrize("mode", ["missing_text", "invalid_text", "missing_prompt", "invalid_prompt",
                                  "invalid_original", "invalid_custom", "hint_text", "whitespace_hint",
                                  "placeholder_text", "conflicting_prompts", "conflicting_original", "empty_original", "grouped",
                                  "custom_text", "custom_type", "child_text", "not_editable", "masked"])
def test_unproven_value_source_is_unknown_not_verified_empty(clear_rig, mode):
    ctx, box = clear_rig
    if mode in ("missing_text", "invalid_text", "invalid_prompt", "invalid_original", "invalid_custom"):
        def transform(attrs):
            if mode == "missing_text":
                attrs.pop("text", None)
            elif mode == "invalid_text":
                attrs["text"] = None
            elif mode == "invalid_prompt":
                attrs["hint"] = []
            elif mode == "invalid_original":
                attrs["originalText"] = []
            else:
                attrs["accessibilityText"] = []
        ctx.device.attr_transform = transform
    elif mode == "missing_prompt":
        box["attrs"] = {}
    elif mode == "hint_text":
        box["attrs"]["hint"] = "B"
    elif mode == "whitespace_hint":
        box["attrs"]["text"] = " 输入查询词 "
    elif mode == "placeholder_text":
        box["attrs"]["placeholder"] = "B"
    elif mode == "conflicting_original":
        box["attrs"]["originalText"] = "other source"
    elif mode == "empty_original":
        box["attrs"]["originalText"] = ""
    elif mode == "conflicting_prompts":
        box["attrs"]["placeholder"] = "other prompt"
    elif mode == "grouped":
        box["attrs"]["accessibilityGroup"] = "true"
    elif mode == "custom_text":
        box["attrs"]["accessibilityText"] = "custom content"
    elif mode == "custom_type":
        box["attrs"]["accessibilityCustomComponentType"] = "custom field"
    elif mode == "child_text":
        box["children"] = [{"t": "Text", "text": "child label", "b": [40, 600, 100, 650]}]
    elif mode == "not_editable":
        box["t"] = "Column"
    else:
        box["attrs"]["password"] = "true"
    result = run_input(ctx)
    assert _counts(ctx.device)["input_text"] == 1  # Unknown retains the previous input contract.
    checks = result["attempts"][0]["clear"]["value_checks"]
    assert checks and all(c["status"] == "unknown" and c["verified"] is None for c in checks)
    if mode in ("conflicting_original", "empty_original", "invalid_original"):
        assert all(c["reason"] == "conflicting_text_sources" for c in checks)


def test_dispatcher_refresh_rechecks_new_non_hint_text(clear_rig, monkeypatch):
    ctx, _ = clear_rig
    ctx.device.partial_clear = False
    ctx.device.cover_after_clear = True
    observe = ctx.observe
    def refreshed(label, *args, **kwargs):
        if label.endswith(":dispatch-relocate"):
            children = ctx.device.world["screens"]["fixture"]["root"]["children"]
            children[:] = [n for n in children if n["t"] != "SyntheticCover"]
            ctx.device.bound["name"] = "B"
        return observe(label, *args, **kwargs)
    monkeypatch.setattr(ctx, "observe", refreshed)
    result = run_input(ctx)
    assert _counts(ctx.device)["input_text"] == 0, ctx.device.bound
    assert result["outcome"] == "error" and result["final_status"] == "clear_not_confirmed"
    checks = result["attempts"][0]["clear"]["value_checks"]
    assert checks[0]["status"] == "unknown"
    assert checks[-1]["phase"] == "dispatch_refresh" and checks[-1]["status"] == "non_hint_text"
    assert checks[0]["obs"] != checks[-1]["obs"]
    assert result["effect_unknown"] and result["action_required"]


def test_secret_path_does_not_read_or_record_clear_text(clear_rig):
    ctx, box = clear_rig
    ctx.params = Params({"pw": {"secret": "CLEAR_ONLY_TEST"}}, {"CLEAR_ONLY_TEST": "SYNTHETIC-ONLY-PASSWORD"}, "r")
    step = _step("input", clear=True, text="${var:pw}")
    step["target"]["secret"] = True
    result = Executor(ctx).run_step(step, "clear-secret-fixture")
    assert result["outcome"] == "passed" and _counts(ctx.device)["input_text"] == 1
    checks = result["attempts"][0]["clear"]["value_checks"]
    assert all(c["status"] == "unknown" and c["verified"] is None for c in checks)
    assert all(c["reason"] == "secret_not_read" and "text_length" not in c and "node" not in c for c in checks)
    assert "SYNTHETIC-ONLY-PASSWORD" not in json.dumps(checks)


@pytest.mark.parametrize("op,sent", [("tap", None), ("key:move_end", True), ("key:delete", True)])
def test_partial_or_unknown_clear_never_appends_or_repeats(clear_rig, op, sent):
    ctx, _ = clear_rig
    ctx.device.fail_next(op, sent=sent)
    result = run_input(ctx, retry=2, idempotent=True)
    assert _counts(ctx.device)["input_text"] == 0 and _counts(ctx.device)["tap"] == 1
    assert len(result["attempts"]) == 1 and result["effect_unknown"] and result["action_required"]
