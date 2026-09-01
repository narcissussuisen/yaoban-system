# 妖板交易系统 yaoban-system

把「妖板选手方法论拆解」从书面手册（v4.0）落地为**可运行、可验证、可自迭代**的量化研究系统。

> EvoAlpha 过渡说明：本目录是自主量化投资团队的策略与模拟交易核心。当前所有自主决策运行于模拟盘，真实下单接口保持关闭；系统通过回测、样本外验证、风控门禁和持续复盘进行进化。

## 系统定位

- **知识层**：`../妖板选手方法论拆解/` 下的 v4.0 手册、OCR 语料、素材提取（本项目参数的唯一来源）
- **数据层**：akshare → SQLite 本地库（指数日线 / 个股日线 / 涨停池 / 市场活跃度）
- **研究层**：回测引擎 → 参数优化 → walk-forward 稳健性验证
- **运行层**：每日盘后信号管线（环境打分 → 候选池 → 信号报告）+ 交易日志与执行合规检查
- **迭代层**：周绩效 → 月参数网格 → 季方法论迭代，规则版本化上线

## 目录结构

```
yaoban-system/
├── config/parameters.toml   # 全部规则参数（带置信度标注），单一事实源
├── docs/ROADMAP.md          # 落地路线图（P0~P6 + 里程碑）
├── src/
│   ├── config.py            # TOML 配置加载（标准库 tomllib，零依赖）
│   ├── data/                # store.py: SQLite 存储；fetchers.py: akshare 采集（重试/退避）
│   ├── core/                # env_score.py: 环境打分；indicators.py: 指标；strategies.py: 五大战法（P2）
│   └── report/              # Markdown/HTML 报告生成
├── scripts/
│   ├── backfill_data.py     # P1: 历史数据回填
│   ├── daily_pipeline.py    # P4: 每日盘后管线（骨架）
│   └── verify_env.py        # 环境自检
├── tests/
│   ├── cases.json           # 视频案例库（已知结果，回测回归测试集）
│   └── test_smoke.py        # 冒烟测试
└── data/yaoban.db           # SQLite（不入库）
```

## 快速开始

```powershell
# 1. 环境自检（不需要 akshare 的部分）
python scripts/verify_env.py

# 2. 回填数据（需要完整权限读取 py_libs 中的 akshare）
$env:PYTHONPATH = "..\py_libs"
python scripts/backfill_data.py --indexes --stocks --limit-pool 5

# 3. 跑冒烟测试
python -m unittest discover -s tests -v
```

## 自动化调度（P4）

```powershell
# 注册每日 15:35 盘后信号任务（生成 scripts\run_daily.cmd 后注册）
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
# 任务: YaobanDailySignal（日志: outputs\signals\daily.log；报告: outputs\signals\YYYY-MM-DD_signal_report.md/.html）
# 手动触发: Start-ScheduledTask -TaskName "YaobanDailySignal"
```

> 注：`--update` 会增量拉取全股票池（东财限流下自动回退新浪/腾讯），首次运行约 20-40 分钟；
> 任务已设 1 小时执行时限。安装脚本为纯 ASCII（PS 5.1 兼容），runner.cmd 以 GBK 写入（cmd 原生编码）。

## 数据口径

- 指数：`ak.stock_zh_index_daily`（新浪，date/open/high/low/close/volume）
- 个股：`ak.stock_zh_a_hist`（东财，前复权；东财接口有频率限制，采集器内置重试与退避）
- 涨停池：`ak.stock_zt_pool_em`（东财，仅近期数据，需每日增量积累）
- 活跃度：`ak.stock_market_activity_legu`（乐咕，仅当日快照）

## 版本与迭代

- 规则参数变更必须：① 通过 walk-forward 样本外验证 → ② 模拟盘灰度 ≥1 个月 → ③ 合入并 bump 版本（v4.x）
- 每次迭代记录见 `docs/iterations/`

## 合规边界

本目录由 EvoAlpha 统一入口接入，保留 yaoban-system 旧路径以兼容已有计划任务。策略结果必须经过模拟盘和风险门禁验证，不代表稳定收益承诺。
