param([Parameter(Mandatory=$true)][string]$Mode)
$ErrorActionPreference='Stop'
$Base=(Get-Content 'C:\Users\YZP\WorkBuddy\yaoban_tasks\root.txt' -Raw -Encoding UTF8).Trim()
$Py='C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
$Day=(Get-Date).ToString('yyyy-MM-dd')
$Notify=$Base+'\scripts\feishu_notify.py'
$NotifyEvents=$Base+'\scripts\notify_trading_events.py'
function Send-Failure([string]$Stage,[int]$Code){
 $details=''
 $report=Join-Path $Base ('outputs\preflight_'+$Day+'_post_plan.json')
 if($Stage -eq 'plan-gate' -and (Test-Path $report)){
  try{
   $j=Get-Content $report -Raw -Encoding UTF8|ConvertFrom-Json
   $failed=@($j.results|Where-Object {-not $_.ok -and $_.critical})
   if($failed.Count -gt 0){$details=[Environment]::NewLine+'Failed checks: '+(($failed|ForEach-Object {$_.name+': '+$_.detail}) -join ' | ')}
   $details+=[Environment]::NewLine+'Report: '+$report
  }catch{$details=[Environment]::NewLine+'Report read failed: '+$_.Exception.Message}
 }
 $msg=('Yaoban task failed '+$Day+[Environment]::NewLine+'Stage: '+$Stage+[Environment]::NewLine+'Exit code: '+$Code+$details+[Environment]::NewLine+'Action: fail closed; no new positions when the critical intraday path fails.')
 & $Py -X utf8 $Notify --kind failure --date $Day --event-key ('failure:'+$Day+':'+$Stage) --message $msg
}
function Run-Stage([string]$Stage,[scriptblock]$Body){
 & $Body
 $code=$LASTEXITCODE
 if($null -eq $code){$code=0}
 if($code -ne 0){Send-Failure $Stage $code}
 exit $code
}
function Gate([string]$Stage){
 $f=Join-Path $Base ('outputs\preflight_'+$Day+'_'+$Stage+'.json')
 if(-not(Test-Path $f)){Send-Failure ('gate-'+$Stage) 20;[Console]::Error.WriteLine('gate missing: '+$f);exit 20}
 $j=Get-Content $f -Raw -Encoding UTF8|ConvertFrom-Json
 if($j.status -ne 'pass' -or $j.date -ne $Day -or $j.stage -ne $Stage){Send-Failure ('gate-'+$Stage) 21;[Console]::Error.WriteLine('gate status/date/stage invalid: '+$f);exit 21}
 $generated=[datetime]::ParseExact([string]$j.time,'yyyy-MM-dd HH:mm:ss',[Globalization.CultureInfo]::InvariantCulture)
 $age=((Get-Date)-$generated).TotalMinutes
 $maxAge=if($Stage -eq 'infra'){30}else{480}
 $afterClose=$Stage -eq 'post_plan' -and (Get-Date).ToString('HH:mm') -gt '15:05'
 if($generated.ToString('yyyy-MM-dd') -ne $Day -or $age -lt 0 -or $age -gt $maxAge -or $afterClose){Send-Failure ('gate-stale-'+$Stage) 23;[Console]::Error.WriteLine('gate stale/outside session: '+$f+' age_min='+$age);exit 23}
}
switch($Mode){
 'infra' {Run-Stage 'infra' {& $Py -X utf8 ($Base+'\scripts\preflight.py')}}
 'premarket' {Gate 'infra';Run-Stage 'premarket' {& $Py -X utf8 ($Base+'\scripts\premarket.py');if($LASTEXITCODE -eq 0){& $Py -X utf8 $Notify --kind premarket --date $Day}}}
 'plan-gate' {Gate 'infra';Run-Stage 'plan-gate' {& $Py -X utf8 ($Base+'\scripts\preflight.py') --post-plan --push}}
 'morning-check' {Run-Stage 'morning-check' {& $Py -X utf8 ($Base+'\scripts\check_morning.py')}}
 'auction' {Gate 'post_plan';Run-Stage 'auction' {& $Py -X utf8 ($Base+'\scripts\auction_monitor.py')}}
 'tick' {Gate 'post_plan';Run-Stage 'tick' {& $Py -X utf8 ($Base+'\scripts\tick_monitor.py') --daemon --interval 5 --execute-risk}}
 'scan' {Gate 'post_plan';Run-Stage 'scan' {& $Py -X utf8 ($Base+'\scripts\scan_and_confirm.py') --min-amt 10 --e4-support --temp-ladder --execute}}
 'monitor' {Gate 'post_plan';Run-Stage 'monitor' {& $Py -X utf8 ($Base+'\scripts\monitor_intraday.py')}}
 'notify' {Gate 'post_plan';Run-Stage 'notify' {& $Py -X utf8 $NotifyEvents --date $Day}}
 'close' {Run-Stage 'close' {& $Py -X utf8 ($Base+'\scripts\close_pipeline.py');if($LASTEXITCODE -eq 0){& $Py -X utf8 $Notify --kind close --date $Day}}}
 'rebuild' {Run-Stage 'rebuild' {& $Py -X utf8 ($Base+'\scripts\fetch_daily_minute_rebuild.py');if($LASTEXITCODE -eq 0){& $Py -X utf8 ($Base+'\scripts\generate_next_plan.py')}}}
 'next-plan' {Run-Stage 'next-plan' {& $Py -X utf8 ($Base+'\scripts\generate_next_plan.py')}}
 'acceptance' {Run-Stage 'acceptance' {& $Py -X utf8 ($Base+'\scripts\collect_daily_acceptance.py');if($LASTEXITCODE -eq 0){$msg=('Yaoban daily acceptance passed '+$Day+[Environment]::NewLine+'Evidence: outputs/acceptance/acceptance_'+$Day+'.json');& $Py -X utf8 $Notify --kind alert --date $Day --event-key ('acceptance:'+$Day) --message $msg}}} 'data-refresh' {Run-Stage 'data-refresh' {& $Py -X utf8 ($Base+'\scripts\r5p_sentiment_build.py') --workers 6;if($LASTEXITCODE -eq 0){& $Py -X utf8 ($Base+'\scripts\r6p_candidates_build.py') --workers 6}}}
 # P0时间表重构(2026-09-01, 用户评审): 盘后链合并为单任务 16:30 (逻辑见 scripts/post_close_chain.ps1)
 'post-close' {& powershell.exe -NoProfile -ExecutionPolicy Bypass -File ($Base+'\scripts\post_close_chain.ps1') -Py $Py -Base $Base -Day $Day -Notify $Notify; exit $LASTEXITCODE}
 default {[Console]::Error.WriteLine('unknown mode '+$Mode);exit 22}
}