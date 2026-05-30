#!/usr/bin/env python3
"""
etf_flow_fetcher.py — BTC / ETH ETF 净流量 + 市场概览 采集模块

数据源: coinmarketcap.com/etf/bitcoin/ + /etf/ethereum/
采集方式: agent-browser CLI (需要 JS 渲染)
提取字段: 
  - BTC/ETH ETF: 当日净流量 / 近一周/近一月/近三月 / AUM / 基金明细
  - 市场概览: BTC主导率 / 总市值 / 总交易量 / F&G / 加密数量

用法:
  from etf_flow_fetcher import fetch_all
  data = fetch_all()
  data['btc_etf']['today_str']  # BTC ETF当日流量
  data['market']['btc_domi']     # BTC主导率
"""

import subprocess, re, json, sys
from datetime import datetime


AGENT_BROWSER = "agent-browser"


def _get_text(url: str) -> str:
    """用 agent-browser 打开页面，返回页面文本"""
    try:
        subprocess.Popen([AGENT_BROWSER, "open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import time; time.sleep(4)
        # 滚动触发图表懒加载（失败不影响后续）
        try:
            subprocess.run([AGENT_BROWSER, "eval", "window.scrollTo(0,3000)"],
                           capture_output=True, timeout=8)
        except: pass
        time.sleep(3)
        subprocess.run([AGENT_BROWSER, "wait", "--load", "networkidle"],
                       capture_output=True, text=True, timeout=60)
        proc = subprocess.run([AGENT_BROWSER, "eval", "document.body.innerText"],
                              capture_output=True, text=True, timeout=20)
        raw = proc.stdout.strip()
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw.strip('"')
        return ""
    except Exception as e:
        print(f"  [ETF fetcher] {e}", file=sys.stderr)
        return ""


def _close():
    try:
        subprocess.run([AGENT_BROWSER, "close"], capture_output=True, timeout=5)
    except Exception:
        pass


def _parse_amount(s: str) -> float | None:
    try:
        s = s.strip().replace("$","").replace(" ","").replace(",","")
        if s.endswith("B"): return float(s[:-1]) * 1e9
        if s.endswith("M"): return float(s[:-1]) * 1e6
        if s.endswith("K"): return float(s[:-1]) * 1e3
        return float(s)
    except: return None


def _extract_etf(text: str) -> dict:
    """提取 ETF 数据"""
    r = {
        "today": None, "today_str": "—",
        "last_week": None, "last_week_str": "—",
        "last_month": None, "last_month_str": "—",
        "last_3m": None, "last_3m_str": "—",
        "aum": None, "aum_str": "—", "funds": [],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    m = re.search(r'Net Flow[^(]*\(([^)]+)\)\s*\n?\s*([+-]?\s*\$\s*[\d,.]+[KMB]?)', text)
    if m:
        r["today_str"] = m.group(2).strip()
        r["today"] = _parse_amount(m.group(2))
    for label, key in [("Last Week","last_week"),("Last Month","last_month"),("Last 3 Months","last_3m")]:
        m = re.search(label + r'\s*\n?\s*([+-]?\s*\$\s*[\d,.]+[KMB]?)', text)
        if m:
            r[key+"_str"] = m.group(1).strip()
            r[key] = _parse_amount(m.group(1))
    # AUM from Total AUM chart
    aum_sec = re.search(r'Total AUM\s*30d\s*1y\s*All\s*(.{0,500}?)(\$\s*[\d,.]+[BM]\s*)+', text, re.DOTALL)
    if aum_sec:
        vals = re.findall(r'\$\s*([\d,.]+)\s*B', aum_sec.group(0))
        if vals:
            v = float(vals[-1].replace(",",""))
            if v > 1:
                r["aum"] = v * 1e9; r["aum_str"] = f"${v:.1f}B"
    # Funds list
    funds = re.findall(r'([A-Z]{2,7})\s*\n\s*([^\n]+?)\s*\n\s*(\$[\d,.]+)\s*\n\s*(\$[\d,.]+[KMB]?)\s*\n\s*(\$[\d,.]+[KMB]?)', text)
    for t,n,p,v,a,*_ in funds[:15]:
        r["funds"].append({"ticker":t.strip(),"name":n.strip(),"price":p.strip(),"aum":a.strip()})
    return r


def _extract_market_overview(text: str) -> dict:
    """从 CMC 页脚提取市场概览"""
    r = {"btc_domi": None, "eth_domi": None, "total_mcap": None, "total_mcap_str": "—",
         "total_vol_24h": None, "fng": None, "fng_str": "—",
         "crypto_count": None, "exchange_count": None}
    # "Cryptos: 51.45MExchanges: 948Market Cap: $2.47T..."
    m = re.search(r'Cryptos:\s*([\d.]+[KM]?)', text)
    if m: r["crypto_count"] = m.group(1)
    m = re.search(r'Exchanges:\s*([\d,]+)', text)
    if m: r["exchange_count"] = m.group(1)
    m = re.search(r'Market Cap:\s*\$([\d,.]+[TBM]?)', text)
    if m:
        r["total_mcap_str"] = f"${m.group(1)}"
        r["total_mcap"] = _parse_amount(m.group(1))
    m = re.search(r'24h Vol:\s*\$([\d,.]+[TBM]?)', text)
    if m: r["total_vol_24h_str"] = f"${m.group(1)}"
    m = re.search(r'Dominance:\s*BTC:\s*([\d.]+)%', text)
    if m: r["btc_domi"] = float(m.group(1))
    m = re.search(r'ETH:\s*([\d.]+)%', text)
    if m: r["eth_domi"] = float(m.group(1))
    m = re.search(r'Fear & Greed:\s*([\d]+)/100', text)
    if m:
        r["fng"] = int(m.group(1))
        v = int(m.group(1))
        r["fng_str"] = "极度恐惧" if v <= 25 else ("恐惧" if v <= 45 else ("中性" if v <= 55 else ("贪婪" if v <= 75 else "极度贪婪")))
    return r


def fetch_all() -> dict:
    """返回 {btc_etf, eth_etf, market, generated_at}"""
    btc_text = _get_text("https://coinmarketcap.com/etf/bitcoin/")
    eth_text = _get_text("https://coinmarketcap.com/etf/ethereum/")
    _close()
    btc = _extract_etf(btc_text) if btc_text else {"error":"采集失败","today_str":"—"}
    eth = _extract_etf(eth_text) if eth_text else {"error":"采集失败","today_str":"—"}
    btc["asset"] = "BTC"
    eth["asset"] = "ETH"
    market = _extract_market_overview(btc_text or "") if btc_text else {}
    return {"btc_etf": btc, "eth_etf": eth, "market": market,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


if __name__ == "__main__":
    print(json.dumps(fetch_all(), indent=2, ensure_ascii=False))
