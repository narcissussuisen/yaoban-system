"""P1 适配层：TDX 逐笔 → 大单净买/抛压衰竭指标（每日 tick 采集数据）

输入: F:/WorkBuddyItem/a股level2/tick/{YYYYMMDD}/{sym}.parquet (time, price, vol, num, buyorsell)
      buyorsell: 0=买(外盘) 1=卖(内盘)（通达信约定）
输出: 每日指标表（每 symbol 每 30 分钟窗口的大单净买等）
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys

TICK = Path(r"F:/WorkBuddyItem/a股level2/tick")

def load_day(date: str) -> pd.DataFrame:
    """加载某日全部 tick，返回 symbol/time/price/vol/num/buyorsell"""
    d = TICK / date
    if not d.exists():
        return pd.DataFrame()
    frames = []
    for p in d.glob("*.parquet"):
        try:
            df = pd.read_parquet(p)
            if df.empty:
                continue
            df["symbol"] = p.stem
            frames.append(df)
        except Exception as e:
            print(f"  skip {p.name}: {e}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

def big_buy_net(df: pd.DataFrame, window_min: int = 30, big_amt: float = 300000) -> pd.DataFrame:
    """每 symbol 每窗口的大单净买（30万+，元）"""
    if df.empty:
        return pd.DataFrame()
    d = df.copy()
    # 竞价段过滤：仅保留 09:25 后（集合竞价 buyorsell=8/9 等非 0/1 标记忽略）
    d = d[d["buyorsell"].isin((0, 1))]
    d = d[d["vol"] > 0]
    # vol 单位=手（1手=100股）
    d["amt"] = d["price"] * d["vol"] * 100.0
    d["dir"] = np.where(d["buyorsell"] == 0, 1, -1)
    d["big"] = d["amt"] >= big_amt
    d["hhmm"] = d["time"].astype(str).str.slice(0, 2) + d["time"].astype(str).str.slice(3, 5)
    d["hhmm"] = pd.to_datetime(d["hhmm"], format="%H%M")
    d["win"] = (d["hhmm"].dt.hour * 60 + d["hhmm"].dt.minute) // window_min
    g = d[d["big"]].groupby(["symbol", "win"])
    out = g.apply(lambda x: (x["dir"] * x["amt"]).sum() / 1e6, include_groups=False).rename("big_buy_net_M")
    return out.reset_index()

if __name__ == "__main__":
    import sys as _s
    date = _s.argv[1] if len(_s.argv) > 1 else "20260826"
    df = load_day(date)
    print(f"{date}: {len(df)} rows, {df['symbol'].nunique() if len(df) else 0} symbols")
    if len(df):
        print(df.head(5).to_string())
        out = big_buy_net(df)
        print(out.head(10).to_string())
        print(f"windows: {out['win'].nunique() if len(out) else 0}")
