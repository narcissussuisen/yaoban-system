"""R7 Level2 低吸质量分验证（8/21+8/24+8/25 三天，全 L2 数据闭环）

流程：L2 行情快照(3s) → 聚合 1 分钟线 → B2 触发检测 → 触发时刻承接质量分
      → 触发后 30/60 分钟收益（L2 成交价序列）
质量分：大单净买(30万+，前30分钟) + 委托簿买盘净增 + 盘口买卖比
输出: outputs/l2_quality_score.md
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import numpy as np

L2 = pathlib.Path(r"F:/WorkBuddyItem/a股 level2/parquet")
OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "l2_quality_score.md"
DATES = ["20260821", "20260824", "20260825"]


def ts_to_int(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 10000000 + int(m) * 100000


def minute_from_hq(hq: pd.DataFrame, date: str = "20260821") -> pd.DataFrame:
    """3s 行情快照 → 1 分钟 OHLCV（价格 /10000，ts=YYYY-MM-DD HH:MM）"""
    h = hq[hq["成交价"] > 0].copy()
    if h.empty:
        return pd.DataFrame()
    h["min"] = h["时间"] // 100000  # HHMM 基准（HH 无前导零，如 915=9:15）
    h["HH"] = h["min"] // 100
    h["MM"] = h["min"] % 100
    d8 = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    h["ts"] = d8 + " " + h["HH"].apply(lambda x: f"{int(x):02d}") + ":" + h["MM"].apply(lambda x: f"{int(x):02d}")
    g = h.groupby("ts")
    out = pd.DataFrame({
        "open": g["成交价"].first() / 10000.0,
        "high": g["成交价"].max() / 10000.0,
        "low": g["成交价"].min() / 10000.0,
        "close": g["成交价"].last() / 10000.0,
        "volume": g["成交量"].sum(),
        "amount": g["成交额"].sum(),
    })
    out["ts"] = out.index
    return out.reset_index(drop=True)


def quality(cj: pd.DataFrame, wt: pd.DataFrame, hq: pd.DataFrame, trig_int: int) -> dict:
    out = {"big_buy": 0.0, "bid_net": 0.0, "ba_ratio": 1.0}
    if cj is not None and len(cj):
        c = cj[(cj["时间"] >= trig_int - 30000000) & (cj["时间"] <= trig_int)]
        if len(c):
            c = c.copy()
            c["amt"] = c["成交价格"] / 10000.0 * c["成交数量"]
            big = c[c["amt"] >= 300000]
            out["big_buy"] = (big[big["BS标志"] == "B"]["amt"].sum()
                              - big[big["BS标志"] == "S"]["amt"].sum()) / 1e6
    if wt is not None and len(wt):
        w = wt[(wt["时间"] >= trig_int - 30000000) & (wt["时间"] <= trig_int)]
        if len(w):
            adds = w[w["委托类型"] == "A"]
            cancels = w[w["委托类型"] == "D"]
            out["bid_net"] = ((adds[adds["委托代码"] == "B"]["委托数量"].sum()
                               - cancels[cancels["委托代码"] == "B"]["委托数量"].sum()) / 1e4)
    if hq is not None and len(hq):
        h = hq[hq["时间"] <= trig_int]
        if len(h):
            last = h.iloc[-1]
            ba = float(last.get("叫买总量") or 0)
            sa = float(last.get("叫卖总量") or 0)
            out["ba_ratio"] = ba / sa if sa > 0 else 2.0
    return out


def fwd_ret(hq: pd.DataFrame, trig_int: int, minutes: int) -> float | None:
    """触发后 N 分钟收益（%），按成交价序列"""
    h = hq[(hq["成交价"] > 0) & (hq["时间"] >= trig_int)].copy()
    if h.empty:
        return None
    target = trig_int + minutes * 60000
    seg = h[h["时间"] <= target]
    if seg.empty:
        return None
    px0 = float(hq[hq["时间"] <= trig_int]["成交价"].iloc[-1]) if len(hq[hq["时间"] <= trig_int]) else None
    if not px0 or px0 <= 0:
        return None
    return (float(seg["成交价"].iloc[-1]) / px0 - 1) * 100


def main():
    sys.path.insert(0, pathlib.Path(__file__).resolve().parent.parent / "src")
    from core.intraday import detect_b_point
    rows_out = []
    for date in DATES:
        d = pathlib.Path(L2 / date)
        if not d.exists():
            continue
        syms = sorted({f.name.split("_")[0] for f in d.glob("*_行情.parquet")})
        for sym in syms:
            try:
                hq = pd.read_parquet(d / f"{sym}_行情.parquet")
                cj = pd.read_parquet(d / f"{sym}_逐笔成交.parquet")
                wt = pd.read_parquet(d / f"{sym}_逐笔委托.parquet")
            except Exception:
                continue
            m1 = minute_from_hq(hq, date)
            if len(m1) < 30:
                continue
            # B2 检测（L2 分钟线；prev_close 用行情表前收盘）
            pc = float(hq["前收盘"].iloc[0]) / 10000.0 if len(hq) and hq["前收盘"].iloc[0] > 0 else None
            if not pc:
                continue
            df1 = m1.copy()
            df1["ts"] = df1["ts"]
            pts = detect_b_point(df1, prev_close=pc)
            if pts.empty:
                continue
            b2 = pts[pts["kind"] == "B2"]
            if b2.empty:
                continue
            r0 = b2.iloc[0]
            trig = ts_to_int(r0["ts"][11:16])
            qs = quality(cj, wt, hq, trig)
            b2px = float(r0["price"])
            rows_out.append({
                "sym": sym, "date": date, "ts": r0["ts"], "b2_px": b2px,
                "r30": fwd_ret(hq, trig, 30), "r60": fwd_ret(hq, trig, 60),
                "r_close": (float(hq[hq["成交价"] > 0]["成交价"].iloc[-1]) / 10000.0 / b2px - 1) * 100,
                **qs})
    df = pd.DataFrame(rows_out)
    print(f"B2 触发样本（3 天 L2）: {len(df)}")
    if df.empty:
        return
    df["score"] = (df["big_buy"].rank(pct=True) + df["bid_net"].rank(pct=True)
                   + df["ba_ratio"].rank(pct=True)) / 3
    L = [f"# R7 Level2 低吸质量分验证（3 天 L2，n={len(df)}）", "",
         "> 质量分=大单净买+买盘净增+盘口买卖比 的分位均值；收益为 L2 成交价口径。", "",
         "| 质量分档 | n | r30均值% | r60均值% | 当日收盘均值% | 胜率% |",
         "|---|---|---|---|---|---|"]
    for lo, hi, lab in ((0, 0.33, "低分位"), (0.33, 0.66, "中分位"), (0.66, 1.01, "高分位")):
        g = df[(df["score"] >= lo) & (df["score"] < hi)]
        if len(g):
            L.append(f"| {lab} | {len(g)} | {g['r30'].mean():+.2f} | {g['r60'].mean():+.2f} "
                     f"| {g['r_close'].mean():+.2f} | {(g['r_close'] > 0).mean() * 100:.0f} |")
    L.append("")
    L.append("| 因子 | 低档 r60% | 高档 r60% | 差值pp |")
    L.append("|---|---|---|---|")
    for col in ("big_buy", "bid_net", "ba_ratio"):
        med = df[col].median()
        lo_g = df[df[col] <= med]["r60"].dropna()
        hi_g = df[df[col] > med]["r60"].dropna()
        if len(lo_g) and len(hi_g):
            L.append(f"| {col} | {lo_g.mean():+.2f} | {hi_g.mean():+.2f} | {hi_g.mean() - lo_g.mean():+.2f} |")
    L.append("")
    L.append("> 三天样本，方向性证据。仅供方法论研究。")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {OUT}")


if __name__ == "__main__":
    main()