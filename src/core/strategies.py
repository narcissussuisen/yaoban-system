"""五大战法策略化（信号检测器）

统一接口: detect(df: pd.DataFrame) -> pd.Series[bool]（df 需含 date/open/high/low/close/volume，
按日期升序；返回与 df 等长的布尔 Series，True=该日为信号日）。

战法（对应 v4.0 手册 §3）:
  huigui        上升回档（核心）
  zt_huicai     涨停回踩低吸（v07 五口诀 → 参数化 3 条件）
  qu_shi_fanbao 趋势反包
  xianren       仙人指路（公式 + 次日反包确认）
  daban         打板接力（预留：需涨停池/连板数据，暂未实现）

注意: 分时买点（v08/v12 的「放量突破均价线回踩站稳」）需要分钟线数据，
日线层无法检测，见 tests/cases.json 中 fen_shi 案例的说明。
"""
from __future__ import annotations

import pandas as pd

from config import strategy
from core import indicators as ind

CFG_HG = strategy("huigui")
CFG_ZT = strategy("zt_huicai")
CFG_FB = strategy("qu_shi_fanbao")
CFG_XR = strategy("xianren")

# 名称映射（便于报告输出）
STRATEGY_NAMES = {
    "huigui": "上升回档",
    "zt_huicai": "涨停回踩低吸",
    "qu_shi_fanbao": "趋势反包",
    "xianren": "仙人指路",
    "daban": "打板接力",
}


def _require(df: pd.DataFrame, n: int) -> bool:
    return len(df) > n + 5


def detect_huigui(df: pd.DataFrame, mode: str = "live",
                  overrides: dict | None = None) -> pd.Series:
    """上升回档（双口径，见 parameters.toml [strategy.huigui]）

    mode="live"   实战低吸口径（案例回归校准，v4.0.1）：
                  close>MA20 且 MA20 向上 + 近10日高点回调 1~7 天 + 幅度 1~15%
                  + 回调期日均量≤拉升峰值量90% + 前期有一波上涨(≥8%)
    mode="screen" 公式筛选口径（v05@00:28）：
                  全多头排列 + 回调 3~5 天 + 幅度 5~15% + 缩量 30%+（vr<0.7）
    overrides     live 模式参数覆盖（用于参数网格敏感性分析，H1）
    """
    out = pd.Series(False, index=df.index)
    if not _require(df, 70):
        return out
    c, hi, lo = df["close"], df["high"], df["low"]
    v = df["volume"]
    multi = ind.multi_head(df).to_numpy()
    vol_ratio = ind.vol_shrink_ratio(df).to_numpy()
    ma20 = ind.ma(c, 20).to_numpy()
    ma20_rising = ma20 > pd.Series(ma20).shift(5).to_numpy()
    n = len(df)
    if mode == "screen":
        d_min, d_max = int(CFG_HG.get("pullback_days_min", 3)), int(CFG_HG.get("pullback_days_max", 5))
        p_min, p_max = float(CFG_HG.get("pullback_pct_min", 5.0)), float(CFG_HG.get("pullback_pct_max", 15.0))
        shrink = float(CFG_HG.get("volume_shrink_ratio", 0.7))
        for i in range(70, n):
            if not multi[i]:
                continue
            window = hi.iloc[i - 20:i]
            hh = float(window.max())
            if hh <= 0 or c.iloc[i] / hh >= 0.95:
                continue
            back_days = i - (i - 20 + int(window.values.argmax()))
            pull = (hh - c.iloc[i]) / hh * 100
            if d_min <= back_days <= d_max and p_min <= pull <= p_max and vol_ratio[i] < shrink:
                out.iloc[i] = True
        return out
    # live 口径（v4.0.1 案例回归校准：回调幅度 1~15%、量能口径改为「回调期日均量 vs 拉升峰值量」）
    live = dict(CFG_HG.get("live", {}))
    if overrides:
        live.update(overrides)
    d_min = int(live.get("pullback_days_min", 1))
    d_max = int(live.get("pullback_days_max", 7))
    p_min = float(live.get("pullback_pct_min", 1.0))
    p_max = float(live.get("pullback_pct_max", 15.0))
    vol_ratio_max = float(live.get("volume_ratio_max", 0.9))
    prior_gain_min = float(live.get("prior_gain_min_pct", 8.0))
    for i in range(70, n):
        if c.iloc[i] <= ma20[i] or not bool(ma20_rising[i]):
            continue
        window = hi.iloc[i - 10:i]
        hh = float(window.max())
        if hh <= 0:
            continue
        peak_pos = int(window.values.argmax())  # 拉升峰值日（相对窗口起点）
        peak_idx = i - 10 + peak_pos
        back_days = i - peak_idx
        pull = (hh - c.iloc[i]) / hh * 100
        if not (d_min <= back_days <= d_max):
            continue
        if not (p_min <= pull <= p_max):
            continue
        # 量能：回调期日均量 vs 拉升峰值日±1天最大量（视频口径「相对拉升缩量」）
        if peak_idx < 1:
            continue
        peak_vol = float(v.iloc[max(0, peak_idx - 1):min(n, peak_idx + 2)].max())
        if peak_vol <= 0:
            continue
        pullback_vol = float(v.iloc[peak_idx + 1:i + 1].mean())
        if pullback_vol / peak_vol >= vol_ratio_max:
            continue
        # 前期一波上涨：近10日高点相对 20 日前收盘的涨幅
        if i < 20 or hh / c.iloc[i - 20] - 1 < prior_gain_min / 100.0:
            continue
        out.iloc[i] = True
    return out


