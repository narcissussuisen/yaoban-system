# -*- coding: utf-8 -*-
"""R1.4 施工：人格层的「记忆」与「自述」。

产出四个文件 + 校验报告：
  yaoban-system/persona/memory/memory_v0.toml              记忆库（教训/案例/元教训/原话）
  yaoban-system/persona/memory/self_narrative_schema_v0.toml 自述 schema（字段 + 枚举 + 必填）
  yaoban-system/persona/memory/self_narrative_v0.toml      当前自述实例（结构化落盘）
  docs/PERSONA_MEMORY_v0.md                                人读版

设计要点
--------
1. **记忆不是日记**。每条 entry 必须给出「可执行的教训」（`lesson`）与「作用对象」
   （`applies_to` = SOP 规则 id 或裁量点 id）。裸案例不入库 —— 只入能被后续决策引用的记忆。
2. **`applies_to` 双向校验**：引用的 id 必须真实存在于 `sop_v0.toml` 或 `discretion_v0.toml`，
   否则构建失败。这样记忆层不会与规则层脱钩（同 R1.1/R1.2 的契约思路）。
3. **元教训（meta_lesson）单独一类**：关于「素材可信度/口径」的教训不用于交易，
   而用于**判断证据能不能信** —— 例如「选手自述的年份不可信」「公开候选池 ≠ 完整交易集」。
   它们不挂 `applies_to`（不对应任何交易规则），是 R3 LLM 的**证据纪律**。
4. **自述分两轨**，避免混淆：
   - `persona_view` / `persona_holding_stance` = **选手视角**（人格要复刻的东西，as-of 最后一次可观测）
   - `account_state` = **EvoAlpha 自己的账户**（50 万独立账本，9/14 起算）
   两者必须显式分开 —— 混在一起会让 LLM 把「选手持仓」误当「我们的持仓」。
5. 自述是**给人格/LLM 读的输入**，所以每个判断都要带 `confidence` 与 `open_questions`
   （即「我不知道什么」），否则 LLM 会把不确定当确定。
"""

import json
import pathlib
import sys
import tomllib
import traceback

ROOT = pathlib.Path(r"C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\EvoAlpha")
SOP = ROOT / "yaoban-system/persona/sop_v0.toml"
DISC = ROOT / "yaoban-system/persona/discretion_v0.toml"
OUTDIR = ROOT / "yaoban-system/persona/memory"
OUT_MEM = OUTDIR / "memory_v0.toml"
OUT_SCHEMA = OUTDIR / "self_narrative_schema_v0.toml"
OUT_SELF = OUTDIR / "self_narrative_v0.toml"
OUT_MD = ROOT / "docs/PERSONA_MEMORY_v0.md"
OUT_REPORT = OUTDIR / "_build_memory.report.txt"

KINDS = {
    "lesson": "可执行教训（含『下次该怎么做』）",
    "case": "正/反案例（含事实与结果，供类比推理）",
    "meta_lesson": "元教训（关于素材可信度/口径，用于判断证据能不能信，不对应交易规则）",
    "quote": "原话（人格语料，供措辞与语气复刻）",
}

# ══════════════════════════════════════════════════════ 记忆库
E = []


def M(mid, kind, title, content, source, evidence, applies_to=None, lesson="", tags=None,
      date=""):
    E.append(dict(id=mid, date=date, kind=kind, title=title, content=content,
                  lesson=lesson, applies_to=applies_to or [], evidence=evidence,
                  source=source, tags=tags or []))


# ── A. 可执行教训（从实盘案例里提炼的「下次该怎么做」）
M("MEM-001", "lesson", "兴民智通：一条上影线洗掉 70% 利润",
  "2022-06-22 收长上影（上影 3.45 倍实体、收 +1.14%），随后连拉 6 个涨停至 9.43。"
  "选手在冲高 5.6~5.7 卖出，后段涨至 9.43（+65%），自述「一条上影线洗掉 70% 利润」。",
  "v70@00:34（含实盘核验：sz002355 日线）", "E1",
  ["P3-ENTRY-01", "GEN-ENTRY-05"],
  lesson="长上影当天不是卖点也不是买点，是路标。**必须等第二/三天的放量反包确认**；"
         "没放量站上上影线高点就当杂波无视 —— 这条帮他避开 90% 的诱多陷阱。",
  tags=["仙人指路", "上影线", "过早离场"], date="2022-06-22")
M("MEM-002", "lesson", "太极实业：同一天里「及时止盈」与「没及时止盈」差 6 个百分点",
  "2026-06-05 大盘血雨腥风，选手早盘把振华科技、得邦照明先止盈；同日太极实业「早上没有及时止盈」，"
  "当日最高 +5.0%、收盘 −1.29%（落差 6pp）。",
  "v25（2026-06-05）", "E1",
  ["GEN-HOLD-21", "GEN-GATE-10"],
  lesson="卖出纪律的核心不是「选价位」，而是**大盘环境变了就无条件执行**。"
         "环境闸门（D1）一旦判定转弱，止盈动作要在早盘完成，不能等个股形态。",
  tags=["止盈", "环境闸门", "执行力"], date="2026-06-05")
M("MEM-003", "lesson", "欢瑞世纪 vs 远东股份：同一动作、两种结果，说明卖出是「资金调度优先」",
  "2026-09-07 同日止盈两只：欢瑞世纪卖 5.7404 → 此后 9/8 −4.78%、9/9 −9.32%（**规避 −17.1%**，教科书）；"
  "远东股份卖 20.5597 → 9/9 +10.02% 涨停至 24.93（**卖飞，少赚 +21.3%**）。"
  "形态差异：欢瑞是自 4.30 连拉至 6.09（+41.6%）的**高位第 4 根**；远东是横盘 1 日后小幅回调（**主升中继**）。",
  "2026-09-07 持仓截图（E0）+ 画像 §4 对照检验", "E0",
  ["GEN-HOLD-17", "GEN-HOLD-18"],
  lesson="卖出触发是**资金调度驱动**，不是逐票择时。故不能用「事后哪只涨了」来评价单笔卖出对错 —— "
         "评价单位必须是**组合**（这正是 §4.6 第④步「组合层」的实证依据）。",
  tags=["资金调度", "组合层", "卖飞"], date="2026-09-07")
