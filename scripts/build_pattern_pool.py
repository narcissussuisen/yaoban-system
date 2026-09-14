"""构建并落盘当日「战法池」（形态筛选层 / ROADMAP §1.2 第三层）。

用法:
    python scripts/build_pattern_pool.py --asof 2026-09-11 --day 2026-09-14
    python scripts/build_pattern_pool.py --asof 2026-09-11 --patterns huigui

口径（**防前视硬约束**）:
    `--asof` 必须是 **T-1**（信号窗口的最新交易日），`--day` 是要服务的交易日。
    本脚本只喂 <= asof 的日线，且把 asof 与 day 同时写进产物，供 scan 侧核对。
    若 --asof >= --day 直接拒绝（防手滑造成前视）。

产物:
    outputs/patterns/<day>_pattern_pool.json
    {day, asof, lookback, patterns, stats:{...}, pool:[{sym,pattern,pattern_cn,patterns,
     sig_date,bars_since_sig,close_asof}], name_map:{sym:name}, built_at, sha256 自校验由调用方做}
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'scripts'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

BASE = pathlib.Path(__file__).resolve().parent.parent
OUTDIR = BASE / 'outputs' / 'patterns'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--asof', required=True, help='信号窗口最新交易日 = T-1（防前视，必填）')
    ap.add_argument('--day', required=True, help='要服务的交易日 T')
    ap.add_argument('--lookback', type=int, default=4, help='信号日回溯窗口（默认 4，与 plan_daily 一致）')
    ap.add_argument('--patterns', default='huigui,zt_huicai,xianren,qu_shi_fanbao',
                    help='启用战法（逗号分隔；单战法用于消融）')
    ap.add_argument('--out', default='', help='输出路径（缺省 outputs/patterns/<day>_pattern_pool.json）')
    args = ap.parse_args()

    day, asof = args.day.strip(), args.asof.strip()
    if asof >= day:
        print(f'ERROR: --asof({asof}) 必须 < --day({day})（T-1 口径，防前视）', file=sys.stderr)
        return 2
    pats = tuple(p.strip() for p in args.patterns.split(',') if p.strip())

    import plan_daily
    from core.pattern_pool import build_pattern_pool, DETECTORS

    unknown = [p for p in pats if p not in DETECTORS]
    if unknown:
        print(f'ERROR: 未知战法 {unknown}；可用 {sorted(DETECTORS)}', file=sys.stderr)
        return 2

    t0 = time.time()
    print(f'[1/3] 载入全市场日线 ...', flush=True)
    dmap = plan_daily.load_all_daily()
    print(f'      载入 {len(dmap)} 只，用时 {time.time() - t0:.1f}s', flush=True)

    print(f'[2/3] 构建战法池 asof={asof} lookback={args.lookback} patterns={pats} ...', flush=True)
    pool, stats = build_pattern_pool(dmap, asof=asof, lookback=args.lookback, patterns=pats)

    # 名称映射（只用现成表，不臆造）
    name_map = {}
    try:
        doc = json.loads((BASE / 'data' / 'stock_names_stocks.json').read_text(encoding='utf-8'))
        nm = doc.get('names', doc)
        name_map = {r['sym']: nm.get(r['sym'], '') for r in pool}
    except Exception as exc:
        print(f'      WARN 名称表不可用: {type(exc).__name__}', file=sys.stderr)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    fp = pathlib.Path(args.out) if args.out else (OUTDIR / f'{day}_pattern_pool.json')
    doc = {
        'day': day, 'asof': asof, 'lookback': args.lookback, 'patterns': list(pats),
        'stats': stats, 'pool': pool, 'name_map': name_map,
        'built_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'core.pattern_pool.build_pattern_pool ← core.strategies（参数: config/parameters.toml）',
    }
    fp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')

    print(f'[3/3] 写入 {fp}')
    print(f'      战法池 {stats["n_pool"]} 只 / 扫描 {stats["n_syms_scanned"]} 只 '
          f'(太短跳过 {stats["n_skipped_short"]})  用时 {time.time() - t0:.1f}s')
    print(f'      分战法: {stats["by_pattern"]}')
    for r in pool[:20]:
        print(f'        {r["sym"]} {name_map.get(r["sym"], ""):<8} {r["pattern_cn"]:<8} '
              f'信号日 {r["sig_date"]} (T-{r["bars_since_sig"]}日) 收 {r["close_asof"]}')
    if len(pool) > 20:
        print(f'        ... 共 {len(pool)} 只')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
