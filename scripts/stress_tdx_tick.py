"""阶段零-1: TDX 逐笔成交压力测试（get_transaction_data 盘中拉取能力实测）
目标: 确认持仓秒级刷新可行性——延迟/限频/失败率

对 2 只标的分别以 1s/3s/5s/10s 间隔连续拉 30 次逐笔，统计:
  - 每次请求延迟(ms)、返回笔数
  - 失败/空返回次数（限频信号）
  - 各频率下稳定获取的结论

用法: python scripts/stress_tdx_tick.py
"""
from __future__ import annotations
import time
from pytdx.hq import TdxHq_API

SERVERS = [('59.36.5.11',7709),('117.34.114.18',7709),('117.34.114.13',7709),('117.34.114.27',7709),
 ('117.34.114.16',7709),('117.34.114.20',7709),('117.34.114.17',7709),('117.34.114.14',7709),
 ('117.34.114.15',7709),('115.238.56.198',7709)]
SYMS = [('601212', 1), ('000426', 0)]


def market_of(sym: str) -> int:
    if sym.startswith('900'):
        return 1
    if sym[0] in ('4', '8') or sym.startswith('92'):
        return 2
    if sym[0] in ('6', '9', '5'):
        return 1
    return 0


def main():
    api = TdxHq_API(heartbeat=False)
    ok = False
    for host, port in SERVERS:
        if api.connect(host, port, time_out=8):
            print(f'connected {host}:{port}')
            ok = True
            break
    if not ok:
        print('连接失败')
        return
    for interval in (1, 3, 5, 10):
        for sym, m in SYMS:
            lat = []
            n_rows = []
            fails = 0
            empties = 0
            for _ in range(30):
                t0 = time.time()
                try:
                    bars = api.get_transaction_data(m, sym, 0, 200)
                except Exception:
                    bars = None
                dt = (time.time() - t0) * 1000
                lat.append(dt)
                if bars is None:
                    fails += 1
                elif len(bars) == 0:
                    empties += 1
                else:
                    n_rows.append(len(bars))
                time.sleep(interval)
            lat_sorted = sorted(lat)
            print(f'间隔{interval}s {sym}: 延迟P50={lat_sorted[len(lat)//2]:.0f}ms '
                  f'P95={lat_sorted[int(len(lat)*0.95)]:.0f}ms 失败={fails} 空={empties} '
                  f'每笔返回{sum(n_rows)/max(len(n_rows),1):.0f}条' if n_rows else
                  f'间隔{interval}s {sym}: 延迟P50={lat_sorted[len(lat)//2]:.0f}ms 失败={fails} 空={empties} 无有效返回')
            print(f'  [最近10次延迟] ' + ' '.join(f'{x:.0f}' for x in lat[-10:]), flush=True)
    api.disconnect()


if __name__ == '__main__':
    main()
