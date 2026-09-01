from __future__ import annotations
import hashlib,json,os,pathlib,shutil,sys
from datetime import datetime
BASE=pathlib.Path(__file__).resolve().parent.parent
LEDGER=BASE/'portfolio'/'ledger.json'
ARCHIVE=pathlib.Path(r'C:\Users\YZP\WorkBuddy\yaoban_tasks\ledger_archive')
START='2026-08-31'
def atomic(path,data):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
 with tmp.open('w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=1);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)
def main():
 raw=LEDGER.read_bytes() if LEDGER.exists() else b'';old=json.loads(raw.decode('utf-8')) if raw else {}
 if old.get('account',{}).get('positions') or old.get('account',{}).get('fills'):
  ARCHIVE.mkdir(parents=True,exist_ok=True);digest=hashlib.sha256(raw).hexdigest();stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
  dst=ARCHIVE/f'ledger_before_autonomous_{stamp}_{digest[:12]}.json';dst.write_bytes(raw)
  manifest={'archived_at':datetime.now().isoformat(timespec='seconds'),'source':str(LEDGER),'archive':str(dst),'sha256':digest,'old_revision':old.get('_revision'),'positions':len(old.get('account',{}).get('positions',{})),'fills':len(old.get('account',{}).get('fills',[])),'reason':'initialize autonomous paper account from 2026-08-31'}
  atomic(ARCHIVE/f'migration_{stamp}.json',manifest)
 elif old.get('policy',{}).get('account_mode')=='autonomous_paper' and old.get('start_date')==START:
  print('already initialized');return 0
 state={'_revision':int(old.get('_revision',0))+1,'start_cash':100000.0,'start_date':START,'benchmark':'000852.SH','account':{'cash':100000.0,'positions':{},'fills':[],'equity_curve':[{'date':START,'equity':100000.0,'cash':100000.0,'market_value':0.0,'baseline':True}]},'plans':{},'reviews':{},'rules_log':[{'date':START,'rule':'autonomous_paper_mandate','status':'active','source':'user_authorization_2026-08-30'}],'policy':{'max_single_weight':0.45,'max_positions':2,'max_gross_exposure':0.90,'max_new_buys_per_day':1,'require_human_decision':False,'account_mode':'autonomous_paper','max_daily_loss_pct':5.0,'pause_drawdown_pct':10.0,'terminate_drawdown_pct':15.0,'transition_reduce_only':False},'signal_requests':{},'human_decisions':{},'risk_state':{'position_multiplier':1.0,'paused':False,'terminated':False},'date':datetime.now().strftime('%Y-%m-%d'),'updated_at':datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
 atomic(LEDGER,state);check=json.loads(LEDGER.read_text(encoding='utf-8'))
 assert check['account']['cash']==100000 and not check['account']['positions'] and check['policy']['account_mode']=='autonomous_paper' and check['policy']['require_human_decision'] is False
 print(json.dumps({'start_date':check['start_date'],'cash':check['account']['cash'],'positions':0,'mode':check['policy']['account_mode'],'revision':check['_revision']},ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
