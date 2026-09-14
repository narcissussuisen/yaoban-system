"""H7.2 盘中执行层：分时 B 点检测 + 均价线（VWAP）盘中信号

数据: minute_kline 表（freq='1m'/'5m'，volume 单位=股）

B 点定义（v08/v12 手册「放量突破均价线回踩站稳」+ C13 大连电瓷四条件）:
  ① 白线向上：VWAP（分时均价线）处于上升——当前 VWAP > lookback 分钟前 × (1+eps)
  ② 拉升有量：单根分钟量 > 前 lookback 根均量 × vol_mult（放量突破）
  ③ 回踩站稳：拉升后回踩 VWAP 不破（low ≥ VWAP×(1-tol)），随后重新收上 VWAP
  ④ 涨幅过滤：距昨收 ≤ max_pct（低吸口径 3%，强势口径可配 5~7%）
  触发 = ①②③④ 同时成立的分钟 → B 点；买入价 ≈ 触发分钟 close（另加滑点）

另含: 分时均价线（VWAP）、开盘走弱判定（open_weak）、做 T 高低点（T 信号，H7.3 用）
"""
from __future__ import annotations

import pandas as pd

# ⭐ TDX 分钟线类别常量 —— **单一事实源**（2026-09-14 收敛）
#   `pytdx.TdxHq_API.get_security_bars(category, …)` 的 category 取值：
#     0 = **5 分钟**   1 = 15 分钟   2 = 30 分钟   3 = 1 小时   4 = 日线
#     7 = **1 分钟**   8 = **1 分钟**   9 = 日线
#   ⚠️ **category=0 是 5 分钟，不是 1 分钟** —— 本仓已因此踩过两次同类事故：
#     ① 2026-09-11 `tick_monitor` 取 cat=0 但下游按 1 分钟口径过滤当日 bar ⇒ 开盘 9 分钟只有
#        2 根 ⇒ 撞 `len(rows)<3` ⇒ 止损/VWAP 判定与 pos_live 写在同一个被 continue 跳过的循环体内
#        ⇒ **tick 作为唯一盘中卖出执行器对全部持仓失明**（300468 压在止损线上仍未执行）。
#        当时引入 `KLINE_1MIN=8` 修掉了，但**没有推广到其它脚本**。
#     ② 2026-09-14 `scan_and_confirm.pull_minutes` 同样取 cat=0 ⇒ 生产跑 5 分钟、回测
#        （`QFQStore.get_minute` freq 硬编码 `1m`）跑 1 分钟 = **生产/回测不同源**；
#        而 `docs/TRADER_ROADMAP_v2.md §1.2` 的约定本就是「确认队列标的拉 **1m** 分时」，
#        且 `participation_cap = bar_volume // 20` 的注释写明是「5% of next-**minute** volume」。
#   ⇒ 凡取盘中分钟线，一律用本常量，勿再写字面量 0。
KLINE_1MIN = 8      # pytdx KLINE_TYPE_1MIN
KLINE_5MIN = 0      # 仅在明确需要 5 分钟聚合时使用（需在调用处写明理由）

# 腾讯 mkline 对应的周期串（备胎源口径，与 KLINE_1MIN 语义对齐）
TX_PERIOD_1MIN = "m1"


# ---------- 工具 ----------


def vwap_series(df: pd.DataFrame) -> pd.Series:
    """分时均价线（当日累计额/累计量）。amount 为 0/缺失时用 close×vol 近似。"""
    v = df["volume"].clip(lower=0)
    if "amount" in df.columns and df["amount"].fillna(0).sum() > 0:
        amt = df["amount"].fillna(0).clip(lower=0)
    else:
        amt = df["close"] * v
    csum_v = v.cumsum().replace(0, float("nan"))
    return (amt.cumsum() / csum_v).ffill()


def prev_close_of(df: pd.DataFrame, date: str) -> float | None:
    """date 前一个交易日（df 内）的收盘价"""
    dates = sorted(df["ts"].str[:10].unique())
    if date not in dates:
        return None
    i = dates.index(date)
    if i == 0:
        return None
    prev = df[df["ts"].str[:10] == dates[i - 1]]
    return float(prev["close"].iloc[-1]) if len(prev) else None


# ---------- B 点检测 ----------


