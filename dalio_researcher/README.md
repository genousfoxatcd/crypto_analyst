# Dalio Researcher
## 达利欧研究追踪系统 - 全球宏观财经智能Agent

---

<p align="center">
  <img src="https://img.shields.io/badge/Version-1.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/Language-Python-orange" alt="Language">
  <img src="https://img.shields.io/badge/Topic-Macro%20Research-green" alt="Topic">
  <img src="https://img.shields.io/badge/Platform-MiniMax%20Agent-purple" alt="Platform">
</p>

---

## 一、项目简介

**Dalio Researcher** 是一个专注于全球宏观研究、投资分析和财经简报生成的智能Agent系统。

### 核心能力

| 能力 | 描述 |
|------|------|
| 桥水研究追踪 | 监控Bridgewater China官网，追踪达利欧最新观点 |
| 全球财经资讯整合 | 从WSJ、Bloomberg、BBC、日经中文网、TechFlow等权威媒体收集信息 |
| 多资产分析 | 覆盖美股、A股、黄金、大宗商品、加密货币、AI/芯片 |
| 每日简报生成 | 生成专业3000字左右的财经简报 |
| 关联分析与预测 | 跨资产联动分析，提供投资建议 |
| 邮件推送服务 | 每日19:00自动推送至用户邮箱 |

---

## 二、快速开始

### 2.1 生成今日简报

```bash
cd /workspace/docs/dalio_researcher
python3 scripts/dalio_briefing_generator.py
```

### 2.2 发送邮件推送

```bash
cd /workspace/docs/dalio_researcher
python3 scripts/send_briefing_email.py
```

### 2.3 完整流程

```bash
cd /workspace/docs/dalio_researcher
bash scripts/run_full_pipeline.sh
```

---

## 三、项目结构

```
dalio_researcher/
├── README.md                    # 本文件
├── agent_profile.md              # Agent配置与身份定义
├── knowledge_base.md             # 知识库（全球宏观数据）
├── skills.md                    # 技能与能力定义
├── automation.md                 # 自动化配置与脚本
├── session_log.md               # 运行记录
├── scripts/                     # 自动化脚本
│   ├── dalio_briefing_generator.py    # 主生成器
│   ├── send_briefing_email.py         # 邮件发送
│   ├── deploy_briefing.sh             # 部署脚本
│   ├── check_health.sh                # 健康检查
│   └── run_full_pipeline.sh           # 完整流程
├── logs/                        # 日志目录
├── templates/                   # 报告模板
├── data/                        # 数据缓存
└── daily_finance_briefing_*.html # 生成的简报
```

---

## 四、配置说明

### 4.1 邮件配置

编辑 `scripts/send_briefing_email.py` 中的配置：

```python
class EmailConfig:
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    SENDER_EMAIL = "your_email@gmail.com"      # 修改为您的邮箱
    SENDER_PASSWORD = "your_app_password"       # Gmail应用密码
    RECIPIENT_EMAIL = "Alex_ylsd@icloud.com"  # 接收邮箱
```

### 4.2 Gmail应用密码

1. 访问 https://myaccount.google.com/security
2. 启用两步验证
3. 生成应用密码：安全 → 应用密码 → 生成新应用密码

---

## 五、定时任务设置

### 5.1 Linux Crontab

```bash
# 编辑定时任务
crontab -e

# 添加以下行（每天晚上19:00执行）
0 19 * * 1-5 /usr/bin/python3 /workspace/docs/dalio_researcher/scripts/dalio_briefing_generator.py >> /workspace/docs/dalio_researcher/logs/cron.log 2>&1

# 同时发送邮件
0 19 * * 1-5 /usr/bin/python3 /workspace/docs/dalio_researcher/scripts/send_briefing_email.py >> /workspace/docs/dalio_researcher/logs/email.log 2>&1
```

### 5.2 systemd定时器

```bash
# 启用定时器
sudo systemctl daemon-reload
sudo systemctl enable dalio-researcher.timer
sudo systemctl start dalio-researcher.timer

# 查看状态
systemctl list-timers dalio-researcher.timer
```

---

## 六、输出预览

### 简报主题：墨黑色 (Ink Black)

```css
body {
    background: linear-gradient(180deg, #0a0a0a 0%, #0d0d0d 50%, #050505 100%);
    color: #e8e8e8;
}
```

### 报告结构

