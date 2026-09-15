# -*- coding: utf-8 -*-
"""R1.2 裁量点清单 + R1.3 容差带定义。

产出三个文件 + 校验报告：
  yaoban-system/persona/discretion_v0.toml
  yaoban-system/persona/tolerance_v0.toml
  docs/PERSONA_DISCRETION_v0.md

**关键设计**：本工具会读回 `sop_v0.toml`，把其中 `disc=` 与 `tol=` 引用到的每个键
**逐个要求有定义**，缺一个就报 ERR 并不产出 —— 这样才能保证
「裁量点清单可枚举且每项有 SOP 原文支撑」不是一句自述，而是被机器守住的契约。

R1.3 的核心是一处**必须裁定的口径冲突**：
  画像 §3.7(8) 的 5 例实盘核验 → 均线口径有 **3~7%** 弹性（并明说「落地实现须用容差带」）
  计划 §4.6③ + 9/11《K线批量核验报告》→ **≤2%**（17/18 命中，精度 0.6~1.6%）
两者并非矛盾，而是**两个不同的档**：前者是「人工读图时的语义容差」，后者是「机械化执行时的判定容差」。
本文件按此分档，并把 3~7% 的上界（7%）**弃用**（依据：该区间来自 n=5 的单案例估计，
且其中 1 例已被我们自己判定为"不精确"，4.45%）。
"""

import json
import pathlib
import tomllib

ROOT = pathlib.Path(r"C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\EvoAlpha")
SOP = ROOT / "yaoban-system/persona/sop_v0.toml"
OUT_DISC = ROOT / "yaoban-system/persona/discretion_v0.toml"
OUT_TOL = ROOT / "yaoban-system/persona/tolerance_v0.toml"
OUT_MD = ROOT / "docs/PERSONA_DISCRETION_v0.md"
OUT_REPORT = ROOT / "yaoban-system/persona/_build_discretion.report.txt"

