[CmdletBinding()]
param(
    [string]$MosquittoDirectory = "C:\Program Files\mosquitto"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$MosquittoExe = Join-Path $MosquittoDirectory "mosquitto.exe"
$Config = Join-Path $ProjectRoot "mosquitto\mosquitto-windows.conf"

if (-not (Test-Path $MosquittoExe)) {
    throw "mosquitto.exe tidak ditemukan: $MosquittoExe"
}
if (-not (Test-Path $Config)) {
    throw "Konfigurasi Mosquitto belum ada. Jalankan setup_windows_server.ps1."
}

$Existing = Get-NetTCPConnection -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
    throw "Port 1883 sudah digunakan. Hentikan broker/server MQTT lain terlebih dahulu."
}

Set-Location $ProjectRoot
Write-Host "Mosquitto starting at 0.0.0.0:1883" -ForegroundColor Cyan
Write-Host "Config: $Config"
& $MosquittoExe -c $Config -v
