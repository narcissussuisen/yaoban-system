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
# 2026-09-12 R0.7: 基准臂只依赖 rebuild 的产物(全市场日线), 与 r5p/r6p/plan 无关, 故单独留标志
$rebuildOk = $gate

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

# 2026-09-12 R0.7 双市场基准臂（判据基础设施）: 全A等权 + 国证2000 的净值序列。
# ⚠️ 它是 **R5.1 净值判定的基准**，不是交易数据链的一环 —— 因此:
#   - 只依赖 rebuild 的产物($rebuildOk)，不依赖 r5p/r6p/next_plan;
#   - 失败**不并入** $codes / $dataFailed（见下方分类），只以 $benchNote 随状态卡告知。
#     否则研究侧的一次取数失败会把整条盘后链判成"数据段故障" —— 那是在骗人。
# 代价: 全A等权需聚合 5646 只 parquet，2026-09-12 实测 354s（约 6 分钟）。
$c = Invoke-ChainStage 'benchmark' $rebuildOk { & $Py -X utf8 ($Base + "\scripts\build_benchmark_arms.py") --year 2026 --workers 6 } ($Py + ' -X utf8 ' + $Base + '\scripts\build_benchmark_arms.py --year 2026 --workers 6') @('portfolio/benchmarks/arms.json')
$benchCode = $c

# 2026-09-12 R2.4 候选池 1m 分钟快照（数据供给基础设施）: 当日候选池并集的 1m 增量落盘。
# ⚠️ 与 benchmark 同类 —— 它是**数据供给**而非交易数据链的一环，因此:
#   - 只依赖 outputs/intraday/ 的产物（当日 confirm_*.json），不依赖 r5p/r6p/next_plan → $Run 传 $true;
#   - 失败**不并入** $codes / $dataFailed（见下方分类），只以 $snapNote 随状态卡告知。
#     否则一次取数限流就会把整条盘后链判成"数据段故障" —— 与 benchmark 同一条理由。
# 非交易日 / 候选池为空 → 脚本自身静默 exit 0（它有 trading_calendar 无关的自判：并集为空即跳过）。
# 放在**最后一个 stage**：L66 会把 UpStage 指向本 stage，放末尾才不会影响其它 stage 的 skip 归因。
# 回看窗口=10 自然日（用户 2026-09-12 拍板）；快照根目录由 EVOALPHA_MINUTE_SNAPSHOT 覆盖（默认 C 盘）。
$c = Invoke-ChainStage 'minute-snap' $true { & $Py -X utf8 ($Base + "\scripts\minute_incremental_snapshot.py") --date $Day } ($Py + ' -X utf8 ' + $Base + '\scripts\minute_incremental_snapshot.py --date ' + $Day) @()
$snapCode = $c

# 2026-09-13 R2.5 · 情绪表入库（决策链 ① 环境闸门的**数据前提**）:
#   `r5p` 每日重建 `outputs/sentiment_full_<year>.csv`（**真相源**），但它**不写 DB**；
#   DB `market_sentiment` 由本 stage 的 `sync_sentiment_db.py` 同步（只 upsert 不 delete）。
# ⚠️ 此前它**没有挂链** → 周一 15:35 时 DB 最新仍停在上一交易日，而
#   ① 段用 `sentiment_as_of(day)`（`date<=day ORDER BY date DESC LIMIT 1`）→ **静默取到旧行**
#   → regime/冰点判定用的是**昨天的情绪**。这会静默污染 ①⑥ 两段。
# → 与 benchmark / minute-snap 同类，失败**不并入链故障判定**。必须排在 r5p 之后。实测 <1s。
$c = Invoke-ChainStage 'sync-sentiment' $true { & $Py -X utf8 ($Base + "\scripts\sync_sentiment_db.py") --year 2026 } ($Py + ' -X utf8 ' + $Base + '\scripts\sync_sentiment_db.py --year 2026') @()
$syCode = $c