# ════════════════════════════════════════════════════════════ R1.3 容差带
TOLERANCES = [
    dict(
        key="ma_pos",
        name="均线支撑/压力的判定容差",
        tight=0.02, loose=0.05, unit="ratio_of_ma",
        applies=["P1-ENTRY-02", "P1-HOLD-01", "P1-HOLD-02", "P2-DRAGON-04", "P3-DRAGON-02",
                 "P4-DRAGON-02", "GEN-HOLD-03", "GEN-HOLD-22", "GEN-DRAGON-01", "GEN-MAIN-05",
                 "P1-DRAGON-08", "P1-DRAGON-10"],
        tight_use="机械化执行（形态筛选、日线兜底触发、止损判定）",
        loose_use="人工/LLM 语义判读（「回踩到 10 日线附近」这类口述）",
        evidence="E1 + kline 交叉",
        source="9/11《K线批量核验报告》17/18 命中、精度 0.6~1.6% ／ 画像 §3.7(8) 5 例核验",
        ruling="机械化档取 **≤2%**（新、样本间一致、精度已知）；语义档取 **≤5%**。"
               "**弃用 3~7% 的 7% 上界** —— 该区间来自 n=5 单案例估计，且其中太极实业一例"
               "（MA5 13.92 vs 最低 14.54，偏差 4.45%）已被我们自己判为「踩 5 日线不精确」。",
    ),
    dict(
        key="ma_pos_broad",
        name="主升浪持有的均线判读容差（含情绪附加项）",
        tight=0.02, loose=0.05, unit="ratio_of_ma",
        applies=["GEN-HOLD-01"],
        tight_use="破 5/10 日线的减半/清仓触发",
        loose_use="「情绪转弱」这类附加条件的联合判读",
        evidence="E1",
        source="v09@00:58（rule 16）",
        ruling="数值同 ma_pos；区别在于本键允许**多条件联合**（均线 + 情绪），"
               "故 LLM 判读介入概率更高 → 实际以 loose 档为主。",
    ),
    dict(
        key="vwap_tol",
        name="分时均价线（VWAP）穿越容差",
        tight=0.003, loose=0.003, unit="ratio_of_price",
        applies=["GEN-ENTRY-02", "GEN-ENTRY-03", "GEN-ENTRY-04", "P2-ENTRY-01", "P3-ENTRY-02"],
        tight_use="分时破线/回踩站稳的机械化判定",
        loose_use="（同值）",
        evidence="E6",
        source="`parameters.toml [sell.intraday].vwap_tol = 0.003`（引擎既有值，非选手口述）",
        ruling="直接沿用引擎既有 0.3%。⚠️ 该值是引擎设计值（E6），**不是**从选手素材标定出来的 → "
               "属「待标定」项，R2 拿到候选池 1m 数据后应回标。",
    ),
    dict(
        key="support_low",
        name="支撑位（前低区域）容差",
        tight=0.005, loose=0.015, unit="ratio_of_price",
        applies=["GEN-GATE-09"],
        tight_use="指数级支撑判定（精度要求高）",
        loose_use="个股级支撑「区域」判读",
        evidence="E2 + kline 交叉",
        source="2026-09-11 盘面观点：支撑 3850（8/24-25 前低 3855.35 / 3850.86），当日最低 3852.03",
        ruling="指数级取 **0.5%**（9/11 实测偏差仅 0.053%，留一个量级余量）；"
               "个股级因缺乏同类验证样本，暂取 **1.5%** 并标为待标定。"
               "取法本身 = **近 20 交易日摆动低点聚类**，非整数关。",
    ),
    dict(
        key="resist_band",
        name="阻力位（前高/套牢筹码区）容差",
        tight=0.005, loose=0.015, unit="ratio_of_price",
        applies=["GEN-HOLD-18"],
        tight_use="「遇压不突破 → 止盈」的机械化判定",
        loose_use="「上方 X 块有阻力」这类口述判读",
        evidence="E1（量级与 support_low 同源，未单独核验）",
        source="v37 / v44 / v45（新安股份 15.7、永杉锂业 24 块）",
        ruling="与 support_low 同值（对称处理）。⚠️ 阻力位**未做过 K 线核验**，"
               "依据只有两条口述实例 → 标为待核验。",
    ),
    dict(
        key="turnover_gate",
        name="换手率闸门",
        healthy_min=10.0, healthy_max=30.0, warn_above=50.0, unit="percent",
        applies=["GEN-MAIN-04", "GEN-GATE-14"],
        tight_use="龙头识别（健康区间）",
        loose_use="风险警戒（>50% 需小心）",
        evidence="E1",
        source="v36@01:22（换手 10%~30%）／ 2026-09-08（超过 50 以上换手都需要小心）",
        ruling="健康区间 [10%, 30%] 与警戒线 50% **分别来自两期视频**，二者之间（30~50%）"
               "选手未给口径 → 该段留空，交 LLM 裁量时须显式说明「无 SOP 依据」。",
    ),
    dict(
        key="seven_pct_band",
        name="冲高「7 个点」封不住的判定带",
        threshold=0.07, band=0.005, unit="ratio",
        applies=["GEN-HOLD-15"],
        tight_use="盘中冲高止盈触发",
        loose_use="（同值）",
        evidence="E1",
        source="v37（06-22）：「冲高 7 个点以上封不住的话，就可以先锁住部分利润」",
        ruling="「7 个点以上」取下界 7%，容差带 ±0.5pp（6.5%~7% 起算）。"
               "⚠️ 选手同时给了「直接高开封板就继续等」的例外 → 该例外**不可机械化**，交 D7。",
    ),
    dict(
        key="pullback_window",
        name="回档天数窗口",
        formula_min=3, formula_max=5, live_min=1, live_max=5, hard_max=5, unit="trading_days",
        applies=["P1-DRAGON-03", "P1-DRAGON-04", "P4-DRAGON-01"],
        tight_use="筛选口径（formula：3~5 天）",
        loose_use="实战判定口径（live：1~3 天，最多 ≤5）",
        evidence="E1",
        source="v05@00:28（rule 3）／ v24@02:28（rule 4）",
        ruling="**两口径并存且都已落在参数里**（`[strategy.huigui]` 用于 screen、"
               "`[strategy.huigui.live]` 用于 live）。硬上界 5 天两口径一致。",
    ),
    dict(
        key="pullback_depth",
        name="回调幅度窗口",
        formula_min=0.05, formula_max=0.15, live_min=0.01, live_max=0.15,
        prior_gain_frac=0.3333, unit="ratio",
        applies=["P1-DRAGON-05"],
        tight_use="筛选口径 5%~15%",
        loose_use="实战口径 1%~15%（案例回归校准 2026-08-24）",
        evidence="E1",
        source="v05@00:28 / v01@00:58（rule 5）＋ `parameters.toml` v4.0.1 修正注释",
        ruling="实战案例幅度**远小于**公式口径（协鑫能科 1.5%、江特电机 5%、振华科技）→ "
               "live 下界放宽到 1%。另加约束「不超前期涨幅 1/3」。",
    ),
    dict(
        key="execution_lag",
        name="执行偏差容许（人格评估档）",
        days=3, unit="trading_days",
        applies=["GEN-HOLD-19", "GEN-HOLD-23"],
        tight_use="**不可用于生产执行**",
        loose_use="只用于「人格像不像」的诊断轨评估",
        evidence="E1（画像 §4 原文明示）",
        source="皖维高新案：明确「留意 7.5 支撑」、收盘 7.41 已破位、实际 06-17 才卖 = **3 个交易日偏差**",
        ruling="⭐ 这是**诊断轨专用**参数。生产执行必须即时（结构破位当场了结，如粤传媒买入次日即砍）。"
               "把 3 日容许套到生产上会系统性放大回撤，与 GEN-HOLD-23 直接冲突。",
    ),
    dict(
        key="washout_time",
        name="洗盘时间止损",
        warn_days=30, exit_days=60, unit="calendar_days",
        applies=["P1-DRAGON-15"],
        tight_use="周级复核（warn 30 天）",
        loose_use="换股决策（exit 60 天）",
        evidence="E1",
        source="v51@02:40（rule 24）＋ `[sell].washout_warn_days=30 / washout_exit_days=60`",
        ruling="⚠️ 单位是**自然月**（选手说「超 1 月/超 2 月」），参数里按自然日 30/60 近似 —— "
               "二者不等价（30 自然日 ≈ 20 交易日），须在实现时明确取哪一个。",
    ),
    dict(
        key="pattern_boundary",
        name="形态边界（「像不像」的判据化）",
        xianren_upper_shadow_ratio_min=2.0,
        xianren_upper_shadow_pct_min=2.8,
        xianren_position=(0.85, 1.35),
        fanbao_strict="close > prev_open（完全吞没）",
        fanbao_loose="close > prev_close（部分覆盖）",
        zthuicai_volume_surge_min=1.2,
        unit="mixed",
        applies=["P3-DRAGON-01", "P3-DRAGON-02", "P2-DRAGON-02", "P4-DRAGON-04"],
        tight_use="仙人指路（唯一零歧义、有公式源码的一套）",
        loose_use="趋势反包的「包住」（严格/宽松两口径未裁定）",
        evidence="E1（仙人指路零歧义）／ E6（反包口径）",
        source="v70 通达信公式源码原文 ／ v22 口述定义",
        ruling="仙人指路三条件**直接照抄公式**，无容差；趋势反包「包住」**未裁定** → "
               "现实现取严格档，并登记为缺口（P2-DRAGON-02 note 已标）。",
    ),
]

