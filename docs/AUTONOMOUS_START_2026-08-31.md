# 2026-08-31 自主模拟盘启动记录

- 2026-08-30 23:07：旧模拟台账已按原始 SHA-256 归档至仓库外 C:\Users\YZP\WorkBuddy\yaoban_tasks\ledger_archive。
- 新台账：现金 100,000 元、空仓、基准中证1000、模式 autonomous_paper。
- 飞书 webhook：保存于仓库外受限文件；仓库审计仅记录消息 SHA-256、HTTP 状态和飞书业务码。
- 2026-08-30 23:14：配置测试成功，HTTP 200，业务码 0。
- 生产 Python 全套测试：28/28 通过。
- PowerShell 生产包装器：语法通过。
- 周日 post-plan 预检失败属于预期：非交易日、无当日计划、外盘快照过期；不得作为 8 月 31 日交易日门禁证据。
- 已知限制：生产做 T 执行器尚未完成，只能进行影子评估；不得虚构做 T 成交。
## 交易日前加固（2026-08-30 23:24）

- 正式交易日历证据确认 2026-08-31 为交易日：baostock query_trade_dates，is_checked_date_trading_day=true；证据位于 outputs/preflight_calendar/2026-08-31/readiness/。
- Vibe 健康门禁兼容当前真实响应字段 ok=true，同时保留 status=ok/healthy 兼容；新增单元测试。
- 全市场扫描改为 fail-closed：温度数据缺失、行情批次失败、行情覆盖率低于 90% 或 TDX 连接失败均返回非零并禁止新仓。
- 失败飞书事件按日期和阶段去重，避免每 2 分钟重复告警。
- 飞书盘中汇报同时读取 alerts JSON 和 tick 守护 risk_events.jsonl，覆盖止损、炸板、破 VWAP、数据失效和执行失败。
- 加固后生产 Python 测试 35/35 通过，修改脚本编译通过。
- 尚待交易日真实证据：08:45 基础门禁、08:50 飞书盘前、08:55 计划门禁、09:40 连续 tick、15:40 收盘汇报、17:00 重建。

## 调度与盘中链路加固（2026-08-30 23:30）

- 盘中监控改为读取 outputs/plans 当日正式计划；缺计划、TDX 连接失败或任一监控标的数据缺失均非零退出并 fail-closed。
- 模拟买入的成交时间改为下一根分钟 K 线的实际时间，不再错误记录为信号分钟。
- 8 个原有妖板任务统一允许电池运行、切换电池不停机、允许唤醒、错过后补跑并保持 IgnoreNew。
- TickDaemon 执行时限由 30 分钟延长至 6 小时；DailyRebuild 延长至 2 小时。
- 新增 YaobanEventNotify：周一至周五 09:35 起每 1 分钟读取审计事件和成交，持续 5.5 小时，幂等发送飞书。
- 新增 YaobanDailyAcceptance：周一至周五 17:10 汇总任务结果、门禁、飞书、实时 tick、台账和下一日计划，生成 acceptance JSON；通过后发送飞书摘要，失败保持非零并告警。
- 交易日前验收器试运行按预期返回 fail，未把尚未发生的任务误报为通过。
- 静态 readiness 证据：outputs/acceptance/static_readiness_2026-08-31.json，状态 pass。
- 当前生产 Python 测试 41/41 通过；PowerShell 包装器语法与 Python 编译均通过。

## 陈旧证据与生产入口加固（2026-08-30 23:40）

- 发现旧 preflight 文件虽然目标日期为 2026-08-31，但生成自然日为 2026-08-30 且仍引用旧 4 仓台账；生产 Gate 现强制校验 status、date、stage、生成自然日和最大年龄。
- infra 门禁最大年龄 30 分钟，post_plan 最大年龄 480 分钟；陈旧门禁端到端测试返回退出码 23，临时夹具已删除。
- 修复 Windows PowerShell 5.1 对无 BOM 中文硬编码路径的误解：生产 wrapper 改从 ASCII 目录 root.txt 读取 UTF-8 根路径。
- 生产入口端到端测试：缺门禁返回 20，飞书失败通知 HTTP 200/业务码 0；不再出现乱码路径。
- ASCII launcher 新增按日期/模式/时间戳保存 stdout、stderr 和元数据 JSON，并保留原始退出码；未知模式实测退出 22 且 stderr 正文成功落盘。
- 日末验收改到 19:10，避免与最长 2 小时的数据重建抢跑；YaobanTickDaemon 已加入必过任务清单。
- 当前生产测试 45/45 通过；两个 PowerShell 入口语法通过；静态 readiness 再次为 pass。

