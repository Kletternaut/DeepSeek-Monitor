# build-windows.ps1
# ==================
# Builds the standalone Windows .exe files for the DeepSeek Monitor and Proxy.
#
# Prerequisites (Windows 11, PowerShell 5.1+):
#   1. Install Python 3.11 or 3.12 from https://www.python.org/downloads/
#      IMPORTANT: tick "Add python.exe to PATH" and install the "tcl/tk" option
#      (enabled by default) -- the Monitor is a Tkinter app.
#   2. Run from the repository root:
#        powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
#
# Output: dist\DeepSeek-Monitor.exe  and  dist\DeepSeek-Proxy.exe

$ErrorActionPreference = "Stop"

# Repository root (parent of the "packaging" folder).
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

Write-Host "==> Python:" -ForegroundColor Cyan
python --version

Write-Host "==> Installing build dependencies" -ForegroundColor Cyan
python -m pip install --upgrade pip pyinstaller
python -m pip install -r deepseek-proxy\requirements.txt
# tzdata is required for zoneinfo on Windows (no system IANA tz database).
python -m pip install tzdata

Write-Host "==> Building DeepSeek-Monitor.exe (GUI)" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "DeepSeek-Monitor" `
    --collect-data tzdata `
    --icon assets\deepseek-monitor-256.png `
    deepseek_rates_v2.4.0.py

Write-Host "==> Building DeepSeek-Proxy.exe (server + web dashboard)" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean --onefile --console `
    --name "DeepSeek-Proxy" `
    --collect-data tzdata `
    --collect-submodules uvicorn `
    --hidden-import influxdb_client `
    --hidden-import tkinter `
    --hidden-import proxy.tkinter_view `
    --hidden-import pystray `
    --hidden-import PIL.Image `
    --hidden-import PIL.ImageDraw `
    --hidden-import deepseek_monitor.config `
    --icon assets\deepseek-proxy-256.png `
    deepseek-proxy\main.py

Write-Host ""
Write-Host "Done. Binaries are in: $Root\dist\" -ForegroundColor Green
