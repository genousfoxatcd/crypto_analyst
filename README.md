# Super Mario — 加密货币量化分析与模拟交易 Agent 🔍

> 从 OpenClaw 平台迁移至 WorkBuddy 框架 | 重建时间：2026-05-17 | 当前版本: v2.2.1

## 核心定位

**Super Mario** 是一个专注**加密货币量化分析**与**模拟合约交易**的智能 Agent，擅长：

- **多源信号引擎** ⭐⭐⭐⭐⭐ — 10币种/6源加权价格+合约+RSI+资金流+宏观+ETF+F&G
- **模拟合约交易** ⭐⭐⭐⭐⭐ — 限价单管理、TP/SL、动态杠杆、4h方向切换冷却
- **网格机器人** ⭐⭐⭐⭐ — 中性双向网格策略，7-14天周期，多维评分筛选
- **贝叶斯迭代学习** ⭐⭐⭐⭐ — 交易结果反哺因子权重优化，目标胜率>60%
- **宏观策略分析** ⭐⭐⭐⭐ — 7阶段市场判定（恐慌投降→狂热顶部）+ 策略建议

---

## 用户约束（必须遵守）

| 规则 | 说明 |
|------|------|
| **数据新鲜度** | >10分钟=⚠️, >30分钟=🚫, >60分钟=终止。禁止模拟数据 |
| **双输出格式** | 报告需同时输出 Markdown + HTML（深色主题，`#0f1117` 背景，`#f0a500` 金色强调） |
| **模拟交易** | 仅模拟持仓，不涉真实资金。所有信号为研究用途 |
| **禁止捏造** | 无法获取的内容必须明确标注"数据不可用" |
| **强制审计** | `audit_data_usage.py` 自动检查新鲜度/模拟数据/报告合规 |

---

## 目录结构

```
crypto_analyst/
├── README.md                    ← 本文件
├── VERSIONING.md                ← SemVer 版本管理规范
├── VERSION                      ← 当前版本号 (v2.2.1)
├── AGENTS.md                    ← Agent 工作流规范与自动化定义
├── requirements.txt             ← Python依赖
│
├── .workbuddy/
│   └── memory/                  ← WorkBuddy 记忆系统（自动化执行摘要）
│       └── 2026-05-27.md        ← 今日日志
│
├── crypto-signal/               ← 🚀 核心系统（加密货币信号+模拟合约交易）
│   ├── crypto_signal_v2.py      ← 信号引擎v2（10币种 + 9因子评分）
│   ├── paper_trader.py          ← 模拟交易器（init/update/cancel/optimize）
│   ├── grid_bot_sim.py          ← 网格合约机器人（中性双向 + 五维筛选）
│   ├── report_generator_v2.py   ← HTML报告生成器（深色主题）
│   ├── macro_strategy.py        ← 宏观策略分析（7阶段市场判定）
│   ├── bayesian_signal_generator.py ← 贝叶斯迭代学习引擎
│   ├── price_fetcher.py         ← 多源价格采集（Binance/CoinGecko/CMC等6源）
│   ├── audit_data_usage.py      ← 数据使用审计（新鲜度/模拟数据/报告合规）
│   ├── report_auditor.py        ← 价格数据一致性审核
│   ├── data_archiver.py         ← 历史数据归档
│   ├── scheduler.py             ← 定时任务调度器
│   ├── daemon.py                ← 守护进程
│   │
│   ├── adapters/                ← 适配器目录
│   │   ├── __init__.py
│   │   └── liquidator.py        ← 清算分析
│   │
│   ├── signal_v2_latest.json/.md ← 最新信号
│   ├── paper_positions.json     ← 模拟持仓
│   ├── paper_trade_history.json ← 交易历史
│   ├── grid_bot_state.json      ← 网格机器人状态
│   └── grid_bot_price_archive.json ← 价格归档（30天滚动）
│
├── scripts/                     ← 运维脚本
│   └── git_auto_sync.sh         ← 核心脚本变更自动 commit + tag + push
│
├── reports/                     ← 历史报告（.md + .html）
│   ├── crypto_signal_v2_*       ← 加密货币信号日报
│   ├── grid_bot_daily_*         ← 网格机器人日报
│   ├── grid_bot_weekly_*        ← 网格机器人周报
│   └── weekly_review_*.html     ← 信号机周度复盘
│
├── docs/                        ← 技术文档
│   └── data-usage-strict-spec.md ← 数据使用强制规范 v2.0
│
├── dalio_researcher/            ← 达利欧宏观日报系统（子项目，可独立运行）
│   ├── README.md
│   └── scripts/
│
├── email_bot/                   ← Gmail 邮件系统（已暂停）
│   └── email_manager.py
│
├── memory/                      ← 旧有记忆（已迁移至 .workbuddy/memory/）
│
├── venv/                        ← Python虚拟环境
├── rebuild_crypto_analyst.sh    ← 构建脚本
├── run_signal_daily.sh          ← 信号日报执行脚本
├── run_grid_bot_daily.sh        ← 网格日更执行脚本
├── run_grid_bot_weekly.sh       ← 网格周度执行脚本
├── run_weekly_review.sh         ← 信号机周度复盘脚本
└── dependency_graph_*.dot/.png  ← 依赖图（自动生成）
```

