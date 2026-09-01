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

## 4. 升级路径

Phase 1C 交易员角色接管影子账户卖出（独立 ledger）；生产卖出保持 tick_monitor 守护直到 1D 晋级门禁通过并 canary 灰度。