def detect_zt_huicai(df: pd.DataFrame) -> pd.Series:
    """涨停回踩低吸（简化 3 条件）：
    近 2~7 天内出现过涨停，回踩不破涨停日最低价，价站 5 日线与 20 日线上方，量缩"""
    out = pd.Series(False, index=df.index)
    if not _require(df, 30):
        return out
    c, lo = df["close"], df["low"]
    v = df["volume"]
    zt = ind.limit_up_mask(df).to_numpy()
    vol_ratio = ind.vol_shrink_ratio(df).to_numpy()
    ma5 = ind.ma(c, 5).to_numpy()
    ma20 = ind.ma(c, 20).to_numpy()
    d_min = int(CFG_ZT.get("pullback_days_min", 2))
    d_max = int(CFG_ZT.get("pullback_days_max", 7))
    shrink = float(CFG_ZT.get("volume_shrink_ratio", 0.7))
    n = len(df)
    for i in range(25, n):
        for back in range(d_min, d_max + 1):
            j = i - back
            if j < 0 or not zt[j]:
                continue
            # 回踩期最低价不破涨停日最低价（不破涨停板支撑）
            if lo.iloc[j + 1:i + 1].min() < lo.iloc[j]:
                continue
            if c.iloc[i] >= ma5[i] and c.iloc[i] >= ma20[i] and vol_ratio[i] < shrink:
                out.iloc[i] = True
                break
    return out


def detect_fanbao(df: pd.DataFrame, overrides: dict | None = None) -> pd.Series:
    """趋势反包：昨日阴线，今日阳线收盘包住昨日最高，量能同比放大
    （v4.0.1 修正：支持高开反包，不再要求开盘低于昨收——中京电子/圣泉集团案例均为高开拉升反包）

    overrides:
      max_gain_pct: 反包日收盘涨幅上限（%）。None=不过滤。
        ⚠️ 2026-08-25 全池 5353 信号验证：反包日涨幅 6%+ 组后5日 +1.65%（胜率53%）
        > 0~2% 组 +0.35% —— 高涨幅组反而更好，勿加低涨幅过滤（12 笔回测样本的
        反直觉结论已被信号级大样本推翻，属过拟合）。
      above_ma: 要求收盘站上 N 日均线（趋势反包语境，0=不要求）
    """
    out = pd.Series(False, index=df.index)
    if not _require(df, 10):
        return out
    o = dict(overrides or {})
    max_gain = o.get("max_gain_pct")
    above_ma = int(o.get("above_ma", 0))
    bear = ind.is_bearish(df).to_numpy()
    bull = ind.is_bullish(df).to_numpy()
    c, hi = df["close"], df["high"]
    v = df["volume"]
    n = len(df)
    ma_n = ind.ma(c, above_ma).to_numpy() if above_ma > 0 else None
    for i in range(1, n):
        if bear[i - 1] and bull[i]:
            if c.iloc[i] > hi.iloc[i - 1]:
                if v.iloc[i] > v.iloc[i - 1]:  # 同比放量（v22 图上标注口径）
                    if max_gain is not None and c.iloc[i] / c.iloc[i - 1] - 1 > max_gain / 100.0:
                        continue
                    if ma_n is not None and c.iloc[i] < ma_n[i]:
                        continue
                    out.iloc[i] = True
    return out


