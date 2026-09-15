$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root 'scripts\run_board_refresh.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File "' + $runner + '"')
$base = New-ScheduledTaskTrigger -Once -At '09:35' -RepetitionInterval (New-TimeSpan -Minutes 3) -RepetitionDuration (New-TimeSpan -Minutes 111)
$am = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '09:35'
$am.Repetition = $base.Repetition
$pm = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '13:05'
$basePm = New-ScheduledTaskTrigger -Once -At '13:05' -RepetitionInterval (New-TimeSpan -Minutes 3) -RepetitionDuration (New-TimeSpan -Minutes 111)
$pm.Repetition = $basePm.Repetition
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'EvoAlphaBoardRefresh' -Action $action -Trigger $am, $pm -Settings $settings -Description 'Rebuild board snapshot every 5 min during trading windows' -Force | Out-Null
Write-Host 'EvoAlphaBoardRefresh registered'
