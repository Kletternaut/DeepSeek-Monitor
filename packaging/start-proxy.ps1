# start-proxy.ps1
# ================
# Starts the DeepSeek proxy (server + web dashboard) from the built .exe.
#
#   - Loads deepseek-proxy\influx.env (if present) so the InfluxDB settings
#     reach the proxy as environment variables (on Linux this is done by the
#     systemd unit via EnvironmentFile=).
#   - All additional arguments are forwarded to DeepSeek-Proxy.exe, e.g.:
#       .\packaging\start-proxy.ps1 --port 9000
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File packaging\start-proxy.ps1 [--port 8000]

$ErrorActionPreference = "Stop"

# Repository root (parent of the "packaging" folder).
$Root = Split-Path $PSScriptRoot -Parent
$Exe  = Join-Path $Root "dist\DeepSeek-Proxy.exe"

if (-not (Test-Path $Exe)) {
    Write-Error "DeepSeek-Proxy.exe not found. Run packaging\build-windows.ps1 first."
    exit 1
}

# Load influx.env if it exists (KEY=VALUE lines, # comments ignored).
$EnvFile = Join-Path $Root "deepseek-proxy\influx.env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $key, $value = $line -split "=", 2
            [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim(), "Process")
        }
    }
    Write-Host "Loaded influx.env"
}

Write-Host "Starting DeepSeek-Proxy.exe ..."
& $Exe @args
