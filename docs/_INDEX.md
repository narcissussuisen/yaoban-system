# EvoAlpha 文档索引

> status: active
> verified_at: 2026-09-15
> 本文件是 `yaoban-system/docs/` 的**唯一权威索引**。新增文档必须先在此登记，否则视为游离文件。

---

## 0. 先搞清「两个 docs 目录」——这是"杂乱感"的最大来源

| 位置 | 定位 | 内容 | 谁在用 |
|---|---|---|---|
| **`EvoAlpha/docs/`**（**项目根**） | ⭐ **文档主目录**（日常查阅的就是这里） | **30 个 md + 1 html** ＋ `dashboard/`（`status.json` = 看板**单一真相源**、`roadmap.html` 产物、午盘备份、测试基线）＋ `source_material/` ＋ 新建的 `archive/`。**自有索引：`EvoAlpha/docs/_INDEX.md`** | `tools/build_roadmap_dashboard.py`（`DASH = PROJ_ROOT/"docs"/"dashboard"`）、`tools/roadmap_server.py`；`src/decision_chain/*` 等以 `docs/XXX.md` 形式引用该目录文档 |
| `EvoAlpha/yaoban-system/docs/`　（**本目录**） | **代码仓文档** | 评审（`reviews/`）、循环工程（`loops/`）、时间线还原、以及本索引与新建的 `archive/` | 本索引 |

⚠️ **两处都有 `docs/` 是本项目"看起来杂乱"的最大结构性原因**（此前无任何文档说明）。判断某个 `docs/XXX.md` 引用指向哪边：**看该工具的 `ROOT` 定义**。
⚠️ **`status.json` 不在本目录，别来这里找**。改动它会影响看板与 `build_roadmap_dashboard.py` 的产物。
⚠️ `yaoban-system/docs/dashboard/` **不存在** —— 历史文档里若出现该路径，属过期引用。
⚠️ **项目根 `docs/` 不在 git 仓库内** ⇒ 那里的文件没有版本保护，改动前必须手工备份。

---

## 1. 三态标签约定（所有文档头部必须声明）

```markdown
> status: active | historical | superseded
> verified_at: YYYY-MM-DD        # 仅 active 需要：最后一次确认与代码/账本一致
> superseded_by: <路径或说明>     # 仅 superseded 需要
```

**判据：读者拿它做决策会不会做错？会 ⇒ 不是 active。**
历史文档**只增不改**（数字停留在当时，改了会毁掉可追溯性）。

---

## 2. 当前生效（active）

### 2.1 权威规范 / 契约

| 文档 | 用途 | 备注 |
|---|---|---|
| `DAY_TIMELINE_2026-09-10.md` | **交易日时间链**（人工可读版） | ⚠️ 机器权威是 `scripts/register_schedule.ps1` 的 `$Schedule` 表；2026-09-15 任务已改名 `EvoAlpha*`，正文任务名待同步 |
| `TRADER_ROADMAP_v2.md` | 选手复刻路线图 / 选股规格 | ⭐ 被 `src/core/pattern_pool.py`、`src/core/intraday.py`、`scripts/scan_and_confirm.py` 作为**规格依据**引用 |
| `SELL_EXECUTION_CONTRACT.md` | 卖出执行契约（§4 防审计重建被误当成交） | 被 `src/core/decision_digest.py` 引用 |
| `SYSTEM_OVERVIEW.md` | 系统总览 | 新接手入口 |
| `USAGE.md` | 使用说明 | — |
| `VERSION_HYGIENE_PLAN.md` | 版本卫生与资料整理方案（本索引的规则来源） | 残留清单 + 分层标准 + 排期 |

### 2.2 运维 / 记录

| 文档 | 用途 |
|---|---|
| `D11_BRAIN_SELL_ACTIVATION_2026-09-13.html` | D11 brain 卖出裁量接线记录（含 P0 前置与回滚） |
| `OPS_TASK_RENAME_EVOALPHA.md` | `Yaoban*`→`EvoAlpha*` 改名方案 —— ✅ **2026-09-15 已执行完毕**，保留作映射与回滚依据 |
| `AUCTION_WIRING_CHECK_20260915.md` | 竞价接线检查（2026-09-15） |
| `PLAYER_TRADES_ANCHOR.md` | 选手交易锚点（证据底稿） |
| `TECH_GAPS_vs_author.md` | 与原作者的实现差距分析 |
| `V5_ALIGNMENT.md` | v5 对齐记录（9/9 用户确认） |
| `ROADMAP_v5.md` / `ROADMAP_v5_PLAYER_REPLICA.md` | v5 路线图 / 选手复刻 |

