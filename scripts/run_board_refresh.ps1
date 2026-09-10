$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = 'C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
$script = Join-Path $root 'scripts\build_board.py'
$logDir = Join-Path $root 'outputs\intraday'
$stdout = Join-Path $logDir 'board_refresh.latest.log'
$stderr = Join-Path $logDir 'board_refresh.latest.stderr.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# 2026-09-10: 非交易日静默(用户裁定⑥) —— 看板刷新只在交易日运行, 零推送零日志噪音。
& $python -X utf8 (Join-Path $root 'scripts\trading_calendar.py') check | Out-Null
if ($LASTEXITCODE -eq 3) { exit 0 }

$process = Start-Process -FilePath $python `
    -ArgumentList @('-X', 'utf8', $script) `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -NoNewWindow -Wait -PassThru
exit $process.ExitCode
