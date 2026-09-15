# -*- coding: utf-8 -*-
"""R2.2 · 板块题材库 `data/sector_themes.json`

## 为什么需要
人格 SOP 的 ② 定主线 / ③ 选真龙 两段依赖**板块语义**：
  - `GEN-MAIN-01` 选板块三信号（钱往哪流 / 核心逻辑 / 业绩跟上）
  - `GEN-MAIN-03` 板块强度三层（高标 / 20cm / 容量票）
  - `GEN-MAIN-04` 龙头三关键（启动强度 / 换手 10~30% / 带动性）
  - `GEN-MAIN-06` 高低切换
  - 裁量点 `D2`/`D3`（选板块三信号的加权、板块强度三层分级）
而现有数据只有「申万行业码 → 名称」（R2.7 建的 `sw_l2_names.json`）——**没有成分、没有选手视角的板块语义、没有可执行的强度判据**。

## 本库三块
① **themes** —— 选手**反复跟踪的主线板块清单及各自逻辑**（语义层，不可替代）
   来源：归档区 `妖板选手方法论拆解/v4_materials/C_板块主线与选股.md §1.2`
   ⚠️ 这一节是从 **72 支视频 OCR 逐字稿**提取的，每条都带 `vNN@mm:ss` 出处 —— 是本库唯一**无法由行情数据重算**的部分。
② **strong_mainline_criteria** —— `C册 §1.3`「**强主线判定三标准**」（v65@03:34）的**机械化判据**。
   这是 R2.2 最有价值的可执行产出：把「怎么判断强主线」从主观表述变成三条可算的判据。
③ **sector_members** —— 板块 → 成分股（**当前**归属）。来源 `data/sw_industry_history.csv` 的
   「每股最新 l2_code」口径（与 R2.7 对齐时的同一口径）。

## 用法
    python scripts/build_sector_themes.py            # 生成 data/sector_themes.json
    python scripts/build_sector_themes.py --report   # 只报告不落盘

## 不做
- 不重算行情（板块强度计算是**下一步** `build_sector_strength.py` 的事，本库只提供静态底座）
- 不改 `sw_l2_names.json`（R2.7 产物）
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
HIST_CSV = BASE / "data" / "sw_industry_history.csv"
L2_NAMES = BASE / "data" / "sw_l2_names.json"
OUT = BASE / "data" / "sector_themes.json"

# ══════════════════════════════════════════════════════════ ① 选手主线板块清单
# 来源：v4_materials/C_板块主线与选股.md §1.2（原文逐条摘录，保留 vNN@mm:ss 出处）
THEMES = [
    dict(
        id="T1", name="AI 应用 / 算力 / 机器人", period="2026-08 三选一主线",
        rank="AI应用最主动 > 算力次之 > 机器人（午后承接的第二进攻线）",
        logic=[
            "AI 应用是科技方向里**涨停梯队最完整、主力进攻最主动**的板块",
            "爆发逻辑一：海外巨头业绩证明 AI 已转化为**云收入与企业需求**（不只是烧钱讲故事）",
            "爆发逻辑二：应用端持续迭代 → 市场从「只盯算力投入」转向「**应用端产生收入利润**」",
            "算力租赁 = 交易 AI 应用扩张后的**真实算力需求**（与应用并行、强度稍弱）",
            "机器人：宇树上市预期 + 大模型走向真实世界（视觉/减速器/电机/控制/仿真齐涨）",
            "选股：「去年炒预期、今年看落地」→ 有收入有订单的 AI 应用公司，业绩释放给**戴维斯双击**",
        ],
        risk=["爆发一致后次日常有**分歧预期**",
              "若只剩小票顶、**大成交核心掉队**（蓝标/昆仑）→ 方向虽强也**不能无脑上**"],
        structure="高标打开高度 → 20cm 制造赚钱效应 → 容量票承接大资金",
        sources=["v63@01:04", "v63@01:52", "v63@01:58", "v63@02:07", "v63@02:52", "v63@03:43", "v67@03:40"],
    ),
    dict(
        id="T2", name="科技主线（半导体/CPO/光模块/PCB/MLCC/存储/先进封装/国产替代）",
        period="贯穿全年（多次重申不动摇）",
        rank="核心：长鑫（=科技灵魂）> 科技配料 > 近期大涨品种",
        logic=[
            "10 倍股统计证明 AI 科技是热点主线（5 月第一周 16 只 10 倍股几乎全为科技/AI）",
            "操作总则：**去弱留强** —— 高位漂回避撤离，**低位滞涨细分龙头补涨**（光刻机/光刻胶/封测/EDA）",
            "MLCC 核心逻辑链：AI 算力投资 → MLCC 需求超预期 → 高端供需紧张 → 价格上行 + 国产化加速",
            "MLCC 涨价节奏：**电感、电阻先涨，MLCC 后涨；高容大涨、普通小涨**（台厂跟进→日韩龙头带头）",
            "MLCC 量价：单台 AI 服务器用量 10000+ 颗（普通 3~5 倍）；已成**第三大成本项**（仅次于 GPU/存储）",
            "科技内部轮动：MLCC/PCB 强后切向新发酵方向（t 定律 / 先进封装）",
            "中报三主线：AI 上游（MPO/存储/覆铜板/基板/电子布）· 新能源链（电池材料 VC）· 资源金属（稀土/钨/锡/锗/钼）",
            "七月三分法：**国产替代**（半导体/光刻机/EDA，机构抱团走慢牛）· **AI 硬件**（CPO/光模块/PCB，供不应求+涨价，等跌下来恐慌低吸）· **券商**（量能 3 万亿+两融 3 万亿，底部放量）",
        ],
        risk=["高位品种「技术修正」后才谈主升浪，直接追高风险大"],
        sources=["v11@00:46", "v11@00:49", "v24@01:43", "v24@01:52", "v24@02:04", "v17@01:28",
                 "v35@02:10", "v37@01:25", "v39@00:13", "v42@01:04", "v42@01:37", "v43@02:13",
                 "v71@01:40"],
    ),
    dict(
        id="T3", name="资源金属 / 新材料（涨价线延伸）", period="2026-06 起跟踪",
        rank="涨价线延伸：科技涨价 → 资源/化工借半导体材料接棒 → 整体资源涨价",
        logic=[
            "**涨价线延伸**：科技线涨价延伸到资源涨价 —— 资源类/化工类借**半导体材料**的理由接棒，再延伸就是整体资源涨价（回到年初「资源化工」）",
            "AI 基建拉动上游资源品：**电力→铜**、**半导体材料→锗/稀土**",
            "一周轮动实例：周一金属 → 周二锂电 → 周三上游元件 → 周四稀土/金属材料/AI 材料",
            "个股逻辑示例：凯盛科技（UTG 绑定华为/三星 + **TGV 通孔玻璃**是英伟达/英特尔高端封装关键基材）；京东方 A（与康宁合作→间接搭上英伟达）",
            "中国巨石：全球规模第一/成本最低/产业链一体化最强 + 电子布涨价",
        ],
        risk=["「反复板块」遇到反弹大涨要**分批做减法**（踩好节奏）"],
        sources=["v30@02:37", "v35@02:16", "v67@03:25", "v31@02:07", "v34@01:04", "v34@01:22",
                 "v34@02:55", "v66@01:55", "v66@02:04"],
    ),
    dict(
        id="T4", name="电力（暗线）", period="2026-05 起多次重申",
        rank="中线看好思路不变",
        logic=["逻辑简单：**AI 算力到最后都绕不开电力** + 夏季用电高峰",
               "电力、电网设备中线看好思路不变；物理 AI 开始重视（无人驾驶/工业互联网/物联网）",
               "6 月底判断：电子布、算力金属没结束，锂矿完成双底，**电力也是底部**，科技主线不动摇"],
        risk=[],
        sources=["v17@02:40", "v19@01:25", "v42@01:04"],
    ),
    dict(
        id="T5", name="商业航天 / 机器人（反复性板块，非追涨方向）", period="2026-05 起",
        rank="反复板块 —— 遇反弹大涨分批做减法",
        logic=["商业航天：政策 + 新闻联播事件驱动；老牌概念股涨停 = **主流资金开始认可**",
               "对待方式：这类板块**不断反复**，遇反弹大涨分批做减法，踩好节奏至关重要"],
        risk=["⭐ **「卖点式利好」警示**：突发利好放量急拉是**找卖点**而非买点 —— 短期一两年看不到业绩高增长，**不建议追涨**"],
        sources=["v11@00:58", "v11@01:01", "v19@01:25", "v50@02:16"],
    ),
    dict(
        id="T6", name="国产算力（8 月中旬新观察主线）", period="2026-08 中",
        rank="盯龙头高度判断板块强度（利通电子 / 数据港）",
        logic=["腾讯资本开支 **191 亿 → 528 亿**（疯狂买 AI 服务器）+ DeepSeek API 涨价（高峰输入涨 200%/输出涨 350%）= **算力紧缺**",
               "带头龙头：**利通电子、数据港**；「看算力链能不能打出核心高度标杆，将决定该方向的延伸空间与市场强度」",
               "个股：神州数码（国内稀缺 AI 算力总包商，绑定昇腾/智谱/DeepSeek/字节）、证通电子（自持算力约 200P）"],
        risk=[],
        sources=["v69@02:31", "v69@02:25", "v69@02:46", "v69@02:49", "v69@03:04", "v50@03:07", "v64@01:04"],
    ),
]

# ══════════════════════════════════════════════════════════ ② 强主线判定三标准（§1.3）
STRONG_MAINLINE = [
    dict(
        id="M1", text="板块指数**连续放量**突破**关键均线**",
        source="v65@03:34",
        mech="板块指数（等权或容量加权）日线：`close > MA(N)` 且 `vol > vol_ma(M) * k`，"
             "且**连续** ≥ `D` 日满足（N/M/k/D 未标定 → 先用 N=20, M=5, k=1.2, D=2 作**占位**，待 R1.3 容差带标定）",
        params_placeholder=dict(ma_n=20, vol_ma_m=5, vol_k=1.2, consecutive_days=2),
        calibrated=False,
    ),
    dict(
        id="M2", text="板块**趋势股率先创阶段新高**并带动跟风",
        source="v65@03:34",
        mech="板块内「趋势股」（`close > MA20 且 MA20 向上`）中，出现 ≥`T` 只在近 `W` 日内创"
             "**阶段新高**（`close >= max(high, W)`）；且这些票创新高后 `K` 日内**板块内跟涨家数占比**上升",
        params_placeholder=dict(trend_min=1, window_days=60, follow_ratio_up=True),
        calibrated=False,
    ),
    dict(
        id="M3", text="板块与**指数形成共振**（指数涨它领涨、指数调它抗跌）",
        source="v65@03:34",
        mech="相对强度双条件：① **领涨** = 指数上涨日，板块收益 > 指数收益（超额 > 0）的比例 ≥ `p1`；"
             "② **抗跌** = 指数下跌日，板块回撤 < 指数回撤 的比例 ≥ `p2`。窗口 `W` 交易日",
        params_placeholder=dict(window_days=20, p1=0.6, p2=0.6),
        calibrated=False,
    ),
]


def latest_membership() -> dict:
    """每股最新 l2_code（与 R2.7 同一口径：取 start_date 最大的那行）。"""
    latest: dict[str, tuple[str, str]] = {}
    with HIST_CSV.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sym = str(row["code"]).strip().zfill(6)
            l2 = str(row.get("l2_code", "")).strip()
            if not l2:
                continue
            started = str(row.get("start_date", "") or "")[:10]
            cur = latest.get(sym)
            if cur is None or started >= cur[0]:
                latest[sym] = (started, l2)
    by_l2: dict[str, list[str]] = collections.defaultdict(list)
    for sym, (_s, l2) in latest.items():
        by_l2[l2].append(sym)
    return by_l2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    t0 = time.time()

    names = json.loads(L2_NAMES.read_text(encoding="utf-8")).get("names", {})
    by_l2 = latest_membership()
    members = {}
    for l2, syms in sorted(by_l2.items()):
        if l2 == "NA":
            continue
        members[l2] = dict(name=names.get(l2, ""), n=len(syms), members=sorted(syms))

    doc = {
        "_meta": {
            "schema_version": "1",
            "built_at": time.strftime("%Y-%m-%d"),
            "purpose": "板块题材库：① 选手反复跟踪的主线板块清单（语义层）② 强主线判定三标准的机械化判据 "
                       "③ 板块→成分股映射。供人格 SOP GEN-MAIN-01/03/04/06 与裁量点 D2/D3 使用。",
            "sources": {
                "themes": "归档区 妖板选手方法论拆解/v4_materials/C_板块主线与选股.md §1.2"
                          "（由 72 支视频 OCR 逐字稿提取，每条带 vNN@mm:ss）",
                "strong_mainline_criteria": "同上 C册 §1.3（v65@03:34）",
                "sector_members": "data/sw_industry_history.csv 的「每股最新 l2_code」口径（与 R2.7 对齐一致）",
                "names": "data/sw_l2_names.json（R2.7 产物）",
            },
            "themes_count": len(THEMES),
            "criteria_count": len(STRONG_MAINLINE),
            "sector_count": len(members),
            "member_total": sum(v["n"] for v in members.values()),
            "calibrated_criteria": sum(1 for c in STRONG_MAINLINE if c["calibrated"]),
            "note": "⚠️ 三标准的**具体阈值均未标定**（SOP 只给了定性表述）→ 本库给出可执行形式 + "
                    "**占位参数并显式标 calibrated=false**，不假装已标定。标定归 R1.3 容差带 / R2 后续。",
            "generator": "scripts/build_sector_themes.py",
            "elapsed_sec": round(time.time() - t0, 2),
        },
        "themes": THEMES,
        "strong_mainline_criteria": STRONG_MAINLINE,
        "sector_members": members,
    }

    print(f"themes={len(THEMES)}  criteria={len(STRONG_MAINLINE)}  "
          f"sectors={len(members)}  members_total={doc['_meta']['member_total']}")
    print("  themes:", ", ".join(f"{t['id']}={t['name'][:18]}" for t in THEMES))
    print("  criteria:", ", ".join(f"{c['id']}({c['source']})" for c in STRONG_MAINLINE))
    top = sorted(members.items(), key=lambda kv: -kv[1]["n"])[:8]
    print("  最大板块:", ", ".join(f"{v['name'] or k}({v['n']})" for k, v in top))
    nonzero_name = sum(1 for v in members.values() if v["name"])
    print(f"  有名称的板块 = {nonzero_name}/{len(members)}")

    if args.report:
        return 0
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    back = json.loads(OUT.read_text(encoding="utf-8"))
    assert back["_meta"]["themes_count"] == len(back["themes"])
    assert back["_meta"]["criteria_count"] == len(back["strong_mainline_criteria"])
    assert back["_meta"]["sector_count"] == len(back["sector_members"])
    print(f"selfcheck OK → {OUT}  ({OUT.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
