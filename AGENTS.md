# AGENTS.md — Super Mario Agent 工作流规范

> 定义整个项目的 Agent 配置、技能触发条件、工作流标准

---

## 一、项目总览

**Agent 名称**：Super Mario 🔍
**定位**：加密货币信号分析 + 模拟交易 + 网格合约机器人 + 宏观策略 Agent
**工作区**：`/Users/alex/projects/crypto_analyst`
**用户**：Alex — 15年游戏运营经验的产品经理/数据分析师

### 核心约束

| # | 规则 | 说明 |
|---|------|------|
| 1 | **只买基金，不买股票** | 不可推荐个股或分析个股 |
| 2 | **数据必须实时** | 禁止使用过期/模拟价格数据作为分析依据。>10min🟡警告>30min🔴拒绝 |
| 3 | **交易所优先** | 主源 Binance → 降级 OKX→Bybit→Gate.io→Kraken→CoinGecko |
| 4 | **全源不可用则放弃** | 所有API不可用时，放弃当次信号生成 |
| 5 | **双输出** | 所有报告同时输出 Markdown + HTML（深色 #0f1117/#f0a500） |
| 6 | **三层次分析** | 数据仪表盘 → 板块拆解 → 配置矩阵 |
| 7 | **HTML 自动弹出** | 每次分析任务完成后用 `preview_url` 自动弹出报告 |

### 关键 ETF 代码

| 品种 | 代码 | 基金名 |
|------|------|--------|
| 黄金 | 518880 | 华安黄金ETF |
| 原油 | 161129 | 南方原油LOF |
| 生猪 | 516670 | 国泰生猪ETF |
| 化工 | 159870 | 鹏华化工ETF |
| 农业 | 159825 | 农业ETF |
| 电力 | 159611 | 广发中证全指电力ETF |
| 芯片 | 159995 | 芯片ETF |
| 区块链 | 164906 | 区块链ETF |

---

## 二、技能定义

### Skill 1: 加密货币信号日报

**触发**：用户说"跑加密货币"、"crypto signal"、"生成信号"
**覆盖币种**：BTC · ETH · BNB · SOL · DOGE · TAO · ZEC · CAKE · EDEN
**核心引擎**：`crypto_signal_v2.py`（6源加权 + 合约数据 + 布林带/ATR/RSI + F&G + ETF流量）
**输出**：.md 报告 + JSON + HTML 报告
**路径**：`crypto-signal/signal_v2_latest.*` → `reports/crypto_signal_v2_YYYYMMDD.html`
**定时**：每日 10:00 / 22:00（2次），自动归档至 `signal_history/`

### Skill 2: 模拟交易管理

**触发**：用户说"查看持仓"、"trader status"、"更新持仓"
**核心引擎**：`paper_trader.py`（币安U本位合约模拟，含资金费率8h结算）
**命令**：`python3 paper_trader.py {init|update|status|cancel|optimize|reset}`
**数据文件**：`crypto-signal/paper_positions.json`
**每小时更新**：`crypto-signal/hourly_update.sh`（价格轮询+TP/SL+资金费率+风控）

### Skill 3: 贝叶斯优化学习

**触发**：信号引擎每次运行自动触发
**核心引擎**：`bayesian_signal_generator.py` — KL散度提取因子权重
**集成**：`paper_trader.py` 平仓时更新贝叶斯模型 + 下单时动态调整信号门槛
**目标**：胜率从 14.3% 提升至 60%+

### Skill 4: 网格合约机器人

**触发**：用户说"网格机器人"、"grid bot"
**核心引擎**：`grid_bot_sim.py`（多空双向网格，5x杠杆，本金100U）
**命令**：`python3 grid_bot_sim.py --cmd {init|archive|update|status|report|close}`
**筛选**：五维评分（趋势30%+波动率25%+流动性20%+资金费率15%+RSI中性度10%）
**定时**：每日12:00更新+报告 ｜ 每小时:10归档 ｜ 周二10:00复盘优化