def detect_b_point(df: pd.DataFrame, prev_close: float | None = None,
                   vol_mult: float = 1.5, tol: float = 0.003, max_pct: float = 0.03,
                   lookback: int = 20, vwap_eps: float = 0.001,
                   open_mult: float = 1.3, open_win: int = 30,
                   cool_min: int = 30, surge_win: int = 60,
                   start_hm: str = "09:35",
                   end_hm: str = "14:45") -> pd.DataFrame:
    """对单日 1 分钟数据检测 B 点（两类 + 冷却合并）。

    df: 单日 1m 数据 [ts, open, high, low, close, volume, amount]
    prev_close: 昨收（决定涨幅%）；缺省用 df 内前一交易日收盘

    B1 开盘强势（开盘放量直接上车）: 开盘 open_win 分钟内，放量(>首根×open_mult)
        + 白线向上 + 价格始终不破均价线 + 涨幅≤max_pct
    B2 回踩确认（放量突破→回踩站稳）: 此前 60 分钟内出现过放量拉升，当前回踩
        均价线不破并重新收上均价线 + 白线向上 + 涨幅≤max_pct
    合并: 触发后 cool_min 分钟内不重复触发；[start_hm, end_hm] 时间窗外不触发。

    返回: DataFrame[{ts, price, vwap, pct, kind}]（kind: B1/B2）
    """
    if df is None or df.empty or len(df) < 3:
        return pd.DataFrame(columns=["ts", "price", "vwap", "pct", "kind"])
    if prev_close is None or prev_close <= 0:
        prev_close = float(df["close"].iloc[0])  # 无法判定涨幅时退化为相对当日
    n = len(df)
    lb = max(1, min(lookback, n - 1))
    v = df["volume"].clip(lower=0)
    vwap = vwap_series(df)
    hm = df["ts"].str[11:16]
    in_win = (hm >= start_hm) & (hm <= end_hm)

    # 放量拉升（盘中：量 > 前 lb 根均量 × vol_mult 且收上均价线）
    vol_ma = v.rolling(lb).mean().shift(1)
    surge_mid = (v > vol_ma * vol_mult) & (df["close"] > vwap)
    # 开盘放量（前 open_win 分钟：量 > 首根竞价量 × open_mult）
    first_vol = float(v.iloc[0]) if v.iloc[0] > 0 else float(v.iloc[1:].mean())
    open_surge = pd.Series(False, index=df.index)
    open_surge.iloc[:open_win] = (v.iloc[:open_win] > first_vol * open_mult).to_numpy()
    surge = (surge_mid | open_surge) & in_win

    # ① 白线向上：VWAP 高于过去 lb 分钟最低 VWAP（开盘阶段退化为 vs 首根）
    vwap_floor = vwap.rolling(lb).min().shift(1).fillna(vwap.iloc[0])
    vwap_up = vwap > vwap_floor * (1 + vwap_eps)

    # 回踩：low 触及 VWAP 但不破；站稳：收上 VWAP 且 low 不破
    hold = df["low"] >= vwap * (1 - tol)
    reclaim = (df["close"] > vwap) & hold

    pct = df["close"] / prev_close - 1
    pct_ok = (pct <= max_pct) & (pct >= -0.05)

    # B1 开盘强势（开盘窗口内放量且未破均价线）
    open_zone = pd.Series(False, index=df.index)
    open_zone.iloc[:open_win] = True
    b1 = surge & open_zone & hold & vwap_up & pct_ok
    # B2 回踩确认：surge_win 根内有放量拉升，当前回踩后重新站稳（1m=60 根≈60 分钟；5m=12 根）
    had_surge = surge.rolling(surge_win, min_periods=1).max().shift(1).fillna(False).astype(bool)
    b2 = reclaim & had_surge & vwap_up & pct_ok & in_win & ~open_zone

    b = (b1 | b2).to_numpy()
    # 冷却合并：同波拉升只取首个触发
    merged = []
    last = -10**9
    for i in range(len(b)):
        if b[i] and i - last >= cool_min:
            merged.append(i)
            last = i

    close_arr = df["close"].to_numpy()
    vwap_arr = vwap.to_numpy()
    rows = []
    for i in merged:
        rows.append({
            "ts": str(df["ts"].iloc[i]),
            "price": round(float(close_arr[i]), 3),
            "vwap": round(float(vwap_arr[i]), 3),
            "pct": round(float(pct.iloc[i]) * 100, 2),
            "kind": "B1" if open_zone.iloc[i] else "B2",
        })
    return pd.DataFrame(rows)


