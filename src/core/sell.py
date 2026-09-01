"""H7.3 盘中卖出管理引擎（做T / 破均价线减半 / 10点纪律 / 止损 / 冲高止盈）

数据: minute_kline 表（freq='1m'/'5m'，volume 单位=股）+ stock_daily（前复权日线，MA 兜底）

规则来源（手册附录 B + E_出场持有做T洗盘 精读）:
  - 破均价线减半: v46@00:52「分时跌破均价线就止盈落袋」/ GAP 文档「盘中破均价线→减半（做T高抛点）」；
    减半后再破 → 清仓（v46/v57「分时跌破均价线短线不拉板则落袋」）
  - 破分时前低 → 清: GAP 文档「破分时前低→清」（买入以来最低价作为移动止损）
  - 做T: v14（正T/反T、1/3 仓、差价 1~3 个点、无波动不做T）、v49 通富微电（T进T出数量一致、
    底仓不变）、v48@01:25「有差价 1~3 个点就要 T 出」
  - 10 点纪律: rule 18（v03/v22/v46/v53「10 点前不板则走」）——10:00 未涨停且未走强 → 出
  - 止损: v20@05:31「亏 -5% 无条件走」/ v22@02:01（低开 -5% 触发）
  - 冲高止盈: v20@06:04「赚 10~20% 先走 1/3~1/2」——冲高（相对昨收≥X%）回落破均价线 → 减 1/3
  - 日线兜底: rule 16（破5日线减半→破10日线清）/ rule 37（持股周期 3 天左右）

T+1 约束: 当日卖出量 ≤ 当日开盘前底仓（正T 的 T出 卖的是底仓，T进 的份额次日才能卖；
反T 先卖底仓后买回）。反T 未买回 → 收盘强制买回（保持「底仓数量不变」纪律）。
"""
from __future__ import annotations

import pandas as pd

from core.intraday import vwap_series, prev_close_of
from config import section

# ---------- 参数 ----------