---

## 核心子系统详解

### 1️⃣ 信号引擎（`crypto_signal_v2.py`）

**覆盖币种**: BTC · ETH · BNB · SOL · DOGE · TAO · ZEC · CAKE · PAXG · EDEN（共10个）

**评分因子**（9因子动态权重）：
| 因子 | 权重 | 数据来源 |
|------|------|---------|
| 合约多空比 L/S | 15% | Binance 合约 |
| BTC ETF 流量 | 20% | 每日追踪 |
| ETH ETF 流量 | 15% | 每日追踪 |
| Taker 吃单比 BSR | 10% | Binance |
| 资金费率 | 10% | Binance 合约 |
| 恐惧贪婪指数 F&G | 15% | alternative.me |
| 宏观阶段 | 15% | macro_strategy.py 输出 |

**输出**:
- 方向：LONG / SHORT / NEUTRAL
- 入场区间（±0.5% ~ 1.5%）
- 动态杠杆（基于波动率×R/R×概率上限）
- TP1 / TP2 / SL
- R/R 比值 + 胜率估算
- RSI(14) + 4h买流占比

**增强机制**：
- **趋势过滤**: 24h跌幅>1.5% → LONG信号score×0.5
- **BB突破检测**: width_pct>15%且突破上轨 → 强制LONG
- **4h方向冷却**: 避免LONG/SHORT高频抖动

---

### 2️⃣ 模拟合约交易（`paper_trader.py`）

**核心功能**:
- `init` — 从信号生成限价单，录入模拟持仓
- `update` — 更新最新价、盈亏、检查TP/SL触发
- `cancel` — 取消未成交限价单
- `optimize` — 根据历史表现优化参数

**风控机制**:
- 回撤阈值：5% → 预警，10% → 暂停
- 单笔最大杠杆：10x
- 4小时方向切换冷却（防止抖动）

**贝叶斯学习集成**:
- 每笔交易完成后更新贝叶斯模型
- 动态调整信号门槛（胜率≥55%降门槛，<35%提门槛）
- `bayesian_learning_log.json` 记录学习轨迹

---

### 3️⃣ 网格机器人（`grid_bot_sim.py`）

**核心约束**:
- 本金：100 USDT（硬约束）
- 杠杆：5x
- 模式：中性（多空双向网格）
- 周期：7-14天

**五维筛选规则**:
| 维度 | 权重 | 满分标准 | 淘汰线 |
|------|------|---------|--------|
| 趋势方向 | 30% | 中性/震荡 | 强趋势 |
| 波动率(BB宽度) | 25% | 3-7% | <2%或>12% |
| 流动性(OI) | 20% | >$500M | <$50M |
| 资金费率 | 15% | 绝对值<0.01% | >0.05% |
| RSI中性度 | 10% | 40-60 | >80或<25 |

**安全启动评估**:
- 🟢 pos_pct 20-80% → 可启动
- 🟡 pos_pct 15-85% → 谨慎
- 🟠 pos_pct <15%或>85% → 不建议
- 🔴 pos_pct <5%或>95% → 禁止

---

### 4️⃣ 宏观策略分析（`macro_strategy.py`）

**四维度评分引擎**（各占25%）：
1. 情绪面 — F&G + 社交媒体情绪
2. 市场结构 — BTC Dominance + 市值结构
3. 机构资金 — ETF流量 + 稳定币供应
4. 宏观风险 — 利率 + 汇率 + 地缘

**七阶段市场判定**:
```
恐慌投降 → 恐惧筑底 → 底部积累 → 温和上涨 → 牛市活跃 → 狂热顶部 → 中性震荡
```

**策略建议**: 对应不同阶段的仓位配置（偏保守/平衡/偏激进）

---

## 报告规范

### 加密信号日报（每日 10:00 / 22:00 CST）