def day_b_points(store, symbol: str, date: str, freq: str = "1m",
                 **kw) -> pd.DataFrame:
    """从 DB 取数据检测当日 B 点（自动带前一交易日作昨收）"""
    from data.minute import market_of  # noqa: F401 - 保持模块独立引用清晰

    rows = store.get_minute(symbol, freq)
    if not rows:
        return pd.DataFrame(columns=["ts", "price", "vwap", "pct", "kind"])
    df = pd.DataFrame(rows, columns=["symbol", "freq", "ts", "open", "high", "low",
                                     "close", "volume", "amount"])
    day = df[df["ts"].str[:10] == date].copy()
    if day.empty:
        return pd.DataFrame(columns=["ts", "price", "vwap", "pct", "kind"])
    pc = prev_close_of(df, date)
    return detect_b_point(day, prev_close=pc, **kw)


# ---------- 抄底买点 4 规则（8/19 新证据 F0019 / PLAYER_NEW_VIDEOS_2026-08-26） ----------


def detect_dibu_buy(df: pd.DataFrame, prev_close: float | None = None,
                    prev5_amt: float | None = None,
                    dip_pct: float = 2.0, lowopen_pct: float = 3.0,
                    rise_pct: float = 1.0, shrink_pct: float = 40.0,
                    max_decline_pct: float = 2.0,
                    realtime: bool = True) -> pd.DataFrame:
    """抄底买点 4 规则（选手 8/19 新证据：在下跌节奏中买入，上涨节奏中卖出）:

    ① 早盘下杀下午回拉 → 买: 早盘(≤10:30)出现较昨收跌幅≥dip_pct% 的低点，
        且 13:00 后重新站上分时均价线 → 买点=站上时刻（逐bar判定，无前视）
    ② 低开幅度大低开高走 → 买: 开盘 ≤ 昨收×(1-lowopen_pct%)，
        且盘中价 ≥ 开盘×(1+rise_pct%) 且站上均价线 → 买点（逐bar判定，无前视）
    ③ 大幅缩量力竭 → 买: 成交额较前5日均额缩量≥shrink_pct% 且跌幅≤max_decline_pct%
        → 尾盘(14:30)买点。
        realtime=True: 缩量判定用 14:30 前累计成交额按 240/210 比例外推（判定时点口径，
        不用 14:30 后数据）; realtime=False: 用全天成交额与收盘（EOD 口径，回放用）
    ④ 早盘下杀下午续跌 → 不买: 早盘低点≤昨收×(1-dip_pct%) 且 13:00 后创日内新低。
        realtime=True: 锁存式增量否决——下午创新低时刻起锁存，之后不再产生①②③信号
        （锁存前已触发的信号保留）; realtime=False: 当日终局判定（全天下午低点否决全天，
        追溯性否决早间信号——评审标注：EOD 口径仅用于回放对齐，实时系统必须 realtime=True）

    返回 DataFrame[{ts, price, pct, kind}]（kind: dibu_pullback/dibu_lowopen/dibu_shrink）
    """
    cols = ["ts", "price", "pct", "kind"]
    if df is None or df.empty or len(df) < 3:
        return pd.DataFrame(columns=cols)
    if prev_close is None or prev_close <= 0:
        prev_close = float(df["close"].iloc[0])
    hm = df["ts"].str[11:16]
    vwap = vwap_series(df)
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    op = float(df["open"].iloc[0])
    morning = hm <= "10:30"
    afternoon = hm >= "13:00"

    dip_thr = prev_close * (1 - dip_pct / 100.0)
    mor = morning.to_numpy()
    aft = afternoon.to_numpy()
    mor_low_min = low[mor].min() if mor.any() else None
    has_morning_dip = mor_low_min is not None and mor_low_min <= dip_thr
    if not realtime:
        # ④ EOD 终局判定（回放口径）：早盘下杀 + 全天下午创新低 → 当日无买点（追溯否决）
        if has_morning_dip and aft.any() and low[aft].min() < mor_low_min:
            return pd.DataFrame(columns=cols)
    # realtime 锁存：下午创新低时刻（位置索引；若无则 None）
    lock_pos = None
    if has_morning_dip and aft.any():
        import numpy as np
        for k in np.where(aft)[0]:
            if low[k] < mor_low_min:
                lock_pos = int(k)
                break

    def _locked(i: int) -> bool:
        return realtime and lock_pos is not None and i >= lock_pos

    rows = []
    # ① 早盘下杀下午回拉
    if has_morning_dip:
        rec = afternoon & (df["close"] > vwap)
        if rec.any():
            i = int(df.index.get_loc(rec.idxmax()))
            if not _locked(i):
                rows.append({"ts": str(df["ts"].iloc[i]), "price": round(float(close[i]), 3),
                             "pct": round((close[i] / prev_close - 1) * 100, 2),
                             "kind": "dibu_pullback"})
    # ② 低开高走
    if op <= prev_close * (1 - lowopen_pct / 100.0):
        go = (df["close"] >= op * (1 + rise_pct / 100.0)) & (df["close"] > vwap)
        if go.any():
            i = int(df.index.get_loc(go.idxmax()))
            if not _locked(i):
                rows.append({"ts": str(df["ts"].iloc[i]), "price": round(float(close[i]), 3),
                             "pct": round((close[i] / prev_close - 1) * 100, 2),
                             "kind": "dibu_lowopen"})
    # ③ 缩量力竭（尾盘 14:30）
    if prev5_amt is not None and prev5_amt > 0:
        tail = df[hm >= "14:30"]
        if not tail.empty:
            i = int(df.index.get_loc(tail.index[0]))  # 位置索引（df 可能是非零起始索引的视图）
            if realtime:
                # 判定时点口径：14:30 前累计成交额按 240/210 外推 + 14:30 价
                up_to = df.iloc[:i + 1]
                day_amt = float(up_to["amount"].sum()) * 240.0 / (i + 1)
                day_decline = close[i] / prev_close - 1
            else:
                day_amt = float(df["amount"].sum())
                day_decline = close[-1] / prev_close - 1
            if (day_amt <= prev5_amt * (1 - shrink_pct / 100.0)
                    and day_decline >= -max_decline_pct / 100.0
                    and not _locked(i)):
                rows.append({"ts": str(df["ts"].iloc[i]), "price": round(float(close[i]), 3),
                             "pct": round((close[i] / prev_close - 1) * 100, 2),
                             "kind": "dibu_shrink"})
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows)
    # 同日内多信号：保留最早触发
    return out.sort_values("ts").iloc[:1]


