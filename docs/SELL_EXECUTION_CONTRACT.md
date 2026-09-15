# 卖出执行契约（B0-P0-3 显式化）

> 状态：Phase 0 交付（2026-09-01）。蓝图对 B0-P0-3 的验收动词是"**明确**每个卖出触发的生产/影子状态和实际成交契约"——本文档即该显式化交付；完整卖出引擎属 Phase 1C 交易员角色。

## 1. 触发源 × 状态 × 执行契约矩阵

| 触发源 | 生产/影子状态 | 执行契约 | 执行器 | provenance |
|---|---|---|---|---|
| 止损（px <= stop_px） | **生产执行** | 实时 tick 价成交，全量卖出 | tick_monitor `--execute-risk` | signal_ts=decision_ts=触发墙钟；decision_id=`dec-tick-{day}-{sym}` |
| 炸板卖出（hi 触板后回落） | **生产执行** | 实时价（last）成交，全量卖出 | tick_monitor `--execute-risk` | 同上 |
| 破 VWAP 减半 | **生产执行** | 实时价（last）成交，半仓（100 股整数倍） | tick_monitor `--execute-risk` | 同上 |
| 盘后决策重建（止损/炸板） | **仅审计** | `close_pipeline.py` 重建全天决策只记录不执行；`--execute` 被 return 5 守卫永久禁用（盘后不得重复执行订单） | close_pipeline（审计层） | close_decision_{day}.json 留痕 |
| R3′ 卖出机制（龙头联动/板块退潮/次高点等 12 类） | **影子/评审态** | 历史评审有条件放行（as-of 接口 + 卖出原因库），未接生产 | Phase 1C 交易员角色 | 蓝图 §6 决策契约 |
| 完整 sell 引擎 | **未建设** | Phase 1C 交付 | — | — |

## 2. 卖出侧时序契约

tick_monitor 是唯一盘中卖出执行器，判的是**当前 tick 价 vs 触发线**，无历史重放窗口：
- signal_ts = decision_ts = 触发时刻墙钟（同一 tick）
- fill.ts = recorded_at ≈ 同刻（自然满足 signal <= decision <= recorded 单调）
- **不适用 120 秒新鲜度门**（无重放缺陷类；新增门无收益）

守卫测试：`tests/test_tick_snapshot_acceptance.py`、`tests/test_monitor_fail_closed.py`（account_mode 限定 autonomous_paper；监控失效 fail-closed）。

## 3. T+1 约束（两执行器共享）

`ledger.sellable_qty`：当日买入不可卖（T+1），按可卖数量而非"当日有买入即全锁"。

## 4. 执行权威 vs 审计记录（2026-09-11 显式化）

**执行权威是唯一的一份**：`portfolio/ledger.json` → `account.fills`（成交流水）与 `account.positions`（持仓）。
只有下列路径会写成交：`decision_cli`（盘中新仓）与 `tick_monitor --execute-risk`（盘中风险卖出）。
`ledger.sellable_qty`、`equity()`、`close_pipeline` 估值、看板持仓**一律以 `account.positions` 为准**。

**`reviews[*]` 是审计重建，不是成交**：`close_pipeline.py` 按当日分钟数据重建"规则全天会怎么打"
（counterfactual），供人工复盘与归因；该流水线**只记录不执行**——`--execute` 被 `return 5` 守卫
永久禁用，且从不调用 `fill()`。故 `reviews[*].buys / sells` 与 `account.fills` **系统性不一致**，
这是设计如此，不是账本错误。

审计实录（INC-2026-09-11-01 附带发现）：

| 日期 | `reviews[*].sells` | 当日 `account.fills` | 实际持仓影响 |
|---|---|---|---|
| 2026-08-31 | 300489 100股 @239.75（止损） | 无卖出 | 300489 持有至 09-02 才真实卖出 @229.81 |
| 2026-09-03 | 603538 1500股 @28.49（止损） | 无卖出 | 603538 持有至 09-10 才真实卖出 @26.19 |
| 2026-09-04 | 300468 1800股 @27.5（炸板卖出） | 无卖出 | 300468 持有至 09-11 才真实止损 @23.31 |

净值侧同样自证：09-04 曲线 101917.58 是持仓按 27.5 盯市（未实现浮盈），**不是**卖出实现。

**机器可读标记**（防后来者/未来代码误读而双重计算）：`ledger.record_review` 以 `setdefault`
补齐 `kind='audit_counterfactual'` / `executed=False` / `authority='account.fills'`；
`close_pipeline` 的 `decisions`（进而 `close_decision_{day}.json`）同带三键。
`setdefault` 不覆盖既有键，**历史 `reviews` 条目不追溯改写**。

## 5. 升级路径

Phase 1C 交易员角色接管影子账户卖出（独立 ledger）；生产卖出保持 tick_monitor 守护直到 1D 晋级门禁通过并 canary 灰度。
