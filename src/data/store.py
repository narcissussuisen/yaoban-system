"""SQLite 存储层（标准库 sqlite3，零第三方依赖）

表结构:
  index_daily   (symbol, date, open, high, low, close, volume)        指数日线
  stock_daily   (symbol, date, open, high, low, close, volume, amount) 个股日线(前复权)
  limit_up_pool (date, symbol, name, pct, first_time, last_time, days, amount) 每日涨停池快照
  market_activity (date, up_count, down_count, limit_up, limit_down, turnover) 活跃度快照
  meta          (key, value)                                           元信息(数据范围/版本)
"""
from __future__ import annotations

import pathlib
import sqlite3

from config import section

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_DB = DATA_DIR / "yaoban.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS index_daily (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS stock_daily (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS limit_up_pool (
    date TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT,
    pct REAL, first_time TEXT, last_time TEXT, days INTEGER, amount REAL,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS market_activity (
    date TEXT PRIMARY KEY,
    up_count INTEGER, down_count INTEGER,
    limit_up INTEGER, limit_down INTEGER, turnover REAL
);
CREATE TABLE IF NOT EXISTS market_sentiment (
    date TEXT PRIMARY KEY,
    zt_count INTEGER, zb_count INTEGER, dt_count INTEGER,
    break_rate REAL, max_height INTEGER, ladder TEXT
);
CREATE TABLE IF NOT EXISTS minute_kline (
    symbol TEXT NOT NULL, freq TEXT NOT NULL, ts TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
    PRIMARY KEY (symbol, freq, ts)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE INDEX IF NOT EXISTS idx_index_daily_date ON index_daily(date);
CREATE INDEX IF NOT EXISTS idx_stock_daily_date ON stock_daily(date);
CREATE INDEX IF NOT EXISTS idx_minute_kline_sym ON minute_kline(symbol, freq, ts);
"""


class Store:
    def __init__(self, db_path: pathlib.Path | str | None = None):
        self.db_path = pathlib.Path(db_path) if db_path else DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- 通用 ----------
    def close(self):
        self.conn.close()

    def meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---------- 写入（df 需含指定列） ----------
    def upsert_index_daily(self, df, symbol: str):
        rows = [
            (symbol, str(r["date"])[:10], r["open"], r["high"], r["low"], r["close"], r["volume"])
            for _, r in df.iterrows()
        ]
        self.conn.executemany(
            "INSERT INTO index_daily(symbol,date,open,high,low,close,volume) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,"
            "close=excluded.close,volume=excluded.volume",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def upsert_stock_daily(self, df, symbol: str):
        rows = [
            (symbol, str(r["date"])[:10], r["open"], r["high"], r["low"], r["close"],
             r["volume"], r.get("amount"))
            for _, r in df.iterrows()
        ]
        self.conn.executemany(
            "INSERT INTO stock_daily(symbol,date,open,high,low,close,volume,amount) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,"
            "close=excluded.close,volume=excluded.volume,amount=excluded.amount",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def upsert_limit_up_pool(self, rows):
        self.conn.executemany(
            "INSERT OR REPLACE INTO limit_up_pool(date,symbol,name,pct,first_time,last_time,days,amount) "
            "VALUES(?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def upsert_activity(self, date: str, **fields):
        self.conn.execute(
            "INSERT INTO market_activity(date,up_count,down_count,limit_up,limit_down,turnover) "
            "VALUES(:date,:up_count,:down_count,:limit_up,:limit_down,:turnover) "
            "ON CONFLICT(date) DO UPDATE SET "
            "up_count=excluded.up_count,down_count=excluded.down_count,"
            "limit_up=excluded.limit_up,limit_down=excluded.limit_down,turnover=excluded.turnover",
            {"date": date, **fields},
        )
        self.conn.commit()

    def upsert_sentiment(self, date: str, zt_count: int, zb_count: int, dt_count: int,
                         break_rate: float, max_height: int, ladder: dict):
        """打板情绪快照（a-stock-data limit_up_sentiment）"""
        import json

        self.conn.execute(
            "INSERT INTO market_sentiment(date,zt_count,zb_count,dt_count,break_rate,max_height,ladder) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
            "zt_count=excluded.zt_count,zb_count=excluded.zb_count,dt_count=excluded.dt_count,"
            "break_rate=excluded.break_rate,max_height=excluded.max_height,ladder=excluded.ladder",
            (date, zt_count, zb_count, dt_count, break_rate, max_height, json.dumps(ladder, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_sentiment(self, date: str | None = None) -> list[tuple]:
        """情绪快照；date 缺省返回全部（升序）"""
        if date:
            return self.conn.execute(
                "SELECT * FROM market_sentiment WHERE date=? ORDER BY date", (date,)).fetchall()
        return self.conn.execute("SELECT * FROM market_sentiment ORDER BY date").fetchall()

    def sentiment_as_of(self, date: str) -> dict | None:
        """截至 date 的最近一条情绪快照（含 ladder 解析）"""
        import json

        row = self.conn.execute(
            "SELECT * FROM market_sentiment WHERE date<=? ORDER BY date DESC LIMIT 1", (date,)).fetchone()
        if not row:
            return None
        return {"date": row[0], "zt_count": row[1], "zb_count": row[2], "dt_count": row[3],
                "break_rate": row[4], "max_height": row[5],
                "ladder": json.loads(row[6]) if row[6] else {}}

    # ---------- 分钟线（H7.1，freq: 1m/5m/15m/30m/60m） ----------
    def upsert_minute(self, df, symbol: str, freq: str):
        if df is None or df.empty:
            return 0
        rows = [
            (symbol, freq, str(r["ts"]), r["open"], r["high"], r["low"], r["close"],
             r["volume"], r.get("amount"))
            for _, r in df.iterrows()
        ]
        self.conn.executemany(
            "INSERT INTO minute_kline(symbol,freq,ts,open,high,low,close,volume,amount) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,freq,ts) DO UPDATE SET "
            "open=excluded.open,high=excluded.high,low=excluded.low,"
            "close=excluded.close,volume=excluded.volume,amount=excluded.amount",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_minute(self, symbol: str, freq: str = "5m",
                   start: str | None = None, end: str | None = None) -> list[tuple]:
        sql = "SELECT * FROM minute_kline WHERE symbol=? AND freq=?"
        args: list = [symbol, freq]
        if start:
            sql += " AND ts>=?"
            args.append(start)
        if end:
            sql += " AND ts<=?"
            args.append(end)
        sql += " ORDER BY ts"
        return self.conn.execute(sql, args).fetchall()

    def minute_stats(self) -> dict:
        """按 (symbol, freq) 统计根数与时间范围"""
        rows = self.conn.execute(
            "SELECT symbol,freq,COUNT(*),MIN(ts),MAX(ts) FROM minute_kline "
            "GROUP BY symbol,freq ORDER BY symbol,freq").fetchall()
        return {f"{r[0]}/{r[1]}": {"n": r[2], "min": r[3], "max": r[4]} for r in rows}

    # ---------- 查询 ----------
    def get_index(self, symbol: str, start: str | None = None, end: str | None = None):
        return self._query("index_daily", symbol, start, end)

    def get_stock(self, symbol: str, start: str | None = None, end: str | None = None):
        return self._query("stock_daily", symbol, start, end)

    def _query(self, table: str, symbol: str, start: str | None, end: str | None):
        sql = f"SELECT * FROM {table} WHERE symbol=?"
        args: list = [symbol]
        if start:
            sql += " AND date>=?"
            args.append(start)
        if end:
            sql += " AND date<=?"
            args.append(end)
        sql += " ORDER BY date"
        return self.conn.execute(sql, args).fetchall()

    def get_limit_up_dates(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT date FROM limit_up_pool ORDER BY date").fetchall()
        return [r[0] for r in rows]

    def get_limit_up_on(self, date: str) -> list[tuple]:
        return self.conn.execute(
            "SELECT symbol,name,pct,days FROM limit_up_pool WHERE date=?", (date,)
        ).fetchall()

    def get_activity(self) -> list[tuple]:
        return self.conn.execute("SELECT * FROM market_activity ORDER BY date").fetchall()

    def stats(self) -> dict:
        out = {}
        for t in ("index_daily", "stock_daily", "limit_up_pool", "market_activity", "minute_kline"):
            n = self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            out[t] = n
        return out


if __name__ == "__main__":
    s = Store()
    print("db:", s.db_path)
    print("stats:", s.stats())
    print("config data section keys:", list(section("data").keys()))
    s.close()
