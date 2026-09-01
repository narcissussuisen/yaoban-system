"""P4 每日盘后信号管线

流程: 数据更新(--update) → 环境打分(简版) → 全池五大战法扫描(当日信号) → 信号报告(md+html)

用法:
    python scripts/daily_pipeline.py                  # 用 DB 最新交易日
    python scripts/daily_pipeline.py --date 2026-08-21
    python scripts/daily_pipeline.py --update         # 先增量更新数据再扫描
输出: outputs/signals/YYYY-MM-DD_signal_report.md / .html
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from data.store import Store  # noqa: E402
from core import strategies as S  # noqa: E402
from core import indicators as ind  # noqa: E402
from core.env_score import env_score, env_score_full, regime_from_simple  # noqa: E402
from core.names import stock_names  # noqa: E402
from report.md2html import md_file2html  # noqa: E402

SIGNAL_DIR = ROOT / "outputs" / "signals"


def rows_to_df(rows) -> pd.DataFrame:
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.DataFrame(rows, columns=cols[:len(rows[0])])
    for c in ("open", "high", "low", "close", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = df["date"].astype(str)
    return df.reset_index(drop=True)


DETECTORS = {
    "上升回档": lambda df: S.detect_huigui(df, mode="live"),
    "涨停回踩": lambda df: S.detect_zt_huicai(df),
    "趋势反包": lambda df: S.detect_fanbao(df),
    "仙人指路": lambda df: S.detect_xianren(df, with_confirm=True),
}


def latest_trading_day(store: Store) -> str:
    r = store.conn.execute("SELECT MAX(date) FROM index_daily").fetchone()
    return r[0] if r and r[0] else dt.date.today().isoformat()


def update_data(store: Store):
    """增量更新：指数 + 全股票池（东财限流时自动回退新浪/腾讯）"""
    from data import fetchers as F

    print("== 增量更新 ==")
    for sym in F.index_symbols():
        try:
            df = F.fetch_index_daily(sym)
            if not df.empty:
                store.upsert_index_daily(df, sym)
        except Exception as e:  # noqa: BLE001
            print(f"  index {sym}: FAIL {type(e).__name__} {str(e)[:80]}")
    rows = store.conn.execute("SELECT DISTINCT symbol FROM stock_daily").fetchall()
    symbols = [r[0] for r in rows]
    last = dict(store.conn.execute(
        "SELECT symbol, MAX(date) FROM stock_daily GROUP BY symbol").fetchall())
    for sym in symbols:
        start = last.get(sym, "2022-01-01")
        try:
            df = F.fetch_stock_daily(sym, start, "20261231")
            if not df.empty:
                df = df[df["date"] >= start]
                store.upsert_stock_daily(df, sym)
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: FAIL {type(e).__name__} {str(e)[:80]}")


def main():
    ap = argparse.ArgumentParser(description="每日盘后信号管线")
    ap.add_argument("--date", default="", help="目标日期（默认 DB 最新交易日）")
    ap.add_argument("--update", action="store_true", help="先增量更新数据")
    ap.add_argument("--top", type=int, default=0, help="仅输出前 N 条信号（0=全部）")
    ap.add_argument("--log", default="", help="追加运行日志到文件（供计划任务记录）")
    args = ap.parse_args()

    store = Store()
    if args.update:
        update_data(store)
    target = args.date or latest_trading_day(store)
    names = stock_names()

    # ---------- 环境打分 ----------
    idx_rows = store.get_index("sh000001", end=target)
    env = None
    if idx_rows:
        idx_df = pd.DataFrame(idx_rows, columns=["symbol", "date", "open", "high", "low", "close", "volume"])
        sent = store.sentiment_as_of(target)
        if sent:
            # 完整五维（a-stock-data 情绪数据就绪）
            env = env_score_full(idx_df, sent)
            env["note"] = f"完整五维（情绪@{sent['date']}）"
        else:
            env = env_score(idx_df)
            # 仅简版维度可用时，用简版专属映射（避免 0-4 制套 0-10 制阈值导致永远弱势）
            if set(env["available_dims"]) <= {"trend", "volume"}:
                simple = env["dims"]["trend"] + env["dims"]["volume"]
                regime, cap = regime_from_simple(simple)
                env["regime"], env["position_cap"] = regime, cap
                env["note"] = "简版映射（情绪数据未就绪）"
    regime_cn = {"strong": "强势（牛）", "neutral": "中性（震荡）", "weak": "弱势（熊）"}

    # ---------- 全池扫描 ----------
    sig_rows = store.conn.execute(
        "SELECT DISTINCT symbol FROM stock_daily").fetchall()
    signals = []
    for (sym,) in sig_rows:
        db = store.get_stock(sym, end=target)
        if not db or len(db) < 80:
            continue
        df = rows_to_df(db)
        if df["date"].iloc[-1] != target:  # 目标日无数据（停牌等）
            continue
        close = float(df["close"].iloc[-1])
        open_ = float(df["open"].iloc[-1])
        vol = float(df["volume"].iloc[-1])
        vol_prev = float(df["volume"].iloc[-2])
        c = df["close"]
        ma5 = float(ind.ma(c, 5).iloc[-1])
        ma10 = float(ind.ma(c, 10).iloc[-1])
        ma20 = float(ind.ma(c, 20).iloc[-1])
        # 买点确认（v4.0 §4.2）：放量阳线 = 当日收阳且量能较前日放大
        bullish = close > open_
        vol_up = vol > vol_prev * 1.0
        for name, det in DETECTORS.items():
            sig = det(df)
            if bool(sig.iloc[-1]):
                signals.append({
                    "symbol": sym, "name": names.get(sym, sym),
                    "pattern": name, "close": close, "date": target,
                    "bullish": bullish, "vol_up": vol_up,
                    "confirmed": (bullish and vol_up) if name == "上升回档" else True,
                    "ma5": ma5, "ma10": ma10, "ma20": ma20,
                })
    if args.top > 0:
        signals = signals[: args.top]

    # 同一股票多战法命中：按战法优先级去重（上升回档=核心）
    PATTERN_PRIORITY = {"上升回档": 0, "涨停回踩": 1, "趋势反包": 2, "仙人指路": 3}
    signals.sort(key=lambda s: PATTERN_PRIORITY.get(s["pattern"], 9))
    deduped: dict[str, dict] = {}
    for s in signals:
        if s["symbol"] not in deduped:
            deduped[s["symbol"]] = s
    signals = list(deduped.values())

    # 排序：已确认优先，其次按收盘距 MA5 乖离（越小越接近支撑）排序
    signals.sort(key=lambda s: (not s["confirmed"],
                                abs(s["close"] / s["ma5"] - 1) if s["ma5"] else 9.9))
    confirmed = [s for s in signals if s["confirmed"]]
    watch = [s for s in signals if not s["confirmed"]]

    # ---------- 报告 ----------
    L: list[str] = []
    L.append(f"# 妖板系统 每日信号报告 — {target}")
    L.append("")
    L.append("> 研究用途，不构成投资建议。信号需人工复核（板块位置/基本面/分时确认）后按手册纪律执行。")
    L.append("")
    L.append("## 市场环境")
    L.append("")
    if env:
        note = env.get("note", "")
        if "完整五维" in note:
            L.append(f"- 环境分（完整五维，0~10）：**{env['score']}** {note}")
        else:
            L.append(f"- 环境分（简版 trend+volume，0~4）：**{env['score']}**（{note}）")
        L.append(f"- 市场状态：**{regime_cn.get(env['regime'], env['regime'])}** → 总仓位上限 **{env['position_cap']:.0f}%**")
        L.append(f"- 维度明细：{env['dims']}")
    else:
        L.append("- 指数数据缺失，无法打分")
    L.append("")
    L.append("## 当日信号（确认 {cn} / 观察 {wn}）".format(cn=len(confirmed), wn=len(watch)))
    L.append("")
    L.append("### 已确认买点（放量阳线，可进入人工复核）")
    L.append("")
    if not confirmed:
        L.append("- 无已确认买点。")
    else:
        L.append("| 代码 | 名称 | 战法 | 收盘 | MA5 | MA10 | MA20 | 复核要点 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for s in confirmed:
            L.append(f"| {s['symbol']} | {s['name']} | {s['pattern']} | {s['close']:.2f} "
                     f"| {s['ma5']:.2f} | {s['ma10']:.2f} | {s['ma20']:.2f} "
                     f"| 板块是否主线/分时放量回踩均价线/涨幅≤3% |")
    L.append("")
    L.append("### 观察池（形态成立但当日未放量收阳，等买点确认）")
    L.append("")
    if not watch:
        L.append("- 无观察标的。")
    else:
        L.append("| 代码 | 名称 | 战法 | 收盘 | 说明 |")
        L.append("|---|---|---|---|---|")
        for s in watch:
            why = "收阳但量未放大" if s["bullish"] else ("放量但收阴" if s["vol_up"] else "缩量回调中")
            L.append(f"| {s['symbol']} | {s['name']} | {s['pattern']} | {s['close']:.2f} | {why} |")
    L.append("")
    L.append("> 注：上升回档买点 = 回调后**放量阳线**（v4.0 §4.2，不提前抄底）；"
             "观察池标的需次日盯放量阳突破回档小高点。")
    L.append("## 执行纪律（下单前逐项打勾）")
    L.append("")
    L.append("- [ ] 环境分允许开仓；仓位 ≤ 总仓上限")
    L.append("- [ ] 板块是热点主线（三信号），龙头梯队完整")
    L.append("- [ ] 战法形态合格；不提前抄底（等放量阳突破回档小高点/反包确认）")
    L.append("- [ ] 分时确认：放量突破均价线后回踩站稳；涨幅 ≤3%")
    L.append("- [ ] 买入前写好止损位（破10日线3天不收回/破20日线/单笔≤2%总资金）")
    L.append("")
    L.append("## 免责声明")
    L.append("")
    L.append("本报告由规则引擎自动生成，仅供个人方法论研究；所有信号须人工复核并独立决策。")

    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    md_p = SIGNAL_DIR / f"{target}_signal_report.md"
    html_p = SIGNAL_DIR / f"{target}_signal_report.html"
    md_p.write_text("\n".join(L), encoding="utf-8")
    md_file2html(md_p, html_p, f"妖板系统 每日信号 {target}")
    if args.log:
        import datetime as _dt

        with open(args.log, "a", encoding="utf-8") as f:
            f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} RUN OK date={target} "
                    f"env={env['score'] if env else 'NA'} confirmed={len(confirmed)} watch={len(watch)}\n")
    print("\n".join(L))
    print(f"\nREPORT -> {md_p}")
    store.close()


if __name__ == "__main__":
    import datetime as _dt

    _log_arg = ""
    try:
        main()
    except Exception as e:  # noqa: BLE001 - 计划任务场景下必须记录失败
        import traceback

        err = traceback.format_exc(limit=6)
        print("PIPELINE FAILED:", err)
        # 尝试写失败日志（即使 main 内部失败）
        try:
            SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
            _log_path = pathlib.Path(SIGNAL_DIR) / "daily.log"
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} RUN FAIL: "
                        f"{type(e).__name__}: {str(e)[:200]}\n{err[-500:]}\n")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)