# ════════════════════════════════════════════════════════════ R1.2 裁量点
DISCRETIONS = [
    dict(
        id="D1", name="大盘环境闸门强度",
        seg="GATE", priority="high",
        smokes=["GEN-GATE-10", "GEN-ENTRY-07"],
        inputs=["指数相对 MA5/MA10/MA20/MA60 的位置",
                "距最近支撑位（前低聚类）的距离",
                "成交额及其 5/20 日均值分位",
                "涨停家数分档、最高连板高度",
                "全 A 等权涨跌幅（vs 加权指数背离）",
                "隔夜外围（尤其美股科技）方向"],
        outputs=["trade_normally（正常出手）", "reduce_size（降档出手）",
                 "observe_only（观望不操作）", "exit_holdings（主动减仓）"],
        sop_quote="「指数很弱，支撑在 3850 点，可以选择观望不操作，等市场明朗再说」",
        sop_src="2026-09-11 10:22 盘面观点（E2）",
        why_hard="选择「今天做还是不做、做到什么强度」是策略级决策；"
                 "选手 9/11 在已发候选池的情况下仍整体观望（3 只候选、仅 1 只成交）→ 确有独立存在",
        budget="1 次/日 + 盘中状态变化时按需（建议 ≤3 次/日）",
    ),
    dict(
        id="D2", name="选板块三信号的加权与取舍",
        seg="MAIN", priority="high",
        smokes=["GEN-MAIN-01"],
        inputs=["各板块主力净流入排序（5/10 日）",
                "板块核心逻辑（题材/催化事件）",
                "板块内成分股业绩增速",
                "板块当日涨停梯队",
                "市场当前炒作的风格（趋势 vs 情绪）"],
        outputs=["板块清单 + 每个板块 primary / secondary / avoid + 0-1 强度分"],
        sop_quote="「钱往哪流 / 有无核心逻辑 / 业绩能否跟上」三信号；"
                  "「并不是所有研报的观点都可以去玩，你要找到主力喜欢的题材，看市场炒什么自然有答案」",
        sop_src="重构计划 §4.1 ② ／ v24（2026-06-04）",
        why_hard="三信号各自可算，但「加权与取舍」是主观的；且「主力喜欢什么」无法形式化",
        budget="1 次/日（盘前）",
    ),
    dict(
        id="D3", name="板块强度三层分级",
        seg="MAIN", priority="medium",
        smokes=["GEN-MAIN-03"],
        inputs=["板块内连板梯队（高标）", "板块内 20cm 标的数量（创业板/科创）",
                "板块成交额与容量票（大市值承接）表现"],
        outputs=["strong（主线，可重仓）", "mid（次线，标准仓）", "weak（不参与）"],
        sop_quote="板块强度三层：高标 / 20cm / 容量票",
        sop_src="选手战法画像 §三（E5 二次归纳）",
        why_hard="「三层」的判定标准与权重未形式化；且 E5 级（后期总结）→ 置信度本身较低",
        budget="1 次/日（随 D2 一起）",
    ),
    dict(
        id="D4", name="「是否热点概念」判定",
        seg="DRAGON", priority="medium",
        smokes=["GEN-DRAGON-02"],
        inputs=["标的所属概念标签（需 R2 板块题材库）", "该概念当日 / 近 3 日强度",
                "该概念在涨停梯队中的位置"],
        outputs=["hot（热点，正常评估）", "warm（边缘，降权）",
                 "not_hot（非热点 → 仅教学，不操作）"],
        sop_quote="「符合形态但不属于热点概念 → 仅教学，不操作」；「冷门概念不玩」",
        sop_src="v04（2026-04-21）／ v11（2026-05-08）",
        why_hard="规则明确（not_hot 即排除），但「热点」的判定本身需语义理解 —— "
                 "规则可机械化、判据不可",
        budget="候选级（≤50 只/日，与 D6 合并可摊薄）",
    ),
    dict(
        id="D5", name="候选池之外的主动机会",
        seg="DRAGON", priority="medium",
        smokes=["GEN-DRAGON-04"],
        inputs=["板块内龙头的示范效应强度", "同板块滞涨股的补涨空间测算",
                "标的与龙头的相关性/跟随度", "标的流通盘与承接能力"],
        outputs=["add_candidate（加入候选，附理由）", "skip"],
        sop_quote="9/8 粤传媒为主动捕捉「龙头（龙版）示范下的补涨空间」，非战法筛选",
        sop_src="2026-09-08 吃板播报（E2）",
        why_hard="⭐ 这条直接对应「**公开候选池 ≠ 完整交易集**」—— 累计 3 例跨 3 个交易日"
                 "（9/8 粤传媒、9/9 诺普信、9/11 道明光学）。不加这条，人格会系统性漏掉其真实交易",
        budget="1 次/日（候选池生成后）",
    ),
    dict(
        id="D6", name="分时质量三档分级 与「止跌企稳」判定",
        seg="ENTRY", priority="high",
        smokes=["GEN-ENTRY-06", "P1-ENTRY-03"],
        inputs=["分时白线（价）与黄线（VWAP）的相对位置与斜率",
                "上行段量能 / 回踩段量能之比",
                "今日低点 vs 昨日低点（重心是否上移）",
                "日内振幅、回落幅度 vs 均价线",
                "（加分）急拉缓跌形态、尾盘是否微翘不破均线"],
        outputs=["A_optimum（有量 + 一步一步向上）",
                 "B_medium（无量、欠缺力度 → 可观察不优先）",
                 "C_reject（第一波回落跌破均价线 → 弃）"],
        sop_quote="① 最优：分时有量 + 一步一步向上走 ② 中等：无量、欠缺力度 ③ 弃：第一波回落跌破均价线；"
                  "「金能科技刚发出来它分时就是跌破均价线的，这种是不玩的啊，纪律说过很多次的！」",
        sop_src="2026-09-10 候选池与纪律（E2）；三档实证：山东玻纤 A→触板 +9.98% / "
                "沃特股份 B→收 0.00% / 金能科技 C→−5.71%",
        why_hard="⭐ 唯一有**完整执行率证据**的入场判据（9/9、9/10 两日一票否决规则执行率 100%）；"
                 "但「有量」「一步步向上」的阈值未给 → 这是最该由 LLM 承担的一项",
        budget="候选级（≤50 只/日）；同标的同状态 5 分钟内复用缓存",
        warning="⚠️ 架构级分离：C 档（破均价线）作为**行为复刻**保留，"
                "但 9/11 反事实已证伪其**alpha 属性**（命中 94.4% 却显著负 alpha，p=0.0013）"
                "→ 不得作为收益过滤器拦单，只作诊断与排序。",
    ),
    dict(
        id="D7", name="买入逻辑是否消失",
        seg="HOLD", priority="high",
        smokes=["GEN-HOLD-13"],
        inputs=["本次买入的规则 id 与理由（由决策链 provenance 落盘）",
                "该理由对应的结构是否仍成立",
                "当日开盘方向（低开 = 低于预期）",
                "盘中分时是否上攻无量 / 回落破线"],
        outputs=["logic_intact（持有）", "logic_weakened（减仓）",
                 "logic_invalidated（卖出）"],
        sop_quote="「买卖逻辑必须保持一致。买入逻辑消失的那一刻，就是卖出的最佳时机」；"
                  "「很多人不知道怎么卖，是因为没想清楚自己当初为什么买」；"
                  "「早上低开不及预期可考虑走」",
        sop_src="v20（2026-05-29）／ v22（2026-06-02）",
        why_hard="这是「八种买入逻辑 → 卖出规则映射表」的执行端。"
                 "映射表本身可枚举，但「逻辑是否已消失」需语义判断 —— 且它是**唯一能解释"
                 "非对称卖出行为**（烽火 −7.4% 割 vs 茶花 −4.9% 拿）的机制",
        budget="持仓级（通常 ≤4 只）；状态变化时触发，非每分钟",
    ),
    dict(
        id="D8", name="仓位档位选择与弹性调节",
        seg="SIZE", priority="high",
        smokes=["GEN-SIZE-14"],
        inputs=["D1 的环境档位", "候选数量与质量档（含筛选数量=市场温度计）",
                "前一日战法信号强度 / 执行结果", "账户动态权益（cost_equity）",
                "当前敞口与现金比例"],
        outputs=["目标总仓位百分比 + 各标的权重排序"],
        sop_quote="实测轨迹：57.0%（9/4）→ 58.1%（9/7）→ ≈69.5%（9/8）→ 43.4%（9/9，净减仓 −36%）"
                  "→ 85.4%（9/10，市值 +101%）；「数量比昨天减少，意味着操作难度加大，仓位和方向都需要踩准再出手」",
        sop_src="2026-09-04 ~ 09-10 持仓截图（E0，最终仲裁源）／ v11（04-24）",
        why_hard="「加多少」是核心裁量；但**硬边界不可越**："
                 "单笔亏损 ≤2% 总资金 / 单票 ≤30% / 市况上限（牛 50-70%·震 30-50%·熊 ≤20%）"
                 "/ 永不满仓 / 现金 ≥10%",
        budget="1 次/日（盘前定档）+ 盘中重大变化时 ≤2 次",
        hard_guard="LLM 只能在硬约束区间内裁量；越界输出必须被风控 veto（R3.4 冲突注入测试覆盖）",
    ),
    dict(
        id="D9", name="洗盘 vs 出货",
        seg="DRAGON", priority="medium",
        smokes=["P1-DRAGON-14"],
        inputs=["是否始终站稳 20 日 EMA", "回调期量能（缩量 / 放量杀跌）",
                "位置（累计涨幅是否 ≤30%）", "盘口大单托底（买一/买二数千手固定托单且不被砸穿）",
                "持续时间（是否超过 30/60 天）"],
        outputs=["washout（真洗盘 → 持有/低吸）", "distribution（真出货 → 果断止损离场）",
                 "uncertain（给出一档保守默认：减半）"],
        sop_quote="真洗缩量高位稳，出货放量破均线；"
                  "「买一/买二位置数千手固定托单，且不被砸穿、无持续砸盘大单」",
        sop_src="v51（2026-07-12 主力洗盘三招，E1）",
        why_hard="四维度中「筹码峰是否上移」需筹码分布数据（R2 依赖）；"
                 "「大单托底」需 L2 盘口（当前管线无）→ 现阶段只能靠余下维度 + 语义判断",
        budget="持仓级；每日 ≤1 次（盘后）",
    ),
    dict(
        id="D10", name="趋势反包是否要求放量（口径待标定）",
        seg="DRAGON", priority="low",
        smokes=["P2-DRAGON-03"],
        inputs=["反包日量比（vs 5 日均量）", "前阴日量能", "反包日所处均线位置"],
        outputs=["require_volume（要求放量，附阈值）", "volume_neutral（不要求）"],
        sop_quote="定义未提量能；但 06-11 博威合金案例强调「缩量 + 10 日线支撑」",
        sop_src="v22（2026-06-02 定义）／ v30（2026-06-11 案例）；"
                "`parameters.toml` 注释已标「图上标注，阈值待验证」（E6）",
        why_hard="⚠️ 这**不是**「不可编码」，而是「**尚未标定**」—— "
                 "属 R1.3 容差带 / R2 数据到位后应**退出裁量层**的临时候选项。"
                 "另：趋势反包自述属**试验期**（「这两天也在研究」「首笔成功」）→ 权重应低于 P1",
        budget="候选级；因属试验期战法，建议限流（≤5 只/日）",
    ),
]