# 2026-09-13 R2.2 第四块 · 板块强度（决策链 ② 定主线的**数据前提**）:
#   `build_sector_strength.py` 产出 `outputs/sector_strength_<day>.json`（162 板块 × M1/M2/M3 + D3 三层）。
# ⚠️ 此前它**没有挂链** → 若缺该文件，决策链 ② 段会 `status=no_data`，
#    R3 验收的「完整决策链 artifact」就不成立（六段里有一段是空的）。
#   → 与 benchmark / minute-snap / decision-chain 同类，**失败不并入链故障判定**。
# ⚠️ 依赖 rebuild 的产物（`daily_rebuilt`），故必须排在 rebuild 之后；实测 ~14s。
# ⚠️ **该脚本只能"当日跑"**：它内部固定用日期轴的**最后一天**算指标（`li = len(dates)-1`），
#    `--date` 只决定输出文件名。生产在盘后跑时 `$Day` 即最后一天 → 一致正确；
#    但**不可用它回算历史日期**（会把当日强度标成历史日）。
$c = Invoke-ChainStage 'sector-strength' $true { & $Py -X utf8 ($Base + "\scripts\build_sector_strength.py") --date $Day } ($Py + ' -X utf8 ' + $Base + '\scripts\build_sector_strength.py --date ' + $Day) @("outputs/sector_strength_$Day.json")
$ssCode = $c

# 2026-09-13 R3.1~R3.4 六段决策环（判据基础设施）: 当日完整决策链 artifact（含 LLM 裁量 + 三方裁定）。
# ⚠️ 与 benchmark / minute-snap 同类 —— 它是**判据基础设施**而非交易数据链的一环，因此:
#   - 只依赖当日已有的产物（情绪表 / sector_strength / 候选池 confirm / 分钟快照），故 $Run 传 $true;
#   - 失败**不并入** $codes / $dataFailed（见下方分类），只以 $dcNote 随状态卡告知。
#     否则一次 LLM 限流就会把整条盘后链判成"数据段故障"。
#   - **放链序最后一位**：Invoke-ChainStage 会把 UpStage 指向本 stage，放末尾才不影响其它 stage 的 skip 归因。
# ⚠️ 全程 shadow：`mode=shadow` / `order_intent.side=none`，**不产生任何成交**。
# ⚠️ 它是 R3 验收「连续 10 个交易日产出完整决策链 artifact」的**唯一累积来源** ——
#    不挂链则该项验收永远无法自动累积（此前只手动跑过）。
# 成本：约 23 次 LLM 调用 ≈ 90s（实测 token 中位 929/次；模型 deepseek-chat，可用 EVOALPHA_LLM_MODEL 覆盖）。
$c = Invoke-ChainStage 'decision-chain' $true { & $Py -X utf8 ($Base + "\scripts\run_decision_chain.py") --date $Day } ($Py + ' -X utf8 ' + $Base + '\scripts\run_decision_chain.py --date ' + $Day) @("outputs/decision_chain/chain_$Day.json")
$dcCode = $c

