"""Call-auction observer (09:15-09:29), strictly read-only.

Captures full-market Tencent snapshots, tracks planned candidates and holdings,
and freezes an auction watchlist after 09:25. It never writes the ledger.
"""
from __future__ import annotations
import argparse,json,os,pathlib,sys
from datetime import datetime
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent/'portfolio'))
from market_scan import fetch_batch,load_universe,tencent_symbol
from ledger import load
BASE=pathlib.Path(__file__).resolve().parent.parent;OUT=BASE/'outputs'/'auction';OUT.mkdir(parents=True,exist_ok=True)

def authorized(sym):return len(sym)==6 and sym.startswith(('600','601','603','605','000','001','002','003','300','301'))
def atomic(path,value):
 tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
 with tmp.open('w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)
def load_plan(day):
 """缺计划时降级为纯快照(用户裁定: auction 不得因计划缺失整体失败; 仍严格只读)。"""
 try:return json.loads((BASE/'outputs'/'plans'/f'{day}_plan.json').read_text(encoding='utf-8')),False
 except Exception:return {},True
def observe(now):
 day=now.strftime('%Y-%m-%d');hm=now.strftime('%H:%M');plan,plan_missing=load_plan(day);ledger=load();syms=load_universe();codes=[tencent_symbol(s) for s in syms];quotes={};fail=[]
 for i in range(0,len(codes),400):
  try:quotes.update(fetch_batch(codes[i:i+400]))
  except Exception as exc:fail.append({'offset':i,'error':type(exc).__name__})
 coverage=len(quotes)/max(1,len(syms))
 if fail or coverage<.90:raise RuntimeError(f'quote coverage {coverage:.1%}, failures={fail}')
 planned={x['sym'] for x in plan.get('picks',[])};held=set(ledger.get('account',{}).get('positions',{}));rows=[]
 for sym,q in quotes.items():
  if not authorized(sym) or q.get('chg') is None or 'ST' in q.get('name','') or q.get('name','').startswith('*'):continue
  rows.append({'sym':sym,'name':q.get('name'),'px':q.get('px'),'chg_pct':round(float(q['chg']),3),'volume_lot':q.get('vol'),'amount_wan':q.get('amt'),'turnover_pct':q.get('turn'),'planned':sym in planned,'held':sym in held})
 rows.sort(key=lambda x:(not(x['planned'] or x['held']),-float(x['amount_wan'] or 0),-float(x['chg_pct'] or -99)))
 focus=[x for x in rows if x['planned'] or x['held']];active=sorted(rows,key=lambda x:-float(x['amount_wan'] or 0))[:30]
 result={'date':day,'time':now.strftime('%H:%M:%S'),'phase':'cancelable' if hm<'09:20' else('no_cancel' if hm<'09:25' else'final'),'coverage':round(coverage,4),'universe_count':len(syms),'quote_count':len(quotes),'focus':focus,'active_top':active,'read_only':True,'plan_missing':plan_missing}
 atomic(OUT/f'auction_{now:%Y%m%d_%H%M}.json',result);atomic(OUT/'latest.json',result)
 if hm>='09:25':
  freeze={'date':day,'frozen_at':now.strftime('%Y-%m-%d %H:%M:%S'),'source':str(OUT/'latest.json'),'picks':focus,'active_top':active[:20],'read_only':True,'orders_allowed':False}
  atomic(OUT/f'auction_freeze_{day}.json',freeze)
 print(json.dumps({'phase':result['phase'],'coverage':result['coverage'],'focus':len(focus),'active':len(active),'frozen':hm>='09:25','plan_missing':plan_missing},ensure_ascii=False));return 0

def main():
 p=argparse.ArgumentParser();p.add_argument('--force',action='store_true');a=p.parse_args();now=datetime.now();hm=now.strftime('%H:%M')
 if not a.force and (now.weekday()>=5 or not('09:15'<=hm<='09:29')):print(f'[{hm}] 非集合竞价观察窗口');return 0
 try:return observe(now)
 except Exception as exc:print(f'auction observer failed: {type(exc).__name__}: {exc}',file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