```
每日财经简报 (2026年4月9日)
├── 重大事件速递
│   └── 美伊停火协议达成
├── 美股市场动态
│   ├── 道琼斯 +2.85%
│   ├── 标普500 +2.51%
│   └── 纳斯达克 +2.80%
├── A股与亚太市场
│   └── 上证指数 -0.72%
├── A股电力与新能源ETF ⭐
│   ├── 绿色电力ETF嘉实(159625)
│   ├── 储能电池ETF易方达(159566)
│   └── 创业板新能源ETF国泰(159387)
├── 宏观经济与政策
│   └── IMF预测：美联储2026年仅降息1次
├── 黄金与加密货币
│   ├── 黄金 $4,742-4,749
│   └── 比特币 $69,904-71,024
├── 原油市场
│   └── 美伊停火，油价预期回落
├── AI与半导体动态
│   └── 英伟达营收681亿美元(+73%)
├── 跨资产关联分析
│   └── 地缘风险缓释 → 风险资产反弹
├── 投资建议
│   ├── 超配：美股、加密货币、科技股
│   └── 标配：黄金、原油、A股电力
└── 风险提示
    └── 停火协议执行不确定性
```

---

## 七、数据源

### 权威财经媒体

| 来源 | 语言 | 重点 |
|------|------|------|
| 华尔街日报 (WSJ) | EN | 全球金融市场 |
| 彭博社 | EN | 宏观经济、政策 |
| BBC | EN | 国际重大事件 |
| 日经中文网 | CN | 亚太市场 |
| TechFlow | CN | 科技/加密货币 |
| 东方财富 | CN | A股数据 |
| 新浪财经 | CN | 中国宏观 |

### 桥水研究追踪

- **官网**: bridgewater.com/china
- **关注领域**: 中国宏观政策、市场走势、人民币国际化

---

## 八、知识库

### 核心数据 (2026-04-09)

| 指标 | 数值 | 状态 |
|------|------|------|
| 道琼斯 | 47,909.92 | +2.85% |
| 标普500 | 6,782.81 | +2.51% |
| 纳斯达克 | 22,635.00 | +2.80% |
| 上证指数 | 3,966.17 | -0.72% |
| 黄金 | $4,742-4,749 | -0.73% |
| 比特币 | $69,904-71,024 | +4.42% |
| WTI原油 | $91-98 | 高位震荡 |

### IMF预测 (2026年)

| 指标 | 预测 |
|------|------|
| 美国GDP增速 | 2.4% |
| 美联储降息次数 | 仅1次 |
| 年末利率 | 3.25%-3.5% |
| 美国债务/GDP | 123.9% |

---

## 九、投资建议

### 配置建议

| 资产 | 建议 | 理由 |
|------|------|------|
| 美股 | 超配 | 短期反弹动能强劲 |
| 加密货币 | 超配 | 风险偏好回升 |
| 科技股 | 超配 | AI叙事持续 |
| 黄金 | 标配 | 高位震荡，长期逻辑不变 |
| 原油 | 标配 | 等待回调后介入 |
| A股电力新能源 | 标配 | 政策+业绩双轮驱动 |

### 风险提示

1. **停火协议变数** - 协议执行仍有不确定性
2. **通胀反复风险** - 能源价格回落需要时间
3. **美联储立场** - 4月维持利率不变概率98.4%

---

## 十、故障排除

### 常见问题

| 问题 | 解决方案 |
|------|---------|
| 邮件发送失败 | 检查SMTP配置和应用密码 |
| 简报生成失败 | 检查Python依赖和网络连接 |
| Cron不执行 | 检查文件权限和路径 |
| 部署失败 | 检查HTML语法 |

### 健康检查

```bash
cd /workspace/docs/dalio_researcher
bash scripts/check_health.sh
```

### 查看日志

```bash
# Cron日志
tail -f logs/cron.log

# 邮件日志
tail -f logs/email.log

# 错误日志
tail -f logs/error.log
```

---

## 十一、版本历史

| 版本 | 日期 | 更新内容 |
|------|------|---------|
| 1.0 | 2026-04-09 | 初始化Dalio Researcher Agent |
| - | - | 从Aliya系统转换 |
| - | - | 新增墨黑色主题 |
| - | - | 新增电力ETF模块 |
| - | - | 建立完整知识库 |

---

## 十二、联系与支持

- **开发者**: MiniMax Agent
- **用户**: Alex_ylsd@icloud.com
- **文档**: /memories/dalio_researcher/

---

<p align="center">
  <strong>Dalio Researcher</strong> - 追踪全球宏观脉搏，洞悉市场趋势
</p>

