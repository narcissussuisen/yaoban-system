"""战法池（形态筛选层）—— 补齐 `docs/TRADER_ROADMAP_v2.md §1.2` 缺失的第三层。

【为什么有这个模块】
ROADMAP §1.2 约定的分层是：
    全市场 → 异动池(30-80) → **选手模式粗筛（形态+板块热度+连板气质）** → 确认队列(5-15)
           → 1m 分时三引擎 → 建仓
但生产 `scan_and_confirm.py` 里 **第三层实际不存在**：它把「异动池」直接当成了「确认队列」
（`pool.sort(key=lambda x: -x['chg'])` 后取前 8）。后果是选股层根本不是选手的战法，
而是「全市场涨幅前 8」—— 与选手的「上升回档回踩低吸」在**同一维度上方向相反**
（追高 vs 低吸），所以两边候选池**交集恒为空**（2026-09-14 实测：EvoAlpha top8 为
+9.29%~+7.10%，选手实买溢价 +2.61%）。

【设计原则】
1. **不重写检测逻辑**：直接复用 `core.strategies` 里已经过案例回归的 4 个检测器
   （`detect_huigui(mode='live')` / `detect_zt_huicai` / `detect_xianren` / `detect_fanbao`），
   参数一律来自 `config/parameters.toml`（单一事实源），本模块**不硬编码任何阈值**。
2. **日线级、T-1 收盘确定、全日不变** ⇒ 每个交易日只需构建一次，落盘缓存；
   盘中 scan 只读，不做重算（全市场 5000 只跑日线检测不可能每分钟重算）。
3. **信息不丢失**：返回每只票的 `pattern` / `sig_date` / `bars_since_sig` / 关键判据，
   供 scan 写入 `confirm_*.json` 与决策链留痕（本仓纪律：不得静默丢弃）。

【与 `src/decision_chain/engine.py:19` 的关系】
该处自述「③ 选真龙 | ⏳ stub | 复用 `plan_daily.detect_huigui_v5` 属后续增量
（**形态筛已有实现，需适配器**）」—— 本模块就是那个适配器。
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime

import pandas as pd

from core import strategies as S

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
POOL_DIR = ROOT / 'outputs' / 'patterns'
NAME_TABLE = ROOT / 'data' / 'stock_names_stocks.json'

# 战法名 → 检测器（全部来自 core.strategies；参数在其内部从 parameters.toml 读取）
# ⚠️ 2026-09-14：四个检测器**统一为双参 `(df, sym)`** —— `zt_huicai` 必须拿到 `sym`
#    才能按板块判涨停（此前硬编码 9.8% ⇒ 20cm/30cm 股的 +10% 被误判为涨停 = 假阳性）。
#    其余三个不吃 `sym`，签名统一只是为了让调用点（`:168`）不必分支。
#    ❗漏改这里 ⇒ `symbol` 传不进去 ⇒ 改了等于没改。
DETECTORS = {
    "huigui": lambda df, sym=None: S.detect_huigui(df, mode="live"),
    "zt_huicai": lambda df, sym=None: S.detect_zt_huicai(df, symbol=sym),
    "xianren": lambda df, sym=None: S.detect_xianren(df, with_confirm=True),
    "qu_shi_fanbao": lambda df, sym=None: S.detect_fanbao(df),
}
PATTERN_CN = S.STRATEGY_NAMES

# 检测器要求的最少日线根数（`strategies._require(df, 70/30/…)` 里最大是 70）
MIN_BARS = 76

DEFAULT_PATTERNS = ("huigui", "zt_huicai", "xianren", "qu_shi_fanbao")


def load_stock_names(path: pathlib.Path | None = None) -> dict:
    """读取个股名称表 `data/stock_names_stocks.json`。

    兼容两段式（`_meta` + `names`）与旧扁平结构 —— 与 `plan_daily.py` 的读法一致
    （此前「旧表 35086 条里 83% 是债/基金/指数」导致 `000004` 被写成「工业指数」而**ST 漏筛**）。
    读不到时返回 `{}`（不抛）：调用方按「无名称」处理，只计数不排除。
    """
    try:
        doc = json.loads((path or NAME_TABLE).read_text(encoding='utf-8'))
        names = doc.get('names', doc)
        return {str(k): str(v) for k, v in names.items()} if isinstance(names, dict) else {}
    except Exception:
        return {}


def write_pattern_pool(day: str, asof: str, lookback: int, patterns,
                       pool: list, stats: dict, name_map: dict,
                       out_dir: pathlib.Path | None = None,
                       path: pathlib.Path | None = None) -> pathlib.Path:
    """落盘战法池产物 `outputs/patterns/<day>_pattern_pool.json`（**唯一写入口**）。

    ⚠️ 本函数是「一次 IO 出两份产物」的收敛点：`build_pattern_pool.py` 与
    `plan_daily.py` 都走这里 ⇒ schema 不可能漂移（scan 侧 `load_pattern_pool`
    的读取契约只认这一份结构）。
    ⚠️ 写盘用 tmp + `os.replace` 原子替换：scan 每 60s 读一次，读到半截 JSON
    会退化成 `None` ⇒ fail-closed rc=8（全天禁新仓）。
    `path` 显式指定输出文件（消融对照用）；缺省 `out_dir` / `outputs/patterns`。
    """
    import os
    fp = pathlib.Path(path) if path else (pathlib.Path(out_dir or POOL_DIR) / f'{day}_pattern_pool.json')
    fp.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        'day': day, 'asof': asof, 'lookback': lookback, 'patterns': list(patterns),
        'stats': stats, 'pool': pool, 'name_map': name_map,
        'built_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'core.pattern_pool.build_pattern_pool ← core.strategies（参数: config/parameters.toml）',
    }
    tmp = fp.with_name(fp.name + f'.{os.getpid()}.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, fp)
    return fp


def build_pattern_pool(dmap: dict, asof: str, lookback: int = 4,
                       patterns: tuple = DEFAULT_PATTERNS,
                       names: dict | None = None,
                       exclude_fanbao_only: bool = True,
                       ) -> tuple[list[dict], dict]:
    """构建战法池。

    dmap      {sym: 日线 DataFrame(date/open/high/low/close/volume)}，date 升序
    asof      **信号窗口的最新交易日**（= T-1；调用方必须保证不含 T 日数据，否则即为前视）
    lookback  信号日回溯窗口（含 asof）。默认 4 —— 与 `plan_daily.py` 的「信号日∈最近4交易日」一致
    patterns  启用哪些战法（默认并集；单战法可传单元素元组做消融）
    names     {sym: 名称} 名称表（来自 `data/stock_names_stocks.json`）；用于**池层**剔除 ST/退市风险
    exclude_fanbao_only  剔除「仅反包命中」（`patterns == ['qu_shi_fanbao']`）的标的

    返回 (pool, stats)：
      pool  [{'sym','pattern','pattern_cn','sig_date','bars_since_sig','close_asof','patterns'}]，按信号日降序
      stats {'n_syms_scanned','n_skipped_short','n_excluded_st','n_no_name',
             'n_excluded_fanbao_only','n_pool','by_pattern','asof','lookback','window'}

    ⚠️ 两条**刻意不设**的池层过滤（2026-09-14 用户裁定，已实证会筛掉真值）：
      ① 「信号日必须在 asof 当日」⇒ 选手 4 只**全部落选**（其信号日是 T-2/T-3）；
      ② 「≥2 战法共振」⇒ 池 292 只但选手只剩 1/4。
      这两条只能当**排序权重**（见 `scan_and_confirm.py` 的 confirm_queue 排序键），
      **绝不可**下沉到池层当准入门槛。将来若有人"优化"回来，请先看这两条实测。
    """
    if not asof:
        raise ValueError("asof 不可为空（必须显式给出 T-1，避免隐式前视）")
    # 信号窗口 = asof 往前数 lookback 个**交易日**（下面按实际出现过的日期切片）
    stats = {"asof": asof, "lookback": lookback, "n_syms_scanned": 0,
             "n_skipped_short": 0, "n_excluded_st": 0, "n_no_name": 0,
             "n_excluded_fanbao_only": 0, "n_pool": 0, "by_pattern": {}}
    # 池层 ST 剔除（2026-09-14）：实测池里存在 `000078 ST海王`、`000909 *ST数源`。
    #   与 `plan_daily.py` 的既有纪律同源（「选手模式无 ST 证据」）——盘中 scan 侧虽也用
    #   实时名称过滤，但池层先剔可让 `by_pattern`/池规模的统计口径干净，且避免 ST 票
    #   在形态层被当成有效信号（ST 涨跌停幅度 5%，`limit_up_mask` 的 10% 口径对它本就不适用）。
    #   ⚠️ 名称缺失**不**排除（只计数 `n_no_name`）：scan 侧用实时名称兜底，此处若按"无名称
    #     即排除"会误伤名称表覆盖不全的正常票 —— 收窄必须发生在板块层，不能靠静默丢票。
    names = names or {}

    def _is_st(sym: str) -> bool:
        nm = str(names.get(sym) or '')
        return ('ST' in nm) or nm.startswith('*')

    out: list[dict] = []
    for sym, df in dmap.items():
        if df is None or not len(df):
            continue
        stats["n_syms_scanned"] += 1
        if _is_st(sym):
            stats["n_excluded_st"] += 1
            continue
        if not str(names.get(sym) or ''):
            stats["n_no_name"] += 1
        d = df[df["date"].astype(str) <= asof]
        if len(d) < MIN_BARS:
            stats["n_skipped_short"] += 1
            continue
        d = d.reset_index(drop=True)
        dates = d["date"].astype(str).tolist()
        # 信号窗口 = dates 里 <= asof 的最后 lookback 个交易日
        window = set(dates[-lookback:])
        hit_patterns, sig_dates = [], []
        for pname in patterns:
            fn = DETECTORS.get(pname)
            if fn is None:
                continue
            try:
                mask = fn(d, sym)      # 双参：sym 供 zt_huicai 按板块判涨停（见 DETECTORS 注释）
            except Exception:
                # 单只票的检测异常不得影响整池（本仓纪律：失败只降级自身，不打死链路）
                continue
            if mask is None or not len(mask):
                continue
            for i, is_sig in enumerate(mask.tolist()):
                if is_sig and dates[i] in window:
                    hit_patterns.append(pname)
                    sig_dates.append(dates[i])
                    break
        if not hit_patterns:
            continue
        uniq = sorted(set(hit_patterns))
        # 仅反包命中 ⇒ 剔除（2026-09-14 用户裁定「零损失收窄」：2308→1720，
        #   选手 4 只仍存活 3/4）。依据：`qu_shi_fanbao` 单独命中在选手实买样本里
        #   零覆盖，属**纯噪声扩容**；一旦与其它战法共振则保留（`patterns` 长度 > 1）。
        if exclude_fanbao_only and uniq == ["qu_shi_fanbao"]:
            stats["n_excluded_fanbao_only"] += 1
            continue
        sig_date = max(sig_dates)
        sig_pos = dates.index(sig_date)
        stats["by_pattern"][hit_patterns[0]] = stats["by_pattern"].get(hit_patterns[0], 0) + 1
        out.append({
            "sym": sym,
            "pattern": hit_patterns[0],                       # 主要战法（首个命中）
            "pattern_cn": PATTERN_CN.get(hit_patterns[0], hit_patterns[0]),
            "patterns": uniq,                                 # 多战法共振
            "sig_date": sig_date,
            "bars_since_sig": len(dates) - 1 - sig_pos,       # 0 = 信号就在 asof 当日
            "close_asof": float(d["close"].iloc[-1]),
        })
    # 按信号日降序（最新信号优先）；Python 排序稳定 ⇒ 同信号日保持 dmap 原顺序，可复现
    out.sort(key=lambda r: r["sig_date"], reverse=True)
    stats["n_pool"] = len(out)
    return out, stats


def pattern_of(pool: list[dict]) -> dict:
    """{sym: 记录} 便于 scan 做 O(1) 查询与留痕。"""
    return {r["sym"]: r for r in pool}
