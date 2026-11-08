"""HDC inherits operating settings, never host-only PIN secret environment values.

All values are synthetic. Only a local Python child is started; never HDC or a device.
"""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from vpdtest.devices.harmony_hdc import HdcDevice


@pytest.mark.parametrize("discovery", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "oserror"])
def test_every_hdc_process_excludes_pin_secrets_without_mutating_host(monkeypatch, discovery, outcome):
    values = {
        "VPD_SECRET_LOCK_PIN": "synthetic-primary",
        "VPD_SECRET_LOCK_PIN_TEST": "synthetic-pin-device",
        "vpd_secret_lock_pin_lower": "synthetic-case-insensitive",
        "VPD_SECRET_LOCK_PIN_": "synthetic-empty-suffix",
        "VPD_SECRET_LOCK_PINNER": "unrelated-prefix",
        "VPD_SECRET_ACCOUNT": "unrelated-host-secret",
        "HDC_SERVER_PORT": "8711",
        "PATH": "/synthetic/tools",
    }
    monkeypatch.setattr(os, "environ", values.copy())
    before = dict(os.environ)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        env = kwargs.get("env", os.environ)
        assert env == {k: v for k, v in before.items()
                       if not (k.upper() == "VPD_SECRET_LOCK_PIN" or
                               k.upper().startswith("VPD_SECRET_LOCK_PIN_"))}
        assert env is not os.environ
        assert all(value not in command for key, value in values.items() if "PIN" in key.upper())
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if outcome == "oserror":
            raise OSError("synthetic launch error")
        return SimpleNamespace(returncode=0 if outcome == "success" else 1,
                               stdout=b"synthetic-device\n" if discovery else b"ok\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    if discovery:
        dev = HdcDevice()  # _first_target must use the same environment boundary.
        error = dev.target_discovery_error
        assert (error is None) == (outcome == "success")
    else:
        dev = HdcDevice("synthetic-device")
        result = dev._run(["shell", "synthetic-command"], "synthetic")
        assert result.ok == (outcome == "success")
    assert len(calls) == 1
    assert dict(os.environ) == before
    assert calls[0][0][:1] == ["hdc"]
    assert ("-t" in calls[0][0]) is (not discovery)


def test_child_environment_is_fresh_and_pin_exclusion_does_not_cache_values(monkeypatch):
    environments = []
    monkeypatch.setattr(os, "environ", {})

    def run(command, **kwargs):
        environments.append(kwargs.get("env", os.environ))
        return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    dev = HdcDevice("synthetic-device")
    monkeypatch.setenv("VPD_SECRET_LOCK_PIN", "synthetic-first")
    monkeypatch.setenv("HDC_SERVER_PORT", "8711")
    dev._run(["shell", "synthetic-command"], "synthetic")
    monkeypatch.setenv("VPD_SECRET_LOCK_PIN", "synthetic-second")
    monkeypatch.setenv("HDC_SERVER_PORT", "8712")
    dev._run(["shell", "synthetic-command"], "synthetic")
    assert [env["HDC_SERVER_PORT"] for env in environments] == ["8711", "8712"]
    assert all("VPD_SECRET_LOCK_PIN" not in env for env in environments)
    assert environments[0] is not environments[1]


def test_environment_filter_preserves_missing_tool_receipt(monkeypatch):
    monkeypatch.setattr(os, "environ", {})
    def run(command, **kwargs):
        assert "VPD_SECRET_LOCK_PIN_TEST" not in kwargs.get("env", os.environ)
        raise FileNotFoundError

    monkeypatch.setenv("VPD_SECRET_LOCK_PIN_TEST", "synthetic-only")
    monkeypatch.setattr(subprocess, "run", run)
    result = HdcDevice("synthetic-device")._run(["shell", "synthetic-command"], "synthetic")
    assert result.sent is False and result.ok is False and result.error_class == "tool_missing"


def test_real_local_child_receives_no_pin_environment(monkeypatch):
    # Exercise OS inheritance with Python, not HDC. No real credential is read.
    env = {"PATH": os.defpath, "VPD_SECRET_LOCK_PIN": "synthetic-primary",
           "VPD_SECRET_LOCK_PIN_TEST": "synthetic-secondary"}
    if "SystemRoot" in os.environ:
        env["SystemRoot"] = os.environ["SystemRoot"]
    monkeypatch.setattr(os, "environ", env)
    dev = HdcDevice("synthetic-device", hdc=sys.executable)
    result = dev._run(["-c", "import os; print(any(k.upper().startswith('VPD_SECRET_LOCK_PIN') for k in os.environ))"],
                      "synthetic_child", with_target=False)
    assert result.ok and result.raw.strip() == "False"
    assert env["VPD_SECRET_LOCK_PIN"] == "synthetic-primary"
