import json, pathlib, sys
BASE = pathlib.Path(__file__).resolve().parent.parent          # yaoban-system（R0.2 修正：原硬编码路径缺 EvoAlpha 段）
sys.path.insert(0, str(BASE / 'src'))
sys.path.insert(0, str(BASE / 'portfolio'))
from core.daily_src import load_daily
from ledger import LEDGER                                       # env-aware (EVOALPHA_LEDGER)
st = json.loads(LEDGER.read_text(encoding='utf-8'))
rows = []
mv = 0.0
for s, x in st['account']['positions'].items():
    d = load_daily(s)
    px = float(d['close'].iloc[-1]); val = x['qty'] * px; mv += val
    rows.append({'symbol': s, 'qty': x['qty'], 'close': px, 'mv': round(val, 2), 'date': str(d['date'].iloc[-1])})
eq = st['account']['cash'] + mv
print(json.dumps({'cash': round(st['account']['cash'], 2), 'positions': rows, 'mv': round(mv, 2),
                  'equity': round(eq, 2), 'curve_last': st['account']['equity_curve'][-1]},
                 ensure_ascii=False, indent=2))