def detect_xianren(df: pd.DataFrame, with_confirm: bool = True) -> pd.Series:
    """仙人指路：公式日（长上影+趋势+量能+位置）+ 可选次日/第三日反包确认
    with_confirm=True 时信号日 = 确认日（放量阳线吃掉上影高点）"""
    out = pd.Series(False, index=df.index)
    if not _require(df, 70):
        return out
    c, o, hi, lo = df["close"], df["open"], df["high"], df["low"]
    v = df["volume"]
    n = len(df)
    body = ind.body(df).to_numpy()
    upsh = ind.upper_shadow(df).to_numpy()
    ma60 = ind.ma(c, 60).to_numpy()
    v5 = v.rolling(5).mean().to_numpy()
    xr_min = float(CFG_XR.get("upper_shadow_min_pct", 2.8))
    xr_ratio = float(CFG_XR.get("upper_shadow_ratio", 2.0))
    pos_min = float(CFG_XR.get("position_min", 0.85))
    pos_max = float(CFG_XR.get("position_max", 1.35))
    formula_day = pd.Series(False, index=df.index)
    for i in range(65, n):
        if body[i] <= 0:
            continue
        if upsh[i] < xr_ratio * body[i]:
            continue
        if upsh[i] / c.iloc[i - 1] * 100 < xr_min:
            continue
        if c.iloc[i] <= ma60[i] or ma60[i] <= ma60[i - 5]:
            continue
        if v.iloc[i] <= v5[i]:
            continue
        ratio = c.iloc[i] / c.iloc[i - 60]
        if not (pos_min < ratio < pos_max):
            continue
        formula_day.iloc[i] = True
    if not with_confirm:
        return formula_day
    # 确认：公式日之后 1~2 天内，放量阳线收盘吃掉公式日最高价
    fdays = df.index[formula_day.to_numpy()]
    for i in fdays:
        for k in (1, 2):
            j = i + k
            if j >= n:
                break
            if (c.iloc[j] > hi.iloc[i] and v.iloc[j] > v.iloc[i]
                    and c.iloc[j] > o.iloc[j]):
                out.iloc[j] = True
                break
    return out





# ============ v5.0 R1' 选股层：上升回档选手 v24 通用版 ============

def _vol_ma(df: pd.DataFrame, n: int) -> pd.Series:
    return df["volume"].rolling(n).mean()


