# 计划任务命名统一：Yaoban* → EvoAlpha*

> status: active
> verified_at: 2026-09-15

> 状态：**待执行（需维护窗口）**　提出：2026-09-11 15:30（用户裁定"全面改名"）
> 范围：17 个在生产计划表中的 `Yaoban*` 任务 + 17 个引用脚本
> 相关：`docs/archive/INCIDENT_LOG.md` INC-2026-09-11-01、`docs/EVOALPHA_VISION_ALIGNMENT.md §1.7`

## 1. 背景

用户可见层面已统一（见 §3），但 OS 计划任务仍名 `Yaoban*`，与 `VibeResearch*` 混编，
导致运维界面（Task Scheduler / preflight 诊断 / 晨检卡 / 复盘摘要）仍出现旧品牌，
观感上"像两个系统"。本文件是把该层改造为 `EvoAlpha*` 的执行依据。

命名血缘（权威）：逐妖交易团队 → 妖板系统/yaoban-system（策略内核）→ **EvoAlpha**。

## 2. 改动映射

纯字符串替换：**`Yaoban` → `EvoAlpha`**（大小写敏感，故 `yaoban-system` / `yaoban_tasks`
路径与 `yaoban_tick_manual` 之类小写标识**不受影响**）。例：`YaobanPreflight` → `EvoAlphaPreflight`。

### 2.1 计划任务清单（唯一注册源 `scripts/register_schedule.ps1` 表内，共 17 项）

| # | 现名 | 模式 | 触发 | 状态 |
|---|---|---|---|---|
| 1 | YaobanPreflight | infra | 08:35 | Ready |
| 2 | YaobanSelfHeal | selfheal | 08:36 | Ready |
| 3 | YaobanPremarket | premarket | 08:50 | Ready |
| 4 | YaobanPlanGate | plan-gate | 08:55 | Ready |
| 5 | YaobanMorningCheck | morning-check | 08:58 | Ready |
| 6 | YaobanTdxProbe | tdx-probe | 09:00 起 30min ×7h | Ready |
| 7 | YaobanAuctionMonitor | auction | 09:15 起 1min ×15m | Ready |
| 8 | YaobanEventNotify | notify | 09:15 起 1min ×5h46m | Ready |
| 9 | YaobanTickDaemon | tick | 09:30 | Running |
| 10 | YaobanScanConfirm | scan | 09:30 起 1min ×5h31m | Ready |
| 11 | YaobanIntradayMonitor | monitor | 09:30 起 1min ×5h31m | Ready |
| 12 | YaobanClosePipeline | close | 15:10 | Ready |
| 13 | YaobanPostCloseChain | post-close | 15:35 | Ready |
| 14 | YaobanEveningCheck | evening-check | 17:30 | Ready |
| 15 | YaobanTdxServerVerify | tdx-verify | 周六 10:00 | Ready |
| 16 | YaobanBoardRefresh | run_board_refresh.ps1 | 09:35 / 13:05 | Ready |
| 17 | YaobanStatusPush | run_status_push.ps1 | 13 个时刻 | Ready |

### 2.2 引用脚本清单（17 个文件）

| 文件 | 引用处 | 性质 |
|---|---|---|
| `scripts/register_schedule.ps1` | 17 个 `Name=` + 1 个 `New-YaobanAction` | **注册源（必改）** |
| `scripts/preflight.py` | `EXPECTED` 11 名 + `TRIGGER_EXPECTED` 17 名 | **门禁校验（必改）** |
| `scripts/check_morning.py` | 任务时刻表 2 行 | 晨检卡可见 |
| `scripts/collect_daily_acceptance.py` | `TASKS` 11 名 + 注释 | 验收 `tasks` 字段可见 |
| `scripts/collect_static_readiness.py` | 2 行（时刻表已陈旧） | 静态就绪检查 |
| `scripts/status_push.py` | `TASK_NAME` 自身名 | 任务历史查询 |
| `scripts/log_error_digest.py` | TDX 降级提示文案 2 处 | 复盘摘要可见 |
| `scripts/daily_iteration.py` | 文档串 2 处 | 注释/来源标注 |
| `scripts/verify_tdx_servers.py` | 1 处注释 | 注释 |
| `scripts/_tick_watch.py` | 1 处 docstring | 注释 |
| `scripts/install_loop_timer.ps1` | 3 处 | 二级注册脚本 |
| `scripts/register_board_refresh.ps1` | 2 处 | 二级注册脚本 |
| `scripts/register_p0_schedule.ps1` | 9 处 | 二级注册脚本 |
| `scripts/register_daily_iteration.ps1` | 3 处 | 二级注册脚本 |
| `scripts/run_daily_iteration.ps1` | 1 处 | 二级 runner |
| `scripts/_register_manual_tick_task.py` | 2 处 | 一次性工具 |
| `scripts/_task_diag.py` | 1 处 | 诊断工具 |

### 2.3 不在本次范围

- **约 12 个 Disabled 遗留任务**（YaobanDailyAcceptance / DailyRebuild / DailyCandidates /
  DailySignal / DataRefresh / LoopEngine / MonthlyOptimize / NextPlan / OCR / TickCollect /
  `yaoban_tick_manual` 等）：不参与生产链，暂不改名；其中 `YaobanNextPlan` 已确认与
  rebuild 职责重叠（INC-2026-08-31-02 §5.4），建议后续直接停用而非改名。
- `EvoAlpha\Vibe-Research` 内的 `VibeResearch*` 命名：另一子系统，本次不动。

