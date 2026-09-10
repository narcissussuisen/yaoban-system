# NOTE(2026-09-03): this file MUST keep the UTF-8 BOM. PS5.1 reads BOM-less files as ANSI/GBK; a Chinese comment ending in a GBK lead byte (e.g. 。=E3 80 82) silently swallows the following newline (DBCS pair) and comments out the next line - this broke all stages on 9/3 night (bodies never ran but manifest said success).
param([Parameter(Mandatory=$true)][string]$Py, [Parameter(Mandatory=$true)][string]$Base, [Parameter(Mandatory=$true)][string]$Day, [Parameter(Mandatory=$true)][string]$Notify)
# P0时间表重构(2026-09-01, 用户评审): 盘后链 16:30 单任务一次跑完
# 2026-09-02 修复(gate 阻塞根因): 顺序调换——rebuild 最先!
#   旧序(情绪→候选→rebuild→计划): 情绪/候选在 rebuild 之前跑, 永远读不到当日日线,
#   sentiment 滞后一天 → 次日 infra 恒 FAIL → 盘中 gate 恒拦截(9/2 全天盘中任务死)。
#   新序: daily_rebuilt(TDX) -> r5p情绪 -> r6p候选 -> next_plan -> acceptance
#   (qfq_store 已同步修复: daily_rebuilt 也并入 _agg_daily 补充源, 口径: rebuilt vol 已为股)
# 严格依赖链: 上游失败则下游跳过并在 manifest 记录 skipped_due_to, acceptance 始终执行并如实记录 missing
#   (2026-09-02 批次C7 注释改写: 原"尽力而为"表述与 if 门控实现矛盾, 以实现为准, 计划§2.8)
# C7 链 manifest 观测(只加观测, 不动门控控制流): 每步向 outputs/acceptance/chain_manifest_{day}.jsonl
#   append 一行, 字段=G8 全集(run_id/stage/attempt_no/started_at/finished_at/exit_code/status/
#   skipped_due_to/command_hash/input_manifest_hash/output_paths); 被跳过步骤写 status=skipped +
#   skipped_due_to(上游:exit_N), 不以上游失败码冒充本步结果(修复 E11); 同日重跑只追加 attempt_no
#   递增的新行不覆盖, 机械选择规则=取该 stage attempt_no 最大行; 9/2 不追溯补造 manifest(仅用于9/3起)。
$ErrorActionPreference = "Continue"
# 2026-09-03: stage python 均 -X utf8 输出, 统一按 UTF-8 解码避免 Out-Host 转发乱码(无控制台时忽略)
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$Manifest = Join-Path $Base ("outputs\acceptance\chain_manifest_" + $Day + ".jsonl")
$RunId = "pc_" + $Day.Replace("-","") + "_" + (Get-Date -Format "HHmmss")
$NextDay = ([datetime]::ParseExact($Day,'yyyy-MM-dd',[Globalization.CultureInfo]::InvariantCulture)).AddDays(1).ToString('yyyy-MM-dd')

