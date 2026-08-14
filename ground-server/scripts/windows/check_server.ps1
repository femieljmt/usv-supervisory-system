[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

Write-Host "=== USV LAPTOP SERVER CHECK ===" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"

foreach ($Port in 1883, 5000) {
    $Listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($Listening) {
        Write-Host "Port $Port : OPEN" -ForegroundColor Green
    }
    else {
        Write-Host "Port $Port : CLOSED" -ForegroundColor Red
    }
}

try {
    $Health = Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/health" -TimeoutSec 5
    Write-Host "Dashboard health: OK" -ForegroundColor Green
    $Health | ConvertTo-Json -Depth 5
}
catch {
    Write-Host "Dashboard health: FAILED - $($_.Exception.Message)" -ForegroundColor Red
}

if (Get-Command tailscale -ErrorAction SilentlyContinue) {
    try {
        $TailscaleIp = (& tailscale ip -4 | Select-Object -First 1).Trim()
        Write-Host "Tailscale IP: $TailscaleIp"
    }
    catch {
        Write-Host "Tailscale IP tidak dapat dibaca." -ForegroundColor Yellow
    }
}

$Database = Join-Path $ProjectRoot "data\database\usv_server.sqlite3"
if (Test-Path $Database) {
    $SizeMb = [math]::Round((Get-Item $Database).Length / 1MB, 2)
    Write-Host "Database: $Database ($SizeMb MB)"
}
else {
    Write-Host "Database belum dibuat; akan dibuat saat server dijalankan." -ForegroundColor Yellow
}
