"""盘后生成下一交易日计划。交易日由 Vibe-Research baostock 日历裁决。"""
import json,pathlib,subprocess,sys,tempfile
from datetime import datetime,timedelta
BASE=pathlib.Path(__file__).resolve().parent.parent;PY=sys.executable
CAL=BASE.parent/'Vibe-Research'/'.agents'/'skills'/'data-access'/'scripts'/'fetch_trade_calendar.py'
def main():
 day=datetime.now().date(); probe=(day+timedelta(days=7)).isoformat();out=BASE/'outputs'/'next_plan_calendar';out.mkdir(parents=True,exist_ok=True)
 r=subprocess.run([PY,str(CAL),'--symbol','300308','--days','45','--check-date',probe,'--out-dir',str(out)])
 if r.returncode:return 2
 data=json.loads((out/'fetch'/'fetch_trade_calendar.json').read_text(encoding='utf-8'))
 raw=json.loads((out/data['evidence'][0]['raw_ref']).read_text(encoding='utf-8'))
 rows=json.loads(raw) if isinstance(raw,str) else raw
 trading=sorted(x['calendar_date'] for x in rows if x.get('is_trading_day')=='1' and x['calendar_date']>day.isoformat())
 if not trading:return 3
 target=trading[0]
 return subprocess.run([PY,str(BASE/'scripts'/'plan_daily.py'),'--date',target],cwd=str(BASE)).returncode
if __name__=='__main__':sys.exit(main())
