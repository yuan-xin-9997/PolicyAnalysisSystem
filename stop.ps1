$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $RootDir "src/logs/server.pid"
$Timeout = if ($env:POLICY_ANALYSIS_STOP_TIMEOUT_SECONDS) { [int]$env:POLICY_ANALYSIS_STOP_TIMEOUT_SECONDS } else { 20 }

if (-not (Test-Path $PidFile)) {
    Write-Host "服务未运行：PID 文件不存在"
    exit 0
}

$PidValue = Get-Content $PidFile -ErrorAction SilentlyContinue
$Process = if ($PidValue) { Get-Process -Id ([int]$PidValue) -ErrorAction SilentlyContinue } else { $null }
if (-not $Process) {
    Remove-Item $PidFile -Force
    Write-Host "服务未运行：已清理失效 PID 文件"
    exit 0
}

Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
for ($i = 0; $i -lt $Timeout; $i++) {
    Start-Sleep -Seconds 1
    if (-not (Get-Process -Id $Process.Id -ErrorAction SilentlyContinue)) {
        Remove-Item $PidFile -Force
        Write-Host "服务已停止"
        exit 0
    }
}

Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
Remove-Item $PidFile -Force
Write-Host "服务已强制停止"