_BASE_PARAMS = {
    # 破均价线（利润兑现语境，v46/v57/v65：拉升有利后破均价线→落袋；早盘洗盘破线不算）
    "vwap_halve": True,          # 收盘破均价线(容差内连续确认) → 减半
    "vwap_tol": 0.003,           # 破线容差
    "vwap_confirm": 2,           # 连续 N 根确认
    "vwap_break_all": True,      # 减半后再次破均价线 → 清仓
    "vwap_halve_min_gain": 7.0,  # 当日高点已相对昨收 ≥ 该值（%）才允许破线减仓（利润兑现语境 v20，避免洗盘震飞）
    "break_low_clear": True,     # 冲高回落破位止损：先冲高(≥买入价×arm)后，回落到峰值×（1-trail）→ 清
    "break_low_arm": 2.0,        # 移动止损布防门槛：日高 ≥ 买入价 × (1+arm%)
    "break_low_trail": 4.0,      # 从日内峰值回撤 ≥ 该值（%）→ 清仓
    "t_reverse_only": False,     # 当日仅允许反T（末日 T+1 约束：不新开 T 进份额）
    "zhaban_sell": True,         # 炸板卖出（v50/v55「封板不坚决=落袋」）：触及涨停后回落>阈值 → 清
    "zhaban_fall_pct": 0.5,      # 回落阈值（相对涨停价）
    "second_high_sell": True,    # 次高点卖出（v54/v58/v62「冲高无力突破前高就走」）
    "sh_pull_pct": 1.0,          # 峰值回落 ≥ 该值（%）且 confirm 根内未再创新高 → 卖
    "sh_confirm_n": 60,          # 确认窗口（1m 根数≈分钟）
    "sh_min_surge": 5.0,         # 布防门槛：日内高点需已相对昨收 ≥ 该值（%）
    # 做T（v14/v48/v49：围绕均价线做，差价 1~3 个点）
    "t_enabled": True,
    "t_frac": 1.0 / 3.0,         # 做T仓位 ≤1/3 底仓
    "t_diff_pct": 1.5,           # 差价 1~3 个点（取 1.5）
    "t_buy_dev": 1.5,            # 正T 触发：急跌至均价线下方 ≥ 该值（%）（v49「均价线下方补仓」）
    "t_sell_dev": 3.0,           # 反T 触发：急拉至均价线上方 ≥ 该值（%）（v14「大幅高开/急速上冲」，防强势日卖飞）
    "t_sell_dev_strong": None,  # 强势日(当日涨幅≥t_strong_pct)正T卖出抬高: 至少VWAP上方该值(7/9强势日卖太早校准)
    "t_strong_pct": 5.0,        # 强势日判定: 当日高点相对昨收涨幅 ≥ 该值(%)
    "t_min_range": 2.0,          # 日内振幅 < 该值（%）不做T（v14「分时没波动的漂不适合做T」）
    # 10 点纪律（rule 18；默认关闭——低吸/做T 持仓不适用，仅打板/连板口径开启，H6 单独验证）
    "ten_oclock": False,
    "ten_time": "10:00",
    "ten_strong_pct": 5.0,       # 10 点前相对昨收涨幅 ≥ 该值视为走强（可留）
    "ten_require_limit": False,  # True=10 点前必须涨停才留（打板/连板口径）
    "ten_action": "all",         # "all"=全出 | "half"=减半
    # 止损 / 止盈
    "stop_loss_pct": 5.0,        # 相对买入价 -5% → 清仓
    "profit_take_pct": 10.0,     # 冲高（相对昨收）≥ 该值后回落破均价线 → 减仓
    "profit_take_frac": 1.0 / 3.0,
    # 日线兜底
    "daily_ma5_halve": True,
    "daily_ma10_clear": True,
    "time_stop_days": 5,         # 持仓超 N 交易日 → 收盘清
    # 组合级联动（需 combo 上下文 {"dragon_time": "HH:MM"} / {"sector_retreat_time": "HH:MM"}）
    "dragon_link_sell": False,   # 龙头联动（v16#8 龙头跳水→跟风补跌 / v25 豫能冲高被砸→辽宁能源先出）
    "dragon_link_action": "clear",  # "clear"=全出（v25「先出来」）| "clear_if_profit"=有赚全出/亏损减半
    "sector_retreat_sell": False,   # 板块退潮先行（板块指数破均价线+较昨收跌幅超阈值 → 先减半）
}

# 配置优先（parameters.toml [sell.intraday]），缺省用代码内基线
DEFAULT_PARAMS = {**_BASE_PARAMS,
                  **{k: v for k, v in section("sell").get("intraday", {}).items()}}

# 涨停幅度（按板块）: 30/301/688 → 20%，4xx/8xx（北交所）→ 30%，其余 10%
def limit_pct_of(symbol: str) -> float:
    if symbol.startswith(("300", "301", "688", "689")):
        return 0.20
    if symbol.startswith(("4", "8", "92")):  # 北交所含 920 新代码段（R4R5 评审整改）
        return 0.30
    return 0.10


def limit_price(prev_close: float, symbol: str) -> float:
    return round(prev_close * (1 + limit_pct_of(symbol)), 2)


# ---------- 单日管理 ----------


