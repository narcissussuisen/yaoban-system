import sys, pathlib, io
sys.path.insert(0, r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\src')
import pandas as pd, requests
SW_URL = "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
out = pathlib.Path(r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\data\sw_industry_history.csv')
if out.exists():
    print('已缓存', out, out.stat().st_size, 'bytes')
    sys.exit(0)
r = requests.get(SW_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
r.raise_for_status()
df = pd.read_excel(io.BytesIO(r.content))
df = df.rename(columns={"股票代码": "code", "计入日期": "start_date", "行业代码": "industry_code", "更新日期": "update_date"})
df["code"] = df["code"].astype(str).str.zfill(6)
df["industry_code"] = df["industry_code"].astype(str).str.zfill(6)
df["l1_code"] = df["industry_code"].str[:2] + "0000"
df["l2_code"] = df["industry_code"].str[:4] + "00"
df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
df = df.sort_values(["code", "start_date"]).reset_index(drop=True)
df.to_csv(out, index=False)
print(f"下载完成: {len(df)} 行 | {df['code'].nunique()} 只 | {df['l1_code'].nunique()} 个一级行业")
print(df.head(3).to_string())
