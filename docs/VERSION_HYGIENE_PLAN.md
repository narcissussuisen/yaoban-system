# 版本卫生与资料整理编排方案

> status: active
> verified_at: 2026-09-15

> 提出：2026-09-15 09:15（用户裁定："不能让任何历史版本残留导致严重错误" + "资料很多但很杂乱，要按可读复用的标准重新整理编排"）
> 状态：**方案待确认，分阶段执行**。T−0（交易日）只做 B 类，C 类排到收盘后。
> 前置事实：2026-09-15 已把工作区积压的 117 项未提交改动按工作线分 **11 个 commit** 落库并推送
> （`e3a75c4` → `9fb2de1`，`ls-remote` 已校验一致）。

---

## 1. 问题定义

两件事被混在一起，必须分开：

| # | 问题 | 性质 | 危险度 |
|---|---|---|---|
| **A** | **数值/口径残留** —— 代码里的 fallback 默认值仍是历史版本（本金 10 万、单票 45%） | 逻辑缺陷 | **高**：账本损坏时静默按旧口径出数，数字看着正常、实则错 |
| **B** | **资料杂乱** —— 历史版本变动混在当前生效的代码/文档里，无法一眼分辨"哪个是现在的" | 可读性/可维护性 | 中：误读、误改、重复排查 |

**A 会直接导致严重错误，B 是它的温床**（B 让人改不对地方、也发现不了 A）。故本方案先收 A、再治 B、最后用护栏锁死。

---

## 2. 残留真实清单（已逐行核过，区分真残留 / 误报 / 史实）

### 2.1 A 类：真残留（必须收口）

| 位置 | 内容 | 分类 | 处置 |
|---|---|---|---|
| `scripts/scan_and_confirm.py:695` | `policy.get('max_single_weight', 0.45)` | **C 决策实时** | 收盘后：fallback 去掉默认值，改 fail-closed（policy 缺失拒下单） |
| `scripts/close_pipeline.py:263` | `st.get('start_cash', 100000.0)` 参与 `peak_equity` → 回撤 → 熔断 | **C 决策实时** | 收盘后：同上，peak 取不到应显式中断而非按旧本金 |
| `scripts/decision_cli.py:24` | `state.get('start_cash',100000)` 影响下单数量 | **C 决策实时** | 收盘后（docstring 已于今日改掉写死的 45%） |
| `scripts/feishu_notify.py:156` | `_close_message` 的 `start_cash` fallback（**今日遗漏点**） | B 观测/推送 | 今日已改 `_plan_message`；此处同样改为 `_capital_label` 口径 |
| `portfolio/ledger.py:118,122,141` | 新建账本的**默认模板** `start_cash=100000.0` | B 初始化 | 默认值必须与当前口径一致，或改为**必填无默认**（推荐） |
| `tools/verify_r02c_ledger_swap.py:75` | 验证工具 fallback | B 工具 | 同上，去默认 |
| `scripts/build_board.py:127` | `b / b0 * 100000` 基准归一化 | B 展示 | 确认口径后改为读账本本金 |
| `scripts/initialize_autonomous_paper.py` | 旧 10 万初始化脚本（**无调用方**） | B 孤儿 | 归档 `scripts/_legacy/` + 头部废弃说明 |

**已修复（今日）**：`feishu_notify.py` 盘前模式行/纪律行、`notify_trading_events.py` 成交说明、`status_push.py` 的 `_start_cash`（fail-loud）。

### 2.2 B 类：真残留——命名

**17 个生产计划任务仍名 `Yaoban*`**，与 `VibeResearch*` 混编。已有完整执行文档 `docs/OPS_TASK_RENAME_EVOALPHA.md`
（含 17 文件引用清单、BOM 前置条件、验证清单、回滚）。**本方案直接引用，不重复设计。**

需同步修改的**生产引用**（11 个文件的硬编码任务名）：
`register_schedule.ps1`(19) · `preflight.py`(12) · `make_baseline_manifest.py`(17) · `build_roadmap_dashboard.py`(5) ·
`collect_daily_acceptance.py`(3) · `check_morning.py`(2) · `log_error_digest.py`(2) · `daily_iteration.py`(2) ·
`status_push.py`(1) · `verify_tdx_servers.py`(1) · `_tick_watch.py`(1) + 测试 5 个文件。

**二级注册脚本**（`register_p0_schedule.ps1` / `install_loop_timer.ps1` / `register_board_refresh.ps1` /
`register_daily_iteration.ps1`）：需先判定**是否已废弃** —— 若已退役则归档而非改名。

