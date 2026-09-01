# Register YaobanLoopEngine (16:35) - loop engineering state check
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