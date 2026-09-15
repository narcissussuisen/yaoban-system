"""阶段零-2: 全市场异动扫描器（腾讯批量行情 qt.gtimg.cn）
每轮: 全市场 5600 只分批 URL → 解析现价/涨跌幅/量比/换手/成交额 → 异动排序
输出: outputs/intraday/market_scan_{date}_{HHMM}.json + 汇总打印
用法: python scripts/market_scan.py
"""
from __future__ import annotations
import http.client
import json
import pathlib
import re
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data.qfq_store import QFQStore  # noqa: E402
from core.combo_sell import industry_of  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / 'outputs' / 'intraday'
BATCH = 400  # 每 URL 标的数

# ⭐ 批次重试（2026-09-15 盘中加，用户指令）
#   动机：腾讯批量行情是**单点外部依赖**，改动前 `fetch_batch` 是「单次 urlopen，
#   失败即抛」⇒ 一次瞬时抖动就丢整批 400 只。实测事故（2026-09-15 09:34:01）：
#   `failures=[{'offset': 0, 'error': 'URLError'}]`、`quotes=4289/4689`（缺数恰为 400），
#   而 `scan_and_confirm.py:317` 的 `batch_failures` 属**一票否决**（coverage 91.5% > 90% 门槛
#   也不放行）⇒ 整轮 `rc=4` fail-closed。同形态历史 6 次（08-31/09-03/09-07/09-08/09-14/09-15）。
#   性质：**取数层**改动，不触碰任何决策参数/阈值/信号 ⇒ 不进 60 日有效性队列。
FETCH_ATTEMPTS = 3   # 含首次；即最多**重试 2 次**
FETCH_BACKOFF = 0.3  # 退避基数（秒）：第 n 次重试前 sleep FETCH_BACKOFF * n（0.3 / 0.6）
#   实测（2026-09-15 09:5x，本机生产 venv）：
#   · 正常路径 = 0.17s / 3 只（解析正确）；
#   · 失败形态 = `URLError(reason=OSError('Tunnel connection failed: 502 Bad Gateway'))`
#     —— 本机行情出口走**代理隧道**，502 是瞬时错误，正是重试对症的形态；
#     该形态 `reason` 是 OSError 而**非** TimeoutError ⇒ `_is_retryable` 判为可重试 ✓。
#   · 单批失败耗时约 10s ⇒ 单批故障的附加代价 ≈ 2 次重试 ≈ +20s（整轮 6s → ~26s，
#     仍 < 60s 触发间隔，不会引起跳拍）；若全 12 批同时故障则约 +6min，
#     但那时整轮本就 rc=4，代价只是「该轮变慢」，不改变 fail-closed 结论。


def _is_retryable(exc: BaseException) -> bool:
    """只有「快速失败」才值得重试；**超时类一律不重试**。

    判据依据（2026-09-15 实测本轮故障形态）：故障是 `URLError` 连接层快速失败
    （09:34 那轮整轮仅 6s 就结束，说明不是 20s 超时），重试成本 ≈ 0。
    反之**超时**意味着对端慢响应，重试会把单批耗时从 20s 抬到 60s，
    12 批累积可达数分钟 ⇒ 把「一轮失败」放大成「整轮卡住 + 后续触发叠加」，
    比原缺陷更危险。
    """
    if isinstance(exc, (TimeoutError, socket.timeout)):  # 3.10+ 二者为同一类型
        return False
    if isinstance(exc, urllib.error.URLError):
        # urllib 会把底层超时包进 URLError.reason，同样不重试
        return not isinstance(exc.reason, (TimeoutError, socket.timeout))
    return isinstance(exc, (OSError, http.client.HTTPException))


def load_universe() -> list[str]:
    st = QFQStore('2026')
    syms = [s for s in st.symbols() if not s.startswith(('399', '5', '15', '16', '899', '688', '689', '4', '8', '92'))]
    st.close()
    return syms


def tencent_symbol(sym: str) -> str:
    return ('sh' if sym[0] in ('6', '9', '5') else 'sz') + sym


