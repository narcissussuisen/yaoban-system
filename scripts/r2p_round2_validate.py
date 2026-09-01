"""R2' 第二轮验证：三引擎(B点∪抄底∪回踩低吸) + 实时/EOD对齐 + 敏感性曲面 + 日线级确认gate统计

整改项映射: ①实时性(EOD vs realtime对齐) ②回踩低吸检测器 ③日线级gate统计 ④敏感性曲面 ⑤诚邦日期复核

用法: python scripts/r2p_round2_validate.py [--surface]
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore  # noqa: E402
from core.intraday import detect_b_point, detect_dibu_buy, detect_pullback_buy  # noqa: E402
from core import strategies as S  # noqa: E402

# 48 锚（诚邦改 5/19=区间证据; 露笑/晶方 标注竞价边缘成交; 康达 标注日期存疑6/3-6/5）
ANCHORS = [
 ('2026-04-07','002580',16.065,'圣阳'), ('2026-04-20','002962',17.647,'五方'),
 ('2026-04-21','603002',13.540,'宏昌'), ('2026-04-21','603178',19.926,'圣龙'),
 ('2026-04-24','002176',12.574,'江特'), ('2026-05-07','605006',12.834,'山东玻纤'),
 ('2026-05-19','603316',21.337,'诚邦(5/19区间证据)'), ('2026-05-20','002156',60.468,'通富'),
 ('2026-05-20','603158',13.725,'腾龙'), ('2026-05-26','002617',9.533,'露笑(竞价边缘)'),
 ('2026-05-26','600719',9.423,'大连热电'), ('2026-05-26','000700',15.365,'模塑'),
 ('2026-05-26','000021',39.012,'深科技'), ('2026-05-27','600207',7.012,'安彩'),
 ('2026-05-27','603693',18.066,'江苏新能'), ('2026-06-01','002354',7.132,'天娱'),
 ('2026-06-02','605589',51.396,'圣泉'), ('2026-06-03','000733',54.940,'振华'),
 ('2026-06-03','600667',15.505,'太极'), ('2026-06-05','601137',23.677,'博威'),
 ('2026-06-08','603823',30.579,'百合花'), ('2026-06-08','002747',31.509,'埃斯顿'),
 ('2026-06-08','002669',16.760,'康达(6/3-6/5存疑)'), ('2026-06-16','600552',21.137,'凯盛'),
 ('2026-06-17','000823',20.906,'超声'), ('2026-06-17','600063',8.153,'皖维'),
 ('2026-06-18','002897',91.437,'意华'), ('2026-06-22','002407',39.792,'多氟多'),
 ('2026-06-23','600360',12.424,'华微'), ('2026-06-23','000737',14.744,'北方铜业'),
 ('2026-06-26','600584',108.647,'长电'), ('2026-06-26','603005',48.785,'晶方(竞价边缘)'),
 ('2026-06-26','605020',39.952,'永和'), ('2026-06-26','603078',46.464,'江化微'),
 ('2026-06-29','601958',27.839,'金钼'), ('2026-07-02','002584',10.093,'西陇'),
 ('2026-07-06','600379',15.865,'宝光'), ('2026-07-09','600360',15.325,'华微2'),
 ('2026-07-09','002137',11.894,'实益达'), ('2026-07-20','000948',14.504,'南天'),
 ('2026-07-20','001206',22.657,'依依'), ('2026-07-21','600584',74.273,'长电2'),
 ('2026-07-22','002379',18.526,'宏桥'), ('2026-07-23','600722',10.273,'金牛'),
 ('2026-07-27','001317',48.955,'三羊马'), ('2026-07-28','000676',7.372,'智度'),
 ('2026-07-29','002539',11.684,'云图'), ('2026-08-04','002303',4.701,'美盈森'),
]


def load_day(st, sym, d):
    rows = st.get_minute(sym, start=d, end=d)
    if not rows:
        return None, None, None
    df = pd.DataFrame(rows, columns=['symbol','freq','ts','open','high','low',
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
    return df, pc, prev5


def run_case(st, d, sym, px, name, max_pct=3.0, realtime=True, dip=2.0, lowopen=3.0, shrink=40.0):
    df, pc, prev5 = load_day(st, sym, d)
    if df is None:
        return ('NO_DATA', None, None, None, None, None)
    bpts = detect_b_point(df, prev_close=pc, max_pct=max_pct / 100.0)
    dpts = detect_dibu_buy(df, prev_close=pc, prev5_amt=prev5, dip_pct=dip,
                           lowopen_pct=lowopen, shrink_pct=shrink, realtime=realtime)
    ppts = detect_pullback_buy(df, prev_close=pc, max_pct=max_pct)
    cands = []
    for _, b in bpts.iterrows():
        cands.append((str(b['ts'])[11:16], float(b['price']), 'B' + str(b['kind'])))
    for _, b in dpts.iterrows():
        cands.append((str(b['ts'])[11:16], float(b['price']), str(b['kind'])))
    for _, b in ppts.iterrows():
        cands.append((str(b['ts'])[11:16], float(b['price']), 'pullback'))
    op = float(df['open'].iloc[0])
    hit = sorted([c for c in cands if c[1] <= px * 1.003], key=lambda x: x[0])
    first = sorted(cands, key=lambda x: x[0])[0] if cands else None
    # 日线级 gate 统计（整改③依据）: 收阳 + 放量(量>前日) + 突破前5日最高收盘
    dall = st.get_stock(sym)
    dates = [r[1] for r in dall]
    gate = None
    if d in dates:
        i0 = dates.index(d)
        r = dall[i0]
        yang = float(r[5]) > float(r[2])
        vol_up = i0 > 0 and float(r[6]) > float(dall[i0 - 1][6])
        brk = False
        if i0 >= 5:
            hi5 = max(float(x[5]) for x in dall[i0 - 5:i0])
            brk = float(r[5]) >= hi5
        gate = {'yang': yang, 'vol_up': vol_up, 'brk_5d_close': brk}
    if hit:
        c = hit[0]
        return ('HIT', c[0], c[1], round((c[1]/px-1)*100, 2), c[2], gate)
    if first:
        return ('MISS', first[0], first[1], round((first[1]/px-1)*100, 2), first[2], gate)
    return ('MISS', None, None, None, None, gate)


def main():
    st = QFQStore('2026')
    do_surface = '--surface' in sys.argv
    if not do_surface:
        print('==== R2\' 第二轮 三引擎回放 (B点∪抄底∪回踩低吸, realtime=True, max_pct=3) ====')
        n_hit = n_all = 0
        kinds = {}
        gate_stat = {'yang': 0, 'vol_up': 0, 'brk': 0, 'n': 0}
        for d, sym, px, name in ANCHORS:
            res, bt, bp, dv, kind, gate = run_case(st, d, sym, px, name)
            if res == 'HIT':
                n_hit += 1
                kinds[kind] = kinds.get(kind, 0) + 1
            if res in ('HIT', 'MISS'):
                n_all += 1
            if gate:
                gate_stat['n'] += 1
                gate_stat['yang'] += gate['yang']
                gate_stat['vol_up'] += gate['vol_up']
                gate_stat['brk'] += gate['brk_5d_close']
            if res == 'MISS' and len(name) < 9:
                print(f'  MISS {d} {sym} {name} px={px} 首个信号={bt}@{bp} kind={kind}')
        print(f'--- 命中 {n_hit}/{n_all} = {n_hit/max(n_all,1)*100:.1f}% 类型={kinds}')
        print(f'--- 日线级gate统计(买入日): 收阳 {gate_stat["yang"]}/{gate_stat["n"]}, '
              f'放量(量>前日) {gate_stat["vol_up"]}/{gate_stat["n"]}, '
              f'突破前5日最高收盘 {gate_stat["brk"]}/{gate_stat["n"]}')
        # EOD vs realtime 对齐（整改①）
        print()
        print('==== 整改①: dibu 检测器 EOD vs realtime 对齐 ====')
        diff = []
        for d, sym, px, name in ANCHORS:
            df, pc, prev5 = load_day(st, sym, d)
            if df is None:
                continue
            r1 = detect_dibu_buy(df, prev_close=pc, prev5_amt=prev5, realtime=True)
            r2 = detect_dibu_buy(df, prev_close=pc, prev5_amt=prev5, realtime=False)
            s1 = set(zip(r1['ts'], r1['kind'])) if len(r1) else set()
            s2 = set(zip(r2['ts'], r2['kind'])) if len(r2) else set()
            if s1 != s2:
                diff.append((d, sym, name, s1, s2))
        print(f'  {len(ANCHORS)} 锚中 EOD≠realtime 的案例: {len(diff)}')
        for x in diff:
            print(f'    {x[0]} {x[1]} {x[2]}: rt={x[3]} eod={x[4]}')
        # 诚邦 R1' 复核（整改⑤）
        print()
        print('==== 整改⑤: 诚邦 R1\'复核 (5/19 vs 5/20) ====')
        dall = pd.DataFrame(st.get_stock('603316'),
                            columns=['symbol','date','open','high','low','close','volume','amount'])
        dall['date'] = dall['date'].astype(str)
        sig = S.detect_huigui_v5(dall)
        for target in ['2026-05-19', '2026-05-20']:
            v = bool(sig[dall['date'] == target].any())
            print(f'  detect_huigui_v5(603316) 信号日 {target} = {v}')
    if do_surface:
        print('==== 整改④: 敏感性曲面 ====')
        print('三引擎命中数 (48锚, max_pct=3):')
        grid = [(1.5, 2, 40), (1.5, 3, 40), (1.5, 4, 40), (2.0, 2, 40),
                (2.0, 3, 30), (2.0, 3, 50), (2.0, 4, 40), (2.5, 3, 40),
                (2.0, 3, 40)]
        for dip, lowopen, shrink in grid:
            nh = 0
            for d, sym, px, name in ANCHORS:
                res, *_ = run_case(st, d, sym, px, name, dip=dip, lowopen=lowopen, shrink=shrink)
                if res == 'HIT':
                    nh += 1
            print(f'  dip={dip} lowopen={lowopen} shrink={shrink}: {nh}/48')
        for mp in [3.0, 5.0, 7.0]:
            nh = 0
            for d, sym, px, name in ANCHORS:
                res, *_ = run_case(st, d, sym, px, name, max_pct=mp)
                if res == 'HIT':
                    nh += 1
            print(f'  max_pct={mp}: {nh}/48')
    st.close()


if __name__ == '__main__':
    main()