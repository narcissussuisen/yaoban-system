# EvoAlpha production schedule - SINGLE SOURCE OF TRUTH (2026-09-10, user ruling).
#
# Every production task is declared in $Schedule below; this script registers them and then
# verifies state + trigger time + repetition + action marker against the declared table.
# preflight.py checks the same table indirectly via the "task trigger" assertions.
#
# 2026-09-10 changes (user rulings):
#   Preflight 08:45 -> 08:35   (17-minute repair window before open)
#   SelfHeal  08:36 (new)      (restart+data repairs only; never touches ledger/gate/code)
#   PostCloseChain 16:30 -> 15:35 (post-close fixed-price trading ends 15:30, data final after)
#   EveningCheck 19:30 -> 17:30    (13h+ repair window)
#   DashboardServices 08:40 -> 08:30 (keepalive must precede the 08:35 health check)
#   calendar-refresh folded into the Saturday maintenance task (tdx-verify).
param([switch]$SkipVibe, [switch]$VerifyOnly)
$ErrorActionPreference = "Stop"

$taskRoot = "C:\Users\YZP\WorkBuddy\yaoban_tasks"
$launch = Join-Path $taskRoot "launch.ps1"
$runHidden = Join-Path $taskRoot "run_hidden.py"
$rootFile = Join-Path $taskRoot "root.txt"
$pythonw = "C:\Users\YZP\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
$expectedRoot = Split-Path -Parent $PSScriptRoot
$vibe = Join-Path (Split-Path -Parent $expectedRoot) "Vibe-Research\scripts"

foreach ($p in @($launch, $runHidden, $rootFile)) { if (-not (Test-Path -LiteralPath $p)) { throw "missing: $p" } }
if (-not (Test-Path -LiteralPath $pythonw)) { throw "missing pythonw: $pythonw" }
$configuredRoot = (Get-Content -LiteralPath $rootFile -Raw -Encoding UTF8).Trim().TrimStart([char]0xFEFF)
if ($configuredRoot -ne $expectedRoot) { throw "root.txt points to $configuredRoot; expected $expectedRoot" }

$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

