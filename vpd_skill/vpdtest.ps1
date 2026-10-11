<#
.SYNOPSIS
  vpdtest CLI wrapper. Usage: .\vpdtest.ps1 <command> [args...]   (JSON on stdout)
  Works in Windows PowerShell 5.1 and PowerShell 7+. Run install.ps1 first.
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Error "Virtual env not found: $py . Run .\install.ps1 first."
    exit 3
}
$prevEnc = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    if ($env:PYTHONPATH) { $env:PYTHONPATH = "$root;$env:PYTHONPATH" } else { $env:PYTHONPATH = $root }
    & $py -m vpdtest @args
    $code = $LASTEXITCODE
} finally {
    [Console]::OutputEncoding = $prevEnc
}
exit $code
