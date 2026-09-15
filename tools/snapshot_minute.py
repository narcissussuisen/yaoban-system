"""分钟数据本地快照管线（解锁分时级规则的确定性复现）。

背景（2026-09-11 实测）：
  - F 盘分钟快照 `F:/WorkBuddyItem/a股分钟线/parquet_qfq_2026` 停在 2026-08-25，
    无法覆盖 9/3-9/10 的案例日期 → 分时规则（KC-0005 跌破均价线一票否决）此前无数据可验。
  - pytdx 可在线拉 1m（约 3.7 个月窗口）/ 5m（约 1.7 年），按 800 根分段翻页。

因此本工具做**按需本地快照**：只对「案例与候选标的」拉取并落 parquet，
一次落盘长期复用，避免每日重复联网；文件头记录来源与拉取时间，可追溯。

用法:
    python tools/snapshot_minute.py --codes 603615,605006 --freq 1m --since 2026-08-20
    python tools/snapshot_minute.py --from-case-table --freq 1m --since 2026-08-20
    python tools/snapshot_minute.py --verify            # 只校验已有快照覆盖度
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SNAPSHOT_DIR = ROOT / "data" / "minute"
# R2.4（2026-09-12）：路径改为由 src/data/minute_paths.py 统一解析 ——
# 环境变量 EVOALPHA_MINUTE_SNAPSHOT 可覆盖根目录，便于把快照整体迁到 F 盘而**无需改代码**。
try:
    from data.minute_paths import snapshot_root as _snapshot_root, plan_path as _plan_path
    SNAPSHOT_DIR = _snapshot_root()
except Exception:  # noqa: BLE001 - src 不可导入时退回本地默认
    def _plan_path():
        return SNAPSHOT_DIR / "_fetch_plan.json"
CASE_FILE = ROOT / "data" / "iteration" / "case_table_yaoban.json"


def meta_path(code: str, freq: str) -> pathlib.Path:
    return SNAPSHOT_DIR / freq / f"{code}.meta.json"


def data_path(code: str, freq: str) -> pathlib.Path:
    return SNAPSHOT_DIR / freq / f"{code}.parquet"


def case_universe() -> list[str]:
    """案例表涉及的标的全集（入场 + 清仓 + 公开候选 + 命中未成交）+ 分时决策案例。"""
    codes: set[str] = set()
    if CASE_FILE.exists():
        t = json.loads(CASE_FILE.read_text(encoding="utf-8"))
        for key in ("entries", "exits", "candidates", "screened_no_fill"):
            for row in t.get(key, []):
                c = row.get("code")
                if c:
                    codes.add(str(c))
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from iteration.intraday import DECISION_CASES
        for cases in DECISION_CASES.values():
            for code, *_rest in cases:
                codes.add(str(code))
    except Exception:  # noqa: BLE001
        pass
    return sorted(codes)


def signal_universe(rules_names: list[str], config_path: pathlib.Path,
                    market_start: str, market_codes: int) -> list[str]:
    """信号标的全集：各入场规则在回测窗口内触发过的标的（供分时反事实检验）。"""
    sys.path.insert(0, str(ROOT / "src"))
    import tomllib

    from iteration import market, rules as rules_mod

    cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    codes = market.available_codes(limit=market_codes)
    hit: set[str] = set()
    for name in rules_names:
        params = rules_mod.rule_params(name, cfg)
        fn = rules_mod.RULES[name]["fn"]
        for code in codes:
            df = market.load_daily(code)
            if df is None or len(df) < 80:
                continue
            try:
                sig = fn(df, code, **params)
            except Exception:  # noqa: BLE001
                continue
            idx = np.flatnonzero(sig.to_numpy())
            if not len(idx):
                continue
            for i in idx:
                if str(df["date"].iloc[i]) >= market_start:
                    hit.add(code)
                    break
    return sorted(hit)


def snapshot_one(code: str, freq: str, since: str, *, force: bool = False) -> dict:
    """拉取并落盘单只标的分钟线。已存在且覆盖 since 时跳过（除非 force）。"""
    from data.minute import fetch_minute_kline

    dp, mp = data_path(code, freq), meta_path(code, freq)
    prev = None
    if mp.exists():
        try:
            prev = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = None
    if not force and prev and dp.exists() and prev.get("since", "9999") <= since:
        return {"code": code, "freq": freq, "status": "cached",
                "rows": prev.get("rows"), "first": prev.get("first"), "last": prev.get("last")}

    df = fetch_minute_kline(code, freq=freq, since=since)
    if df is None or df.empty:
        return {"code": code, "freq": freq, "status": "empty"}

    dp.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dp, index=False)
    meta = {
        "code": code, "freq": freq, "since": since,
        "rows": int(len(df)), "first": str(df["ts"].iloc[0]), "last": str(df["ts"].iloc[-1]),
        "columns": list(df.columns),
        "source": "pytdx get_security_bars (see src/data/minute.py PYTDX_SERVERS)",
        "fetched_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "unit_note": "volume 为股；amount 为元",
    }
    mp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"code": code, "freq": freq, "status": "fetched",
            "rows": meta["rows"], "first": meta["first"], "last": meta["last"]}


def verify(freq: str = "1m") -> dict:
    """校验快照覆盖度：逐只给出日期区间，标注是否覆盖 2026-08-28~2026-09-10 案例窗口。"""
    out = []
    need_lo, need_hi = "2026-08-28", "2026-09-10"
    for code in case_universe():
        mp = meta_path(code, freq)
        if not mp.exists():
            out.append({"code": code, "status": "missing"})
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        cov = (m["first"][:10] <= need_lo) and (m["last"][:10] >= need_hi)
        out.append({"code": code, "status": "covered" if cov else "partial",
                    "first": m["first"], "last": m["last"], "rows": m["rows"],
                    "fetched_at": m.get("fetched_at")})
    ok = sum(1 for x in out if x["status"] == "covered")
    return {"freq": freq, "need_window": [need_lo, need_hi], "total": len(out),
            "covered": ok, "detail": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="分钟数据本地快照")
    ap.add_argument("--codes", default=None, help="逗号分隔股票代码；缺省配合 --from-case-table")
    ap.add_argument("--from-case-table", action="store_true", help="用案例表标的全集")
    ap.add_argument("--from-signals", action="store_true",
                    help="用「各入场规则触发过的标的全集」（分时反事实检验用）")
    ap.add_argument("--rules", default="huigui,zthuicai,fanbao")
    ap.add_argument("--config", default=str(ROOT / "config" / "parameters.toml"))
    ap.add_argument("--market-start", default="2026-04-01")
    ap.add_argument("--market-codes", type=int, default=1200)
    ap.add_argument("--workers", type=int, default=1, help="并发进程数（沙箱下不可用，见下方说明）")
    ap.add_argument("--shard", default=None, help="分片 i/n（如 1/6）；用多个独立进程并行拉取")
    ap.add_argument("--from-plan", action="store_true",
                    help="读 data/minute/_fetch_plan.json 的 need 列表")
    ap.add_argument("--freq", default="1m", choices=["1m", "5m", "15m", "30m", "60m"])
    ap.add_argument("--since", default="2026-08-20")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--verify", action="store_true", help="只校验覆盖度，不拉取")
    ap.add_argument("--limit", type=int, default=0, help="限制标的数（0=不限）")
    a = ap.parse_args(argv)

    if a.verify:
        print(json.dumps(verify(a.freq), ensure_ascii=False, indent=1))
        return 0

    codes = [c.strip() for c in a.codes.split(",")] if a.codes else []
    if a.from_plan:
        pf = _plan_path()
        codes = json.loads(pf.read_text(encoding="utf-8")).get("need", [])
    elif a.from_signals:
        codes = signal_universe([x.strip() for x in a.rules.split(",")],
                                pathlib.Path(a.config), a.market_start, a.market_codes)
        print(f"signal universe: {len(codes)} codes")
    elif a.from_case_table or not codes:
        codes = case_universe()
    if a.shard:
        i, n = (int(x) for x in a.shard.split("/"))
        codes = [c for k, c in enumerate(codes) if k % n == (i - 1)]
        print(f"shard {i}/{n}: {len(codes)} codes")
    if a.limit:
        codes = codes[:a.limit]
    if not codes:
        print("no codes to snapshot", file=sys.stderr)
        return 2

    results = []
    if a.workers and a.workers > 1:
        # 沙箱环境禁止多进程（multiprocessing 需要命名管道）；此处保留但会失败。
        # 并行请改用：多个后台进程各带 --shard i/n。
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(snapshot_one, c, a.freq, a.since, force=a.force): c
                    for c in codes}
            for i, f in enumerate(futs, 1):
                try:
                    r = f.result()
                except Exception as e:  # noqa: BLE001
                    r = {"code": futs[f], "status": f"error:{type(e).__name__}"}
                results.append(r)
                if i % 25 == 0 or i == len(codes):
                    got = sum(1 for x in results if x["status"] in ("cached", "fetched"))
                    print(f"[{i}/{len(codes)}] ok={got}", flush=True)
    else:
        for i, code in enumerate(codes, 1):
            r = snapshot_one(code, a.freq, a.since, force=a.force)
            results.append(r)
            if i % 20 == 0 or i == len(codes):
                got = sum(1 for x in results if x["status"] in ("cached", "fetched"))
                print(f"[{i}/{len(codes)}] {code} {r['status']} ok={got}", flush=True)
    got = sum(1 for r in results if r["status"] in ("cached", "fetched"))
    ok = sum(1 for r in results if r["status"] in ("cached", "fetched"))
    print(f"done: {got}/{len(codes)} available -> {SNAPSHOT_DIR / a.freq}")
    if ok < len(codes):
        bad = [r for r in results if r["status"] not in ("cached", "fetched")]
        print(f"unavailable {len(bad)}: {[r['code'] + ':' + r['status'] for r in bad[:20]]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