$Schedule = @(
    @{ Name = "YaobanPreflight";          Mode = "infra";          At = @("08:35");          Limit = "PT30M"; Restart = "3xPT1M"; Enabled = $true;  Desc = "EvoAlpha infra preflight gate" },
    @{ Name = "YaobanSelfHeal";           Mode = "selfheal";       At = @("08:36");          Limit = "PT20M"; Enabled = $true;  Desc = "EvoAlpha pre-open self-heal (restart/data repairs only)" },
    @{ Name = "YaobanPremarket";          Mode = "premarket";      At = @("08:50");          Limit = "PT30M"; Restart = "3xPT1M"; Enabled = $true;  Desc = "EvoAlpha premarket snapshot" },
    @{ Name = "YaobanPlanGate";           Mode = "plan-gate";      At = @("08:55");          Limit = "PT30M"; Restart = "3xPT1M"; Enabled = $true;  Desc = "EvoAlpha plan acceptance gate" },
    @{ Name = "YaobanMorningCheck";       Mode = "morning-check";  At = @("08:58");          Limit = "PT2M";  Enabled = $true;  Desc = "EvoAlpha single pre-open selfcheck card" },
    @{ Name = "YaobanTdxProbe";           Mode = "tdx-probe";      At = @("09:00");          Limit = "";      Interval = "PT30M"; Duration = "PT7H"; Enabled = $true; Desc = "EvoAlpha TDX recovery probe" },
    @{ Name = "YaobanAuctionMonitor";     Mode = "auction";        At = @("09:15");          Limit = "PT5M";  Interval = "PT1M";  Duration = "PT15M"; Enabled = $true; Desc = "EvoAlpha auction observer (read-only)" },
    @{ Name = "YaobanEventNotify";        Mode = "notify";         At = @("09:15");          Limit = "PT5M";  Interval = "PT1M";  Duration = "PT5H46M"; Enabled = $true; Desc = "EvoAlpha intraday event notify" },
    # 2026-09-12 R0.8: Limit 由 PT6H(09:30+6h=15:30) 放宽到 PT6H20M(→15:50)。
    # 盘后固定价格交易窗口 15:05-15:30 需要 daemon 存活, 而 watcher 在 15:40 优雅自退
    # (WATCH_EXIT_AT)、daemon 在 15:30 自退 —— 任务上限必须晚于二者, 否则会被硬杀在 15:30。
    @{ Name = "YaobanTickDaemon";         Mode = "tick";           At = @("09:30");          Limit = "PT6H20M"; Restart = "3xPT1M"; Enabled = $true; Desc = "EvoAlpha tick risk daemon (gate-independent; covers 15:05-15:30 after-hours window)" },
    @{ Name = "YaobanScanConfirm";        Mode = "scan";           At = @("09:30");          Limit = "PT10M"; Interval = "PT1M";  Duration = "PT5H31M"; Enabled = $true; Desc = "EvoAlpha full-market scan (T2)" },
    @{ Name = "YaobanIntradayMonitor";    Mode = "monitor";        At = @("09:30");          Limit = "PT10M"; Interval = "PT1M";  Duration = "PT5H31M"; Enabled = $true; Desc = "EvoAlpha intraday monitor" },
    @{ Name = "YaobanClosePipeline";      Mode = "close";          At = @("15:10");          Limit = "PT30M"; Restart = "3xPT1M"; Enabled = $true;  Desc = "EvoAlpha close pipeline" },
    @{ Name = "YaobanPostCloseChain";     Mode = "post-close";     At = @("15:35");          Limit = "PT3H";  Restart = "2xPT5M"; Enabled = $true; Desc = "EvoAlpha post-close chain (rebuild/r5p/r6p/plan/acceptance/log-review)" },
    @{ Name = "YaobanEveningCheck";       Mode = "evening-check";  At = @("17:30");          Limit = "PT15M"; Enabled = $true;  Desc = "EvoAlpha evening verification card" },
    @{ Name = "YaobanTdxServerVerify";    Mode = "tdx-verify";     At = @("10:00"); Days = @("Saturday"); Limit = ""; Enabled = $true; Desc = "Weekly TDX server liveness + calendar refresh" },
    @{ Name = "YaobanBoardRefresh";       Script = "run_board_refresh.ps1"; At = @("09:35", "13:05"); Limit = "PT3M"; Interval = "PT3M"; Duration = "PT1H51M"; Enabled = $true; Desc = "EvoAlpha board snapshot refresh" },
    # 2026-09-11 (用户裁定): 关键节点状态卡推送 —— 只读观测者, 不参与门禁/账本/计划;
    # At 为节点产卡时刻(含迟到的补推触发点), PT5M 重复窗负责吸收上游迟到; 状态文件 + 事件键双重去重。
    # 2026-09-14 (用户裁定, 定时任务全景审计): 重复窗 PT10H24M -> PT4H。
    #   原窗口下最晚起点 18:30 会一直跟到次日 04:54(跨夜空转), 全天约 244 次进程启动,
    #   多数只为"查一眼"后立即退出; 收为 4h 后最晚 22:30 收工(约 167 次/日)。
    #   ⚠️ At 的 13 个时刻**保持不变** ⇒ preflight.py:40 的 TRIGGER_EXPECTED 无需同步
    #   (该断言只比对 StartBoundary 时刻, 不比对 Duration)。上游迟到 >4h 时补推失效,
    #   但 PENDING_ALERT_ROUNDS=3(15 分钟)即升级告警, 不会静默。
    @{ Name = "YaobanStatusPush";         Script = "run_status_push.ps1"; At = @("08:36", "08:50", "08:55", "09:00", "09:30", "09:35", "11:30", "13:05", "13:10", "15:05", "15:40", "17:45", "18:30"); Limit = "PT3M"; Interval = "PT5M"; Duration = "PT4H"; Enabled = $true; Desc = "EvoAlpha key-node status cards (read-only observer)" },
    @{ Name = "VibeResearchDashboardServices"; Vibe = "ensure-dashboard-services.ps1"; At = @("08:30", "09:20"); Limit = "PT5M"; Enabled = $true; Desc = "Vibe dashboard services keepalive" },
    @{ Name = "VibeResearchLiveTickValidation"; Vibe = "run-live-tick-validation.ps1";  At = @("09:35", "13:05"); Limit = "PT5M"; Restart = "3xPT1M"; Enabled = $true; Desc = "Vibe live tick validation" }
)

function New-IsoSpan([string]$iso) {
    if ([string]::IsNullOrEmpty($iso)) { return $null }
    return [System.Xml.XmlConvert]::ToTimeSpan($iso)
}

