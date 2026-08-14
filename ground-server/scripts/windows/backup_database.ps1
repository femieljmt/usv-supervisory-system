[CmdletBinding()]
param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Source = Join-Path $ProjectRoot "data\database\usv_server.sqlite3"

if (-not (Test-Path $Python)) {
    throw "Virtual environment belum tersedia."
}
if (-not (Test-Path $Source)) {
    throw "Database tidak ditemukan: $Source"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ProjectRoot "backups"
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Destination = Join-Path $OutputDirectory "usv_server_$Timestamp.sqlite3"

$PythonCode = @'
import sqlite3
import sys
from pathlib import Path
src = Path(sys.argv[1])
dst = Path(sys.argv[2])
source = sqlite3.connect(src)
target = sqlite3.connect(dst)
try:
    with target:
        source.backup(target)
finally:
    target.close()
    source.close()
check = sqlite3.connect(f"file:{dst.resolve()}?mode=ro", uri=True)
try:
    result = check.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    check.close()
if result != "ok":
    raise SystemExit(f"Integrity check gagal: {result}")
print(dst)
'@

$PythonCode | & $Python - $Source $Destination
if ($LASTEXITCODE -ne 0) {
    throw "Backup database gagal."
}
Write-Host "Backup berhasil: $Destination" -ForegroundColor Green