### 2.3 误报（**不要改**）

| 命中 | 为何不是残留 |
|---|---|
| `l2_quality_score.py:23,31` / `p1a_prevalidation.py:22,27` 的 `100000` | 是 HHMM 时间编码常量（`hh*10000+mm*100`），非本金 |
| `src/decision_chain/llm.py:304` 的 `%100000` | 缓存文件名随机后缀 |
| `src/core/sentiment.py:58` 的 `45%` | 炸板率阈值，与仓位无关 |
| `tools/build_persona_*.py` 的 `4.45%` | 个股指标偏差值（证据引用） |
| `rebuild_morning.py` / `replay_0827.py` 的 `100000` | 历史回放脚本，按当时本金复算，属**史实** |
| `tests/*` 的大量 `100000` | 测试夹具的孤立数值，与生产口径无关（且部分测试故意用非 50 万以证"文案跟随账本"） |
| `yaoban_tasks` 路径（32 处） | **活路径**（任务运行目录），小写标识，改名方案明确排除 |

### 2.4 史实（**保留不改**，但要标注状态）

`docs/archive/INCIDENT_LOG.md`(18) · `docs/reviews/*` · `docs/archive/R6_GATE_DECISION.md` · `docs/TRADING_TEAM.md` ·
`docs/BOARD_SPEC.md` · `docs/RUNBOOK_0831.md` · `docs/archive/DELIVERY_REPORT.md` · `docs/archive/STAGE_STATUS.md` ·
`docs/archive/AUTONOMOUS_START_2026-08-31.md` —— 它们记录的是**当时的决策与数字**，改动会破坏可追溯性。
**但必须加上状态标签**（见 §3），否则会被当成现行规范——这正是"杂乱感"的来源。

⚠️ 例外：`docs/AUTONOMOUS_PAPER_MANDATE.md`（授权书）与 `docs/PREMARKET_SELFCHECK.md`（盘前自检说明）
是**面向当下操作**的，其中「单票上限 45%」「08:45 体检」已过期 ⇒ 应更新，而非标注史实。

---

## 3. 「可读复用」的分层标准

### 3.1 三态标签（零破坏，先立此规则）

每份 docs 文档头部必须有且仅有一个状态：

```markdown
> status: active | historical | superseded
> verified_at: YYYY-MM-DD        # 仅 active 需要
> superseded_by: <path>          # 仅 superseded 需要
```

- **active** —— 当前生效的规范/契约/运维手册。改动必须同步 `verified_at`。
- **historical** —— 历史决策与复盘记录。**只增不改**，数字停留在当时。
- **superseded** —— 已被取代但仍被引用。必须给出取代者路径。

判据：**读者拿它做决策，会不会做错**——会，就不是 active。

### 3.2 单一真相源（SSOT）登记

`docs/_INDEX.md`（新建）列出每类事实的唯一来源，其他位置一律引用、禁止复制数值：

| 事实 | 唯一来源 | 禁止 |
|---|---|---|
| 本金 | `portfolio/ledger.json::start_cash` | 任何代码/文档写死金额 |
| 风控阈值 | `portfolio/ledger.json::policy` | 任何 fallback 默认值 |
| 交易日时间链 | `scripts/register_schedule.ps1` 的 `$Schedule` 表 | 手写时刻表 |
| 非交易日判定 | `scripts/trading_calendar.py` | 硬编码节假日 |
| webhook | `yaoban_tasks/feishu_webhook.txt` | 仓库内任何明文 |

### 3.3 目录约定（**不急，最后做**）

现状 `docs/` 33 个 md 平铺 + `reviews/` + `loops/`。建议目标结构：

```
docs/
  _INDEX.md          # 权威索引（active 优先，historical 折叠）
  contracts/         # active：契约类
  ops/               # active：时间链/运维/自检
  archive/           # historical：事故日志、历史 review、旧版决策
```

⚠️ **移动文件会打断交叉引用**（大量文档以路径互引，测试也读路径）。
故**必须最后做**，且移动前先 grep 全部引用点。**不建议在 T−0 或本周做。**

---

## 4. 分阶段执行计划（含窗口约束）

> 窗口约束源自 `docs/OPS_TASK_RENAME_EVOALPHA.md` §5：任务改名**禁止在 15:35–18:35 之间**执行
> （盘后链与验收按任务名读取）。其余改动按 `t0-safe-prod-patch` 的 A/B/C 分类排期。

