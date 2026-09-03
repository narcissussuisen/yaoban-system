# 交易系统故障/事件记录（Incident Log）

> 本台账服务于「方法论与研究文档」工作区的交易系统故障处理职责。
> 每条记录固定结构：事件原文 → 链路核验 → 根因判定 → 处置结论 → 改进观察。
> 事件均为 10 万元自主模拟盘（autonomous_paper），不涉及真实资金。

---

## INC-2026-08-31-01 · 300489 vwap_halve 秒级持仓风险事件（alert_only）

### 1. 事件原文（飞书推送，2026-08-31 11:22:19）

```
逐妖交易团队｜盘中重大事件 2026-08-31 11:22:19
标的：300489
事件：秒级持仓风险或数据事件
规则：vwap_halve
执行状态：alert_only
详情：跌停/无量/无可卖份额
说明：仅为自主模拟盘事件，不涉及真实资金。
```

### 2. 链路核验（全部通过 ✅）

| 环节 | 证据 | 状态 |
|---|---|---|
| 事件落盘 | `outputs/intraday/risk_events.jsonl` 第 3 条：`{"date":"2026-08-31","time":"11:22:19","sym":"300489","trigger":"vwap_halve","px":241.26,"qty":0,"limit_down":187.27,"action":"alert_only","blocked_reason":"跌停/无量/无可卖份额"}` | ✅ 与推送原文一致 |
| 飞书投递 | `outputs/notifications/delivery_20260831.jsonl`：`alert:2026-08-31:300489:vwap_halve:11:22:19:alert_only` @ 11:23:04，http 200 / business_code 0 / ok=true | ✅ 送达 |
| 幂等键 | `alert:{date}:{sym}:{rule}:{time}:{action}` 与 risk_events 行一一对应，无重复发送 | ✅ |
| 持仓账本 | `portfolio/ledger.json`：300489 qty=100，cost=247.30875，entry=2026-08-31 10:00，stop_px=234.65，当日无卖出流水 | ✅ 无异常成交 |

### 3. 时间线（当日同类事件共 3 次）

| 时间 | 触发价 | 行为 |
|---|---|---|
| 10:00:00 | 247.00（买入 100 股，e4_support，plan-2026-08-31） | 模拟成交（已推送 ok=true） |
| 10:00:51 | 245.71 | vwap_halve → qty=0 → alert_only（已推送） |
| 11:08:44 | 238.89 | vwap_halve → qty=0 → alert_only（已推送） |
| 11:22:19 | 241.26 | vwap_halve → qty=0 → alert_only（已推送） |
| 13:21:26 | 239.98（+2.52%，t1_locked=true） | pos_live 快照，持仓未动 |

### 4. 根因判定

- **触发条件成立**：当日高点 ≥ 昨收×1.07（+7% 利润兑现门），且现价 < VWAP×0.997，`vwap_halve` 规则按设计触发。
- **被拦截的真实原因（非跌停、非无量）**：
  1. `qty = 100 // 2 // 100 * 100 = 0` —— 100 股持仓的"减半"= 50 股，不足 1 手（100 股），直接归零；
  2. `t1 = sellable_qty(...)` = 0 —— 当日 10:00 买入，A 股 T+1 无可卖份额（pos_live `t1_locked: true`）；
  - 两因素叠加 → `qty < 100` → 保守成交门禁 `blocked=True` → `alert_only`。
- **"跌停/无量/无可卖份额"是三类条件的合并字符串，本次并不贴切**：现价 ~241 元 vs 跌停价 187.27 元（创业板 ±20%），且多时点有成交价变动，既非跌停也非无量；实际原因仅为**无可卖份额（T+1 + 不足一手）**。

### 5. 处置结论

- **系统行为正确且安全**：tick 守护（`scripts/tick_monitor.py`）的保守成交门禁生效，未产生虚假模拟卖出，持仓原样保留；事件落盘、去重、推送全链路正常。**本次为设计内的 alert_only 风险事件，非管道故障、非数据故障**（TDX 数据正常，规则基于真实行情触发）。
- 无需修复动作；持仓 300489 继续按现有止损/破线规则守护。

### 6. 改进观察（非阻塞建议，供后续迭代）

1. **blocked_reason 粒度**：建议把"跌停/无量/无可卖份额"拆成独立原因字段（如 `limit_down` / `no_volume` / `t1_no_sellable` / `sub_lot`），飞书消息按实际原因展示，避免误导（本次实际是 T+1+不足一手）。
2. **迷你仓规则无效告警**：100 股持仓的 vwap_halve 永远 qty=0，规则对迷你仓位无执行意义却会反复告警；可对 `qty==0 且因不足一手` 的场景降级为不推送或合并提示。
3. **跨轮次重复告警**：`fired` 集合按守护进程内去重，当日同类事件（10:00:51/11:08:44/11:22:19）每次新进程都会再发一次；若希望降噪，可在 risk_events 层按 (date, sym, trigger, 状态未变) 做日级去重。
4. **买入时机观察**：10:00 买入后 51 秒即触发破均价线卖出规则（买在冲高 247 附近），属"买即破线"场景；可评估买入信号叠加 VWAP 状态确认或买入后冷却期，降低追高买入概率。

## INC-2026-08-31-02 · 日末验收失败（acceptance exit 2）——竞价链路整体死亡

### 1. 事件原文（飞书推送，2026-08-31 19:10）

