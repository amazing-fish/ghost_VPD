"""Canonical paths are portable; source bytes remain strictly bound."""
import hashlib

from vpdtest import cases
from vpdtest.util import sha256_obj


def test_engine_manifest_uses_portable_paths_and_exact_source_bytes(ws, tmp_path, monkeypatch):
    engine = tmp_path / "engine"
    (engine / "providers").mkdir(parents=True)
    (engine / "entry.py").write_bytes(b"pass\n")
    source = engine / "providers/oracle.py"
    source.write_bytes(b"value = 1\n")
    monkeypatch.setattr(cases, "__file__", str(engine / "entry.py"))
    case = ws.load_case("TC-QK-244")
    actual = cases.review_status(ws, case)["current"]["engine_hash"]
    expected = sha256_obj({"entry.py": hashlib.sha256(b"pass\n").hexdigest(),
                           "providers/oracle.py": hashlib.sha256(b"value = 1\n").hexdigest()})
    assert actual == expected
    # The checkout policy makes source bytes equal across hosts; the gate itself
    # must still detect any changed bytes, including a manual CRLF edit.
    source.write_bytes(b"value = 1\r\n")
    assert cases.review_status(ws, case)["current"]["engine_hash"] != actual


def test_template_manifest_uses_portable_relative_names(ws):
    app = ws.app("qiankun_hm")
    nested = app.dir / "templates/nested"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "sample.bin").write_bytes(b"fixture pixels")
    templates = cases.review_context(ws, app)["templates"]
    assert templates["templates/nested/sample.bin"] == hashlib.sha256(b"fixture pixels").hexdigest()
