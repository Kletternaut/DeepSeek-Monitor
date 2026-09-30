# install-proxy-service.ps1
# ==========================
# Registers the DeepSeek proxy as a Windows scheduled task (autostart).
#
# By default the task starts at LOGON of the current user, so the proxy writes
# its SQLite DB to the user's %LOCALAPPDATA%\deepseek\usage.db (the same DB the
# monitor reads). Use -AtStartup to start at boot instead -- but then the task
# runs as SYSTEM, whose profile differs, so the DB is written to the SYSTEM
# profile instead of the user's. Prefer -AtLogOn (the default) unless you know
# you need boot-time startup.
#
# Usage (PowerShell as the target user):
#   powershell -ExecutionPolicy Bypass -File packaging\install-proxy-service.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\install-proxy-service.ps1 -AtStartup -Port 9000
#
# Remove again:
#   Unregister-ScheduledTask -TaskName "DeepSeek-Proxy" -Confirm:$false

param(
    [switch]$AtStartup,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$Exe  = Join-Path $Root "dist\DeepSeek-Proxy.exe"

if (-not (Test-Path $Exe)) {
    Write-Error "DeepSeek-Proxy.exe not found. Run packaging\build-windows.ps1 first."
    exit 1
}

$TaskName = "DeepSeek-Proxy"
$Action = New-ScheduledTaskAction -Execute $Exe -Argument "--port $Port" -WorkingDirectory $Root

if ($AtStartup) {
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Write-Host "Registering '$TaskName' to start at BOOT (SYSTEM)." -ForegroundColor Yellow
    Write-Host "WARNING: SYSTEM has its own profile -> the SQLite DB is written to the" -ForegroundColor Yellow
    Write-Host "         SYSTEM profile, NOT the user's %LOCALAPPDATA% (monitor may not see it)." -ForegroundColor Yellow
} else {
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Write-Host "Registering '$TaskName' to start at LOGON of $env:USERNAME." -ForegroundColor Cyan
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Description "DeepSeek-Anthropic proxy (port $Port)"

Write-Host "Done. The proxy will start automatically." -ForegroundColor Green