```
Yaoban task failed 2026-08-31 19:10
Stage: acceptance
Exit code: 2
Action: fail closed; no new positions when the critical intraday path fails.
```

### 2. 验收失败明细（outputs/acceptance/acceptance_2026-08-31.json）

| 检查项 | 结果 | 原因 |
|---|---|---|
| task_log_continuity | false | 09:40–09:55 早盘窗口无 scan/monitor/notify 成功记录（被门禁拦截） |
| auction_latest / auction_freeze / auction_delivery | false | 竞价监控零运行，无任何竞价证据 |
| live_tick | false | VibeResearchLiveTickValidation 11:39:39 exit 2（午休补跑，fail-closed 属设计行为） |
| tasks | false | YaobanAuctionMonitor=21、YaobanPlanGate=1、VibeResearchLiveTickValidation=2 |
| infra_gate / post_plan_gate / premarket / close / tick_snapshot / ledger / next_plan | true | 其余正常 |

### 3. 根因链（三层叠加）

1. **早盘触发点（08:55）**：YaobanPlanGate 的 preflight --post-plan 中 `TDX行情` 检查失败（connected=True bars=0，旧版单服务器逻辑，TDX 瞬时无数据）→ 门禁文件 status=fail → exit 1。
2. **竞价窗口死亡（09:15–09:30）**：YaobanAuctionMonitor 每分钟被 `Gate post_plan` 拦截（exit 21，门禁状态无效）→ 竞价数据零采集 → 19:10 验收竞价四项证据全部缺失。
3. **09:55:20 门禁重建**：TDX 恢复后 post_plan 门禁重新生成（status=pass）→ 09:56 起 scan/monitor/notify/tick 全部恢复；当日 fail-closed 生效（无新仓），安全。
4. **独立问题 A（VibeResearchLiveTickValidation）**：11:35 无窗口包装重注册后 StartWhenAvailable 补跑落在 11:30–13:00 午休，脚本自身 fail-closed（trading_window 检查）→ 写 pass=false、exit 2。设计行为，非故障。
5. **独立问题 B（YaobanNextPlan 17:10 exit 22）**：launch.ps1 的 run_trading_task.ps1 switch 无 `next-plan` 分支 → unknown mode。任务已冗余（rebuild 17:00 已调用 generate_next_plan.py），但注册为 Ready，每日必败。
6. **迁移遗留（YaobanBoardRefresh）**：EvoAlpha 迁移后该活动任务 Action 仍指向已删除的旧路径 `...\方法论与研究文档\yaoban-system\...`，每交易日 09:35–11:26 / 13:05–14:56 每 3 分钟失败一次。

### 4. 处置结论

- **验收系统行为正确**：日末验收如实暴露了竞价链路死亡，fail-closed 生效，无虚假通过。
- **本次已修复**：
  1. `run_trading_task.ps1` 补上 `next-plan` 分支（调用 generate_next_plan.py）——YaobanNextPlan 不再 exit 22。
  2. YaobanBoardRefresh 任务 Action 更新为 EvoAlpha 新路径，并与其他 17 个任务一致改用 pythonw + run_hidden.py 无窗口包装（触发器保留，NextRun 09-01 09:35）。
  3. TDX 健康检查验证：当前 preflight.py 为 10 服务器 × 4 线型回退（含日K），晚间实测 ok（server=115.238.56.198:7709 cat=0 bars=2 last=15:00），盘前日K回退可避免同类 false-fail。
  4. 验收收集器在新路径重跑正常（status=fail 为今日真实记录，退出码透传 2 正确）。
- **明日预期**：08:55 plan-gate（TDX 回退）→ 09:15 竞价监控（门禁有效）→ 竞价证据齐全 → Vibe 校验 09:32 正常触发（交易时段内）→ 19:10 验收通过。今日失败属历史事实，不追溯。

### 5. 改进观察（非阻塞建议）

1. **Gate 失败恢复自愈**：今晨 09:15–09:55 竞价窗口内门禁无效时无自动重试机制（09:55 的重建为人工/偶然触发）。建议 plan-gate 增加 08:55–09:15 内的有限自动重试（TDX 瞬时故障常见）。
2. **竞价窗口保护**：auction 任务 09:15 首次执行前置门禁失效时，可考虑降级为"只读观察不建仓"而非整段跳过，避免竞价监控整体空白。
3. **计划任务路径审计**：register_monitor_tasks.ps1 / register_scan.ps1 / register_scan_gray.ps1 / update_tasks_ps1.ps1 仍硬编码旧路径 `方法论与研究文档\yaoban-system`，若有人重跑会把任务注册回已删除路径；建议统一改为读取 root.txt。
4. **任务冗余清理**：YaobanNextPlan 与 rebuild 职责重叠（均已调用 generate_next_plan.py），可择一停用，减少失败噪音。

---

## INC-2026-09-01-01 · 盘后链部分失败 codes=0,0,0,0,2（验收过早运行，fail-closed 正确拒绝）

### 1. 事件原文（飞书推送，2026-09-01 01:07:36）

```
盘后链部分失败 2026-09-01 codes=0,0,0,0,2
```

### 2. 链路核验（全部通过 ✅）

