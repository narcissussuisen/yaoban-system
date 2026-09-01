"""R3' 第一轮自验：乖离率止盈/KDJ死叉 × 选手案例触发测试"""
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.qfq_store import QFQStore
from core import indicators as ind

# 案例1: 密尔克卫 7/8 KDJ死叉止损（选手原话"出线死叉选择止损出局"）
qfq = QFQStore("2026")
rows = qfq.get_stock("603713")
df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
dates = df["date"].astype(str).tolist()
i = dates.index("2026-07-08")
kd = ind.kdj(df.iloc[:i+1])
print(f"密尔克卫 7/8: K={kd['k'].iloc[-1]:.1f} D={kd['d'].iloc[-1]:.1f} J={kd['j'].iloc[-1]:.1f}")
print(f"  前日 K={kd['k'].iloc[-2]:.1f} D={kd['d'].iloc[-2]:.1f}")
dead = kd['k'].iloc[-2] > kd['d'].iloc[-2] and kd['k'].iloc[-1] < kd['d'].iloc[-1]
print(f"  死叉触发: {dead}")

# 案例2: 圣阳 4/21 开盘出局（热度高+乖离大）
rows2 = qfq.get_stock("002580")
df2 = pd.DataFrame(rows2, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
dates2 = df2["date"].astype(str).tolist()
i2 = dates2.index("2026-04-21")
bias = ind.bias_ma5(df2)
print(f"\n圣阳 4/21: 乖离率={bias.iloc[i2]:.1f}% (7连板后落袋)")
print(f"  4/20 乖离率={bias.iloc[i2-1]:.1f}%")

# 案例3: 华微电子 7/10 炸板（zhaban_sell 已有，确认涨停价回落）
rows3 = qfq.get_stock("600360")
df3 = pd.DataFrame(rows3, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
dates3 = df3["date"].astype(str).tolist()
i3 = dates3.index("2026-07-10")
pc = float(df3["close"].iloc[i3-1])
lpx = round(pc * 1.10, 2)
print(f"\n华微 7/10: 昨收{pc:.2f} 涨停价{lpx:.2f} 当日高{df3['high'].iloc[i3]:.2f} 收{df3['close'].iloc[i3]:.2f}")
print(f"  触板后回落: high>=limit({df3['high'].iloc[i3] >= lpx-0.01}) 收盘<limit({df3['close'].iloc[i3] < lpx-0.005})")
