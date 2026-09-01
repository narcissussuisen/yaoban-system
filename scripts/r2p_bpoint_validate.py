"""R2' 买点层验证：B点确认价回放（选手实盘买入价 vs 引擎B点）

验证口径（V5_ALIGNMENT #7：B点确认价为主口径）:
  选手买入日 = 分时确认日；引擎 detect_b_point 当日触发 B 点，
  且 B 点价 ≤ 选手买入价 × (1+0.3%)（回踩价成交，0.3% 成交容差）→ 命中
对照: 开盘价买入（旧执行层口径）vs B点价 的执行 alpha

3 只锚价格单数字修正（OCR 假设，落在 qfq 当日区间内为证）:
  诚邦 603316 5/20: 21.337→23.337; 腾龙 603158 5/20: 14.725→13.725; 长电 600584 6/26: 88.647→108.647

用法: python scripts/r2p_bpoint_validate.py [--max-pct 3]
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore  # noqa: E402
from core.intraday import detect_b_point, detect_dibu_buy, prev_close_of  # noqa: E402

ANCHORS = [
 ('2026-04-07','002580',16.065,'上升回档','圣阳'),
 ('2026-04-20','002962',17.647,'上升回档','五方'),
 ('2026-04-21','603002',13.540,'上升回档','宏昌'),
 ('2026-04-21','603178',19.926,'上升回档','圣龙'),
 ('2026-04-24','002176',12.574,'上升回档','江特'),
 ('2026-05-07','605006',12.834,'上升回档','山东玻纤'),
 ('2026-05-20','603316',23.337,'上升回档','诚邦(OCR修正)'),
 ('2026-05-20','002156',60.468,'上升回档','通富'),
 ('2026-05-20','603158',13.725,'低吸','腾龙(OCR修正)'),
 ('2026-05-26','002617',9.533,'上升回档','露笑'),
 ('2026-05-26','600719',9.423,'上升回档','大连热电'),
 ('2026-05-26','000700',15.365,'上升回档','模塑'),
 ('2026-05-26','000021',39.012,'上升回档','深科技'),
 ('2026-05-27','600207',7.012,'上升回档','安彩'),
 ('2026-05-27','603693',18.066,'上升回档','江苏新能'),
 ('2026-06-01','002354',7.132,'低位科技','天娱'),
 ('2026-06-02','605589',51.396,'趋势反包','圣泉'),
 ('2026-06-03','000733',54.940,'上升回档','振华'),
 ('2026-06-03','600667',15.505,'上升回档','太极'),
 ('2026-06-05','601137',23.677,'趋势反包','博威'),
 ('2026-06-08','603823',30.579,'上升回档','百合花'),
 ('2026-06-08','002747',31.509,'上升回档','埃斯顿'),
 ('2026-06-08','002669',16.760,'上升回档','康达'),
 ('2026-06-16','600552',21.137,'上升回档','凯盛'),
 ('2026-06-17','000823',20.906,'上升回档','超声'),
 ('2026-06-17','600063',8.153,'上升回档','皖维'),
 ('2026-06-18','002897',91.437,'4天2板','意华'),
 ('2026-06-22','002407',39.792,'上升回档','多氟多'),
 ('2026-06-23','600360',12.424,'上升回档','华微'),
 ('2026-06-23','000737',14.744,'超跌反弹','北方铜业'),
 ('2026-06-26','600584',108.647,'上升回档','长电(OCR修正)'),
 ('2026-06-26','603005',48.785,'上升回档','晶方'),
 ('2026-06-26','605020',39.952,'趋势反包','永和'),
 ('2026-06-26','603078',46.464,'上升回档','江化微'),
 ('2026-06-29','601958',27.839,'上升回档','金钼'),
 ('2026-07-02','002584',10.093,'上升回档','西陇'),
 ('2026-07-06','600379',15.865,'上升回档','宝光'),
 ('2026-07-09','600360',15.325,'上升回档','华微2'),
 ('2026-07-09','002137',11.894,'上升回档','实益达'),
 ('2026-07-20','000948',14.504,'上升回档','南天'),
 ('2026-07-20','001206',22.657,'上升回档','依依'),
 ('2026-07-21','600584',74.273,'超跌反弹','长电2'),
 ('2026-07-22','002379',18.526,'上升回档','宏桥'),
 ('2026-07-23','600722',10.273,'上升回档','金牛'),
 ('2026-07-27','001317',48.955,'上升回档','三羊马'),
 ('2026-07-28','000676',7.372,'超跌反弹','智度'),
 ('2026-07-29','002539',11.684,'超跌反弹','云图'),
 ('2026-08-04','002303',4.701,'上升回档','美盈森'),
]


def main():
    st = QFQStore('2026')
    max_pct = 3.0
    if '--max-pct' in sys.argv:
        max_pct = float(sys.argv[sys.argv.index('--max-pct') + 1])
    rows_out = []
    for d, sym, px, zf, name in ANCHORS:
        mrows = st.get_minute(sym, start=d, end=d)
        if not mrows:
            rows_out.append((d, sym, name, zf, px, 'NO_DATA', None, None, None, None, None))
            continue
        df = pd.DataFrame(mrows, columns=['symbol','freq','ts','open','high','low',
                                         'close','volume','amount'])
        dall = st.get_stock(sym)
        dates = [r[1] for r in dall]
        pc = None
        prev5 = None
        if d in dates:
            i0 = dates.index(d)
            if i0 > 0:
                pc = float(dall[i0 - 1][5])
            if i0 >= 5:
                prev5 = float(pd.Series([r[7] for r in dall[i0 - 5:i0]]).mean())
        if pc is None:
            pc = prev_close_of(df, d)
        bpts = detect_b_point(df, prev_close=pc, max_pct=max_pct / 100.0)
        dpts = detect_dibu_buy(df, prev_close=pc, prev5_amt=prev5)
        op = float(df['open'].iloc[0])
        # 双引擎命中: B点 或 抄底信号, 价 ≤ 选手价×1.003
        cands = []
        for _, b in bpts.iterrows():
            cands.append(('B', str(b['ts'])[11:16], float(b['price']), str(b['kind'])))
        for _, b in dpts.iterrows():
            cands.append(('D', str(b['ts'])[11:16], float(b['price']), str(b['kind'])))
        hit = [c for c in cands if c[2] <= px * 1.003]
        if hit:
            c = sorted(hit, key=lambda x: x[1])[0]
            rows_out.append((d, sym, name, zf, px, 'HIT', c[1], c[2],
                             round((c[2]/px-1)*100, 2), round((c[2]/op-1)*100, 2), c[3]))
        else:
            c = sorted(cands, key=lambda x: x[1])[0] if cands else None
            rows_out.append((d, sym, name, zf, px, 'MISS',
                             c[1] if c else None, c[2] if c else None,
                             round((c[2]/px-1)*100, 2) if c else None,
                             round((c[2]/op-1)*100, 2) if c else None,
                             c[3] if c else None))
    st.close()
    print(f'== R2\' 买点确认回放 (B点∪抄底4规则) max_pct={max_pct}% ==')
    print(f'{"日期":<12}{"代码":<8}{"名称":<12}{"战法":<8}{"买入价":>8} {"结果":<5}{"信号时间":>8} {"信号价":>8} {"vs选手%":>8} {"vs开盘%":>8} {"类型":<12}')
    n_hit = n_all = 0
    kind_cnt = {}
    for r in rows_out:
        d, sym, name, zf, px, res, bt, bp, dv, do, kind = r
        if res in ('HIT', 'MISS'):
            n_all += 1
        if res == 'HIT':
            n_hit += 1
            kind_cnt[kind] = kind_cnt.get(kind, 0) + 1
        bts = bt if bt else '-'
        bps = f'{bp:.3f}' if bp is not None else '-'
        dvs = f'{dv:+.2f}' if dv is not None else '-'
        dos = f'{do:+.2f}' if do is not None else '-'
        kd = kind if kind else '-'
        print(f'{d:<12}{sym:<8}{name:<12}{zf:<8}{px:>8.3f} {res:<5}{bts:>8} {bps:>8} {dvs:>8} {dos:>8} {kd:<12}')
    print(f'--- 命中 {n_hit}/{n_all} = {n_hit/max(n_all,1)*100:.1f}% (类型分布: {kind_cnt}) ---')
    hits = [r for r in rows_out if r[5] == 'HIT' and r[9] is not None]
    if hits:
        dv_mean = sum(r[8] for r in hits) / len(hits)
        do_mean = sum(r[9] for r in hits) / len(hits)
        print(f'命中案例: 信号价 vs 选手价 均值 {dv_mean:+.2f}% (负=引擎买得更低)')
        print(f'命中案例: 信号价 vs 开盘价 均值 {do_mean:+.2f}% (开盘执行 = 0 基准)')
    miss = [r for r in rows_out if r[5] == 'MISS']
    print(f'--- MISS {len(miss)} 例明细 ---')
    for r in miss:
        extra = f'{r[7]:.3f}({r[10]})' if r[7] is not None else '无信号'
        print(f'  {r[0]} {r[1]} {r[2]} px={r[4]:.3f} {extra}')


if __name__ == '__main__':
    main()