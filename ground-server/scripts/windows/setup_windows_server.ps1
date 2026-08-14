[CmdletBinding()]
param(
    [string]$MosquittoDirectory = "C:\Program Files\mosquitto",
    [string]$OnboardTailscaleIp = "",
    [switch]$SkipTests,
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-PythonLauncher {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{ Command = "py"; Arguments = @("-3") }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{ Command = "python"; Arguments = @() }
    }
    throw "Python tidak ditemukan. Instal Python 3.11+ dan aktifkan Add Python to PATH."
}

function Convert-SecureStringToPlainText([Security.SecureString]$SecureValue) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

Write-Step "Memeriksa prasyarat"
$python = Resolve-PythonLauncher
$PythonCommand = $python.Command
$PythonArguments = $python.Arguments
$MosquittoExe = Join-Path $MosquittoDirectory "mosquitto.exe"
$MosquittoPasswdExe = Join-Path $MosquittoDirectory "mosquitto_passwd.exe"

if (-not (Test-Path $MosquittoExe)) {
    throw "mosquitto.exe tidak ditemukan di $MosquittoDirectory. Instal Eclipse Mosquitto Windows terlebih dahulu."
}
if (-not (Test-Path $MosquittoPasswdExe)) {
    throw "mosquitto_passwd.exe tidak ditemukan di $MosquittoDirectory."
}

if ([string]::IsNullOrWhiteSpace($OnboardTailscaleIp)) {
    $OnboardTailscaleIp = Read-Host "IP Tailscale Raspberry Pi onboard"
}
if ([string]::IsNullOrWhiteSpace($OnboardTailscaleIp)) {
    throw "IP Tailscale Raspberry Pi onboard wajib diisi."
}

Write-Host "Project    : $ProjectRoot"
Write-Host "Mosquitto  : $MosquittoExe"
Write-Host "Onboard IP : $OnboardTailscaleIp"

if (Test-IsAdministrator) {
    $MosquittoService = Get-Service -Name "mosquitto" -ErrorAction SilentlyContinue
    if ($MosquittoService) {
        if ($MosquittoService.Status -eq "Running") {
            Write-Host "Menghentikan service Mosquitto bawaan agar port 1883 dapat memakai konfigurasi proyek." -ForegroundColor Yellow
            Stop-Service -Name "mosquitto" -Force
        }
        Set-Service -Name "mosquitto" -StartupType Manual
    }
}

Write-Step "Membuat virtual environment Windows"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $PythonCommand @PythonArguments -m venv .venv
}
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt
if (-not $SkipTests) {
    & $VenvPython -m pip install pytest
}

Write-Step "Membuat konfigurasi MQTT dan server"
$DefaultUser = "usv_backend"
$MqttUser = Read-Host "Username MQTT [$DefaultUser]"
if ([string]::IsNullOrWhiteSpace($MqttUser)) {
    $MqttUser = $DefaultUser
}
$SecurePassword = Read-Host "Password MQTT" -AsSecureString
$MqttPassword = Convert-SecureStringToPlainText $SecurePassword
if ([string]::IsNullOrWhiteSpace($MqttPassword)) {
    throw "Password MQTT tidak boleh kosong."
}
$EnvUser = $MqttUser.Replace('\', '\\').Replace('"', '\"')
$EnvPassword = $MqttPassword.Replace('\', '\\').Replace('"', '\"')

New-Item -ItemType Directory -Force -Path "config", "data\database", "data\logs", "mosquitto\data", "mosquitto\log" | Out-Null

$EnvContent = @"
APP_ENV=production

MQTT_HOST=127.0.0.1
MQTT_PORT=1883
MQTT_USERNAME="$EnvUser"
MQTT_PASSWORD="$EnvPassword"
MQTT_KEEPALIVE=30
MQTT_QOS=1

DATABASE_PATH=data/database/usv_server.sqlite3

DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=5000
DASHBOARD_REFRESH_SECONDS=2.0
DASHBOARD_TRACK_LIMIT=500
DASHBOARD_LIVE_SECONDS=5.0
DASHBOARD_STALE_SECONDS=15.0

TERMINAL_SUMMARY_INTERVAL=10
"@
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $ProjectRoot "config\.env"), $EnvContent, $Utf8NoBom)

$PasswordFile = (Join-Path $ProjectRoot "mosquitto\passwd") -replace '\\','/'
$AclFile = (Join-Path $ProjectRoot "mosquitto\acl") -replace '\\','/'
$PersistenceDir = ((Join-Path $ProjectRoot "mosquitto\data") -replace '\\','/') + "/"
$LogFile = (Join-Path $ProjectRoot "mosquitto\log\mosquitto.log") -replace '\\','/'
$MosquittoConfigPath = Join-Path $ProjectRoot "mosquitto\mosquitto-windows.conf"

& $MosquittoPasswdExe -b -c (Join-Path $ProjectRoot "mosquitto\passwd") $MqttUser $MqttPassword
if ($LASTEXITCODE -ne 0) {
    throw "Gagal membuat password file Mosquitto."
}

$AclContent = @"
user $MqttUser
topic readwrite usv/+/telemetry
topic readwrite usv/+/waypoints
topic readwrite usv/+/ack
"@
Set-Content -Path "mosquitto\acl" -Value $AclContent -Encoding ASCII

$MosquittoContent = @"
per_listener_settings true
listener 1883 0.0.0.0
allow_anonymous false
password_file $PasswordFile
acl_file $AclFile

persistence true
persistence_location $PersistenceDir
persistence_file mosquitto.db
autosave_interval 60

log_dest file $LogFile
log_type error
log_type warning
log_type notice
connection_messages true

max_inflight_messages 100
max_queued_messages 10000
"@
Set-Content -Path $MosquittoConfigPath -Value $MosquittoContent -Encoding ASCII

Write-Step "Menyiapkan firewall"
if (-not $SkipFirewall) {
    if (-not (Test-IsAdministrator)) {
        Write-Warning "PowerShell tidak dijalankan sebagai Administrator. Rule firewall dilewati."
        Write-Warning "Jalankan scripts\windows\configure_firewall.ps1 sebagai Administrator."
    }
    else {
        & (Join-Path $PSScriptRoot "configure_firewall.ps1") -OnboardTailscaleIp $OnboardTailscaleIp
    }
}

if (-not $SkipTests) {
    Write-Step "Menjalankan unit test"
    & $VenvPython -m pytest -q tests
    if ($LASTEXITCODE -ne 0) {
        throw "Unit test gagal. Server belum boleh digunakan untuk pengujian."
    }
}

Write-Step "Setup selesai"
$TailscaleIp = $null
if (Get-Command tailscale -ErrorAction SilentlyContinue) {
    try {
        $TailscaleIp = (& tailscale ip -4 | Select-Object -First 1).Trim()
    }
    catch {
        $TailscaleIp = $null
    }
}

Write-Host "Jalankan broker + server:" -ForegroundColor Green
Write-Host "  .\scripts\windows\run_all.ps1"
Write-Host "Dashboard lokal: http://127.0.0.1:5000"
if ($TailscaleIp) {
    Write-Host "IP Tailscale laptop: $TailscaleIp" -ForegroundColor Yellow
    Write-Host "Set MQTT_HOST=$TailscaleIp pada config/.env Raspberry Pi onboard."
}
else {
    Write-Host "Jalankan 'tailscale ip -4' untuk memperoleh IP server laptop." -ForegroundColor Yellow
}
Write-Host "Jangan menjalankan server Raspberry Pi Lab secara bersamaan."
