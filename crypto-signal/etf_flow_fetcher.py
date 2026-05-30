#!/usr/bin/env python3
"""
etf_flow_fetcher.py — BTC / ETH ETF 净流量采集模块

数据源: coinmarketcap.com/etf/bitcoin/ + /etf/ethereum/
采集方式: agent-browser CLI (需要 JS 渲染)
提取字段: 当日净流量 / 近一周 / 近一月 / 近三月 / AUM / 各ETF基金明细

用法:
  from etf_flow_fetcher import fetch_btc_etf, fetch_eth_etf
  btc = fetch_btc_etf()
  eth = fetch_eth_etf()
"""

import subprocess, re, json, sys
from datetime import datetime
from pathlib import Path

AGENT_BROWSER = "agent-browser"
TIMEOUT_SEC = 45


def _agent_open_and_get(url: str, js: str = "document.body.innerText") -> str:
    """打开页面、等待加载、执行JS、返回文本（不关闭browser daemon）
    
    Note: agent-browser 使用 daemon 模式，open 命令会启动后台进程
    因此需要用 Popen 而非 subprocess.run（后者会等待进程退出）
    """
    try:
        subprocess.Popen([AGENT_BROWSER, "open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 等待 daemon 启动 + 页面加载
        import time
        time.sleep(8)
        subprocess.run([AGENT_BROWSER, "wait", "--load", "networkidle"],
                       capture_output=True, text=True, timeout=90)
        proc = subprocess.run([AGENT_BROWSER, "eval", js],
                              capture_output=True, text=True, timeout=20)
        raw = proc.stdout.strip()
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw.strip('"')
        return ""
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"  [ETF fetcher] error: {e}", file=sys.stderr)
        return ""


def _close_browser():
    """关闭 agent-browser daemon"""
    try:
        subprocess.run([AGENT_BROWSER, "close"], capture_output=True, timeout=5)
    except Exception:
        pass


def fetch_btc_etf() -> dict:
    """获取比特币 ETF 流量数据"""
    text = _agent_open_and_get("https://coinmarketcap.com/etf/bitcoin/")
    if not text:
        return {"error": "agent-browser 采集失败", "today_str": "数据不可用"}
    data = _extract_net_flow(text)
    data["asset"] = "BTC"
    return data


def fetch_eth_etf() -> dict:
    """获取以太坊 ETF 流量数据"""
    text = _agent_open_and_get("https://coinmarketcap.com/etf/ethereum/")
    if not text:
        return {"error": "agent-browser 采集失败", "today_str": "数据不可用"}
    data = _extract_net_flow(text)
    data["asset"] = "ETH"
    return data


def _extract_net_flow(text: str) -> dict:
    """从 CMC 页面文本提取 ETF 净流量数据"""
    result = {
        "today": None,   # 当日净流量(float, 单位USD)
        "today_str": "数据不可用",
        "last_week": None,
        "last_week_str": "—",
        "last_month": None,
        "last_month_str": "—",
        "last_3m": None,
        "last_3m_str": "—",
        "aum": None,
        "aum_str": "—",
        "strongest_month": "—",
        "weakest_month": "—",
        "funds": [],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # 当日净流: "Net Flow (May 28, 2026)\n- $223.30M"
    m = re.search(r'Net Flow[^(]*\(([^)]+)\)\s*\n?\s*([+-]?\s*\$\s*[\d,.]+[KMB]?)', text)
    if m:
        result["today_str"] = f"{m.group(2).strip()}"
        result["today"] = _parse_amount(m.group(2))

    # 上周: "Last Week\n- $102.41M"
    m = re.search(r'Last Week\s*\n?\s*([+-]?\s*\$\s*[\d,.]+[KMB]?)', text)
    if m:
        result["last_week_str"] = m.group(1).strip()
        result["last_week"] = _parse_amount(m.group(1))

    # 上月
    m = re.search(r'Last Month\s*\n?\s*([+-]?\s*\$\s*[\d,.]+[KMB]?)', text)
    if m:
        result["last_month_str"] = m.group(1).strip()
        result["last_month"] = _parse_amount(m.group(1))

    # 近3月
    m = re.search(r'Last 3 Months\s*\n?\s*([+-]?\s*\$\s*[\d,.]+[KMB]?)', text)
    if m:
        result["last_3m_str"] = m.group(1).strip()
        result["last_3m"] = _parse_amount(m.group(1))

    # AUM: "6.4%\n6.8%\n7.2%\n7.16%" 前面的"$100B\n$105B\n$110B\n$106B"
    # 取最后出现的 $xxxB 值
    aum_matches = re.findall(r'\$([\d,.]+)([BM])\b', text)
    if aum_matches:
        # 找 ETF 净值表后面的 AUM 值，通常是最后一个大的 B值
        for val, unit in reversed(aum_matches):
            v = float(val.replace(",", ""))
            if unit == "B" and v > 1:
                result["aum"] = v * 1e9
                result["aum_str"] = f"${v:.1f}B"
                break

    # 最强/最弱月份
    m = re.search(r'Strongest Month[^(]*\(([^)]+)\).*?\+\s*\$([\d,.]+[BM]?)', text)
    if m:
        result["strongest_month"] = f"{m.group(1).strip()} +${m.group(2)}"

    m = re.search(r'Weakest Month[^(]*\(([^)]+)\).*?-\s*\$([\d,.]+[BM]?)', text)
    if m:
        result["weakest_month"] = f"{m.group(1).strip()} -${m.group(2)}"

    # 提取各 ETF 基金明细 (Ticker\tFund Name\tPrice\tVolume\tAUM\t...)
    # 格式: "IBIT\n\tiShares Bitcoin Trust\n$41.86\n$893.39M\n$66.57B\n..."
    fund_pattern = re.findall(
        r'([A-Z]{2,7})\s*\n\s*([^\n]+?)\s*\n\s*(\$[\d,.]+)\s*\n\s*(\$[\d,.]+[KMB]?)\s*\n\s*(\$[\d,.]+[KMB]?)\s*\n\s*(\$[\d,.]+[KMB]?)',
        text
    )
    for ticker, name, price, volume, aum_, mcap in fund_pattern[:20]:
        result["funds"].append({
            "ticker": ticker.strip(),
            "name": name.strip(),
            "price": price.strip(),
            "volume": volume.strip(),
            "aum": aum_.strip(),
        })

    return result


def _parse_amount(s: str) -> float:
    """解析 $123.45M / $1.2B / $500K 为 float"""
    s = s.strip().replace("$", "").replace(" ", "").replace(",", "")
    try:
        if s.endswith("B"):
            return float(s[:-1]) * 1e9
        elif s.endswith("M"):
            return float(s[:-1]) * 1e6
        elif s.endswith("K"):
            return float(s[:-1]) * 1e3
        else:
            return float(s)
    except ValueError:
        return 0.0


def fetch_all() -> dict:
    """一次采集 BTC + ETH ETF 流量，结束后关闭浏览器"""
    result = {
        "btc_etf": fetch_btc_etf(),
        "eth_etf": fetch_eth_etf(),
    }
    _close_browser()
    return result


# ═══════════════════════  CLI 测试 ═══════════════════════
if __name__ == "__main__":
    import json as _json
    print("=== BTC ETF ===")
    btc = fetch_btc_etf()
    print(_json.dumps(btc, indent=2, ensure_ascii=False))
    print("\n=== ETH ETF ===")
    eth = fetch_eth_etf()
    print(_json.dumps(eth, indent=2, ensure_ascii=False))