| 环节 | 证据 | 状态 |
|---|---|---|
| 推送原文 | `outputs/notifications/delivery_20260901.jsonl` 第 1 条：`failure:2026-09-01:post-close` @ 01:07:36，http 200 / business_code 0 / ok=true；message_sha256=`7b5741b6…d73b` 与「盘后链部分失败 2026-09-01 codes=0,0,0,0,2」UTF-8 SHA-256 逐字节一致 | ✅ 与用户提供原文一致 |
| 分步退出码 | `scripts/post_close_chain.ps1` 顺序：r5p_sentiment_build → r6p_candidates_build → fetch_daily_minute_rebuild → generate_next_plan → collect_daily_acceptance；codes=0,0,0,0,2 ⇒ 前 4 步成功，仅第 5 步验收 exit 2 | ✅ 定位到失败环节 |
| 运行记录 | `outputs/task_logs/2026-09-01/` 共 4 次 post-close 运行（00:30:56 / 00:31:46 / 01:10:07 / 01:10:50），最后一次 01:47:09 完成、exit 1（链级：bad>0 时整体 exit 1）；stdout 尾部可见 next_plan（published_at 01:46:58）与验收 fail JSON | ✅ 与 codes 自洽 |
| 验收明细 | `outputs/acceptance/acceptance_2026-09-01.json`（generated_at 01:47:07，run 4 写入）：status=fail；pass=infra_gate / ledger_mode / ledger_start / next_plan；fail=task_log_continuity、auction_latest、auction_freeze、auction_delivery、post_plan_gate、premarket_delivery、close_delivery、live_tick、tick_snapshot、tasks（11 项任务 last_run 全部停在 2026-08-31） | ✅ fail 项全部为「当日盘中证据」 |
| 任务注册 | YaobanPostCloseChain 计划任务：Ready，触发器 2026-09-01 16:30:00，LastRun=从未运行（1999/11/30 0:00:00，0x41303） | ✅ 凌晨 4 次运行均为手工/调试触发，非计划任务 |
| 当日恢复 | 09-01 08:45:45 YaobanPreflight exit 0、08:50:50 YaobanPremarket exit 0、premarket 推送 08:50:02 ok=true | ✅ 盘前链路健康，事件无残留影响 |

### 3. 时间线（2026-09-01 凌晨，P0 时间表重构调试期）

| 时刻 | 运行 | 结果 |
|---|---|---|
| 00:30:56 | run 1 | run_trading_task.ps1 语法错误（switch 分支缺语句块）→ 2 秒内失败，重构中间态 |
| 00:31:46 | run 2 | 五步跑完（stdout 8.4KB 完整输出），acceptance exit 2 → 01:07:36 推送失败消息（本次事件原文） |
| 01:10:07 | run 3 | post_close_chain.ps1 param 解析错误（脚本编辑中间态）→ 2 秒内失败 |
| 01:10:50 | run 4 | 五步重跑，01:47:09 完成；acceptance 再次 fail / exit 2，覆盖写入 acceptance_2026-09-01.json；同一 event_key 幂等去重，未重复推送 |

### 4. 根因判定

- **直接原因**：验收收集器 exit 2（status=fail → fail-closed）。fail 的 10 项检查全部要求「2026-09-01 当日盘中证据」——竞价产物、post_plan 门禁、premarket/close 投递、live_tick（expected_date 需=2026-09-01）、tick_snapshot（date 需=2026-09-01）、任务连续性（task_logs/2026-09-01/ 无 auction/scan/monitor/notify 记录）、11 项任务 last_run 均停在上一交易日。
- **深层原因（时机错误，非管道故障）**：链在 09-01 凌晨 00:30–01:47 被手工触发（P0 时间表重构调试期，脚本头注释「P0时间表重构(2026-09-01, 用户评审)」），早于当日开市 8 小时以上；而 `collect_daily_acceptance.py --date` 默认取当天日期，无「过早运行」前置防护——当日尚未产生任何交易证据时，验收必然 fail。pass 的 4 项（infra_gate / ledger_mode / ledger_start / next_plan）恰好是盘前门禁与盘后产物，从反面印证了该判断。
- **数据与产物**：前 4 步基于 08-31 及更早数据正常产出（r5p 情绪、r6p 候选、日K重建、09-02 计划均有效），无数据损坏；账本未受影响（ledger_mode=autonomous_paper、ledger_start=2026-08-31、持仓 300489 保留）。

### 5. 处置结论

- **系统行为正确且安全**：验收 fail-closed 生效，如实拒绝了一个「当日零交易证据」的验收日，未虚报通过；前 4 步尽力而为逻辑正常，失败在验收中可见。
- **无需修复代码**：YaobanPostCloseChain 已按 16:30 正确注册且从未被计划任务错误触发；凌晨运行属重构期手工触发。当日（09-01）盘前链路（08:45 preflight、08:50 premarket）已恢复正常，16:30 计划任务将产出当日正式验收，凌晨产物为重构期调试快照、不追溯。
- **遗留动作**：run 1/run 3 暴露重构中间态直接落盘 task_logs，属调试噪音，不做追溯处理。

### 6. 改进观察（非阻塞建议）

