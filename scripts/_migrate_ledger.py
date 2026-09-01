import json,pathlib,shutil,sys
sys.path.insert(0,r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\portfolio')
sys.path.insert(0,r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\src')
import ledger
from core.daily_src import load_daily
p=ledger.LEDGER
backup=p.with_name('ledger.preflight_backup_20260830.json')
if not backup.exists(): shutil.copy2(p,backup)
st=ledger.load()
st['policy'].update({'max_positions':2,'max_single_weight':0.45,'max_gross_exposure':0.90,'max_new_buys_per_day':1,'require_human_decision':True,'transition_reduce_only':len(st['account']['positions'])>2})
st['risk_state'].setdefault('position_multiplier',1.0)
marks={s:float(load_daily(s)['close'].iloc[-1]) for s in st['account']['positions']}
old=st['account']['equity_curve'][-1] if st['account']['equity_curve'] else None
new_eq=ledger.equity(st,'2026-08-28',marks)
st['rules_log'].append({'date':'2026-08-30','kind':'valuation_correction','before':old,'after':st['account']['equity_curve'][-1],'reason':'authoritative daily_rebuilt closes; previous mv overstated by 1550','recorded_at':'2026-08-30 00:00:00'})
st['rules_log'].append({'date':'2026-08-30','kind':'policy_migration','policy':st['policy'],'reason':'approved 45% x 2 positions; existing 4 positions transition_reduce_only','recorded_at':'2026-08-30 00:00:00'})
ledger.save(st)
print(json.dumps({'backup':str(backup),'revision':st['_revision'],'equity':new_eq,'curve':st['account']['equity_curve'],'policy':st['policy']},ensure_ascii=False,indent=2))
