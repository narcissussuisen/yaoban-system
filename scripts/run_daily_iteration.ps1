# EvoAlpha 每日自迭代批入口（22:00，EvoAlphaDailyIteration）。
#
# 定位：学习闭环的夜间批处理。资料解析 → 知识卡片 → 画像 → 提案 → 影子回归。
# 硬边界：
#   * 只读资料与行情；不写账本、不改门禁判定、不改生产代码。
#   * 参数类提案过门槛后只写台账（outputs/iteration/ledger.jsonl）与补丁**预览**；
#     config/parameters.toml 由人工确认后修改（改 config 必须 bump RULES_VERSION）。
#   * 规则/代码/数据类提案恒为 pending_confirm。
# 恒 0 退出：结论承载在 outputs/iteration/<date>/digest.md，不靠退出码传结论。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = 'C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
$script = Join-Path $root 'scripts\daily_iteration.py'
$logDir = Join-Path $root 'outputs\iteration'
$stdout = Join-Path $logDir 'daily_iteration.latest.log'
$stderr = Join-Path $logDir 'daily_iteration.latest.stderr.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# 非交易日静默（与看板刷新/状态卡同口径）：自迭代批只在交易日有增量资料时才有意义。
# 日历不可用(exit 4)按交易日继续执行，绝不因为日历故障漏跑（fail-open 于执行）。
& $python -X utf8 (Join-Path $root 'scripts\trading_calendar.py') check | Out-Null
if ($LASTEXITCODE -eq 3) { exit 0 }

$process = Start-Process -FilePath $python `
    -ArgumentList @('-X', 'utf8', $script, '--market-codes', '1200', '--market-cap', '3000') `
    -WorkingDirectory $root `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -NoNewWindow -Wait -PassThru
exit $process.ExitCode