# ════════════════════════════════════════════════════════════════ 生成
def main():
    sop = tomllib.loads(SOP.read_text(encoding="utf-8"))
    rules = sop["rule"]

    used_disc, used_tol = {}, {}
    for r in rules:
        if r.get("disc"):
            used_disc.setdefault(r["disc"], []).append(r["id"])
        if r.get("tol"):
            for t in [x.strip() for x in str(r["tol"]).split(",") if x.strip()]:
                used_tol.setdefault(t, []).append(r["id"])

    defined_disc = {d["id"] for d in DISCRETIONS}
    defined_tol = {t["key"] for t in TOLERANCES}

    errs, warns = [], []
    for k in sorted(used_disc):
        if k not in defined_disc:
            errs.append(f"sop_v0.toml 引用了未定义的裁量点 {k}（被 {used_disc[k]} 引用）")
    for k in sorted(used_tol):
        if k not in defined_tol:
            errs.append(f"sop_v0.toml 引用了未定义的容差键 {k}（被 {used_tol[k]} 引用）")
    for d in DISCRETIONS:
        for s in d["smokes"]:
            if s not in {r["id"] for r in rules}:
                errs.append(f"{d['id']}.smokes 引用了不存在的规则 {s}")
            elif s not in used_disc.get(d["id"], []):
                warns.append(f"{d['id']} 声明引用 {s}，但该规则未把 disc 设为 {d['id']}")
        if not d["sop_quote"].strip() or not d["sop_src"].strip():
            errs.append(f"{d['id']}: 缺 SOP 原文支撑或出处")
    # 反向：定义了但没被引用的裁量点
    for k in sorted(defined_disc - set(used_disc)):
        warns.append(f"裁量点 {k} 已定义但未被任何规则引用（可能是冗余）")
    for k in sorted(defined_tol - set(used_tol)):
        warns.append(f"容差键 {k} 已定义但未被任何规则引用（可能是冗余）")

    log = []
    lw = log.append

    # ---- 输出 TOML
    with OUT_TOL.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# EvoAlpha 人格层 · 容差带 v0（R1.3）\n")
        f.write("# 生成器：yaoban-system/tools/build_persona_discretion.py（勿手改）\n\n")
        f.write("[meta]\n")
        f.write('version = "tolerance-v0"\n')
        f.write('built_at = "2026-09-12"\n')
        f.write(f"count = {len(TOLERANCES)}\n")
        f.write('conflict_note = "均线容差两口径并存（3~7% 语义档 vs <=2% 机械档）；'
                '本表按 2%/5% 分档并弃用 7% 上界，理由见各条 ruling"\n\n')
        for t in TOLERANCES:
            f.write("[[tolerance]]\n")
            for k, v in t.items():
                f.write(f"{k} = {_tv(v)}\n")
            f.write("\n")

    with OUT_DISC.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# EvoAlpha 人格层 · 裁量点清单 v0（R1.2）\n")
        f.write("# 生成器：yaoban-system/tools/build_persona_discretion.py（勿手改）\n\n")
        f.write("[meta]\n")
        f.write('version = "discretion-v0"\n')
        f.write('built_at = "2026-09-12"\n')
        f.write(f"count = {len(DISCRETIONS)}\n")
        f.write('interface_note = "每项 = 输入特征 + 输出枚举 + SOP 原文 + 限流预算；'
                '输出必须过 schema 校验且可重放（计划 §4.2）"\n\n')
        for d in DISCRETIONS:
            f.write("[[discretion]]\n")
            for k, v in d.items():
                f.write(f"{k} = {_tv(v)}\n")
            f.write("\n")

    # ---- 报告
    lw("=" * 78)
    lw("R1.2 裁量点清单 + R1.3 容差带 构建报告")
    lw("=" * 78)
    lw(f"sop_v0.toml 规则数      : {len(rules)}")
    lw(f"引用到的裁量点          : {len(used_disc)} 个 → {sorted(used_disc)}")
    lw(f"引用到的容差键          : {len(used_tol)} 个 → {sorted(used_tol)}")
    lw(f"已定义裁量点 / 容差键   : {len(DISCRETIONS)} / {len(TOLERANCES)}")
    lw(f"ERR {len(errs)} / WARN {len(warns)}")
    for e in errs:
        lw(f"  ERR  {e}")
    for e in warns:
        lw(f"  WARN {e}")
    lw("")
    lw("裁量点 → 被哪些规则引用：")
    for d in DISCRETIONS:
        lw(f"  {d['id']:<4} [{d['seg']:<6}] {d['name']}")
        lw(f"        ← {used_disc.get(d['id'], ['(未被引用)'])}")
    lw("")
    lw("容差键 → 被哪些规则引用：")
    for t in TOLERANCES:
        lw(f"  {t['key']:<18} ← {len(used_tol.get(t['key'], []))} 条")
    OUT_REPORT.write_text("\n".join(log), encoding="utf-8")

    # ---- 人读 MD
    write_md(used_disc, used_tol)

    # ---- 自校验（返回码 0 ≠ 成功）
    for path in (OUT_TOL, OUT_DISC):
        tomllib.loads(path.read_text(encoding="utf-8"))
    if OUT_MD.stat().st_size < 4000:
        raise SystemExit("FAIL: PERSONA_DISCRETION_v0.md 过小")
    print(f"selfcheck: tolerance+discretion TOML 回读 OK；md={OUT_MD.stat().st_size}B")
    print(f"OK disc={len(DISCRETIONS)} tol={len(TOLERANCES)} err={len(errs)} warn={len(warns)}")
    print(f"  {OUT_TOL}\n  {OUT_DISC}\n  {OUT_MD}\n  {OUT_REPORT}")