function New-YaobanAction($e) {
    if ($e.ContainsKey("Vibe")) {
        $file = Join-Path $vibe $e.Vibe
        if (-not (Test-Path -LiteralPath $file)) { throw "missing vibe script: $file" }
        $inner = "powershell.exe -NoProfile -WindowStyle Hidden -NonInteractive -ExecutionPolicy Bypass -File `"$file`""
    } elseif ($e.ContainsKey("Script")) {
        $file = Join-Path $expectedRoot ("scripts\" + $e.Script)
        if (-not (Test-Path -LiteralPath $file)) { throw "missing script: $file" }
        $inner = "powershell.exe -NoProfile -WindowStyle Hidden -NonInteractive -ExecutionPolicy Bypass -File `"$file`""
    } else {
        $inner = "powershell.exe -NoProfile -WindowStyle Hidden -NonInteractive -ExecutionPolicy Bypass -File `"$launch`" -Mode " + $e.Mode
    }
    return New-ScheduledTaskAction -Execute $pythonw -Argument ("`"$runHidden`" " + $inner)
}

function New-EntryTrigger($e, [string]$at) {
    $days = if ($e.ContainsKey("Days")) { $e.Days } else { $weekdays }
    $t = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $days -At $at
    if ($e.ContainsKey("Interval")) {
        $base = New-ScheduledTaskTrigger -Once -At $at -RepetitionInterval (New-IsoSpan $e.Interval) -RepetitionDuration (New-IsoSpan $e.Duration)
        $t.Repetition = $base.Repetition
    }
    return $t
}

function New-EntrySettings($e) {
    # CIM settings expect ISO-8601 durations as strings (a raw TimeSpan serialises as 00:30:00 and is rejected).
    $s = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    $limitIso = "PT0S"
    if ($e.ContainsKey("Limit") -and $e.Limit) { $limitIso = $e.Limit }
    $s.ExecutionTimeLimit = $limitIso
    $s.WakeToRun = $true
    if ($e.ContainsKey("Restart")) {
        $parts = $e.Restart -split "x"
        $s.RestartCount = [int]$parts[0]
        $s.RestartInterval = $parts[1]
    }
    return $s
}

function Test-Entry($e) {
    $t = Get-ScheduledTask -TaskName $e.Name -ErrorAction Stop
    $want = if ($e.Enabled) { "Ready" } else { "Disabled" }
    if ($t.State -ne $want) { throw "$($e.Name) state=$($t.State) expected=$want" }
    $actual = @($t.Triggers | ForEach-Object { ([datetime]$_.StartBoundary).ToString("HH:mm") } | Sort-Object)
    $expected = @($e.At | Sort-Object)
    if (($actual -join ",") -ne ($expected -join ",")) { throw "$($e.Name) triggers=$($actual -join ",") expected=$($expected -join ",")" }
    if ($e.ContainsKey("Interval")) {
        foreach ($tr in $t.Triggers) {
            if (-not $tr.Repetition -or $tr.Repetition.Interval -ne $e.Interval) { throw "$($e.Name) repetition missing/mismatch" }
        }
    }
    $arg = [string]$t.Actions[0].Arguments
    if ($e.ContainsKey("Vibe")) { if ($arg -notlike ("*" + $e.Vibe + "*")) { throw "$($e.Name) action mismatch: $arg" } }
    elseif ($e.ContainsKey("Script")) { if ($arg -notlike ("*" + $e.Script + "*")) { throw "$($e.Name) action mismatch: $arg" } }
    elseif ($arg -notlike ("*-Mode " + $e.Mode + "*")) { throw "$($e.Name) action mismatch: $arg" }
    $info = $t | Get-ScheduledTaskInfo
    return ($e.Name + " ok state=" + $t.State + " triggers=" + ($actual -join "/") + " next=" + $info.NextRunTime)
}

if (-not $VerifyOnly) {
    foreach ($e in $Schedule) {
        if ($SkipVibe -and $e.ContainsKey("Vibe")) { continue }
        $triggers = @($e.At | ForEach-Object { New-EntryTrigger $e $_ })
        Register-ScheduledTask -TaskName $e.Name -Action (New-YaobanAction $e) -Trigger $triggers `
            -Settings (New-EntrySettings $e) -Description $e.Desc -Force | Out-Null
        if (-not $e.Enabled) { Disable-ScheduledTask -TaskName $e.Name | Out-Null }
        Write-Host ("registered " + $e.Name)
    }
}

$failed = @()
foreach ($e in $Schedule) {
    if ($SkipVibe -and $e.ContainsKey("Vibe")) { continue }
    try { Write-Host (Test-Entry $e) } catch { $failed += $_.Exception.Message; Write-Host ("FAIL " + $_.Exception.Message) }
}
if ($failed.Count -gt 0) { Write-Error ("schedule verification failed: " + $failed.Count + " entr(ies)"); exit 1 }
Write-Host ("schedule verified: " + $Schedule.Count + " entries")