| 阶段 | 内容 | 分类 | 窗口 | 前置 |
|---|---|---|---|---|
| **P0-1** | Yaoban→EvoAlpha 任务改名（17 任务 + 11 生产引用 + 5 测试） | B | **今晚 19:00 后** 或周六 10:00 后 | BOM 前置处理（§4 文档已列 3 个地雷文件） |
| **P0-2** | A 类数值残留收口：`scan_and_confirm.py:695`、`close_pipeline.py:263`、`decision_cli.py:24`、`feishu_notify.py:156`、`ledger.py` 默认值、`verify_r02c_ledger_swap.py`、`build_board.py` | **C** | **今日 15:05 后**（收盘链跑完） | 全量测试全绿 |
| **P0-3** | 孤儿脚本归档：`initialize_autonomous_paper.py` → `_legacy/`；判定 4 个二级注册脚本去留 | B | 今日 15:05 后 | 先 grep 确认零调用方 |
| **P1-1** | 三态标签落到全部 docs（33 个 md） | B | 本周任意时段 | §3.1 规则确认 |
| **P1-2** | 新建 `docs/_INDEX.md` 权威索引 | B | P1-1 完成后 | — |
| **P1-3** | 修正两份过期 active 文档（授权书 45%、PREMARKET 时刻表） | B | 本周 | — |
| **P2** | 残留护栏自动化（§5） | B | 本周 | P0-2 完成 |
| **P3** | 目录重构（`contracts/`/`ops/`/`archive/`） | B | **暂缓**，需先 grep 全部引用点 | P1 稳定运行 ≥1 周 |

---

## 5. 护栏设计（本方案最值钱的一件）

仿照仓库既有成功先例 `tests/test_ps1_encoding_guard.py`（BOM 棘轮）与
`orchestrator/test/evoalpha_child_process_windows_hide.test.ts`（windowsHide 棘轮）：

新增 `tests/test_version_residue_guard.py`，扫描**生产代码**（`scripts/` `src/` `portfolio/` `tools/`，
排除 `docs/` `tests/` `_legacy/`），断言：

1. 不含硬编码本金字面量（`100000` / `10万`），白名单仅限**已登记的非本金用途**
   （时间编码、缓存后缀）——白名单以 `# residue-guard: allow <理由>` 行内注释显式声明，
   **每个豁免都必须写理由**，杜绝"加个白名单了事"。
2. 不含 `max_single_weight` 的旧默认值 `0.45`（作为 default 参数时）。
3. 不含 `Yaoban<Xxx>` 形式的任务名（改名完成后启用；改名前置灰名单）。
4. 新增「护栏自身有效」用例：扫描到的检查点 < N 则失败（防棘轮被绕过）。

配套行内规范：**任何 `dict.get(key, <默认值>)` 用于金额/阈值时，默认值必须为 `None` 并显式处理**，
不得填历史数值 —— 这条正是 A 类残留的根因模式。

---

## 6. 已知风险

| 风险 | 缓解 |
|---|---|
| 改名与数值收口都要碰 `scripts/`，同窗口并发会互踩 | **P0-1 与 P0-2 串行**，P0-1 先做且逐项验证 |
| 改名漏改引用 → 次日门禁「任务Action」全 bad（同 INC-2026-08-31-02） | 用文档 §6 验证清单；`runner+watch` 8 token 契约改动后必查 |
| 旧任务未删 → 新旧同名双跑 → scan/tick 双写账本 | 先删后建 + `findstr Yaoban` 显式核对 |
| .ps1 改中文触发 GBK 吞行 | 先加 BOM；改后 `Parser::ParseFile` syntax_errors 必须为 0 |
| 目录重构打断引用 | P3 暂缓；动手前 grep 全部引用点 |
| fail-loud 化的反向风险：正常路径若走到 fallback 会从"静默出错"变"显式中断" | 每处改动须证明**正常账本下不触发 fallback**（本次 `_start_cash` 已实测：正常 500000.0） |

---

## 7. 未决项（需用户裁定）

1. **P0-1 改名窗口**：今晚 19:00 后执行，还是等周六 10:00 后（更宽裕、且次日可观察完整交易日）？
2. **改名范围**：约 12 个 Disabled 遗留任务（`YaobanDailyAcceptance` / `DailyRebuild` / `LoopEngine` 等）
   建议**直接停用/删除**而非改名 —— 是否同意？
3. **目录重构（P3）**：是否真的要做？收益是长期可读性，代价是打断引用 + 一次大 diff。
4. **`ledger.py` 默认本金**：改为「必填无默认」（更严格，但会改到 3 处调用方）还是「默认值与当前口径一致」？
