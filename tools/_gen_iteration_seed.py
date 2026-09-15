# -*- coding: utf-8 -*-
"""一次性生成器：把「选手学习资料」已归纳结论结构化为自迭代批的种子数据。

产物（供 scripts/daily_iteration.py 消费）：
  data/iteration/knowledge_cards/20260911_seed.md   知识卡片库（人读 + 机读）
  data/iteration/case_table_yaoban.json             冻结案例表（选手实际交易 + 候选池）

设计口径（诚实边界）：
  - 卡片只搬运资料中**已归纳**的结论，不新增语义判断；来源逐条可追溯。
  - 案例表把「选手实际动作」与「公开候选池」分列，因为画像已证「公开候选池 ≠ 完整交易集」。
  - 未量化的判据（分时口径）标 testability 字段，禁止当作已验证规则。
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARDS_DIR = ROOT / "data" / "iteration" / "knowledge_cards"
CASE_FILE = ROOT / "data" / "iteration" / "case_table_yaoban.json"

PROFILE = "选手学习资料/选手战法画像-累计.md"
VIDEO = "选手学习资料/视频复盘学习笔记/复盘视频学习笔记_20260903-20260910.md"
VOLUME = "选手学习资料/成交量六种形态-学习笔记.md"
LOG0901 = "选手学习资料/文字资料/2026-08-31_0901_妖板选手日志/record.md"
T0902 = "选手学习资料/文字资料/2026-09-02_1047_持仓截图/record.md"
T0903 = "选手学习资料/文字资料/2026-09-03_0940_候选池/record.md"
T0904 = "选手学习资料/文字资料/2026-09-04_1041_持仓截图/record.md"
T0907 = "选手学习资料/文字资料/2026-09-07_1045_持仓截图/record.md"
T0908 = "选手学习资料/文字资料/2026-09-08_1033_吃板播报/record.md"
T0909 = "选手学习资料/文字资料/2026-09-09_0946_候选池与复盘/record.md"
T0910 = "选手学习资料/文字资料/2026-09-10_0935_候选池与纪律/record.md"
T0910H = "选手学习资料/文字资料/2026-09-10_1044_持仓截图/record.md"

# id, title, category, source, source_date, statement, quantified (或 None), testability, linked_params
CARDS = [
    # ---------------- 选股 / 入场 ----------------
    dict(id="KC-0001", title="战法谱系：上升回档 + 趋势反包（两套，非一套）",
         category="选股", source=VIDEO, source_date="2026-09-08",
         statement="主力战法为「上升回档」，辅助战法为「趋势反包」；此前只记录一套属认知缺口。",
         quantified=None, testability="qualitative",
         linked_params=["strategy.huigui", "strategy.qu_shi_fanbao"]),
    dict(id="KC-0002", title="上升回档形态定义（选手原话）",
         category="选股", source=VIDEO, source_date="2026-09-09",
         statement="「二退一或者进三退二的蛇形走位模式」，趋势票讲求慢中取胜：涨一涨、调一调，再涨一涨、再调一调。",
         quantified="回档日数 1~3 日（最多 ≤5 日）；回调幅度 1%~15%；回调期缩量",
         testability="daily",
         linked_params=["strategy.huigui.live.pullback_days_min",
                        "strategy.huigui.live.pullback_days_max",
                        "strategy.huigui.live.pullback_pct_min",
                        "strategy.huigui.live.pullback_pct_max"]),
    dict(id="KC-0003", title="趋势反包形态定义",
         category="选股", source=VIDEO, source_date="2026-09-09",
         statement="「回踩均线企稳的标准形态」；样本仅光洋股份(9/7)、海通发展(9/9)两例。",
         quantified="前一日阴线 → 次日阳线包住阴线 + 同比放量（图上标注，阈值待验证）",
         testability="daily", sample_n=2,
         linked_params=["strategy.qu_shi_fanbao"]),
    dict(id="KC-0004", title="涨停回踩低吸战法 DNA",
         category="选股", source=PROFILE, source_date="2026-08-28",
         statement="倍量涨停柱 → 缩量回踩 → 再上攻入场。茶花股份 8/28 入场 19.036 为该战法样本。",
         quantified="回踩 2~7 日；守住 5 日线；价站 20 日线上方；不破涨停板实体；量缩至 0.7 倍以下",
         testability="daily",
         linked_params=["strategy.zt_huicai.pullback_days_min",
                        "strategy.zt_huicai.pullback_days_max",
                        "strategy.zt_huicai.hold_ma",
                        "strategy.zt_huicai.volume_shrink_ratio"]),
    dict(id="KC-0005", title="⭐ 入场过滤：分时第一波回落跌破均价线 → 一票否决",
         category="入场过滤", source=T0910H, source_date="2026-09-10",
         statement="候选发布后立即看分时：第一波回落跌破均价线即放弃，不参与。9/9 规避金帝/武汉凡谷/金富，9/10 规避金能(−5.71%)，同时只买唯一未被警示的海通发展(+9.98% 涨停)。",
         quantified="价格 < 当日分时均价线（VWAP）→ 弃；检查时点=候选发布后立即",
         testability="intraday", sample_n=9,
         linked_params=["entry.vwap_veto"]),
    dict(id="KC-0006", title="候选质量三级分层（先筛后排）",
         category="入场过滤", source=T0910H, source_date="2026-09-10",
         statement="① 最优=分时有量+一步一步向上；② 中等=无量、欠缺力度；③ 弃=跌破均价线。分级用于排序取舍，未体现为仓位权重差异。",
         quantified="判据权重：一票否决(均价线) > 质量加权(有量+稳步向上) > 力度减分(无量拖沓)",
         testability="intraday", sample_n=4,
         linked_params=["entry.vwap_veto"]),
    dict(id="KC-0007", title="不追高：拉升过快则放弃",
         category="入场过滤", source=VIDEO, source_date="2026-09-08",
         statement="泸天化连续三日被战法筛出却零成交，原话「拉的太快了，没上车」。筛选命中 ≠ 成交。",
         quantified="介入点需回踩/分时回落确认；快速拉升不追",
         testability="intraday", sample_n=1,
         linked_params=["entry.no_chase_pct"]),
    dict(id="KC-0008", title="买点当日涨幅上限 ≤3%",
         category="入场过滤", source="config/parameters.toml(rule 8)", source_date="2026-08-25",
         statement="既有知识表 rule 8：买点当日涨幅上限 ≤3%。",
         quantified="entry_gain_pct <= 3.0", testability="daily",
         linked_params=["strategy.huigui.live.pullback_pct_max"]),
    dict(id="KC-0009", title="公开候选池执行率（真实执行集不可由公开消息推出）",
         category="执行映射", source=PROFILE, source_date="2026-09-11",
         statement="9/8 粤传媒、9/9 诺普信均为完全未公开的买入 → 仅用公开候选池训练会系统性漏掉实际交易。",
         quantified="公开池执行率 9/2=50%、9/4=60%、9/7=50%、9/9=25%",
         testability="daily", sample_n=4,
         linked_params=["entry.vwap_veto"]),

    # ---------------- 持有 / 出场 ----------------
    dict(id="KC-0010", title="⭐ 趋势票 vs 连板票二分法（卖点判据的钥匙）",
         category="出场", source=VIDEO, source_date="2026-09-09",
         statement="「趋势票对涨停板不敏感」——涨停后是否炸板、次日能否连板，都不影响其多头趋势。故卖点按票型分档，不可混用。",
         quantified="趋势票看 5/10/20 日线；短线票看隔天强弱/开盘回落/分时买量",
         testability="daily",
         linked_params=["sell.trend_ma", "sell.short_term_rule"]),
    dict(id="KC-0011", title="趋势票持有与离场（自述原文）",
         category="出场", source=VIDEO, source_date="2026-09-09",
         statement="「趋势线没有有效跌破，就继续持有」「趋势一旦破位，果断离场，绝不恋战」「哪怕浮亏也要果断离场」。",
         quantified="收盘有效跌破趋势线（5 日线短线 / 10 日线生命线 / 20 日线中期）→ 离场",
         testability="daily",
         linked_params=["sell.trend_ma", "sell.confirm_on_close"]),
    dict(id="KC-0012", title="短线票隔天不强就走",
         category="出场", source=VIDEO, source_date="2026-09-08",
         statement="「除非第二天继续冲高连板会留，一般短线隔天不强就走了」；「开盘后不久就回落，短线走势低于预期 → 有赚就走」。",
         quantified="次日开盘后回落 + 不封板 → 当日离场",
         testability="intraday",
         linked_params=["sell.short_term_rule"]),
    dict(id="KC-0013", title="茶花股份完整闭环：结构未破则拿，反弹到支撑上方即了结",
         category="出场", source=PROFILE, source_date="2026-09-09",
         statement="浮亏 −4.92% 未割、持有 6 个交易日；9/9 反弹 +3.74% 当日清仓 @19.1501(+0.599%)。决策依据是结构位置而非盈亏数字。",
         quantified="结构未破 → 持有；反弹到支撑上方 → 了结（视为离场机会而非加仓）",
         testability="daily", sample_n=1,
         linked_params=["sell.trend_ma"]),
    dict(id="KC-0014", title="止损锚=结构而非百分比（非对称系统）",
         category="出场", source=VIDEO, source_date="2026-09-08",
         statement="「割肉本身没有错，错的是被恐惧支配的盲目割肉；持仓也不是死扛，关键是要有客观的判断标准。我的标准就是趋势线。」",
         quantified="止损锚 = 均线结构 + 量能配合；与盈亏百分比无关",
         testability="daily", sample_n=2,
         linked_params=["sell.stop_loss_pct", "sell.trend_ma"]),
    dict(id="KC-0015", title="⚠️ 旧结论修正：止损被动 ≠ 止损拖延",
         category="出场", source=PROFILE, source_date="2026-09-09",
         statement="粤传媒 −3.890% 持 1 日即砍，vs 诺普信 −3.423% 继续持有 → 结构一旦明确破坏就快速了结。",
         quantified="结构破位 → 快速了结；结构未破 → 容忍回撤",
         testability="daily", sample_n=2,
         linked_params=["sell.trend_ma", "sell.stop_loss_pct"]),
    dict(id="KC-0016", title="冲高未兑现 ≠ 遗漏（杭电股份结案）",
         category="出场", source=VIDEO, source_date="2026-09-09",
         statement="杭电 9/9 冲高 44.00(+9.70%) 未卖出属主动持有：『它是趋势票，是否涨停一点也不重要』。所谓「拉板次日兑现的唯一反例」实为误判。",
         quantified="趋势票卖点 = 趋势线破位，非日内冲高",
         testability="daily", sample_n=1,
         linked_params=["sell.trend_ma"]),
    dict(id="KC-0017", title="持仓了结=f(票型)，同一策略对两票两极（9/7 对照）",
         category="出场", source=PROFILE, source_date="2026-09-07",
         statement="9/7 同时止盈欢瑞(+8.886%)与远东(+1.600%)：欢瑞此后 −17.1%（正确），远东此后 +21.3%（卖飞）。",
         quantified="同一卖出动作对「高位加速末端」正确、对「主升中继」误伤",
         testability="daily", sample_n=2,
         linked_params=["sell.trend_ma"]),
    dict(id="KC-0018", title="仓位机制：信号驱动的高弹性仓位",
         category="仓位", source=PROFILE, source_date="2026-09-10",
         statement="仓位 57.0%(9/4) → 58.1% → 69.5% → 43.4%(9/9 净减仓) → 85.4%(9/10)；修正旧印象：既非稳定高仓也非稳定低仓。",
         quantified="随前一日战法信号强度与市场状态大幅调节；9/9 宣告「≤5 只、单只加重」",
         testability="daily", sample_n=5,
         linked_params=["risk.regime_position_cap", "risk.max_positions"]),

    # ---------------- 风险信号 ----------------
    dict(id="KC-0019", title="⚠️ 换手率闸门：>50% 需小心",
         category="风险", source=VIDEO, source_date="2026-09-08",
         statement="「它们换手特别高，超过 50 以上换手都需要小心」，集泰/龙版/金健当日下午均有炸板。",
         quantified="换手率 > 50% → 风险警示", testability="daily",
         linked_params=["risk.turnover_warn_pct"]),
    dict(id="KC-0020", title="⚠️ A 杀形态：生还率低（需量化特征）",
         category="风险", source=VIDEO, source_date="2026-09-10",
         statement="「A 杀形态是最可怕的形态，生还成功率很低」；各板块分支龙头基本全部 A 杀，形成负循环。",
         quantified=None, testability="qualitative",  # 待量化：阻塞自动化
         linked_params=["risk.akill_detect"]),
    dict(id="KC-0021", title="尖顶形态 → 连板高度递减",
         category="风险", source=VIDEO, source_date="2026-09-10",
         statement="深中华A 尖顶形态预示短线连板高标越来越难玩，短线票高度呈递减态势。",
         quantified=None, testability="qualitative",
         linked_params=["risk.top_shape"]),
    dict(id="KC-0022", title="攻击不足：差一分钱未封板 → 板上抛压重",
         category="风险", source=VIDEO, source_date="2026-09-03",
         statement="「差一分钱没封住」通常意味着板上抛压较重，多头没能完成最终一击，短线可能先震荡调整消化。",
         quantified="触及涨停价但收盘未封板（上影 > 0 且最高=涨停价）→ 抛压警示",
         testability="daily", linked_params=["risk.touch_no_seal"]),
    dict(id="KC-0023", title="梯队集体砸盘 → 倾向看卖点",
         category="风险", source=VIDEO, source_date="2026-09-08",
         statement="「如果梯队集体砸盘到时候就是倾向于看卖点」。",
         quantified=None, testability="qualitative",
         linked_params=["env.leader_break"]),
    dict(id="KC-0024", title="地量阈值（当期）：1.65 万亿 = 年内新低",
         category="环境", source=VIDEO, source_date="2026-09-10",
         statement="「今天大盘量能只有 1.65 万亿，已经是今年以来的地量；所谓地量见地价」。",
         quantified="两市成交额 ≤ 1.65e12 → 地量（知识表 rule 35 记 1.93 万亿为阶段地量，阈值随行情下移）",
         testability="daily", linked_params=["env.volume_floor_yi"]),
    dict(id="KC-0025", title="情绪温度：涨停家数与下跌家数",
         category="环境", source=VIDEO, source_date="2026-09-10",
         statement="9/7 涨停 92 家=赚钱效应回归；9/10 4504 家下跌=偏弱。",
         quantified="涨停家数分档（冰点 34 / 磨底 64 / 普反 116~124 / 活跃 99~106）",
         testability="daily", linked_params=["env.limit_up"]),
    dict(id="KC-0026", title="支撑位/压力位本质与有效性判据",
         category="方法论", source=VIDEO, source_date="2026-09-06",
         statement="支撑=觉得「足够便宜了」；压力=前期被套筹码的「解套卖压」。遇压力不突破→找风险；遇支撑不跌破→找机会。",
         quantified="支撑有效 = 最近每次回调都不跌破前面的低点",
         testability="daily", linked_params=["entry.support_hold"]),
    dict(id="KC-0027", title="均线口径：线上为支撑，线下为压力",
         category="方法论", source=VIDEO, source_date="2026-09-07",
         statement="「K 线在均线上方，上升趋势上涨概率达到 70% 左右」；「K 线跌破重要的支撑代表趋势有可能转弱，那个时候也是离场点」。",
         quantified="close > MA(n) → 均线为支撑；close < MA(n) → 均线为压力；上升趋势胜率 ≈70%",
         testability="daily", linked_params=["sell.trend_ma"]),
    dict(id="KC-0028", title="量柱六形态口诀",
         category="方法论", source=VOLUME, source_date="2026-08-30",
         statement="看位置 → 看量柱 → 看均线；放量在分歧、缩量在惜售、倍量在主力、梯量在健康。",
         quantified="倍量=vr≥2.0；梯量=连续温和递增；缩量=vr<0.7",
         testability="daily", linked_params=["strategy.zt_huicai.volume_shrink_ratio"]),
    dict(id="KC-0029", title="🏆 单笔盈利榜：前三名全部为 1 日持有",
         category="执行映射", source=PROFILE, source_date="2026-09-10",
         statement="海通发展 +13.682%(1日)、集泰 +11.510%(1日)、欢瑞 +8.886%(1日) → 与「拉板次日兑现」纪律一致。",
         quantified="短线票平均持有 1 日；趋势票可持有 8 日以上",
         testability="daily", sample_n=5,
         linked_params=["sell.short_term_rule", "sell.trend_ma"]),
    dict(id="KC-0030", title="读图口径（六次验证）",
         category="数据口径", source=PROFILE, source_date="2026-09-10",
         statement="总盈亏 = Σ当前持仓浮盈 + Σ当日已平仓已实现盈亏（不含往日已实现）；市值合计 == 总市值。",
         quantified="恒等式已 6 次验证通过（9/2、9/4、9/7、9/8、9/9、9/10）",
         testability="daily", sample_n=6, linked_params=[]),
]

# 选手实际交易（建仓）—— 由持仓截图逐笔核验
ENTRIES = [
    # code, name, 建仓日, 成本价, 股数, 平仓日, 平仓价, 实现收益率%, 来源
    ("603615", "茶花股份", "2026-08-28", 19.0360, 3000, "2026-09-09", 19.1501, 0.599, T0910H),
    ("603618", "杭电股份", "2026-09-08", 38.3120, 1000, None, None, None, T0910H),
    ("605006", "山东玻纤", "2026-09-10", 16.8750, 6500, None, None, None, T0910H),
    ("002886", "沃特股份", "2026-09-10", 26.8380, 4000, None, None, None, T0910H),
    ("603162", "海通发展", "2026-09-09", 14.5850, 4000, "2026-09-10", 16.5805, 13.682, T0910H),
    ("002215", "诺普信", "2026-09-09", 10.9030, 5000, None, None, None, T0910H),
    ("002181", "粤传媒", "2026-09-08", 10.1030, 4000, "2026-09-09", 9.7100, -3.890, T0909),
    # 注：8/31 日志中的「有研新材/双良节能/宿迁联盛 9/6 建仓」三行在既有资料中与 8/31 场次自相矛盾
    # （9/6 为周日，且 9/9 截图已将宿迁联盛换出），属未解数据缺口 —— 不纳入冻结案例表，避免污染回归。
]

# 未解数据缺口（显式记录，不装作已知）
DATA_GAPS = [
    "8/31 日志「有研新材/双良节能/宿迁联盛 9/6 建仓」：9/6 为周日，与 8/31 场次矛盾，未解。",
    "9/3 持仓截图缺失：三只离场票的精确时点/价格无法定。",
    "9/3-9/4 选手文字复盘缺失。",
    "2026-08-24 两笔建仓（300468 四方精创 / 300394 天孚通信）缺成本价与标的确认来源。",
]

# 选手实际清仓（可独立回测出场规则；含持有周期与结果）
EXITS = [
    ("603615", "茶花股份", "2026-09-09", 19.1501, 0.599, "8日", "反弹到支撑上方了结", T0910H),
    ("603162", "海通发展", "2026-09-10", 16.5805, 13.682, "1日", "短线拉升不板止盈", T0910H),
    ("002181", "粤传媒", "2026-09-09", 9.7100, -3.890, "1日", "板块龙头停牌情绪影响出局", T0909),
    ("002909", "集泰股份", "2026-09-09", 9.1795, 11.510, "1日", "短线拉升不板止盈", T0909),
    ("000628", "高新发展", "2026-09-09", 56.3196, 2.762, "3日", "洗盘后反弹了结", T0909),
    ("002015", "协鑫能科", "2026-09-09", 16.5697, -5.505, "多日", "开盘直接跌破10日线破位止损", T0909),
    ("002708", "光洋股份", "2026-09-08", 16.6402, 3.580, "1日", "开盘后不久回落，短线低于预期", T0907),
    ("601208", "东材科技", "2026-09-08", 48.8500, 1.233, "1日", "同上", T0907),
    ("000892", "欢瑞世纪", "2026-09-07", 5.7404, 8.886, "多日", "分时上攻但没什么买量", T0907),
    ("600869", "远东股份", "2026-09-07", 20.5597, 1.600, "多日", "同上（后续卖飞 +21.3%）", T0907),
    ("603118", "共进股份", "2026-09-02", 17.9800, -0.86, "多日", "破倍量涨停柱+反弹乏力（共进式触发器）", T0902),
]

# 公开候选池：是否执行 + 事后结果（用于规则召回/误杀统计）
CANDIDATES = [
    # date, code, name, executed, entry_date, entry_px, note, source
    ("2026-08-31", "603615", "茶花股份", True, "2026-08-28", 19.0360, "涨停回踩低吸", T0902),
    ("2026-08-31", "603118", "共进股份", True, "2026-08-31", 18.1350, "8/31 14:18 建仓", T0902),
    ("2026-09-03", "003018", "金富科技", True, None, None, "上升回档精选，当日涨停", T0903),
    ("2026-09-03", "605118", "力鼎光电", False, None, None, "分时没什么量→次日冲高不板则止盈", T0903),
    ("2026-09-03", "603912", "佳力图", False, None, None, "候选未执行", T0903),
    ("2026-09-03", "300192", "科德教育", False, None, None, "候选未执行", T0903),
    ("2026-09-04", "600511", "欢瑞世纪", True, None, None, "执行率 3/5", T0904),
    ("2026-09-04", "300492", "高新发展", True, None, None, "执行率 3/5", T0904),
    ("2026-09-04", "600682", "协鑫能科", True, None, None, "执行率 3/5", T0904),
    ("2026-09-04", "000626", "远大控股", False, None, None, "被过滤", T0904),
    ("2026-09-04", "002279", "久其软件", False, None, None, "被过滤", T0904),
    ("2026-09-07", "002708", "光洋股份", True, "2026-09-07", 16.0650, "趋势反包战法", T0907),
    ("2026-09-07", "603258", "东材科技", True, "2026-09-07", 48.2550, "上升回档战法", T0907),
    ("2026-09-07", "000998", "隆平高科", False, None, None, "未执行", T0907),
    ("2026-09-07", "000912", "泸天化", False, None, None, "后续两连板但拉升太快未上车", T0907),
    ("2026-09-09", "603270", "金帝股份", False, None, None, "分时跌破均价线→弃", T0909),
    ("2026-09-09", "002194", "武汉凡谷", False, None, None, "分时跌破均价线→弃", T0909),
    ("2026-09-09", "003018", "金富科技", False, None, None, "分时跌破均价线→弃", T0909),
    ("2026-09-09", "603162", "海通发展", True, "2026-09-09", 14.5850, "唯一未被警示→买入，当日涨停", T0909),
    ("2026-09-10", "605006", "山东玻纤", True, "2026-09-10", 16.8750, "①最优：有量+一步步向上", T0910H),
    ("2026-09-10", "002886", "沃特股份", True, "2026-09-10", 26.8380, "②中等：无量欠缺力度", T0910H),
    ("2026-09-10", "603113", "金能科技", False, None, None, "③弃：破均价线，当日 −5.71%", T0910H),
    ("2026-09-10", "603093", "南华期货", False, None, None, "③弃：破均价线", T0910H),
]

# 战法命中但未成交（筛选命中 ≠ 成交）
SCREENED_NO_FILL = [
    ("2026-09-07", "000912", "泸天化", "9/7 筛出，9/8 早盘分时快速拉升，拉的太快没上车", VIDEO),
    ("2026-09-08", "000912", "泸天化", "再次动态讲解但未成交", VIDEO),
    ("2026-09-08", "603031", "集泰股份", "试错龙头抱团方向（已成交）", VIDEO),
]

# 读图口径恒等式验证记录
READOUT_CHECKS = [
    dict(date="2026-09-02", holding_pl=-713.31, realized=4589.35, total=3876.04),
    dict(date="2026-09-04", holding_pl=3137.89, realized=430.95, total=3568.84),
    dict(date="2026-09-07", holding_pl=-2192.67, realized=3457.97, total=1265.30),
    dict(date="2026-09-08", holding_pl=4641.36, realized=-870.58, total=2932.98, note="协鑫由恒等式反推"),
    dict(date="2026-09-09", holding_pl=5383.69, realized=2485.45, total=7869.14),
    dict(date="2026-09-10", holding_pl=4370.58, realized=7981.92, total=12352.50),
]


def card_md(c: dict) -> str:
    q = c.get("quantified") or "—（原文未给出量化阈值）"
    lp = c.get("linked_params") or []
    lp_s = ", ".join(f"`{p}`" for p in lp) if lp else "—"
    return "\n".join([
        f"### {c['id']} {c['title']}",
        "",
        f"- **类别**：{c['category']}",
        f"- **来源**：`{c['source']}` @ {c['source_date']}",
        f"- **结论**：{c['statement']}",
        f"- **可量化表述**：{q}",
        f"- **可测试性**：`{c['testability']}`",
        "- **样本数**：" + (str(c["sample_n"]) if c.get("sample_n") else "—"),
        f"- **关联参数**：{lp_s}",
        "",
    ])


def main() -> None:
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    body = [
        "# 知识卡片库 · 自迭代批种子（2026-09-11）",
        "",
        "> 来源：`选手学习资料/`（画像累计 + 9/3-9/10 复盘视频笔记 + 成交量六形态笔记 + 9/2-9/10 逐日文字资料）。",
        "> 本文件由 `tools/_gen_iteration_seed.py` 生成，内容**只搬运资料已归纳结论**，不新增语义判断。",
        "> 字段语义：`testability=daily` 可用日线确定性回归；`intraday` 需分钟数据；`qualitative` 未量化，阻塞自动化。",
        "",
        f"共 {len(CARDS)} 张卡片。统计：",
        "",
        "| 可测试性 | 张数 |", "|---|---|",
    ]
    for k in ("daily", "intraday", "qualitative"):
        body.append(f"| {k} | {sum(1 for c in CARDS if c['testability'] == k)} |")
    body += ["", "---", ""]
    body += [card_md(c) for c in CARDS]

    (CARDS_DIR / "20260911_seed.md").write_text("\n".join(body), encoding="utf-8")

    table = {
        "meta": {
            "generated_at": "2026-09-11",
            "source": "选手学习资料（画像累计 + 复盘视频笔记 + 逐日文字资料）",
            "disclaimer": "公开候选池 ≠ 完整交易集；本表分列 entries(实际交易) / exits(实际清仓) / candidates(公开池) / screened_no_fill(命中未成交)。",
        },
        "entries": [
            dict(code=c, name=n, entry_date=ed, entry_px=px, shares=sh,
                 exit_date=xd, exit_px=xp, realized_pct=r, source=src)
            for c, n, ed, px, sh, xd, xp, r, src in ENTRIES
        ],
        "exits": [
            dict(code=c, name=n, exit_date=d, exit_px=px, realized_pct=r,
                 hold=hold, reason=reason, source=src)
            for c, n, d, px, r, hold, reason, src in EXITS
        ],
        "candidates": [
            dict(pool_date=d, code=c, name=n, executed=ex, entry_date=ed,
                 entry_px=px, note=note, source=src)
            for d, c, n, ex, ed, px, note, src in CANDIDATES
        ],
        "data_gaps": DATA_GAPS,
        "screened_no_fill": [
            dict(date=d, code=c, name=n, note=note, source=src)
            for d, c, n, note, src in SCREENED_NO_FILL
        ],
        "readout_checks": READOUT_CHECKS,
    }
    CASE_FILE.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"cards={len(CARDS)} -> {CARDS_DIR / '20260911_seed.md'}")
    print(f"entries={len(ENTRIES)} exits={len(EXITS)} candidates={len(CANDIDATES)} -> {CASE_FILE}")


if __name__ == "__main__":
    main()
