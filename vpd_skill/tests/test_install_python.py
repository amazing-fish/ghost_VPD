"""Windows installer checks; no pip, network, config or device mutations."""
import json
import shutil
import subprocess
import sys

import pytest
from vpdtest.util import SKILL_ROOT


@pytest.fixture(params=["pwsh", "powershell"])
def powershell(request):
    if sys.platform != "win32":
        pytest.skip("install.ps1 is a Windows entry point")
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} is not installed")
    return executable


def _run(powershell, script):
    return subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, check=False, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.parametrize("version,exit_code,expected", [
    ("3.11", 0, "Python 3.12+ required"),
    ("3.12", 0, "3.12"),
    ("3.13", 0, "3.13"),
    ("not-a-version", 0, "Invalid Python version"),
    ("3.12", 1, "Python version check failed"),
])
def test_python_check_handles_version_and_query_failure(tmp_path, powershell, version, exit_code, expected):
    # Parse only the actual helper definition; importing install.ps1 would install packages.
    installer = str(SKILL_ROOT / "install.ps1").replace("'", "''")
    script = tmp_path / "check.ps1"
    script.write_text(f"""
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{installer}', [ref]$null, [ref]$null)
$helper = $ast.Find({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-SupportedPythonVersion' }}, $false)
Invoke-Expression $helper.Extent.Text
function TestPython {{ $global:LASTEXITCODE = {exit_code}; '{version}' }}
try {{ $result = @{{ ok = $true; value = (Get-SupportedPythonVersion TestPython) }} }}
catch {{ $result = @{{ ok = $false; value = $_.Exception.Message }} }}
$result | ConvertTo-Json -Compress
""", encoding="utf-8")
    result = _run(powershell, script)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["ok"] == (exit_code == 0 and version in ("3.12", "3.13"))
    assert expected in out["value"]


@pytest.mark.parametrize("version,expected", [
    ("3.11", "Python 3.12+ required"),
    ("3.12", "Creating .venv failed"),
])
def test_installer_stops_before_pip_and_config(tmp_path, powershell, version, expected):
    shutil.copy(SKILL_ROOT / "install.ps1", tmp_path / "install.ps1")
    fake = tmp_path / "python.ps1"
    fake.write_text(f"""
if ($args[0] -eq '-c') {{ '{version}'; exit 0 }}
if ($args[0] -eq '-m' -and $args[1] -eq 'venv') {{ exit 1 }}
throw 'unexpected invocation after a failed check'
""", encoding="utf-8")
    script = tmp_path / "run.ps1"
    script.write_text("$ErrorActionPreference = 'Stop'\n"
                      "& (Join-Path $PSScriptRoot 'install.ps1') -Python (Join-Path $PSScriptRoot 'python.ps1')\n",
                      encoding="utf-8")
    result = _run(powershell, script)
    assert result.returncode != 0
    assert expected in result.stderr
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "config.local.json").exists()
