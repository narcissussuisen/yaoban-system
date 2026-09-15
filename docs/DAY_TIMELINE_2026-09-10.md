# EvoAlpha 单日时间线还原 · 2026-09-10（星期四）

> 人读版叙事时间线。机器证据表见 `outputs/reviews/timeline_2026-09-10.md`（280 事件，0 缺失源，由 `scripts/day_timeline.py` 生成）。
> 全部结论挂本地证据路径；`preflight_*.json` 会被重跑覆盖，门禁历史态一律以 `outputs/selfcheck/*.md` 为准。
> 本文件只描述系统行为，不含任何交易动作建议。

---

## 0. 一句话结论

**当日不是「某处报错」，而是四条互相独立的事故链在同一个交易日叠加。** 其中三条由外部数据源（TDX 停供）与跨日遗留缺陷（9/9 计划缺失）引起，一条是**我们自己的门禁误报**；而唯一一条贯穿全期的红灯（验收 `live_tick`）与前三条都无关。

终局：盘后链数据段（rebuild / r5p / r6p / next_plan）**全部 success**，`acceptance` rc=3（`incomplete`，7 项未过），`log-review` rc=0。当日计入 **非正常日**。

---

## 1. 分钟级时间线

| 时刻 | 事件 | 结果 | 证据 |
|---|---|---|---|
| 08:45:01 | infra 盘前自检（16 项） | 14 PASS / 2 WARN / **0 FAIL**；净值守恒 PASS `curve=calc=96589.58`，`rev=24 pos=2` | `selfcheck/2026-09-10_084501_infra.md` |
| 08:50:02 | premarket | **rc=2**：`ERROR: 盘后预生成计划缺失: outputs\plans\2026-09-10_plan.json` | `task_logs/2026-09-10/20260910_085000_644_premarket.stderr.log` |
| 08:55:02 | post_plan 自检（18 项） | 14 PASS / 2 WARN / **2 FAIL**（当日计划缺失、外盘新鲜度 `generated=2026-09-09`）→ gate `status=fail` | `selfcheck/2026-09-10_085502_post_plan.md` |
| 08:56:03 | 推送 | `failure:2026-09-10:plan-gate` | `delivery_20260910.jsonl` |
| 08:58:02 | morning-check | **rc=2**；晨检 `pass=false`（`chain_ok=false, preflight_ok=false, no_failure_push=false`） | `validation/morning_check_2026-09-10.json` |
| 08:58:05–06 | 推送 | 晨检卡 `selfcheck:…:morning` + `failure:…:morning-check` | `delivery_20260910.jsonl` |
| **09:15:01→10:22:02** | **门禁拦截窗口** | 所有 `Gate('post_plan')` 任务 **rc=21**：notify×68、scan×53、monitor×53、auction×16、tick×1 = **194 次** | `task_logs/2026-09-10/*.json` |
| 09:18:30–10:20:33 | **人工介入窗口**（非排期） | 15 次手工 post_plan 自检；其中 09:18:30/09:19:51 两次含 `任务Action bad=[全部任务]` 与 `残留进程: Get-CimInstance : 拒绝访问` —— **受限运行环境造成的假 FAIL** | `selfcheck/2026-09-10_09*.md`、`_10*.md` |
| 09:19:51 | 计划补生成 | `outputs/plans/2026-09-10_plan.json` 落盘 | `outputs/plans/` |
| 10:19:57 | 风控 | 603538 `stop_loss` → `alert_only`（被保守门禁拦截） | `intraday/risk_events.jsonl` |
| 10:21:42 | post_plan 自检 | **0 FAIL** → gate `status=pass` | `selfcheck/2026-09-10_102142_post_plan.md` |
| 10:22:57 | 守护 | tick watchdog v2 up，daemon pid=15340 | `intraday/risk_events.jsonl` |
| 10:23:00 | 风控 | 603538 `stop_loss` → **execute** | 同上 |
| 10:23:03–05 | 推送 | `monitor-gap:…:scan:1023`、`monitor-gap:…:monitor:1023`、`failure:…:scan` | `delivery_20260910.jsonl` |
| 10:24:05 | **成交** | `sell 603538 1500 @26.19`（reason=`stop_loss`，plan_ref=`tick-risk`） | `portfolio/ledger.json · account.fills` |
| 10:24:02 / 10:25:03 | 推送 | `monitor-recovered:…:monitor:1024` / `…:scan:1025` —— 门禁解除后链路自愈 | `delivery_20260910.jsonl` |
| 10:24:49 | 推送 | `tdx-incident-recovery:2026-09-10` | 同上 |
| 10:30:00 | **成交** | `buy 300394 100 @275.74`（plan_ref=`offplan-2026-09-10:300394`）→ 后成为验收 `offplan_fills` 失败项 | `portfolio/ledger.json · account.fills` |
| 11:02:03 | 推送 | `failure:2026-09-10:tick` | `delivery_20260910.jsonl` |
| 11:03:16 / 11:07:08 / 11:15:25 | 守护 | tick watchdog v2 up ×3（**当日共 4 次 watcher 启动**） | `intraday/risk_events.jsonl` |
| 11:17:25 | 风控 | 300394 `vwap_halve` → `alert_only` | 同上 |
| 13:00:05 | scan | **rc=6**：`伴随监控失效，禁止新仓: tick stale >2m` | `task_logs/…/20260910_130001_299_scan.stderr.log` |
| 13:05:04 | Vibe live-tick 校验 | **4 项 check false**：`guard_time_advances` / `live_count_complete` / `every_live_px_finite` / `not_stale`；`invalid_live_px_symbols.first=["300394"]` | `Vibe-Research/validation/live-ticks/latest.json` |
| 15:05:53 | pos_live 收盘快照 | `300468 @24.1`、`300394 @269.0`（验收 `tick_snapshot` 门槛 ≥14:55，达标） | `acceptance_2026-09-10.json · tick_snapshot` |
| 15:06:13 | 守护 | `tick_guard_state`: `state=closed_ok`，`detail=watchdog closed clean` | `outputs/intraday/tick_guard_state.json` |
| 15:10:01 | close 任务 | **rc=3**：`ERROR: 2026-09-10 非交易日或数据未就绪(600000 最新 无)` | `task_logs/…/20260910_151001_329_close.stderr.log` |
| 15:13:37 | infra 重跑 | **rc=1**，`净值守恒 curve=93325.36 calc=93200.36` → **假阳性**（见 §2-C） | `selfcheck/2026-09-10_151337_infra.md` |
| 15:14:33 / 15:14:53 | 推送 | `failure:…:infra`、`failure:…:gate-infra`（均由此假阳性引发） | `delivery_20260910.jsonl` |
| 15:17:22 | close 手工补跑 | **rc=0**，收盘净值 93325.36，持仓 2 只 | `task_logs/…/20260910_151722_508_close.stdout.log` |
| 15:19:32 | 代码变更 | `scripts/close_pipeline.py` 落地：TDX 降级时收盘估值改用腾讯实时报价 | 文件 mtime |
| 15:19:40 | infra 再跑 | 仍 FAIL 净值守恒（同一假阳性；此时价源尚未更新） | `selfcheck/2026-09-10_151940_infra.md` |
| 15:21:04 | tick | **rc=23**：`gate stale/outside session … age_min=299`（post_plan gate 生成于 10:21:42，15:05 后属盘外时段） | `task_logs/…/20260910_152104_697_tick.stderr.log` |
| 16:30:00 | **盘后链启动** | run_id `pc_20260910_163001` | `chain_manifest_2026-09-10.jsonl` |
| 16:30:01→17:49:19 | rebuild | rc=0，**耗时 79 分钟**（9/9 为 26 分钟）；`ok=5291 skip=353`；stderr：`WARN: TDX 60m不可用, 降级腾讯 mkline m60 增量回填`；期间 `腾讯空响应连续25只, 冷却60s续跑` | `20260910_163000_633_post-close.stdout/stderr.log` |
| 17:49:20→17:54:06 | r5p | rc=0 | `chain_manifest` |
| 17:54:06→18:09:49 | r6p | rc=0 | 同上 |
| 18:09:49→18:24:48 | next_plan | rc=0 → `outputs/plans/2026-09-11_plan.json` | 同上 |
| 18:24:48→18:24:56 | acceptance | **rc=3**（`status=incomplete`，`final=true`），7 项未过 | `acceptance/acceptance_2026-09-10.json` |
| 18:24:56→18:24:57 | log-review | rc=0；报 25 errors / 6 warnings（**旧代码，仍漏采 205 次任务失败**） | `reviews/log_review_2026-09-10.md` |
| 18:24:58 | 推送 | `failure:2026-09-10:post-close` | `delivery_20260910.jsonl` |

