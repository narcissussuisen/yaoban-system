import json, urllib.request, time
def em_fetch(sym: str):
    secid = ('1.' if sym[0] in ('6', '9') else '0.') + sym
    url = (f'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}'
           f'&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58'
           f'&klt=101&fqt=1&beg=20250101&end=20261231')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='ignore')
    data = json.loads(raw)
    return (data.get('data') or {}).get('klines') or []
# 连续 10 次单只, 观察是否被限频
for i in range(10):
    try:
        kl = em_fetch('600000')
        print(f'{i}: ok {len(kl)}')
    except Exception as e:
        print(f'{i}: ERR {type(e).__name__} {str(e)[:60]}')
    time.sleep(0.3)