1. **验收过早运行防护**：`collect_daily_acceptance.py` 无阶段约束。建议增加窗口检查：当日 15:00 前运行且 `task_logs/{day}` 无任何盘中模式（auction/scan/monitor/notify）记录时，直接输出 `premature: day 尚未收盘` 并 exit 0（或独立退出码），避免凌晨误跑产生 10 项 fail 的告警噪音；若保留 fail 语义，至少在报告中标注 `phase=premature`。
2. **验收历史保留**：同一验收日多次运行会覆盖 `acceptance_{day}.json`（本次 run 2 与 run 4 各写一次）。建议按 `acceptance_{day}_{run}.json` 保留每次运行快照，或记录 `generated_at` 之外增加运行序号，便于追溯重构期调试运行。
3. **重构期脚本自检**：run 1（switch 语法错误）、run 3（param 解析失败）表明改脚本后直接经任务包装执行。建议改完 .ps1 先做语法解析校验（`[System.Management.Automation.Language.Parser]::ParseFile` 或 `powershell -NoProfile -Command "[scriptblock]::Create((Get-Content -Raw ...))"`）再触发运行。
4. **幂等去重验证通过**：4 次运行仅推送 1 次（`failure:{day}:post-close` 幂等键生效），该机制保持现状即可。

---

## INC-2026-09-01-02 · 监控中断 模块 tick（持仓tick 3 小时无新鲜快照）——守护进程启动即死且当日无自愈

### 1. 事件原文（飞书推送，2026-09-01 13:05:03，delivery 13:05:04 ok=true）

```
逐妖交易团队｜监控中断 2026-09-01 13:05:03
模块：tick
详情：持仓tick最近2分钟无新鲜快照
状态：禁止依赖该模块产生新仓，等待恢复。
```

### 2. 链路核验（全部通过 ✅）

| 环节 | 证据 | 状态 |
|---|---|---|
| 推送记录 | `outputs/notifications/delivery_20260901.jsonl`：`monitor-gap:2026-09-01:tick:1305` @ 13:05:04，http 200 / ok=true；`outputs/notifications/monitor_health_2026-09-01.json`（13:08:03）active={tick: 持仓tick最近2分钟无新鲜快照} | ✅ 与事件原文一致 |
| 触发条件 | `scripts/notify_trading_events.py` `_tick_time()` 读 `outputs/intraday/pos_live.json` 的 time 字段；该文件 **time=09:45:03**（300489 px=235.34, t1_locked=false），13:05 时陈旧约 3 小时 20 分 ≫ 120 秒阈值 | ✅ 触发真实 |
| 守护进程死亡 | `outputs/task_logs/2026-09-01/20260901_093003_234_tick.json`：09:30:03 启动即死 exit 1，stderr=`SyntaxError: Non-UTF-8 code starting with '\xe3' ... tick_monitor.py on line 1`（文件被以 GBK 编码保存）；计划任务 YaobanTickDaemon：LastRun 09-01 09:30:30 / LastTaskResult 1，**日级任务，下次运行 09-02 09:30:30，当日无自动重启** | ✅ 根因定位 |
| 快照时间线 | pos_live.json 最后写入 09:45:03（mtime 一致）；tick_daemon.latest.log 停在 08-30 17:08（包装脚本最后一次拉起）；当前无任何 tick 相关 python 进程 | ✅ 守护确实未在跑 |
| 并发禁新仓 | `scripts/scan_and_confirm.py` `companion_health()`：pos_live 陈旧 >120s → `tick stale >2m` → exit 6 + stderr「伴随监控失效，禁止新仓: tick stale >2m」；证据：09:43–11:30 及 13:00–13:09 scan 全部 exit 6 | ✅ fail-closed 生效 |
| 账本安全 | `portfolio/ledger.json`：当日 fills=0；300489 qty=100 / stop_px=234.65 保留未动 | ✅ 无虚假成交 |

### 3. 时间线（2026-09-01，脚本编辑事故 + 守护死亡）

| 时刻 | 事件 | 说明 |
|---|---|---|
| 09:30:02–03 | 三脚本批量启动即死：tick_monitor.py / scan_and_confirm.py / monitor_intraday.py 均为非 UTF-8（GBK）→ SyntaxError exit 1；随即推送 failure:scan/monitor/tick @09:30:05 | 编辑事故（脚本被 GBK 保存落盘） |
| 09:35:04 | monitor-gap:tick:0935 首次报缺口 | 守护死亡后第一次告警 |
| 09:40:04 | monitor-gap:scan/monitor:0940；scan 09:40 还有 IndentationError（line 338，仍在编辑中）；monitor exit 4「监控数据不完整」 | 编辑中间态 + 数据源问题 |
| 09:41:54 / 09:42:34 / 09:45:23 | 三脚本先后修复为 UTF-8（scan 另修 IndentationError） | 修复完成 |
| 09:45:03 | 一次手工单轮运行 tick_monitor.py 写出 pos_live（time=09:45:03）→ 09:46:04 推送 monitor-recovered:tick:0946 | **单轮快照，非常驻守护** |
| 09:46:02–09:47 | monitor 恢复 exit 0；09:47:03 推送 monitor-recovered:monitor:0947 | monitor 链路恢复 |
| 09:48:03 | monitor-gap:tick:0948（09:45:03+2 分钟后又陈旧） | 守护未重启，再次中断 |
| 09:43–11:30 | scan 全程 exit 6（tick stale，禁新仓）；09:55 risk_floor 预警正常推送（scan/monitor 链路，与 tick 无关） | fail-closed 全程生效 |
| 11:31:03/04 | monitor-recovered:scan/tick:1131 —— **假恢复**：notify 缺口检查窗口为 scan 09:40–11:30、tick 09:35–11:30，11:31 落在窗口外，缺口状态被清空；scan 午休也以「非交易时段」exit 0 | 真实中断被掩盖 1.5 小时 |
| 13:00 | scan 重入交易窗口 → tick stale → exit 6（继续禁新仓） | 真实状态复现 |
| 13:05:03/04 | monitor-gap:tick:1305 推送（**本次事件**） | pos_live 仍为 09:45:03 |
| 13:08+ | monitor_health active={tick}；无守护进程；计划任务下次 09-02 09:30:30 | 中断持续中 |

