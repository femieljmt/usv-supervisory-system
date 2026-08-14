[CmdletBinding()]
param(
    [string]$MosquittoDirectory = "C:\Program Files\mosquitto",
    [switch]$UseExistingBroker
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BrokerScript = Join-Path $PSScriptRoot "run_mosquitto.ps1"
$ServerScript = Join-Path $PSScriptRoot "run_server.ps1"

$BrokerListening = Get-NetTCPConnection -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue
if ($BrokerListening -and -not $UseExistingBroker) {
    throw "Port 1883 sudah digunakan. Hentikan Mosquitto/service lain atau jalankan dengan -UseExistingBroker bila konfigurasinya memang benar."
}

if (-not $BrokerListening) {
    $BrokerCommand = "& '$BrokerScript' -MosquittoDirectory '$MosquittoDirectory'"
    Start-Process powershell.exe -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", $BrokerCommand
    ) -WorkingDirectory $ProjectRoot
    Start-Sleep -Seconds 3
}
else {
    Write-Host "Port 1883 sudah aktif; broker yang sedang berjalan akan digunakan." -ForegroundColor Yellow
}

& $ServerScript