def detect_huigui_v5(df: pd.DataFrame) -> pd.Series:
    """上升回档 v5（选手实盘主口径 = v24 通用版五条，v24@02:28）

    条件1: 趋势多头——5/10/20/60 均线多头排列，股价重心上移
    条件2: 一波拉升+缩量回调——近 15 日有上涨波段（高点距低点 >=8%），
           回调不能放量（回调日均量 <= 拉升峰值量）
    条件3: 回踩关键支撑——当日低点触及 5/10/20 日线或前平台（±1.5% 内）
    条件4: 回调时间短——高点后 1~5 天（含当日）
    条件5: 资金不撤退——红肥绿瘦（回调期阳线均量 >= 阴线均量×0.9）
           或止跌日护盘（低开高走且量 >= 回调期最大量×0.9）

    输出: 止跌企稳日信号（True=当日回踩支撑企稳，买点=次日放量收阳突破小高点，
          由 R2' 分时确认层负责）
    """
    out = pd.Series(False, index=df.index)
    if not _require(df, 70):
        return out
    c, hi, lo, o = df["close"], df["high"], df["low"], df["open"]
    v = df["volume"]
    n = len(df)
    ma5 = ind.ma(c, 5).to_numpy()
    ma10 = ind.ma(c, 10).to_numpy()
    ma20 = ind.ma(c, 20).to_numpy()
    ma60 = ind.ma(c, 60).to_numpy()
    vma5 = _vol_ma(df, 5).to_numpy()
    vma20 = _vol_ma(df, 20).to_numpy()
    # 一字板近似（排除弱势用）
    yizi = ((hi.to_numpy() - lo.to_numpy()) <= 0.001) & ((c.to_numpy() / pd.Series(c).shift(1).to_numpy() - 1) > 0.09)

    for i in range(65, n):
        # 条件1: 趋势多头（回档止跌日口径——评审 R1.3 放宽：
        #   价>MA20 且 MA20>MA60 且 MA20 上翘；回档期 MA5/MA10 自然纠缠，不要求严格 5>10>20）
        if not (c.iloc[i] > ma20[i] and ma20[i] > ma60[i]):
            continue
        if not (ma20[i] > ma20[i - 5]):
            continue
        # 条件2: 近15日上涨波段（低点→高点 >=8%）+ 回调不破拉升结构
        win15 = lo.iloc[max(0, i - 15):i]
        low_pos = i - 15 + int(win15.values.argmin())
        rise_lo = float(win15.min())
        win_hi = hi.iloc[max(0, i - 15):i]
        peak_pos = i - 15 + int(win_hi.values.argmax())
        if peak_pos <= low_pos:
            continue  # 低点必须先于高点（波形方向约束，评审 R1.5）
        rise_hi = float(win_hi.max())
        if rise_lo <= 0 or (rise_hi - rise_lo) / rise_lo < 0.08:
            continue
        # 高点位置（回调起点）已在上方定义
        back_days = i - 1 - peak_pos  # 回调日数（不含当日）
        if not (0 <= back_days <= 5):
            continue  # v24「回调1-3天最多5天」；0=当日信号（选手当日买入当日信号合法，圣阳4/7）
        # 缩量回调：回调日（不含当日）均量 <= 拉升峰值量（v24"回调不能放量"）
        rise_seg = v.iloc[max(0, peak_pos - 4):peak_pos + 1]
        pull_seg = v.iloc[peak_pos + 1:i]
        if len(rise_seg) == 0:
            continue
        if len(pull_seg) > 0 and float(pull_seg.max()) > float(rise_seg.max()):
            continue
        # 条件3: 回踩关键支撑——当日低点触及 5/10/20 日线（±1.5%）
        touch = (abs(lo.iloc[i] - ma5[i]) / ma5[i] <= 0.015
                 or abs(lo.iloc[i] - ma10[i]) / ma10[i] <= 0.015
                 or abs(lo.iloc[i] - ma20[i]) / ma20[i] <= 0.015)
        if not touch:
            continue
        # 止跌形态：收盘在支撑上方（收回）且非大阴线
        if c.iloc[i] <= o.iloc[i] and (o.iloc[i] - c.iloc[i]) / o.iloc[i] > 0.04:
            continue
        # 排除弱势：近10日一字板 >=2
        if int(yizi[i - 10:i].sum()) >= 2:
            continue
        # 条件5: 资金不撤退（双口径）
        pull_df = df.iloc[peak_pos + 1:i + 1]
        yang_v = float(pull_df[pull_df["close"] >= pull_df["open"]]["volume"].mean()) if (pull_df["close"] >= pull_df["open"]).any() else 0.0
        yin_v = float(pull_df[pull_df["close"] < pull_df["open"]]["volume"].mean()) if (pull_df["close"] < pull_df["open"]).any() else 0.0
        hongfei = yang_v >= yin_v * 0.9
        prev_pull_max = float(v.iloc[peak_pos + 1:i].max()) if i > peak_pos + 1 else 0.0
        hudipan = (o.iloc[i] < c.iloc[i - 1]) and (c.iloc[i] > o.iloc[i]) and (float(v.iloc[i]) >= prev_pull_max * 0.9)
        if not (hongfei or hudipan):
            continue
        out.iloc[i] = True
    return out


def detect(df: pd.DataFrame, name: str) -> pd.Series:
    """统一入口"""
    fn = {
        "huigui": detect_huigui,
        "zt_huicai": detect_zt_huicai,
        "qu_shi_fanbao": detect_fanbao,
        "xianren": detect_xianren,
    }.get(name)
    if fn is None:
        raise ValueError(f"未知战法: {name}")
    return fn(df)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    print("策略模块加载 OK；已注册:", list(STRATEGY_NAMES))