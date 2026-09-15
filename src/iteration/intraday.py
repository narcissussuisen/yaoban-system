"""分时（分钟线）规则层：确定性复现「分时跌破均价线 → 不玩」等分时纪律。

数据来源：`data/minute/<freq>/<code>.parquet`（由 tools/snapshot_minute.py 落盘的本地快照）。
无快照时返回明确原因，绝不猜测——失败可见优先于静默降级。

口径定义（全部显式参数化，便于影子回归校准）：
  * 分时均价线 = 当日累计成交额 / 当日累计成交量（VWAP，自 09:30 起累计）——即行情软件「均价线」。
  * 「第一波回落跌破」= 决策窗口内首次出现的「连续 N 分钟收盘 < VWAP ×(1−tol)」。
  * 决策窗口默认 09:30–10:30（选手候选发布后立即判定；实测候选 09:35-10:52 发布）。
"""
from __future__ import annotations

import datetime
import json
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SNAPSHOT = ROOT / "data" / "minute"
# R2.4（2026-09-12）：路径由 src/data/minute_paths.py 统一解析 ——
# 环境变量 EVOALPHA_MINUTE_SNAPSHOT 可覆盖根目录（便于快照整体迁到 F 盘而无需改代码）。
try:
    from data.minute_paths import snapshot_root as _snapshot_root
    SNAPSHOT = _snapshot_root()
except Exception:  # noqa: BLE001 - src 不可导入时退回本地默认
    pass


def snapshot_path(code: str, freq: str = "1m") -> pathlib.Path:
    return SNAPSHOT / freq / f"{code}.parquet"


_SNAP_CACHE: dict[tuple[str, str], pd.DataFrame | None] = {}


def load_snapshot(code: str, freq: str = "1m") -> pd.DataFrame | None:
    """读取分钟快照（进程内缓存；同一批次会对同一标的反复按日切片）。"""
    key = (code, freq)
    if key in _SNAP_CACHE:
        return _SNAP_CACHE[key]
    fp = snapshot_path(code, freq)
    df = None
    if fp.exists():
        try:
            df = pd.read_parquet(fp)
            df["date"] = df["ts"].str[:10]
            df["hm"] = df["ts"].str[11:16]
        except Exception:  # noqa: BLE001 - 单文件损坏不阻塞批次
            df = None
    _SNAP_CACHE[key] = df
    return df


def clear_snapshot_cache() -> None:
    _SNAP_CACHE.clear()


def intraday_vwap(day: pd.DataFrame) -> np.ndarray:
    """当日累计 VWAP。amount 缺失/为 0 时退回典型价加权（并保持可追溯）。"""
    v = day["volume"].to_numpy(dtype=float)
    a = day["amount"].to_numpy(dtype=float)
    if not np.isfinite(a).any() or float(np.nansum(a)) <= 0:
        typ = (day["high"].to_numpy(float) + day["low"].to_numpy(float)
               + day["close"].to_numpy(float)) / 3.0
        a = typ * v
    cv = np.cumsum(np.nan_to_num(v))
    ca = np.cumsum(np.nan_to_num(a))
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = np.where(cv > 0, ca / np.where(cv > 0, cv, 1.0), np.nan)
    return vwap