M("MEM-004", "lesson", "皖维高新：破位后 3 个交易日才卖 —— 执行偏差要建容差带",
  "2026-06-11 明确「留意 7.5 的支撑」，当日收盘 7.41 已破位，但实际到 06-17 才卖 → **3 个交易日偏差**。",
  "画像 §4（2026-06-11 / 06-17）", "E1",
  ["GEN-HOLD-19", "GEN-HOLD-22"],
  lesson="⭐ **这条是双向的**：① 复刻人格时必须在评估侧给 3 日容差（`execution_lag` 属**诊断轨专用**）；"
         "② 但**生产执行不得套用该容差** —— 生产必须当场了结（见 MEM-005 粤传媒）。"
         "混用会系统性放大回撤。",
  tags=["容差带", "执行偏差", "诊断轨"], date="2026-06-11")
M("MEM-005", "lesson", "粤传媒：买入次日即砍 —— 反证「止损被动 ≠ 止损拖延」",
  "2026-09-08 买 @10.103 → 09-09 卖 @9.7100，持有 **1 个交易日**，−1,572.12（−3.890%）。"
  "同日另有协鑫能科 −1,930.52（−5.505%）。而茶花股份在 −4.92% 时却继续持有 6 个交易日。",
  "2026-09-09 持仓截图（E0）", "E0",
  ["GEN-HOLD-22", "GEN-HOLD-23"],
  lesson="止损锚是**结构（均线 + 量能）**，不是百分比。同一账户内「烽火 −7.4% 割」与「茶花 −4.9% 拿」并存 —— "
         "差异只来自结构是否破坏。**结构一旦明确破坏就快速了结，不等百分比**。",
  tags=["止损", "结构锚", "非对称系统"], date="2026-09-09")
M("MEM-006", "lesson", "茶花股份完整生命周期：决策依据是结构位置，不是盈亏数字",
  "8/28 建仓 @19.036（涨停回踩低吸）→ 9/2 −4.39% → 9/4 **−4.92%（最低点）** 未割 → 9/7 缩量横盘未割 → "
  "9/8 −3.08% 未割 → **9/9 反弹 +3.74%（收 19.140）当日清仓 @19.1501，实现 +342.30（+0.599%）** → 9/10 回落至 18.820。",
  "2026-09-09 持仓截图 + K 线核验（E0/E1）", "E0",
  ["GEN-HOLD-19", "GEN-HOLD-36", "P1-HOLD-02"],
  lesson="① **结构未破 → 持有**（哪怕 −4.92%）；② **反弹到支撑上方 → 了结** —— "
         "把反弹视为**离场机会**而非加仓机会；③ 老仓的定位是「待了结」不是「看好加仓」。"
         "这条同时解释为什么「持有 8 个交易日」与「持股周期 3 天」不矛盾（后者是节奏，前者受结构支配）。",
  tags=["持有", "结构锚", "了结时机"], date="2026-09-09")
M("MEM-007", "lesson", "高新发展：洗盘 4 日 −8.2% 后单日 +8.01% —— 「为什么舍不得走」的正面样本",
  "9/3 高 57.97 → 9/8 收 53.20（洗盘 4 日，最大回撤 −8.2%）→ **9/9 单日 +8.01% 涨回**，成本 54.806 转浮盈清仓。"
  "选手 9/9 13:58 发帖复盘：「为什么舍不得走，为什么不及时止损……再次证明顺势而为的重要性」。",
  "2026-09-09 持仓截图 + 选手原话", "E0", ["GEN-HOLD-22", "GEN-HOLD-34"],
  lesson="拿住的前提是**结构未破 + 量能未恶化**（缩量洗盘），不是「看好」也不是「扛单」。"
         "⚠️ 同时注意：该帖发布于 11:08 已清仓之后 —— **复盘贴 ≠ 持仓快照**。",
  tags=["洗盘", "持有", "播报时点陷阱"], date="2026-09-09")
M("MEM-008", "lesson", "百花医药：板块情绪分歧 → 冲高就止盈（板块情绪与个股卖出的联动）",
  "2026-08-26 选手点评「板块情绪分歧，冲高就选择了止盈落袋」，印证当日 10:16 的「龙头炸板，有赚就走」。",
  "v73/v74（2026-08-26）+ `NEW_VIDEOS_READ_2026-08-26.md`", "E1",
  ["GEN-HOLD-33", "GEN-HOLD-20", "GEN-HOLD-30"],
  lesson="个股卖点要叠加**板块情绪**：同板块出现分歧/龙头炸板 → 本仓降级处理（有赚就走）。"
         "这是「板块退潮先行」在持仓侧的执行形态。",
  tags=["板块联动", "炸板", "止盈"], date="2026-08-26")
M("MEM-009", "lesson", "恒宝股份：消息刺激的题材拉升，有赚就走（哪怕 −0.96% 也止盈）",
  "2026-08-25 恒宝股份「消息刺激的题材拉升有赚就走了」，卖出时涨幅 −0.96%（后续最高冲到 3 个多点）。",
  "`NEW_VIDEOS_READ_2026-08-26.md`（v4，2026-08-25）", "E1",
  ["GEN-HOLD-14", "GEN-HOLD-17"],
  lesson="**消息刺激驱动的题材拉升 ≠ 趋势票**，其持有逻辑是「事件性」，事件兑现即走；"
         "不能用趋势票的均线纪律去持有它（会错过卖点）。",
  tags=["消息题材", "快进快出", "卖点分型"], date="2026-08-25")
M("MEM-010", "lesson", "山东玻纤 vs 沃特股份 vs 金能科技：分时质量三档的真实结果",
  "2026-09-10 四只候选按分时质量分级并**100% 执行**：① 山东玻纤（有量+一步步向上）→ 买入 6500 股 @16.875，"
  "盘中触板 +9.98%、收 +6.00%；② 沃特股份（无量、欠缺力度）→ 买入 4000 股 @26.838，收 0.00%（盘中曾 +5.81%）；"
  "③ 金能科技 / 南华期货（第一波回落破均价线）→ **一股未买**，当日 −5.71% / +1.80%。",
  "2026-09-10 持仓截图 + 候选池（E0/E2）", "E0",
  ["GEN-ENTRY-06", "GEN-ENTRY-04"],
  lesson="① 一票否决（破均价线）**执行率 100%**，规避了 −5.71%；② 但分级只用于**排序与取舍**，"
         "**未体现为仓位权重差异**（山玻 11.3 万 vs 沃特 10.8 万，市值相近）→ 复刻时不要自行发明权重。",
  tags=["分时质量", "一票否决", "执行率"], date="2026-09-10")
