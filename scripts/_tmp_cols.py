import pandas as pd
df = pd.read_csv("outputs/backtest_market_2025_t3raw.csv", dtype={"sym": str})
print("cols:", list(df.columns))
print(df.head(3).to_string())
