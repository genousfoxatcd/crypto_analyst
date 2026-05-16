#!/usr/bin/env python3
"""
Gmail 邮件管理系统
① SMTP 发送 HTML 报告邮件
② Gmail API OAuth2 读取收件箱、获取验证码、管理邮件

凭据配置（任选其一）：
  环境变量:  GMAIL_APP_PASSWORD / GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN
  Keychain: python3 email_manager.py set-password
"""

import os
import re
import time
import json
import smtplib
import ssl
import subprocess
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────────
GMAIL_ADDRESS    = "alexhoo2025@gmail.com"
SMTP_HOST        = "smtp.gmail.com"
SMTP_PORT        = 587
TOKEN_FILE       = Path.home() / ".config" / "super_mario" / "gmail_token.json"
CREDENTIALS_FILE = Path.home() / ".config" / "super_mario" / "gmail_credentials.json"


def _get_app_password() -> str:
    """从环境变量或 macOS Keychain 读取 App Password"""
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    if pw:
        return pw
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", GMAIL_ADDRESS,
             "-s", "gmail_app_password", "-w"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _save_app_password(password: str):
    """将 App Password 保存到 macOS Keychain"""
    subprocess.run([
        "security", "add-generic-password",
        "-a", GMAIL_ADDRESS,
        "-s", "gmail_app_password",
        "-w", password,
        "-U"  # update if exists
    ], check=True)
    print(f"✅ App Password 已保存到 Keychain（账户: {GMAIL_ADDRESS}）")


# ══════════════════════════════════════════════════════════
# ① SMTP 发送邮件
# ══════════════════════════════════════════════════════════

def send_email(
    to: str | list[str],
    subject: str,
    html_body: str,
    text_body: str = "",
    attachments: list[tuple[str, bytes]] | None = None,
    app_password: str = "",
) -> bool:
    """
    发送 HTML 邮件

    Args:
        to: 收件人地址（字符串或列表）
        subject: 邮件主题
        html_body: HTML 正文
        text_body: 纯文本正文（可选）
        attachments: [(filename, bytes), ...] 附件列表
        app_password: Gmail App Password（留空则从环境变量/Keychain读取）

    Returns:
        True 表示发送成功
    """
    pw = app_password or _get_app_password()
    if not pw:
        print("❌ 未配置 Gmail App Password")
        print("   请运行: python3 email_manager.py set-password")
        print("   或设置环境变量: export GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx")
        return False

    if isinstance(to, str):
        to = [to]

    msg = MIMEMultipart("alternative")
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"]    = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if attachments:
        outer = MIMEMultipart("mixed")
        outer.attach(msg)
        for fname, data in attachments:
            part = MIMEApplication(data, Name=fname)
            part["Content-Disposition"] = f'attachment; filename="{fname}"'
            outer.attach(part)
        msg = outer

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(GMAIL_ADDRESS, pw)
            server.sendmail(GMAIL_ADDRESS, to, msg.as_string())
        print(f"✅ 邮件已发送 → {', '.join(to)}")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        return False


def send_crypto_report(
    report_html_path: Path | str,
    to: str | list[str] | None = None,
    app_password: str = "",
) -> bool:
    """
    发送加密货币信号报告邮件（读取HTML文件）
    """
    report_path = Path(report_html_path)
    if not report_path.exists():
        print(f"❌ 报告文件不存在: {report_path}")
        return False

    html_body   = report_path.read_text(encoding="utf-8")
    report_date = datetime.now().strftime("%Y-%m-%d")
    subject     = f"🔍 Super Mario · 加密货币信号报告 · {report_date}"

    if to is None:
        to = GMAIL_ADDRESS  # 默认发给自己

    return send_email(
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=f"Super Mario 加密货币信号报告 {report_date}，请查看 HTML 版本",
        app_password=app_password,
    )


# ══════════════════════════════════════════════════════════
# ② Gmail API OAuth2 读取邮件
# ══════════════════════════════════════════════════════════

