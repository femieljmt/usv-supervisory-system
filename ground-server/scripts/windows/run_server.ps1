[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$EnvFile = Join-Path $ProjectRoot "config\.env"

if (-not (Test-Path $Python)) {
    throw ".venv Windows belum tersedia. Jalankan setup_windows_server.ps1."
}
if (-not (Test-Path $EnvFile)) {
    throw "config/.env belum tersedia. Jalankan setup_windows_server.ps1."
}

$Existing = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
    throw "Port 5000 sudah digunakan. Pastikan hanya satu server.app berjalan."
}

Set-Location $ProjectRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
& $Python -m server.app