M("MEM-011", "lesson", "海通发展：在 +3.21% 时预告「今日吃板」→ 战法具盘中前瞻性",
  "2026-09-09 11:08 选手发海通发展 K 线图（现价 14.48 / +3.21%），标注「战法筛选 海通发展 今日吃板了」，"
  "当日收盘涨停 +9.98%；该仓 9/10 以 **+13.682%（+7,981.92）** 了结，为迄今单笔最高。"
  "同日三只候选（金帝/武汉凡谷/金富科技）均破均价线被否决、0 只涨停。",
  "2026-09-09 11:08 持仓与 K 线截图（E0）", "E0",
  ["GEN-ENTRY-04", "GEN-ENTRY-06", "GEN-MAIN-04"],
  lesson="「筛选 ≠ 执行」：**只买唯一未被警示的那一只**不是碰巧。"
         "候选池价值在于**前瞻性提示**，执行取决于入场过滤是否通过。",
  tags=["前瞻性", "筛选≠执行", "单笔最佳"], date="2026-09-09")
M("MEM-012", "lesson", "每涨一点卖一点、每过一周卖一点 —— 老仓用时间维度摊薄",
  "v26@02:19-02:46 原文：`每涨一点卖一点，每过一周卖一点`。",
  "v26（2026-06-07 八误区那期）", "E1", ["GEN-HOLD-36", "GEN-HOLD-07"],
  lesson="分批离场有**两个维度**：价格维度（赚 10~20% 走 1/3~1/2）与**时间维度**（每周卖一点）。"
         "对结构未破但迟迟不涨的**老仓**，时间维度更适用 —— 这解释茶花为何能持 8 个交易日仍属于「在执行纪律」。",
  tags=["分批离场", "时间维度", "老仓"], date="2026-06-07")
M("MEM-013", "lesson", "加仓只用「回调缩量」这一种理由",
  "v36@01:40：`洗盘回调 1-2 天缩量（加仓点）`；v15@00:43：`永不追加亏损仓位`。",
  "v36@01:40 / v15@00:43", "E1", ["GEN-SIZE-16", "GEN-SIZE-07", "GEN-SIZE-04"],
  lesson="两条不矛盾，合起来是一条完整规则：**回调中缩量（未亏、结构未破）可加；亏损中一律不可加**。"
         "加仓是**结构事件**而非**盈亏事件** —— 与止损同源（都锚结构，不锚百分比）。",
  tags=["加仓", "缩量", "结构锚"], date="2026-08-15")

# ── B. 案例（供类比推理，不直接给「该怎么做」）
M("MEM-101", "case", "依顿电子：9/11 被买入的正是形态最标准的那只",
  "9/9 倍量涨停（vr 2.24）→ 9/10 缩量回踩 → 9/11 再上攻。9/11 候选 3 只中只买依顿电子，"
  "而它正是 K 线核验里「三段式最完整」的一只。",
  "2026-09-11 持仓截图 + 画像", "E0", ["GEN-DRAGON-08", "GEN-DRAGON-10"],
  lesson="取舍标准**高度可预测** → 若候选池按形态质量排序，可大幅逼近其真实执行集。",
  tags=["形态质量", "可预测性"], date="2026-09-11")
M("MEM-102", "case", "四川黄金：5 日线企稳 + 5/10/20 日线向上发散（上升回档教科书样本）",
  "2026-08-24 现价 56.60（+9.97% 接近涨停），成本 52.626；5 日线企稳，符合上升回档战法。"
  "次日（8/25）止盈落袋。",
  "`NEW_VIDEOS_READ_2026-08-26.md`（2026-08-24/25）", "E1",
  ["P1-ENTRY-02", "P1-DRAGON-10"], lesson="「缩量回调 + 均线多头 + 支撑企稳」三件套齐全时，进场后爆发力最强。",
  tags=["上升回档", "教科书案例"], date="2026-08-24")
M("MEM-103", "case", "协鑫能科：9/1 涨停即判「游资拉升、短线类型」→ 9/9 破位 −5.505%",
  "9/1 涨停（+9.97%）时选手在视频里标注「符合上升回档战法，**今天是游资拉升，短线类型**」；"
  "9/4 回接 → 9/9 破位清仓 −1,930.52（−5.505%）。",
  "2026-09-01 视频解析 + 09-09 持仓截图", "E1",
  ["GEN-HOLD-17", "GEN-HOLD-30", "P1-HOLD-01"],
  lesson="**入场时就判定票型（趋势票 / 短线票）**，并据此选卖出规则 —— 票型判错会导致用错纪律。"
         "「游资拉升」类以板块情绪与破位为锚，不用均线慢纪律。",
  tags=["票型判定", "游资", "破位"], date="2026-09-01")
M("MEM-104", "case", "共进股份：图形逻辑被破 + 放量 + 分时反弹乏力 → 割",
  "9/2 开盘跳空破倍量涨停柱 17.88 → 反弹乏力至 18.01 → 卖 17.98（−0.86%）。"
  "触发条件是**三重确认**（结构破 + 放量 + 分时乏力）。",
  "2026-09-02 持仓截图（E0）", "E0",
  ["GEN-HOLD-22", "GEN-ENTRY-04", "GEN-HOLD-29"],
  lesson="止损需要**多重确认**而非单点触发；但一旦三重齐全，动作要快（不等百分比）。",
  tags=["三重确认", "止损"], date="2026-09-02")
M("MEM-105", "case", "茶花 8/31-9/2 三连缩量（vr 0.58/0.71/0.48）仍在 MA10/MA20 上方 → 不止损",
  "缩量下跌 = 惜售 + 主力未走 + 结构未破 → 持仓观察。",
  "2026-09-02 持仓截图（E0）", "E0", ["P1-DRAGON-16", "GEN-HOLD-11"],
  lesson="**缩量下跌与放量下跌要分开处理**：缩量是安全信号（主力未走），放量才是危险信号。",
  tags=["缩量", "真假洗盘"], date="2026-09-02")
M("MEM-106", "case", "德明利 vs 税友股份：承接力强弱的分时案例（v62 独家）",
  "德明利成交 173.55 亿、换手 25.61%（筹码疯狂换手）+ 龙虎榜两家席位合计净卖出 4.44 亿 → "
  "主力出逃意愿不减 → 降低该方向预期；同时低位方向接力（税友股份 2 连板）。",
  "v62（2026-07-31）+ v4_materials/E§4.4", "E1",
  ["GEN-MAIN-06", "GEN-ENTRY-06", "GEN-MAIN-04"],
  lesson="承接力可用「换手率爆发 + 龙虎榜净卖出」组合量化 —— 这是「主力出逃」的可观测代理。",
  tags=["承接力", "龙虎榜", "高低切换"], date="2026-07-31")
