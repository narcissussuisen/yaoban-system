"""案例回归测试：用视频已知结果验证策略检测器（复现率 ≥80% 为引擎正确）

逻辑:
  对 tests/cases.json 中每个可检测案例（huigui/zt_huicai/fanbao/xianren），
  在案例日期 ±5 个交易日内检测对应战法信号：
    - outcome=valid      → 期望出现信号
    - outcome=invalid    → 期望不出现信号（如万顺新材回调放量）
    - fen_shi/zuot/zhaban/chengjie/gaobiao → 需要分钟线或其他数据，跳过并注明
  复现率 = 判定正确数 / 可检测案例数

用法: python scripts/run_case_regression.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from data.store import Store  # noqa: E402
from core import strategies as S  # noqa: E402

WINDOW = 5  # 案例日期前后交易日窗口

PATTERN_DETECTOR = {
    "huigui": lambda df: S.detect_huigui(df, mode="live"),
    "zt_huicai": lambda df: S.detect_zt_huicai(df),
    "fanbao": lambda df: S.detect_fanbao(df),
    "xianren": lambda df: S.detect_xianren(df, with_confirm=False),
}


def nearest_trading_day(df: pd.DataFrame, target: str, max_calendar_days: int = 10) -> int | None:
    """案例日期可能是周末/节假日（视频发布日≠交易日）：在 ±max_calendar_days 内找最近交易日"""
    from datetime import date, timedelta

    try:
        t = date.fromisoformat(target)
    except ValueError:
        return None
    dates = pd.to_datetime(df["date"]).dt.date
    best, best_delta = None, None
    for d, idx in zip(dates, df.index):
        delta = abs((d - t).days)
        if delta <= max_calendar_days and (best_delta is None or delta < best_delta):
            best, best_delta = idx, delta
    return best
# 需要其他数据/无法用日线检测的模式
SKIP_PATTERNS = {"fen_shi": "需分钟线（分时买点）",
                 "zuot": "需分钟线（做T）",
                 "zhaban": "需分时/封单数据",
                 "chengjie": "需分时承接数据",
                 "gaobiao": "需涨停池连板数据"}


def rows_to_df(rows) -> pd.DataFrame:
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.DataFrame(rows, columns=cols[:len(rows[0])] if rows else cols)
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = df["date"].astype(str)
    return df.reset_index(drop=True)


def main():
    cases = json.loads((ROOT / "tests" / "cases.json").read_text(encoding="utf-8"))["cases"]
    store = Store()
    lines = ["# 案例回归报告（策略检测器 × 视频已知结果）", ""]
    correct, total, skipped = 0, 0, 0
    rows_out = []

    for case in cases:
        cid, pattern = case["id"], case["pattern"]
        if pattern in SKIP_PATTERNS:
            lines.append(f"- **{cid}** {case['name']}（{pattern}）：跳过 — {SKIP_PATTERNS[pattern]}")
            skipped += 1
            continue
        if case.get("outcome") == "reference":
            # 参考案例（叙述-数据差异 / 定性判断 / 图解）：检测结果展示但不计入评分
            db0 = store.get_stock(case["symbol"], start="2023-01-01")
            if db0:
                df0 = rows_to_df(db0)
                sig0 = PATTERN_DETECTOR[pattern](df0)
                dts = "、".join(df0.loc[sig0, "date"].tolist()[-5:]) if sig0.any() else "无"
                lines.append(f"- **{cid}** {case['name']}（{pattern}）：参考案例，检测到信号日 {dts or '无'} — 不计分")
            else:
                lines.append(f"- **{cid}** {case['name']}（{pattern}）：参考案例，无数据")
            skipped += 1
            continue
        detector = PATTERN_DETECTOR[pattern]
        db = store.get_stock(case["symbol"], start="2023-01-01")
        if not db:
            lines.append(f"- **{cid}** {case['name']}（{pattern}）：无数据，跳过")
            skipped += 1
            continue
        df = rows_to_df(db)
        sig = detector(df)
        sig_dates = set(df.loc[sig, "date"])
        # 案例日期定位：优先精确匹配，否则取 ±10 个日历日内最近交易日（视频发布日常为周末）
        pos = df.index[df["date"] == case["date"]]
        anchor = ""
        if pos.empty:
            near = nearest_trading_day(df, case["date"])
            if near is None:
                lines.append(f"- **{cid}** {case['name']}：案例日期 {case['date']} 附近无交易日，跳过")
                skipped += 1
                continue
            pos = df.index[[near]]
            anchor = f"（对齐到 {df['date'].iloc[near]}）"
        i = pos[0]
        lo_i, hi_i = max(0, i - WINDOW), min(len(df), i + WINDOW + 1)
        window_dates = set(df["date"].iloc[lo_i:hi_i])
        hit = bool(sig_dates & window_dates)
        expect = case["outcome"] == "valid"
        ok = (hit == expect)
        total += 1
        correct += int(ok)
        hit_str = "、".join(sorted(sig_dates & window_dates)) if hit else "无"
        lines.append(
            f"- **{cid}** {case['name']}（{S.STRATEGY_NAMES.get(pattern, pattern)}）："
            f"案例日 {case['date']}{anchor}，窗口内信号 {'✅ ' + hit_str if hit else '❌ 无'}，"
            f"期望 {'信号' if expect else '无信号'} → **{'✔ 复现' if ok else '✘ 未复现'}**"
        )
        rows_out.append({"id": cid, "symbol": case["symbol"], "name": case["name"],
                         "pattern": pattern, "date": case["date"], "hit": hit,
                         "expected": expect, "ok": ok})

    rate = correct / total * 100 if total else 0
    lines += ["", f"**可检测案例 {total} 个，复现 {correct} 个，复现率 {rate:.0f}%**"
              f"（目标 ≥80%；跳过 {skipped} 个需其他数据）", ""]
    if rate < 80:
        lines += ["> ⚠️ 复现率未达 80%，需排查：参数口径 / 数据复权 / 案例日期对齐。", ""]
    lines += ["## 未复现明细", ""]
    for r in rows_out:
        if not r["ok"]:
            lines.append(f"- {r['id']} {r['name']}：期望信号={'是' if r['expected'] else '否'}，实际={'有' if r['hit'] else '无'}")

    out = ROOT / "outputs"
    out.mkdir(exist_ok=True)
    p = out / "case_regression.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    store.close()
    print(f"\nRESULT: {correct}/{total} = {rate:.0f}% (skipped {skipped})")
    return 0 if rate >= 80 else 1


if __name__ == "__main__":
    sys.exit(main())