1. **宏观策略摘要** — 市场阶段 | F&G | BTC主导率 | 总市值 | 稳定币 | 策略建议
2. **实时价格表** — 价格 | 24h | 24h交易量（自动格式化亿/万单位）
3. **合约数据表** — 资金费率 | 多空比 | 多仓均价 | 空仓均价 | 买方流量 | 多头占比
4. **信号表（12列）** — 币种 | 当前价 | 方向 | 入场区间 | 入场距离% | 杠杆 | 止盈1 | 止盈2 | 止损 | R/R | RSI(14) | 胜率估算
5. **爆仓表（8列）** — 币种 | 当前价 | 多头10x/20x(距%) | 空头10x/20x(距%) | 爆仓概率 | 风险/插针
6. **宏观ETF表** — 24h/48h/72h 三列净流量
7. **技术指标** — RSI(14) + 4h买流占比

### 网格机器人周报（周二 10:15 CST）
1. 筛选币种 + 网格参数 + 五维评分
2. P&L 更新 + 配对统计
3. 风险预警

### 信号机周度复盘（周一 22:30 CST）
1. 7日KPI诊断（配对率、P&L、TP-SL触发率）
2. 贝叶斯模型更新
3. 算法改进方案

---

## 定时任务（WorkBuddy 自动化）

| 优先级 | 任务名 | 时间（CST） | 周期 | 说明 |
|--------|--------|-----------|------|------|
| P0 | 加密货币每日信号 | 10:00, 22:00 | 每日2次 | 采集价格+合约数据，生成信号+报告 |
| P0 | 网格机器人价格归档 | :10 每小时 | 每小时 | 归档实时价格（30天滚动） |
| P1 | 每日记忆维护 | 17:05 | 每日 | 归档报告、更新索引、知识沉淀 |
| P2 | 网格机器人每日更新+报告 | 12:00 | 每日 | 更新P&L+生成日报 |
| P2 | 信号机周度复盘优化 | 周一 22:30 | 每周 | 7日KPI诊断+贝叶斯更新+改进方案 |
| P2 | 网格机器人周度分析 | 周二 10:15 | 每周 | 筛选币种+网格参数+五维评分报告 |
| P2 | 网格机器人复盘+优化 | 周二 09:30 | 每周 | 7日KPI诊断+算法改进 |
| P3 | 会话索引+周总结 | 周日 02:00 | 每周 | 会话摘要索引更新 |
| P3 | Git自动同步兜底 | 10:15, 22:15 | 每日2次 | 检测核心脚本变更，自动commit+tag+push |
| - | 系统每周维护 | 周日 21:00 | 每周 | 存储检查、依赖更新、清理缓存 |

---

## 版本管理

采用 **SemVer 2.0** 规范，参见 [VERSIONING.md](VERSIONING.md)。

核心脚本变更通过 `scripts/git_auto_sync.sh` 自动触发：
- `git commit` + 语义化 tag → `git push origin main --tags`

GitHub 仓库：https://github.com/geniousfoxatcd/crypto_analyst

---

## 快速开始

### 生成加密货币信号日报
```bash
cd crypto-signal
python3 crypto_signal_v2.py
python3 paper_trader.py update
```

### 更新模拟持仓
```bash
cd crypto-signal
python3 paper_trader.py update
python3 paper_trader.py status
```

### 网格机器人操作
```bash
cd crypto-signal
python3 grid_bot_sim.py --cmd init   # 初始化网格
python3 grid_bot_sim.py --cmd update # 更新P&L
python3 grid_bot_sim.py --cmd status # 查看状态
python3 grid_bot_sim.py --cmd report # 生成报告
```

### 数据审计
```bash
cd crypto-signal
python3 audit_data_usage.py
```

### Git 同步（手动触发）
```bash
bash scripts/git_auto_sync.sh
```

### 重建环境
```bash
bash rebuild_crypto_analyst.sh
```

---

## 数据使用强制规范

参见 `docs/data-usage-strict-spec.md` v2.0：
- 价格必须有 `sources` 字段 + `is_valid=True`
- >10分钟=🟡警告, >30分钟=🔴拒绝, >60分钟=sys.exit终止
- 禁止模拟数据（非真实价格拒绝生成信号）
- 每份报告必须通过 `audit_data_usage.py` 合规检查

---

## 数据源优先级

| 级别 | 来源 |
|------|------|
| 🥇 一级 | Binance API（价格、合约、资金费率） |
| 🥈 二级 | CoinGecko / CoinMarketCap（市值、ETF流量） |
| 🥉 三级 | alternative.me（Fear & Greed Index） |

---

*Super Mario 🔍 · v2.2.1 · 2026-05-27 · 数据驱动 · 框架先行 · 知行合一*