---

## 2. 四条因果链（互相独立）

### A. TDX 全源停供链（外部）
```
08:45 TDX 10 台全失败(WARN, 降级放行)
  → 09:15 起 10+9 节点真实取数全空(TCP 通、2 字节空 body)
  → 14:58 tdx_state.json state=down
  → 15:10 close rc=3「非交易日或数据未就绪(600000 最新 无)」
  → 17:49 rebuild 被迫降级腾讯 mkline m60，26 分钟 → 79 分钟
```
处置已在当日完成：`src/core/tencent_minline.py` 备胎接入 tick/scan/monitor/preflight，`10:24:49` 推送 `tdx-incident-recovery`。
**下游代价**：rebuild 耗时 3 倍（见 §4 前向风险）。

### B. 计划缺失 → 门禁连锁链（跨日遗留，**当日最大单点损失**）
```
9/9 next_plan rc=2（baostock 日历失败）
  → 9/10 计划文件缺失
  → 08:50 premarket rc=2「盘后预生成计划缺失」
  → 08:55 post_plan 自检 2 FAIL → gate status=fail
  → 09:15-10:22 所有盘中任务 rc=21，共 194 次被拦
  → 09:19:51 人工补生成计划
  → 10:21:42 gate 转 pass → 10:23 链路恢复（monitor-recovered 推送）
```
**这是当日可归因损失的主体**：194 次任务运行 + 开盘后前 52 分钟（09:30-10:22）无扫描/监控/推送能力。

