@echo off
setlocal
set "RADAR_ORIGINAL_PATH=%PATH%"
set "Path="
set "PATH=%RADAR_ORIGINAL_PATH%"
set "RADAR_ORIGINAL_PATH="
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