# ---------- 回踩低吸检测器（R2' 第二轮：无放量要求的温和回踩，选手 1-5% 区间低吸实证） ----------


def detect_pullback_buy(df: pd.DataFrame, prev_close: float | None = None,
                        min_pct: float = 0.0, max_pct: float = 5.0,
                        tol: float = 0.003, start_hm: str = "09:35",
                        end_hm: str = "14:45", cool_min: int = 30) -> pd.DataFrame:
    """温和走强确认（选手盘中低吸的共性口径——评审复算定名）：价格在分时均价线上方温和走强。

    三阶段状态机（评审整改后真实时序）:
      above: 收盘站上均价线（close > vwap）
      touch: 在 above 之后，回踩触及均价线附近（low ≤ vwap×(1+tol)）
      fire : touch 之后重新收上均价线 → 买点=收复时刻；涨幅 ∈ [min_pct, max_pct]%
    触发后状态重置（下一波重新计数）。不要求此前放量拉升（区别于 B2）。
    与 B2 的区别：不要求放量拉升——选手大量买入为温和盘中低吸（19 例无信号中 10 例属此）。

    返回 DataFrame[{ts, price, vwap, pct, kind='pullback'}]（冷却合并同 B 点）
    """
    cols = ["ts", "price", "vwap", "pct", "kind"]
    if df is None or df.empty or len(df) < 5:
        return pd.DataFrame(columns=cols)
    if prev_close is None or prev_close <= 0:
        prev_close = float(df["close"].iloc[0])
    vwap = vwap_series(df)
    close = df["close"]
    low = df["low"]
    hm = df["ts"].str[11:16]
    pct = close / prev_close - 1
    in_win = (hm >= start_hm) & (hm <= end_hm)
    # 三阶段状态机: 0=未上穿, 1=已上穿等回踩, 2=已回踩等收复
    above = (close > vwap).to_numpy()
    touch = (low <= vwap * (1 + tol)).to_numpy()
    phase = 0
    sig = [False] * len(df)
    for i in range(len(df)):
        if not in_win.iloc[i] or not (min_pct / 100.0 <= pct.iloc[i] <= max_pct / 100.0):
            # 时间窗外/涨幅越界：仍推进状态（等待期可跨窗口边界）
            if phase == 0 and above[i]:
                phase = 1
            elif phase == 1 and touch[i]:
                phase = 2
            continue
        if phase == 0:
            if above[i]:
                phase = 1
        elif phase == 1:
            if touch[i]:
                phase = 2
            elif not above[i]:
                phase = 0  # 掉回均价线下方：重置（未回踩先破位）
        else:  # phase == 2
            if above[i]:
                sig[i] = True
                phase = 0  # 触发后重置
            elif close.iloc[i] < vwap.iloc[i] * (1 - tol):
                phase = 0  # 回踩变破位：重置
    # 冷却合并
    merged = []
    last = -10 ** 9
    for i in range(len(sig)):
        if sig[i] and i - last >= cool_min:
            merged.append(i)
            last = i
    rows = []
    for i in merged:
        rows.append({"ts": str(df["ts"].iloc[i]),
                     "price": round(float(close.iloc[i]), 3),
                     "vwap": round(float(vwap.iloc[i]), 3),
                     "pct": round(float(pct.iloc[i]) * 100, 2),
                     "kind": "pullback"})
    return pd.DataFrame(rows)