# 2026-09-10: 区分「数据链故障」与「日终验收评级」。
#   acceptance 的 exit 2/3 是"当日评级"(fail/incomplete, 如输入缺失/检查未过), 不是链故障;
#   原实现把两者并入同一个 failure 推送 -> "盘后链部分失败 codes=0,0,0,0,3,0" 会让读者误判链崩
#   (9/4 变更日志已记录该混淆, 今日 9/10 再次触发)。
#   新语义: 数据链五段(rebuild/r5p/r6p/next_plan/log-review)非零 => 链故障(failure + exit 1);
#          仅 acceptance 非零 => 链完成、验收按评级单独推送(alert, 保留 exit 1 以维持"可见的未达标")。
# 从 manifest 读各 stage 结果(位置索引在"stage 被跳过"时会错位, 故按 stage 名取)。
$rows = @()
if (Test-Path $Manifest) {
  $rows = @(Get-Content $Manifest -Encoding UTF8 | ForEach-Object { try { $_ | ConvertFrom-Json } catch { } })
}
$accRow = $rows | Where-Object { $_.stage -eq 'acceptance' } | Select-Object -Last 1
$accCode = if ($accRow) { $accRow.exit_code } else { 0 }
# 2026-09-12 R0.7: benchmark 是判据基础设施, 不属交易数据链 —— 从"链故障"分类中排除,
# 否则研究侧取数失败会以"盘后链数据段失败"的名义推送, 把读者引向错误的方向。
# 2026-09-12 R2.4: minute-snap 同理排除（数据供给基础设施）。
# 2026-09-13 R3: decision-chain 同理排除（判据基础设施；一次 LLM 限流不应判成"数据段故障"）。
# 2026-09-13 R2.2: sector-strength 同理排除（决策链 ② 段的数据前提）。
$dataFailed = @($rows | Where-Object { $_.stage -ne 'acceptance' -and $_.stage -ne 'benchmark' -and $_.stage -ne 'minute-snap' -and $_.stage -ne 'decision-chain' -and $_.stage -ne 'sector-strength' -and $_.stage -ne 'sync-sentiment' -and $_.status -eq 'failed' })
$dataCodes = @($rows | Where-Object { $_.stage -ne 'acceptance' -and $_.stage -ne 'benchmark' -and $_.stage -ne 'minute-snap' -and $_.stage -ne 'decision-chain' -and $_.stage -ne 'sector-strength' -and $_.stage -ne 'sync-sentiment' } | ForEach-Object { $_.exit_code })
$badData = $dataFailed
$skipNote = ''
$benchNote = ''
if ($null -eq $benchCode) { $benchNote = '; benchmark=skipped(上游 rebuild 失败)' }
elseif ($benchCode -ne 0) { $benchNote = '; benchmark=FAILED(exit ' + $benchCode + '; 基准臂未更新, R5.1 判据会停在上一交易日)' }
$snapNote = ''
if ($null -eq $snapCode) { $snapNote = '; minute-snap=skipped' }
elseif ($snapCode -ne 0) { $snapNote = '; minute-snap=FAILED(exit ' + $snapCode + '; 候选池分钟快照未更新, 分时级规则将缺数据)' }
$dcNote = ''
if ($null -eq $dcCode) { $dcNote = '; decision-chain=skipped' }
elseif ($dcCode -ne 0) { $dcNote = '; decision-chain=FAILED(exit ' + $dcCode + '; 当日决策链 artifact 缺失, R3 的"连续 10 交易日"验收会断档)' }
$ssNote = ''
if ($null -eq $ssCode) { $ssNote = '; sector-strength=skipped' }
elseif ($ssCode -ne 0) { $ssNote = '; sector-strength=FAILED(exit ' + $ssCode + '; 板块强度未更新, 决策链 ② 段会是 no_data)' }
$syNote = ''
if ($null -eq $syCode) { $syNote = '; sync-sentiment=skipped' }
elseif ($syCode -ne 0) { $syNote = '; sync-sentiment=FAILED(exit ' + $syCode + '; 情绪表未入库, 决策链 ① 段会读到上一交易日的旧情绪)' }
if ($skipped.Count -gt 0) { $skipNote = '; skipped=' + ($skipped -join ',') + '(manifest skipped_due_to)' }
$accStatus = ''
try { $accStatus = (Get-Content (Join-Path $Base ('outputs\acceptance\acceptance_' + $Day + '.json')) -Raw -Encoding UTF8 | ConvertFrom-Json).status } catch { }
if ($badData.Count -gt 0) {
  & $Py -X utf8 $Notify --kind failure --date $Day --event-key ("failure:" + $Day + ":post-close") --message ("盘后链数据段失败 " + $Day + " data_codes=" + ($dataCodes -join ",") + " acceptance=" + $accCode + $skipNote + $benchNote + $snapNote + $ssNote + $syNote + $dcNote)
} elseif ($accCode -ne 0) {
  & $Py -X utf8 $Notify --kind alert --date $Day --event-key ("postclose-verdict:" + $Day) --message ("盘后链数据段全部通过 " + $Day + " (rebuild/r5p/r6p/next_plan/log-review=0)" + [Environment]::NewLine + "日终验收未达标: status=" + $accStatus + " exit=" + $accCode + " —— 属当日评级(输入缺口/检查未过), 非链故障; 详见 outputs/acceptance/acceptance_" + $Day + ".json" + $skipNote + $benchNote + $snapNote + $ssNote + $syNote + $dcNote)
} else {
  & $Py -X utf8 $Notify --kind close --date $Day --event-key ("postclose:" + $Day) --message ("盘后链全部通过 + 日终验收通过 " + $Day + $skipNote + $benchNote + $snapNote + $ssNote + $syNote + $dcNote)
}
$bad = @($codes | Where-Object { $_ -ne 0 })
if ($bad.Count -gt 0) { exit 1 } else { exit 0 }
