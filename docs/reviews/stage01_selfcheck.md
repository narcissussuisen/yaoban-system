# 阶段零/一 自验报告（数据源 + 盘中感知网络）

> 日期：2026-08-28 晚 · 待 agent 评审

## 交付清单
| 组件 | 文件 | 验证结果 |
|---|---|---|
| TDX 逐笔压力测试 | scripts/stress_tdx_tick.py | **1s 间隔零失败/零空返回，延迟 P50≈67ms P95≈400ms** → 秒级做T可行 |
| 全市场异动扫描 | scripts/market_scan.py | **3.3s 完成 4689 只**（腾讯批量，60s 目标超额达标）；B股/ST 过滤 |
| 盘中增量管线 | scripts/pull_intraday.py | 当日 1m 分钟实时拉取 → JSON（已验证 8/28 全天） |
| 盘中主循环 | scripts/scan_and_confirm.py | 扫描→异动池(≤20)→粗筛(换手3-30%/额≥1亿/涨幅-1~9.8%/ST剔除)→分时三引擎→触发建仓；板块轮动 board_momentum.json |
| 持仓秒级监控 | scripts/tick_monitor.py | 守护模式：3s 轮询，止损/炸板/破线减半自主执行，做T观察提示；pos_live.json |
| 定时任务 | YaobanScanConfirm(2分钟)/YaobanTickDaemon(09:31-15:05)/YaobanPremarket(8:50)/YaobanClosePipeline(15:40) | 已注册（schtasks 确认） |

## 自验点
1. **前视纪律**：scan_and_confirm 非交易时段默认退出（--force 仅测试）；三引擎逐bar只用 ≤当前bar 数据；tick_monitor 交易时段过滤
2. **做T收益源**：3s 轮询 × P50 67ms 延迟 → 触发到记录 <4s，差价 1.5% 级别的机会不会错过
3. **自主执行**：scan_and_confirm --execute 触发即建仓（单票≤30%，最多 2 只/轮）；tick_monitor 止损/炸板/破线减半自动执行
4. **失败降级**：腾讯批次失败跳过继续；TDX 连接失败重试服务器；无候选打印空
5. **数据完整性**：扫描覆盖 4689 只（排除 688/689/4/8/92 不可买 + 399/899 指数）

## 待评审关注点
1. 秒级监控的限频长期风险（压力测试 30 次通过，需 1 日实盘观察）；
2. 异动池粗筛阈值（换手 3-30%/额≥1亿/涨幅窗口）为经验初值，需选手证据校准；
3. scan_and_confirm 每轮最多确认 8 只（分时拉取瓶颈），异动池 20 只内选择策略；
4. 做T目前为提示级（未自动执行）——差价确认规则待选手案例校准后自动化。