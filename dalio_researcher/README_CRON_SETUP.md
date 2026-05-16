# ============================================
# Aliya 每日财经简报 - 定时任务配置
# ============================================

## 更新说明 (2026-04-14)
- 新增数据来源：华尔街日报、彭博社、深潮
- 数据来源整合：汇通网、新浪财经、东方财富、第一财经、证券时报
- 推送时间调整为每日19:00
- 新增邮件推送至 Alex_ylsd@icloud.com

## 数据来源配置

### 权威财经媒体列表

| 类别 | 媒体名称 | 语言 | 说明 |
|------|---------|------|------|
| **国际权威** | 华尔街日报 (WSJ) | 英文 | 全球最具影响力财经媒体 |
| **国际权威** | 彭博社 (Bloomberg) | 英文 | 全球最大财经资讯公司 |
| **中文深度** | 深潮 (TechFlow) | 中文 | 新锐财经深度媒体 |
| **国内主流** | 汇通网 | 中文 | 专业外汇黄金资讯 |
| **国内主流** | 新浪财经 | 中文 | 中国最大财经门户 |
| **国内主流** | 东方财富 | 中文 | A股权威资讯平台 |
| **国内主流** | 第一财经 | 中文 | 权威财经媒体 |
| **国内主流** | 证券时报 | 中文 | 证监会指定信息披露媒体 |

### 搜索关键词配置

```python
SEARCH_QUERIES = {
    "us_market": [
        "道琼斯 纳斯达克 指数 今日收盘",
        "Dow Jones Nasdaq index today",
        "美股行情 2026"
    ],
    "asia_market": [
        "日经225指数 恒生指数 今日收盘",
        "上证指数 行情",
        "Nikkei225 Hong Kong stock"
    ],
    "power_etf": [
        "电力ETF 新能源ETF 2026 行情",
        "绿色电力ETF 储能ETF 走势",
        "创业板新能源ETF 国泰 净值"
    ],
    "fed_policy": [
        "美联储 货币政策 最新",
        "Federal Reserve interest rate 2026",
        "IMF 美联储 降息 预测"
    ],
    "china_policy": [
        "中国人民银行 财政部 政策 2026",
        "中国财政政策 最新动态"
    ],
    "gold_crypto": [
        "黄金价格 走势 分析",
        "比特币 以太坊 加密货币",
        "gold price bitcoin ethereum 2026"
    ],
    "oil": [
        "原油价格 大宗商品 走势",
        "oil price WTI Brent 2026",
        "美伊停火 原油"
    ],
    "ai_chip": [
        "英伟达 AI芯片 动态",
        "NVIDIA semiconductor news 2026",
        "AI芯片 半导体 行情"
    ]
}
```

### HTML页面数据来源展示

```html
<div class="email-notice">
    <strong>数据来源：</strong>华尔街日报、彭博社、深潮、汇通网、新浪财经、东方财富、第一财经、证券时报
</div>
```

---

## Linux/Mac 设置方法

### 使用 crontab

```bash
# 编辑定时任务
crontab -e

# 添加以下行（每天晚上19:00执行）
0 19 * * 1-5 /usr/bin/python3 /workspace/docs/daily_briefing_generator.py >> /workspace/docs/cron.log 2>&1
```

### 使用 systemd (Linux)

创建服务文件 /etc/systemd/system/aliya-briefing.service:
```ini
[Unit]
Description=Aliya Daily Finance Briefing
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /workspace/docs/daily_briefing_generator.py
User=www-data
WorkingDirectory=/workspace/docs

[Install]
WantedBy=multi-user.target
```

创建定时器 /etc/systemd/system/aliya-briefing.timer:
```ini
[Timer]
OnCalendar=Mon-Fri 19:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

启用定时任务：
```bash
sudo systemctl daemon-reload
sudo systemctl enable aliya-briefing.timer
sudo systemctl start aliya-briefing.timer
```

查看状态：
```bash
systemctl list-timers aliya-briefing.timer
```

---

## Windows 设置方法

使用任务计划程序或命令行：
```batch
schtasks /create /tn "Aliya Daily Briefing" /tr "python C:\workspace\docs\daily_briefing_generator.py" /sc daily /st 19:00
```

---

## 邮件推送配置

### SMTP邮件发送设置

编辑生成器脚本中的邮件配置，或创建配置文件 `email_config.py`：

```python
# email_config.py
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "your_email@gmail.com"
SENDER_PASSWORD = "your_app_password"
RECIPIENT_EMAIL = "Alex_ylsd@icloud.com"
```

### 自动发送脚本

创建邮件发送脚本 `send_briefing_email.py`：

```python
#!/usr/bin/env python3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

def send_briefing_email():
    # 邮件配置
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    sender_email = "your_email@gmail.com"
    sender_password = "your_app_password"
    recipient_email = "Alex_ylsd@icloud.com"

    # 生成日期
    date_str = datetime.now().strftime("%Y%m%d")

    # 读取HTML文件
    html_file = f"/workspace/docs/daily_finance_briefing_{date_str}.html"
    with open(html_file, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # 创建邮件
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"每日财经简报 {datetime.now().strftime('%Y年%m月%d日')}"
    msg['From'] = sender_email
    msg['To'] = recipient_email

    # 添加HTML内容
    html_part = MIMEText(html_content, 'html', 'utf-8')
    msg.attach(html_part)

    # 发送邮件
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        print(f"✅ 邮件已发送至 {recipient_email}")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

if __name__ == "__main__":
    send_briefing_email()
```

### Gmail SMTP使用应用密码

1. 访问 https://myaccount.google.com/security
2. 启用两步验证
3. 生成应用密码：安全 → 应用密码 → 生成新应用密码
4. 将生成的密码填入 `sender_password`

---

## 手动运行

```bash
# 运行简报生成器
python3 /workspace/docs/daily_briefing_generator.py

# 发送邮件（如需单独发送）
python3 /workspace/docs/send_briefing_email.py
```

---

## 查看生成的文件

```bash
# 查看简报文件
ls -la /workspace/docs/daily_finance_briefing_*.html

# 查看日志
cat /workspace/docs/cron.log
```

---

## 文件结构

```
/workspace/docs/
├── daily_briefing_generator.py      # 简报生成器
├── send_briefing_email.py            # 邮件发送脚本
├── email_config.py                   # 邮件配置
├── daily_finance_briefing_*.html     # 生成的HTML简报
├── daily_finance_briefing_*.md       # Markdown摘要
└── cron.log                          # 运行日志
```

---

## 故障排除

### Cron任务不执行
```bash
# 检查cron状态
service cron status

# 查看cron日志
grep CRON /var/log/syslog
```

### 邮件发送失败
```bash
# 测试SMTP连接
telnet smtp.gmail.com 587

# 检查Python smtplib
python3 -c "import smtplib; print('smtplib OK')"
```

### 权限问题
```bash
# 确保文件可执行
chmod +x /workspace/docs/daily_briefing_generator.py

# 确保目录可写
chmod 755 /workspace/docs/
```
