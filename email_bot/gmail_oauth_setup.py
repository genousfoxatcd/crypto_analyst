#!/usr/bin/env python3
"""
Gmail OAuth2 一次性授权配置向导
运行一次获取 refresh_token，后续自动刷新
"""

import json
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "super_mario"
CRED_FILE  = CONFIG_DIR / "gmail_credentials.json"
TOKEN_FILE = CONFIG_DIR / "gmail_token.json"

GMAIL_ADDRESS = "alexhoo2025@gmail.com"

GUIDE = """
╔══════════════════════════════════════════════════════════════════╗
║         Gmail OAuth2 配置向导 — Super Mario Email System        ║
╚══════════════════════════════════════════════════════════════════╝

【步骤 1】在 Google Cloud Console 创建 OAuth2 凭据
  1. 访问 https://console.cloud.google.com/
  2. 新建项目（或选择已有项目）
  3. 启用 Gmail API：APIs & Services → Enable APIs → Gmail API
  4. 创建凭据：Credentials → Create Credentials → OAuth client ID
     - 应用类型：Desktop App
     - 名称：super-mario-email
  5. 下载 JSON 文件（credentials.json）

【步骤 2】将下载的 credentials.json 放到指定位置
  目标路径: ~/.config/super_mario/gmail_credentials.json

  快捷命令（替换 ~/Downloads/credentials.json 为你的实际路径）:
  mkdir -p ~/.config/super_mario
  cp ~/Downloads/credentials.json ~/.config/super_mario/gmail_credentials.json

【步骤 3】运行授权
  python3 gmail_oauth_setup.py authorize

【App Password 配置（用于 SMTP 发送）】
  1. 访问 https://myaccount.google.com/security
  2. 确保已开启两步验证
  3. 搜索 "应用密码" → 选择应用: 邮件，设备: Mac → 生成
  4. 保存生成的 16 位密码（格式: xxxx xxxx xxxx xxxx）
  5. 运行: python3 email_manager.py set-password

╔══════════════════════════════════════════════════════════════════╗
║  当前状态                                                        ║
"""

def check_status():
    cred_ok  = CRED_FILE.exists()
    token_ok = TOKEN_FILE.exists()
    print(GUIDE)
    print(f"║  credentials.json : {'✅ 已配置' if cred_ok  else '❌ 未找到 — 请完成步骤1-2'}")
    print(f"║  gmail_token.json : {'✅ 已授权' if token_ok else '❌ 未授权 — 请运行步骤3'}")

    import subprocess
    result = subprocess.run(
        ["security", "find-generic-password", "-a", GMAIL_ADDRESS,
         "-s", "gmail_app_password", "-w"],
        capture_output=True, text=True, timeout=5
    )
    smtp_ok = result.returncode == 0
    print(f"║  App Password     : {'✅ 已配置' if smtp_ok else '❌ 未配置 — 请完成 App Password 步骤'}")
    print("╚══════════════════════════════════════════════════════════════════╝")


def do_authorize():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("❌ 缺少依赖，请运行: pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    if not CRED_FILE.exists():
        print(f"❌ 未找到 credentials.json，请先完成步骤1-2")
        print(f"   目标路径: {CRED_FILE}")
        sys.exit(1)

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    print(f"\n正在打开浏览器进行 Google OAuth2 授权...")
    print(f"请登录 {GMAIL_ADDRESS} 并授权访问 Gmail")
    flow  = InstalledAppFlow.from_client_secrets_file(str(CRED_FILE), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    print(f"\n✅ OAuth2 授权成功！Token 已保存至: {TOKEN_FILE}")
    print("现在可以运行 email_manager.py read-inbox 测试读取邮件")


def install_deps():
    import subprocess
    deps = [
        "google-auth-oauthlib",
        "google-api-python-client",
        "playwright",
    ]
    print("安装依赖包...")
    subprocess.run([sys.executable, "-m", "pip", "install"] + deps, check=True)
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    print("✅ 所有依赖安装完成")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        check_status()
    elif cmd == "authorize":
        do_authorize()
    elif cmd == "install":
        install_deps()
    else:
        print("用法:")
        print("  python3 gmail_oauth_setup.py status    # 查看配置状态")
        print("  python3 gmail_oauth_setup.py authorize # 执行 OAuth2 授权")
        print("  python3 gmail_oauth_setup.py install   # 安装所有依赖")
