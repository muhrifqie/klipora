<#
.SYNOPSIS
  Klipora install helper: checks Python + FFmpeg, links the panel into Premiere Pro (CEP junction) and, after you
  confirm, enables unsigned CEP panels (PlayerDebugMode for CSXS.12).

.DESCRIPTION
  Run it yourself from the repo root in a normal (non-admin) PowerShell:
      powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
  Every change is printed first and asked for with Read-Host. Nothing is downloaded and nothing outside your own
  Windows user profile (HKCU and %APPDATA%) is changed.

  Undo:
      cmd /c rmdir "%APPDATA%\Adobe\CEP\extensions\com.klipora.panel"
      reg add "HKCU\Software\Adobe\CSXS.12" /v PlayerDebugMode /t REG_SZ /d 0 /f
#>
[CmdletBinding()]
param(
    [switch]$InstallPackages   # also run "pip install -r requirements.txt" (asks first)
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Panel = Join-Path $Repo 'panel'
$ExtRoot = Join-Path $env:APPDATA 'Adobe\CEP\extensions'
$Link = Join-Path $ExtRoot 'com.klipora.panel'
$CsxsKey = 'HKCU:\Software\Adobe\CSXS.12'

function Ask([string]$Question) {
    $a = Read-Host "$Question [y/N]"
    return $a -match '^(y|yes|j|ya)$'
}

Write-Host "Klipora install helper" -ForegroundColor Cyan
Write-Host "Repo:  $Repo"
Write-Host ""

# ---------------------------------------------------------------- 1. checks (read-only)
Write-Host "1. Checking requirements" -ForegroundColor Cyan
$py = $null
foreach ($cand in @('py', 'python')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $ver = & $cand -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver) { $py = $cand; break }
        } catch { }
    }
}
if ($py) {
    $parts = $ver.Split('.')
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 12)) {
        Write-Host "   Python $ver found ($py), but 3.12 or newer is needed." -ForegroundColor Yellow
    } else {
        Write-Host "   Python $ver ($py)  OK" -ForegroundColor Green
    }
} else {
    Write-Host "   Python not found. Install Python 3.12+ (tested 3.14): winget install Python.Python.3.14" -ForegroundColor Yellow
}

$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ff) {
    $line = (& ffmpeg -hide_banner -version 2>$null | Select-Object -First 1)
    $cfg = (& ffmpeg -hide_banner -version 2>$null) -join ' '
    Write-Host "   $line  OK" -ForegroundColor Green
    if ($cfg -notmatch 'enable-libass') {
        Write-Host "   FFmpeg has no libass: burned-in captions will not work. Use a full build (winget install Gyan.FFmpeg)." -ForegroundColor Yellow
    }
} else {
    Write-Host "   FFmpeg not found on PATH. Install a full build: winget install Gyan.FFmpeg (then open a new terminal)." -ForegroundColor Yellow
}

$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    Write-Host "   NVIDIA GPU driver found (optional): install requirements-gpu.txt for faster transcription." -ForegroundColor Green
} else {
    Write-Host "   No NVIDIA driver found (optional): transcription will use the CPU." -ForegroundColor DarkGray
}
Write-Host ""

# ---------------------------------------------------------------- 2. Python packages (optional)
if ($InstallPackages -and $py) {
    Write-Host "2. Python packages" -ForegroundColor Cyan
    Write-Host "   Will run: $py -m pip install --no-cache-dir -r `"$Repo\requirements.txt`""
    if (Ask "   Install the engine packages now?") {
        & $py -m pip install --no-cache-dir -r (Join-Path $Repo 'requirements.txt')
    } else { Write-Host "   Skipped." }
    Write-Host ""
}

# ---------------------------------------------------------------- 3. CEP junction
Write-Host "3. Link the panel into Premiere Pro" -ForegroundColor Cyan
if (Test-Path $Link) {
    $item = Get-Item $Link -Force
    if ($item.LinkType -eq 'Junction' -or $item.LinkType -eq 'SymbolicLink') {
        Write-Host "   Already linked: $Link -> $($item.Target)" -ForegroundColor Green
    } else {
        Write-Host "   $Link exists and is not a link. Remove or rename it yourself, then run this again." -ForegroundColor Yellow
    }
} else {
    Write-Host "   Will create the junction:"
    Write-Host "     $Link"
    Write-Host "     -> $Panel"
    if (Ask "   Create it?") {
        New-Item -ItemType Directory -Force $ExtRoot | Out-Null
        New-Item -ItemType Junction -Path $Link -Target $Panel | Out-Null
        Write-Host "   Linked." -ForegroundColor Green
    } else { Write-Host "   Skipped." }
}
Write-Host ""

# ---------------------------------------------------------------- 4. PlayerDebugMode
Write-Host "4. Allow unsigned CEP panels (Premiere Pro 26.x = CSXS.12)" -ForegroundColor Cyan
$cur = $null
try { $cur = (Get-ItemProperty -Path $CsxsKey -Name PlayerDebugMode -ErrorAction Stop).PlayerDebugMode } catch { }
if ($cur -eq '1') {
    Write-Host "   PlayerDebugMode is already 1." -ForegroundColor Green
} else {
    Write-Host "   Will set the registry value (current user only, no admin needed):"
    Write-Host "     HKCU\Software\Adobe\CSXS.12  PlayerDebugMode = 1  (REG_SZ)"
    Write-Host "   This lets Premiere load unsigned CEP extensions such as Klipora. Undo: set it back to 0."
    if (Ask "   Set it?") {
        if (-not (Test-Path $CsxsKey)) { New-Item -Path $CsxsKey -Force | Out-Null }
        New-ItemProperty -Path $CsxsKey -Name PlayerDebugMode -Value '1' -PropertyType String -Force | Out-Null
        Write-Host "   Done." -ForegroundColor Green
    } else { Write-Host "   Skipped." }
}
Write-Host ""
Write-Host "Next: restart Premiere Pro, open Window > Extensions > Klipora, then Settings > Engine > Test." -ForegroundColor Cyan
