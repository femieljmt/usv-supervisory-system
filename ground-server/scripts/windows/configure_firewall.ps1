[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OnboardTailscaleIp,
    [switch]$AllowRemoteDashboard,
    [string]$DashboardRemoteAddress = "100.64.0.0/10"
)

$ErrorActionPreference = "Stop"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Jalankan PowerShell sebagai Administrator."
    }
}

Assert-Administrator

$MqttRule = "USV MQTT from Onboard Tailscale"
Get-NetFirewallRule -DisplayName $MqttRule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule `
    -DisplayName $MqttRule `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 1883 `
    -RemoteAddress $OnboardTailscaleIp `
    -Profile Any | Out-Null
Write-Host "Firewall MQTT diizinkan hanya dari $OnboardTailscaleIp" -ForegroundColor Green

$DashboardRule = "USV Dashboard over Tailscale"
Get-NetFirewallRule -DisplayName $DashboardRule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($AllowRemoteDashboard) {
    New-NetFirewallRule `
        -DisplayName $DashboardRule `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 5000 `
        -RemoteAddress $DashboardRemoteAddress `
        -Profile Any | Out-Null
    Write-Host "Dashboard remote diizinkan dari $DashboardRemoteAddress" -ForegroundColor Green
}
else {
    Write-Host "Port dashboard 5000 tidak dibuka. Gunakan http://127.0.0.1:5000 pada laptop."
}
