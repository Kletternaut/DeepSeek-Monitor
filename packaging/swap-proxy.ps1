# swap-proxy.ps1
# ===============
# Ersetzt die laufende DeepSeek-Proxy.exe durch die neu gebaute Version
# aus dist-new\ und startet sie wieder (mit --tray, wie zuvor).
#
# WICHTIG: Dieser Schritt trennt kurz die Proxy-Verbindung (inkl. eines
# darueber laufenden Copilot-Chats). Daher einmalig manuell ausfuehren.
#
#   powershell -ExecutionPolicy Bypass -File packaging\swap-proxy.ps1

$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$NewExe = Join-Path $Root "dist-new\DeepSeek-Proxy.exe"
$OldExe = Join-Path $Root "dist\DeepSeek-Proxy.exe"

if (-not (Test-Path $NewExe)) {
    Write-Error "dist-new\DeepSeek-Proxy.exe nicht gefunden. Erst bauen."
    exit 1
}

Write-Host "==> Proxy stoppen" -ForegroundColor Cyan
Get-Process -Name "DeepSeek-Proxy" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 800

Write-Host "==> Neue EXE kopieren" -ForegroundColor Cyan
Copy-Item $NewExe $OldExe -Force

Write-Host "==> Proxy neu starten (--tray)" -ForegroundColor Cyan
Start-Process $OldExe -ArgumentList "--tray"

Write-Host "Fertig. Proxy laeuft mit neuem Stand." -ForegroundColor Green