# ---------- 日线级确认 gate（R2' 第二轮 整改③：PLAYER_REVIEW 8/26「次日放量收阳、突破回档小高点再进」） ----------


def day_confirm_gate(daily_df: pd.DataFrame, date: str, mode: str = "huigui") -> dict:
    """买入日线级确认 gate（按战法分模式——「突破小高点」仅适用于上升回档口径）。

    daily_df: 日线 DataFrame（date 升序, columns: date/open/high/low/close/volume）
    mode:
      huigui(上升回档): 收阳 且 收盘 ≥ 前5日最高收盘（突破回档小高点）
      fanbao(趋势反包): 收阳 且 收盘 > 前日最高（反包前阴线）
      chaodie(超跌反弹): 收阳（止跌阳线即可——北方铜业6/23 选手买在阴线日，属模式内例外）
    返回 {pass, yang, brk, note}
    """
    out = {'pass': False, 'yang': False, 'brk': False, 'note': ''}
    dates = daily_df['date'].astype(str).tolist()
    if date not in dates:
        out['note'] = '日期不在库'
        return out
    i = dates.index(date)
    r = daily_df.iloc[i]
    close, opn = float(r['close']), float(r['open'])
    out['yang'] = close > opn
    if mode == 'huigui':
        if i >= 5:
            hi5 = max(float(x) for x in daily_df['close'].iloc[i - 5:i])
            out['brk'] = close >= hi5
        out['pass'] = out['yang'] and out['brk']
    elif mode == 'fanbao':
        if i >= 1:
            out['brk'] = close > float(daily_df['high'].iloc[i - 1])
        out['pass'] = out['yang'] and out['brk']
    elif mode == 'chaodie':
        out['pass'] = out['yang']
    else:
        out['pass'] = True
        out['note'] = f'未知模式 {mode} 不设 gate'
    return out


# ---------- 盘中卖出/做 T 信号（H7.3 基础） ----------


def open_weak(df: pd.DataFrame, weak_pct: float = -0.03) -> bool:
    """开盘走弱：开盘 30 分钟内跌破昨收 × (1+weak_pct) 或低开过多 → 当日放弃买入"""
    if df is None or df.empty:
        return True
    pc = float(df["close"].iloc[0])  # 首根近似昨收（调用方应传含前日的 df）
    first30 = df.head(30)
    return bool((first30["low"] < pc * (1 + weak_pct)).any())


def t_signals(df: pd.DataFrame, up_mult: float = 1.02, down_mult: float = 0.98) -> dict:
    """做 T 高低点信号（H7.3）：日内冲高/急跌相对均价线幅度"""
    if df is None or df.empty:
        return {"sell_t": [], "buy_t": []}
    vwap = vwap_series(df)
    sell_t = df[df["high"] >= vwap * up_mult]["ts"].tolist()
    buy_t = df[df["low"] <= vwap * down_mult]["ts"].tolist()
    return {"sell_t": sell_t, "buy_t": buy_t}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
    from data.store import Store

    store = Store()
    for sym, d in (("002606", "2026-05-12"), ("600584", "2026-08-21")):
        pts = day_b_points(store, sym, d)
        print(f"{sym} {d}: B点 {len(pts)} 个")
        if len(pts):
            print(pts.to_string(index=False))
    store.close()