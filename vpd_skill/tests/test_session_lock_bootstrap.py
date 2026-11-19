"""Cross-process lock bootstrap, without any device or production assets."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from vpdtest.session import session_lock


@pytest.mark.parametrize("original", [b"", b"\0"])
def test_contender_waits_without_writing_before_owning_the_lock(tmp_path, original):
    # Hold byte 0 (or flock on POSIX) while the marker is still empty: this is
    # the first-use interleaving between creating the path and initializing it.
    path = tmp_path / ".session.lock"
    path.write_bytes(original)
    with path.open("r+b", buffering=0) as owner:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A separate interpreter must take the ordinary contention path, not
        # fail with a raw write/flush error or enter the critical section.
        probe = """
from pathlib import Path
import sys
from vpdtest.session import session_lock
try:
    with session_lock(Path(sys.argv[1]), timeout_s=0):
        raise AssertionError("contender entered a held lock")
except TimeoutError:
    pass
"""
        result = subprocess.run([sys.executable, "-c", probe, str(tmp_path)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert os.fstat(owner.fileno()).st_size == len(original)
    # The OS releases the holder on close. Contention cannot poison the path.
    with session_lock(tmp_path, timeout_s=1):
        pass
    assert path.read_bytes() == b"\0"