### 4. 根因判定

- **直接原因**：tick 守护进程未运行 → `pos_live.json` 自 09:45:03 后无新鲜快照 → 13:05 进入检查窗口（13:05–15:00）后触发 120 秒陈旧阈值。
- **中层原因**：09:30 守护启动即死——`tick_monitor.py` 被以 GBK 编码保存，Python 解析 SyntaxError，exit 1；09:41:54 编码修复后仅手工单轮验证（09:45:03 写了一次快照），**未重启常驻守护**；YaobanTickDaemon 为日级 09:30:30 单次启动任务，**当日无自愈机制**。
- **深层/过程原因**：今晨对三个盘中脚本（tick_monitor / scan_and_confirm / monitor_intraday）的编辑以非 UTF-8 编码落盘，恰好落在 09:30 批量启动时刻；修复后又未恢复守护。此外 **notify 的缺口检查窗口在午休（11:30–13:05）跳过，11:31 推送了「恢复」假信号**，掩盖了真实持续中断，直到 13:05 重新报出。
- **性质**：脚本编码事故 + 守护自愈缺失 + 午休窗口假恢复，三者叠加。非行情/数据故障，非交易逻辑故障。

### 5. 处置结论

- **系统行为正确且安全**：缺口检测、推送、幂等去重、scan fail-closed（exit 6 禁新仓）全部按设计生效；账本无虚假成交，持仓 300489 原样保留。
- **已完成修复**：三脚本已于 09:41–09:45 修复为 UTF-8（scan 另修 IndentationError），当前均为合法 UTF-8，monitor/scan 语法可正常解析。
- **待人工动作（重要）**：重启 tick 守护——`powershell -File scripts\run_tick_daemon.ps1`（等价 `python -X utf8 scripts\tick_monitor.py --daemon --interval 5`）。重启后 pos_live 恢复新鲜，notify 将在检查窗口内自动推送 monitor-recovered:tick。**重启前**：持仓 300489 的止损/炸板/vwap_halve 卖出守护不可用（模拟盘 alert_only，不涉真实资金，但破位将无告警）；新仓已被 scan 禁止（安全）。
- **明日预期**：09-02 09:30:30 计划任务将重新拉起守护；但若仍以"启动即死"方式失败，同日将再次无自愈（见改进 2）。
- **处置执行（13:11–15:06，已闭环）**：经用户确认后重启守护（`run_tick_daemon.ps1`，alert_only）。验证：pos_live.json 恢复 5 秒级更新（13:12:34/13:13:06）；notify 自动推送 `monitor-recovered:2026-09-01:tick:1312` @13:12:03 ok=true；scan fail-closed 解除（13:12:01 起 exit 0，任务 LastRun 13:13:13 Result=0）。**附带发现**：恢复后 scan 补记了一笔早盘被门禁拦截的计划买入——300468 买入 1800 股 @24.58（ts=10:05，e4_support，plan-2026-09-01，recorded_at 13:12:08），成交推送 @13:13:03 ok=true。
- **午后运行结果（截至 15:05 收盘，正常）**：守护持续运行至 15:05:55 最后快照，15:05+ 按设计以 exit 0 退出（已过收盘）；当日 tick 链路全程新鲜，无再次缺口。期间 tick 守护触发并推送：13:15 risk_floor（300468，alert_only）、**13:21:10 300489 stop_loss（px 234.50 ≤ stop 234.65，qty=100，action=alert_only——守护未带 --execute-risk，仅告警不执行卖出，持仓保留过夜且止损已破）**，两条均 ok=true 送达。收盘快照：300489 px 233.40（-2.65%）、300468 px 24.54（+6.79%，T+1 锁定）。

### 6. 改进观察（非阻塞建议）

1. **Python 脚本编码防护**：本次三个 .py 因 GBK 落盘全部启动即死。建议仓库统一 UTF-8（.editorconfig / IDE 默认 / gitattributes `*.py text eol=lf`），修改后立即 `python -m py_compile <file>` 校验再离开。
2. **守护自愈缺失**：YaobanTickDaemon 日级单次启动，启动即死则当日无自愈。建议：计划任务加 RestartOnFailure（1 分钟间隔重试），或 run_tick_daemon.ps1 内循环拉起（检测子进程退出即重启，收盘后退出）。
3. **午休假恢复**：notify 缺口检查窗口（tick 09:35–11:30 / 13:05–15:00）在窗口外不清除 active 状态，或「恢复」消息标注「窗口跳检，未验证」——本次真实中断被 11:31 假恢复掩盖 1.5 小时，13:05 才重新告警。
4. **单轮快照触发"恢复"抖动**：09:45 一次单轮运行即触发 09:46 真恢复通知，随后 09:48 再次中断。建议恢复判定要求连续新鲜（如 pos_live 连续 3 轮更新）再宣告恢复。
5. **盘中修改纪律**：交易日 09:30 前后是批量启动时刻，脚本修改应避开该窗口，或改完立即 `py_compile` + 单轮试跑 + 恢复守护三步完成后再离手。

