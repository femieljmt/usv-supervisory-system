[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Database = Join-Path $ProjectRoot "data\database\usv_server.sqlite3"

if (-not (Test-Path $Python)) { throw "Virtual environment belum tersedia." }
if (-not (Test-Path $Database)) { throw "Database tidak ditemukan: $Database" }

$Code = @'
import sqlite3
import sys
from pathlib import Path
p = Path(sys.argv[1])
conn = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True)
try:
    print("Integrity:", conn.execute("PRAGMA integrity_check").fetchone()[0])
    for table in [
        "telemetry_records",
        "delivery_receipts",
        "mission_plans",
        "mission_waypoints",
        "latest_mission_plan",
    ]:
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except sqlite3.Error:
            count = "NOT FOUND"
        print(f"{table}: {count}")
finally:
    conn.close()
'@

$Code | & $Python - $Database