def _get_gmail_service():
    """
    获取 Gmail API 服务对象（需要 oauth 凭据）
    首次运行会打开浏览器授权
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("❌ 缺少 Gmail API 依赖，请运行:")
        print("   pip install google-auth-oauthlib google-api-python-client")
        return None

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    creds = None
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 检查现有 token
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    # 刷新或重新授权
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"❌ 未找到 OAuth 凭据文件: {CREDENTIALS_FILE}")
                print("   请先运行: python3 gmail_oauth_setup.py")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def read_inbox(query: str = "", limit: int = 10) -> list[dict]:
    """
    读取收件箱邮件

    Args:
        query: Gmail 搜索语法（如 "from:noreply@binance.com" 或 "subject:验证码"）
        limit: 最大返回数量

    Returns:
        [{"id": ..., "from": ..., "subject": ..., "date": ..., "body": ...}, ...]
    """
    service = _get_gmail_service()
    if not service:
        return []

    try:
        results = service.users().messages().list(
            userId="me", q=query, maxResults=limit
        ).execute()

        messages = results.get("messages", [])
        parsed   = []
        for msg_ref in messages:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()

            headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            body    = _extract_body(msg["payload"])

            parsed.append({
                "id":      msg_ref["id"],
                "from":    headers.get("From", ""),
                "to":      headers.get("To", ""),
                "subject": headers.get("Subject", ""),
                "date":    headers.get("Date", ""),
                "body":    body,
                "snippet": msg.get("snippet", ""),
            })
        return parsed
    except Exception as e:
        print(f"❌ 读取邮件失败: {e}")
        return []


def _extract_body(payload: dict) -> str:
    """从 Gmail API payload 提取文本内容"""
    import base64

    if "body" in payload and payload["body"].get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
            if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
            if "parts" in part:
                result = _extract_body(part)
                if result:
                    return result
    return ""


def get_verification_code(
    sender_domain: str = "",
    subject_keyword: str = "验证|verify|code|码",
    timeout_seconds: int = 120,
    code_pattern: str = r"\b(\d{4,8})\b",
) -> str | None:
    """
    等待并提取验证码邮件中的数字验证码

    Args:
        sender_domain: 发件人域名过滤（如 "binance.com"）
        subject_keyword: 主题关键词
        timeout_seconds: 等待超时（秒）
        code_pattern: 验证码正则

    Returns:
        验证码字符串，超时返回 None
    """
    service = _get_gmail_service()
    if not service:
        return None

    query = "is:unread"
    if sender_domain:
        query += f" from:{sender_domain}"

    print(f"⏳ 等待验证码邮件（最长 {timeout_seconds}s）...")
    deadline = time.time() + timeout_seconds
    seen_ids: set = set()

    while time.time() < deadline:
        try:
            results = service.users().messages().list(
                userId="me", q=query, maxResults=5
            ).execute()
            messages = results.get("messages", [])

            for msg_ref in messages:
                if msg_ref["id"] in seen_ids:
                    continue
                seen_ids.add(msg_ref["id"])

                msg = service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="full"
                ).execute()
                headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
                subject = headers.get("Subject", "")

                if not re.search(subject_keyword, subject, re.IGNORECASE):
                    continue

                body = _extract_body(msg["payload"])
                snippet = msg.get("snippet", "")
                text = body or snippet

                codes = re.findall(code_pattern, text)
                if codes:
                    code = codes[0]
                    # 标记为已读
                    service.users().messages().modify(
                        userId="me",
                        id=msg_ref["id"],
                        body={"removeLabelIds": ["UNREAD"]}
                    ).execute()
                    print(f"✅ 验证码: {code}  (来自: {headers.get('From', '')})")
                    return code
        except Exception as e:
            print(f"[警告] 邮件查询异常: {e}")

        time.sleep(5)

    print("❌ 等待验证码超时")
    return None


def mark_as_read(message_id: str) -> bool:
    """标记邮件为已读"""
    service = _get_gmail_service()
    if not service:
        return False
    try:
        service.users().messages().modify(
            userId="me", id=message_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        return True
    except Exception:
        return False


def delete_email(message_id: str) -> bool:
    """移到垃圾箱"""
    service = _get_gmail_service()
    if not service:
        return False
    try:
        service.users().messages().trash(userId="me", id=message_id).execute()
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "set-password":
        pw = input("请输入 Gmail App Password（格式: xxxx-xxxx-xxxx-xxxx）: ").strip()
        _save_app_password(pw)

    elif cmd == "test-send":
        to = sys.argv[2] if len(sys.argv) > 2 else GMAIL_ADDRESS
        send_email(
            to=to,
            subject=f"🔍 Super Mario 邮件测试 {datetime.now().strftime('%H:%M')}",
            html_body="<h2>✅ Gmail SMTP 发送测试成功！</h2><p>Super Mario 邮件系统已就绪。</p>",
        )

    elif cmd == "send-report":
        reports_dir = Path("/Users/alex/hermes_claud/super_mario/reports")
        reports     = sorted(reports_dir.glob("crypto_signal_v2_*.html"))
        if not reports:
            print("❌ 未找到报告文件")
            sys.exit(1)
        latest = reports[-1]
        print(f"发送报告: {latest}")
        send_crypto_report(latest, to=GMAIL_ADDRESS)

    elif cmd == "read-inbox":
        query = sys.argv[2] if len(sys.argv) > 2 else "is:unread"
        emails = read_inbox(query=query, limit=5)
        for e in emails:
            print(f"\n[{e['date'][:20]}] From: {e['from']}")
            print(f"  Subject: {e['subject']}")
            print(f"  Snippet: {e['snippet'][:100]}")

    elif cmd == "get-code":
        domain  = sys.argv[2] if len(sys.argv) > 2 else ""
        code    = get_verification_code(sender_domain=domain, timeout_seconds=120)
        print(f"验证码: {code}")

    else:
        print("""
Gmail 邮件管理系统 — 使用方法:

  python3 email_manager.py set-password         # 保存 App Password 到 Keychain
  python3 email_manager.py test-send            # 发送测试邮件到自己
  python3 email_manager.py send-report          # 发送最新信号报告
  python3 email_manager.py read-inbox [query]   # 读取收件箱
  python3 email_manager.py get-code [domain]    # 等待并提取验证码

凭据配置:
  App Password:  export GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
  OAuth2:        放置 credentials.json 到 ~/.config/super_mario/
""")