def _tv(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_tv(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {_tv(x)}" for k, x in v.items()) + "}"
    s = (str(v).replace("\\", "\\\\").replace('"', '\\"')
         .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{s}"'


def write_md(used_disc, used_tol):
    L = []
    a = L.append
    a("# EvoAlpha 人格层 · 裁量点清单与容差带 v0")
    a("")
    a("> **自动生成**，请勿手改。生成器 `yaoban-system/tools/build_persona_discretion.py`；")
    a("> 机器可读：`persona/discretion_v0.toml` + `persona/tolerance_v0.toml`；")
    a("> 上游：`persona/sop_v0.toml`（R1.1，125 条规则）。权威：`docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md` §R1.2/§R1.3。")
    a("")
    a("**契约**：SOP 表中被 `disc=` 引用的每个裁量点、被 `tol=` 引用的每个容差键，")
    a("在本文件里都必须有定义 —— 缺一个即构建失败。反向（定义了却无人引用）只出 WARN。")
    a("")
    a("## 一、裁量点总览（R1.2）")
    a("")
    a("| id | 段 | 名称 | 优先级 | 引用规则数 | 限流预算 |")
    a("|---|---|---|---|---|---|")
    SEGS = {"GATE": "① 环境闸门", "MAIN": "② 定主线", "DRAGON": "③ 选真龙",
            "ENTRY": "④ 找低吸", "HOLD": "⑤ 稳持仓", "SIZE": "⑥ 仓位"}
    for d in DISCRETIONS:
        a(f"| `{d['id']}` | {SEGS[d['seg']]} | {d['name']} | {d['priority']} | "
          f"{len(used_disc.get(d['id'], []))} | {d['budget']} |")
    a("")
    a("## 二、裁量点明细")
    a("")
    for d in DISCRETIONS:
        a(f"### `{d['id']}` {d['name']}")
        a("")
        a(f"- **所属段**：{SEGS[d['seg']]}　**优先级**：{d['priority']}")
        a(f"- **引用规则**：{', '.join('`'+x+'`' for x in used_disc.get(d['id'], [])) or '—'}")
        a(f"- **限流预算**：{d['budget']}")
        a(f"- **为什么不可机械化**：{d['why_hard']}")
        if d.get("warning"):
            a(f"- ⚠️ **警告**：{d['warning']}")
        if d.get("hard_guard"):
            a(f"- 🔒 **硬边界**：{d['hard_guard']}")
        a("")
        a("**输入特征**")
        a("")
        for x in d["inputs"]:
            a(f"- {x}")
        a("")
        a("**输出枚举**")
        a("")
        for x in d["outputs"]:
            a(f"- `{x}`")
        a("")
        a(f"**SOP 原文支撑**：{d['sop_quote']}")
        a("")
        a(f"　出处：{d['sop_src']}")
        a("")
    a("## 三、容差带（R1.3）")
    a("")
    a("| 键 | 名称 | 精确档 | 宽松档 | 证据 |")
    a("|---|---|---|---|---|")
    for t in TOLERANCES:
        nums = ", ".join(f"{k}={v}" for k, v in t.items()
                         if isinstance(v, (int, float)) and k not in ("tight", "loose"))
        a(f"| `{t['key']}` | {t['name']} | {t.get('tight','—')} | {t.get('loose','—')} | {t['evidence']} |")
    a("")
    for t in TOLERANCES:
        a(f"### `{t['key']}` {t['name']}")
        a("")
        for k, v in t.items():
            if k in ("key", "name"):
                continue
            a(f"- **{k}**：{v}")
        a(f"- **引用规则**：{', '.join('`'+x+'`' for x in used_tol.get(t['key'], [])) or '—'}")
        a("")
    a("## 四、⚠️ 本表裁定的一处口径冲突")
    a("")
    a("均线支撑容差有两个来源、两个数值：")
    a("")
    a("| 来源 | 数值 | 样本 | 性质 |")
    a("|---|---|---|---|")
    a("| 画像 §3.7(8) 5 例实盘核验 | **3~7%** | n=5，含 1 例被判「不精确」 | 人工读图时的**语义**容差 |")
    a("| 计划 §4.6③ + 9/11 K 线核验 | **≤2%** | 18 只个股、17 命中，精度 0.6~1.6% | 机械化执行时的**判定**容差 |")
    a("")
    a("**裁定（R1.3）**：两者不是矛盾，是**两个档**。机械化档取 **≤2%**（样本更一致、精度已知）；")
    a("语义档取 **≤5%**；**弃用 7% 上界**（来自 n=5 单案例估计，且太极实业一例偏差 4.45% 已被我们")
    a("自己判为「踩 5 日线不精确」）。")
    a("")
    a("## 五、待标定项（下一步应退出裁量层或收紧）")
    a("")
    a("- `vwap_tol = 0.3%` —— 引擎既有值（E6），**不是**从选手素材标定的；R2 拿到候选池 1m 后回标。")
    a("- `support_low` 个股档 1.5% / `resist_band` —— 只有指数级被核验过，个股级待核验。")
    a("- `D10`（趋势反包是否放量）—— 属「尚未标定」，标定后应退出裁量层。")
    a("- 反包「包住」的严格/宽松两口径 —— 未裁定，现取严格档（`close > prev_open`）。")
    a("- `washout_time` 的 30/60 —— 选手说的是自然月，参数是自然日，实现须明确取哪一个。")
    a("")
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
