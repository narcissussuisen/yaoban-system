"""逐妖08:50盘前快任务：刷新外盘，确认盘后预生成计划存在；不做全市场重算。"""
from __future__ import annotations
import argparse,hashlib,json,pathlib,subprocess,sys
from datetime import datetime
BASE=pathlib.Path(__file__).resolve().parent.parent;PY=sys.executable

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--date',default='');a=ap.parse_args();day=a.date or datetime.now().strftime('%Y-%m-%d')
 plan=BASE/'outputs'/'plans'/f'{day}_plan.json'
 if not plan.exists(): print(f'ERROR: 盘后预生成计划缺失 {plan}',file=sys.stderr);return 2
 log=BASE/'outputs'/f'premarket_{day}.log'
 with open(log,'w',encoding='utf-8') as f:r=subprocess.run([PY,str(BASE/'scripts'/'pull_global.py')],cwd=str(BASE),stdout=f,stderr=subprocess.STDOUT)
 if r.returncode: print(f'ERROR: 外盘刷新失败 rc={r.returncode}',file=sys.stderr);return 3
 try:
  snapshot=BASE/'outputs'/'global_snapshot.json';raw=snapshot.read_bytes()
  p=json.loads(plan.read_text(encoding='utf-8'));p['global']=json.loads(raw.decode('utf-8'))
  p['premarket_refreshed_at']=datetime.now().strftime('%Y-%m-%d %H:%M:%S');p['global_snapshot_sha256']=hashlib.sha256(raw).hexdigest()
  tmp=plan.with_name(plan.name+'.tmp');tmp.write_text(json.dumps(p,ensure_ascii=False,indent=1),encoding='utf-8');tmp.replace(plan)
 except Exception as e:print(f'ERROR: 更新计划外盘失败 {e}',file=sys.stderr);return 4
 print(f'盘前快任务完成 {day} picks={len(p.get("picks",[]))}',flush=True);return 0
if __name__=='__main__':sys.exit(main())
