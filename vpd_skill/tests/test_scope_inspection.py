"""Synthetic offline scope diagnostics, never product acceptance/calibration."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

import pytest

from vpdtest import scope_inspection as S
from vpdtest.case_templates import build_case
from vpdtest.tree import UiTree

APP = SimpleNamespace(id="qiankun_hm", bundle="synthetic.bundle", targets={})
OID = "obs-0001"


def node(typ="Column", *, text="", id="", bounds=(10, 10, 390, 190), children=(), **attrs):
    x, y, r, b = bounds
    return {"attributes": {"type": typ, "text": text, "id": id, "bounds": f"[{x},{y}][{r},{b}]", **attrs},
            "children": list(children)}


def tree(texts=("园区A",)):
    return node("Root", bounds=(0, 0, 400, 800), children=[
        node(id="scope", children=[node("Text", text=text, bounds=(20, 20 + i * 30, 380, 40 + i * 30))
                                   for i, text in enumerate(texts)])])


def spec(template="TC-QK-240"):
    return {"template_id": template, "candidate_target": {"side_effect": "read_only", "forbid_tap": True,
                                                           "strategies": [{"kind": "id", "value": "scope"}]}}


def observation():
    return {"obs_id": OID, "ts": "2001-01-01T00:00:00+00:00", "device": "synthetic-device", "source": "hdc",
            "foreground": APP.bundle, "foreground_after": APP.bundle, "fresh": True, "process_alive": True,
            "scope": {"app": APP.id, "app_version": "1.2.3"}, "page": {"state": "qk_vpd_home"},
            "tree": {"status": "ok", "path": f"obs/{OID}/tree.json"},
            "screenshot": {"status": "ok", "path": f"obs/{OID}/screen.png"}}


def png():
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(
        b"IDAT", zlib.compress(b"\x00\xff\x00\xff")) + chunk(b"IEND", b"")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "session-synthetic"
    save(root / f"obs/{OID}/observation.json", observation())
    save(root / f"obs/{OID}/tree.json", tree())
    (root / f"obs/{OID}/screen.png").write_bytes(png())
    save(root / "run.json", {"run_id": root.name, "app": {"id": APP.id, "bundle": APP.bundle, "version": "1.2.3"},
         "device": {"serial": "synthetic-device", "kind": "hdc"}, "device_spec": "hdc:synthetic-device",
         "validation_level": "real_device", "started": "2001-01-01T00:00:00+00:00"})
    return root


def inspect(root):
    return S.inspect_saved_scope(APP, spec(), root, OID)


def codes(result):
    return {row["code"] for row in result["issues"]}


def change(root, file, update):
    path = root / file
    value = json.loads(path.read_text())
    update(value)
    save(path, value)


def header(**updates):
    identity = {"app": APP.id, "bundle": APP.bundle, "device": "hdc:synthetic-device", "validation_level": "real_device"}
    identity.update(updates)
    return {"event": "session_open", "ts": "2001-01-01T00:00:00+00:00", "identity": identity,
            "facts": {**identity, "app_version": "1.2.3"}}


def events(root, *rows):
    (root / "events.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


@pytest.mark.parametrize("template,texts", [("TC-QK-240", ["园区A"]), ("TC-QK-241", ["共 7 个"]),
                                            ("TC-QK-243", ["L1", "B1"])])
def test_three_synthetic_structures_without_registration_or_expected(template, texts):
    result = S.inspect_scope(APP, spec(template), observation(), tree(texts))
    assert result["diagnostic_status"] == "inspected"
    assert result["shape"]["compatible"] is True
    assert result["localization"]["status"] == "unique"
    assert result["semantic_role"] == "requires_human_review"
    assert result["current_applicability"] == "not_checked"
    assert result["production_calibration"] == "not_granted"
    assert result["mode"] == "offline_diagnostic"
    assert not any(k in result for k in ("verdict", "case", "steps", "assertions", "expected_name", "expected_floors"))
    assert "point" not in json.dumps(result)


@pytest.mark.parametrize("text,matches", [("共 7 个", True), ("共7个", False), ("共 0 个", False)])
def test_count_format_is_only_a_legacy_script_constraint(text, matches):
    result = S.inspect_scope(APP, spec("TC-QK-241"), observation(), tree([text]))
    assert result["shape"]["compatible"] is True
    assert result["legacy_count_format"]["matches"] is matches
    assert result["legacy_count_format"]["meaning"] == "legacy_script_constraint_not_confirmed_general_requirement"


def test_floor_observations_are_not_expected_set():
    result = S.inspect_scope(APP, spec("TC-QK-243"), observation(), tree(["unexpected", "L1", "L1"]))
    assert result["observed_labels"]["unique_values"] == ["L1", "unexpected"]
    assert result["observed_labels"]["duplicates"] == ["L1"]
    assert result["shape"]["compatible"] is False
    assert "expected_floors" not in json.dumps(result)


@pytest.mark.parametrize("kind,problem", [("root", "root_scope"), ("mixed", "requires_one_nonempty_text_descendant"),
    ("own", "scope_has_own_text"), ("empty", "no_nonempty_text_descendants"),
    ("description", "no_nonempty_text_descendants"), ("hidden_text", "text_hidden_clipped_or_outside_scope"),
    ("outside", "text_hidden_clipped_or_outside_scope"), ("nested_clip", "text_hidden_clipped_or_outside_scope")])
def test_shape_counterexamples(kind, problem):
    raw, candidate = tree(), spec()
    container = raw["children"][0]
    label = container["children"][0]
    if kind == "root":
        raw["attributes"]["id"] = "scope"
        container["attributes"]["id"] = "other"
    elif kind == "mixed":
        container["children"].append(node("Text", text="Unrelated", bounds=(20, 50, 380, 70)))
    elif kind == "own":
        container["attributes"]["text"] = "self"
    elif kind == "empty":
        container["children"] = []
    elif kind == "description":
        label["attributes"].update(text="", description="园区A")
    elif kind == "hidden_text":
        label["attributes"].update(visible="false", description="园区A")
    elif kind == "outside":
        label["attributes"]["bounds"] = "[20,790][380,810]"
    elif kind == "nested_clip":
        container["children"] = [node("Scroll", bounds=(10, 10, 390, 30), children=[label])]
    result = S.inspect_scope(APP, candidate, observation(), raw)
    assert result["shape"]["compatible"] is False
    assert problem in result["shape"]["problems"]


@pytest.mark.parametrize("kind,status", [("missing", "missing"), ("multiple", "ambiguous"), ("hidden", "hidden"),
    ("hidden_ancestor", "hidden"), ("quarter", "clipped"), ("rounding", "clipped")])
def test_localization_exact_visibility(kind, status):
    raw = tree()
    container = raw["children"][0]
    if kind == "missing":
        container["attributes"]["id"] = "other"
    elif kind == "multiple":
        second = deepcopy(container)
        second["attributes"]["visible"] = "false"  # Hidden duplicate is still ambiguous.
        raw["children"].append(second)
    elif kind == "hidden":
        container["attributes"]["visible"] = "false"
    elif kind == "hidden_ancestor":
        raw["children"] = [node(visible="false", children=[container])]
    elif kind == "quarter":
        raw["children"] = [node("Scroll", bounds=(10, 10, 390, 55), children=[container])]
    elif kind == "rounding":
        raw = node("Root", bounds=(0, 0, 10000, 10000), children=[
            node("Scroll", bounds=(0, 0, 10000, 9999), children=[
                node(id="scope", bounds=(0, 0, 10000, 10000), children=[node("Text", text="A")])])])
    result = S.inspect_scope(APP, spec(), observation(), raw)
    assert result["localization"]["status"] == status
    if kind == "rounding":
        assert result["localization"]["locator_status"] == "found"
        assert result["shape"]["compatible"] is False


@pytest.mark.parametrize("selector", ["/Root/Column[1]", "//Column", "/Root/*[1]"])
def test_restricted_structural_xpath(selector):
    candidate = spec()
    candidate["candidate_target"]["strategies"] = [{"kind": "xpath", "value": selector}]
    assert S.inspect_scope(APP, candidate, observation(), tree())["shape"]["compatible"]


@pytest.mark.parametrize("field", ["status", "calibration", "template_scope", "expected_name", "secret", "scroll",
                                    "allow_input", "fallback", "min_visible_fraction", "when", "within"])
def test_candidate_rejects_all_extra_fields(field):
    candidate = spec()
    candidate["candidate_target"][field] = False
    with pytest.raises(ValueError):
        S.inspect_scope(APP, candidate, observation(), tree())


@pytest.mark.parametrize("selector", ["//*[@text='A']", "//Column[0]", "//Column[last()]", "//*[contains(@id,'x')]",
                                        "${secret:NAME}", "//Column | //Root", "//Column/.."])
def test_dynamic_xpath_rejected(selector):
    candidate = spec()
    candidate["candidate_target"]["strategies"] = [{"kind": "xpath", "value": selector}]
    with pytest.raises(ValueError):
        S.inspect_scope(APP, candidate, observation(), tree())


@pytest.mark.parametrize("mutation", [lambda s: s.update(expected_name="A"), lambda s: s.update(template_id="other"),
    lambda s: s.update(candidate_target="name"), lambda s: s["candidate_target"].update(forbid_tap=False),
    lambda s: s["candidate_target"].update(side_effect="user_data"),
    lambda s: s["candidate_target"]["strategies"].append({"kind": "id", "value": "other"}),
    lambda s: s["candidate_target"]["strategies"][0].update(within="pane"),
    lambda s: s["candidate_target"]["strategies"][0].update(kind="template"),
    lambda s: s["candidate_target"]["strategies"][0].update(value={"secret": "NAME"})])
def test_invalid_specs_fail_before_evidence_access(monkeypatch, mutation):
    candidate = spec()
    mutation(candidate)
    monkeypatch.setattr(S, "_read", lambda *a: pytest.fail("invalid specs cannot read files"))
    with pytest.raises(ValueError):
        S.inspect_saved_scope(APP, candidate, "irrelevant", OID)


@pytest.mark.parametrize("oid", ["../obs-0001", "/obs-0001", "C:\\obs-0001", "\\\\host\\obs-0001", "obs-0001/..", "obs-1"])
def test_invalid_obs_id_rejected_before_reads(monkeypatch, oid):
    monkeypatch.setattr(S, "_read", lambda *a: pytest.fail("invalid obs ID cannot read files"))
    with pytest.raises(ValueError):
        S.inspect_saved_scope(APP, spec(), "irrelevant", oid)


def test_saved_complete_historical_snapshot(package):
    result = inspect(package)
    assert result["diagnostic_status"] == "inspected", result
    assert result["shape"]["evidence_usable"] is True
    assert result["historical_observation"]["freshness_scope"] == "collection_time_only"
    assert result["historical_environment"]["app_version"] == "1.2.3"
    assert result["current_applicability"] == "not_checked"
    assert result["visual_evidence"] == {"status": "present_unreviewed", "recorded_status": "ok", "pixel_review": "not_performed"}
    for rel, item in result["evidence_files"].items():
        if item["status"] == "read":
            assert item["sha256"] == hashlib.sha256((package / rel).read_bytes()).hexdigest()


@pytest.mark.parametrize("file", [f"obs/{OID}/screen.png", f"obs/{OID}/tree.json", f"obs/{OID}/observation.json", "run.json"])
def test_missing_material_is_explicit_not_na(package, file):
    (package / file).unlink()
    result = inspect(package)
    assert result["diagnostic_status"] == "incomplete"
    assert result["issues"]
    assert "not_applicable" not in json.dumps(result)
    if file.endswith("screen.png"):
        assert result["shape"]["compatible"] is True
        assert result["visual_evidence"]["status"] == "missing"
        assert result["shape"]["evidence_usable"] is False


@pytest.mark.parametrize("path", ["../tree.json", "/tmp/tree.json", "C:/private/tree.json", "C:\\private\\tree.json",
                                    "\\\\host\\share\\tree.json", "obs/obs-0002/tree.json", "obs\\obs-0001/tree.json",
                                    "obs/obs-0001/../obs-0001/tree.json"])
def test_json_paths_never_followed(package, path):
    change(package, f"obs/{OID}/observation.json", lambda value: value["tree"].update(path=path))
    result = inspect(package)
    assert "evidence_path_conflict" in codes(result)
    assert result["diagnostic_status"] == "conflict"
    assert result["shape"]["evidence_usable"] is False


@pytest.mark.parametrize("mutation", [lambda o: o.update(obs_id="obs-0002"), lambda o: o.update(device="other"),
    lambda o: o.update(source="replay"), lambda o: o.update(source="unrecognized"),
    lambda o: o.update(foreground="other"), lambda o: o.update(foreground_after="other"),
    lambda o: o["scope"].update(app="other"), lambda o: o["scope"].update(app_version="other"),
    lambda o: o["tree"].update(status="stale"), lambda o: o.update(fresh="true"),
    lambda o: o.update(run_id="other"), lambda o: o.update(session_id="other"),
    lambda o: o.update(foreground_query_errors=[{"error_class": "transport"}]),
    lambda o: o.update(ts="not-a-time"), lambda o: o["screenshot"].update(status="skipped"),
    lambda o: o["screenshot"].update(sha256="0" * 16)])
def test_observation_conflicts_cannot_be_usable(package, mutation):
    change(package, f"obs/{OID}/observation.json", mutation)
    result = inspect(package)
    assert result["diagnostic_status"] == "conflict"
    assert result["inspection_errors"]
    assert result["shape"]["evidence_usable"] is False


@pytest.mark.parametrize("field", ["run_id", "app", "bundle", "serial", "kind", "device_spec", "validation_level"])
def test_run_identity_conflicts(package, field):
    def mutate(run):
        if field in ("app", "bundle"):
            run["app"]["id" if field == "app" else field] = "other"
        elif field in ("serial", "kind"):
            run["device"][field] = "other"
        else:
            run[field] = "other"
    change(package, "run.json", mutate)
    result = inspect(package)
    assert result["inspection_errors"] or result["diagnostic_status"] == "incomplete"
    assert result["shape"]["evidence_usable"] is False


def test_session_header_can_bind_instead_of_run(package):
    (package / "run.json").unlink()
    events(package, header())
    result = inspect(package)
    assert result["diagnostic_status"] == "inspected", result
    assert result["historical_environment"]["source"] == "events.jsonl:session_open"
    assert result["historical_environment"]["session_id_source"] == "explicit_evidence_directory"


@pytest.mark.parametrize("variant", ["app", "bundle", "device", "validation_level", "facts", "second", "version"])
def test_conflicting_session_headers(package, variant):
    row = header()
    if variant in ("app", "bundle", "validation_level"):
        row["identity"][variant] = "other"
    elif variant == "device":
        row["identity"][variant] = "hdc:other"
    elif variant == "facts":
        row["facts"]["device"] = "hdc:other"
    elif variant == "version":
        row["facts"]["app_version"] = "other"
    if variant == "second":
        events(package, row, header(device="hdc:other"))
    else:
        events(package, row)
    result = inspect(package)
    assert result["inspection_errors"]
    assert result["shape"]["evidence_usable"] is False


def test_replay_equal_filename_does_not_hide_distinct_declared_sources(package):
    change(package, f"obs/{OID}/observation.json", lambda o: o.update(source="replay", device="replay-world"))
    change(package, "run.json", lambda r: r.update(device={"serial": "replay-world", "kind": "replay"},
           device_spec="replay:/a/world.json", validation_level="offline_replay"))
    events(package, header(device="replay:/b/world.json", validation_level="offline_replay"))
    result = inspect(package)
    assert any(e["field"].endswith("device_declared_spec") for e in result["inspection_errors"])


@pytest.mark.parametrize("file,payload", [(f"obs/{OID}/observation.json", b"{"), (f"obs/{OID}/tree.json", b"[]"),
    ("run.json", b'{"run_id":"a", "run_id":"b"}'), ("events.jsonl", b"{\n"),
    (f"obs/{OID}/observation.json", b'{"value":NaN}'), ("run.json", b'{"value":1e999}')])
def test_corrupt_json_is_not_silently_ignored(package, file, payload):
    (package / file).write_bytes(payload)
    result = inspect(package)
    assert result["diagnostic_status"] == "conflict"
    assert result["inspection_errors"]


@pytest.mark.parametrize("value", [{"children": [5]}, {"attributes": []}, {"children": {}},
    {"attributes": {"x": {"secret": "NAME"}}}, {"attributes": {"x": float("inf")}}])
def test_malformed_raw_tree_fails_closed(value):
    result = S.inspect_scope(APP, spec(), observation(), value)
    assert "invalid_tree" in codes(result)
    assert result["inspection_errors"]


@pytest.mark.parametrize("payload", [b"not-png", b"\x89PNG\r\n\x1a\n" + b"\x00"*4 + b"IHDR" + b"\x00"*17,
    png()[:-12], png() + b"trailing", png()[:-1] + b"x"])
def test_bad_png_is_not_visual_evidence(package, payload):
    (package / f"obs/{OID}/screen.png").write_bytes(payload)
    result = inspect(package)
    assert "invalid_screenshot" in codes(result)
    assert result["visual_evidence"]["status"] == "invalid"
    assert result["shape"]["evidence_usable"] is False


@pytest.mark.parametrize("rel", [f"obs/{OID}/tree.json", f"obs/{OID}", "obs", "run.json", "events.jsonl"])
def test_symlink_escape_rejected(package, tmp_path, rel):
    import shutil
    target = package / rel
    outside = tmp_path / "outside"
    if target.exists() and target.is_dir():
        shutil.copytree(target, outside)
        shutil.rmtree(target)
    else:
        outside.write_bytes(target.read_bytes() if target.exists() else b"{}")
        target.unlink(missing_ok=True)
    try:
        target.symlink_to(outside, target_is_directory=outside.is_dir())
    except OSError:
        pytest.skip("symlinks not permitted by platform")
    result = inspect(package)
    assert "evidence_file_rejected" in codes(result)
    assert result["inspection_errors"]


def test_symlink_swap_between_check_and_open_is_rejected(package, tmp_path, monkeypatch):
    outside = tmp_path / "outside.json"
    outside.write_text('{"private":"must not read"}')
    original = S._safe_bytes
    def swap(root, rel, limit):
        if rel == f"obs/{OID}/tree.json":
            path = root / rel
            path.unlink()
            try:
                path.symlink_to(outside)
            except OSError:
                pytest.skip("symlinks not permitted by platform")
        return original(root, rel, limit)
    monkeypatch.setattr(S, "_safe_bytes", swap)
    result = inspect(package)
    assert "evidence_file_rejected" in codes(result)
    assert "private" not in json.dumps(result)


def test_strict_input_json_parser(tmp_path):
    path = tmp_path / "candidate.json"
    for bad in (b'{"template_id":"a","template_id":"b"}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}'):
        path.write_bytes(bad)
        with pytest.raises(ValueError):
            S.read_scope_input(path)
    save(path, spec())
    assert S.read_scope_input(path) == spec()


def test_pure_diagnostic_does_not_mutate_inputs_or_tree_caches():
    raw, candidate, obs = tree(), spec(), observation()
    parsed = UiTree(raw)
    before = deepcopy((raw, candidate, obs, APP.targets))
    S.inspect_scope(APP, candidate, obs, parsed)
    assert (raw, candidate, obs, APP.targets) == before
    assert parsed._vis is None and parsed._xml is None


def test_saved_diagnostic_no_writes_device_configuration_or_governance(package, monkeypatch):
    import socket
    from vpdtest import workspace, observe, params, dispatch, cases, knowledge
    def forbidden(*args, **kwargs):
        pytest.fail("offline inspection must not invoke device/config/secret/governance/network")
    for owner, name in ((workspace, "load_config"), (workspace.Workspace, "device"), (observe.Observer, "__init__"),
                        (observe.RunStore, "__init__"), (dispatch.Dispatcher, "__init__"), (params.Params, "__init__"),
                        (cases, "promote"), (knowledge.KnowledgeStore, "__init__"), (socket, "socket")):
        monkeypatch.setattr(owner, name, forbidden)
    before = {str(path.relative_to(package)): path.read_bytes() for path in package.rglob("*") if path.is_file()}
    result = inspect(package)
    after = {str(path.relative_to(package)): path.read_bytes() for path in package.rglob("*") if path.is_file()}
    assert before == after
    assert result["diagnostic_status"] == "inspected"
    assert not APP.targets


def test_diagnostic_does_not_grant_instantiate_registration():
    candidate = spec()
    assert S.inspect_scope(APP, candidate, observation(), tree())["shape"]["compatible"]
    app = SimpleNamespace(id=APP.id, bundle=APP.bundle, targets={"candidate": candidate["candidate_target"]})
    with pytest.raises(ValueError, match="登记"):
        build_case(app, {"template_id": "TC-QK-240", "new_case_id": "TC-QK-240-SYNTHETIC", "scope_target": "candidate",
                   "scope_source": {"source": "device_observation", "ref": "synthetic-evidence"},
                   "expected_source": {"source": "test_data", "ref": "independent"}, "expected_name": "园区A"})


@pytest.mark.parametrize("path", ["//host/share/run", "\\\\host\\share\\run", "\\\\?\\C:\\run", "\\\\.\\Device\\run"])
def test_remote_root_paths_rejected_before_filesystem(monkeypatch, path):
    monkeypatch.setattr(Path, "lstat", lambda *a, **k: pytest.fail("must reject remote syntax before filesystem"))
    with pytest.raises(ValueError, match="UNC"):
        S.inspect_saved_scope(APP, spec(), path, OID)
    with pytest.raises(ValueError, match="UNC"):
        S.read_scope_input(path)


def test_symbolic_evidence_root_rejected(package, tmp_path):
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(package, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted by platform")
    result = inspect(alias)
    assert "evidence_directory_rejected" in codes(result)
    assert result["shape"]["evidence_usable"] is False


def test_false_historical_fresh_does_not_grant_current_permission(package):
    change(package, f"obs/{OID}/observation.json", lambda o: o.update(fresh=False))
    result = inspect(package)
    assert result["shape"]["compatible"] is True
    assert result["shape"]["evidence_usable"] is False
    assert result["current_applicability"] == "not_checked"
    assert "collection_not_fresh" in codes(result)


def test_recorded_node_count_conflict(package):
    change(package, f"obs/{OID}/observation.json", lambda o: o["tree"].update(nodes=9999))
    result = inspect(package)
    assert "tree_node_count_conflict" in codes(result)
    assert not result["shape"]["evidence_usable"]


def test_huge_png_dimensions_rejected_before_decoder(package, monkeypatch):
    import cv2
    data = bytearray(png())
    data[16:24] = struct.pack(">II", 100000, 100000)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]) & 0xffffffff)
    (package / f"obs/{OID}/screen.png").write_bytes(data)
    monkeypatch.setattr(cv2, "imdecode", lambda *a: pytest.fail("dimension bomb must fail before decode"))
    assert "invalid_screenshot" in codes(inspect(package))


def test_hash_uses_exact_read_snapshot_not_reopened_file(package, monkeypatch):
    original = S._safe_bytes
    seen = {}
    def read_then_change(root, rel, limit):
        data = original(root, rel, limit)
        seen[rel] = data
        if rel == f"obs/{OID}/tree.json":
            save(root / rel, tree(["changed after read"]))
        return data
    monkeypatch.setattr(S, "_safe_bytes", read_then_change)
    result = inspect(package)
    rel = f"obs/{OID}/tree.json"
    assert result["evidence_files"][rel]["sha256"] == hashlib.sha256(seen[rel]).hexdigest()
    assert result["shape"]["text_nodes"][0]["node"]["text"] == "园区A"
    assert result["evidence_files"][rel]["sha256"] != hashlib.sha256((package / rel).read_bytes()).hexdigest()


@pytest.mark.parametrize("first", ["run", "session"])
def test_later_known_version_is_compared_to_observation_even_if_first_is_missing(package, first):
    if first == "run":
        change(package, "run.json", lambda r: r["app"].pop("version"))
        row = header()
        row["facts"]["app_version"] = "conflicting-version"
        events(package, row)
    else:
        (package / "run.json").unlink()
        unknown = header()
        unknown["facts"].pop("app_version")
        conflicting = header()
        conflicting["facts"]["app_version"] = "conflicting-version"
        events(package, unknown, conflicting)
    result = inspect(package)
    assert any(e["field"] == "scope.app_version" for e in result["inspection_errors"])
    assert result["shape"]["evidence_usable"] is False


@pytest.mark.parametrize("path", ["CON", "NUL.json", "COM1", "LPT9", "C:/CON", "folder/PRN.txt"])
def test_input_device_aliases_rejected_before_filesystem(monkeypatch, path):
    monkeypatch.setattr(S, "_safe_bytes", lambda *a: pytest.fail("device alias must not reach filesystem"))
    with pytest.raises(ValueError):
        S.read_scope_input(path)


def test_input_fifo_rejected_without_waiting(tmp_path):
    import os
    if not hasattr(os, "mkfifo"):
        pytest.skip("platform has no FIFO")
    pipe = tmp_path / "candidate.json"
    os.mkfifo(pipe)
    with pytest.raises(ValueError, match="普通"):
        S.read_scope_input(pipe)


@pytest.mark.parametrize("field,extra", [("facts", {"app": "other"}), ("facts", {"app_version": "other"}),
    ("oracle_facts", {"app_version": "other"}), ("preflight", {"app_version": "other"}),
    ("preflight", {"device": {"serial": "other"}}), ("preflight", {"device": {"kind": "replay"}})])
def test_known_repeated_run_identity_must_not_conflict(package, field, extra):
    change(package, "run.json", lambda r: r.update({field: extra}))
    result = inspect(package)
    assert any(e["code"] == "identity_conflict" and e["field"].startswith("run." + field) for e in result["inspection_errors"])
    assert not result["shape"]["evidence_usable"]


@pytest.mark.parametrize("payload", [b'{"x":"\\ud800"}', b'{"\\udfff":"value"}', b'{"a":["\\ud800"]}'])
def test_strict_json_rejects_unpaired_surrogates(payload):
    with pytest.raises(ValueError, match="Unicode"):
        S._decode(payload)


def test_valid_unicode_surrogate_pair_is_allowed():
    assert S._decode(b'{"x":"\\ud83d\\ude80"}') == {"x": "🚀"}


@pytest.mark.parametrize("where", ["observation", "tree"])
def test_pure_input_bad_unicode_is_rejected_without_echoing_it(where):
    obs, raw = observation(), tree()
    if where == "observation":
        obs["page"]["state"] = "\ud800"
    else:
        raw["children"][0]["children"][0]["attributes"]["text"] = "\ud800"
    result = S.inspect_scope(APP, spec(), obs, raw)
    assert result["inspection_errors"]
    json.dumps(result, ensure_ascii=False).encode("utf-8")


def test_saved_surrogate_conflict_is_serializable(package):
    change(package, f"obs/{OID}/observation.json", lambda o: o["page"].update(state="bad"))
    path = package / f"obs/{OID}/observation.json"
    path.write_bytes(path.read_bytes().replace(b'"bad"', b'"\\ud800"'))
    result = inspect(package)
    assert result["inspection_errors"]
    json.dumps(result, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("template", ["TC-QK-240", "TC-QK-241", "TC-QK-243"])
@pytest.mark.parametrize("blank,counted", [("", False), (" \t\r\n", False), ("\u00a0", True),
                                         ("\u2003", True), ("\u202f", True), ("\u3000", True), (" \u00a0\t", True)])
def test_structural_text_count_matches_template_xpath_whitespace(template, blank, counted):
    value = "共 7 个" if template == "TC-QK-241" else "L1"
    raw = tree([value, blank])
    result = S.inspect_scope(APP, spec(template), observation(), raw)
    expected_nodes = UiTree(raw).xpath("//*[@id='scope']//*[normalize-space(@text) != '']")
    assert result["shape"]["nonempty_text_descendants"] == len(expected_nodes) == 1 + int(counted)
    assert result["shape"]["text_count_basis"] == "xpath_1_0_normalize_space"
    assert [entry["node"]["text"] for entry in result["shape"]["text_nodes"]] == [n.text for n in expected_nodes]
    if template != "TC-QK-243":
        assert result["shape"]["compatible"] is not counted
    else:
        assert result["observed_labels"]["values"] == [n.text for n in expected_nodes]


@pytest.mark.parametrize("blank,has_text", [(" \t\r\n", False), ("\u00a0", True), ("\u2003", True), ("\u202f", True), ("\u3000", True)])
def test_scope_own_text_matches_template_xpath_whitespace(blank, has_text):
    raw = tree()
    raw["children"][0]["attributes"]["text"] = blank
    result = S.inspect_scope(APP, spec(), observation(), raw)
    own_text = UiTree(raw).xpath("//*[@id='scope'][normalize-space(@text) != '']")
    assert bool(own_text) is has_text
    assert ("scope_has_own_text" in result["shape"]["problems"]) is has_text
    assert result["shape"]["compatible"] is not has_text


def test_unicode_whitespace_floor_duplicates_follow_template_shape_count():
    raw = tree(["L1", "\u00a0", "\u00a0"])
    result = S.inspect_scope(APP, spec("TC-QK-243"), observation(), raw)
    assert result["observed_labels"]["duplicates"] == ["\u00a0"]
    assert "duplicate_labels" in result["shape"]["problems"]
    assert result["shape"]["nonempty_text_descendants"] == 3


def test_saved_scope_uses_selected_recheck_tree_and_hash(package):
    original = package / f'obs/{OID}/tree.json'
    selected = package / f'obs/{OID}/tree_recheck.json'
    selected.write_bytes(original.read_bytes())
    save(original, {})
    obs = observation()
    obs['tree']['path'] = f'obs/{OID}/tree_recheck.json'
    save(package / f'obs/{OID}/observation.json', obs)
    result = inspect(package)
    assert result['localization']['status'] == 'unique'
    assert 'evidence_path_conflict' not in codes(result)
    assert result['evidence_files'][f'obs/{OID}/tree_recheck.json']['sha256'] == hashlib.sha256(selected.read_bytes()).hexdigest()
    assert original.read_text(encoding='utf-8') == '{}'