---

## INC-2026-09-02-01 · 盘前门禁链失效→全天 fail-closed（零新仓）+ 手动解锁卖出侧 + 15:06「卖出执行器死亡」告警实为收盘自退出

### 1. 事件原文（飞书推送，2026-09-02 多条）

```
08:45:25 failure:2026-09-02:infra / 08:50:01 failure:gate-infra / 08:58:03 failure:morning-check / 09:15:02 failure:gate-post_plan（×2，幂等去重后全天仅此二条）
15:06:08 [manual-tick][ALERT] 卖出执行器死亡且不再自拉起(pid=2796)，持仓失去盘中守护，请人工关注
15:10:06 failure:2026-09-02:close（Exit code 7，详见 INC-2026-09-02-02）
```

### 2. 链路核验（全部通过 ✅）

| 环节 | 证据 | 状态 |
|---|---|---|
| infra 晨检 fail | `outputs/preflight_2026-09-02_infra.json`：12 pass / 3 critical fail——净值守恒 curve=99244.12(8/31 点) vs calc=98481.82；情绪表 last=2026-08-31 expected∈{9/1,9/2}；候选表 max=2026-08-31 expected≥9/1 | ✅ 3 fail 属实 |
| 门禁级联 | 08:50 premarket `Gate infra` exit 21（gate status invalid）；08:55 plan-gate exit 21（同因，event_key 幂等去重不再推）→ `preflight_2026-09-02_post_plan.json` **全天未生成**；09:15 起 auction/tick/scan/monitor/notify 每分钟 exit 20（gate missing，如 `20260902_093002_127_tick.json`） | ✅ 全天 fail-closed |
| 手动解锁存证 | `outputs/task_logs/2026-09-02/20260902_manual_tick.json`：用户 09:07 授权（follow system sell rules, no human override）；09:23:57 daemon pid=2796 + watcher pid=7112；scope=sell-side only | ✅ 授权与范围清晰 |
| 卖出执行 | 09:40:04 daemon 执行 300489 stop_loss 100股@229.81（`risk_events.jsonl` + ledger fills + watcher 推送 RISK/RISK-EXEC 共 2 条） | ✅ 当日唯一必需出场被完成 |
| daemon 全天存活 | `pos_live.json` 每 ~5s 原子写，最后 time=15:05:57（300468 px=24.43）；watcher `restarts=0`（5s 周期检查，盘中死亡必触发重启） | ✅ 无盘中守护缺口 |
| 15:06「死亡」告警 | `tick_monitor.py` L78-79：daemon 循环在 `hm>'15:05'` 时 break→disconnect→return 0（设计性收盘自退出，该路径无 stdout 输出，与日志仅 264 字节吻合）；watcher 15:06:08 检测到 pid 死亡，因 `hm≥"15:00"` 走「仅告警」分支 | ✅ 收盘自退出，非暴毙 |
| 账本安全 | ledger（15:10:03）：现金 53,910.60 + 持仓 300468×1800；equity 97884.60；回撤 -2.12% 未触线；零新仓成交 | ✅ 无虚假成交 |

### 3. 根因链（三层，均为前一日遗留）

1. **9/1 15:10 close 崩溃**（`20260901_151001_008_close.stderr.log`）：`argparse.ArgumentError: argument --execute: conflicting option string`——close_pipeline.py 处于编辑中间态（与 INC-2026-09-01-02 GBK 事故同模式），参数解析即崩、零工作完成 → **9/1 净值点缺失**（equity_curve 最后一点停在 8/31 99244.12）→ 9/2 晨检「净值守恒」fail。（close 链本身已于 9/1 16:22 commit `770b7a4`「P0.3 close 链修复」修复。）
2. **9/1 16:30 盘后链旧序**：r5p 情绪/r6p 候选跑在 `fetch_daily_minute_rebuild` **之前**，永远读不到当日日线 → 情绪/候选滞后一天（last/max=8/31）→ 9/2 晨检两项 fail。（已由 9/2 晨间会话 commit `bcc967c` 调序修复：rebuild 最先 + qfq_store 并入。）
3. **门禁拓扑放大**：infra status=fail → gate-infra invalid → plan-gate 无法生成 post_plan 门禁 → **进场（scan）与出场（tick）双通道同时被拦**。按 fail-closed 语义手动解锁卖出侧（封进场、放出场），是当日唯一正确的处置。

### 4. 处置结论

- **系统行为正确且安全**：fail-closed 全天生效（灰度决策 买入 000882@1.55 / 300189@6.87 均未执行）；手动解锁的卖出执行器完成当日唯一必需止损（300489 −7.0% 割离）；账本、净值、复盘、日报全链正常。
- **15:06 告警为误报级别**：daemon 09:23:57→15:05:57 全程存活，15:05 后按设计自退出；「持仓失去盘中守护」仅对收盘后时段字面成立，无实际风险。
- **已修复并提交**（晨间会话）：`bcc967c`（盘后链调序=gate 阻塞根因）+ `c274f55`（晨检 GBK/时区修复）。
- **今晚自愈预期**：16:30 盘后链（新序 rebuild→r5p→r6p→next_plan→acceptance）将带回 9/2 当日情绪/候选/日线；今晨 close 已补 9/2 净值点（97884.60）→ 明晨 infra「净值守恒/情绪表/候选表」三项应 pass → 门禁链恢复正常拓扑（9/3 无需手动干预）。