M("MEM-107", "case", "北证脉冲：主线势竭的报警器（2026-06-05 首次点出）",
  "原文：「当一轮主线出现涨不动、无法上涨的时候，大概率北证会有表现。而北证脉冲结束之后，就轮到新股了……"
  "若创不了新高，切换就要正式开始。」",
  "v25（2026-06-05）", "E1", ["GEN-GATE-08"], lesson="北证 50（899050）相对主板的超额脉冲可作为独立 regime 特征。",
  tags=["regime", "北证脉冲", "风格切换"], date="2026-06-05")
M("MEM-108", "case", "9/11 大盘闸门：支撑 3850 与实际最低 3852.03 的精确吻合",
  "选手 10:22 说「指数很弱，支撑在 3850 点，可以选择观望不操作」。该 3850 并非整数关，"
  "而是 8/24–8/25 前低区（3855.35 / 3850.86）；当日实际最低 **3852.03**，随后止跌回升。"
  "当日同时跌破 MA5/MA10/MA20（3924/3940/3933）。",
  "2026-09-11 10:22 盘面观点（E2）+ K 线核验", "E2",
  ["GEN-GATE-09", "GEN-GATE-10"], lesson="支撑位取**前低区域**而非整数关；且「指数很弱」的定性有均线结构支撑，不是感觉。",
  tags=["支撑位", "前低区", "环境闸门"], date="2026-09-11")

# ── C. 元教训（关于素材可信度与口径 —— 供 LLM 判断「证据能不能信」）
M("MEM-201", "meta_lesson", "选手自述的年份/日期会记错，必须外部核验",
  "① 兴民智通案例自述「2024 年 6 月 23 号」，实测为 **2022-06-22**（年份记错，形态与连板数全对）；"
  "② 方正科技「19 号放量拉升」实为 4/17（4/19 是周日）。",
  "v70 + 画像 §3.6/§3.7(8)", "E1", [],
  lesson="**形态细节可信、年份/日期不可信**。引用时一律以行情数据核验日期，不要直接采信口播时间。",
  tags=["证据纪律", "日期核验"], date="2022-06-22")
M("MEM-202", "meta_lesson", "净值与胜率声明不可作真值（只有离散决策可核验）",
  "可解析净值点仅 **28 天（38.4%）**；7 月 **44 天断裂**；本金口径在 3W/5W 间摇摆；天数出现倒退；"
  "「6 月胜率 91%」**只出现在视频标题、正文未复述**。"
  "另：04-17 口述「本金 3W」而 `PLAYER_EVIDENCE_FULL` 记 04-17 总资产 **44,384**（非 3W）。",
  "画像 §4.4 硬约束 3 + `PLAYER_EVIDENCE_FULL.md`", "E5", [],
  lesson="**可核验的只有：离散决策标签（候选/执行/持仓截图）+ 单笔盈亏 + K 线**。"
         "连续净值轨只能标为**弱证据**，不得用于计算其收益率或作为复利基准。",
  tags=["证据纪律", "净值口径"], date="2026-09-11")
M("MEM-203", "meta_lesson", "公开候选池 ≠ 完整交易集（跨 3 个交易日稳定出现）",
  "未公开但买入：9/8 粤传媒 @10.103、9/9 诺普信 @10.903、9/11 道明光学 @9.603。"
  "9/11 更出现**双向偏离**：候选池 3 只只买 1 只（漏买 2 只）+ 池外多买 1 只。",
  "2026-09-08 / 09-09 / 09-11 持仓截图（E0）", "E0",
  ["GEN-DRAGON-11", "GEN-DRAGON-04"],
  lesson="**仅以公开候选池为训练样本会系统性漏掉真实交易** → 必须用持仓截图交叉校验。"
         "这是 R3.3 诊断轨（漏选/误选统计）的前提。",
  tags=["证据纪律", "样本完整性"], date="2026-09-11")
M("MEM-204", "meta_lesson", "「吃板」有两层含义，不可当成交记录",
  "第一层：**盘中触及涨停价**即可称吃板（杭电股份 9/8 最高精确 +10.01%、收盘回落至 +8.52%，仍称「吃板啦」）。"
  "第二层：**「吃板」= 战法筛选命中，不等于个人持仓** —— 9/8 播报「一天收获 3 只板」，"
  "实际只持有 2 只（集泰、杭电），泸天化表述为「用战法**筛选**出来」。",
  "2026-09-08 吃板播报 + 持仓截图", "E2", ["GEN-DRAGON-06"],
  lesson="核对播报须**双层拆解**：① 战法是否命中（命中率高，可逐条 K 线核验）"
         "② 是否转化为持仓（**≠100%**）。",
  tags=["证据纪律", "口径陷阱"], date="2026-09-08")
M("MEM-205", "meta_lesson", "复盘贴 ≠ 持仓快照（播报时点不等于持仓状态）",
  "9/9 13:58 选手发帖讲高新发展「为什么舍不得走」，但 11:08 持仓截图显示该票**当日上午已清仓**（+908.14）。"
  "该帖是回顾性方法论教学，发布时已无该持仓。",
  "2026-09-09 持仓截图 + 选手帖", "E0", [],
  lesson="帖子/播报是**教学**，不是持仓状态。判定持仓必须以截图为准。",
  tags=["证据纪律", "时点陷阱"], date="2026-09-09")
M("MEM-206", "meta_lesson", "OCR 字幕有错字，个股名必须外部核实代码",
  "已知错字：「博达合金」← 博威合金、「苍位」← 仓位。v4_materials 对明显误识别处标了 `[OCR疑似]`。",
  "v4_materials 引用规则 + 画像", "E1", [],
  lesson="**个股名一律外部核实代码**（本会话已遵循：所有股票代码均经外部核验，不凭记忆或字幕写入）。",
  tags=["证据纪律", "OCR"], date="2026-09-11")
M("MEM-207", "meta_lesson", "命中的是「行为」不等于有「超额收益」——已证伪项清单",
  "「分时跌破均价线 → 不玩」命中选手行为 **94.4%**，但全市场反事实**显著负 alpha**"
  "（被否决组前向 5 日均值 0.872% vs 通过组 0.366%，Welch p=0.0013，单调反向）→ 判为**行为特征/心理偏好**。"
  "「涨停回踩低吸」独立运行影子盘 **−33.7%**（113 笔）；同期「上升回档低吸」+22.4%。",
  "evoalpha_all/docs_archive_20260915/ITERATION_2026-09-11.md + 画像 §4", "E6",
  ["GEN-ENTRY-04", "P4-NOTE-01"],
  lesson="⭐⭐ **架构级分离：行为复刻 ≠ 收益来源**。凡「命中率高」的规则，仍须过"
         "「反事实方向 + 统计显著」双门槛才能作为收益过滤器；否则**只保留为行为复刻/诊断项**。",
  tags=["证据纪律", "证伪", "架构分离"], date="2026-09-11")

