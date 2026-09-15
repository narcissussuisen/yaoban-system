"""EvoAlpha 每日自迭代批核心包（EvoAlphaDailyIteration）。

模块划分：
  model      数据结构（Card / Proposal / BatchResult）与变更类别常量
  sources    资料扫描与哈希清单（只读）
  cards      知识卡片解析与累加去重
  profile    机读画像生成与版本差异
  rules      候选规则注册表（确定性信号表达式 + 参数网格）
  market     日线数据适配与前向收益口径
  shadow     影子回归（案例归因 + 市场影子盘）
  gate       参数门禁（过门槛才生效的判定）
  proposals  提案挖掘（参数轴 + 卡片缺口）
  reports    人读摘要

边界：全包禁止 LLM 语义判断；不改账本、不写门禁、不改生产代码。
"""