## 最终交易日前验证与时间阻塞（2026-08-30 23:43）

- 生产 Gate 双向端到端验证完成：缺失门禁退出 20，陈旧门禁退出 23，当日新鲜且阶段匹配的只读 notify 入口退出 0。
- 新鲜门禁测试使用临时替换并完成原样恢复；历史 post-plan 文件最终 SHA-256 恢复为 4CAFBFF445080F8923EAD03DD8CE73ADF2DD70BA625B652AB920179723B413E8。
- notify 正向测试持久化日志显示 exit_code=0、stdout sent_or_seen=0/failed=0、stderr 为空。
- 最终生产测试 45/45 通过；静态 readiness 为 pass。
- 当前时间仍为 2026-08-30 23:42，实际交易日任务尚未到触发时间。客观待验收项为 08:45/08:50/08:55/09:31/09:35/09:40/15:40/17:00/19:10 的真实任务结果。
- 时间条件解除后，应恢复本目标并以 outputs/acceptance/acceptance_2026-08-31.json 为主证据，不得用静态 readiness 替代真实运行验收。

## 集合竞价与完整交易时段监控调整（2026-08-31 00:10）

- 原 09:31/09:35/09:40 安排只覆盖连续竞价分钟线策略，遗漏 09:15–09:30 集合竞价观察，已确认不合理并完成重构。
- 新增 YaobanAuctionMonitor：09:15 起每分钟全市场腾讯快照，持续至 09:29；记录计划候选、持仓、价格、涨幅、累计量额、换手和活跃排名。
- 09:25 后生成 auction_freeze_YYYY-MM-DD.json，明确 read_only=true、orders_allowed=false；竞价阶段不生成模拟订单，冻结结果通过幂等飞书事件汇报。
- tick 守护、全市场扫描和候选/持仓规则监控统一提前至 09:30；扫描和监控均每分钟，tick 守护常驻并每轮重新加载台账，因此可接管盘中新建仓位。
- 连续 tick 验证从 09:40 提前到 09:32，并启用 WakeToRun、StartWhenAvailable 和电池运行。
- 连续交易脚本窗口改为 09:30–11:30、13:00–15:00；午间任务正常跳过交易逻辑，事件通知保持运行。
- 09:30–09:34 分钟数据不足按积累阶段处理，不虚构信号、不误报数据故障；需要至少 5 根分钟线的模式约 09:35 后完整启用。
- 日末验收新增 auction_latest、auction_freeze、auction_delivery 和 YaobanAuctionMonitor 任务结果四类证据。
- 静态 readiness 现强制校验各任务精确启动时间，避免后续误改回 09:35；本次发现并修复 Vibe 实时验证 WakeToRun=false。
- 当前腾讯快照可审计参考价、相对昨收涨幅、累计量额、换手和候选排名变化；尚不提供可靠的竞价未匹配量、完整买卖委托队列与撤单轨迹，不能宣称 Level-2 竞价盘口覆盖。
- 最终测试 49/49 通过，Python/PowerShell 编译通过，精确时间静态 readiness=pass。

## 盘前内容新鲜度与全天连续性验收（2026-08-31 00:15）