def first_break_below_vwap(day: pd.DataFrame, *, window_start: str | None = None,
                           window_end: str = "10:30",
                           confirm_minutes: int = 3, tol: float = 0.0) -> dict:
    """决策窗口内是否出现「第一波回落跌破均价线」。

    `day` 可为全天分钟线；`window_start` 给定时先在全日线上算好累计 VWAP，
    再在窗口内判定——这样窗口内的均价线仍包含开盘以来的成交，符合行情软件口径。

    返回 {veto, break_ts, below_minutes, window_minutes, reason}
    veto=True 表示按纪律「不玩」。
    """
    if day.empty:
        return {"veto": False, "break_ts": None, "below_minutes": 0,
                "window_minutes": 0, "reason": "无分钟数据"}
    full = day.reset_index(drop=True).copy()
    full["vwap"] = intraday_vwap(full)
    win = full[full["hm"] <= window_end]
    if window_start:
        win = win[win["hm"] >= window_start]
    if len(win) < confirm_minutes + 1:
        return {"veto": False, "break_ts": None, "below_minutes": 0,
                "window_minutes": int(len(win)), "reason": "窗口内分钟数不足"}
    win = win.reset_index(drop=True)
    vwap = win["vwap"].to_numpy(dtype=float)
    close = win["close"].to_numpy(dtype=float)
    below = close < vwap * (1.0 - tol)
    below &= np.isfinite(vwap)
    run, first_idx = 0, None
    for i, b in enumerate(below):
        run = run + 1 if b else 0
        if run >= confirm_minutes:
            first_idx = i - confirm_minutes + 1
            break
    if first_idx is None:
        return {"veto": False, "break_ts": None, "below_minutes": int(below.sum()),
                "window_minutes": int(len(win)), "reason": "窗口内未出现连续跌破"}
    return {"veto": True, "break_ts": str(win["ts"].iloc[first_idx]),
            "below_minutes": int(below.sum()), "window_minutes": int(len(win)),
            "reason": f"连续 {confirm_minutes} 分钟收盘低于均价线"}


def vwap_stats(day: pd.DataFrame, *, window_start: str | None = None,
               window_end: str = "10:30", tol: float = 0.0) -> dict:
    """窗口内均价线诊断量：低于均价线的比例、最深偏离、首次跌破分钟数。"""
    if day.empty:
        return {"error": "no_data", "n": 0}
    full = day.reset_index(drop=True).copy()
    full["vwap"] = intraday_vwap(full)
    win = full[full["hm"] <= window_end]
    if window_start:
        win = win[win["hm"] >= window_start]
    if len(win) < 5:
        return {"error": "insufficient_minutes", "n": int(len(win))}
    win = win.reset_index(drop=True)
    vwap = win["vwap"].to_numpy(dtype=float)
    close = win["close"].to_numpy(dtype=float)
    ok = np.isfinite(vwap)
    if not ok.any():
        return {"error": "vwap_unavailable", "n": int(len(win))}
    below = (close < vwap * (1.0 - tol)) & ok
    dev = np.where(ok, (close / np.where(ok, vwap, np.nan) - 1.0) * 100.0, np.nan)
    first = None
    for i, b in enumerate(below):
        if b:
            first = str(win["ts"].iloc[i])[11:16]
            break
    return {
        "n": int(len(win)),
        "below_min": int(below.sum()),
        "below_frac": round(float(below.mean()), 3),
        "min_dev_pct": round(float(np.nanmin(dev)), 3) if np.isfinite(dev).any() else None,
        "first_below_hm": first,
        "mean_dev_pct": round(float(np.nanmean(dev)), 3) if np.isfinite(dev).any() else None,
    }


def eval_entry_vwap(code: str, date: str, *, freq: str = "1m", **kw) -> dict:
    """对某个「信号日」评估该标的当日分时是否触发否决（供全市场反事实检验用）。"""
    df = load_snapshot(code, freq)
    if df is None:
        return {"code": code, "date": date, "error": "no_snapshot"}
    day = df[df["date"] == date]
    if day.empty:
        return {"code": code, "date": date, "error": "no_data_for_date"}
    r = first_break_below_vwap(day, **kw)
    s = vwap_stats(day, window_start=kw.get("window_start"),
                   window_end=kw.get("window_end", "10:30"), tol=kw.get("tol", 0.0))
    r.update({"code": code, "date": date, **{f"stat_{k}": v for k, v in s.items()}})
    return r


def eval_rule_on_case(code: str, date: str, *, freq: str = "1m", **kw) -> dict:
    df = load_snapshot(code, freq)
    if df is None:
        return {"code": code, "date": date, "error": "no_snapshot"}
    day = df[df["date"] == date]
    if day.empty:
        return {"code": code, "date": date, "error": "no_data_for_date"}
    r = first_break_below_vwap(day, **kw)
    r.update({"code": code, "date": date,
              "day_first": str(day["ts"].iloc[0]), "day_last": str(day["ts"].iloc[-1])})
    return r


