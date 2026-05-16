"""
Crypto Signal Machine — urllib 版本（无第三方包依赖）
四维评分模型（满分100）：
  1. 趋势 (EMA20 vs price, EMA20 vs EMA50)       max 25 pts
  2. 资金流 (BTC ETF 净流量)                     max 25 pts
  3. 衍生品 (资金费率 + OI 7日变化)             max 25 pts
  4. 宏观 (纳指 vs EMA20)                       max 25 pts

数据源（全部无需 API Key）：
  - Bybit Spot:       现货价格
  - CoinGecko:        现货价格 + BTC 主导率
  - Bybit Futures:    资金费率 + 持仓量(OI)
  - CoinMarketCap:    BTC ETF 净流量
  - Alternative.me:   恐慌贪婪指数
  - Yahoo Finance:    纳指 OHLC（EMA 计算）
  - Bybit OHLC:       各币种 EMA 计算

输出：
  - report_YYYY-MM-DD.html   完整 HTML 报告
  - report_output.json        Cron 任务读取的调度文件
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import os
import statistics
from datetime import datetime, timezone, timedelta

# ─── 全局配置 ────────────────────────────────────────────────────────────────

COINS = ["BTC", "ETH", "BNB", "SOL", "DOGE"]

COINGECKO_IDS = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "BNB":  "binancecoin",
    "SOL":  "solana",
    "DOGE": "dogecoin",
}

BYBIT_SPOT = {
    "BTC":  "BTCUSDT",
    "ETH":  "ETHUSDT",
    "BNB":  "BNBUSDT",
    "SOL":  "SOLUSDT",
    "DOGE": "DOGEUSDT",
}

BYBIT_PERP = {
    "BTC":  "BTCUSDT",
    "ETH":  "ETHUSDT",
    "BNB":  "BNBUSDT",
    "SOL":  "SOLUSDT",
    "DOGE": "DOGEUSDT",
}

YAHOO_TICKERS = {
    "BTC":  "BTC-USD",
    "ETH":  "ETH-USD",
    "BNB":  "BNB-USD",
    "SOL":  "SOL-USD",
    "DOGE": "DOGE-USD",
}

REPORT_DIR = os.path.dirname(os.path.abspath(__file__))


# ─── HTTP 工具函数 ────────────────────────────────────────────────────────────

def http_get(url, params=None, headers=None, timeout=15):
    """统一 HTTP GET（urllib 版），返回解析后的 JSON 或原始文本。"""
    if params:
        if "?" in url:
            url += "&" + urllib.parse.urlencode(params)
        else:
            url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
    req.add_header("Accept", "application/json, text/html, */*")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            if "json" in ct or "application" in ct:
                return json.loads(raw.decode("utf-8"))
            return raw.decode("utf-8")
    except Exception as e:
        print(f"  [WARN] GET {url[:60]} → {e}")
        return None


# ─── 1. 现货价格 ─────────────────────────────────────────────────────────────

def fetch_prices():
    """三级兜底：Bybit Spot → CoinGecko。"""
    prices = {}

    # Tier 1: Bybit Spot
    data = http_get("https://api.bybit.com/v5/market/tickers",
                    params={"category": "spot"})
    if data and data.get("retCode") == 0:
        rev = {v: k for k, v in BYBIT_SPOT.items()}
        for item in data["result"]["list"]:
            sym = item["symbol"]
            if sym in rev:
                prices[rev[sym]] = float(item["lastPrice"])
    if len(prices) >= 3:
        print(f"  [prices] Bybit: {prices}")
        return prices

    # Tier 2: CoinGecko
    ids = ",".join(COINGECKO_IDS.values())
    data = http_get("https://api.coingecko.com/api/v3/simple/price",
                     params={"ids": ids, "vs_currencies": "usd"})
    if data:
        rev = {v: k for k, v in COINGECKO_IDS.items()}
        for cg_id, vals in data.items():
            if cg_id in rev:
                prices[rev[cg_id]] = vals["usd"]
        print(f"  [prices] CoinGecko: {prices}")
    return prices


# ─── 2. EMA 计算 ─────────────────────────────────────────────────────────────

def ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    val = statistics.mean(prices[:period])
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


def fetch_ohlc_bybit(coin, limit=90):
    sym = BYBIT_PERP.get(coin)
    if not sym:
        return []
    data = http_get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "linear", "symbol": sym, "interval": "D", "limit": str(limit)}
    )
    if not data or data.get("retCode") != 0:
        return []
    # [startTime, open, high, low, close, volume, turnover] — newest first
    closes = [float(r[4]) for r in reversed(data["result"]["list"])]
    return closes


def fetch_ohlc_yahoo(coin, limit=90):
    ticker = YAHOO_TICKERS.get(coin)
    if not ticker:
        return []
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&range={limit}d")
    data = http_get(url)
    if not data:
        return []
    try:
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return [float(c) for c in closes if c is not None]
    except Exception as e:
        print(f"  [WARN] Yahoo OHLC {coin}: {e}")
        return []


def fetch_ohlc(coin, limit=90):
    c = fetch_ohlc_bybit(coin, limit)
    if len(c) >= 20:
        return c
    return fetch_ohlc_yahoo(coin, limit)


def fetch_nasdaq_ema():
    # ^IXIC first
    data = http_get(
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EIXIC",
        params={"interval": "1d", "range": "60d"}
    )
    if data:
        try:
            closes = [c for c in
                      data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                      if c is not None]
            if len(closes) >= 20:
                return closes[-1], ema(closes, 20)
        except Exception as e:
            print(f"  [WARN] ^IXIC: {e}")

    # ^NDX fallback
    data = http_get(
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX",
        params={"interval": "1d", "range": "60d"}
    )
    if data:
        try:
            closes = [c for c in
                      data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                      if c is not None]
            if len(closes) >= 20:
                return closes[-1], ema(closes, 20)
        except Exception as e:
            print(f"  [WARN] ^NDX: {e}")

    return None, None


# ─── 3. 资金费率 + OI ───────────────────────────────────────────────────────

def fetch_funding_rates():
    rates = {}
    data = http_get("https://api.bybit.com/v5/market/tickers",
                    params={"category": "linear"})
    if data and data.get("retCode") == 0:
        rev = {v: k for k, v in BYBIT_PERP.items()}
        for item in data["result"]["list"]:
            sym = item["symbol"]
            if sym in rev:
                fr = item.get("fundingRate")
                if fr is not None:
                    rates[rev[sym]] = float(fr) * 100
    for coin in COINS:
        rates.setdefault(coin, 0.0)
    print(f"  [funding_rates] {rates}")
    return rates


def fetch_open_interest():
    result = {}
    for coin in COINS:
        sym = BYBIT_PERP.get(coin)
        if not sym:
            result[coin] = (0.0, 0.0)
            continue
        try:
            d1 = http_get(
                "https://api.bybit.com/v5/market/open-interest",
                params={"category": "linear", "symbol": sym,
                        "intervalTime": "1h", "limit": "1"}
            )
            current_oi = 0.0
            if d1 and d1.get("retCode") == 0 and d1["result"]["list"]:
                current_oi = float(d1["result"]["list"][0].get("openInterest", 0))

            d2 = http_get(
                "https://api.bybit.com/v5/market/open-interest",
                params={"category": "linear", "symbol": sym,
                        "intervalTime": "D", "limit": "8"}
            )
            pct = 0.0
            if d2 and d2.get("retCode") == 0 and len(d2["result"]["list"]) >= 2:
                hist = d2["result"]["list"]
                new_oi = float(hist[0].get("openInterest", 0))
                old_oi = float(hist[-1].get("openInterest", 0))
                pct = ((new_oi - old_oi) / old_oi * 100) if old_oi else 0.0

            result[coin] = (current_oi, round(pct, 2))
        except Exception as e:
            print(f"  [WARN] OI {coin}: {e}")
            result[coin] = (0.0, 0.0)

    print(f"  [open_interest] {result}")
    return result


# ─── 4. ETF 净流量 ───────────────────────────────────────────────────────────

def fetch_etf_flow():
    """CMC BTC ETF 净流量（百万美元）。兜底：Bybit OI 变化代理。"""
    data = http_get(
        "https://api.coinmarketcap.com/data-api/v3/etf/netflow/metrics",
        params={"category": "btc", "convertId": "2781"}
    )
    if data:
        try:
            cur = data.get("data", {}).get("current", {})
            for key in ("value", "netFlow", "netInflow", "net_flow", "amount"):
                if key in cur:
                    flow_m = round(float(cur[key]) / 1e6, 2)
                    print(f"  [CMC ETF] {flow_m:+.2f}M USD ({key})")
                    return flow_m
        except Exception as e:
            print(f"  [WARN] CMC ETF: {e}")
    return _bybit_oi_proxy()


def _bybit_oi_proxy():
    """备用：用 Bybit BTC 持仓量7日变化作为资金流向粗略代理。"""
    try:
        d = http_get(
            "https://api.bybit.com/v5/market/open-interest",
            params={"category": "linear", "symbol": "BTCUSDT",
                    "intervalTime": "D", "limit": "8"}
        )
        if d and d.get("retCode") == 0 and len(d["result"]["list"]) >= 2:
            oi_new = float(d["result"]["list"][0].get("openInterest", 0))
            oi_old = float(d["result"]["list"][-1].get("openInterest", 0))
            # 换算为近似 M USD（OI * BTC价格近似 $75000）
            proxy = round(oi_new * 75000 / 1e6, 2)
            print(f"  [Bybit OI proxy] {proxy:+.1f}M USD")
            return proxy
    except Exception as e:
        print(f"  [WARN] Bybit OI proxy: {e}")
    return 0.0


# ─── 5. 恐慌贪婪指数 ─────────────────────────────────────────────────────────

def fetch_fear_greed():
    data = http_get("https://api.alternative.me/fng/?limit=1")
    if data:
        try:
            d = data["data"][0]
            return int(d["value"]), d["value_classification"]
        except Exception as e:
            print(f"  [WARN] F&G: {e}")
    return None, "Unknown"


# ─── 6. BTC 市占率 ──────────────────────────────────────────────────────────

def fetch_btc_dominance():
    data = http_get("https://api.coingecko.com/api/v3/global")
    if data:
        try:
            dom = data.get("data", {}).get("market_cap_percentage", {}).get("btc", 0)
            if dom:
                return round(float(dom), 1)
        except Exception as e:
            print(f"  [WARN] BTC dom: {e}")
    return None


# ─── 7. 四维评分模型 ────────────────────────────────────────────────────────

def score_trend(closes):
    """趋势（满分25）：价格>EMA20 +15 / EMA20>EMA50金叉 +10"""
    if not closes or len(closes) < 50:
        return 12, None, None
    price = closes[-1]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    pts = (15 if (e20 and price > e20) else 0) + (10 if (e20 and e50 and e20 > e50) else 0)
    return pts, e20, e50


def score_flow(etf_flow_m, coin):
    if coin == "BTC":
        return 25 if etf_flow_m > 50 else (15 if etf_flow_m > 0 else 5)
    return 18 if etf_flow_m > 50 else (12 if etf_flow_m > 0 else 6)


def score_derivatives(fr, oi_7d):
    pts = 15 if fr < -0.005 else (12 if fr < 0 else (8 if fr <= 0.03 else 2))
    pts += 10 if abs(oi_7d) < 5 else (7 if abs(oi_7d) < 12 else 2)
    return pts


def score_macro(nasdaq_last, nasdaq_ema20):
    if nasdaq_last is None or nasdaq_ema20 is None:
        return 12
    return 25 if nasdaq_last > nasdaq_ema20 else 5


def signal_label(total):
    return ("BUY",  "#00c853") if total >= 75 else \
           ("HOLD", "#ffd600") if total >= 50 else \
           ("SELL", "#ff1744")


def contract_direction(total, fr):
    if total >= 75:
        return "做多 (Long)", "#00c853"
    if total >= 50:
        return ("轻仓做多 (Cautious Long)", "#a5d6a7") if fr < 0 else ("观望 (Wait)", "#ffd600")
    return "做空 (Short)", "#ff1744"


def calc_levels(price, total, fr):
    direction, _ = contract_direction(total, fr)
    if "Long" in direction or ("多" in direction and "观望" not in direction):
        return round(price * 0.985, 6), round(price * 1.10, 6), round(price * 0.945, 6), "long"
    if "Short" in direction or "空" in direction:
        return round(price * 1.015, 6), round(price * 0.92, 6), round(price * 1.05, 6), "short"
    return None, None, None, "none"


def fmt_price(coin, v):
    if v is None:
        return "—"
    if coin in ("BTC", "ETH", "BNB"):
        return f"${v:,.2f}"
    if coin == "SOL":
        return f"${v:.3f}"
    return f"${v:.5f}"


# ─── 8. HTML 报告构建 ───────────────────────────────────────────────────────



def build_html(prices, funding_rates, oi_data, etf_flow_m,
               fg_score, fg_label, btc_dom,
               nasdaq_last, nasdaq_ema20,
               coin_emas, analysis_date):

    fg_v      = fg_score if fg_score is not None else 50
    fg_col    = "#d9534f" if fg_v < 25 else ("#d4a017" if fg_v < 50 else "#4caf82")
    nasdaq_ok = bool(nasdaq_last and nasdaq_ema20 and nasdaq_last > nasdaq_ema20)
    nasdaq_col = "#4caf82" if nasdaq_ok else "#d9534f"
    nasdaq_txt = "高于 EMA20 \u2713" if nasdaq_ok else "低于 EMA20 \u2717"
    etf_col    = "#4caf82" if etf_flow_m >= 0 else "#d9534f"
    etf_sign   = "+" if etf_flow_m >= 0 else ""
    btc_price  = prices.get("BTC", 0)
    btc_dom_s  = (str(btc_dom) + "%") if btc_dom else "N/A"
    nasdaq_d   = ("{:.0f} / EMA20 {:.0f}".format(nasdaq_last, nasdaq_ema20)
                  if nasdaq_last and nasdaq_ema20 else "N/A")

    BG="#1e2c2f"; BG2="#243336"; BG3="#2c3e42"; BDR="#354d52"
    TXT="#c8d8da"; TXT2="#8aa8ac"; TXT3="#5f7e83"; ACC="#6ab0b8"
    DOT = "\u00b7"; BULLET = "\u25ce"

    # ── Build coin_data ─────────────────────────────────────────────────────
    coin_data = []
    for coin in COINS:
        closes, e20, e50 = coin_emas.get(coin, ([], None, None))
        price_v  = prices.get(coin, 0)
        fr_v     = funding_rates.get(coin, 0.0)
        oi_cur, oi_7d = oi_data.get(coin, (0.0, 0.0))

        s_t, _, _ = score_trend(closes)
        s_f = score_flow(etf_flow_m, coin)
        s_d = score_derivatives(fr_v, oi_7d)
        s_m = score_macro(nasdaq_last, nasdaq_ema20)
        total = s_t + s_f + s_d + s_m

        sig_lbl, sig_c = signal_label(total)
        dir_lbl, dir_c = contract_direction(total, fr_v)
        arr = "\u2191" if "Long" in dir_lbl or "\u505a\u591a" in dir_lbl else ("\u2192" if "\u89c2\u671b" in dir_lbl else "\u2193")
        entry, tp, sl, mode = calc_levels(price_v, total, fr_v)

        # color for funding rate
        fr_col = "#4caf82" if fr_v < -0.01 else ("#d4a017" if fr_v < 0 else "#d9534f")
        oi_col = "#4caf82" if oi_7d < 0 else ("#d4a017" if oi_7d < 5 else "#d9534f")

        # gold cross label
        if e20 and e50:
            if e20 > e50:
                gl_v = "\u9ec4\u91d1\u4ea4\u53c9(\u4e0a)\u2713"; gc_v = "#4caf82"
            else:
                gl_v = "\u6b7b\u4ea4\u53c9(\u4e0b)\u2717"; gc_v = "#d9534f"
        else:
            gl_v = "\u6570\u636e\u4e0d\u8db3"; gc_v = TXT2

        coin_data.append({
            "coin": coin, "price": price_v, "fr": fr_v,
            "oi_7d": oi_7d, "e20": e20, "e50": e50,
            "gold": gl_v, "gold_c": gc_v,
            "fr_col": fr_col, "oi_col": oi_col,
            "sig_lbl": sig_lbl, "sig_c": sig_c,
            "total": total,
            "s_trend": s_t, "s_flow": s_f, "s_deriv": s_d, "s_macro": s_m,
            "dir_c": dir_c, "arrow": arr, "dir_lbl": dir_lbl,
            "mode": mode, "entry": entry, "tp": tp, "sl": sl,
        })

    # ── Helper: card wrapper ─────────────────────────────────────────────────
    def wc(content, lc=BDR):
        return (
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="background:{{BG2}};border-radius:10px;border-left:3px solid {{lc}};margin-bottom:10px;">'
            '<tr><td style="padding:14px 16px;">{c}</td></tr></table>'
        ).format(BG2=BG2, lc=lc, c=content)

    # ── Helper: table row ─────────────────────────────────────────────────────
    def tr_row(left, right, lc=TXT2, rc=TXT, sep=True):
        b = "border-bottom:1px solid {};".format(BDR) if sep else ""
        return (
            '<tr><td style="padding:7px 0;font-size:14px;color:{lc};{b}">{l}</td>'
            '<td style="padding:7px 0;font-size:14px;color:{rc};font-weight:700;text-align:right;{b}">{r}</td></tr>'
        ).format(lc=lc, rc=rc, b=b, l=left, r=right)

    # ── Helper: section header ───────────────────────────────────────────────
    def sh(text):
        return (
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:28px 0 12px;">'
            '<tr><td style="border-left:3px solid {ACC};padding:2px 0 2px 10px;'
            'font-size:12px;font-weight:700;color:{ACC};letter-spacing:1px;">{t}</td></tr></table>'
        ).format(ACC=ACC, t=text)

    # ── Helper: badge token ───────────────────────────────────────────────────
    def bg_tok(text, bg_c, fg_c="#1c2b2e"):
        return (
            '<span style="display:inline-block;background:{bg};color:{fg};'
            'padding:3px 11px;border-radius:20px;font-weight:700;font-size:13px;">{t}</span>'
        ).format(bg=bg_c, fg=fg_c, t=text)

    # ── Helper: level row ─────────────────────────────────────────────────────
    def lv(icon, label, value, color=TXT):
        return (
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="background:{{BG3}};border-radius:7px;margin-bottom:7px;">'
            '<tr><td style="padding:10px 14px;font-size:14px;color:{T2};{{ic}} {la}</td>'
            '<td style="padding:10px 14px;font-size:15px;font-weight:700;color:{c};text-align:right;">{v}</td></tr>'
            '</table>'
        ).format(BG3=BG3, T2=TXT2, ic=icon, la=label, c=color, v=value)

    # ── Helper: metric cell ──────────────────────────────────────────────────
    def mc(val, label, vc=TXT, sub=""):
        sub_html = (
            '<div style="font-size:11px;color:{T2};margin-top:3px;">{s}</div>'
        ).format(T2=TXT2, s=sub) if sub else ""
        return (
            '<td style="padding:0 5px 10px 0;" valign="top">'
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="background:{{BG2}};border-radius:10px;">'
            '<tr><td style="padding:14px 10px;text-align:center;">'
            '<div style="font-size:22px;font-weight:700;color:{vc};line-height:1.2;">{v}</div>'
            '<div style="font-size:11px;color:{T3};margin-top:5px;">{l}</div>'
            '{sh}'
            '</td></tr></table></td>'
        ).format(BG2=BG2, T3=TXT3, vc=vc, v=val, l=label, sh=sub_html)

    # ── Build sections ─────────────────────────────────────────────────────────

    sec1 = sh("&#127758;&nbsp; \u4e00\u3001\u603b\u4f53\u6570\u636e\u6982\u51b5")
    macro_grid = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">'
        '<tr>' + mc(str(fg_v), "\u6050\u61c8\u8bdd\u8d28\u6307\u6570", fg_col, fg_label)
                 + mc(btc_dom_s, "BTC \u5e02\u5360\u7387", ACC, "\u8d44\u91d1\u5411 BTC \u96c6\u4e2d") + '</tr>'
        '<tr>' + mc("${:,.0f}".format(btc_price), "BTC \u73b0\u8d27\u4ef7", TXT, "Bybit \u5b9e\u65f6")
                 + mc(etf_sign + "{:.1f}M".format(etf_flow_m), "BTC ETF \u51c0\u6d41\u91cf", etf_col, "CMC") + '</tr>'
        '</table>'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:14px;">'
        '<tr><td style="background:{{BG2}};border-radius:10px;padding:12px 16px;text-align:center;">'
        '<span style="font-size:16px;font-weight:700;color={nc};">\u7eb3\u65af\u8fbe\u514b {nt}</span>'
        '<br><span style="font-size:11px;color={T3};">{nd}</span>'
        '</td></tr></table>'
    ).format(BG2=BG2, nc=nasdaq_col, nt=nasdaq_txt, T3=TXT3, nd=nasdaq_d)

    score_leg = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:{{BG2}};border-radius:10px;margin-bottom:6px;">'
        '<tr><td style="padding:14px 16px;">'
        '<div style="font-size:13px;font-weight:700;color:{ACC};margin-bottom:10px;">'
        '&#128200; \u56db\u7ef4\u5ea6\u8bc4\u5206\uff08\u6ee1\u5206 100\uff09</div>'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        + tr_row("\u8d8b\u52bf /25", "\u4ef7\u683c&gt;EMA20(+15)\uff0cEMA20&gt;EMA50\u91d1\u53c9(+10)")
        + tr_row("\u8d44\u91d1\u6d41 /25", "CMC ETF \u51c0\u6d41\u5165\u8d8b\u5927\u5206\u8d8b\u9ad8")
        + tr_row("\u884d\u751f\u54c1 /25", "\u8d39\u7387\u8d8b\u8d1f + OI \u7a33\u5b9a\u5206\u8d8b\u9ad8")
        + tr_row("\u5b8f\u89c2 /25", "\u7eb3\u6307\u7ad9\u4e0a EMA20 \u6ee1\u5206", sep=False)
        + '</table>'
        '<div style="margin-top:12px;font-size:12px;">'
        '<span style="color:#4caf82;font-weight:700;">{b} 75+ BUY</span>'
        '&emsp;<span style="color:#d4a017;font-weight:700;">{b} 50-74 HOLD</span>'
        '&emsp;<span style="color:#d9534f;font-weight:700;">{b} &lt;50 SELL</span>'
        '</div></td></tr></table>'
    ).format(BG2=BG2, ACC=ACC, b=BULLET)

    sec2 = sh("&#128202;&nbsp; \u4e8c\u3001\u5404\u5e01\u79cd\u5f53\u524d\u6570\u636e")
    coin_cards = ""
    for d in coin_data:
        coin = d["coin"]
        fr_v   = d["fr"];    price_v = d["price"]
        oi_v   = d["oi_7d"]; e20_v   = d["e20"]; e50_v = d["e50"]
        gl_v   = d["gold"];  frc_v   = d["fr_col"]
        oic_v  = d["oi_col"]; gc_v   = d["gold_c"]
        sg_l   = d["sig_lbl"]; sg_c  = d["sig_c"]
        tot_s  = str(d["total"])
        st_v=d["s_trend"]; sf_v=d["s_flow"]
        sd_v=d["s_deriv"]; sm_v=d["s_macro"]
        badge_s = tot_s + DOT + sg_l
        fr_fmt  = "{:+.4f}%".format(fr_v)
        oi_fmt  = "{:+.1f}%".format(oi_v)
        ema_fmt = (fmt_price(coin,e20_v)+" / "+fmt_price(coin,e50_v) if e20_v else "N/A")
        sc_dtl  = "\u52d5\u52e2 {} \u00b7 \u8cc7\u91d1 {} \u00b7 \u884d\u751f {} \u00b7 \u5b8f\u89c2 {}".format(
            st_v, sf_v, sd_v, sm_v)
        hdr_row = (
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="margin-bottom:10px;">'
            '<tr><td style="font-size:18px;font-weight:700;color={T};{{c}}</td>'
            '<td style="font-size:15px;color={T2};>{p}</td>'
            '<td style="text-align:right;">{b}</td></tr></table>'
        ).format(T=TXT, T2=TXT2, p=fmt_price(coin, price_v), b=bg_tok(badge_s, sg_c))
        data_rows = (
            '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
            + tr_row("\u8d44\u91d1\u8d39\u7387",  fr_fmt,  rc=frc_v)
            + tr_row("OI 7\u65e5\u53d8\u5316", oi_fmt, rc=oic_v)
            + tr_row("EMA20 / EMA50", ema_fmt)
            + tr_row("\u5747\u7ebf\u5f62\u6001", gl_v, rc=gc_v)
            + tr_row("\u8bc4\u5206\u660e\u7ec6", sc_dtl, lc=TXT2, sep=False)
            + '</table>'
        )
        coin_cards += wc(hdr_row + data_rows, lc=sg_c)

    sec3 = sh("&#128221;&nbsp; \u4e09\u3001\u63a8\u8350\u64cd\u4f5c\u7b56\u7565\uff083X \u5e01\u672c\u4f4d\uff09")
    strat_cards = ""
    for d in coin_data:
        coin=d["coin"]; dir_c=d["dir_c"]; arr=d["arrow"]; dirl=d["dir_lbl"]
        mode=d["mode"]; ent=d["entry"]; tp=d["tp"]; sl=d["sl"]; sc=d["sig_c"]
        hdr2 = (
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="margin-bottom:12px;">'
            '<tr><td style="font-size:18px;font-weight:700;color={T};{{c}}</td>'
            '<td style="text-align:right;font-size:14px;font-weight:700;color={dc};>{a} {dl}</td></tr>'
            '</table>'
        ).format(T=TXT, dc=dir_c, a=arr, dl=dirl)
        if mode != "none":
            body2 = (
                lv("\ud83d\udccc","\u5165\u573a\u4ef7", fmt_price(coin, ent))
                + lv("\ud83c\udfaf","\u6b62\u76c8\u76ee\u6807", fmt_price(coin, tp), "#4caf82")
                + lv("\ud83d\udd0d","\u6b62\u635f\u7ebf", fmt_price(coin, sl), "#d9534f")
            )
        else:
            body2 = '<div style="font-size:13px;color={T3};padding:8px 0;">\u7b49\u5f85\u65b9\u5411\u786e\u8ba4\uff0c\u6682\u4e0d\u5efa\u8bae\u5f00\u4ed3</div>'.format(T3=TXT3)
        strat_cards += wc(hdr2 + body2, lc=sc)

    rk1 = "\u8d1f\u8d44\u91d1\u8d39\u7387 + \u6781\u7aef\u6050\u60e7 + ETF\u51c0\u6d41\u5165 \u4e09\u8005\u5171\u632f \u2192 \u7a7a\u5934\u8e0f\u574f\u9ec4\u91d1\u4fe1\u53f7"
    rk2 = "3X \u674e\u6756\u9006\u5411 33% \u89e6\u53d1\u5f3a\u5e73 \u00b7 \u4e25\u683c\u6267\u884c\u6b62\u635f \u00b7 \u7eb3\u6307\u8d2f\u4f4e EMA20 \u8bf7\u51cf\u534a\u4ed3\u4f4d"
    risk = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:{{BG2}};border-radius:10px;margin-top:20px;">'
        '<tr><td style="padding:14px 16px;font-size:13px;color={T2};line-height:1.9;">'
        '<span style="color:{ACC};font-weight:700;">\u6838\u5fc3\u903b\u8f91</span> {r1}<br>'
        '<span style="color:#d4a017;font-weight:700;">\u98ce\u63a7\u63d0\u793a</span> {r2}'
        '</td></tr></table>'
    ).format(BG2=BG2, T2=TXT2, ACC=ACC, r1=rk1, r2=rk2)

    footer = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:28px;">'
        '<tr><td style="text-align:center;font-size:11px;color={T3};line-height:1.9;">'
        '\u672c\u62a5\u544a\u81ea\u52a8\u751f\u6210\uff0c\u4ec5\u4f9b\u53c2\u8003\uff0c\u4e0d\u6784\u6210\u6295\u8d44\u5efa\u8bae<br>'
        '\u6bcf\u65e5\u5317\u4eac\u65f6\u95f4 09:00 \u63a8\u9001'
        '</td></tr></table>'
    ).format(T3=TXT3)

    html = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{margin:0;padding:0;background:{BG};}}
@media only screen and (max-width:600px){{.outer{{width:100%!important;}}}}
</style>
</head>
<body style="margin:0;padding:0;background:{BG};">
<table class="outer" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{BG};max-width:600px;margin:0 auto;">
<tr><td style="padding:20px 14px 40px;">

  <div style="font-size:19px;font-weight:700;color:{ACC};margin-bottom:3px;">
    &#128200; \u52a0\u5bc6\u8d27\u5e01\u6bcf\u65e5\u4fe1\u53f7\u62a5\u544a
  </div>
  <div style="font-size:12px;color:{T3};margin-bottom:22px;">
    {ad}<br>
    \u6570\u636e\u6765\u6e90\uff1aBybit &middot; CoinGecko &middot; CoinMarketCap &middot; Alternative.me &middot; Yahoo Finance
  </div>

  {s1}
  {mg}
  {sl}

  {s2}
  {cc}

  {s3}
  {sc}

  {rk}
  {ft}

</td></tr>
</table>
</body>
</html>""".format(
        BG=BG, ACC=ACC, T3=TXT3, ad=analysis_date,
        s1=sec1, mg=macro_grid, sl=score_leg,
        s2=sec2, cc=coin_cards,
        s3=sec3, sc=strat_cards,
        rk=risk, ft=footer)

    return html



