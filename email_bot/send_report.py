#!/usr/bin/env python3
"""
每日报告自动发送集成
由定时任务或 crypto_signal_v2.py 调用
"""

import sys
from pathlib import Path
from datetime import datetime

BASE_DIR   = Path("/Users/alex/hermes_claud/super_mario")
EMAIL_BOT  = BASE_DIR / "email_bot"
REPORTS    = BASE_DIR / "reports"

sys.path.insert(0, str(EMAIL_BOT))


def send_daily_report():
    try:
        from email_manager import send_crypto_report
    except ImportError as e:
        print(f"[email] 跳过邮件发送: {e}")
        return False

    # 找最新报告
    reports = sorted(REPORTS.glob("crypto_signal_v2_*.html"))
    if not reports:
        print("[email] 未找到报告文件，跳过")
        return False

    latest = reports[-1]
    print(f"[email] 发送报告: {latest.name}")
    return send_crypto_report(latest, to="alexhoo2025@gmail.com")


if __name__ == "__main__":
    send_daily_report()
