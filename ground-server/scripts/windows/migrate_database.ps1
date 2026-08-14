[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Destination = Join-Path $ProjectRoot "data\database\usv_server.sqlite3"

if (-not (Test-Path $Python)) {
    throw "Virtual environment belum tersedia. Jalankan setup terlebih dahulu."
}
$ResolvedSource = (Resolve-Path $SourcePath).Path
if ($ResolvedSource -eq $Destination) {
    throw "Source dan destination tidak boleh sama."
}
if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) {
    throw "Port 5000 aktif. Hentikan server.app sebelum migrasi database."
}

New-Item -ItemType Directory -Force -Path (Split-Path $Destination) | Out-Null
if (Test-Path $Destination) {
    $Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OldBackup = "$Destination.before_migration_$Timestamp"
    Copy-Item $Destination $OldBackup
    Write-Host "Database lama dicadangkan: $OldBackup" -ForegroundColor Yellow
}

$PythonCode = @'
import sqlite3
import sys
from pathlib import Path
src = Path(sys.argv[1])
dst = Path(sys.argv[2])
check = sqlite3.connect(f"file:{src.resolve()}?mode=ro", uri=True)
try:
    result = check.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    check.close()
if result != "ok":
    raise SystemExit(f"Source database rusak: {result}")
source = sqlite3.connect(src)
target = sqlite3.connect(dst)
try:
    with target:
        source.backup(target)
finally:
    target.close()
    source.close()
print(dst)
'@

$PythonCode | & $Python - $ResolvedSource $Destination
if ($LASTEXITCODE -ne 0) {
    throw "Migrasi database gagal."
}
Write-Host "Migrasi berhasil: $Destination" -ForegroundColor Green
