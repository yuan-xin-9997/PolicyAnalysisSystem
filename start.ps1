$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $RootDir "src/logs/server.pid"
$LogFile = Join-Path $RootDir "src/logs/app.log"
$AppDir = Join-Path $RootDir "src/app/backend"
$ConfigFile = Join-Path $RootDir "src/config/app.json"
$HostName = if ($env:POLICY_ANALYSIS_SERVER__HOST) { $env:POLICY_ANALYSIS_SERVER__HOST } else { "127.0.0.1" }
$Port = if ($env:POLICY_ANALYSIS_SERVER__PORT) { [int]$env:POLICY_ANALYSIS_SERVER__PORT } else { 30080 }

if (-not $env:POLICY_ANALYSIS_SERVER__PORT -and (Test-Path $ConfigFile)) {
    try {
        $Config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if ($Config.server.port) { $Port = [int]$Config.server.port }
    } catch {}
}

New-Item -ItemType Directory -Force -Path (Join-Path $RootDir "src/logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $RootDir "src/data") | Out-Null

if (Test-Path $PidFile) {
    $ExistingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($ExistingPid -and (Get-Process -Id ([int]$ExistingPid) -ErrorAction SilentlyContinue)) {
        Write-Host "服务已在运行，PID=$ExistingPid"
        exit 0
    }
    Remove-Item $PidFile -Force
}

$Python = if (Test-Path (Join-Path $RootDir ".venv/Scripts/python.exe")) {
    Join-Path $RootDir ".venv/Scripts/python.exe"
} elseif (Test-Path (Join-Path $RootDir ".venv/bin/python")) {
    Join-Path $RootDir ".venv/bin/python"
} else {
    "python"
}

$Args = @("-m", "uvicorn", "policy_analysis.main:app", "--app-dir", $AppDir, "--host", $HostName, "--port", "$Port")
$Process = Start-Process -FilePath $Python -ArgumentList $Args -WorkingDirectory $RootDir -RedirectStandardOutput $LogFile -RedirectStandardError $LogFile -PassThru
$Process.Id | Out-File -FilePath $PidFile -Encoding ascii -Force
Write-Host "服务已启动，PID=$($Process.Id)，地址=http://${HostName}:${Port}"