def manage_day(day_df: pd.DataFrame, prev_close: float, base0: int, stop_px: float,
               low_track: float, params: dict | None = None,
               limit_px: float | None = None, combo: dict | None = None) -> dict:
    """对持仓股单日执行盘中卖出/做T 管理。

    day_df: 单日分钟数据 [ts, open, high, low, close, volume, amount]
    prev_close: 昨收（涨幅基准）
    base0: 当日开盘前底仓（股）
    stop_px: 止损价（相对买入价）；None=不设
    low_track: 买入以来最低价（分时前低移动止损基准）
    limit_px: 涨停价（10 点纪律判定）；None=按 prev_close 计算

    返回: {fills: [{ts, side, qty, px, reason}], qty: 日末持仓, low_track: 更新后前低,
          t_round: 做T成功次数}
    """
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    if day_df is None or day_df.empty:
        return {"fills": [], "qty": base0, "low_track": low_track, "t_round": 0}
    entry_px = low_track if low_track > 0 else base0 * 0.0 or 1.0  # 成本基准（= 传入的 low_track 初值）
    fills: list = []
    qty = base0
    sellable = base0                      # T+1：当日可卖上限（不含当日买入）
    t_state = None                        # None | {"dir": "buy_first"|"sell_first", "px": fill, "qty": n}
    t_round = 0
    halved = False
    ten_done = False
    sector_done = False
    vwap = vwap_series(day_df)
    if limit_px is None:
        limit_px = limit_price(prev_close, "") if prev_close > 0 else None
    hm = day_df["ts"].str[11:16]
    n = len(day_df)
    vwap_arr = vwap.to_numpy()
    high = day_df["high"].to_numpy()
    low = day_df["low"].to_numpy()
    close = day_df["close"].to_numpy()
    day_high_max = 0.0  # 当日高点（相对昨收），用于破线减仓的利润门槛
    peak_high = entry_px  # 日内峰值（相对买入价），用于冲高回落移动止损
    touched_limit = False  # 当日曾触及涨停（炸板判定）
    strong_day = bool(p.get('t_strong_pct') and prev_close > 0)
    strong_dev = float(p['t_sell_dev_strong']) if p.get('t_sell_dev_strong') else 0.0
    sh_peak = 0.0            # 次高点规则：日内峰值跟踪
    sh_age = 10 ** 9         # 距上次创新高的根数

    def sell(n_qty: int, px: float, reason: str, ts: str):
        nonlocal qty, sellable
        n_qty = max(0, min(int(n_qty), qty, sellable))
        if n_qty <= 0:
            return
        qty -= n_qty
        sellable -= n_qty
        fills.append({"ts": ts, "side": "sell", "qty": n_qty, "px": round(float(px), 3), "reason": reason})

    def buy(n_qty: int, px: float, reason: str, ts: str):
        nonlocal qty
        n_qty = max(0, int(n_qty))
        if n_qty <= 0:
            return
        qty += n_qty
        fills.append({"ts": ts, "side": "buy", "qty": n_qty, "px": round(float(px), 3), "reason": reason})

    # 日内振幅（做T 前提）
    if n > 0:
        day_range = float(high.max() / max(low.min(), 1e-9) - 1) * 100
    else:
        day_range = 0.0
    t_ok = p["t_enabled"] and day_range >= p["t_min_range"] and base0 > 0
    t_qty = int(max(100, base0 * p["t_frac"]) // 100 * 100)

    confirm = 0
    touched_stop = False
    for i in range(n):
        ts = str(day_df["ts"].iloc[i])
        hhmm = hm.iloc[i]
        vw = float(vwap_arr[i])
        day_high_max = max(day_high_max, float(high[i]))
        # 1) 止损（跳空低开立即执行；盘中触及仅标记，收盘双条件判定在日线兜底）
        if stop_px and low[i] <= stop_px and qty > 0:
            touched_stop = True
            if float(day_df["open"].iloc[0]) <= stop_px:
                sell(qty, stop_px, "stop_loss", ts)
                break
        # 2) 10 点纪律
        if p["ten_oclock"] and not ten_done and hhmm >= p["ten_time"] and qty > 0:
            ten_done = True
            pct = close[i] / prev_close - 1 if prev_close > 0 else 0.0
            at_limit = limit_px is not None and close[i] >= limit_px - 0.01
            strong = at_limit or (pct >= p["ten_strong_pct"] / 100.0)
            if p["ten_require_limit"]:
                strong = at_limit
            if not strong:
                n_out = qty if p["ten_action"] == "all" else qty // 2
                sell(n_out, close[i], "ten_oclock", ts)
        if qty <= 0:
            break
        # 2.5) 龙头联动（组合级）：龙头炸板/冲高被砸 → 跟风股出局（v16#8 / v25）
        # 接线期整改：成交价=信号下一根 close（同分钟收盘略乐观）
        if (combo and p["dragon_link_sell"] and qty > 0
                and combo.get("dragon_time") and hhmm >= combo["dragon_time"]):
            j = min(i + 1, n - 1)
            px_exec = close[j] if j > i else close[i]
            ts_exec = str(day_df["ts"].iloc[j]) if j > i else ts
            if p["dragon_link_action"] == "clear" or close[j] > entry_px:
                sell(qty, px_exec, "dragon_link", ts_exec)
                break
            else:
                sell(qty // 2, px_exec, "dragon_link", ts_exec)
                combo["dragon_time"] = None   # 单日只触发一次
        if qty <= 0:
            break
        # 2.6) 板块退潮先行（组合级）：板块指数破均价线且较昨收跌幅超阈值 → 持仓先减仓
        if (combo and p["sector_retreat_sell"] and qty > 0 and not sector_done
                and combo.get("sector_retreat_time") and hhmm >= combo["sector_retreat_time"]):
            sell(qty // 2, close[i], "sector_retreat", ts)
            sector_done = True
        if qty <= 0:
            break
        # 3) 破均价线减半 / 再破清仓（需当日已有利——拉升后回落才落袋，早盘洗盘破线不触发）
        gain_ok = prev_close > 0 and day_high_max >= prev_close * (1 + p["vwap_halve_min_gain"] / 100.0)
        if p["vwap_halve"] and gain_ok:
            if close[i] < vw * (1 - p["vwap_tol"]):
                confirm += 1
            else:
                confirm = 0
            if not halved and confirm >= p["vwap_confirm"] and qty > 0:
                sell(qty // 2, close[i], "vwap_halve", ts)
                halved = True
                confirm = 0
            elif halved and p["vwap_break_all"] and confirm >= p["vwap_confirm"] and qty > 0:
                sell(qty, close[i], "vwap_break_all", ts)
                break
        if qty <= 0:
            break
        # 4) 冲高回落破位止损：先冲高（≥买入价×arm）布防，再从日内峰值回撤 trail% → 清
        peak_high = max(peak_high, float(high[i]))
        if (p["break_low_clear"] and qty > 0
                and peak_high >= entry_px * (1 + p["break_low_arm"] / 100.0)
                and low[i] < peak_high * (1 - p["break_low_trail"] / 100.0)):
            sell(qty, low[i], "break_low", ts)
            break
        # 5) 做T（每日至多一次往返——GAP 文档「日内高抛低吸一次」，避免卖飞/追高）
        if t_ok and qty > 0 and t_round == 0:
            if p["t_reverse_only"]:
                # 末日仅反T：先卖后买回（T+1 合规，不新增不可卖份额）
                if t_state is None:
                    if high[i] >= vw * (1 + p["t_sell_dev"] / 100.0) and sellable >= t_qty:
                        sell(t_qty, high[i], "t_sell", ts)
                        t_state = {"dir": "sell_first", "px": high[i], "qty": t_qty}
                elif t_state["dir"] == "sell_first":
                    target = t_state["px"] * (1 - p["t_diff_pct"] / 100.0)
                    if low[i] <= target:
                        buy(t_state["qty"], min(low[i], target), "t_buy_back", ts)
                        t_state = None
                        t_round += 1
                continue
            if t_state is None:
                # 反T：急拉远离均价线 → 先卖（v14「急拉不追」）
                if high[i] >= vw * (1 + p["t_sell_dev"] / 100.0) and sellable >= t_qty:
                    sell(t_qty, high[i], "t_sell", ts)
                    t_state = {"dir": "sell_first", "px": high[i], "qty": t_qty}
                # 正T：急跌至均价线下方 → 先买（v49「均价线下方补仓」）
                elif low[i] <= vw * (1 - p["t_buy_dev"] / 100.0):
                    buy(t_qty, low[i], "t_buy", ts)
                    t_state = {"dir": "buy_first", "px": low[i], "qty": t_qty}
            elif t_state["dir"] == "sell_first":
                target = t_state["px"] * (1 - p["t_diff_pct"] / 100.0)
                if low[i] <= target:
                    buy(t_state["qty"], min(low[i], target), "t_buy_back", ts)
                    t_state = None
                    t_round += 1
            elif t_state["dir"] == "buy_first":
                target = t_state["px"] * (1 + p["t_diff_pct"] / 100.0)
                if strong_dev > 0:  # VWAP保底门槛(参数启用即生效, 不依赖盘中涨幅判定——10:00无法预知全天强势)
                    target = max(target, vw * (1 + strong_dev / 100.0))  # 强势日正T卖出抬高(7/9校准)
                if high[i] >= target and sellable >= t_state["qty"]:
                    sell(t_state["qty"], target, "t_sell_out", ts)
                    t_state = None
                    t_round += 1
        if qty <= 0:
            break
        # 6) 冲高止盈：冲高（相对昨收 ≥ X%）回落破均价线 → 减 1/3
        if p["profit_take_pct"] and prev_close > 0 and qty > 0:
            if high[i] >= prev_close * (1 + p["profit_take_pct"] / 100.0) and close[i] < vw:
                n_out = int(qty * p["profit_take_frac"] // 100 * 100)
                if n_out > 0 and sellable >= n_out:
                    sell(n_out, close[i], "profit_take", ts)
        # 7) 炸板卖出（v50/v55）：触及涨停后回落超阈值 → 清仓（n=16 验证 100% 优于持有到收盘）
        if p["zhaban_sell"] and limit_px is not None and qty > 0:
            if high[i] >= limit_px - 0.01:
                touched_limit = True
            if touched_limit and close[i] < limit_px * (1 - p["zhaban_fall_pct"] / 100.0):
                sell(qty, close[i], "zhaban_sell", ts)
                break
        # 8) 次高点卖出（v54/v58/v62）：创出新高后回落且确认期内未再创新高 → 清
        if p["second_high_sell"] and qty > 0:
            if high[i] > sh_peak:
                sh_peak = high[i]
                sh_age = 0
            else:
                sh_age += 1
            if (sh_age >= p["sh_confirm_n"]
                    and prev_close > 0 and sh_peak >= prev_close * (1 + p["sh_min_surge"] / 100.0)
                    and close[i] < sh_peak * (1 - p["sh_pull_pct"] / 100.0)):
                sell(qty, close[i], "second_high", ts)
                break
    # 日末 T 收尾：
    #  - 反T 未买回：收盘价仍低于卖出价 → 买回（保持底仓）；否则不回补（高抛落袋，卖飞不追）
    #    （v14「待股价结束快速上涨并出现回落后买进」——不回落就不接回，避免把盈利做成亏损）
    #  - 正T 未 T出：T进份额转底仓（次日可卖，T+1 合规）
    if t_state is not None:
        if t_state["dir"] == "sell_first":
            last_close = float(close[-1])
            if last_close < t_state["px"]:
                buy(t_state["qty"], last_close, "t_buy_back_eod", str(day_df["ts"].iloc[-1]))
                t_round += 1
            else:
                t_round += 1   # 高抛成功（未接回，利润已锁定）
        else:
            t_round += 1       # 正T 已买未卖：份额转底仓（T+1 约束下当日无法卖出）
    return {"fills": fills, "qty": qty, "low_track": low_track, "t_round": t_round}


# ---------- 多日持仓模拟 ----------


def simulate_hold(store, symbol: str, entry_ts: str, entry_px: float, entry_qty: int,
                  params: dict | None = None, horizon_days: int = 5,
                  start_mgmt_day: int = 1, freq: str = "1m",
                  daily_df: pd.DataFrame | None = None,
                  combo: dict | None = None) -> dict:
    """B 点买入后多日持仓模拟（卖出管理引擎 + 日线兜底）。

    store: Store；symbol: 代码；entry_ts: 买入分钟（'YYYY-MM-DD HH:MM'）
    entry_px: 买入价（已含成本则 caller 负责净值口径，此处为原始成交价）
    entry_qty: 买入股数
    params: 卖出引擎参数覆盖
    horizon_days: 最长持有交易日
    start_mgmt_day: 买入当日是否管理（0=当日即管，1=次日开始——选手模式：买入日只负责买点）
    daily_df: 该股日线 DataFrame（date/open/high/low/close/volume，升序）；缺省从 store 读
    freq: 分钟频率

    返回: {fills, exit_ts, exit_px, exit_qty, exit_reason, realized_pnl_pct,
          t_rounds, days_held, day_log: [{date, close, qty_end, t_round}]}
    """
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    def _empty(reason: str) -> dict:
        return {"fills": [], "exit_ts": entry_ts, "exit_px": entry_px, "exit_qty": entry_qty,
                "exit_reason": reason, "realized_pnl_pct": 0.0, "t_rounds": 0,
                "days_held": 0, "day_log": [], "entry_px": entry_px, "entry_qty": entry_qty}

    if daily_df is None or daily_df.empty:
        rows = store.get_stock(symbol)
        if not rows:
            return _empty("no_data")
        daily_df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                               "close", "volume", "amount"])
    mrows = store.get_minute(symbol, freq)
    if not mrows:
        return _empty("no_minute")
    mdf = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high", "low",
                                       "close", "volume", "amount"])

    entry_date = entry_ts[:10]
    all_dates = sorted(daily_df["date"].astype(str).unique().tolist())
    if entry_date not in all_dates:
        return _empty("date_not_found")
    i0 = all_dates.index(entry_date)
    hold_dates = all_dates[i0:i0 + horizon_days + 1]

    qty = entry_qty
    stop_px = entry_px * (1 - p["stop_loss_pct"] / 100.0)
    low_track = entry_px
    fills: list = []
    day_log = []
    exit_reason = ""
    exit_px = entry_px
    exit_ts = entry_ts
    t_rounds = 0
    realized = 0.0  # 卖出份额的 (卖出价-买入价)*数量 累计（不含 T 进成本，T 进份额计入持仓）

    # 买入 fill 记录
    fills.append({"ts": entry_ts, "side": "buy", "qty": entry_qty, "px": entry_px, "reason": "entry"})

    for k, d in enumerate(hold_dates):
        day = mdf[mdf["ts"].str[:10] == d]
        if day.empty:
            continue
        prev_close = prev_close_of(mdf, d)
        if prev_close is None or prev_close <= 0:
            prev_close = float(day["close"].iloc[0])
        day_no = k
        if day_no < start_mgmt_day or qty <= 0:
            # 不管理日：仅记录估值（low_track=成本基准保持不变）
            day_log.append({"date": d, "close": float(day["close"].iloc[-1]),
                            "qty_end": qty, "t_round": 0})
            continue
        lp = limit_price(prev_close, symbol)
        if k == horizon_days:
            p["t_reverse_only"] = True   # 末日：仅反T（T+1 合规）
        # 组合级上下文按日期索引: combo = {"YYYY-MM-DD": {"dragon_time": "HH:MM", ...}}
        day_combo = combo.get(d) if combo else None
        res = manage_day(day, prev_close, qty, stop_px, low_track, p, limit_px=lp,
                         combo=day_combo)
        for f in res["fills"]:
            f["date"] = d
            if f["side"] == "sell":
                realized += (f["px"] - entry_px) * f["qty"]
        fills.extend(res["fills"])
        qty = res["qty"]
        low_track = res["low_track"]
        t_rounds += res["t_round"]
        close_px = float(day["close"].iloc[-1])
        day_log.append({"date": d, "close": close_px, "qty_end": qty, "t_round": res["t_round"]})
        if qty <= 0:
            exit_reason = fills[-1]["reason"] if fills else "exit"
            exit_ts = fills[-1]["ts"]
            exit_px = fills[-1]["px"]
            break
        # 日线兜底：破 MA5 减半 / 破 MA10 清 / 时间止损
        di = all_dates.index(d)
        if p["daily_ma5_halve"] or p["daily_ma10_clear"]:
            c = daily_df["close"]
            ma5 = float(c.rolling(5).mean().iloc[di]) if di >= 4 else None
            ma10 = float(c.rolling(10).mean().iloc[di]) if di >= 9 else None
            if p["daily_ma10_clear"] and ma10 and close_px < ma10 and k >= 3:
                # 买入后连续3日收盘<MA10 才清（v05「跌破10日线3天不收回必须止损」；
                # 回踩20日线买点在MA10下方属正常——圣阳4/7买在MA10下方，买入前日期不参与判定）
                ma10_arr = c.rolling(10).mean().to_numpy()
                c_arr = c.to_numpy()
                if (float(c_arr[di - 1]) < float(ma10_arr[di - 1])
                        and float(c_arr[di - 2]) < float(ma10_arr[di - 2])):
                    qty = 0
                    exit_reason, exit_px, exit_ts = "ma10_clear", close_px, f"{d} 15:00"
                    fills.append({"ts": f"{d} 15:00", "date": d, "side": "sell",
                                  "qty": int(day_log[-1]["qty_end"]), "px": close_px,
                                  "reason": "ma10_clear"})
                    realized += (close_px - entry_px) * int(day_log[-1]["qty_end"])
                    day_log[-1]["qty_end"] = 0
                    break
            if (p["daily_ma5_halve"] and ma5 and close_px < ma5 and qty > 0
                    and k >= 2 and close_px > entry_px):
                # 破5日线减半仅适用于"主升浪持有"语境（持有≥2天且有浮盈——利润兑现；
                # 回档刚买入前几日不启用——圣阳 4/8 破5日线选手未减仓，持有至七连板）
                half = qty // 2
                if half > 0:
                    qty -= half
                    realized += (close_px - entry_px) * half
                    fills.append({"ts": f"{d} 15:00", "date": d, "side": "sell",
                                  "qty": half, "px": close_px, "reason": "ma5_halve"})
                    day_log[-1]["qty_end"] = qty
        if qty <= 0:
            break
        # 时间止损（含当日）超出 horizon → 收盘清
        if k >= horizon_days:
            qty = 0
            exit_reason, exit_px, exit_ts = "time_stop", close_px, f"{d} 15:00"
            fills.append({"ts": f"{d} 15:00", "date": d, "side": "sell",
                          "qty": int(day_log[-1]["qty_end"]), "px": close_px, "reason": "time_stop"})
            realized += (close_px - entry_px) * int(day_log[-1]["qty_end"])
            day_log[-1]["qty_end"] = 0
            break

    if qty > 0:
        # 窗口结束仍未清：按最后交易日收盘估值（未实现）
        last_day = hold_dates[-1]
        ld = mdf[mdf["ts"].str[:10] == last_day]
        if not ld.empty:
            exit_px = float(ld["close"].iloc[-1])
        exit_ts = f"{last_day} 15:00"
        exit_reason = "window_end"
        fills.append({"ts": exit_ts, "date": last_day, "side": "sell",
                      "qty": qty, "px": exit_px, "reason": "window_end"})
        realized += (exit_px - entry_px) * qty
        if day_log:
            day_log[-1]["qty_end"] = 0
        else:
            day_log.append({"date": last_day, "close": exit_px, "qty_end": 0, "t_round": 0})

    realized_pnl_pct = realized / (entry_px * entry_qty) * 100.0
    return {"fills": fills, "exit_ts": exit_ts, "exit_px": exit_px, "exit_qty": 0,
            "exit_reason": exit_reason, "realized_pnl_pct": round(realized_pnl_pct, 3),
            "t_rounds": t_rounds, "days_held": len(day_log), "day_log": day_log,
            "entry_px": entry_px, "entry_qty": entry_qty}


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
    from data.store import Store

    st = Store()
    r = simulate_hold(st, "002606", "2026-05-12 09:33", 21.0, 1000, horizon_days=3)
    print("t_rounds:", r["t_rounds"], "pnl:", r["realized_pnl_pct"], "exit:", r["exit_reason"])
    for f in r["fills"]:
        print(" ", f)
    st.close()