# ── D. 原话（人格语料）
M("MEM-301", "quote", "买卖逻辑必须一致",
  "「买卖逻辑必须保持一致。买入逻辑消失的那一刻，就是卖出的最佳时机。」"
  "「很多人不知道怎么卖，是因为没想清楚自己当初为什么买。」"
  "「你买它的理由是什么，卖它的理由就是什么。」",
  "v20（2026-05-29）", "E1", ["GEN-HOLD-12", "GEN-HOLD-13"])
M("MEM-302", "quote", "最优卖点的定义",
  "「最优卖点不是卖在最高价，而是**在确定性走弱时离场**。」"
  "「什么叫确定性走弱？不是『感觉要跌』，而是客观信号出现了……"
  "你的交易规则替你做了判断，而不是你在猜顶。」",
  "v20（2026-05-29）", "E1", ["GEN-HOLD-03", "GEN-HOLD-22"])
M("MEM-303", "quote", "止损哲学",
  "「割肉本身没有错，错的是被恐惧支配的盲目割肉；持仓也不是死扛，关键是要有客观的判断标准。**我的标准就是趋势线。**」"
  "「止损不是为了赚钱，是为了让你不会从小亏变成大亏。」"
  "「到了就走，不加、不等、不幻想。」",
  "v30（2026-09-08）/ v20（2026-05-29）", "E1", ["GEN-HOLD-22", "P1-HOLD-02"])
M("MEM-304", "quote", "纪律前置",
  "「买卖之前先定好自己的规则。什么情况持有，什么情况离场，把标准写在前面。」"
  "「不要跟市场讲道理，要跟市场讲纪律 —— 有量才买，跌破支撑直接走，该休息的时候休息。」",
  "v30（2026-09-08）/ v52（2026-07-17）", "E1", ["GEN-HOLD-12", "GEN-GATE-10"])
M("MEM-305", "quote", "心法层",
  "「恐惧的真正来源，不是高度，是不知道这个高度下面有没有人托着。差别在于——你知道不知道自己在干什么。」"
  "「你不可能又想吃主升浪，又想避开所有回调。」"
  "「涨跌，表面看是 K 线，本质是主力意图的投射。你读懂了意图，就看到了剧本。」"
  "「盈亏不是能力，是周期。」",
  "v33（2026-06-14）", "E1", ["GEN-HOLD-34"])
M("MEM-306", "quote", "利润与风险观",
  "「利润落袋才是我追求的！」「没有不好的行情，只有不好的操作。」"
  "「市场讲故事越满，我们越要冷静，不贪最后一段泡沫，吃鱼身不吃鱼尾巴。」"
  "「高位一致乐观 = 未来一致崩盘。」",
  "v25（2026-06-05）/ v60（2026-07-29）", "E1",
  ["GEN-HOLD-25", "GEN-SIZE-11"])

# ══════════════════════════════════════════════════════ 自述 schema
SCHEMA = {
    "version": "self-narrative-schema-v0",
    "purpose": "人格层的「当前状态自述」契约：由决策环每写入一次，供 R3 LLM 裁量时作为输入引用。"
               "目的是让 LLM 知道「人格此刻怎么看市场、怎么看待持仓、以及**自己不知道什么**」。",
    "tracks": {
        "persona_view": "选手视角的市场观点（人格要复刻的对象，as-of 最后一次可观测）",
        "persona_holding_stance": "选手视角的持仓心态（人格要复刻的对象）",
        "account_state": "**EvoAlpha 自己的账户**（与上面两轨严格分离，避免 LLM 把选手持仓误当我们持仓）",
    },
    "fields": {
        "meta.as_of": {"type": "date", "required": True, "note": "本自述对应的观测日"},
        "meta.built_at": {"type": "date", "required": True},
        "meta.writer": {"type": "enum", "values": ["manual_seed", "R3_shadow", "R4_live"],
                        "required": True, "note": "谁写的：R1.4 种子 / R3 影子盘 / R4 实盘"},
        "persona_view.regime": {"type": "enum", "values": ["strong", "neutral", "weak"],
                                "required": True, "maps_to": "GEN-SIZE-02 / [risk].regime_position_cap"},
        "persona_view.regime_note": {"type": "text", "required": True},
        "persona_view.index_vs_ma": {"type": "text", "required": True,
                                     "note": "相对 MA5/10/20/60 的位置"},
        "persona_view.key_support": {"type": "list[number]", "required": True,
                                     "maps_to": "GEN-GATE-09（前低区域，非整数关）"},
        "persona_view.key_resistance": {"type": "list[number]", "required": False,
                                        "maps_to": "GEN-HOLD-18"},
        "persona_view.turnover_state": {"type": "text", "required": True,
                                        "maps_to": "GEN-GATE-04"},
        "persona_view.sentiment": {"type": "text", "required": True,
                                   "maps_to": "GEN-GATE-01/02/17（含跌侧）"},
        "persona_view.main_lines": {"type": "list[text]", "required": True, "maps_to": "GEN-MAIN-01"},
        "persona_view.avoid": {"type": "list[text]", "required": True, "maps_to": "GEN-DRAGON-02/03"},
        "persona_view.confidence": {"type": "enum", "values": ["high", "medium", "low"],
                                    "required": True,
                                    "note": "对**本自述整体**的置信度；低置信时 LLM 应倾向于保守档"},
        "persona_view.open_questions": {"type": "list[text]", "required": True,
                                        "note": "⭐ 显式写「我不知道什么」—— 否则 LLM 会把不确定当确定"},
        "persona_holding_stance.as_of": {"type": "date", "required": True},
        "persona_holding_stance.positions": {"type": "list[table]", "required": True,
                                             "fields": ["sym", "name", "cost", "entry_logic",
                                                        "hold_condition", "exit_condition",
                                                        "stance"],
                                             "note": "每只持仓必须写清 entry_logic（买入逻辑）与 "
                                                     "exit_condition（离场条件）—— 这是 GEN-HOLD-13 的输入"},
        "persona_holding_stance.stance_note": {"type": "text", "required": True},
        "account_state.as_of": {"type": "date", "required": True},
        "account_state.status": {"type": "enum", "values": ["flat", "holding"], "required": True},
        "account_state.equity_basis": {"type": "number", "required": True},
        "account_state.start_date": {"type": "date", "required": True},
        "account_state.positions": {"type": "list[table]", "required": True,
                                    "fields": ["sym", "name", "qty", "cost", "entry_logic",
                                               "exit_condition"]},
        "account_state.positions_note": {"type": "text", "required": True},
    },
    "hard_rules": [
        "persona_* 与 account_state 不得交叉引用（选手持仓 ≠ 我们的持仓）。",
        "open_questions 不得为空 —— 必须至少写出一条不确定性。",
        "account_state 的持仓若为空，status 必须为 flat 且 positions 为空数组。",
        "本条自述不产生任何交易指令；它是 LLM 裁量的**输入**，不是输出。",
    ],
}

