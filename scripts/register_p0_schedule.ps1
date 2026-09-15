param([switch]$SkipVibe)
$ErrorActionPreference = 'Stop'

$taskRoot = 'C:\Users\YZP\WorkBuddy\yaoban_tasks'
$launch = Join-Path $taskRoot 'launch.ps1'
$rootFile = Join-Path $taskRoot 'root.txt'
$expectedRoot = Split-Path -Parent $PSScriptRoot
$vibeRegister = Join-Path (Split-Path -Parent $expectedRoot) 'Vibe-Research\scripts\register-live-tick-validation.ps1'

if (-not (Test-Path -LiteralPath $launch)) { throw "Missing task launcher: $launch" }
if (-not (Test-Path -LiteralPath $rootFile)) { throw "Missing task root file: $rootFile" }
$configuredRoot = (Get-Content -LiteralPath $rootFile -Raw -Encoding UTF8).Trim().TrimStart([char]0xFEFF)
if ($configuredRoot -ne $expectedRoot) {
    throw "root.txt points to '$configuredRoot'; expected '$expectedRoot'. Refusing to register tasks."
}

function New-EvoAlphaAction([string]$Mode) {
    New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launch + '" -Mode ' + $Mode)
}

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
$morningTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '08:58'
$morningSettings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName 'EvoAlphaMorningCheck' -Action (New-EvoAlphaAction 'morning-check') -Trigger $morningTrigger -Settings $morningSettings -Description 'P0 pre-open readiness check at 08:58 China Standard Time' -Force | Out-Null

# 2026-09-10: evening verification at 19:30 (post-close chain finishes ~18:25). Read-only merged
# check that replaces the two WorkBuddy prompt tasks; it never returns non-zero, the verdict rides
# on its own Feishu card, so it cannot double-push with the generic failure notifier.
$eveningTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $weekdays -At '19:30'
$eveningSettings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName 'EvoAlphaEveningCheck' -Action (New-EvoAlphaAction 'evening-check') -Trigger $eveningTrigger -Settings $eveningSettings -Description 'EvoAlpha evening verification 19:30 CST: post-close chain result, close pipeline, sentiment/candidates freshness and sanity, next-day plan, daily_rebuilt coverage, acceptance verdict, fill compliance' -Force | Out-Null

if (-not $SkipVibe) {
    if (-not (Test-Path -LiteralPath $vibeRegister)) { throw "Missing Vibe register script: $vibeRegister" }
    & $vibeRegister
}

$expected = [ordered]@{
    EvoAlphaMorningCheck = @('08:58')
    EvoAlphaEveningCheck = @('19:30')
    VibeResearchLiveTickValidation = @('09:35', '13:05')
}
foreach ($entry in $expected.GetEnumerator()) {
    if ($SkipVibe -and $entry.Key -eq 'VibeResearchLiveTickValidation') { continue }
    $task = Get-ScheduledTask -TaskName $entry.Key -ErrorAction Stop
    if ($task.State -eq 'Disabled') { throw "$($entry.Key) is disabled" }
    $actual = @($task.Triggers | ForEach-Object { ([datetime]$_.StartBoundary).ToString('HH:mm') } | Sort-Object -Unique)
    $wanted = @($entry.Value | Sort-Object -Unique)
    if (($actual -join ',') -ne ($wanted -join ',')) {
        throw "$($entry.Key) trigger mismatch: actual=$($actual -join ',') expected=$($wanted -join ',')"
    }
    if ($entry.Key -eq 'EvoAlphaMorningCheck') {
        $argument = [string]$task.Actions[0].Arguments
        if ($argument -notlike '*launch.ps1*' -or $argument -notlike '*-Mode morning-check*') {
            throw "EvoAlphaMorningCheck action mismatch: $argument"
        }
    }
    if ($entry.Key -eq 'EvoAlphaEveningCheck') {
        $argument = [string]$task.Actions[0].Arguments
        if ($argument -notlike '*launch.ps1*' -or $argument -notlike '*-Mode evening-check*') {
            throw "EvoAlphaEveningCheck action mismatch: $argument"
        }
    }
    if ($entry.Key -eq 'VibeResearchLiveTickValidation') {
        $restartInterval = [string]$task.Settings.RestartInterval
        $validRestartIntervals = @('PT1M', '00:01:00')
        if ([int]$task.Settings.RestartCount -ne 3 -or $restartInterval -notin $validRestartIntervals) {
            throw "Vibe restart policy mismatch: count=$($task.Settings.RestartCount) interval=$restartInterval"
        }
    }
    Write-Host ($entry.Key + ' verified at ' + ($actual -join ', '))
}
