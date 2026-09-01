# 妖板系统 使用与回溯手册 v1.0

> 系统已落地（P0~P6 全部完成）。本文回答两个问题：
> **① 系统每天怎么用？② 如何回溯查看盈利水平、驱动优化迭代？**

---

## 1. 使用节奏总览

| 周期 | 动作 | 产出 | 命令 |
|---|---|---|---|
| **每日 15:35** | 计划任务自动跑盘后管线 | `outputs/signals/YYYY-MM-DD_signal_report.md/.html` | （自动，`YaobanDailySignal`） |
| **每日盘后** | 人工复核信号报告 + 模拟/实盘记录 | 交易日志 | 见 §2 |
| **每周五** | 模拟盘周报 + 执行合规统计 | `outputs/paper_trade_report.md` + journal 统计 | 见 §3 |
| **每月 1 日 10:00** | 计划任务自动生成优化提案 | `outputs/iterations/YYYY-MM_proposal.md` | （自动，`YaobanMonthlyOptimize`） |
| **每月** | 复核提案 → 决定是否 `--apply` | 参数版本 bump + CHANGELOG | 见 §4 |
| **每季度** | 方法论迭代（新视频 → 规则草稿 → 回测 → 合入） | 规则版本 v4.x | 见 §5 |

---

## 2. 每日使用流程

### 2.1 盘后（15:30 后）

```powershell
# 任务会自动生成报告；也可以手动跑：
$env:PYTHONPATH = "..\py_libs"   # 相对 yaoban-system
python scripts\daily_pipeline.py --update
```

打开 `outputs/signals/<今日>_signal_report.html`，按顺序做三件事：

1. **看市场环境**：环境分（完整五维 0~10）+ 市场状态 + 仓位上限。
   - ≥8 强势：可 50-70% 仓位；4-7 中性：30-50%；≤3 弱势：≤20% 或空仓。
2. **看已确认买点**（放量阳线）：逐只对照复核清单——
   - 板块是否主线（三信号：资金流/核心逻辑/业绩）
   - 龙头梯队是否完整（高标/20cm/容量票，盯大成交核心是否掉队）
   - 分时确认（次日盘中：放量突破均价线回踩站稳、涨幅≤3%）
3. **看观察池**：形态成立但未放量收阳，加入自选次日盯"放量阳突破回档小高点"。

### 2.2 记录交易（模拟盘或实盘）

```powershell
# 开仓
python -m src.journal.journal add_trade --symbol 600584 --name 长电科技 `
  --date 2026-08-24 --price 32.5 --shares 2000 --pattern huigui --env 5 `
  --stop "破10日线3天不收回" --logic "上升回档+封测主线"

# 卖出
python -m src.journal.journal add_exit --trade 1 --date 2026-08-28 --price 35.0 `
  --reason profit_take --rule "赚10-20%先落袋" --compliant 1