### C. 净值守恒假阳性链（**我们自己的误报**）
```
15:17 close 写入 9/10 估值点 93325.36（mark: 300468@24.10, 300394@269.00）
  而独立价源 daily_rebuilt 仍停在 9/9（24.64 / 258.03）—— rebuild 16:30 才起跑
  → 15:13 / 15:19 两次 infra 直接比对两个不同"数据世代" → 必不相等
  → 差 125.00，恰等于 1800×(24.10−24.64) + 100×(269.00−258.03) = −972 + 1097
  → 假 FAIL + 假 failure:infra + 假 failure:gate-infra 推送，污染 anomalies.log 与 selfcheck
```
**已于当日修复**（`scripts/preflight.py` 改为按数据世代比较并区分方向：账本落后价源=真问题保持 critical；价源落后账本=数据未就绪降为 WARN）。四场景回放验证通过。
**反向确认**：当晚 rebuild 写出 9/10 收盘价后，独立源算出 `calc=93325.36`，与账本 `curve=93325.36` **完全相等** —— 9/10 净值点无误。

### D. live_tick 校验链（**贯穿全期，与前三条无关**）
```
13:05:04 校验器 10 秒内采样 2 点，要求 pos_live_time 严格递增且所有 live.px 有限
  首样本：pos_live_time 未推进、live_count=1≠持仓 2、300394 价格非法、stale=true
  10 秒后第二样本：已恢复正常(2/2)
  → 一次瞬时半更新快照同时打挂 4 项 check → pass=false → rc=2
  → 验收 live_tick=false + tasks=false → 当日计入非正常日
```
同一失效模式自 **9/3 起连续 6 个交易日**发生（见 `docs/FAIL_RCA_2026-09-03_to_2026-09-10.md` §4.1）。旁证：13:00:05 scan 报 `tick stale >2m`；当日 watcher 启动 4 次。

---

## 3. 门禁传播定量

| mode | rc | 次数 | 首次 | 末次 |
|---|---|---|---|---|
| notify | 21 | 68 | 09:15:02 | 10:22:02 |
| scan | 21 | 53 | 09:30:02 | 10:22:02 |
| monitor | 21 | 53 | 09:30:03 | 10:22:02 |
| auction | 21 | 16 | 09:15:03 | 09:30:03 |
| tick | 21 | 1 | 09:30:03 | 09:30:03 |
| premarket | 21 | 1 | 15:14:54 | 15:14:54 |
| plan-gate | 21 | 1 | 15:16:08 | 15:16:08 |
| tick | 23 | 1 | 15:21:06 | 15:21:06 |
| **合计** | | **194** | | |

连续失败窗口（仅计窗口内相邻失败间隔 ≤300 秒者）：notify 09:15:00→10:22:01 连续 68 次；scan 09:30:00→10:23:01 连续 54 次；monitor 09:30:01→10:22:01 连续 53 次；auction 09:15:01→09:30:01 连续 16 次。

---

## 4. 盘后链与验收