function Get-Sha256([string]$Text) {
  $sha=[System.Security.Cryptography.SHA256]::Create()
  try { ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLower() }
  finally { $sha.Dispose() }
}
function Get-NextAttempt([string]$Stage) {
  $n=0
  if (Test-Path $Manifest) {
    foreach ($ln in (Get-Content $Manifest -Encoding UTF8)) {
      if ([string]::IsNullOrWhiteSpace($ln)) { continue }
      try { $mj=$ln|ConvertFrom-Json } catch { continue }
      if ($mj.stage -eq $Stage -and $null -ne $mj.attempt_no) { $v=[int]$mj.attempt_no; if ($v -gt $n) { $n=$v } }
    }
  }
  return ($n+1)
}
$script:PrevLine = 'day=' + $Day
$script:UpStage = $null
$script:UpCode = 0
function Invoke-ChainStage([string]$Stage,[bool]$Run,[scriptblock]$Body,[string]$CmdText,[string[]]$Outputs) {
  # 门控语义与原 if ($LASTEXITCODE -eq 0) 等价: 上游失败则本步跳过; 区别仅在跳过被显式记录, 不再冒充
  $attempt=Get-NextAttempt $Stage
  $t0=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
  if ($Run) {
    # 2026-09-03 修复(晚, 例行): & $Body 必须经管道送 Out-Host——否则 stage 的 stdout 会混入函数返回值,
    # $c 变 Object[], $gate=($c -eq 0) 成数组过滤而非布尔, 绑不进 [bool]$Run -> r5p/r6p/next_plan 全部未执行(9/3 16:30 事故)。
    # Out-Host 把 stage 输出送到宿主 stdout(launch.ps1 重定向的 task_logs stdout.log), 不进返回值管道。
    & $Body | Out-Host
    $code=$LASTEXITCODE
    if ($null -eq $code) { $code=0 }
    $t1=(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    $status='failed'; if ($code -eq 0) { $status='success' }
    $skip=$null
  } else {
    $code=$null; $t1=$t0; $status='skipped'; $skip=($script:UpStage + ':exit_' + $script:UpCode)
  }
  $row=[ordered]@{ run_id=$RunId; stage=$Stage; attempt_no=$attempt; started_at=$t0; finished_at=$t1;
    exit_code=$code; status=$status; skipped_due_to=$skip; command_hash=(Get-Sha256 $CmdText);
    input_manifest_hash=(Get-Sha256 $script:PrevLine); output_paths=$Outputs }
  $line=($row|ConvertTo-Json -Compress)
  Add-Content -Path $Manifest -Value $line -Encoding UTF8
  $script:PrevLine=$line
  if ($Run) { $script:UpStage=$Stage; $script:UpCode=$code }
  return $code
}

$codes = New-Object System.Collections.ArrayList
$skipped = New-Object System.Collections.ArrayList

$c = Invoke-ChainStage 'rebuild' $true { & $Py -X utf8 ($Base + "\scripts\fetch_daily_minute_rebuild.py") } ($Py + ' -X utf8 ' + $Base + '\scripts\fetch_daily_minute_rebuild.py') @('F:/WorkBuddyItem/a股level2/daily_rebuilt')
[void]$codes.Add($c)
$gate = ($c -eq 0)

$c = Invoke-ChainStage 'r5p' $gate { & $Py -X utf8 ($Base + "\scripts\r5p_sentiment_build.py") --workers 6 } ($Py + ' -X utf8 ' + $Base + '\scripts\r5p_sentiment_build.py --workers 6') @('outputs/sentiment_full_2026.csv')
if ($null -eq $c) { [void]$skipped.Add('r5p') } else { [void]$codes.Add($c) }
$gate = ($c -eq 0)

$c = Invoke-ChainStage 'r6p' $gate { & $Py -X utf8 ($Base + "\scripts\r6p_candidates_build.py") --workers 6 } ($Py + ' -X utf8 ' + $Base + '\scripts\r6p_candidates_build.py --workers 6') @('outputs/r6p_candidates_2026.csv')
if ($null -eq $c) { [void]$skipped.Add('r6p') } else { [void]$codes.Add($c) }
$gate = ($c -eq 0)

$c = Invoke-ChainStage 'next_plan' $gate { & $Py -X utf8 ($Base + "\scripts\generate_next_plan.py") } ($Py + ' -X utf8 ' + $Base + '\scripts\generate_next_plan.py') @(('outputs/plans/' + $NextDay + '_plan.json'))
if ($null -eq $c) { [void]$skipped.Add('next_plan') } else { [void]$codes.Add($c) }

$c = Invoke-ChainStage 'acceptance' $true { & $Py -X utf8 ($Base + "\scripts\collect_daily_acceptance.py") --date $Day --final } ($Py + ' -X utf8 ' + $Base + '\scripts\collect_daily_acceptance.py --date ' + $Day + ' --final') @(('outputs/acceptance/acceptance_' + $Day + '.json'))
[void]$codes.Add($c)

# 2026-09-09 日志复盘 v1: 每日聚合错误信号(任务日志/验收/盘后链/失败推送/风控/门禁/晨检/看门狗)
# -> log_review_<date>.md + iteration_proposals 合并 + 飞书推送(kind=review, 审计)。复盘不再漏消费报错。
$c = Invoke-ChainStage 'log-review' $true { & $Py -X utf8 ($Base + "\scripts\log_error_digest.py") --date $Day --push } ($Py + ' -X utf8 ' + $Base + '\scripts\log_error_digest.py --date ' + $Day + ' --push') @(('outputs/reviews/log_review_' + $Day + '.md'), ('outputs/iteration_proposals/' + $Day + '.json'))
[void]$codes.Add($c)

$bad = @($codes | Where-Object { $_ -ne 0 })
$skipNote = ''
if ($skipped.Count -gt 0) { $skipNote = '; skipped=' + ($skipped -join ',') + '(manifest skipped_due_to)' }
if ($bad.Count -eq 0) {
  & $Py -X utf8 $Notify --kind close --date $Day --event-key ("postclose:" + $Day) --message ("盘后链全部通过 " + $Day + $skipNote)
} else {
  & $Py -X utf8 $Notify --kind failure --date $Day --event-key ("failure:" + $Day + ":post-close") --message ("盘后链部分失败 " + $Day + " codes=" + ($codes -join ",") + $skipNote)
}
if ($bad.Count -gt 0) { exit 1 } else { exit 0 }