### 2.3 ⚠️ 已确证存在过期内容、**待更新**（不要直接照做）

| 文档 | 过期点 | 现行口径 |
|---|---|---|
| `AUTONOMOUS_PAPER_MANDATE.md` | 「初始资金 100,000」「单票上限 45%」 | 本金 **500,000**（R0.2 / 裁决 8.1，`portfolio/ledger.json::start_cash`）；单票 **30%**（`policy.max_single_weight`，2026-09-13） |
| `BOARD_SPEC.md` | 「单票 ≤45%」 | 同上 30% |
| `TRADING_TEAM.md` | 「单票≤45%」「佣金 0.025%」 | 30%；成本模型见 `portfolio/ledger.py` |
| `PREMARKET_SELFCHECK.md` | 08:45 体检等旧时刻 | 08:35（`register_schedule.ps1`） |
| `LOOP_ENGINEERING.md` | `YaobanLoopEngine`（16:35） | 该任务**已退役**，不在运行时间链内 |
| `ROADMAP.md` | 旧版路线图 | 已被 `ROADMAP_v5*` 取代（superseded） |

---

## 3. 运行时数据（runtime —— **当数据看，别当文档改**）

| 路径 | 性质 |
|---|---|
| `EvoAlpha/docs/dashboard/status.json` | 看板 / roadmap 的**单一真相源**（不在本目录） |
| `loops/state.json` | loop engine 状态（`scripts/loop_engine.py` 读） |
| `loops/evidence_invalidation.json` | 证据失效清单（`src/iteration/` 读） |
| `loops/tasks/next_task.json` | loop engine 任务卡输出 |
| `reviews/_TEMPLATE.md` | 评审模板（供新建评审复用） |

⚠️ 这类文件**不要因为"看起来像 md/json 文档"就归档或改路径** —— 有代码实时读写。

---

## 4. 历史归档（`archive/` —— 只增不改）

**已归档 33 项（2026-09-15）**：事故日志与根因分析、阶段轮次报告（R1P–R7P）、gap 分析、
交付记录、单日/单次快照、以及 `schedule_snapshot_20260910/`（26 份任务 XML 快照）。

典型：`INCIDENT_LOG.md`（65KB，事故史）、`FAIL_RCA_2026-09-03_to_2026-09-10.md`、
`AUTONOMOUS_START_2026-08-31.md`（当时 10 万口径，已被 R0.2 取代）、`STAGE_STATUS.md`（已被看板取代）。

`reviews/`（20 个）与 `player_signals/` 虽属历史产物，但被 `tests/test_evidence_invalidation.py`
等**硬编码引用**，故留在原位；后续若移动须同步改测试。

---

## 5. SSOT 登记（唯一真相源 —— 他处只引用，禁止复制数值）

| 事实 | 唯一来源 | 禁止 |
|---|---|---|
| 本金 | `portfolio/ledger.json::start_cash` | 任何代码/文档写死金额 |
| 风控阈值 | `portfolio/ledger.json::policy` | 任何 `dict.get(k, <数值>)` 形式的 fallback |
| 交易日时间链（机器权威） | `scripts/register_schedule.ps1:: $Schedule` | 手写时刻表 |
| 账本迁移归档 | `yaoban_tasks/ledger_archive/` | 覆盖式改写 |
| 非交易日判定 | `scripts/trading_calendar.py` | 硬编码节假日 |
| 看板叙事状态 | `EvoAlpha/docs/dashboard/status.json` | 在别处维护第二份 |
| 飞书 webhook | `yaoban_tasks/feishu_webhook.txt` | 仓库内任何明文 |

---

## 6. 放置规则（新增文档往哪放）

1. **面向当下操作**（契约 / 时刻表 / 运维手册 / 使用说明）→ 本目录顶层，`status: active`。
2. **带日期的产物**（某次扫描 / 单日复盘 / 阶段轮次报告）→ `archive/`，`status: historical`。
3. **被代码读写**（含路径引用、运行时状态）→ 本目录对应子目录（`loops/` 等），**并在 §3 登记**。
4. **看板数据** → `EvoAlpha/docs/dashboard/`（项目根），**不进本目录**。
5. 新增 `active` 文档**必须**同时登记到本索引 §2，并写 `verified_at`。
