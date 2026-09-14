# Register YaobanLoopEngine (16:35) - loop engineering state check
#
# 2026-09-14 (user ruling, scheduled-task audit): the scheduled task YaobanLoopEngine
# was RETIRED and is currently NOT registered. Do NOT run this script routinely.
#
# Why the file is kept: docs/ROADMAP_v5_PLAYER_REPLICA.md states the "loop_engine
# four-piece" is still reused for per-stage reviews, so the machinery is kept on purpose.
#
# When it WOULD be needed: loop_engine.py flips a research mode from waiting_data to
# pending once tick data reaches 20 trading days (tick_days). Measured 2026-09-14:
# tick_days = 3, so nothing would happen for weeks - running it daily only prints a number.
#
# Preferred usage instead of registering a task:
#   python -X utf8 scripts\loop_engine.py
#   (run_loop_engine.ps1 is self-locating and still valid; run_loop_engine.cmd was
#    deleted on 2026-09-14 because it embedded a stale pre-EvoAlpha path.)
$root = Split-Path -Parent $PSScriptRoot
$py = (Get-Command python).Source
$runner = Join-Path $root 'scripts\run_loop_engine.cmd'
$content = '@echo off' + [Environment]::NewLine + 'set PYTHONPATH=' + (Join-Path (Split-Path $root) 'py_libs') + [Environment]::NewLine + '"' + $py + '" "' + (Join-Path $root 'scripts\loop_engine.py') + '" >> "' + (Join-Path $root 'outputs\loop_engine.log') + '" 2>&1'
Set-Content -Path $runner -Value $content -Encoding Default -NoNewline
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $runner + '"')
$trigger = New-ScheduledTaskTrigger -Daily -At '16:35'
Register-ScheduledTask -TaskName 'YaobanLoopEngine' -Action $action -Trigger $trigger -Settings $settings -Description 'Loop engineering state check (R8)' -Force | Out-Null
Write-Host 'Registered YaobanLoopEngine (16:35)'