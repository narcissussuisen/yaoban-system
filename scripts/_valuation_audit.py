import json, pathlib, sys
sys.path.insert(0,r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\src')
from core.daily_src import load_daily
p=pathlib.Path(r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\portfolio\ledger.json')
st=json.loads(p.read_text(encoding='utf-8'))
rows=[]
mv=0.0
for s,x in st['account']['positions'].items():
 d=load_daily(s)
 px=float(d['close'].iloc[-1]); val=x['qty']*px; mv+=val
 rows.append({'symbol':s,'qty':x['qty'],'close':px,'mv':round(val,2),'date':str(d['date'].iloc[-1])})
eq=st['account']['cash']+mv
print(json.dumps({'cash':round(st['account']['cash'],2),'positions':rows,'mv':round(mv,2),'equity':round(eq,2),'curve_last':st['account']['equity_curve'][-1]},ensure_ascii=False,indent=2))
