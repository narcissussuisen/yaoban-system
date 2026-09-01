"""检查 pytdx 1m datetime 原始格式
"""
from pytdx.hq import TdxHq_API
api = TdxHq_API(heartbeat=False)
api.connect('115.238.56.198', 7709, time_out=8)
bars = api.get_security_bars(0, 1, '601212', 0, 5)
for b in bars:
    print(repr(b['datetime']), b['close'])
api.disconnect()
