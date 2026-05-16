# Crypto Signal Report Skill

## 概述

加密货币每日信号报告生成 Skill。基于四维评分模型（趋势 + 资金流 + 衍生品 + 宏观），对 BTC/ETH/BNB/SOL/DOGE 进行评分，输出 Gmail-safe HTML 报告并推送至 WebChat。

**触发方式：** 用户说"生成加密货币报告"、"跑一下加密货币"、"crypto signal"时激活。

---

## 工作流程

### 第一步：数据采集（使用 MCP 工具）

使用 `batch_web_search` 搜索以下数据：

```
# BTC 价格
"Bitcoin price today site:coinmarketcap.com OR site:coingecko.com"
"BTC USDT price April 2026"

# ETH 价格
"Ethereum price today April 2026"

# 恐慌贪婪指数
"Fear and Greed Index crypto April 2026"

# BTC ETF 净流量
"BTC ETF net flow April 2026"

# 资金费率（Bybit/币安）
"Bitcoin funding rate April 2026 Bybit"

# 纳斯达克
"NASDAQ composite index April 2026"

# BTC 市占率
"BTC dominance percentage April 2026"
```

使用 `extract_content_from_websites` 深入获取：
- https://coinmarketcap.com/ （BTC 价格 + ETF 流量）
- https://coingecko.com/ （所有币种价格）
- https://alternative.me/fear-and-greed-index/ （恐慌贪婪指数）

### 第二步：数据整理

将采集到的数据整理成以下 JSON 结构：

```json
{
  "prices": {
    "BTC": 74978.00,
    "ETH": 2361.35,
    "BNB": 624.60,
    "SOL": 85.32,
    "DOGE": 0.096
  },
  "funding_rates": {
    "BTC": -0.0045,
    "ETH": 0.0067,
    "BNB": 0.0097,
    "SOL": 0.0100,
    "DOGE": 0.0100
  },
  "oi_data": {
    "BTC": {"current_oi": 15000000000, "oi_7d_pct": 3.2},
    "ETH": {"current_oi": 8000000000, "oi_7d_pct": 5.1},
    "BNB": {"current_oi": 500000000, "oi_7d_pct": 2.0},
    "SOL": {"current_oi": 2000000000, "oi_7d_pct": 8.5},
    "DOGE": {"current_oi": 800000000, "oi_7d_pct": 1.2}
  },
  "etf_flow_m": -77.8,
  "fear_greed": {"score": 23, "label": "Extreme Fear"},
  "btc_dominance": 57.3,
  "nasdaq": {"last": 24016, "ema20": 22548},
  "coin_closes": {
    "BTC": [74000, 74200, ...],
    "ETH": [2300, 2320, ...],
    ...
  }
}
```

注：`coin_closes` 为最近 90 日收盘价列表（用于 EMA 计算），可通过搜索历史价格或从 CoinGecko 获取。

### 第三步：生成报告

调用 `build_report.py` 生成 HTML：

```bash
python3 /workspace/crypto-signal/build_report.py '{"prices":{"BTC":74978,...},...}' /workspace/crypto-signal/report_latest.html
```

### 第四步：部署 + 推送

1. `deploy(dist_dir="/workspace/crypto-signal")` 部署 HTML 目录
2. 获取 `website_url`
3. `message action=send channel=webchat` 发送报告摘要

---

## 报告格式规范

**标题：** 📊 加密货币每日信号报告 | YYYY-MM-DD

**推送内容：**
```
🪙 BTC现货价：$XX,XXX（周环比XX%）
📊 ETH现货价：$X,XXX（周环比XX%）
🔔 恐慌贪婪：XX（XX）

📈 评分速览：
  BTC：XX分 | ETH：XX分 | BNB：XX分 | SOL：XX分 | DOGE：XX分

📎 HTML完整报告：[website_url]
📁 本地存档：/workspace/crypto-signal/report_latest.html
```

---

## 四维评分模型（满分100）

| 维度 | 指标 | 满分 |
|------|------|------|
| 趋势 | 价格>EMA20(+15)，EMA20>EMA50金叉(+10) | 25 |
| 资金流 | BTC ETF净流入越大越高 | 25 |
| 衍生品 | 资金费率越负+OI 7d变化<5%越高 | 25 |
| 宏观 | 纳指>EMA20则满分 | 25 |

**信号阈值：** 75+ = BUY | 50-74 = HOLD | <50 = SELL

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `crypto_signal.py` | 参考实现（urllib版，需在有网络环境运行） |
| `build_report.py` | HTML报告构建器（接收JSON数据，输出HTML） |
| `report_latest.html` | 最新报告（由Cron任务生成） |
| `report_output.json` | Cron调度文件 |

---

## 定时任务

**Cron Job：** 每天 09:00 UTC 自动执行
**Job ID：** crypto-signal-daily
**执行内容：** 触发 isolated agent session，执行完整数据采集→生成→部署→推送流程