### 5. 改进观察（非阻塞建议）

1. **watcher 告警语义分级**：`_risk_watch_20260902.py` 的 ALERT 分支未区分「盘中暴毙不再拉起」与「收盘自退出」，后者应降级为 INFO/收尾汇总一行（本次告警措辞引发不必要的紧急感）。
2. **equity_curve 缺 9/1 点**：曲线 8/31 99244.12 → 9/2 97884.60 跳变缺中间点；回补需专用脚本（close_pipeline 无 --date 回填口），是否补、如何补待用户决策。
3. **编辑纪律再犯**：9/1 argparse 事故与 8/31-9/1 GBK 事故同模式（任务触发时脚本处于编辑中间态）；建议 15:10/16:30 任务窗口前 30 分钟冻结脚本改动，或改后立即 `py_compile` + 单轮试跑。

---

## INC-2026-09-02-02 · close exit 7（build_board 导入 timing_contract 失败）——已修复并验证

### 1. 事件原文（飞书推送，2026-09-02 15:10:06，即用户转发本条）

```
Yaoban task failed 2026-09-02
Stage: close
Exit code: 7
Action: fail closed; no new positions when the critical intraday path fails.
```

### 2. 链路核验（全部通过 ✅）

| 环节 | 证据 | 状态 |
|---|---|---|
| close 核心步骤 | `20260902_151000_996_close.stdout.log`：决策重建（灰度 2 买 0 卖）、收盘估值 97884.60、review 记账（ledger 15:10:03 saved）、trader_daily rc=0 | ✅ 记账/估值/日报全部成功 |
| 失败定位 | stderr：`ERROR: 子任务失败: trader_daily=0 build_board=1`；`close_pipeline.py` L247-249 → exit 7 | ✅ exit 7 = build_board 单点失败 |
| build_board 死因 | `outputs/intraday/build_board_2026-09-02.log`：`portfolio\ledger.py line 20: from timing_contract import validate_buy_timing → ModuleNotFoundError` | ✅ 根因明确 |
| 引入时间 | `ledger.py` 于 9/1 16:59-17:10（commit `acb0d38` P0.2 信号时序契约）加入该裸导入；9/2 是改动后首个交易日 close → 首次暴露 | ✅ 非当日新错 |
| 影响面 | 仅看板（display 层）；账本/净值/验收证据不受影响 | ✅ 无资金路径影响 |

### 3. 根因判定

- **导入上下文不兼容**：`ledger.py` 内部 `from timing_contract import ...` 是裸导入，仅当 `portfolio/` 自身在 sys.path 时可解析（tick_monitor/close_pipeline/scan_and_confirm 均按此惯例插路径）；`build_board.py` 以包风格 `from portfolio.ledger import ...` 导入且未插 `portfolio/` 路径 → ledger.py 顶层裸导入必然 ModuleNotFoundError。
- **次生日志误导**：`close_pipeline.py` L246「看板已更新」在 rc 检查**之前**无条件打印（stdout 报成功、stderr 报失败并存）。

### 4. 处置结论（2026-09-02 15:43 本会话修复）

- **修复**：`build_board.py` 补 `sys.path.insert(0, BASE/'portfolio')`（与其余消费方惯例一致；**不动资金路径模块 ledger.py**）。
- **验证**：`py_compile` OK；`build_board.py --fills-day 2026-09-02` rc=0，输出「看板已生成: http://127.0.0.1:8765/ (2026-09-02, 持仓 1 只)」；`board.json`（15:43 重生成）持仓与 ledger 一致（300468×1800 / cost 24.611 / stop 23.351）。
- **不重跑 close_pipeline**：其记账路径已正确执行完毕（equity/review 均已落账），仅补跑失败的看板子步骤，避免重复执行账本写入路径。
- **已知显示滞后（非回归）**：看板 `last=24.54` 为 9/1 收盘价——日线仓今晚 16:30 rebuild 后才含 9/2 bar；权威净值以 ledger 97884.60（300468@24.43）为准。

### 5. 改进观察（非阻塞建议）

1. **「看板已更新」打印位置**：移到 rc 检查之后，按实际结果打印。
2. **裸导入脆弱性**：`ledger.py` 的 timing_contract 裸导入对消费方导入方式敏感；可改为双兼容（try 裸导入 except `from portfolio.timing_contract import`）——涉资金路径模块，留待评审后实施。
3. **改动后冒烟**：P0.2（9/1 晚）改 ledger.py 后未跑 build_board 冒烟即过夜；建议资金路径相关模块改动后，收盘子任务三件套（trader_daily/build_board/close_pipeline --dry-run）至少各跑一次再收工。

---


## INC-2026-09-03-01 tick daemon 11:16 崩溃 + 重启排障链 + 缩进自伤事故

### 1. 事件原文

- 11:18 `[tick][ALERT] 监控中断: pos_live 超过300秒未更新`；11:31 假恢复（午休窗口外检查清空状态）；13:05/13:15 再次中断告警
- 上午正常事件（非异常，背景）：10:15 300468 vwap_break alert、10:34 603538 zhaban_sell alert_only（涨停打开回落触发，被"无量/无可卖份额"保守门禁拦截，合规）、10:35 scan --execute 买入 603538 1500股@28.28（e4_support）
- 10:25 monitor exit 4（见附录）