| stage | 起 | 止 | rc | status |
|---|---|---|---|---|
| rebuild | 16:30:01 | 17:49:19 | 0 | success |
| r5p | 17:49:20 | 17:54:06 | 0 | success |
| r6p | 17:54:06 | 18:09:49 | 0 | success |
| next_plan | 18:09:49 | 18:24:48 | 0 | success |
| acceptance | 18:24:48 | 18:24:56 | **3** | **failed** |
| log-review | 18:24:56 | 18:24:57 | 0 | success |

**验收 `status=incomplete`，7 项未过**：

| 检查项 | 归因 |
|---|---|
| `task_log_continuity` | 链 B：09:15–10:22 盘中窗口无成功记录 |
| `auction_latest` / `auction_freeze` / `auction_delivery` | 链 B：16 次 auction 全 rc=21，竞价零运行 |
| `live_tick` | 链 D：校验器竞态 |
| `offplan_fills` | 10:30 `300394` 计划外买入（`offplan_fills_today=["300394"]`） |
| `tasks` | `YaobanAuctionMonitor` / `YaobanTickDaemon` / `VibeResearchLiveTickValidation` |

未失败项（正面证据）：`tick_snapshot` 15:05:53 达标；`tick_watchdog` `restart_events=0`、`fail_events=[]`（当日 4 次 watcher 启动均未触发 `watch_limit` 隔离）；`next_plan` 产出 `2026-09-11_plan.json`（**9/9 的 next_plan 失败未复现**）。

---

## 5. 前向风险（本次新增观察）

盘后链耗时从 9/9 的 56 分钟拉长到 **116 分钟**（rebuild 79 分钟占 68%）。acceptance 的 final 资格要求 `final_window_ok = 当日且 ≥15:05`（`FINAL_NOT_BEFORE=(15,5)`）—— **跨零点即降级为 `_probe.json`，当日不再产出正式验收**。今日 18:26 收尾，距零点尚有约 5.5 小时余量；但若 TDX 长期停供使 rebuild 稳定在 80 分钟以上，叠加 r6p 波动，余量会被持续压缩。建议纳入监控。

> **重要更正（18:5x 补记）**：上段的「TDX 降级导致 rebuild 变慢」**不是完整成因**。收尾清点时发现一条并行工作流处理的事故更为严重：
> **腾讯降级回填误用「覆盖写」→ 5291 只标的 3.3 年历史被截成 1 行**（见 `scripts/restore_daily_rebuilt_ths.py` docstring；`fetch_daily_minute_rebuild.py` 的修复 mtime 18:27:40，**晚于链的 rebuild 17:49 完成时刻**）。
> 本文件 18:5x 实测：`daily_rebuilt` 共 5643 个 parquet，**抽样 200 只中 183 只（91.5%）行数 < 60，中位数 1 行**；两个持仓 `300468`/`300394` 各仅剩 1 行（即 9/10 行）→ **恢复尚未完成**。
> 对本文件结论的影响：§2-C 与 §4 关于「9/10 净值点为 93325.36、独立源一致」的判断**仍然成立**（所依据的正是现存的那一行）；但 §1 时间线中 16:30–17:49 rebuild 的成因描述需按本条理解。完整说明见 `FAIL_RCA_2026-09-03_to_2026-09-10.md` §8。

---

## 6. 证据索引

| 类别 | 路径 |
|---|---|
| 机器时间线 | `outputs/reviews/timeline_2026-09-10.md` |
| 跨日 FAIL 汇总 | `outputs/reviews/fail_rollup_2026-09-03_to_2026-09-10.md` |
| 当日复盘（链产出，含已知漏采） | `outputs/reviews/log_review_2026-09-10.md` |
| 门禁历史态 | `outputs/selfcheck/2026-09-10_*.md`（19 份） |
| 任务原始日志 | `outputs/task_logs/2026-09-10/`（1049 个 json） |
| 推送审计 | `outputs/notifications/delivery_20260910.jsonl` |
| 账本/成交 | `portfolio/ledger.json · account` |
| 盘后链 | `outputs/acceptance/chain_manifest_2026-09-10.jsonl` |
| 验收 | `outputs/acceptance/acceptance_2026-09-10.json` |
| 风控/守护 | `outputs/intraday/risk_events.jsonl`、`outputs/intraday/tick_guard_state.json` |
| live-tick 校验 | `Vibe-Research/validation/live-ticks/latest.json` |
| 数据源状态 | `outputs/validation/tdx_state.json` |

**复现命令**：
```powershell
$py='C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
& $py -X utf8 yaoban-system\scripts\day_timeline.py --date 2026-09-10
& $py -X utf8 yaoban-system\scripts\fail_rollup.py --from 2026-09-03 --to 2026-09-10 --json
```
