"""Canonical paths are portable; the engine manifest records exact source bytes."""
import hashlib

from vpdtest import cases, review_snapshot


def test_engine_manifest_uses_portable_paths_and_exact_source_bytes(tmp_path):
    engine = tmp_path / "engine"
    (engine / "providers").mkdir(parents=True)
    (engine / "entry.py").write_bytes(b"pass\n")
    source = engine / "providers/oracle.py"
    source.write_bytes(b"value = 1\n")
    actual = review_snapshot.engine_files(engine)
    assert actual == {"entry.py": hashlib.sha256(b"pass\n").hexdigest(),
                      "providers/oracle.py": hashlib.sha256(b"value = 1\n").hexdigest()}
    # The checkout policy makes source bytes equal across hosts; the diagnostic
    # still reports any changed bytes, including a manual CRLF edit.
    source.write_bytes(b"value = 1\r\n")
    assert review_snapshot.engine_files(engine)["providers/oracle.py"] != actual["providers/oracle.py"]


def test_template_manifest_uses_portable_relative_names(ws):
    app = ws.app("qiankun_hm")
    nested = app.dir / "templates/nested"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "sample.bin").write_bytes(b"fixture pixels")
    templates = cases.review_context(ws, app)["templates"]
    assert templates["templates/nested/sample.bin"] == hashlib.sha256(b"fixture pixels").hexdigest()