- 核对新自主台账：equity_curve 已包含 2026-08-31 基准净值 100000，当前净值守恒契约正确；周日 curve=None 是初始化前旧预检，不代表当前状态。
- 确认 08:50 生产链已调用 premarket.py：先刷新外盘，再原子更新当日计划，成功后才发送飞书；08:55 随后执行 post-plan 门禁。
- 盘前计划新增 premarket_refreshed_at 和 global_snapshot_sha256；post-plan 门禁要求刷新时间位于当日 08:30–09:25，且计划内 SHA-256 必须与当前 global_snapshot.json 字节内容一致。
- 日末验收新增 task_logs 连续性：集合竞价成功日志最大间隔 5 分钟，且覆盖 09:20 前至 09:25 后；扫描和规则监控分别覆盖上午、下午，成功日志最大间隔 15 分钟；事件通知覆盖 09:20 前至 14:50 后，最大间隔 15 分钟。
- Windows 任务继续使用 IgnoreNew，避免重叠扫描同时写账；因此调度目标是每分钟触发，验收口径是无超过阈值的监控盲区，而非虚称每一分钟均必然完成一轮。
- Vibe API 当前 HTTP 200 且 ok=true；盘前新鲜度、连续性合成测试、Python 编译均通过。
- 最终测试 53/53 通过，精确时间静态 readiness=pass。

## 真实交易日基础门禁演练与日历语义修复（2026-08-31 00:21）

- 在不发送飞书、不交易的隔离演练中真实调用 2026-08-31 日历、TDX、腾讯快照、空仓台账、研究数据和 Vibe 健康检查。
- 首次演练发现关键缺陷：日历 last_trading_day 在当天为交易日时等于 2026-08-31，导致门禁错误要求盘前情绪/候选已包含尚未发生的当天数据，返回 2 项失败。
- 修复为使用日历明确提供的 previous_trading_day=2026-08-28，并强制 previous_trading_day < checked day；不再用含义不同的 last_trading_day 作为盘前数据截止日。
- 修复后真实演练结果：11 项通过、0 项失败、1 项非关键警告（尚未到时的任务历史）；TDX 最后日线 2026-08-28、腾讯快照 513 bytes、净值 100000、Vibe HTTP 200/ok=true。
- 失败演练保留为 outputs/acceptance/infra_drill_2026-08-31_0015.json；修复通过演练保留为 outputs/acceptance/infra_drill_2026-08-31_0020_fixed.json。
- 每次演练后正式 preflight_2026-08-31_infra.json 均恢复至原 SHA-256 204E02B26EF11C824BF4F0281A88FFFEBFC5C3BD5EDD71E017552944331A114B，且其生成时间仍为 2026-08-30 20:35:51；因此 08:45 前无法误用演练文件放行。
- post-plan 交易门禁新增 15:05 后强制过期，收盘后不能继续复用当日交易授权。
- 飞书正式事件键 premarket/close/auction-freeze/acceptance:2026-08-31 均未被测试占用。
- 最终测试 56/56 通过，PowerShell 语法通过，静态 readiness=pass。

## 盘中即时监控中断报警与新仓联锁（2026-08-31 00:25）

- 日末连续性验收之外，独立 YaobanEventNotify 现每分钟执行监控健康检查。
- 竞价宽限至 09:20，之后最近 5 分钟无成功 auction 日志即飞书报警；连续竞价宽限至 09:40，scan/monitor 最近 15 分钟无成功日志即报警；tick 从 09:35 起要求 pos_live 最近 2 分钟新鲜。
- 每个中断 episode 只发送一次故障消息；模块恢复后发送一次恢复消息，并允许未来新 episode 再次告警。
- 飞书失败时不推进 episode 状态：故障消息失败会下一分钟重试，恢复消息失败会保留 active 状态继续重试。
- 移除 scan/monitor 完成后的重复通知调用，生产环境只由独立 notifier 统一读取事件和写入飞书幂等状态，避免三进程并发覆盖 delivery_state。
- 告警已形成实际风控联锁：09:40 后自主扫描在拉取全市场和产生信号前检查 monitor 最近 15 分钟成功记录及 tick 最近 2 分钟快照；任一失效返回退出码 6并禁止新仓。扫描自身断档由 notifier 报警，但不作为下一次扫描的前置条件，确保可自动恢复。
- 午后 13:00 首轮若伴随模块尚未刷新会被保护性拒绝，伴随数据新鲜后下一分钟自动恢复，不使用午休前快照开仓。
- 本轮未发送测试飞书、未修改正式台账；watchdog 和通知状态测试均在临时目录/mock webhook 完成。
- 最终测试 65/65 通过，Python 编译、PowerShell 语法和静态 readiness 均通过。

