# -*- coding: utf-8 -*-
"""确定分时反事实检验的快照范围：仅取「回测窗口内触发过信号」的标的。"""
import sys
import pathlib
import json

ROOT = pathlib.Path(__file__).resolve().parent.parent

# R2.4（2026-09-12）：分钟快照根目录由 src/data/minute_paths.py 统一解析
# （EVOALPHA_MINUTE_SNAPSHOT 可覆盖；便于快照整体迁到 F 盘而无需改代码）
try:
    from data.minute_paths import snapshot_root as _minute_root
    MINUTE_ROOT = _minute_root()
except Exception:  # noqa: BLE001
    MINUTE_ROOT = ROOT / "data" / "minute"

sys.path.insert(0, str(ROOT / "src"))
import tomllib  # noqa: E402
from iteration import market, rules, intraday  # noqa: E402

SINCE = "2026-08-20"
cfg = tomllib.loads((ROOT / "config" / "parameters.toml").read_text(encoding="utf-8"))
codes = market.available_codes(limit=1200)
hits: dict[str, list[str]] = {}
for name in ("huigui", "zthuicai", "fanbao"):
    params = rules.rule_params(name, cfg)
    fn = rules.RULES[name]["fn"]
    for code in codes:
        df = market.load_daily(code)
        if df is None or len(df) < 80:
            continue
        try:
            sig = fn(df, code, **params)
        except Exception:  # noqa: BLE001
            continue
        idx = [int(i) for i in sig.to_numpy().nonzero()[0]]
        ds = [str(df["date"].iloc[i]) for i in idx if str(df["date"].iloc[i]) >= SINCE]
        if ds:
            hits.setdefault(code, []).extend(ds)

already = {p.stem for p in (MINUTE_ROOT / "1m").glob("*.parquet")}
need = sorted(set(hits) - already)
print(json.dumps({
    "since": SINCE,
    "codes_scanned": len(codes),
    "codes_with_signal": len(hits),
    "already_snapshotted": len(already & set(hits)),
    "need_fetch": len(need),
    "signal_days_total": sum(len(v) for v in hits.values()),
}, ensure_ascii=False, indent=1))
(MINUTE_ROOT / "_fetch_plan.json").write_text(
    json.dumps({"since": SINCE, "need": need, "signal_days": hits},
               ensure_ascii=False, indent=1), encoding="utf-8")
print("plan ->", MINUTE_ROOT / "_fetch_plan.json")
