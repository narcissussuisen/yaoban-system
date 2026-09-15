# EvoAlpha 每日自迭代批任务注册（22:00，YaobanDailyIteration）。
#
# 定位：这是**排期变更**，需人工确认后运行；与 scripts/register_schedule.ps1 的 $Schedule 表配套：
#   1. 本脚本只注册 YaobanDailyIteration 单条，并做与表中一致的验证；
#   2. 正式固化请把下面 $Entry 的同一行合并进 register_schedule.ps1 的 $Schedule，
#      否则 register_schedule.ps1 的「任务触发器」校验会因为缺这条而告警、preflight 会记为漂移。
#
# 非交易日守卫写在 scripts/run_daily_iteration.ps1 内（日历不可用时 fail-open 继续执行）。
param([switch]$VerifyOnly, [switch]$Unregister)
$ErrorActionPreference = "Stop"

$taskRoot = "C:\Users\YZP\WorkBuddy\yaoban_tasks"
$launch = Join-Path $taskRoot "launch.ps1"
$runHidden = Join-Path $taskRoot "run_hidden.py"
$rootFile = Join-Path $taskRoot "root.txt"
$pythonw = "C:\Users\YZP\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
$expectedRoot = Split-Path -Parent $PSScriptRoot

foreach ($p in @($launch, $runHidden, $rootFile)) { if (-not (Test-Path -LiteralPath $p)) { throw "missing: $p" } }
if (-not (Test-Path -LiteralPath $pythonw)) { throw "missing pythonw: $pythonw" }
$configuredRoot = (Get-Content -LiteralPath $rootFile -Raw -Encoding UTF8).Trim().TrimStart([char]0xFEFF)
if ($configuredRoot -ne $expectedRoot) { throw "root.txt points to $configuredRoot; expected $expectedRoot" }

# 与 register_schedule.ps1 $Schedule 的行保持一致
$Entry = @{ Name = "YaobanDailyIteration"; Script = "run_daily_iteration.ps1";
            At = @("22:00"); Limit = "PT1H"; Enabled = $true;
            Desc = "EvoAlpha daily self-iteration batch (materials->cards->profile->proposals->shadow)" }

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $Entry.Name -Confirm:$false
    Write-Host ("unregistered " + $Entry.Name)
    exit 0
}

if (-not $VerifyOnly) {
    $scriptFile = Join-Path $expectedRoot ("scripts\" + $Entry.Script)
    if (-not (Test-Path -LiteralPath $scriptFile)) { throw "missing script: $scriptFile" }
    $inner = "powershell.exe -NoProfile -WindowStyle Hidden -NonInteractive -ExecutionPolicy Bypass -File `"$scriptFile`""
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument ("`"$runHidden`" " + $inner)
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
        -DaysOfWeek @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday") -At $Entry.At[0]
    $settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    $settings.ExecutionTimeLimit = $Entry.Limit
    $settings.WakeToRun = $true
    Register-ScheduledTask -TaskName $Entry.Name -Action $action -Trigger $trigger `
        -Settings $settings -Description $Entry.Desc -Force | Out-Null
    Write-Host ("registered " + $Entry.Name)
}

$t = Get-ScheduledTask -TaskName $Entry.Name -ErrorAction Stop
if ($t.State -ne "Ready" -and $t.State -ne "Running") { throw "$($Entry.Name) state=$($t.State) expected=Ready" }
$actual = @($t.Triggers | ForEach-Object { ([datetime]$_.StartBoundary).ToString("HH:mm") } | Sort-Object)
if (($actual -join ",") -ne ($Entry.At -join ",")) { throw "$($Entry.Name) triggers=$($actual -join ',') expected=$($Entry.At -join ',')" }
$arg = [string]$t.Actions[0].Arguments
if ($arg -notlike ("*" + $Entry.Script + "*")) { throw "$($Entry.Name) action mismatch: $arg" }
$info = $t | Get-ScheduledTaskInfo
Write-Host ("ok state=" + $t.State + " triggers=" + ($actual -join "/") + " next=" + $info.NextRunTime)
