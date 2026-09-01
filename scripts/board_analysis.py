"""R4 板块共振分析：申万行业状态 × 模式交叉验证

行业归属：申万 2021 分类变迁史（as-of 防前视偏差）
行业指数：全市场日线按行业等权聚合（日收益链）
行业状态：行业指数 5 日趋势 + 行业内当日涨停家数
交叉验证：5+ 连板（R3）/ 形态池（R1）候选 × 行业状态分档
输出: outputs/board_analysis_{year}.md
"""
from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402
from data.qfq_store import QFQStore, build_daily_map  # noqa: E402
from core.sell import limit_pct_of  # noqa: E402

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"
SW = pathlib.Path(__file__).resolve().parent.parent / "data" / "sw_industry_history.csv"


def main():
    t0 = time.time()
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"
    store = Store()
    qfq = QFQStore(year)
    symbols = [s for s in qfq.symbols() if not s.startswith(("399", "5", "15", "16"))]
    print(f"[{time.time()-t0:.0f}s] 聚合日线…", flush=True)
    daily_map = build_daily_map(year, symbols, workers=6)
    daily_map = {k: v for k, v in daily_map.items() if v}
    print(f"[{time.time()-t0:.0f}s] 日线就绪 {len(daily_map)}，加载申万归属…", flush=True)
    sw = pd.read_csv(SW, dtype={"code": str, "industry_code": str, "l1_code": str, "l2_code": str})
    sw["start_date"] = pd.to_datetime(sw["start_date"], errors="coerce")
    sw = sw.dropna(subset=["start_date"]).sort_values(["code", "start_date"])
    print(f"[{time.time()-t0:.0f}s] 申万 {len(sw)} 行 {sw['l1_code'].nunique()} 行业", flush=True)

    # 行业归属（as-of）：对每只股票构建 (start_date, l1) 序列，用 searchsorted 向量化查询
    sw_by = {c: g.sort_values("start_date") for c, g in sw.groupby("code")}
    _cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def l1_lookup(sym: str) -> tuple[np.ndarray, np.ndarray] | None:
        if sym in _cache:
            return _cache[sym]
        g = sw_by.get(sym)
        if g is None or not len(g):
            _cache[sym] = None
            return None
        arr = (g["start_date"].astype("int64").to_numpy() // 10**9,
               g["l1_code"].to_numpy())
        _cache[sym] = arr
        return arr

    def l1_of(sym: str, date_ts) -> str | None:
        lu = l1_lookup(sym)
        if lu is None:
            return None
        ts_arr, codes = lu
        pos = np.searchsorted(ts_arr, int(date_ts.timestamp()), side="right") - 1
        return str(codes[pos]) if pos >= 0 else None

    # 每日行业状态
    # 1) 每只股票每日收益 + 行业归属 + 涨停标记
    all_rows = []
    for sym, rows in daily_map.items():
        if len(rows) < 25:
            continue
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                         "close", "volume", "amount"])
        c = df["close"]
        ret = c.pct_change() * 100
        lpx = limit_pct_of(sym)
        zt = (c / c.shift(1) - 1 >= lpx - 0.005).astype(int)
        prev = c.shift(1)
        lpx_v = prev * (1 + lpx)
        zb = ((df["high"] >= lpx_v - 0.01) & (c < lpx_v - 0.005)).astype(int)
        for i in range(25, len(df)):
            d = pd.Timestamp(df["date"].iloc[i])
            l1 = l1_of(sym, d)
            if l1 is None:
                continue
            all_rows.append({"date": df["date"].iloc[i], "l1": l1, "sym": sym,
                             "ret": float(ret.iloc[i]) if pd.notna(ret.iloc[i]) else 0.0,
                             "zt": int(zt.iloc[i]), "zb": int(zb.iloc[i])})
    adf = pd.DataFrame(all_rows)
    print(f"[{time.time()-t0:.0f}s] 日度面板 {len(adf)} 行", flush=True)

    # 行业日收益（等权）与 5 日趋势、行业涨停家数
    ind_ret = adf.groupby(["date", "l1"])["ret"].mean().reset_index()
    ind_zt = adf.groupby(["date", "l1"])["zt"].sum().reset_index().rename(columns={"zt": "zt_n"})
    board = ind_ret.merge(ind_zt, on=["date", "l1"])
    board = board.sort_values(["l1", "date"])
    board["ret5"] = board.groupby("l1")["ret"].transform(lambda s: s.rolling(5).sum())
    print(f"[{time.time()-t0:.0f}s] 行业面板 {len(board)} 行，交叉验证…", flush=True)

    # 加载模式候选
    daban = pd.read_csv(OUT_DIR / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    pull = pd.read_csv(OUT_DIR / f"pullback_b2_{year}_raw.csv", dtype={"sym": str})
    L = [f"# R4 板块共振分析 {year}", "",
         f"> 申万 38 一级行业（变迁史 as-of 防前视）；行业指数=全市场等权；ret5=行业5日累计收益%；zt_n=行业内当日涨停家数。", ""]

    # 行业状态分档（按当日截面）
    def board_state(d: str, l1: str) -> dict | None:
        b = board[(board["date"] == d) & (board["l1"] == l1)]
        return b.iloc[0].to_dict() if len(b) else None

    # a) 5+ 连板 × 行业状态
    L.append("## 5+ 连板（妖股接力）× 行业状态（无纪律对照）")
    L.append("")
    L.append("| 行业状态 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    rows = []
    for _, r in daban[daban["lb"] >= 5].dropna(subset=["pnl_nt"]).iterrows():
        l1 = l1_of(r["sym"], pd.Timestamp(r["t"]))
        bs = board_state(r["t"], l1) if l1 else None
        if bs is None:
            continue
        rows.append({"pnl": r["pnl_nt"], "ret5": bs["ret5"], "zt_n": bs["zt_n"]})
    bd = pd.DataFrame(rows)
    if len(bd):
        for name, cond in (("行业5日趋势<0", bd["ret5"] < 0), ("行业5日趋势≥0", bd["ret5"] >= 0),
                           ("行业内涨停<3", bd["zt_n"] < 3), ("行业内涨停≥3", bd["zt_n"] >= 3),
                           ("行业强(趋势≥0且涨停≥3)", (bd["ret5"] >= 0) & (bd["zt_n"] >= 3))):
            g = bd[cond]
            if len(g):
                L.append(f"| {name} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl'] > 0).mean() * 100:.0f} |")
    L.append("")

    # b) 形态池（开盘执行）× 行业状态
    L.append("## 形态池（开盘执行）× 行业状态")
    L.append("")
    L.append("| 行业状态 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    rows = []
    for _, r in pull.dropna(subset=["p_open"]).iterrows():
        l1 = l1_of(r["sym"], pd.Timestamp(r["d"]))
        bs = board_state(r["d"], l1) if l1 else None
        if bs is None:
            continue
        rows.append({"pnl": r["p_open"], "ret5": bs["ret5"], "zt_n": bs["zt_n"]})
    pd_ = pd.DataFrame(rows)
    if len(pd_):
        for name, cond in (("行业5日趋势<0", pd_["ret5"] < 0), ("行业5日趋势≥0", pd_["ret5"] >= 0),
                           ("行业内涨停<3", pd_["zt_n"] < 3), ("行业内涨停≥3", pd_["zt_n"] >= 3),
                           ("行业强(趋势≥0且涨停≥3)", (pd_["ret5"] >= 0) & (pd_["zt_n"] >= 3))):
            g = pd_[cond]
            if len(g):
                L.append(f"| {name} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl'] > 0).mean() * 100:.0f} |")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    outp = OUT_DIR / f"board_analysis_{year}.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {outp}  （总耗时 {(time.time()-t0)/60:.1f} 分钟）")
    store.close()


if __name__ == "__main__":
    main()