def fetch_batch(codes: list[str]) -> dict:
    """取一批行情，失败按 `FETCH_ATTEMPTS` 重试（线性退避），**全部失败才抛**。

    ⚠️ 语义刻意保持 fail-closed：既不吞异常、也**不返回空 dict**
    （返回空 dict 会被上层误当「该批本就没数据」而静默放行，比抛异常更危险）。
    重试成功后走的是**同一套解析逻辑**，行为与改动前完全一致。
    抛出的仍是原始异常类型 ⇒ 上层 `type(exc).__name__` 留痕口径不变（如 'URLError'）。
    """
    q = ','.join(codes)
    url = f'https://qt.gtimg.cn/q={q}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = None
    last_exc = None
    for attempt in range(FETCH_ATTEMPTS):
        if attempt:
            time.sleep(FETCH_BACKOFF * attempt)
        try:
            raw = urllib.request.urlopen(req, timeout=20).read().decode('gbk', errors='ignore')
            break
        except (OSError, http.client.HTTPException) as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == FETCH_ATTEMPTS - 1:
                raise
    if raw is None:  # 防御：循环至少执行一次，不可达
        raise last_exc if last_exc is not None else RuntimeError('fetch_batch: 未执行任何尝试')
    out = {}
    for line in raw.strip().split(';'):
        m = re.match(r'v_(\w+)="(.*)"', line.strip())
        if not m:
            continue
        parts = m.group(2).split('~')
        if len(parts) < 40 or not parts[3]:
            continue
        try:
            price = float(parts[3])
            prev = float(parts[4]) if parts[4] else None
            chg = (price / prev - 1) * 100 if prev and prev > 0 else None
            vol = float(parts[6]) if parts[6] else 0  # 手
            amount = float(parts[37]) if len(parts) > 37 and parts[37] else 0  # 万元
            turnover = float(parts[38]) if len(parts) > 38 and parts[38] else None  # 换手%
            pe = float(parts[39]) if len(parts) > 39 and parts[39] else None
            code = m.group(1)[2:]
            out[code] = {'px': price, 'chg': chg, 'vol': vol, 'amt': amount,
                         'turn': turnover, 'name': parts[1]}
        except (ValueError, IndexError):
            continue
    return out


def main():
    syms = load_universe()
    t0 = time.time()
    allq = {}
    codes = [tencent_symbol(s) for s in syms]
    for i in range(0, len(codes), BATCH):
        chunk = codes[i:i + BATCH]
        try:
            allq.update(fetch_batch(chunk))
        except Exception as e:
            print(f'batch {i} fail: {e}')
        if (i // BATCH) % 3 == 2:
            time.sleep(0.3)
    dt = time.time() - t0
    rows = []
    for sym, v in allq.items():
        if v['chg'] is None or sym.startswith('900'):
            continue
        rows.append({'sym': sym, 'name': v['name'], 'chg': v['chg'], 'turn': v['turn'],
                     'amt': v['amt'], 'vol': v['vol'], 'l2': industry_of(sym, '2026-08-28') or ''})
    rows.sort(key=lambda x: -(x['chg'] or -99))
    from datetime import datetime
    now = datetime.now()
    fp = OUT / f'market_scan_{now.strftime("%Y%m%d_%H%M")}.json'
    fp.write_text(json.dumps({'time': now.strftime('%Y-%m-%d %H:%M:%S'), 'n': len(rows),
                              'rows': rows[:300]}, ensure_ascii=False), encoding='utf-8')
    print(f'全市场扫描 {len(rows)} 只 耗时 {dt:.1f}s')
    print('涨幅 Top10:')
    for r in rows[:10]:
        print(f"  {r['sym']} {r['name']}: {r['chg']:+.2f}% 换手{r['turn']}% 额{r['amt']/10000:.1f}亿")
    print('跌幅 Top5:')
    for r in rows[-5:]:
        print(f"  {r['sym']} {r['name']}: {r['chg']:+.2f}%")


if __name__ == '__main__':
    main()
