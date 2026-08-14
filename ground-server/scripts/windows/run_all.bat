@echo off
set SCRIPT_DIR=%~dp0
powershell.exe -NoExit -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_all.ps1"
