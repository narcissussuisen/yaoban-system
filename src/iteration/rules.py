"""候选规则注册表：把「可量化卡片」编码为**确定性信号表达式**（无 LLM 语义判断）。

实现要点
  1. 全部用 numpy 向量化（滚动窗口靠 O(1) 滑窗），单标的毫秒级 —— 全市场扫描才可行。
  2. `pattern_*` 只算「形态」，与阈值参数**无关**，故每 (规则, 标的) 只需算一次并缓存；
     `apply_*` 再按参数做阈值门控。这样参数网格从 O(变体数 × 全市场) 降到 O(1 × 全市场)。
  3. 参数不属于 config 时（无 config_paths 映射）不得进入自动生效路径，由 gate 拦截。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# 向量化工具
# --------------------------------------------------------------------------

def lead_roll_max(a: np.ndarray, w: int) -> np.ndarray:
    """升序数组的「前 w 根最大值」（含慢启动，前 w-1 根用逐渐变长的窗口）。"""
    out = np.full(a.shape, np.nan)
    dq: list[int] = []
    for i in range(a.shape[0]):
        while dq and dq[0] < i - w:
            dq.pop(0)
        while dq and a[dq[-1]] <= a[i]:
            dq.pop()
        dq.append(i)
        out[i] = a[dq[0]]
    return out


def lead_roll_min(a: np.ndarray, w: int) -> np.ndarray:
    return -lead_roll_max(-a, w)


def roll_mean(a: np.ndarray, w: int) -> np.ndarray:
    s = np.concatenate(([0.0], np.nancumsum(a)))
    out = np.full(a.shape, np.nan)
    if a.shape[0] >= w:
        out[w - 1:] = (s[w:] - s[:-w]) / w
    return out


def limit_up_pct(code: str) -> float:
    """涨停幅度（确定性口径）：创业板/科创板 20%，北交所 30%，其余 10%。"""
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("4", "8", "92")):
        return 30.0
    return 10.0


def limit_up_price(prev_close: float, code: str) -> float:
    return round(prev_close * (1.0 + limit_up_pct(code) / 100.0), 2)


def bars(df: pd.DataFrame) -> dict[str, np.ndarray]:
    return {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}


def limit_up_mask(b: dict, code: str) -> np.ndarray:
    c = b["close"]
    prev = np.concatenate(([np.nan], c[:-1]))
    lu = np.round(prev * (1.0 + limit_up_pct(code) / 100.0), 2)
    m = np.abs(c - lu) < 0.005
    m[0] = False
    return m


# --------------------------------------------------------------------------
# 形态层（与阈值参数无关）
# --------------------------------------------------------------------------

def pattern_huigui(df: pd.DataFrame, code: str, *, max_k: int = 7) -> dict:
    """上升回档形态量：回撤幅度（相对前 max_k 根内峰值根的最高价）、量能比。"""
    b = bars(df)
    c, h, v = b["close"], b["high"], b["volume"]
    n = len(c)
    peak20 = lead_roll_max(h, 20)
    retr = np.full(n, np.nan)          # 相对峰值根的最高价回撤 %
    volr = np.full(n, np.nan)          # (峰值根之后到 i 的均量) / 峰值根量
    kbest = np.zeros(n, dtype=int)     # 峰值根距今日数（1..max_k）
    ma20 = roll_mean(c, 20)
    for i in range(9, n):
        best_j, best_h = -1, -1.0
        for j in range(max(0, i - max_k), i):
            if h[j] > best_h:
                best_h, best_j = h[j], j
        if best_j < 0 or best_h <= 0:
            continue
        retr[i] = (best_h - c[i]) / best_h * 100.0
        kbest[i] = i - best_j
        seg = v[best_j + 1:i + 1]
        if len(seg) and v[best_j] > 0:
            volr[i] = float(seg.mean()) / float(v[best_j])
    return {"retr": retr, "volr": volr, "kbest": kbest, "ma20": ma20, "peak20": peak20,
            "prev_close": np.concatenate(([np.nan], c[:-1]))}


def pattern_zthuicai(df: pd.DataFrame, code: str, *, vol_base_ma: int = 5,
                     zt_max_lookback: int = 8) -> dict:
    """涨停回踩形态量：最近一次涨停柱的位置、量能倍数、回踩期破位与量能比。

    量能比给两种口径（均可在门控层取用，便于对比「缩量的比较基准」）：
      * volr_peak  = 回踩期均量 / 涨停柱当日量     —— 原实现口径（实测偏严，见 tools/_diag_zthuicai.py）
      * volr_pre   = 回踩期均量 / 涨停前 vol_base_ma 日均量 —— 候选口径
      注：2026-09-11 实测表明**两种口径都无法表达「缩量回踩」**——真实样本（茶花 1.07、
      山东玻纤 1.19 相对涨停柱）在回踩期反而是放量的。故缩量条件默认**不启用**，
      缩量只作为可选过滤器保留，由影子回归判定是否有增量价值。
    """
    b = bars(df)
    c, l, v = b["close"], b["low"], b["volume"]
    n = len(c)
    zt = limit_up_mask(b, code)
    vbase = roll_mean(v, vol_base_ma)
    vmult = np.where(vbase > 0, v / np.where(vbase > 0, vbase, 1.0), np.nan)
    ma5, ma20 = roll_mean(c, 5), roll_mean(c, 20)

    zt_idx = np.full(n, -1)
    zt_mult = np.full(n, np.nan)
    broke_low = np.zeros(n, dtype=bool)
    volr_peak = np.full(n, np.nan)
    volr_pre = np.full(n, np.nan)
    last = -1
    for i in range(n):
        if zt[i]:
            last = i
        zt_idx[i] = last
        if last < 0:
            continue
        zt_mult[i] = vmult[last]
        if i > last:
            broke_low[i] = bool(l[last + 1:i + 1].min() < l[last])
            seg = v[last + 1:i + 1]
            if v[last] > 0:
                volr_peak[i] = float(seg.mean()) / float(v[last])
            pre = v[max(0, last - vol_base_ma):last]
            if len(pre) and float(pre.mean()) > 0:
                volr_pre[i] = float(seg.mean()) / float(pre.mean())
    return {"zt_idx": zt_idx, "zt_mult": zt_mult, "broke_low": broke_low,
            "volr_peak": volr_peak, "volr_pre": volr_pre,
            "ma5": ma5, "ma20": ma20,
            "prev_close": np.concatenate(([np.nan], c[:-1]))}


def pattern_fanbao(df: pd.DataFrame, code: str) -> dict:
    """趋势反包形态量：前阴后阳包住 + 距参照均线的接近度（对 5/10/20 各算一份）。"""
    b = bars(df)
    o, c, l, v = b["open"], b["close"], b["low"], b["volume"]
    n = len(c)
    prev_bear = np.concatenate(([False], c[:-1] < o[:-1]))
    cur_bull = c > o
    engulf = (c >= np.concatenate(([np.nan], c[:-1]))) & (o <= np.concatenate(([np.nan], o[:-1])))
    vma5 = roll_mean(v, 5)
    vexp = np.where(np.concatenate(([np.nan], vma5[:-1])) > 0,
                    v / np.concatenate(([np.nan], vma5[:-1])), np.nan)
    near: dict[int, np.ndarray] = {}
    rising: dict[int, np.ndarray] = {}
    for w in (5, 10, 20):
        ma = roll_mean(c, w)
        m = np.where(ma > 0, ma, np.nan)
        a = np.abs(np.concatenate(([np.nan], l[:-1])) - m) / m
        bb = np.abs(l - m) / m
        cc = np.abs(np.concatenate(([np.nan], c[:-1])) - m) / m
        near[w] = np.fmin(np.fmin(a, bb), cc)
        rising[w] = ma > np.concatenate(([np.nan], ma[:-1]))
    core = prev_bear & cur_bull & engulf
    core[0] = False
    return {"core": core, "vexp": vexp, "near": near, "rising": rising,
            "ma": {w: roll_mean(c, w) for w in (5, 10, 20)}}


def pattern_exit_ma(df: pd.DataFrame, code: str) -> dict:
    b = bars(df)
    c, v = b["close"], b["volume"]
    vma5 = roll_mean(v, 5)
    vrat = np.where(np.concatenate(([np.nan], vma5[:-1])) > 0,
                    v / np.concatenate(([np.nan], vma5[:-1])), np.nan)
    return {"ma": {w: roll_mean(c, w) for w in (5, 10, 20)},
            "low": b["low"], "close": c, "vol_ratio": vrat}


# --------------------------------------------------------------------------
# 门控层（阈值参数在这里生效）
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 对外信号接口（形态 + 门控）
# --------------------------------------------------------------------------

_CACHE: dict = {}


def _pat(name: str, df: pd.DataFrame, code: str, **kw):
    # 缓存键必须含数据身份（id + 行数）：仅按 code 缓存会在同一进程内切换不同 DataFrame 时命中脏数据。
    key = (name, code, id(df), len(df), tuple(sorted(kw.items())))
    hit = _CACHE.get(key)
    if hit is None:
        fn = {"huigui": pattern_huigui, "zthuicai": pattern_zthuicai,
              "fanbao": pattern_fanbao, "exit_ma": pattern_exit_ma}[name]
        hit = fn(df, code, **kw) if kw else fn(df, code)
        _CACHE[key] = hit
    return hit


def clear_cache() -> None:
    _CACHE.clear()


def sig_huigui(df: pd.DataFrame, code: str, *, pullback_days_min: int = 1,
               pullback_days_max: int = 5, pullback_pct_min: float = 1.0,
               pullback_pct_max: float = 15.0, vol_ratio_max: float = 0.9,
               above_ma20: bool = True, entry_gain_max: float | None = 3.0,
               ma_long: int = 20) -> pd.Series:
    """上升回档低吸（KC-0002 / KC-0008）。回档天数由 pullback_days_min/max 门控。"""
    pmax = max(int(pullback_days_max), int(pullback_days_min))
    p = _pat("huigui", df, code, max_k=pmax)
    c = df["close"].to_numpy(dtype=float)
    retr, volr, ma20, kbest = p["retr"], p["volr"], p["ma20"], p["kbest"]
    with np.errstate(invalid="ignore"):
        ok = (retr >= pullback_pct_min) & (retr <= pullback_pct_max) & (volr <= vol_ratio_max)
        ok &= (kbest >= pullback_days_min) & (kbest <= pmax)
        if above_ma20:
            ok &= c > ma20
        if entry_gain_max is not None:
            pc = p["prev_close"]
            gain = np.where(pc > 0, (c / np.where(pc > 0, pc, 1.0) - 1.0) * 100.0, np.inf)
            ok &= gain <= entry_gain_max
    ok = np.nan_to_num(ok, nan=0.0).astype(bool)
    ok[: ma_long + 2] = False
    return pd.Series(ok, index=df.index)


def sig_zthuicai(df: pd.DataFrame, code: str, *, pullback_days_min: int = 1,
                 pullback_days_max: int = 5, hold_ma: int = 5, above_ma20: bool = True,
                 not_break_zt_low: bool = True, vol_ratio_max: float | None = None,
                 vol_ratio_basis: str = "peak", vol_base_ma: int = 5,
                 vol_mult_min: float = 2.0,
                 entry_gain_max: float | None = None,
                 require_up_bar: bool = True) -> pd.Series:
    """涨停回踩低吸（KC-0004）：放量涨停 → 回踩不破位 → 再上攻。

    2026-09-11 口径修复（依据 tools/_diag_zthuicai.py 实测）：
      1. **缩量不再是必要条件**——真实样本回踩期相对涨停柱反而放量（茶花 1.07、山东玻纤 1.19），
         「缩量回踩」的字面口径无法覆盖任何真实交易。`vol_ratio_max=None` 即关闭该过滤器。
      2. 回踩日数默认 1~5（原 2~7 偏长，会把「涨停后一周仍在磨」的样本算进来）。
      3. 保留 `vol_ratio_basis` 双口径供影子回归对比（peak=相对涨停柱量，pre=相对涨停前均量）。
    """
    pmax = max(int(pullback_days_max), 1)
    p = _pat("zthuicai", df, code, vol_base_ma=vol_base_ma)
    b = bars(df)
    c, o = b["close"], b["open"]
    n = len(c)
    ok = np.zeros(n, dtype=bool)
    # 约定：vol_ratio_max <= 0 代表「不启用缩量过滤器」（config 用 0.0 表达关闭，避免用 None 破坏 TOML 语义）
    if vol_ratio_max is not None and float(vol_ratio_max) <= 0:
        vol_ratio_max = None
    volr = p["volr_peak"] if vol_ratio_basis == "peak" else p["volr_pre"]
    for i in range(25, n):
        if require_up_bar and not (c[i] > o[i]):
            continue
        if entry_gain_max is not None:
            pc = c[i - 1]
            if pc <= 0 or (c[i] / pc - 1.0) * 100.0 > entry_gain_max:
                continue
        z = p["zt_idx"][i]
        if z < 0 or not (pullback_days_min <= i - z <= pmax):
            continue
        if not (p["zt_mult"][i] >= vol_mult_min):
            continue
        if above_ma20 and not (c[i] > p["ma20"][i]):
            continue
        if not (c[i] > p["ma5"][i]):
            continue
        if not_break_zt_low and p["broke_low"][i]:
            continue
        if vol_ratio_max is not None and not (volr[i] <= vol_ratio_max):
            continue
        ok[i] = True
    return pd.Series(ok, index=df.index)


def sig_fanbao(df: pd.DataFrame, code: str, *, ma_ref: int = 10,
               ma_tol: float = 0.02, vol_expand: float | None = 1.0,
               require_ma_rising: bool = True) -> pd.Series:
    """趋势反包（KC-0003）：回踩均线企稳 + 前阴后阳反包 + 放量。

    注：`ma_tol` 是「企稳」的显式口径（前一日低点/当日低点/前收 距参照均线的最近相对距离）。
    选手实操中常见「涨停/大阳脱离均线」，故该容差需由影子回归校准而非拍定。
    """
    p = _pat("fanbao", df, code)
    with np.errstate(invalid="ignore"):
        ok = p["core"] & (p["near"][ma_ref] <= ma_tol)
        if require_ma_rising:
            ok &= p["rising"][ma_ref]
        if vol_expand is not None:
            ok &= p["vexp"] >= vol_expand
    return pd.Series(ok & np.isfinite(p["near"][ma_ref]), index=df.index)


def exit_ma_break(df: pd.DataFrame, code: str, *, ma_n: int = 10,
                  confirm_on_close: bool = True, need_prev_above: bool = False,
                  vol_confirm: float | None = None) -> pd.Series:
    """趋势线破位离场（KC-0011 / KC-0014）。"""
    p = _pat("exit_ma", df, code)
    ma = p["ma"][ma_n]
    ref = p["close"] if confirm_on_close else p["low"]
    with np.errstate(invalid="ignore"):
        ok = ref < ma
        if need_prev_above:
            prev_ma = np.concatenate(([np.nan], ma[:-1]))
            ok &= np.concatenate(([np.nan], p["close"][:-1])) >= prev_ma
        if vol_confirm is not None:
            ok &= p["vol_ratio"] >= vol_confirm
    ok &= np.isfinite(ma)
    return pd.Series(ok, index=df.index)


def exit_short_weak(df: pd.DataFrame, code: str, *, exit_gain_max: float = 3.0) -> pd.Series:
    """短线隔天不强就走（KC-0012）：开盘不高于昨收 + 涨幅不足 + 未涨停。"""
    b = bars(df)
    c, o = b["close"], b["open"]
    pc = np.concatenate(([np.nan], c[:-1]))
    with np.errstate(invalid="ignore"):
        gain = np.where(pc > 0, (c / np.where(pc > 0, pc, 1.0) - 1.0) * 100.0, np.nan)
        ok = (o <= pc) & (gain < exit_gain_max) & (~limit_up_mask(b, code))
    ok &= np.isfinite(pc)
    ok[:2] = False
    return pd.Series(ok, index=df.index)


def flag_touch_no_seal(df: pd.DataFrame, code: str) -> pd.Series:
    """攻击不足（KC-0022）：当日最高触及涨停价但收盘未封板。"""
    b = bars(df)
    c, h = b["close"], b["high"]
    prev = np.concatenate(([np.nan], c[:-1]))
    lu = np.round(prev * (1.0 + limit_up_pct(code) / 100.0), 2)
    ok = (np.abs(h - lu) < 0.005) & (np.abs(c - lu) >= 0.005)
    ok &= np.isfinite(lu)
    return pd.Series(ok, index=df.index)


def flag_akill(df: pd.DataFrame, code: str, *, lookback: int = 5,
               drop_pct: float = 8.0) -> pd.Series:
    """A 杀形态代理（KC-0020 未量化 → 显式代理口径，待人工确认）。"""
    b = bars(df)
    c = b["close"]
    hi = lead_roll_max(c, lookback)
    prev = np.concatenate(([np.nan], c[:-1]))
    lu = np.round(prev * (1.0 + limit_up_pct(code) / 100.0), 2)
    zt = np.abs(c - lu) < 0.005
    ok = np.zeros(len(c), dtype=bool)
    for i in range(10, len(c)):
        if not zt[max(0, i - lookback):i + 1].any():
            continue
        h = np.nanmax(c[max(0, i - lookback):i + 1])
        if h > 0 and (h - c[i]) / h * 100.0 >= drop_pct:
            ok[i] = True
    return pd.Series(ok, index=df.index)


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------

RULES: dict[str, dict] = {
    "huigui": dict(
        kind="entry", fn=sig_huigui, card_ids=["KC-0002", "KC-0008"],
        label="上升回档低吸",
        grid={"vol_ratio_max": [0.7, 0.9, 1.1, 1.5],
              "pullback_days_max": [3, 5, 7],
              "entry_gain_max": [3.0, 5.0, None]},
        config_paths={"vol_ratio_max": "strategy.huigui.live.volume_ratio_max",
                      "pullback_days_max": "strategy.huigui.live.pullback_days_max",
                      "pullback_days_min": "strategy.huigui.live.pullback_days_min",
                      "pullback_pct_min": "strategy.huigui.live.pullback_pct_min",
                      "pullback_pct_max": "strategy.huigui.live.pullback_pct_max"},
        grid_params=("vol_ratio_max", "pullback_days_max", "entry_gain_max"),
    ),
    "zthuicai": dict(
        kind="entry", fn=sig_zthuicai, card_ids=["KC-0004", "KC-0008"],
        label="涨停回踩低吸",
        grid={"vol_mult_min": [1.0, 1.2, 1.5, 2.0],
              "vol_ratio_max": [None, 1.2, 1.5, 2.0],
              "pullback_days_max": [3, 5, 7],
              "entry_gain_max": [3.0, 5.0, None],
              "not_break_zt_low": [True, False],
              "vol_ratio_basis": ["peak", "pre"]},
        config_paths={"vol_ratio_max": "strategy.zt_huicai.volume_shrink_ratio",
                      "vol_mult_min": "strategy.zt_huicai.volume_surge_min",
                      "vol_base_ma": "strategy.zt_huicai.volume_surge_base_ma",
                      "vol_ratio_basis": "strategy.zt_huicai.volume_shrink_basis",
                      "pullback_days_min": "strategy.zt_huicai.pullback_days_min",
                      "pullback_days_max": "strategy.zt_huicai.pullback_days_max",
                      "hold_ma": "strategy.zt_huicai.hold_ma"},
        grid_params=("vol_mult_min", "vol_ratio_max", "pullback_days_max",
                     "entry_gain_max", "not_break_zt_low", "vol_ratio_basis"),
    ),
    "fanbao": dict(
        kind="entry", fn=sig_fanbao, card_ids=["KC-0003"],
        label="趋势反包",
        grid={"ma_ref": [5, 10, 20], "ma_tol": [0.02, 0.06, 0.12],
              "vol_expand": [0.8, 1.0, 1.3, None]},
        config_paths={},     # 无 config 归属 → 恒不进自动生效路径
        grid_params=("ma_ref", "ma_tol", "vol_expand"),
    ),
    "exit_ma10": dict(
        kind="exit", fn=exit_ma_break, card_ids=["KC-0011", "KC-0014", "KC-0016"],
        label="趋势线破位离场",
        grid={"ma_n": [5, 10, 20], "vol_confirm": [None, 1.0, 1.2]},
        config_paths={},
        grid_params=("ma_n", "vol_confirm"),
    ),
    "exit_short": dict(
        kind="exit", fn=exit_short_weak, card_ids=["KC-0012", "KC-0029"],
        label="短线隔天不强就走",
        grid={"exit_gain_max": [0.0, 3.0, 5.0]},
        config_paths={},
        grid_params=("exit_gain_max",),
    ),
}


def rule_params(name: str, config: dict) -> dict:
    """取规则在当前 config 下的实际参数（把「现状」并入候选集）。"""
    spec = RULES[name]
    out: dict = {}
    for p, path in spec.get("config_paths", {}).items():
        cur = config
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur is not None:
            out[p] = cur
    return out


def variants(name: str, config: dict) -> list[dict]:
    """展开候选参数组合；当前配置值并入每个可调参数的候选集（「不改」也是候选之一）。"""
    import itertools

    spec = RULES[name]
    base = rule_params(name, config)
    axes: dict[str, list] = {}
    for p in spec["grid_params"]:
        vals = list(spec["grid"].get(p, []))
        if p in base:
            vals.append(base[p])
        seen, uniq = set(), []
        for v in vals:
            key = repr(v)
            if key not in seen:
                seen.add(key)
                uniq.append(v)
        axes[p] = uniq
    keys = list(axes)
    combos = []
    for combo in itertools.product(*(axes[k] for k in keys)):
        merged = dict(base)
        merged.update(dict(zip(keys, combo)))
        merged["_axis"] = dict(zip(keys, combo))
        combos.append(merged)
    return combos


def single_axis_variants(name: str, config: dict) -> list[dict]:
    """单变量候选集：基准配置 + 每次只改一个参数。

    影子回归用这套而不是全笛卡尔网格，原因：
      ① 可审计——每个提案只对应一个参数变化，归因明确；
      ② 防过拟合——全网格最优常是多参数联合拟合噪声，单轴对比更保守；
      ③ 成本低——组合数从 Π(轴长) 降到 Σ(轴长)。
    """
    spec = RULES[name]
    base = rule_params(name, config)
    base_axis = {p: base[p] for p in spec["grid_params"] if p in base}
    b0 = dict(base)
    b0["_axis"] = dict(base_axis)
    out: list[dict] = [b0]
    for p in spec["grid_params"]:
        vals = list(spec["grid"].get(p, []))
        if p in base:
            vals.append(base[p])
        seen, uniq = set(), []
        for v in vals:
            key = repr(v)
            if key not in seen:
                seen.add(key)
                uniq.append(v)
        for v in uniq:
            if p in base and v == base[p]:
                continue
            m = dict(base)
            m[p] = v
            ax = dict(base_axis)
            ax[p] = v
            m["_axis"] = ax
            out.append(m)
    # 去重（不同轴可能产生同一 _axis）
    seen2, uniq2 = set(), []
    for m in out:
        k = tuple(sorted((kk, repr(vv)) for kk, vv in m["_axis"].items()))
        if k in seen2:
            continue
        seen2.add(k)
        uniq2.append(m)
    return uniq2
