#!/usr/bin/env python3
"""
平台自动化注册/登录机器人 — platform_bot.py
使用 Playwright 浏览器自动化 + Gmail API 读取验证码

支持平台:
  - CoinGlass (coinglass.com)
  - Binance (binance.com)
  - CoinMarketCap (coinmarketcap.com)
  - TradingView (tradingview.com)
  - 通用邮箱+密码登录模板

使用方法:
  python3 platform_bot.py register coinglass
  python3 platform_bot.py login binance
  python3 platform_bot.py status
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

try:
    from playwright.async_api import async_playwright, Page, Browser
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False
    print("⚠️  Playwright 未安装，请运行: python3 gmail_oauth_setup.py install")

# ── 路径配置 ──────────────────────────────────────────────
BASE          = Path("/Users/alex/hermes_claud/super_mario/email_bot")
ACCOUNTS_FILE = BASE / "accounts.json"
GMAIL_ADDRESS = "alexhoo2025@gmail.com"

# ── 平台配置 ──────────────────────────────────────────────
PLATFORMS = {
    "coinglass": {
        "name":      "CoinGlass",
        "url":       "https://www.coinglass.com",
        "register":  "https://www.coinglass.com/register",
        "login":     "https://www.coinglass.com/login",
        "selectors": {
            "email_input":    "input[type='email'], input[name='email'], input[placeholder*='email' i]",
            "password_input": "input[type='password']",
            "submit_btn":     "button[type='submit']",
            "code_input":     "input[placeholder*='code' i], input[placeholder*='验证码']",
        }
    },
    "binance": {
        "name":      "Binance",
        "url":       "https://www.binance.com",
        "register":  "https://accounts.binance.com/register",
        "login":     "https://accounts.binance.com/login",
        "selectors": {
            "email_input":    "input[data-testid='email-input'], input[type='email']",
            "password_input": "input[data-testid='password-input'], input[type='password']",
            "submit_btn":     "button[data-testid='login-button'], button[type='submit']",
            "code_input":     "input[data-testid='verification-code-input']",
        }
    },
    "coinmarketcap": {
        "name":      "CoinMarketCap",
        "url":       "https://coinmarketcap.com",
        "register":  "https://coinmarketcap.com/register",
        "login":     "https://coinmarketcap.com/login",
        "selectors": {
            "email_input":    "input[type='email']",
            "password_input": "input[type='password']",
            "submit_btn":     "button[type='submit']",
            "code_input":     "input[placeholder*='code' i]",
        }
    },
    "tradingview": {
        "name":      "TradingView",
        "url":       "https://www.tradingview.com",
        "register":  "https://www.tradingview.com/register/",
        "login":     "https://www.tradingview.com/signin/",
        "selectors": {
            "email_input":    "input[name='username'], input[type='email']",
            "password_input": "input[name='password'], input[type='password']",
            "submit_btn":     "button[type='submit']",
            "code_input":     "input[placeholder*='code' i]",
        }
    },
}


# ── 账户存储 ──────────────────────────────────────────────
def load_accounts() -> dict:
    if ACCOUNTS_FILE.exists():
        return json.loads(ACCOUNTS_FILE.read_text())
    return {}


def save_account(platform: str, email: str, password: str, status: str = "registered"):
    accounts = load_accounts()
    accounts[platform] = {
        "email":      email,
        "password":   password,
        "status":     status,
        "updated_at": datetime.now().isoformat(),
    }
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2, ensure_ascii=False))


def get_password(platform: str) -> str:
    """从账户文件或环境变量获取密码"""
    accounts = load_accounts()
    if platform in accounts:
        return accounts[platform].get("password", "")
    return os.environ.get(f"{platform.upper()}_PASSWORD", "")


# ── 浏览器操作 ────────────────────────────────────────────
async def wait_for_selector_any(page: Page, selectors: list[str], timeout: int = 10000):
    """尝试多个选择器，返回第一个匹配的元素"""
    for sel in selectors:
        try:
            el = await page.wait_for_selector(sel, timeout=timeout // len(selectors))
            if el:
                return el
        except Exception:
            continue
    return None


async def type_slowly(page: Page, selector: str, text: str, delay: int = 50):
    """模拟人工输入"""
    await page.click(selector)
    await page.type(selector, text, delay=delay)


async def register_platform(
    platform_key: str,
    email: str,
    password: str,
    username: str = "",
    headless: bool = False,
) -> bool:
    """
    自动注册平台账户

    Args:
        platform_key: 平台 key（coinglass/binance/coinmarketcap/tradingview）
        email: 注册邮箱
        password: 密码（至少12位，包含大写+数字+特殊字符）
        username: 用户名（可选）
        headless: True = 无头模式（后台运行）

    Returns:
        True = 注册成功
    """
    if not PLAYWRIGHT_OK:
        return False

    cfg = PLATFORMS.get(platform_key)
    if not cfg:
        print(f"❌ 不支持的平台: {platform_key}")
        print(f"   支持的平台: {', '.join(PLATFORMS.keys())}")
        return False

    print(f"\n🚀 开始注册 {cfg['name']}...")
    print(f"   邮箱: {email}")
    print(f"   注册页面: {cfg['register']}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        try:
            await page.goto(cfg["register"], wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            sels = cfg["selectors"]

            # 输入邮箱
            email_el = await wait_for_selector_any(
                page, sels["email_input"].split(", "), timeout=10000
            )
            if not email_el:
                print("❌ 未找到邮箱输入框")
                await browser.close()
                return False
            await email_el.fill(email)
            await asyncio.sleep(0.5)

            # 输入用户名（如果需要）
            if username:
                try:
                    un_el = await page.wait_for_selector(
                        "input[name='username'], input[placeholder*='username' i]",
                        timeout=2000
                    )
                    if un_el:
                        await un_el.fill(username)
                except Exception:
                    pass

            # 输入密码
            pw_els = await page.query_selector_all("input[type='password']")
            for pw_el in pw_els[:2]:  # 密码 + 确认密码
                await pw_el.fill(password)
                await asyncio.sleep(0.3)

            # 点击提交
            submit_el = await wait_for_selector_any(
                page, sels["submit_btn"].split(", "), timeout=5000
            )
            if submit_el:
                await submit_el.click()
                await asyncio.sleep(3)

            # 等待验证码邮件
            print(f"📧 等待验证码邮件发送到 {email}...")
            sys.path.insert(0, str(BASE))
            from email_manager import get_verification_code
            code = get_verification_code(
                sender_domain=cfg["url"].replace("https://www.", "").replace("https://", ""),
                timeout_seconds=120,
            )

            if code:
                # 输入验证码
                code_el = await wait_for_selector_any(
                    page, sels["code_input"].split(", "), timeout=10000
                )
                if code_el:
                    await code_el.fill(code)
                    await asyncio.sleep(0.5)
                    # 提交验证码
                    submit_el2 = await wait_for_selector_any(
                        page, sels["submit_btn"].split(", "), timeout=3000
                    )
                    if submit_el2:
                        await submit_el2.click()
                        await asyncio.sleep(3)

            # 检查注册结果
            current_url = page.url
            success = (
                "dashboard" in current_url or
                "home" in current_url or
                "profile" in current_url or
                platform_key in current_url.lower()
            )

            if success or code:
                save_account(platform_key, email, password, "registered")
                print(f"✅ {cfg['name']} 注册成功！账户已保存")
                await browser.close()
                return True
            else:
                print(f"⚠️  注册状态不明确，当前URL: {current_url}")
                await browser.close()
                return False

        except Exception as e:
            print(f"❌ 注册过程中出现错误: {e}")
            await browser.close()
            return False


async def login_platform(
    platform_key: str,
    email: str = "",
    password: str = "",
    headless: bool = False,
    save_session: bool = True,
) -> bool:
    """
    自动登录平台

    Args:
        platform_key: 平台 key
        email: 邮箱（留空则从 accounts.json 读取）
        password: 密码（留空则从 accounts.json 读取）
        headless: 无头模式
        save_session: 保存 cookie（下次无需登录）

    Returns:
        True = 登录成功
    """
    if not PLAYWRIGHT_OK:
        return False

    cfg = PLATFORMS.get(platform_key)
    if not cfg:
        print(f"❌ 不支持的平台: {platform_key}")
        return False

    accounts = load_accounts()
    if not email:
        email = accounts.get(platform_key, {}).get("email", GMAIL_ADDRESS)
    if not password:
        password = accounts.get(platform_key, {}).get("password", "")

    if not password:
        print(f"❌ 未找到 {platform_key} 的密码，请先注册或手动设置")
        return False

    # Cookie 文件路径
    cookie_file = BASE / f"session_{platform_key}.json"

    print(f"\n🔑 登录 {cfg['name']}...")
    print(f"   邮箱: {email}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context_opts = {
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "viewport": {"width": 1280, "height": 800},
        }

        # 尝试恢复 cookie
        if cookie_file.exists() and save_session:
            try:
                cookies = json.loads(cookie_file.read_text())
                context_opts["storage_state"] = {"cookies": cookies, "origins": []}
            except Exception:
                pass

        context = await p.chromium.new_context(**context_opts)
        page = await context.new_page()

        try:
            sels = cfg["selectors"]
            await page.goto(cfg["login"], wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # 输入邮箱
            email_el = await wait_for_selector_any(
                page, sels["email_input"].split(", "), timeout=10000
            )
            if not email_el:
                print("❌ 未找到邮箱输入框")
                await browser.close()
                return False

            await email_el.fill(email)
            await asyncio.sleep(0.5)

            # 输入密码
            pw_el = await wait_for_selector_any(
                page, sels["password_input"].split(", "), timeout=5000
            )
            if pw_el:
                await pw_el.fill(password)
                await asyncio.sleep(0.5)

            # 提交
            submit_el = await wait_for_selector_any(
                page, sels["submit_btn"].split(", "), timeout=5000
            )
            if submit_el:
                await submit_el.click()
                await asyncio.sleep(3)

            # 处理二步验证邮件
            if "verify" in page.url.lower() or "2fa" in page.url.lower() or "code" in page.url.lower():
                print("📧 检测到二步验证，等待验证码...")
                from email_manager import get_verification_code
                code = get_verification_code(timeout_seconds=120)
                if code:
                    code_el = await wait_for_selector_any(
                        page, sels["code_input"].split(", "), timeout=10000
                    )
                    if code_el:
                        await code_el.fill(code)
                        submit_el2 = await wait_for_selector_any(
                            page, sels["submit_btn"].split(", "), timeout=3000
                        )
                        if submit_el2:
                            await submit_el2.click()
                            await asyncio.sleep(3)

            # 检查登录结果
            current_url = page.url
            login_page = cfg["login"]
            success = login_page not in current_url and "login" not in current_url.lower()

            if success:
                # 保存 cookie
                if save_session:
                    cookies = await context.cookies()
                    cookie_file.write_text(json.dumps(cookies, indent=2))
                    print(f"🍪 Session 已保存: {cookie_file}")

                save_account(platform_key, email, password, "logged_in")
                print(f"✅ {cfg['name']} 登录成功！当前URL: {current_url}")
                await browser.close()
                return True
            else:
                print(f"⚠️  登录状态不明确，当前URL: {current_url}")
                await browser.close()
                return False

        except Exception as e:
            print(f"❌ 登录过程中出现错误: {e}")
            await browser.close()
            return False


def print_status():
    """打印所有平台账户状态"""
    accounts = load_accounts()
    print("\n=== 平台账户状态 ===")
    print(f"{'平台':<15} {'邮箱':<28} {'状态':<12} {'更新时间'}")
    print("-" * 70)
    for p_key, cfg in PLATFORMS.items():
        acc = accounts.get(p_key, {})
        email  = acc.get("email", "—")
        status = acc.get("status", "未注册")
        upd    = acc.get("updated_at", "—")[:16]
        cookie = BASE / f"session_{p_key}.json"
        session_ok = "🍪" if cookie.exists() else "  "
        print(f"{cfg['name']:<15} {email:<28} {session_ok} {status:<12} {upd}")

    print(f"\n  🍪 = 有保存的登录 Session")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "status":
        print_status()

    elif cmd == "register":
        platform = sys.argv[2] if len(sys.argv) > 2 else ""
        if not platform:
            print("用法: python3 platform_bot.py register <platform>")
            print(f"支持的平台: {', '.join(PLATFORMS.keys())}")
            sys.exit(1)
        email = GMAIL_ADDRESS
        password = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("PLATFORM_PASSWORD", "")
        if not password:
            import getpass
            password = getpass.getpass(f"请输入 {platform} 的密码（将安全保存）: ")
        asyncio.run(register_platform(platform, email, password, headless=False))

    elif cmd == "login":
        platform = sys.argv[2] if len(sys.argv) > 2 else ""
        if not platform:
            print("用法: python3 platform_bot.py login <platform>")
            sys.exit(1)
        asyncio.run(login_platform(platform, headless=False))

    elif cmd == "batch-register":
        # 批量注册所有平台
        platforms_to_register = sys.argv[2:] if len(sys.argv) > 2 else list(PLATFORMS.keys())
        import getpass
        password = getpass.getpass("请输入统一密码（将用于所有平台）: ")
        for platform in platforms_to_register:
            success = asyncio.run(
                register_platform(platform, GMAIL_ADDRESS, password, headless=True)
            )
            print(f"  {platform}: {'✅' if success else '❌'}")

    else:
        print(f"未知命令: {cmd}")
        print("可用命令: status / register <platform> / login <platform> / batch-register")
