"""akshare 数据采集封装：重试 / 指数退避 / 限流友好

注意事项:
  - 东财接口（stock_zh_a_hist / stock_zt_pool_em）有频率限制，实测会 RemoteDisconnected。
    所有请求走 fetch_* 包装，自动重试 + 请求间隔。
  - akshare 以 --target 安装在 ../py_libs，运行需 PYTHONPATH 指向该目录；
    沙箱环境下读取 py_libs 需要完整权限（见 README）。
"""
from __future__ import annotations

import time

import pandas as pd

from config import section

CFG = section("data")
RETRY = int(CFG.get("retry_times", 3))
BACKOFF = list(CFG.get("retry_backoff_s", [2.0, 4.0, 8.0]))
SLEEP = float(CFG.get("request_sleep_s", 1.2))
_last_call = 0.0


def _throttle():
    """请求间隔节流（东财限流）"""
    global _last_call
    wait = SLEEP - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _retry(fn, *args, **kwargs):
    for attempt in range(RETRY):
        _throttle()
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - 采集器需要兜底
            if attempt == RETRY - 1:
                raise
            backoff = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            print(f"  [retry {attempt + 1}/{RETRY}] {fn.__name__} {args}: {type(e).__name__} "
                  f"{str(e)[:120]} -> sleep {backoff}s")
            time.sleep(backoff)
    return None


def _norm_date(s) -> str:
    return str(pd.to_datetime(s).date())


def fetch_index_daily(symbol: str) -> pd.DataFrame:
    """指数日线（新浪）：date/open/high/low/close/volume"""
    import akshare as ak

    df = _retry(ak.stock_zh_index_daily, symbol=symbol)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["symbol"] = symbol
    df["date"] = df["date"].map(_norm_date)
    return df[["symbol", "date", "open", "high", "low", "close", "volume"]]


def fetch_stock_daily(symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
    """个股日线，三级数据源回退：东财 → 新浪 → 腾讯（限流容错，见 ROADMAP 风险表）

    东财 stock_zh_a_hist 有频率限制（实测 RemoteDisconnected），失败时自动切换数据源。
    新浪 stock_zh_a_daily 需要 sh/sz 前缀；腾讯 stock_zh_a_hist_tx 作为最后手段。
    """
    errs = []
    try:
        df = _fetch_em_daily(symbol, start, end, adjust)
        if df is not None and not df.empty:
            return df
        errs.append("em:empty")
    except Exception as e:  # noqa: BLE001
        errs.append(f"em:{type(e).__name__}")
    try:
        df = _fetch_sina_daily(symbol, start, end)
        if df is not None and not df.empty:
            return df
        errs.append("sina:empty")
    except Exception as e:  # noqa: BLE001
        errs.append(f"sina:{type(e).__name__}")
    try:
        df = _fetch_tx_daily(symbol, start, end)
        if df is not None and not df.empty:
            return df
        errs.append("tx:empty")
    except Exception as e:  # noqa: BLE001
        errs.append(f"tx:{type(e).__name__}")
    raise ConnectionError(f"三级数据源均失败 [{symbol}]: {' | '.join(errs)}")


def _fetch_em_daily(symbol: str, start: str, end: str, adjust: str) -> pd.DataFrame:
    import akshare as ak

    df = _retry(ak.stock_zh_a_hist, symbol=symbol, period="daily",
                start_date=start, end_date=end, adjust=adjust)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["symbol"] = symbol
    df["date"] = df["日期"].map(_norm_date)
    df.rename(columns={
        "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount",
    }, inplace=True)
    return df[["symbol", "date", "open", "high", "low", "close", "volume", "amount"]]


def _sina_symbol(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "sh" + symbol
    if symbol.startswith(("0", "2", "3")):
        return "sz" + symbol
    return "bj" + symbol


def _fetch_sina_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    import akshare as ak

    df = _retry(ak.stock_zh_a_daily, symbol=_sina_symbol(symbol),
                start_date=start, end_date=end, adjust="qfq")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["symbol"] = symbol
    df["date"] = df["date"].map(_norm_date)
    if "amount" not in df.columns:
        df["amount"] = None
    return df[["symbol", "date", "open", "high", "low", "close", "volume", "amount"]]


def _fetch_tx_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    import akshare as ak

    df = _retry(ak.stock_zh_a_hist_tx, symbol=_sina_symbol(symbol),
                start_date=start, end_date=end, adjust="qfq")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["symbol"] = symbol
    df["date"] = df["date"].map(_norm_date)
    # 腾讯接口列: date/open/close/high/low/amount（amount 为成交量）
    df.rename(columns={"amount": "volume"}, inplace=True)
    df["amount"] = None
    return df[["symbol", "date", "open", "high", "low", "close", "volume", "amount"]]


def fetch_limit_up_pool(date: str) -> list[tuple]:
    """当日涨停池（东财，仅近期数据）→ [(date,symbol,name,pct,first_time,last_time,days,amount)]"""
    import akshare as ak

    df = _retry(ak.stock_zt_pool_em, date=date)
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append((
            date,
            str(r.get("代码", "")).zfill(6),
            str(r.get("名称", "")),
            float(r.get("涨跌幅", 0) or 0),
            str(r.get("首次封板时间", "") or ""),
            str(r.get("最后封板时间", "") or ""),
            int(r.get("连板数", 1) or 1),
            float(r.get("成交额", 0) or 0),
        ))
    return rows


def fetch_market_activity() -> dict | None:
    """市场活跃度（乐咕，仅当日快照）"""
    import akshare as ak

    df = _retry(ak.stock_market_activity_legu)
    if df is None or df.empty:
        return None
    kv = dict(zip(df["item"], df["value"]))
    out = {}
    for k, v in kv.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            out[str(k)] = str(v)
    return out


def index_symbols() -> list[str]:
    return list(CFG.get("index_symbols", ["sh000001"]))


if __name__ == "__main__":
    df = fetch_index_daily("sh000001")
    print("index rows:", len(df))
    print(df.tail(2).to_string())