## 最终生产链审计与待运行状态（2026-08-31 00:36）

- 逐层核对退出码：业务 Python 非零由 Run-Stage 保存并传播；launcher 通过 Start-Process 捕获子 PowerShell ExitCode、持久化元数据并原样退出；Task Scheduler Action 指向同一 launcher。
- 关键一次性任务新增有限重试：RestartCount=3、RestartInterval=PT1M；重复触发任务继续使用 IgnoreNew。重试不绕过任何 Gate，不改变 fail-closed 语义。
- 真实时间仍为 2026-08-31 00:35，首个正式任务 08:45 才触发；当前 acceptance_2026-08-31.json 仍是交易日前预验收，不能作为真实日验收证据。
- 日末验收新增 tick_snapshot 硬检查，要求当日 pos_live.json 存在 date/time；同时修复 next_plan 判断，要求未来文件日期、JSON date 和 picks 三者一致。
- 日末验收连续性要求仍有效：auction 5 分钟、scan/monitor/notify 15 分钟最大成功间隔，上午/下午覆盖分别验证。
- 事件通知 watchdog 已与自主新仓联锁：monitor 超过 15 分钟或 tick 超过 2 分钟不新鲜时，scan 返回退出码 6并禁止新仓；飞书失败不提交 episode 状态并持续重试。
- 正式任务当前下一次运行：08:45 infra、08:50 premarket、08:55 plan-gate、09:15 auction/notify、09:30 tick/scan/monitor、09:32 Vibe live-tick、15:40 close、17:00 rebuild、19:10 acceptance。
- 最终测试 69/69 通过；Python 编译、PowerShell 语法、静态 readiness=pass。
- 本轮没有发送测试飞书、没有修改正式台账，也没有创建后台任务。

## Vibe 看板名称补全（2026-08-31 01:10）

- 用户指出看板（持仓快照/盘前候选/盘中事件/板块轮动）多处只显示代码无名称。
- 股票名称统一来源：data/stock_names_full.json（全市场 5000+ 只），看板装配层 vr_trader.ts 新增 stockNameOf 兜底；plan_daily 生成候选时写入 name；monitor_intraday 写告警时写入 name。
- 行业名称来源：申万宏源官方 institute-sw API（www.swsresearch.com/institute-sw/api/index_publish/current + component_stocks），共 124 个官方二级行业；与 data/sw_industry_history.csv 的当前行业码（每股票取 start_date 最大行）按成分股集合 Jaccard 重叠对齐，ratio>=0.5 且按码唯一；官方未发布指数的 110300 林业Ⅱ / 110600 农业综合Ⅱ 人工固化（脚本 MANUAL_FILLS）。
- 产物：data/sw_l2_names.json（126 项）、data/sw_l2_alignment_report.json、data/sw_publish_names.json（官方 124 项缓存）；复现：python scripts/_align_sw_names.py。
- 注意：官方口径下 710400=软件开发、640200=专用设备、270500=消费电子（与 RUNBOOK 人工概括"计算机设备/机械设备"不同，以官方为准）。
- Vite 5930 只代理 /api/* 到 8766 编排器；8766 按 INTEGRATION_PROGRESS.md 方式以 DEEPSEEK_API_KEY 环境注入重启，token 仍从 .local/api.token 读取；web 页面需刷新使 Home.tsx 变更（HMR）生效。
- 后端已验证：/api/vr/picks 候选全部带名称（百川股份/红墙股份/四川美丰/新洋丰）；/api/vr/board sectors 带行业名（渔业/林业Ⅱ/种植业/房地产服务/饰品/农化制品）；alerts 301205=联特科技。
- 测试 76/76 通过（新增 test_dashboard_names 7 项含看板代码全覆盖断言）；orchestrator tsc 通过。

