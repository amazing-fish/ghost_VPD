<#
.SYNOPSIS
  Install vpd_skill on a Windows workstation (Windows PowerShell 5.1 or PowerShell 7+).
.PARAMETER Python
  Python 3.12+ executable used to create .venv (default: python on PATH).
.PARAMETER LinkSkill
  Create a directory junction so the host agent can load SKILL.md.
.PARAMETER SkillDir
  Junction path. Default: %USERPROFILE%\.claude\skills\vpd-app-test (Claude Code user skills).
.PARAMETER IndexUrl
  Optional pip index (company mirror).
.EXAMPLE
  .\install.ps1
  .\install.ps1 -LinkSkill
  .\install.ps1 -Python D:\Tool\Python312\python.exe -IndexUrl https://mirror.example/simple
#>
param(
    [string]$Python = 'python',
    [switch]$LinkSkill,
    [string]$SkillDir = (Join-Path $env:USERPROFILE '.claude\skills\vpd-app-test'),
    [string]$IndexUrl
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Get-SupportedPythonVersion([string]$Executable) {
    $ver = & $Executable -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($LASTEXITCODE -ne 0) { throw "Python version check failed: $Executable" }
    if ($ver -notmatch '^([0-9]+)\.([0-9]+)$') { throw "Invalid Python version from ${Executable}: $ver" }
    if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 12)) {
        throw "Python 3.12+ required by pinned requirements; got $ver from $Executable. Select a supported Python with -Python. If this is .venv, preserve it elsewhere and rerun install.ps1 to recreate it."
    }
    return $ver
}

Write-Host '[1/5] Checking Python...'
$ver = Get-SupportedPythonVersion $Python
Write-Host "      Python $ver"

Write-Host '[2/5] Creating .venv and installing pinned requirements...'
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    & $Python -m venv (Join-Path $root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Creating .venv failed' }
}
$venvVer = Get-SupportedPythonVersion $venvPy
Write-Host "      .venv Python $venvVer"
$pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $root 'requirements.txt'))
if ($IndexUrl) { $pipArgs += @('-i', $IndexUrl) }
& $venvPy @pipArgs
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

Write-Host '[3/5] Local config...'
$local = Join-Path $root 'config.local.json'
if (-not (Test-Path $local)) {
    Copy-Item (Join-Path $root 'config.example.json') $local
    Write-Host "      Created $local (no default device: pass --device, or add `"device`": `"hdc:<serial>`")"
} else { Write-Host "      Keeping existing $local" }

Write-Host '[4/5] Checking hdc...'
$hdc = Get-Command hdc -ErrorAction SilentlyContinue
if ($hdc) { Write-Host "      hdc: $($hdc.Source)" } else { Write-Warning 'hdc not on PATH: real-device runs will be blocked (offline replay still works).' }

if ($LinkSkill) {
    Write-Host "[5/5] Linking skill -> $SkillDir"
    $parent = Split-Path $SkillDir -Parent
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force $parent | Out-Null }
    if (Test-Path $SkillDir) {
        $item = Get-Item $SkillDir -Force
        if ($item.LinkType -ne 'Junction') { throw "$SkillDir exists and is not a junction; remove it manually." }
        Write-Host '      Junction already exists'
    } else {
        New-Item -ItemType Junction -Path $SkillDir -Target $root | Out-Null
    }
} else { Write-Host '[5/5] Skill link skipped (use -LinkSkill)' }

Write-Host ''
& (Join-Path $root 'vpdtest.ps1') doctor