## 3. 已完成（2026-09-11 15:04–15:30，本会话）

- `scripts/run_trading_task.ps1` 两处用户可见文案统一（全库仅此两处不叫 EvoAlpha）：
  - L28 `Yaoban task failed {day}` → `EvoAlpha｜任务失败 {day}`，字段改中文卡风格
    （`阶段：`/`退出码：`/`处置：`），`$details` 标签一并中文化；
  - L79 `Yaoban daily acceptance passed` → `EvoAlpha｜日终验收通过` + `证据：`。
- 验证：文案无代码消费方；BOM 保住 `EF BB BF`；`Parser::ParseFile` syntax_errors=0；
  `preflight.runner_ok` 8 token 全在；试渲染与新卡风格一致。

## 4. 前置条件：BOM 地雷（**执行前必须先处理**）

`scripts/` 下 ps1 的 BOM 现状盘点（2026-09-11 实测）：

- ✅ 有 BOM（安全）：`post_close_chain.ps1`、`register_schedule.ps1`、`run_status_push.ps1`、`run_trading_task.ps1`
- ⚠️ **含中文但无 BOM（吞行地雷）**：`register_daily_iteration.ps1`、`run_daily.ps1`、
  `run_daily_iteration.ps1`、`run_board_refresh.ps1`、`_setup_cmd_runners.ps1`

其中前 3 个在本次改动清单内。**任何编辑前先加 BOM**，编辑后逐字节复查首 3 字节仍为 `EF BB BF`：

```powershell
$b=[IO.File]::ReadAllBytes($p)
if($b[0] -ne 0xEF){ [IO.File]::WriteAllBytes($p,[byte[]](0xEF,0xBB,0xBF)+$b) }
```

（地雷机制与验证法：见 `win-ps1-gbk-bom-trap` skill。同类事故：INC-2026-08-31-02、INC-2026-09-02-02。）

## 5. 执行步骤（严格顺序，不可并发）

**窗口约束**：必须在 `YaobanPostCloseChain`(15:35) 与 `YaobanEveningCheck`(17:30)、
`YaobanStatusPush` 末次(18:30) **全部跑完**之后 → 建议 **19:00 之后**，或周六
`YaobanTdxServerVerify`(10:00) 跑完之后。**禁止在 15:35–18:35 之间执行**（链与验收按名读取任务）。

1. **备份**：导出全部 `Yaoban*` 任务 XML + 复制 17 个引用脚本到
   `outputs/_taskrename_backup_<ts>/`（含旧名清单）。
2. **加 BOM**：对 §4 中本次涉及的 3 个无 BOM 文件执行字节级加 BOM。
3. **改代码**：对 §2.2 的 17 个文件做 `Yaoban`→`EvoAlpha` 精确替换；
   每个 .ps1 改后复查 BOM，`python -m py_compile` 全部改动 .py。
4. **删旧任务**：`schtasks /Delete /TN \Yaoban<X> /F` × 17（先确认 XML 已备份）。
5. **重注册**：`powershell -File scripts/register_schedule.ps1`（表驱动 + 注册后自校验）。
6. **验证**（§6）。
7. **收口**：更新 `register_schedule.ps1` 表头注释与本文档状态为"已完成"。

## 6. 验证清单（全绿方可收工）

- [ ] 17 个 `EvoAlpha*` 任务存在且 `State=Ready`（tick 盘中为 Running），ACTION 指向
      `yaoban_tasks\launch.ps1` 且 `-Mode` 正确
- [ ] `Yaoban*` 旧任务全部消失（`schtasks /Query | findstr Yaoban` 为空）
- [ ] 触发器时刻与 §2.1 表一致（preflight 的 `TRIGGER_EXPECTED` 漂移检查 pass）
- [ ] `python scripts/preflight.py` → **任务Action pass**、`runner_ok=True`
- [ ] `python -X utf8 scripts/preflight.py --post-plan` → status=pass
- [ ] `python scripts/collect_daily_acceptance.py --date <上一交易日>` 的 `tasks` 字段全部解析成功（无 None）
- [ ] `python scripts/check_morning.py` 与 `scripts/status_push.py` 冒烟通过（卡片文案无残留 Yaoban）
- [ ] 所有 `Yaoban`→`EvoAlpha` 的替换文件里已无 `Yaoban` 前缀任务名

## 7. 已知风险与缓解

| 风险 | 缓解 |
|---|---|
| 漏改某引用 → 次日门禁"任务Action"全 bad（同 INC-2026-08-31-02 链路） | §6 强制逐项验证；`runner+watch` 8 token 契约改动后必查 |
| 旧任务未删 → 新旧同名双跑（scan/tick 双写账本） | 步骤 4 先删后建；步骤 6 显式 `findstr Yaoban` 核对 |
| `LastRunTime` 归零 → 次日「任务历史 unproven」告警 | 预期噪音，非故障；1 个交易日后自愈 |
| .ps1 改中文触发 GBK 吞行 | 先加 BOM；`Parser::ParseFile` syntax_errors 必须为 0 |
| 编辑窗口撞上任务触发（本月已三度致事故） | 严守 §5 窗口约束，19:00 后或周六执行 |

## 8. 回滚

脚本级：从 `outputs/_taskrename_backup_<ts>/` 还原 17 个文件（或 `git checkout -- scripts/`）。
任务级：`schtasks /Create /XML <备份XML> /TN \Yaoban<X>` 逐条还原，或直接
`powershell -File scripts/register_schedule.ps1`（回滚代码后即按旧表重建）。
