<#
.SYNOPSIS
  Install vpd_skill on a Windows workstation (Windows PowerShell 5.1 or PowerShell 7+).
.PARAMETER Python
  Python 3.9+ executable used to create .venv (default: python on PATH).
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

Write-Host '[1/5] Checking Python...'
$ver = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($LASTEXITCODE -ne 0) { throw "Python not found: $Python" }
$parts = $ver.Split('.')
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 9)) { throw "Python 3.9+ required, got $ver" }
Write-Host "      Python $ver"

Write-Host '[2/5] Creating .venv and installing pinned requirements...'
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { & $Python -m venv (Join-Path $root '.venv') }
$pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $root 'requirements.txt'))
if ($IndexUrl) { $pipArgs += @('-i', $IndexUrl) }
& $venvPy @pipArgs
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

Write-Host '[3/5] Local config...'
$local = Join-Path $root 'config.local.json'
if (-not (Test-Path $local)) {
    Copy-Item (Join-Path $root 'config.example.json') $local
    Write-Host "      Created $local (edit 'device' to hdc:<serial> for a real device)"
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
