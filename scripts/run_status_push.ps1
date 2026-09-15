# EvoAlpha 关键节点状态卡推送入口（2026-09-11，用户裁定）。
#
# 只读观测者：消费既有产物与 task_logs，把每个时间关键节点的状态渲染成飞书卡片。
# 不参与门禁判定、不改账本、不改计划；停用本任务对交易链路零影响。
# 非交易日守卫在 status_push.py 内（读 trading_calendar 缓存），此处不重复判定。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = 'C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
$script = Join-Path $root 'scripts\status_push.py'
$logDir = Join-Path $root 'outputs\intraday'
$stdout = Join-Path $logDir 'status_push.latest.log'
$stderr = Join-Path $logDir 'status_push.latest.stderr.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$process = Start-Process -FilePath $python `
    -ArgumentList @('-X', 'utf8', $script, '--node', 'run') `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -NoNewWindow -Wait -PassThru
exit $process.ExitCode