# ══════════════════════════════════════════════════════ 自述实例（种子）
SELF = {
    "meta": {
        "version": "self-narrative-v0",
        "as_of": "2026-09-11",
        "built_at": "2026-09-12",
        "writer": "manual_seed",
        "note": "R1.4 种子：观测日 = 最后一次有完整素材的交易日（2026-09-11）。"
                "此后由 R3 影子盘 / R4 实盘每日覆写。",
    },
    "persona_view": {
        "regime": "weak",
        "regime_note": "「指数很弱」—— 当日同时跌破 MA5/MA10/MA20（3924/3940/3933）；"
                       "选手 10:22 主动明示「可以选择观望不操作，等市场明朗再说」",
        "index_vs_ma": "收盘低于 MA5/MA10/MA20；支撑 3850 = 8/24–8/25 前低区",
        "key_support": [3850],
        "key_resistance": [3924, 3940],
        "turnover_state": "1.65 万亿 = 年内新地量（「地量见地价」）；此前 9/10 亦记 1.65 万亿",
        "sentiment": "很弱：需同时看涨侧与跌侧（涨停家数 + 跌停家数 + 跌幅>7% 家数）",
        "main_lines": ["文传（16 只齐涨停，与科技形成对冲）"],
        "avoid": ["科技全线（MLCC/复合铜箔/存储芯片/PCB/先进封装/CPO 等 22 个细分全绿）",
                  "高位漂"],
        "confidence": "medium",
        "open_questions": [
            "地量是否见地价：1.65 万亿为年内新地量，但「连续缩量 3 天内必选方向」的方向未知",
            "「科技弱→看文传」是板块轮动反向判断，属**真假设但未回测验证**（见 GEN-ENTRY-13）",
            "自身 50 万账本 9/14 才起算，尚无净值可参照",
            "本自述的 regime 判定只用了均线结构与量能，未接入完整的情绪温度计口径（GEN-GATE-15 待实现）",
        ],
    },
    "persona_holding_stance": {
        "as_of": "2026-09-09",
        "positions": [
            dict(sym="603618", name="杭电股份", cost=38.312,
                 entry_logic="趋势票（「属于趋势走法，很少走连板，涨涨调调」）；9/8 买入 1000 股 @38.312",
                 hold_condition="趋势线未有效跌破（5 日线短线 / 10 日线生命线 / 20 日线中期）",
                 exit_condition="趋势破位立即离场；或拉升兑现时顺势止盈",
                 stance="9/9 净减仓日唯一保留的持仓"),
        ],
        "stance_note": "选手 9/9 为**净减仓日**：清仓 5 只（茶花/协鑫/高新/集泰/粤传）、"
                       "仅新进 2 只（海通发展/诺普信）、保留杭电 1 只，持仓 6 → 3 只，仓位降至 43.4%。"
                       "按票型分档：**趋势票看均线结构，短线票看隔天强弱**，两者判据不同不可混用。"
                       "⚠️ 本轨是**选手的持仓心态**，与下方 account_state 严格分离。",
    },
    "account_state": {
        "as_of": "2026-09-12",
        "status": "flat",
        "equity_basis": 500000,
        "start_date": "2026-09-14",
        "positions": [],
        "positions_note": "EvoAlpha 独立主账本（R0.2）：start_cash=500000、start_date=2026-09-14、"
                          "空仓、risk_state 熔断已清零（切换前为 paused=true / drawdown=-10.64 / "
                          "position_multiplier=0.0）。首批成交预计在 2026-09-14（周一）。"
                          "⚠️ **本账本与选手账户无关**，不得用选手的仓位/净值填充本轨。",
    },
}


