param([Parameter(Mandatory=$true)][string]$Py, [Parameter(Mandatory=$true)][string]$Base, [Parameter(Mandatory=$true)][string]$Day, [Parameter(Mandatory=$true)][string]$Notify)
# P0时间表重构(2026-09-01, 用户评审): 盘后链 16:30 单任务一次跑完
# 顺序: r5p情绪 -> r6p候选 -> daily_rebuilt(TDX) -> next_plan(依赖新候选) -> acceptance(依赖全部盘后产物)
# 每步失败不中止后续(尽力而为, 失败在 acceptance 中可见), 最后按整体结果推送
$ErrorActionPreference = "Continue"
$codes = New-Object System.Collections.ArrayList
& $Py -X utf8 ($Base + "\scripts\r5p_sentiment_build.py") --workers 6; [void]$codes.Add($LASTEXITCODE)
if ($LASTEXITCODE -eq 0) { & $Py -X utf8 ($Base + "\scripts\r6p_candidates_build.py") --workers 6 }; [void]$codes.Add($LASTEXITCODE)
if ($LASTEXITCODE -eq 0) { & $Py -X utf8 ($Base + "\scripts\fetch_daily_minute_rebuild.py") }; [void]$codes.Add($LASTEXITCODE)
if ($LASTEXITCODE -eq 0) { & $Py -X utf8 ($Base + "\scripts\generate_next_plan.py") }; [void]$codes.Add($LASTEXITCODE)
& $Py -X utf8 ($Base + "\scripts\collect_daily_acceptance.py"); [void]$codes.Add($LASTEXITCODE)
$bad = @($codes | Where-Object { $_ -ne 0 })
if ($bad.Count -eq 0) {
  & $Py -X utf8 $Notify --kind close --date $Day --event-key ("postclose:" + $Day) --message ("盘后链全部通过 " + $Day)
} else {
  & $Py -X utf8 $Notify --kind failure --date $Day --event-key ("failure:" + $Day + ":post-close") --message ("盘后链部分失败 " + $Day + " codes=" + ($codes -join ","))
}
if ($bad.Count -gt 0) { exit 1 } else { exit 0 }
