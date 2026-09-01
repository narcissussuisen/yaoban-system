import sys,json,pathlib
sys.path.insert(0,r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\portfolio')
import ledger
st=ledger.load(); before=list(st['account']['equity_curve']); by={}
for row in before: by[row['date']]=row
st['account']['equity_curve']=sorted(by.values(),key=lambda x:x['date'])
st['rules_log'].append({'date':'2026-08-30','kind':'equity_curve_dedup','before_rows':len(before),'after_rows':len(st['account']['equity_curve']),'reason':'one canonical close valuation per trade date'})
ledger.save(st)
print(json.dumps({'before':before,'after':st['account']['equity_curve'],'revision':st['_revision']},ensure_ascii=False,indent=2))