```

> 铁律：**买入前写好止损位**；卖出时 `--compliant` 如实标注是否按规则执行（违规=0）。

### 2.3 情绪面补充查看（实盘认证模式）

```powershell
python scripts\backfill_sentiment.py --date 20260824   # 当日涨停/炸板/跌停/连板梯队
```

情绪温度计（涨停家数/炸板率/最高连板/梯队）对应手册 §1.2 情绪周期六阶段：
- 冰点：涨停 <40、跌停 >涨停
- 发酵：涨停回升、最高连板 ≥5（"打开高度"）
- 高潮/退潮：炸板率 >30%、龙头跳水、跌停激增

---

## 3. 每周回溯：查看盈利水平

### 3.1 模拟盘周报（回放最近窗口）

```powershell
python scripts\paper_trade.py --start 2026-08-01 --end 2026-08-24
```

`outputs/paper_trade_report.md` 给出：总收益/年化/最大回撤/交易笔数/胜率/平均盈亏/退出原因分布/周度统计。

> ⚠️ 窗口边界说明：短窗口（周报）与长窗口（整段基线）的信号集在边缘可能略有差异——
> ① 5 日去重只看窗口内信号，窗口外 5 天内的前序信号不会抑制窗口首日信号（周窗口可能
> 多出长窗口被去重掉的信号）；② 同时持仓上限 4 只，入场日持仓已满时按扫描顺序截断。
> 因此周报入场名单与整段基线报告在窗口边界日可能不一致，属正常伪影；盈利水平回溯以
> 整段基线报告为准，周报用于观察本周信号质量与执行偏差。

### 3.2 执行合规统计（journal）

```powershell
python -m src.journal.journal compliance
```

逐笔显示盈亏与触发规则，底部给 **合规率**。每周只看一件事：**哪笔交易违反了哪条规则**（10 点纪律/止损/仓位上限/买卖逻辑一致）。

### 3.3 盈利水平怎么看（指标体系）

| 指标 | 含义 | 健康参考（手册口径） |
|---|---|---|
| 胜率 | 盈利交易占比 | 视频自述约 60%+（"每天3只1只板"）；回测 50% 上下 |
| 盈亏比 | 平均盈利/平均亏损 | 止盈 10-20% vs 止损 5% → 目标 ≥2 |
| 最大回撤 | 资金曲线峰值回落 | 单笔2%+硬地板5-8% → 组合回撤应 <15% |
| 执行偏差 | 违规交易占比 | 目标 0；每笔违规=系统失效点 |
| 环境匹配度 | 强势期仓位 vs 弱势期仓位 | 强势重仓/弱势轻仓，反向即违规 |

**判断标准**：连续 4 周胜率 ≥45% 且盈亏比 ≥1.5 且执行合规率 ≥95% → 系统健康；任何一项连续不达标 → 查执行偏差 → 查参数（§4）。

---

## 4. 每月回溯：优化迭代（自迭代闭环）

### 4.1 自动提案

每月 1 日 10:00 `YaobanMonthlyOptimize` 自动跑 `run_optimize.py`，生成
`outputs/iterations/YYYY-MM_proposal.md`：
- 当前参数 vs 网格候选的验证段净收益对比
- 提升 ≥0.15pp 才提案，否则明确"不提案"（防参数空转）

### 4.2 手动复核/应用

```powershell
python scripts\run_optimize.py              # 生成提案（只读，不改配置）
python scripts\run_optimize.py --apply      # 应用提案（更新 parameters.toml + bump 版本 + CHANGELOG）
```

**护栏**：① walk-forward 评级必须 A/B；② 应用后模拟盘灰度 ≥1 个月；③ 随时可 git 回滚
（`git log --oneline config/parameters.toml` 找上一版本）。

### 4.3 深度复盘（可选）

```powershell
python scripts\run_walkforward.py   # 重新跑训练/验证切分，看参数高原是否还在
python scripts\run_hypotheses.py    # 六项假设复测（新增数据后）
```

---

## 5. 每季度回溯：方法论迭代

1. **新视频入库**：把新视频的 OCR 文本放入 `../妖板选手方法论拆解/ocr_text/`，按 `v4_materials` 的格式提取规则草稿（带 v##@mm:ss 出处）
2. **规则草稿回测**：新规则加入 `src/core/strategies.py` → 用 `tests/cases.json` 案例库回归（复现率 ≥80%）→ 六项假设复测
3. **合入/否决**：通过 → 更新 `config/parameters.toml` + bump 版本；否决 → 记录原因到 `outputs/iterations/CHANGELOG.md`

---

## 6. 实盘认证模式（a-stock-data 数据链路）

实盘阶段数据链路切换到 [a-stock-data](https://github.com/simonlin1212/a-stock-data)（V3.7.1，Apache-2.0）：

| 环节 | 研究模式（现状） | 实盘认证模式 |
|---|---|---|
| 个股日线 | akshare（东财→新浪→腾讯回退） | mootdx/腾讯（不封 IP）+ 复权因子 qfq/hfq |
| 涨停池/连板梯队 | 缺（akshare 东财仅近期） | **东财 push2ex 四池 + 同花顺涨停揭秘**（`src/data/astock.py` 已接入） |
| 情绪温度计 | 无 | `limit_up_sentiment()`：炸板率/连板高度/梯队（**环境打分五维已升级**） |
| 实时报价/盘口 | 无 | `tencent_quote()` + mootdx 五档盘口 |
| 资金面/筹码 | 无 | 龙虎榜/资金流/筹码分布 CYQ（SKILL.md §5/§6，按需接入） |
| 限流策略 | 东财 1.2s 节流 | em_get 统一节流（1s+抖动），mootdx/腾讯不封 IP |

**切换方式**：`src/data/astock.py` 与 `src/data/fetchers.py` 接口同构（都是
`fetch_* → DataFrame/list[dict] → store.upsert_*`），实盘认证时把 daily 管线/回测的
数据源指向 astock 即可，业务代码零改动。完整 54 端点见 `vendor_astock_skill.md`（已随仓库保存）。

---

## 7. 常见问题

- **信号太多/太少？** 已确认买点 = 形态+放量阳；观察池 = 等次日确认。每日 2-4 只最健康；
  若长期 0 确认，说明环境分低或候选池形态差——**空仓也是仓位**。
- **环境分用简版还是完整五维？** 情绪数据回填后自动用完整五维（报告标注来源日期）；
  未回填则用简版+专属映射。
- **参数被 --apply 改坏了？** `git checkout config/parameters.toml` 回滚，然后
  `python -m unittest discover -s tests -v` 回归。
- **数据源被限流？** astock 东财接口内置节流；akshare 模式自动回退新浪/腾讯；
  间隔拉长可调 `config/parameters.toml [data] request_sleep_s`。

---

## 8. 免责声明

本手册与系统仅用于个人方法论研究，不构成投资建议。所有信号须人工复核，实盘决策由使用者独立承担。