### 2. 链路核验

| 环节 | 证据 | 状态 |
|---|---|---|
| daemon 死亡时刻 | `pos_live.json` 最后写入 11:15:57；孤儿 tmp `pos_live.json.23384.tmp`（11:16） | ✅ 死于 11:16，非 13:05 |
| 死亡直接原因 | `20260903_093001_541_tick.stderr.log`：`tick_monitor.py L35 os.replace(tmp,path) → PermissionError [WinError 5]`，主循环无 try/except，一次异常打死 daemon | ✅ 根因明确 |
| 为何 9/2 未发生 | 9/2 monitor/scan/notify 全天被门禁拦（exit 20）无并发读者；9/3 门禁修复后每分钟读 pos_live.json，daemon 每 5s 写，Windows 上 replace 目标被并发读锁 → 撞锁概率 ~1h46m 后兑现 | ✅ 因果链闭合 |
| scan exit 6 | 每分钟 stderr `伴随监控失效，禁止新仓: tick stale >2m` | ✅ fail-closed 预期行为，根因在 tick |
| 13:41-14:10 重启屡败 | daemon 空转不写 pos_live、无 WARN、`--rounds 1` 却成功——缩进 bug 特征三联 | ✅ 见根因判定 |

### 3. 根因判定（两层）

- **第一层（原始 bug，已修）**：`atomic_json` 的 `os.replace` 无重试 + 主循环无兜底。Windows 下 monitor/scan/notify 并发读 pos_live.json 时 replace 抛 WinError 5，一次撞锁即打死唯一卖出执行器。
- **第二层（修复引入的自伤，已修）**：首轮修复的 Edit 把 `try:atomic_json(...)/time.sleep(interval)` 写成 1 空格缩进——本文件 while 行为 1 空格、循环体为 2 空格，1 空格 = 语句被挪出循环体。daemon 模式（`while True`）永不退出循环 → atomic_json/sleep 永不执行 → 空转（无 sleep 狂转、r125+ 轮次暴增）、pos_live 停更、无 WARN。`--rounds 1` 单轮测试阴差阳错"通过"（出循环后执行到循环外语句），掩盖了 bug。
- **排障弯路（记录备查）**：沙箱 Job Object 在命令返回后清理全部后代进程（DETACHED 假阳性：启动器存活期内验证通过、退出后 Job 关闭才杀子进程）；schtasks 直拉 python.exe 卡在 import 前（恒 3.4MB）；多 watcher 并存互杀对方 daemon 耗尽重启配额；"检测到别人就退出"互斥在 Task Scheduler 启动延迟下双退。TDX 服务器与代码循环经实测均正常（前台 --rounds 1 七秒完整跑通）。

### 4. 处置结论（2026-09-03 14:15 恢复守护）

- **修复 1**：`atomic_json` 对 `os.replace` 加 8 次×50ms PermissionError 重试（读者读完即释放）；主循环对写入加 try/except 兜底（单轮写失败仅告警，下轮重写，daemon 不死）。
- **修复 2**：缩进回正（`try:atomic_json`/`time.sleep` 2 空格 = while 循环体内），文件内注明缩进陷阱注释。
- **新增看门狗架构**（`_tick_watch.py` + `_restart_tick_daemon.py`）：schtasks(沙箱外) → spawner → watcher(文件锁单实例) → daemon(detached)。watcher 每 20s 检查 pos_live 停写 >90s 判挂死自动重启（上限 5 次，超限 halt 告警），15:10 自退，_tick_watch.beat 心跳。
- **验证**：14:14 起 pos_live 每 5s 刷新（300468@24.13 + 603538@28.49 涨停 t1_locked），scan exit_code=0（fail-closed 解除），watcher beat restarts=0。
- **清理**：schtasks 任务 `yaoban_tick_manual` 已删除（避免 23:59 重复触发）；孤儿 tmp 已清。

### 5. 改进观察（非阻塞）

1. **Edit 缩进纪律**：本仓库脚本用 1 空格缩进风格（while 行 1 空格、循环体 2 空格），Edit 的 new_string 必须逐字符核对缩进层级；改循环体后冒烟必须用 **daemon 模式**跑（`--rounds 1` 单轮测不出"语句挪出循环"类 bug）。
2. **DBG 打点待清**：`tick_monitor.py` 主循环现有 [DBG] 诊断打点（约 L99-107），收盘后施工时移除或降级为低频心跳。
3. **603538 数据瞬时缺席**：14:12 一轮 pos_live 缺 603538（get_bars 偶发 None → continue），下轮自愈；若频发考虑 err 计数提示。
4. **watcher 重启计数语义**：重启计数按 watcher 生命周期累计，多 watcher 时代的事件（13:52-13:54 watch_restart #1-3×2 波）与单实例时代不可直接比较；risk_events 中 watch_* 事件可辨析。

### 附录：monitor exit 4（10:25）定因

单轮瞬时事件：该轮 002451/300468/300670 三只数据拉取不完整（`监控数据不完整: unavailable=[...]`），与今晨 08:55 盘前自检 TDX all_servers_failed 警告同源（TDX 服务器间歇抖动）；10:26 下一轮自愈 exit 0。非代码缺陷，无需处置。

---