# ---------------------------------------------------------------- 冻结口径
# 由 18 条可追溯决策（7 个交易日）的 108 组网格搜索选出：准确率 94.4%（17/18），
# 6/6 明示否决全部命中、0 漏否决、1 误否决（协鑫能科 8/31）。
# **冻结后不再调参**：后续用于全市场反事实检验时必须沿用此口径，否则即为样本内择优。
FROZEN_VWAP_PARAMS = {"window_start": "09:35", "window_end": "10:00",
                      "confirm_minutes": 5, "tol": 0.003}


def evaluate_frozen(**kw) -> dict:
    """按冻结口径评估全部决策案例。"""
    p = dict(FROZEN_VWAP_PARAMS)
    p.update(kw)
    return evaluate_decisions(**p)


# 案例归因：选手的「看/买」决策（来自 选手学习资料 文字资料，逐条可追溯）
#   action 三值：
#     BUY       选手实际买入（规则不应否决）
#     VETO      选手**明示**因分时走弱而放弃（规则应否决）
#     UNKNOWN   未执行但未说明原因 → 不计入评分（避免把「资金已满/大盘闸门」误判为分时否决）
DECISION_CASES = {
    "2026-08-31": [
        ("600186", "莲花控股", "VETO", "选手点评：分时回落跌破分时均价线，短线走势不强，不考虑玩"),
        ("002015", "协鑫能科", "BUY", "技术形态好看，回踩 10 日均线企稳，分时量价配合"),
    ],
    "2026-09-02": [
        ("603615", "茶花股份", "BUY", "涨停回踩低吸"),
        ("603118", "共进股份", "BUY", "8/31 14:18 建仓，超节点概念"),
    ],
    "2026-09-03": [
        ("003018", "金富科技", "BUY", "上升回档精选，当日涨停"),
        ("605118", "力鼎光电", "UNKNOWN", "分时没什么量（量能滤据，非均价线）→ 未执行"),
        ("603912", "佳力图", "UNKNOWN", "候选未执行，未说明原因"),
        ("300192", "科德教育", "UNKNOWN", "候选未执行，未说明原因"),
    ],
    "2026-09-04": [
        ("000892", "欢瑞世纪", "BUY", "候选池执行 3/5"),
        ("000628", "高新发展", "BUY", "候选池执行 3/5"),
        ("002015", "协鑫能科", "BUY", "候选池执行 3/5"),
        ("000626", "远大控股", "UNKNOWN", "未执行；资料推断为分时走弱被过滤（推断，非原话）"),
        ("002279", "久其软件", "UNKNOWN", "未执行；资料推断为分时走弱被过滤（推断，非原话）"),
    ],
    "2026-09-07": [
        ("002708", "光洋股份", "BUY", "趋势反包战法"),
        ("603258", "东材科技", "BUY", "上升回档战法"),
        ("000998", "隆平高科", "UNKNOWN", "未执行，未说明原因（事后 +3.09%）"),
        ("000912", "泸天化", "UNKNOWN", "未执行；事后两连板。**规则反面样本**，因原因未明故不计分"),
    ],
    "2026-09-09": [
        ("603162", "海通发展", "BUY", "唯一未被警示 → 买入，当日涨停 +9.98%"),
        ("603270", "金帝股份", "VETO", "分时跌破均价线→不玩（后收 +2.30%）"),
        ("002194", "武汉凡谷", "VETO", "分时跌破均价线→不玩（后收 −2.26%）"),
        ("003018", "金富科技", "VETO", "分时跌破均价线→不玩（后收 −1.55%）"),
    ],
    "2026-09-10": [
        ("605006", "山东玻纤", "BUY", "①最优档：有量+一步步向上 → 买入（当日 +6.00%，盘中触板）"),
        ("002886", "沃特股份", "BUY", "②中等档：无量欠缺力度 → 买入（当日 0.00%）"),
        ("603113", "金能科技", "VETO", "③弃：破均价线（当日 −5.71%）"),
        ("603093", "南华期货", "VETO", "③弃：破均价线（当日 +1.80%）"),
    ],
}


