import copy,sys,pathlib,json
sys.path.insert(0,r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\portfolio')
import ledger
s=ledger._default_state();s['policy']['require_human_decision']=True
rid=ledger.record_signal_request(s,{'date':'2026-08-31','sym':'000001','kind':'e4_support','suggested_px':10,'expires_at':'2026-08-31 15:00'})
try:ledger.buy(s,'000001','2026-08-31 09:40',10,100,'e4_support')
except Exception as e:print('missing decision blocked:',e)
did=ledger.record_human_decision(s,rid,'approve','逐妖','盘口/分时确认',9.8,10.2)
ledger.buy(s,'000001','2026-08-31 09:40',10,4000,'e4_support',decision_id=did)
print(json.dumps({'request':s['signal_requests'][rid],'decision':s['human_decisions'][did],'position':s['account']['positions']['000001'],'fill':s['account']['fills'][-1]},ensure_ascii=False,indent=2))