# ══════════════════════════════════════════════════════ 生成
def tv(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(tv(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {tv(x)}" for k, x in v.items()) + "}"
    if v is None:
        return '""'
    s = (str(v).replace("\\", "\\\\").replace('"', '\\"')
         .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{s}"'


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    sop = tomllib.loads(SOP.read_text(encoding="utf-8"))
    disc = tomllib.loads(DISC.read_text(encoding="utf-8"))
    rule_ids = {r["id"] for r in sop["rule"]}
    disc_ids = {d["id"] for d in disc["discretion"]}
    valid_ref = rule_ids | disc_ids

    log = []
    lw = log.append
    errs, warns = [], []

    # ── 校验记忆
    seen = set()
    for e in E:
        if e["id"] in seen:
            errs.append(f"重复 id: {e['id']}")
        seen.add(e["id"])
        if e["kind"] not in KINDS:
            errs.append(f"{e['id']}: 非法 kind {e['kind']}")
        if not e["source"].strip():
            errs.append(f"{e['id']}: 缺出处")
        if not e["evidence"].startswith("E"):
            errs.append(f"{e['id']}: 非法证据等级 {e['evidence']}")
        if e["kind"] == "lesson" and not e["lesson"].strip():
            errs.append(f"{e['id']}: kind=lesson 但 lesson 为空（裸案例不应入 lesson 类）")
        # applies_to 双向校验
        for ref in e["applies_to"]:
            if ref not in valid_ref:
                errs.append(f"{e['id']}: applies_to 引用了不存在的 id {ref}")
        # meta_lesson **可以**挂 applies_to —— 它常是某条规则的由来（如「公开候选池 ≠ 完整交易集」
        # 正是 GEN-DRAGON-11 的存在理由）。但若挂了规则却不属「证据纪律」范畴，才值得提醒。
        if e["kind"] == "meta_lesson" and e["applies_to"] and "证据纪律" not in e["tags"]:
            warns.append(f"{e['id']}: meta_lesson 挂了 applies_to 但未标「证据纪律」标签")
        for t in e["tags"]:
            pass

    # ── 校验自述实例 vs schema
    f = SCHEMA["fields"]

    def get(d, path):
        cur = d
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    for path, spec in f.items():
        if not spec.get("required"):
            continue
        val = get(SELF, path)
        if val is None:
            # 允许顶层容器通过子字段存在
            if any(p.startswith(path + ".") for p in f):
                continue
            errs.append(f"self_narrative: 缺必填字段 {path}")
            continue
        if spec["type"] == "enum" and val not in spec["values"]:
            errs.append(f"self_narrative: {path}={val!r} 不在枚举 {spec['values']}")
        if spec["type"] == "list[text]" and not isinstance(val, list):
            errs.append(f"self_narrative: {path} 应为列表")

    if not SELF["persona_view"]["open_questions"]:
        errs.append("self_narrative: open_questions 不得为空（hard_rule）")
    acc = SELF["account_state"]
    if acc["status"] == "flat" and acc["positions"]:
        errs.append("self_narrative: status=flat 但 positions 非空")
    # 两轨分离
    for k in ("sym", "name"):
        for p in acc["positions"]:
            if p.get(k) in [x.get(k) for x in SELF["persona_holding_stance"]["positions"]]:
                warns.append(f"self_narrative: {k}={p.get(k)} 同时出现在 persona_ 与 account_state（应确认是否有意）")

    # ── 输出记忆库
    with OUT_MEM.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# EvoAlpha 人格层 · 记忆库 v0（R1.4）\n")
        fh.write("# 生成器：yaoban-system/tools/build_persona_memory.py（勿手改）\n")
        fh.write("# kind: " + " | ".join(f"{k}={v}" for k, v in KINDS.items()) + "\n\n")
        fh.write("[meta]\n")
        fh.write('version = "memory-v0"\n')
        fh.write('built_at = "2026-09-12"\n')
        fh.write(f"count = {len(E)}\n")
        fh.write('contract = "每条 entry 必须带 source + evidence；kind=lesson 必须带可执行 lesson；'
                 'applies_to 引用的 id 必须真实存在于 sop_v0.toml 或 discretion_v0.toml"\n\n')
        for e in E:
            fh.write("[[entry]]\n")
            for k in ("id", "date", "kind", "title", "content", "lesson",
                      "applies_to", "evidence", "source", "tags"):
                fh.write(f"{k} = {tv(e.get(k, ''))}\n")
            fh.write("\n")

    # ── 输出 schema
    with OUT_SCHEMA.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# EvoAlpha 人格层 · 自述 schema v0（R1.4）\n")
        fh.write("# 生成器：yaoban-system/tools/build_persona_memory.py（勿手改）\n\n")
        fh.write("[meta]\n")
        fh.write(f'version = "{SCHEMA["version"]}"\n')
        fh.write(f'purpose = {tv(SCHEMA["purpose"])}\n\n')
        for tr, desc in SCHEMA["tracks"].items():
            fh.write("[[track]]\n")
            fh.write(f'name = "{tr}"\n')
            fh.write(f'desc = {tv(desc)}\n\n')
        for path, spec in f.items():
            fh.write("[[field]]\n")
            fh.write(f'path = "{path}"\n')
            for k, v in spec.items():
                fh.write(f"{k} = {tv(v)}\n")
            fh.write("\n")
        for hr in SCHEMA["hard_rules"]:
            fh.write("[[hard_rule]]\n")
            fh.write(f"rule = {tv(hr)}\n\n")

    # ── 输出自述实例
    def dump_self(d, fh, prefix=""):
        scalars = {k: v for k, v in d.items() if not isinstance(v, (dict, list))}
        sub = {k: v for k, v in d.items() if isinstance(v, dict)}
        lists = {k: v for k, v in d.items() if isinstance(v, list)}
        if prefix:
            fh.write(f"[{prefix}]\n")
        for k, v in {**scalars, **lists}.items():
            fh.write(f"{k} = {tv(v)}\n")
        fh.write("\n")
        for k, v in sub.items():
            dump_self(v, fh, f"{prefix}.{k}" if prefix else k)

    with OUT_SELF.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# EvoAlpha 人格层 · 自述当前实例 v0（R1.4 种子）\n")
        fh.write("# 生成器：yaoban-system/tools/build_persona_memory.py（勿手改）\n")
        fh.write("# ⚠️ 这是 LLM 裁量的**输入**，不是交易指令。\n\n")
        for k, v in SELF.items():
            dump_self(v, fh, k)

    # ── 报告
    lw("=" * 80)
    lw("R1.4 记忆与自述 构建报告")
    lw("=" * 80)
    lw(f"记忆条数            : {len(E)}")
    lw(f"  lesson           : {sum(1 for e in E if e['kind']=='lesson')}")
    lw(f"  case             : {sum(1 for e in E if e['kind']=='case')}")
    lw(f"  meta_lesson      : {sum(1 for e in E if e['kind']=='meta_lesson')}")
    lw(f"  quote            : {sum(1 for e in E if e['kind']=='quote')}")
    lw(f"证据等级分布        : " + ", ".join(
        f"{k}={sum(1 for e in E if e['evidence']==k)}" for k in sorted({e['evidence'] for e in E})))
    lw(f"引用到的规则/裁量点 : {len({r for e in E for r in e['applies_to']})} 个")
    lw(f"自述 schema 字段数  : {len(f)}")
    lw(f"ERR {len(errs)} / WARN {len(warns)}")
    for x in errs:
        lw(f"  ERR  {x}")
    for x in warns:
        lw(f"  WARN {x}")
    lw("")
    lw("── 记忆 → 规则映射覆盖 ──")
    covered = sorted({r for e in E for r in e["applies_to"]})
    lw(f"  {' '.join(covered)}")
    lw("")
    lw("── 未被任何记忆引用的规则（正常，规则不必都有案例）──")
    lw(f"  共 {len(rule_ids - set(covered))} 条 / 总 {len(rule_ids)} 条")
    OUT_REPORT.write_text("\n".join(log), encoding="utf-8")

    # ── 人读 MD
    write_md(covered, len(rule_ids), len(disc_ids))

    # ── 自校验
    for p in (OUT_MEM, OUT_SCHEMA, OUT_SELF):
        tomllib.loads(p.read_text(encoding="utf-8"))
    if OUT_MD.stat().st_size < 4000:
        raise SystemExit("FAIL: PERSONA_MEMORY_v0.md 过小")
    print(f"selfcheck: memory/schema/self TOML 回读 OK；md={OUT_MD.stat().st_size}B")
    print(f"OK entries={len(E)} err={len(errs)} warn={len(warns)}")
    for x in errs:
        print("  ERR ", x)


def write_md(covered, n_rules, n_disc):
    L = []
    a = L.append
    a("# EvoAlpha 人格层 · 记忆与自述 v0")
    a("")
    a("> **自动生成**，请勿手改。生成器 `yaoban-system/tools/build_persona_memory.py`；")
    a("> 机器可读：`persona/memory/memory_v0.toml` + `self_narrative_schema_v0.toml` + `self_narrative_v0.toml`。")
    a("> 权威：`docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md` §R1.4「决策日志 → 教训沉淀；"
      "当前市场观点/持仓心态自述（结构化落盘，供 LLM 裁量时引用）」。")
    a("")
    a("## 一、设计要点")
    a("")
    a("1. **记忆不是日记**：每条 entry 必须给出**可执行的教训**（`lesson`）与**作用对象**"
      "（`applies_to` = SOP 规则 id 或裁量点 id）。裸案例不入库。")
    a("2. **`applies_to` 双向校验**：引用的 id 必须真实存在于 `sop_v0.toml` 或 `discretion_v0.toml`，"
      "否则构建失败 —— 保证记忆层不与规则层脱钩。")
    a("3. **元教训（`meta_lesson`）单独一类**：关于「素材可信度 / 口径」的教训**不用于交易**，"
      "而用于判断**证据能不能信**。它们不挂 `applies_to`，是 R3 LLM 的**证据纪律**。")
    a("4. **自述分三轨，严格分离**：")
    a("   - `persona_view` = 选手视角的市场观点（人格要复刻的对象）")
    a("   - `persona_holding_stance` = 选手视角的持仓心态")
    a("   - `account_state` = **EvoAlpha 自己的账户**（50 万独立账本）")
    a("   混在一起会让 LLM 把「选手持仓」误当「我们的持仓」。")
    a("5. **必须写「我不知道什么」**：`open_questions` 不得为空 —— 否则 LLM 会把不确定当确定。")
    a("")
    a(f"**记忆条数 {len(E)}**：lesson {sum(1 for e in E if e['kind']=='lesson')} / "
      f"case {sum(1 for e in E if e['kind']=='case')} / "
      f"meta_lesson {sum(1 for e in E if e['kind']=='meta_lesson')} / "
      f"quote {sum(1 for e in E if e['kind']=='quote')}")
    a("")
    a("## 二、记忆库")
    a("")
    for kind, label in (("lesson", "可执行教训"), ("case", "案例"), ("meta_lesson", "元教训（证据纪律）"),
                        ("quote", "原话（人格语料）")):
        rs = [e for e in E if e["kind"] == kind]
        if not rs:
            continue
        a(f"### {label}（{len(rs)} 条）")
        a("")
        for e in rs:
            a(f"#### `{e['id']}` {e['title']}")
            a("")
            a(f"- **证据**：{e['evidence']}　**出处**：{e['source']}"
              + (f"　**日期**：{e['date']}" if e["date"] else ""))
            if e["applies_to"]:
                a(f"- **作用对象**：{', '.join('`'+x+'`' for x in e['applies_to'])}")
            if e["tags"]:
                a(f"- **标签**：{', '.join(e['tags'])}")
            a("")
            a(f"{e['content']}")
            a("")
            if e["lesson"]:
                a(f"> 💡 **教训**：{e['lesson']}")
                a("")
    a("## 三、自述 schema")
    a("")
    a("**三轨**")
    a("")
    a("| 轨 | 含义 |")
    a("|---|---|")
    for tr, desc in SCHEMA["tracks"].items():
        a(f"| `{tr}` | {desc} |")
    a("")
    a("**字段**")
    a("")
    a("| 字段 | 类型 | 必填 | 映射到 | 说明 |")
    a("|---|---|---|---|---|")
    for path, spec in SCHEMA["fields"].items():
        a(f"| `{path}` | {spec['type']} | {'✅' if spec.get('required') else '—'} | "
          f"{spec.get('maps_to','—')} | {spec.get('note','—')} |")
    a("")
    a("**硬规则**")
    a("")
    for hr in SCHEMA["hard_rules"]:
        a(f"- {hr}")
    a("")
    a("## 四、当前自述实例（种子）")
    a("")
    pv = SELF["persona_view"]
    a(f"- **观测日**：{SELF['meta']['as_of']}　**写入者**：`{SELF['meta']['writer']}`")
    a(f"- **regime**：`{pv['regime']}` — {pv['regime_note']}")
    a(f"- **支撑**：{pv['key_support']}　**压力**：{pv['key_resistance']}")
    a(f"- **量能**：{pv['turnover_state']}")
    a(f"- **主线**：{', '.join(pv['main_lines'])}")
    a(f"- **回避**：{', '.join(pv['avoid'])}")
    a(f"- **置信度**：`{pv['confidence']}`")
    a("")
    a("**open_questions（我不知道什么）**")
    a("")
    for q in pv["open_questions"]:
        a(f"- {q}")
    a("")
    ps = SELF["persona_holding_stance"]
    a(f"**选手持仓心态**（as-of {ps['as_of']}）")
    a("")
    a("| 代码 | 名称 | 买入逻辑 | 持有条件 | 离场条件 |")
    a("|---|---|---|---|---|")
    for p in ps["positions"]:
        a(f"| `{p['sym']}` | {p['name']} | {p['entry_logic']} | {p['hold_condition']} | {p['exit_condition']} |")
    a("")
    a(f"> {ps['stance_note']}")
    a("")
    acc = SELF["account_state"]
    a(f"**EvoAlpha 账户**（as-of {acc['as_of']}）")
    a("")
    a(f"- 状态：`{acc['status']}`　起始资金：{acc['equity_basis']}　起算日：{acc['start_date']}")
    a(f"- {acc['positions_note']}")
    a("")
    a("## 五、记忆 → 规则映射覆盖")
    a("")
    a(f"记忆引用了 **{len(covered)}** 条规则/裁量点：")
    a("")
    a(", ".join(f"`{x}`" for x in covered))
    a("")
    a(f"⚠️ 另有 {n_rules - len([x for x in covered if not x.startswith('D')])} 条规则未被任何记忆引用 —— "
      f"**这是正常的**：规则不必都有案例，但**每条记忆都必须有作用对象**。"
      f"（当前 SOP {n_rules} 条规则 / {n_disc} 个裁量点）")
    a("")
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        OUTDIR.mkdir(parents=True, exist_ok=True)
        OUT_REPORT.write_text("!!! EXCEPTION !!!\n" + traceback.format_exc(), encoding="utf-8")
        raise