def evaluate_decisions(*, freq: str = "1m", only_scored: bool = True, **kw) -> dict:
    """在全部可追溯决策上评估分时规则。

    评分口径：只统计 BUY / VETO（选手明示原因）两类；UNKNOWN 单列不计分。
    """
    rows, tp, fp, tn, fn, unknown = [], 0, 0, 0, 0, 0
    for date, cases in DECISION_CASES.items():
        for code, name, action, note in cases:
            r = eval_rule_on_case(code, date, freq=freq, **kw)
            r.update({"name": name, "action": action, "note": note})
            if "error" not in r:
                if action == "VETO" and r["veto"]:
                    tp += 1
                elif action == "VETO" and not r["veto"]:
                    fn += 1
                elif action == "BUY" and r["veto"]:
                    fp += 1
                elif action == "BUY":
                    tn += 1
                else:
                    unknown += 1
            rows.append(r)
    total = tp + fp + tn + fn
    return {
        "params": kw, "freq": freq,
        "scored": total, "total_evaluated": total, "unknown_excluded": unknown,
        "veto_correct": tp, "veto_missed": fn, "false_veto": fp, "buy_correct": tn,
        "veto_recall_pct": round(tp / (tp + fn) * 100.0, 1) if (tp + fn) else None,
        "buy_survival_pct": round(tn / (tn + fp) * 100.0, 1) if (tn + fp) else None,
        "accuracy_pct": round((tp + tn) / total * 100.0, 1) if total else None,
        "exact_match": (fp == 0 and fn == 0),
        "days": len(DECISION_CASES),
        "rows": rows,
    }


def run_grid() -> dict:
    """在显式参数网格上评估，报告哪一档口径最贴合选手实际决策（不作自动生效）。

    重点检验「候选发布窗口」假设：选手的检查时点是**候选发布后立即**，
    而非全天候——因此窗口起点应与发布时刻对齐（资料中 09:35~09:46）。
    """
    grid = []
    for window_start in (None, "09:35", "09:45"):
        for window_end in ("10:00", "10:30", "11:30"):
            for confirm in (1, 2, 3, 5):
                for tol in (0.0, 0.001, 0.003):
                    grid.append({"window_start": window_start, "window_end": window_end,
                                 "confirm_minutes": confirm, "tol": tol})
    results = [evaluate_decisions(**g) for g in grid]
    usable = [r for r in results if r["scored"] >= 10]
    exact = [r for r in usable if r["exact_match"]]
    def score(r):
        return (-(r["accuracy_pct"] or 0), r["false_veto"], r["veto_missed"])
    return {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "grid_size": len(grid),
        "exact_match_count": len(exact),
        "best": sorted(usable, key=score)[:5],
        "worst": sorted(usable, key=score)[-3:],
        "all": results,
    }


def coverage() -> dict:
    miss = []
    for date, cases in DECISION_CASES.items():
        for code, name, _a, _n in cases:
            if snapshot_path(code).exists():
                continue
            miss.append({"code": code, "name": name, "date": date})
    return {"snapshot_dir": str(SNAPSHOT / "1m"), "missing": miss,
            "missing_n": len(miss)}


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "eval"
    if cmd == "coverage":
        print(json.dumps(coverage(), ensure_ascii=False, indent=1))
    elif cmd == "grid":
        print(json.dumps(run_grid(), ensure_ascii=False, indent=1))
    else:
        kw: dict = {}
        for a in sys.argv[2:]:
            k, _, v = a.partition("=")
            if k == "window_end":
                kw[k] = v
            elif k in ("confirm_minutes",):
                kw[k] = int(v)
            else:
                kw[k] = float(v)
        print(json.dumps(evaluate_decisions(**kw), ensure_ascii=False, indent=1))