# ─── 9. 主程序 ──────────────────────────────────────────────────────────────

def main():
    tz_beijing = timezone(timedelta(hours=8))
    now = datetime.now(tz_beijing)
    analysis_date = now.strftime("%Y\u5e74%m\u6708%d\u65e5 %H:%M (\u5317\u4eac\u65f6\u95f4)")

    print(f"\n=== Crypto Signal Machine ({now.strftime('%Y-%m-%d %H:%M')}) ===")

    print("  [1/7] Fetching prices...")
    prices = fetch_prices()
    print(f"        → {prices}")

    print("  [2/7] Fetching funding rates...")
    funding_rates = fetch_funding_rates()

    print("  [3/7] Fetching open interest...")
    oi_data = fetch_open_interest()

    print("  [4/7] Fetching ETF flow...")
    etf_flow_m = fetch_etf_flow()
    print(f"        → ETF net flow: {etf_flow_m:+.1f}M USD")

    print("  [5/7] Fetching Fear & Greed...")
    fg_score, fg_label = fetch_fear_greed()
    print(f"        → F&G: {fg_score} ({fg_label})")

    print("  [6/7] Fetching BTC dominance + Nasdaq EMA...")
    btc_dom = fetch_btc_dominance()
    nasdaq_last, nasdaq_ema20 = fetch_nasdaq_ema()
    print(f"        → BTC Dom: {btc_dom}% | Nasdaq: {nasdaq_last} / EMA20 {nasdaq_ema20}")

    print("  [7/7] Computing EMAs for each coin...")
    coin_emas = {}
    for coin in COINS:
        closes = fetch_ohlc(coin, limit=90)
        e20 = ema(closes, 20) if len(closes) >= 20 else None
        e50 = ema(closes, 50) if len(closes) >= 50 else None
        coin_emas[coin] = (closes, e20, e50)
        e20s = f"{e20:.2f}" if e20 else "N/A"
        e50s = f"{e50:.2f}" if e50 else "N/A"
        print(f"        {coin}: EMA20={e20s}  EMA50={e50s}")

    # ── Build HTML ─────────────────────────────────────────────────────────
    html = build_html(
        prices, funding_rates, oi_data, etf_flow_m,
        fg_score, fg_label, btc_dom,
        nasdaq_last, nasdaq_ema20,
        coin_emas, analysis_date,
    )

    # ── Save HTML report ───────────────────────────────────────────────────
    report_name = f"report_{now.strftime('%Y-%m-%d')}.html"
    report_path = os.path.join(REPORT_DIR, report_name)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  [OK] HTML report → {report_path}")

    # ── Write JSON dispatch file ─────────────────────────────────────────────
    output = {
        "status": "success",
        "generated_at": now.isoformat(),
        "analysis_date": analysis_date,
        "report_file": report_path,
        "report_name": report_name,
        "data_summary": {
            "prices": prices,
            "funding_rates": funding_rates,
            "oi_data": {c: {"current_oi": v[0], "oi_7d_pct": v[1]}
                         for c, v in oi_data.items()},
            "etf_flow_m": etf_flow_m,
            "fear_greed": {"score": fg_score, "label": fg_label},
            "btc_dominance": btc_dom,
            "nasdaq": {"last": nasdaq_last, "ema20": nasdaq_ema20},
            "coin_emas": {c: {"ema20": e[1], "ema50": e[2]}
                          for c, e in coin_emas.items()},
        }
    }
    json_path = os.path.join(REPORT_DIR, "report_output.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  [OK] JSON output → {json_path}")
    return json_path


if __name__ == "__main__":
    main()
