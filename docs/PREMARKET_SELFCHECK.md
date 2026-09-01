# 盘前自检（preflight）使用说明

> 对齐 tpoint `selfcheck_daily.py` 能力升级（2026-09-01）：在原有两阶段门禁（fail-closed 契约不变）之上，
> 新增 Markdown 报告、异常日志、处理建议表、彩色控制台摘要、资源检查与飞书状态卡片推送。

## 一、调度与两阶段门禁

| 时间 | 任务 | 命令 | 用途 |
|---|---|---|---|
| 08:45 | YaobanPreflight (Mode=infra) | `preflight.py` | 基础设施门禁：日历/TDX/腾讯/账本/研究数据/任务/看板/进程/脚本/资源 |
| 08:50 | YaobanPremarket | `premarket.py` | 刷新外盘快照并原子更新当日计划，成功后发飞书盘前汇报 |
| 08:55 | YaobanPlanGate (Mode=plan-gate) | `preflight.py --post-plan --push` | 计划验收门禁：当日计划 + 外盘新鲜度；**成功后推送飞书状态卡片** |

- 门禁 JSON 契约不变：`outputs/preflight_<date>_<stage>.json`（date/stage/time/results/summary/status）
- fail-closed 语义不变：任一 critical 项失败 → 退出码非零 → `run_trading_task.ps1` 发失败告警并阻断后续任务
- `run_trading_task.ps1` 的 Gate 校验（status/date/stage/生成时间新鲜度）不受影响

## 二、新增产物（tpoint 对齐）

| 产物 | 位置 | 说明 |
|---|---|---|
| Markdown 自检报告 | `outputs/selfcheck/YYYY-MM-DD_HHMMSS_<stage>.md` | 总体结果 + 统计表 + 分类明细 + 异常项与建议 |
| 异常日志（累积） | `outputs/selfcheck/anomalies.log` | 每次 FAIL/WARN 追加，含详情与处理建议 |
| 飞书审计 | `outputs/notifications/delivery_*.jsonl`（kind=selfcheck） | 卡片推送结果审计，与 feishu_notify 同口径 |

## 三、新增检查项

- **资源使用**（类别"资源"）：CPU / 内存 / 磁盘 C: / 磁盘 F:（PowerShell CIM，不依赖 psutil）
  - CPU ≥80% WARN、≥95% FAIL；内存 ≥85% WARN；磁盘 ≥90% WARN、≥95% FAIL
- 原有 14 项检查均保留，并补充了 `category` 分组字段（不影响 JSON 消费者）

## 四、飞书推送

- `--push` 参数：交易日（以交易所日历为准）**必发状态卡片**（✅正常/⚠️注意/❌异常，含关键组件健康检查、潜在风险、异常项与处理建议）
- 非交易日仅当门禁非 pass 时推送文本告警，正常不扰民
- 推送失败不影响门禁退出码（降级为 WARN 日志），fail-closed 语义不受推送影响
- webhook 来源与 feishu_notify.py 一致：环境变量 `YAOBAN_FEISHU_WEBHOOK` 或外部密钥文件 `C:\Users\YZP\WorkBuddy\yaoban_tasks\feishu_webhook.txt`

## 五、手动使用

```powershell
# 完整自检（基础设施阶段，不推送）
python scripts/preflight.py --date 2026-09-01

# 计划验收阶段 + 推送状态卡片
python scripts/preflight.py --date 2026-09-01 --post-plan --push

# 查看最新报告
Get-ChildItem outputs\selfcheck\*.md | Sort-Object LastWriteTime -Descending | Select -First 1
```

## 六、测试

```powershell
python -m unittest tests.test_preflight_report -v
```

覆盖：JSON 契约字段、Markdown 报告结构、建议表完整性、状态卡片模板（红/绿）、推送审计、资源解析。
