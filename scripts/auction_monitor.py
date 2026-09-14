"""Call-auction observer (09:15-09:29), strictly read-only.

Captures full-market Tencent snapshots, tracks planned candidates and holdings,
and freezes an auction watchlist after 09:25. It never writes the ledger.

── 2026-09-14 扩展：S2 竞价初筛 / S3 观察名单 / 竞价档情绪（**只留痕，不接入任何买卖判定**）──
为什么落在这里：本脚本**不在决策链上**（消费方只有验收与推送脚本）、**从不写账本**、
失败只自身 rc=2、且 09:15–09:29 **时段独占**（与 09:30 起的 scan 不抢资源）
⇒ 爆炸半径≈0，是唯一能"当晚改、次日生效"而不冒取样日风险的位置。

选手原文依据（`选手学习资料/选手战法画像-累计.md:112-126`，三处表述互证）：
    「9 点 20 看集合竞价做初步筛选，9 点半用战法筛选三只观察」
⇒ 新增：① `freeze.pool_snapshot`（**昨日战法池全量**的当日竞价特征，非仅计划+持仓）
        ② `freeze.sentiment_pre`（竞价档情绪，对应 SOP 三档顺序的第②档）
        ③ S2 `outputs/patterns/<day>_auction_screen.json` / S3 `<day>_watchlist.json`
⚠️ 必须**池全量**而非队列交集：只有覆盖"竞价有数、盘中没进队列"的标的才有**对照组**，
   否则样本=队列那几只，永远问不出竞价的区分度
   （2026-09-14 实测：freeze 24 只 ∩ 队列 15 只 = **0 只**）。
⚠️ 红线：`read_only` / `orders_allowed` 两个字段**不可改动** ——
   `collect_daily_acceptance.py:227` 断言 `orders_allowed is False`，改了验收立刻变红。
"""
from __future__ import annotations
import argparse,json,os,pathlib,sys
from datetime import datetime
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent/'portfolio'))
from market_scan import fetch_batch,load_universe,tencent_symbol
from ledger import load
BASE=pathlib.Path(__file__).resolve().parent.parent;OUT=BASE/'outputs'/'auction';OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(BASE/'src'))
from core import selection_chain as SC

def authorized(sym):return len(sym)==6 and sym.startswith(('600','601','603','605','000','001','002','003','300','301'))
def atomic(path,value):
 tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
 with tmp.open('w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)
def load_plan(day):
 """缺计划时降级为纯快照(用户裁定: auction 不得因计划缺失整体失败; 仍严格只读)。"""
 try:return json.loads((BASE/'outputs'/'plans'/f'{day}_plan.json').read_text(encoding='utf-8')),False
 except Exception:return {},True
def load_pool(day):
 """读**当日**战法池（盘后 `plan_daily.py` 产出，asof=T−1），返回 (pool, asof, missing)。

 缺产物时返回 ([], None, True) —— 只降级自身、不抛（观察层失败不得打死链路）；
 同时把缺失写 stderr，**不静默**（否则事后分不清"池空"与"读取失败"）。
 """
 fp=BASE/'outputs'/'patterns'/f'{day}_pattern_pool.json'
 try:
  doc=json.loads(fp.read_text(encoding='utf-8'))
  return list(doc.get('pool') or []),doc.get('asof'),False
 except Exception as exc:
  print(f'[auction] 战法池不可读 {fp}: {type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
  return [],None,True
def observe(now,dry_run=False,target_n=SC.DEFAULT_TARGET_N):
 root=(OUT/'_dryrun') if dry_run else OUT
 root.mkdir(parents=True,exist_ok=True)
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
 atomic(root/f'auction_{now:%Y%m%d_%H%M}.json',result);atomic(root/'latest.json',result)
 if hm>='09:25':
  pool,pool_asof,pool_missing=load_pool(day)
  screen=SC.build_auction_screen(pool,quotes,target_n=target_n)
  for x in screen['rows']:x['in_plan']=x['sym'] in planned        # 计划标记由上游注入（S3 排序键①）
  watchlist=SC.build_watchlist(screen,target_n=target_n)
  sent_pre=SC.build_sentiment_pre(rows,screen['rows'])
  s2_dir=root if dry_run else (BASE/'outputs'/'patterns')
  s2_dir.mkdir(parents=True,exist_ok=True)
  atomic(s2_dir/f'{day}_auction_screen.json',
         {'date':day,'asof':pool_asof,'frozen_at':now.strftime('%Y-%m-%d %H:%M:%S'),'n_target':target_n,
          'stats':screen['stats'],'rows':screen['rows'],'read_only':True,
          'note':'S2 竞价初筛：只产特征与池内分位；keep 仅表示"有特征可排序"，不是买卖许可'})
  atomic(s2_dir/f'{day}_watchlist.json',
         dict(watchlist,date=day,asof=pool_asof,frozen_at=now.strftime('%Y-%m-%d %H:%M:%S'),read_only=True))
  freeze={'date':day,'frozen_at':now.strftime('%Y-%m-%d %H:%M:%S'),'source':str(root/'latest.json'),'picks':focus,'active_top':active[:20],'read_only':True,'orders_allowed':False,
          'pool_missing':pool_missing,'pool_asof':pool_asof,'pool_stats':screen['stats'],'pool_snapshot':screen['rows'],
          'sentiment_pre':sent_pre,'watchlist_picks':[x['sym'] for x in watchlist['picks']],
          'n_observed':watchlist['n_observed'],'n_target':target_n}
  atomic(root/f'auction_freeze_{day}.json',freeze)
 print(json.dumps({'phase':result['phase'],'coverage':result['coverage'],'focus':len(focus),'active':len(active),'frozen':hm>='09:25','plan_missing':plan_missing,'dry_run':dry_run},ensure_ascii=False));return 0

def main():
 p=argparse.ArgumentParser();p.add_argument('--force',action='store_true')
 p.add_argument('--dry-run',dest='dry_run',action='store_true',help='产物写 outputs/auction/_dryrun/，不覆盖当日 freeze/S2/S3')
 p.add_argument('--watchlist-n',dest='target_n',type=int,default=SC.DEFAULT_TARGET_N,help='观察名单规模（选手原文=3；SOP GEN-DRAGON-05 ≤5）')
 a=p.parse_args();now=datetime.now();hm=now.strftime('%H:%M')
 if not a.force and (now.weekday()>=5 or not('09:15'<=hm<='09:29')):print(f'[{hm}] 非集合竞价观察窗口');return 0
 try:return observe(now,dry_run=a.dry_run,target_n=a.target_n)
 except Exception as exc:print(f'auction observer failed: {type(exc).__name__}: {exc}',file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
