$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = 'C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
$script = Join-Path $root 'scripts\tick_monitor.py'
$logDir = Join-Path $root 'outputs\intraday'
$stdout = Join-Path $logDir 'tick_daemon.latest.log'
$stderr = Join-Path $logDir 'tick_daemon.latest.stderr.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$process = Start-Process -FilePath $python `
    -ArgumentList @('-X', 'utf8', $script, '--daemon', '--interval', '5') `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -NoNewWindow -Wait -PassThru
exit $process.ExitCode
