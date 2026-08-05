$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $RootDir "src/logs/server.pid"
$ConfigFile = Join-Path $RootDir "src/config/app.json"
$HostName = if ($env:POLICY_ANALYSIS_SERVER__HOST) { $env:POLICY_ANALYSIS_SERVER__HOST } else { "127.0.0.1" }
$Port = if ($env:POLICY_ANALYSIS_SERVER__PORT) { [int]$env:POLICY_ANALYSIS_SERVER__PORT } else { 30080 }

if (-not $env:POLICY_ANALYSIS_SERVER__PORT -and (Test-Path $ConfigFile)) {
    try {
        $Config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if ($Config.server.port) { $Port = [int]$Config.server.port }
    } catch {}
}

if (-not (Test-Path $PidFile)) {
    Write-Host "服务未运行：PID 文件不存在"
    exit 1
}

$PidValue = Get-Content $PidFile -ErrorAction SilentlyContinue
$Process = if ($PidValue) { Get-Process -Id ([int]$PidValue) -ErrorAction SilentlyContinue } else { $null }
if (-not $Process) {
    Remove-Item $PidFile -Force
    Write-Host "服务未运行：PID 无效"
    exit 1
}

try {
    Invoke-WebRequest -Uri "http://${HostName}:${Port}/health/ready" -UseBasicParsing -TimeoutSec 5 | Out-Null
    Write-Host "服务运行中，PID=$PidValue，健康检查通过"
    exit 0
} catch {
    Write-Host "服务进程存在，PID=$PidValue，但健康检查未通过"
    exit 2
}