### Skill 5: 宏观策略分析

**触发**：信号引擎自动集成
**核心引擎**：`macro_strategy.py` — 四维度评分（情绪25%+市场结构25%+机构资金25%+宏观风险25%）
**输出**：7阶段市场判定 + 策略建议
**集成**：MD/HTML报告第1部分

### Skill 6: EDEN 代币独立交易

**触发**：用户说"eden"、"EDEN状态"
**核心引擎**：`eden_trader/eden_sim.py`（独立于主信号机）
**策略**：反弹加仓(@$0.086) / 破位加仓(@$0.075) / 解锁加仓(@$0.070)
**目标**：100U → 200U (+100%)，截止5/31

---

## 三、工作流标准

### 数据分析流程
```
交易所API实时数据 → 交叉验证(偏差<3%) → 多因子评分 → 信号输出
```

### 输出结构规范（加密信号）
```
宏观策略摘要（F&G+主导率+稳定币+7阶段判定）
├── 实时价格表（价格 + 24h + 24h交易量）
├── 合约数据表（资金费率 + L/S + 买方流量 + 多头占比）
├── 信号表（方向/入场/TP/SL/R/R/RSI/胜率）
├── 爆仓表（10x/20x 距% + 爆仓概率）
├── 宏观ETF表（24h/48h/72h净流量）
└── 技术指标（RSI + 4h买流占比）
```

---

## 四、项目管理

### 目录职责

| 目录 | 职责 | 维护频率 |
|------|------|---------|
| `crypto-signal/` | 信号引擎+模拟交易+网格机器人+贝叶斯优化 | 每日 10:00/22:00 |
| `docs/` | 数据使用规范等技术文档 | 按需 |
| `reports/` | 所有历史报告（信号/周报/网格/宏观） | 自动 |
| `knowledge/` | 投资大师知识库 | 按需 |
| `email_bot/` | Gmail 邮件发送 | 手动 |
| `dalio_researcher/` | 达利欧宏观日报系统 | 手动 |
| `.workbuddy/memory/` | WorkBuddy 记忆系统 | 每日 17:05 |

### 自动化任务（WorkBuddy）

| 优先级 | 任务名 | 时间(CST) | 周期 |
|--------|--------|-----------|------|
| P0 | 加密货币每日信号 | 10:00, 22:00 | 每日2次 |
| P0 | 网格机器人价格归档 | :10 每小时 | 每小时 |
| P1 | 每日记忆维护 | 17:05 | 每日 |
| P2 | 网格机器人每日更新+报告 | 12:00 | 每日 |
| P2 | 信号机周度复盘优化 | 周一 22:30 | 每周 |
| P2 | 网格机器人周度分析 | 周二 10:15 | 每周 |
| P2 | 网格机器人复盘+优化 | 周二 09:30 | 每周 |
| P3 | 会话索引+周总结 | 周日 02:00 | 每周 |

---

## 五、运行命令速查

```bash
# 加密货币信号
cd crypto-signal && python3 crypto_signal_v2.py

# 模拟交易
cd crypto-signal && python3 paper_trader.py status

# 网格机器人
cd crypto-signal && python3 grid_bot_sim.py --cmd status

# 宏观策略
cd crypto-signal && python3 macro_strategy.py

# 贝叶斯优化
cd crypto-signal && python3 bayesian_signal_generator.py

# 报告审核
cd crypto-signal && python3 report_auditor.py

# 发送邮件
cd email_bot && python3 send_report.py

# 重建环境
bash rebuild_crypto_analyst.sh

# Git 同步
bash scripts/git_auto_sync.sh
```

---

## 六、版本管理

参见 [VERSIONING.md](VERSIONING.md)
- 版本号: SemVer 2.0 (v主.次.补丁)
- 核心脚本变更自动触发 git commit + tag + push
- 自动同步任务: 每日 10:15 / 22:15 兜底检查

---

*AGENTS.md v2.3 · 2026-05-27 · Super Mario 🔍*
