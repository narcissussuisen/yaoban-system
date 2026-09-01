import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path('.').resolve() / 'portfolio'))
sys.path.insert(0, str(pathlib.Path('.').resolve() / 'src'))
from ledger import load, save, buy, t_buy, t_sell, equity, record_plan, record_review
st = load()
# 10w 账户: 单票 ~40% → 600股@65.61 ≈ 39.4k
buy(st, '002156', '2026-07-08 09:35', 65.61, 600, 'huigui_confirm', stop_pct=5.0, plan_ref='demo')
equity(st, '2026-07-08', {'002156': 65.61})
# 7/9 做T：T进 10:00@65.44 → T出 10:05@66.42（选手 v49 视频一致点位, 1/3仓=200股）
t_buy(st, '002156', '2026-07-09 10:00', 65.44, 200, plan_ref='demo_t')
t_sell(st, '002156', '2026-07-09 10:05', 66.42, 200, plan_ref='demo_t')
equity(st, '2026-07-09', {'002156': 72.17})
record_plan(st, '2026-07-09', {'标题': '演示日计划', '备选': [{'sym': '002156', '触发': '回踩均价线收复≤+3%'}], '卖出清单': ['破均价线减半'], '仓位档': '90%'})
record_review(st, '2026-07-09', {'归因': 'T进65.44/T出66.42 差价1.5% 符合v14口径', '规则迭代': '无'})
save(st)
print('demo fills:', len(st['account']['fills']), 'equity:', st['account']['equity_curve'])