from __future__ import annotations
import hashlib,json,os,pathlib,subprocess
from datetime import datetime
BASE=pathlib.Path(__file__).resolve().parent.parent
EXPECTED_TIMES={'YaobanAuctionMonitor':'09:15','YaobanPreflight':'08:45','YaobanPremarket':'08:50','YaobanPlanGate':'08:55','YaobanTickDaemon':'09:30','YaobanScanConfirm':'09:30','YaobanIntradayMonitor':'09:30','YaobanEventNotify':'09:15','YaobanClosePipeline':'15:40','YaobanDailyRebuild':'17:00','YaobanDailyAcceptance':'19:10'}
TASKS={'YaobanAuctionMonitor':'auction','YaobanPreflight':'infra','YaobanPremarket':'premarket','YaobanPlanGate':'plan-gate','YaobanTickDaemon':'tick','YaobanScanConfirm':'scan','YaobanIntradayMonitor':'monitor','YaobanEventNotify':'notify','YaobanClosePipeline':'close','YaobanDailyRebuild':'rebuild','YaobanDailyAcceptance':'acceptance'}
SECRET=pathlib.Path(r'C:\Users\YZP\WorkBuddy\yaoban_tasks\feishu_webhook.txt')
def ps_json(command):
 cp=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True,encoding='utf-8',errors='replace')
 return json.loads(cp.stdout) if cp.returncode==0 and cp.stdout.strip() else {'error':cp.stderr[-300:],'exit':cp.returncode}
def main():
 rows={}
 for name,mode in TASKS.items():
  cmd=f"$t=Get-ScheduledTask -TaskName '{name}';$i=Get-ScheduledTaskInfo -TaskName '{name}';[xml]$x=Export-ScheduledTask -TaskName '{name}';[pscustomobject]@{{enabled=$t.Settings.Enabled;next=$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss');start=[string]$t.Triggers[0].StartBoundary;days=[string]$t.Triggers[0].DaysOfWeek;wake=$t.Settings.WakeToRun;battery=(-not $t.Settings.DisallowStartIfOnBatteries);stop_battery=$t.Settings.StopIfGoingOnBatteries;start_available=$t.Settings.StartWhenAvailable;multiple=[string]$t.Settings.MultipleInstances;limit=[string]$t.Settings.ExecutionTimeLimit;args=[string]$t.Actions.Arguments;interval=[string]$x.Task.Triggers.CalendarTrigger.Repetition.Interval;duration=[string]$x.Task.Triggers.CalendarTrigger.Repetition.Duration}}|ConvertTo-Json -Compress"
  rows[name]=ps_json(cmd)
 vibe=ps_json("$t=Get-ScheduledTask -TaskName 'VibeResearchLiveTickValidation';$i=Get-ScheduledTaskInfo -TaskName 'VibeResearchLiveTickValidation';[pscustomobject]@{enabled=$t.Settings.Enabled;start=[string]$t.Triggers[0].StartBoundary;days=[string]$t.Triggers[0].DaysOfWeek;wake=$t.Settings.WakeToRun;battery=(-not $t.Settings.DisallowStartIfOnBatteries);stop_battery=$t.Settings.StopIfGoingOnBatteries;start_available=$t.Settings.StartWhenAvailable;multiple=[string]$t.Settings.MultipleInstances}|ConvertTo-Json -Compress")
 task_checks={name:bool(r.get('enabled') and r.get('days')=='62' and r.get('wake') and r.get('battery') and not r.get('stop_battery') and r.get('start_available') and r.get('multiple')=='IgnoreNew' and f'-Mode {TASKS[name]}' in r.get('args','') and EXPECTED_TIMES[name] in r.get('start','')) for name,r in rows.items()}
 task_checks['VibeResearchLiveTickValidation']=bool(vibe.get('enabled') and vibe.get('days')=='62' and vibe.get('wake') and vibe.get('battery') and not vibe.get('stop_battery') and vibe.get('start_available') and vibe.get('multiple')=='IgnoreNew' and '09:32' in vibe.get('start',''))
 rows['VibeResearchLiveTickValidation']=vibe
 source='\n'.join(p.read_text(encoding='utf-8',errors='replace') for p in list((BASE/'scripts').glob('*'))+list((BASE/'tests').glob('*')) if p.is_file())
 secret=SECRET.read_text(encoding='utf-8').strip();secret_leaked=bool(secret and secret in source)
 checks={'all_task_settings':all(task_checks.values()),'secret_exists':SECRET.exists(),'secret_not_in_source':not secret_leaked,'ledger_exists':(BASE/'portfolio'/'ledger.json').exists(),'mandate_exists':(BASE/'docs'/'AUTONOMOUS_PAPER_MANDATE.md').exists()}
 report={'generated_at':datetime.now().isoformat(timespec='seconds'),'status':'pass' if all(checks.values()) else 'fail','checks':checks,'task_checks':task_checks,'tasks':rows,'secret_sha256':hashlib.sha256(secret.encode()).hexdigest() if secret else None}
 path=BASE/'outputs'/'acceptance'/'static_readiness_2026-08-31.json';path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,path);print(json.dumps({'status':report['status'],'checks':checks,'path':str(path)},ensure_ascii=False));return 0 if report['status']=='pass' else 2
if __name__=='__main__':raise SystemExit(main())
