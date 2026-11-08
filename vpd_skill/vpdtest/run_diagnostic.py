"""Best-effort run failure location, independent of the evidence journal.

This is not a verdict or a device-stop receipt. Never call device/safety methods.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class RunDiagnostic:
    def __init__(self):
        self.store = None
        self.phase = "initialization"
        self.step = None
        self.context = None
        self.record = None

    def allocated(self, store):
        self.store = store

    def capture(self, error):
        if self.store is None or self.record is not None:
            return
        record = {"schema_version": 1, "run_id": self.store.run_id, "dir": str(self.store.dir),
                  "state": "interrupted" if isinstance(error, KeyboardInterrupt) else "tool_error",
                  "phase": self.phase, "step": self.step, "error_type": type(error).__name__,
                  "persisted": False,
                  "safety": {"motion_armed": None, "motion_timeout": None, "safety_hold": None}}
        self.record = record
        # Attach first: even serialization or writing failures retain the locator.
        try:
            error.run_diagnostic = record
        except (Exception, KeyboardInterrupt):
            pass  # An exception may reject attributes; the independent file still helps.
        try:
            ctx = self.context
            if ctx is not None:
                motion = ctx.__dict__.get("motion")
                if motion is not None:
                    for field in ("armed", "timeout"):
                        if field in motion.__dict__:
                            record["safety"]["motion_" + field] = motion.__dict__[field] is not None
                if "safety_hold" in ctx.__dict__:
                    record["safety"]["safety_hold"] = ctx.__dict__["safety_hold"] is not None
        except (Exception, KeyboardInterrupt):
            pass  # Optional safety metadata must not prevent recording the known location.
        try:
            target = Path(self.store.dir) / "run_diagnostic.json"
            temporary = target.with_suffix(".json.tmp")
            data = json.dumps({**record, "persisted": True}, ensure_ascii=False, allow_nan=False)
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(data)
            os.replace(temporary, target)
            record["persisted"] = True
        except (Exception, KeyboardInterrupt):
            # Secondary diagnostic faults must never mask the original failure.
            pass


def exception_diagnostic(error):
    try:
        record = getattr(error, "run_diagnostic", None)
        if isinstance(record, dict):
            json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
            return {"run_diagnostic": record}
    except (Exception, KeyboardInterrupt):
        pass
    return {}
