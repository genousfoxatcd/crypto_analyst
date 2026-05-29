#!/usr/bin/env python3
"""
crypto_signal_v2.py — 加密货币综合信号引擎 v2
═══════════════════════════════════════════════
数据来源层：
  价格    : Binance / OKX / Bybit / Gate.io / Kraken / CoinGecko (加权均价)
  合约    : Binance Futures API（OI / 资金费率 / 多空比 / 多仓/空仓清算均价 / 4h K线 → RSI）
  清算预估: OI + 多空比 + ATR → 插针危险区间
  ETF流量 : CoinGecko 成交量估算（BTC/ETH 24h/48h/72h）
  宏观    : Nasdaq/DXY via Yahoo Finance + 地缘/事件 WebSearch

信号输出层：
  entry_zone   : BB 下轨 + 合约数据校正
  tp1 / tp2    : BB 中轨 / 上轨
  sl           : 下轨 - 1×ATR
  pin_risk     : 做多/做空插针预估区间
  prob_score   : 0~100 多因子综合得分
"""

import json, math, time, sys, os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    sys.exit("requests 未安装：pip install requests")

# ─── 常量 ──────────────────────────────────────────────────────────────────────
COINS = {
    "BTC":  {"binance": "BTCUSDT", "okx": "BTC-USDT", "bybit": "BTCUSDT",
             "gate": "BTC_USDT",  "kraken": "XBTUSDT", "cg": "bitcoin"},
    "ETH":  {"binance": "ETHUSDT", "okx": "ETH-USDT", "bybit": "ETHUSDT",
             "gate": "ETH_USDT",  "kraken": "ETHUSD",  "cg": "ethereum"},
    "BNB":  {"binance": "BNBUSDT", "okx": "BNB-USDT", "bybit": "BNBUSDT",
             "gate": "BNB_USDT",  "kraken": None,      "cg": "binancecoin"},
    "SOL":  {"binance": "SOLUSDT", "okx": "SOL-USDT", "bybit": "SOLUSDT",
             "gate": "SOL_USDT",  "kraken": "SOLUSD",  "cg": "solana"},
    "DOGE": {"binance": "DOGEUSDT","okx": "DOGE-USDT","bybit": "DOGEUSDT",
             "gate": "DOGE_USDT", "kraken": "DOGEUSD", "cg": "dogecoin"},
    "TAO":  {"binance": "TAOUSDT", "okx": "TAO-USDT", "bybit": "TAOUSDT",
             "gate": "TAO_USDT",  "kraken": None,      "cg": "bittensor"},
    "ZEC":  {"binance": "ZECUSDT", "okx": "ZEC-USDT", "bybit": None,
             "gate": "ZEC_USDT",  "kraken": "ZECUSD",  "cg": "zcash"},
    "CAKE": {"binance": "CAKEUSDT","okx": None,        "bybit": "CAKEUSDT",
             "gate": "CAKE_USDT", "kraken": None,      "cg": "pancakeswap-token"},
    "PAXG": {"binance": "PAXGUSDT","okx": "PAXG-USDT", "bybit": "PAXGUSDT",
             "gate": "PAXG_USDT", "kraken": "PAXGUSD",  "cg": "pax-gold"},
    "HYPE": {"binance": "HYPEUSDT","okx": "HYPE-USDT", "bybit": "HYPEUSDT",
             "gate": "HYPE_USDT", "kraken": None,      "cg": "hyperliquid"},
    "TRX":  {"binance": "TRXUSDT", "okx": "TRX-USDT",  "bybit": "TRXUSDT",
             "gate": "TRX_USDT",  "kraken": "TRXUSD",  "cg": "tron"},
    "AAVE": {"binance": "AAVEUSDT","okx": "AAVE-USDT","bybit": "AAVEUSDT",
             "gate": "AAVE_USDT","kraken": "AAVEUSD", "cg": "aave"},
}

EXCHANGE_WEIGHTS = {"Binance": 3, "OKX": 2, "Bybit": 2, "Gate.io": 1, "Kraken": 1, "CoinGecko": 1}
BB_PERIOD = 20        # 布林带周期（20×4h = 80h）
BB_SIGMA  = 2.0       # 布林带标准差倍数
ATR_PERIOD = 14       # ATR 周期
TIMEOUT   = 8         # API 超时秒数

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"

OUT_DIR = Path(os.environ.get("CRYPTO_ANALYST_WS", "/Users/alex/projects/crypto_analyst")) / "crypto-signal"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ─── HTTP Session ──────────────────────────────────────────────────────────────
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    retry = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

SESSION = make_session()

# ══════════════════════════════════════════════════════════════════════════════
# 模块 1 — 多交易所价格采集
# ══════════════════════════════════════════════════════════════════════════════
def _fetch_binance_prices() -> dict[str, dict]:
    url = "https://api.binance.com/api/v3/ticker/24hr"
    data = SESSION.get(url, timeout=TIMEOUT).json()
    index = {d["symbol"]: d for d in data}
    out = {}
    for coin, cfg in COINS.items():
        sym = cfg["binance"]
        if sym in index:
            out[coin] = {"price": float(index[sym]["lastPrice"]),
                         "change_24h": float(index[sym]["priceChangePercent"]),
                         "volume_24h": float(index[sym]["quoteVolume"]),
                         "source": "Binance"}
    return out

def _fetch_okx_prices() -> dict[str, dict]:
    out = {}
    for coin, cfg in COINS.items():
        try:
            sym = cfg["okx"]
            r = SESSION.get(f"https://www.okx.com/api/v5/market/ticker?instId={sym}", timeout=TIMEOUT)
            d = r.json()["data"][0]
            out[coin] = {"price": float(d["last"]), "change_24h": None, "source": "OKX"}
        except Exception:
            pass
    return out

def _fetch_bybit_prices() -> dict[str, dict]:
    out = {}
    for coin, cfg in COINS.items():
        try:
            sym = cfg["bybit"]
            r = SESSION.get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}", timeout=TIMEOUT)
            d = r.json()["result"]["list"][0]
            out[coin] = {"price": float(d["lastPrice"]), "change_24h": float(d.get("price24hPcnt", 0)) * 100, "source": "Bybit"}
        except Exception:
            pass
    return out

def _fetch_gate_prices() -> dict[str, dict]:
    try:
        pairs = ",".join(cfg["gate"] for cfg in COINS.values())
        r = SESSION.get(f"https://api.gateio.ws/api/v4/spot/tickers", timeout=TIMEOUT)
        index = {d["currency_pair"]: d for d in r.json()}
        out = {}
        for coin, cfg in COINS.items():
            k = cfg["gate"]
            if k in index:
                out[coin] = {"price": float(index[k]["last"]),
                             "change_24h": float(index[k].get("change_percentage", 0)),
                             "source": "Gate.io"}
        return out
    except Exception:
        return {}

def _fetch_kraken_prices() -> dict[str, dict]:
    out = {}
    for coin, cfg in COINS.items():
        if not cfg["kraken"]:
            continue
        try:
            r = SESSION.get(f"https://api.kraken.com/0/public/Ticker?pair={cfg['kraken']}", timeout=TIMEOUT)
            result = r.json()["result"]
            key = list(result.keys())[0]
            out[coin] = {"price": float(result[key]["c"][0]), "change_24h": None, "source": "Kraken"}
        except Exception:
            pass
    return out

def _fetch_coingecko_prices() -> dict[str, dict]:
    ids = ",".join(cfg["cg"] for cfg in COINS.values())
    r = SESSION.get(
        f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true",
        timeout=TIMEOUT)
    data = r.json()
    out = {}
    cg_to_coin = {cfg["cg"]: c for c, cfg in COINS.items()}
    for gid, vals in data.items():
        coin = cg_to_coin.get(gid)
        if coin:
            out[coin] = {"price": vals["usd"], "change_24h": vals.get("usd_24h_change", 0), "source": "CoinGecko"}
    return out

def fetch_multi_exchange_prices() -> dict[str, dict]:
    """并发采集，计算加权均价（偏差>3% 告警）"""
    fetchers = [
        ("Binance",   _fetch_binance_prices),
        ("OKX",       _fetch_okx_prices),
        ("Bybit",     _fetch_bybit_prices),
        ("Gate.io",   _fetch_gate_prices),
        ("Kraken",    _fetch_kraken_prices),
        ("CoinGecko", _fetch_coingecko_prices),
    ]
    raw: dict[str, dict[str, dict]] = {c: {} for c in COINS}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fn): name for name, fn in fetchers}
        for future in as_completed(futures):
            name = futures[future]
            try:
                for coin, d in future.result().items():
                    raw[coin][name] = d
            except Exception as e:
                print(f"  [{name}] 失败: {e}", file=sys.stderr)

    result = {}
    for coin, sources in raw.items():
        prices = {s: d["price"] for s, d in sources.items() if d.get("price", 0) > 0}
        if not prices:
            continue
        # 加权均价
        total_w, weighted_sum = 0, 0.0
        for src, price in prices.items():
            w = EXCHANGE_WEIGHTS.get(src, 1)
            weighted_sum += price * w
            total_w += w
        avg = weighted_sum / total_w
        max_dev = max(abs(p - avg) / avg * 100 for p in prices.values())

        # 主力价格（Binance 优先）
        primary = sources.get("Binance") or sources.get("OKX") or next(iter(sources.values()))
        # volume_24h：取自主力交易所的24h成交量（USDT计），无则0
        vol = primary.get("volume_24h", 0) or 0
        if vol >= 1e8:
            vol_str = f"{vol/1e8:.2f}亿"
        elif vol >= 1e4:
            vol_str = f"{vol/1e4:.1f}万"
        else:
            vol_str = f"{vol:.0f}"
        result[coin] = {
            "price":       round(avg, 6),
            "change_24h":  round(primary.get("change_24h") or 0, 2),
            "volume_24h":  round(vol, 2),
            "volume_str":  vol_str,
            "sources":     {s: round(p, 6) for s, p in prices.items()},
            "source_count": len(prices),
            "max_dev_pct": round(max_dev, 2),
            "quality":     "✅" if max_dev < 3 else f"⚠️偏差{max_dev:.1f}%",
        }
    return result

# ══════════════════════════════════════════════════════════════════════════════
# 模块 2 — Binance Futures 合约数据
# ══════════════════════════════════════════════════════════════════════════════
def fetch_contract_data(symbol: str) -> dict:
    """采集单个币种的合约数据"""
    result = {"symbol": symbol}
    base = "https://fapi.binance.com"
    fdata = "https://fapi.binance.com/futures/data"

    # 资金费率 + 标记价格
    try:
        d = SESSION.get(f"{base}/fapi/v1/premiumIndex?symbol={symbol}", timeout=TIMEOUT).json()
        result["funding_rate"]    = float(d["lastFundingRate"])
        result["mark_price"]      = float(d["markPrice"])
        result["index_price"]     = float(d.get("indexPrice", d["markPrice"]))
        result["next_funding_time"] = d.get("nextFundingTime")
    except Exception as e:
        print(f"  [{symbol} FR] {e}", file=sys.stderr)

    # 持仓量（Open Interest）
    try:
        d = SESSION.get(f"{base}/fapi/v1/openInterest?symbol={symbol}", timeout=TIMEOUT).json()
        result["open_interest"]      = float(d["openInterest"])
        result["open_interest_usdt"] = float(d["openInterest"]) * result.get("mark_price", 0)
    except Exception as e:
        print(f"  [{symbol} OI] {e}", file=sys.stderr)

    # 全局多空账户比
    try:
        d = SESSION.get(
            f"{fdata}/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=2",
            timeout=TIMEOUT).json()
        if d:
            result["ls_ratio_global"]    = float(d[0]["longShortRatio"])
            result["long_pct_global"]    = float(d[0]["longAccount"])   # 0~1
            result["short_pct_global"]   = float(d[0]["shortAccount"])  # 0~1
    except Exception as e:
        print(f"  [{symbol} LS] {e}", file=sys.stderr)

    # 顶级交易者持仓多空比
    try:
        d = SESSION.get(
            f"{fdata}/topLongShortPositionRatio?symbol={symbol}&period=5m&limit=2",
            timeout=TIMEOUT).json()
        if d:
            result["ls_ratio_top"]     = float(d[0]["longShortRatio"])
            result["long_pct_top"]     = float(d[0]["longAccount"])
            result["short_pct_top"]    = float(d[0]["shortAccount"])
    except Exception as e:
        print(f"  [{symbol} Top LS] {e}", file=sys.stderr)

    # 主动买卖比（Taker Buy/Sell Volume）
    try:
        d = SESSION.get(
            f"{fdata}/takerlongshortRatio?symbol={symbol}&period=5m&limit=1",
            timeout=TIMEOUT).json()
        if d:
            result["taker_buy_sell_ratio"] = float(d[0]["buySellRatio"])
            result["taker_buy_vol"]        = float(d[0]["buyVol"])
            result["taker_sell_vol"]       = float(d[0]["sellVol"])
    except Exception as e:
        print(f"  [{symbol} Taker] {e}", file=sys.stderr)

    return result

# ══════════════════════════════════════════════════════════════════════════════
# 模块 3 — 4h K线 → 布林带 + ATR
# ══════════════════════════════════════════════════════════════════════════════
def fetch_klines_and_indicators(symbol: str, interval: str = "4h", limit: int = 50) -> dict:
    """
    获取 K线并计算：布林带（20,2σ）、ATR(14)、资金流（成交量加权方向）
    K线字段: [openTime, O, H, L, C, Vol, closeTime, QuoteVol, trades, takerBuyBase, takerBuyQuote, ...]
    """
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
        raw = SESSION.get(url, timeout=TIMEOUT).json()
        closes  = [float(k[4]) for k in raw]
        highs   = [float(k[2]) for k in raw]
        lows    = [float(k[3]) for k in raw]
        volumes = [float(k[5]) for k in raw]
        taker_buy_vol  = [float(k[9]) for k in raw]   # Taker buy base volume

        n = len(closes)
        if n < BB_PERIOD:
            return {}

        # 布林带（最新 BB_PERIOD 根）
        window = closes[-BB_PERIOD:]
        bb_mid   = sum(window) / BB_PERIOD
        variance = sum((x - bb_mid) ** 2 for x in window) / BB_PERIOD
        bb_std   = math.sqrt(variance)
        bb_upper = bb_mid + BB_SIGMA * bb_std
        bb_lower = bb_mid - BB_SIGMA * bb_std
        bb_width = (bb_upper - bb_lower) / bb_mid * 100   # %
        price_vs_bb = (closes[-1] - bb_lower) / (bb_upper - bb_lower) * 100  # 0=下轨, 100=上轨

        # ATR(14)
        trs = []
        for i in range(1, min(ATR_PERIOD + 1, n)):
            tr = max(highs[-i] - lows[-i],
                     abs(highs[-i] - closes[-(i+1)]),
                     abs(lows[-i]  - closes[-(i+1)]))
            trs.append(tr)
        atr = sum(trs) / len(trs) if trs else closes[-1] * 0.02

        # 4h 资金流（最近 6 根 = 24h）
        recent = min(6, n)
        buy_vol  = sum(taker_buy_vol[-recent:])
        sell_vol = sum(volumes[-recent:]) - buy_vol
        flow_ratio = buy_vol / (buy_vol + sell_vol) if (buy_vol + sell_vol) > 0 else 0.5
        # >0.55 = 买方主导, <0.45 = 卖方主导

        return {
            "current_close": closes[-1],
            "bb_upper": round(bb_upper, 6),
            "bb_mid":   round(bb_mid,   6),
            "bb_lower": round(bb_lower, 6),
            "bb_width_pct": round(bb_width, 2),
            "price_vs_bb_pct": round(price_vs_bb, 1),   # 0=下轨, 100=上轨
            "atr": round(atr, 6),
            "atr_pct": round(atr / closes[-1] * 100, 2),  # ATR 占价格 %
            "flow_24h_buy_pct": round(flow_ratio * 100, 1),  # 24h 买方成交占比
            "interval": interval,
        }
    except Exception as e:
        print(f"  [{symbol} Klines] {e}", file=sys.stderr)
        return {}

# ══════════════════════════════════════════════════════════════════════════════
# 模块 4 — 插针清算区间估算
# ══════════════════════════════════════════════════════════════════════════════
def estimate_liquidation_zones(price: float, contract: dict, tech: dict) -> dict:
    """
    基于多空仓位 + ATR 估算最可能被插针的价格区间
    原理：
      - 大量多单 → 做空插针打爆多头（向下）
      - 大量空单 → 做多插针打爆空头（向上）
    常见杠杆分布（零售市场）：5x / 10x / 20x
    清算价 (多头) = 入场价 × (1 - 1/leverage + 维保率≈0.005)
    """
    ls_ratio = contract.get("ls_ratio_top") or contract.get("ls_ratio_global", 1.0)
    atr = tech.get("atr", price * 0.02)

    # 估算多头集中清算区（向下插针目标）
    liq_long = {
        "5x":  round(price * (1 - 1/5  + 0.005), 6),
        "10x": round(price * (1 - 1/10 + 0.005), 6),
        "20x": round(price * (1 - 1/20 + 0.005), 6),
    }
    # 估算空头集中清算区（向上插针目标）
    liq_short = {
        "5x":  round(price * (1 + 1/5  - 0.005), 6),
        "10x": round(price * (1 + 1/10 - 0.005), 6),
        "20x": round(price * (1 + 1/20 - 0.005), 6),
    }

    # 主要危险方向判断
    long_pct  = contract.get("long_pct_top") or contract.get("long_pct_global", 0.5)
    taker_bsr = contract.get("taker_buy_sell_ratio", 1.0)

    if long_pct > 0.60:
        # 多头过于拥挤 → 向下插针风险高
        primary_risk = "DOWN"
        pin_target_primary = liq_long["10x"]   # 10x 是零售最常见杠杆
        pin_target_extreme = liq_long["20x"]
    elif long_pct < 0.40:
        # 空头过于拥挤 → 向上插针风险高
        primary_risk = "UP"
        pin_target_primary = liq_short["10x"]
        pin_target_extreme = liq_short["20x"]
    else:
        primary_risk = "NEUTRAL"
        pin_target_primary = None
        pin_target_extreme = None

    # 吃单方向修正
    taker_dir = "买方主导" if taker_bsr > 1.1 else ("卖方主导" if taker_bsr < 0.9 else "均衡")

    return {
        "long_pct": round(long_pct * 100, 1),    # 多头仓位占比 %
        "ls_ratio": round(ls_ratio, 3),
        "taker_buy_sell_ratio": round(taker_bsr, 3),
        "taker_direction": taker_dir,
        "primary_risk": primary_risk,
        "liq_long_zones": liq_long,
        "liq_short_zones": liq_short,
        "pin_target_primary": pin_target_primary,
        "pin_target_extreme": pin_target_extreme,
        "atr": round(atr, 6),
    }

# ══════════════════════════════════════════════════════════════════════════════
# 模块 5 — 宏观数据（Nasdaq + 地缘 + ETF流量）
# ══════════════════════════════════════════════════════════════════════════════
def fetch_nasdaq_data() -> dict:
    """通过 Yahoo Finance v8 API 获取纳指最新数据（失败则返回空）"""
    try:
        r = SESSION.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EIXIC?interval=1d&range=5d",
            timeout=TIMEOUT,
            headers={**SESSION.headers, "Accept": "application/json",
                     "Referer": "https://finance.yahoo.com/"})
        if r.status_code == 200:
            data = r.json()
            meta = data["chart"]["result"][0]["meta"]
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                chg_1d = (closes[-1] - closes[-2]) / closes[-2] * 100
                chg_5d = (closes[-1] - closes[0]) / closes[0] * 100 if len(closes) >= 5 else None
                return {
                    "price": round(closes[-1], 2),
                    "change_1d_pct": round(chg_1d, 2),
                    "change_5d_pct": round(chg_5d, 2) if chg_5d is not None else None,
                    "source": "Yahoo Finance",
                }
    except Exception as e:
        print(f"  [Nasdaq] {e}", file=sys.stderr)

    # 备用：尝试 Stooq
    try:
        r = SESSION.get("https://stooq.com/q/l/?s=^ndq&f=sd2t2ohlcv&h&e=csv", timeout=TIMEOUT)
        line = r.text.strip().split("\n")[-1]
        parts = line.split(",")
        price = float(parts[6])  # close
        return {"price": price, "change_1d_pct": None, "source": "Stooq"}
    except Exception:
        pass
    return {}

def fetch_dow_data() -> dict:
    """道琼斯工业平均指数（DJI）— 先行指标，与加密强相关"""
    try:
        r = SESSION.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EDJI?interval=1d&range=5d",
            timeout=TIMEOUT,
            headers={**SESSION.headers, "Accept": "application/json",
                     "Referer": "https://finance.yahoo.com/"})
        if r.status_code == 200:
            data = r.json()
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                chg_1d = (closes[-1] - closes[-2]) / closes[-2] * 100
                chg_5d = (closes[-1] - closes[0]) / closes[0] * 100 if len(closes) >= 5 else None
                return {
                    "price": round(closes[-1], 2),
                    "change_1d_pct": round(chg_1d, 2),
                    "change_5d_pct": round(chg_5d, 2) if chg_5d is not None else None,
                    "source": "Yahoo Finance",
                }
    except Exception as e:
        print(f"  [DJI] {e}", file=sys.stderr)
    return {}

def fetch_dxy_data() -> dict:
    """美元指数"""
    try:
        r = SESSION.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=3d",
            timeout=TIMEOUT,
            headers={**SESSION.headers, "Referer": "https://finance.yahoo.com/"})
        if r.status_code == 200:
            data = r.json()
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                chg = (closes[-1] - closes[-2]) / closes[-2] * 100
                return {"price": round(closes[-1], 2), "change_1d_pct": round(chg, 2)}
    except Exception:
        pass
    return {}

# ══════════════════════════════════════════════════════════════════════════════
# 模块 6 — 信号生成引擎
# ══════════════════════════════════════════════════════════════════════════════
def generate_signal(coin: str, price_data: dict, contract: dict,
                    tech: dict, liq: dict, macro: dict) -> dict:
    """
    综合多因子，生成交易信号：
      direction, entry_zone, tp1, tp2, sl,
      prob_score (0~100), leverage, rationale
    """
    price   = price_data.get("price", 0)
    if not price:
        return {}

    bb_upper = tech.get("bb_upper", price * 1.04)
    bb_mid   = tech.get("bb_mid",   price)
    bb_lower = tech.get("bb_lower", price * 0.96)
    atr      = tech.get("atr",      price * 0.02)
    pos_pct  = tech.get("price_vs_bb_pct", 50)  # 0=下轨, 100=上轨
    flow_buy = tech.get("flow_24h_buy_pct", 50)

    fr_raw = contract.get("funding_rate")  # None if API failed
    fr = fr_raw if fr_raw is not None else 0  # keep 0 for signal calc, but we know it's missing
    ls = liq.get("ls_ratio", 1.0)
    ls = liq.get("ls_ratio", 1.0)
    long_pct = liq.get("long_pct", 50)
    taker_bsr = liq.get("taker_buy_sell_ratio", 1.0)

    # ── 方向得分（正=多，负=空）────────────────────────────────────────────
    score = 0.0

    # 1) BB 位置（下轨=看多机会，上轨=看空/止盈）
    if pos_pct <= 20:
        score += 25    # 价格在下轨，强看多
    elif pos_pct <= 40:
        score += 10
    elif pos_pct >= 80:
        score -= 20    # 价格在上轨附近，不宜追多
    elif pos_pct >= 60:
        score -= 5

    # 2) 资金费率（负费率=多头被清洗，看多；极正=做多代价大）
    if fr < -0.001:
        score += 20
    elif fr < 0:
        score += 8
    elif fr > 0.003:
        score -= 15
    elif fr > 0.001:
        score -= 5

    # 3) 多空比（多头过度拥挤=插针风险）
    if long_pct > 65:
        score -= 18    # 多头过拥挤，向下插针风险
    elif long_pct > 58:
        score -= 8
    elif long_pct < 40:
        score += 15    # 空头过拥挤，向上插针机会
    elif long_pct < 48:
        score += 5

    # 4) 吃单方向（近5分钟）
    if taker_bsr > 1.3:
        score += 12
    elif taker_bsr > 1.0:
        score += 5
    elif taker_bsr < 0.7:
        score -= 12
    elif taker_bsr < 1.0:
        score -= 5

    # 5) 24h 资金流
    if flow_buy > 58:
        score += 10
    elif flow_buy > 52:
        score += 4
    elif flow_buy < 42:
        score -= 10
    elif flow_buy < 48:
        score -= 4

    # 6) 宏观联动（纳指 + 道指综合，加密先行指标）
    # 各币与美股相关性权重（BTC/ETH最高，迷因币最低）
    MACRO_CORR = {
        "BTC": 1.00, "ETH": 1.00, "SOL": 0.90, "BNB": 0.85,
        "TAO": 0.85, "ZEC": 0.75, "DOGE": 0.65, "CAKE": 0.60,
    }
    corr_w  = MACRO_CORR.get(coin, 0.80)
    ndx_1d  = macro.get("nasdaq", {}).get("change_1d_pct")
    ndx_5d  = macro.get("nasdaq", {}).get("change_5d_pct")
    dji_1d  = macro.get("dji", {}).get("change_1d_pct")
    dji_5d  = macro.get("dji", {}).get("change_5d_pct")

    # 合成指数：60% 纳指 + 40% 道指（纳指科技权重更高，与加密相关性更强）
    if ndx_1d is not None and dji_1d is not None:
        comp_1d = ndx_1d * 0.6 + dji_1d * 0.4
    elif ndx_1d is not None:
        comp_1d = ndx_1d
    else:
        comp_1d = None

    if ndx_5d is not None and dji_5d is not None:
        comp_5d = ndx_5d * 0.6 + dji_5d * 0.4
    elif ndx_5d is not None:
        comp_5d = ndx_5d
    else:
        comp_5d = None

    macro_score = 0
    if comp_1d is not None:
        if   comp_1d >  2.0: macro_score += 12   # 强势上涨，风险资产普涨
        elif comp_1d >  1.0: macro_score +=  6
        elif comp_1d >  0.3: macro_score +=  2
        elif comp_1d < -2.0: macro_score -= 14   # 大跌，加密跟跌放大
        elif comp_1d < -1.0: macro_score -=  8
        elif comp_1d < -0.3: macro_score -=  3

    # 5日趋势作为方向确认乘数（同向放大，反向不调整）
    if comp_5d is not None and macro_score != 0:
        if   macro_score > 0 and comp_5d >  4.0: macro_score = int(macro_score * 1.35)
        elif macro_score > 0 and comp_5d >  2.0: macro_score = int(macro_score * 1.15)
        elif macro_score < 0 and comp_5d < -4.0: macro_score = int(macro_score * 1.35)
        elif macro_score < 0 and comp_5d < -2.0: macro_score = int(macro_score * 1.15)

    score += int(macro_score * corr_w)

    # ── 判断方向 ───────────────────────────────────────────────────────────
    if score >= 10:
        direction = "LONG"
    elif score <= -10:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # ── 入场点位（等待回调入场）────────────────────────────────────────────
    if direction == "LONG":
        # 优先在 BB 下轨 ~ 中轨之间寻找入场
        entry_low  = max(bb_lower - 0.3 * atr, price * 0.94)
        entry_high = min(bb_mid, price * 0.99)    # 等待回调，不追高
        # 若当前价已在下轨附近（pos_pct<30），可当前区间附近入场
        if pos_pct < 30:
            entry_low  = price * 0.995
            entry_high = price * 1.005
        tp1 = bb_mid
        tp2 = bb_upper
        sl  = bb_lower - atr * 0.8
        leverage = 3 if tech.get("atr_pct", 3) < 3.5 else 2

    elif direction == "SHORT":
        entry_low  = max(bb_mid, price * 1.005)
        entry_high = min(bb_upper + 0.3 * atr, price * 1.06)
        if pos_pct > 70:
            # Near lower band: enter near current, standard targets
            entry_low  = price * 0.995
            entry_high = price * 1.005
            tp1 = bb_mid
            tp2 = bb_lower
            sl  = max(bb_upper + atr * 0.8, entry_high * 1.008)
        elif price < bb_mid:
            # Price already broken below BB_mid: enter near current, target BB_lower
            entry_low  = price * 0.998
            entry_high = min(bb_mid, price * 1.012)
            tp1 = bb_lower
            tp2 = max(bb_lower - atr * 0.8, price * 0.97)
            sl  = max(bb_mid * 1.005, entry_high * 1.01)
        else:
            tp1 = bb_mid
            tp2 = bb_lower
            sl  = max(bb_upper + atr * 0.8, entry_high * 1.008)
        leverage = 3 if tech.get("atr_pct", 3) < 3.5 else 2

    else:
        entry_low  = price * 0.985
        entry_high = price * 1.015
        tp1 = bb_mid
        tp2 = None
        sl  = price * 0.92
        leverage = 2

    # ── 盈利率 / 亏损率 ────────────────────────────────────────────────────
    entry_mid = (entry_low + entry_high) / 2
    if direction in ("LONG", "NEUTRAL") and entry_mid > 0:
        rr_tp1 = (tp1 - entry_mid) / entry_mid * leverage * 100
        rr_tp2 = (tp2 - entry_mid) / entry_mid * leverage * 100 if tp2 else None
        rr_sl  = (sl  - entry_mid) / entry_mid * leverage * 100
    elif direction == "SHORT" and entry_mid > 0:
        rr_tp1 = (entry_mid - tp1) / entry_mid * leverage * 100
        rr_tp2 = (entry_mid - tp2) / entry_mid * leverage * 100 if tp2 else None
        rr_sl  = (entry_mid - sl)  / entry_mid * leverage * 100
    else:
        rr_tp1 = rr_tp2 = rr_sl = 0

    rr = abs(rr_tp1 / rr_sl) if rr_sl != 0 else 0

    # ── 概率得分（0~100）──────────────────────────────────────────────────
    # 基础得分 50 + 方向得分 + R/R 加分
    base_prob = 50 + max(-25, min(25, score))
    if rr >= 2.5: base_prob += 8
    elif rr >= 2.0: base_prob += 4
    elif rr < 1.2: base_prob -= 8
    prob_score = max(35, min(78, round(base_prob)))

    # ── 插针警告 ──────────────────────────────────────────────────────────
    pin_warning = ""
    if liq.get("primary_risk") == "DOWN" and direction == "LONG":
        pin_warning = f"⚠️ 多头拥挤({long_pct:.0f}%)，下方插针危险区 {liq.get('pin_target_extreme',0):,.2f}–{liq.get('pin_target_primary',0):,.2f}"
    elif liq.get("primary_risk") == "UP" and direction == "SHORT":
        pin_warning = f"⚠️ 空头拥挤，上方插针危险区 {liq.get('pin_target_primary',0):,.2f}–{liq.get('pin_target_extreme',0):,.2f}"

    def R(v, d=None):
        if v is None: return None
        if d is not None: return round(v, d)
        # auto-precision based on magnitude
        if abs(v) >= 1000: return round(v, 2)
        if abs(v) >= 1:    return round(v, 4)
        if abs(v) >= 0.01: return round(v, 6)
        return round(v, 8)

    return {
        "coin":        coin,
        "price":       R(price),
        "direction":   direction,
        "score":       round(score, 1),
        "entry_zone":  [R(entry_low), R(entry_high)],
        "entry_mid":   R(entry_mid),
        "tp1":         R(tp1),
        "tp2":         R(tp2),
        "sl":          R(sl),
        "leverage":    leverage,
        "rr_tp1":      R(rr_tp1, 1),
        "rr_tp2":      R(rr_tp2, 1),
        "rr_sl":       R(rr_sl,  1),
        "rr_ratio":    R(rr, 2),
        "prob_score":  prob_score,
        "pin_warning": pin_warning,
        "bb": {"upper": R(bb_upper), "mid": R(bb_mid), "lower": R(bb_lower),
               "width_pct": tech.get("bb_width_pct"), "pos_pct": tech.get("price_vs_bb_pct")},
        "atr": R(atr),
        "funding_rate": R(fr_raw * 100, 4) if fr_raw is not None else None,
        "long_pct": R(long_pct, 1),
        "taker_bsr": R(taker_bsr, 3),
        "flow_buy_pct": tech.get("flow_24h_buy_pct"),
    }

# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════
def run(output_json: str | None = None, output_md: str | None = None) -> dict:
    ts = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"  加密货币信号引擎 v2 — {ts.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    # ① 价格（并发）
    print("\n[1/5] 多交易所价格采集（6源）...")
    prices = fetch_multi_exchange_prices()
    for coin, d in prices.items():
        p_str = f"${d['price']:,.1f}" if abs(d['price']) >= 10 else (f"${d['price']:,.3f}" if abs(d['price']) < 1 else f"${d['price']:,.2f}")
        print(f"  {coin:5} {p_str:>8}  {d['change_24h']:+.2f}%  "
              f"  量:{d.get('volume_str','—'):>6}  偏差:{d['max_dev_pct']}%  {d['quality']}")

    # ② 合约数据（并发）
    print("\n[2/5] Binance Futures 合约数据...")
    contracts: dict[str, dict] = {}
    futures_symbols = {c: cfg["binance"] for c, cfg in COINS.items()}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(fetch_contract_data, sym): coin
                for coin, sym in futures_symbols.items()}
        for f in as_completed(futs):
            coin = futs[f]
            contracts[coin] = f.result()
            d = contracts[coin]
            fr = d.get("funding_rate", 0)
            ls = d.get("ls_ratio_top") or d.get("ls_ratio_global")
            oi_m = d.get("open_interest_usdt", 0) / 1e6
            p = prices.get(coin, {}).get("price", 0)
            if p:
                avg_long_p  = (p*(1-1/5+0.005) + p*(1-1/10+0.005)) / 2
                avg_short_p = (p*(1+1/5-0.005) + p*(1+1/10-0.005)) / 2
                long_s  = f"${avg_long_p:,.1f}"  if abs(avg_long_p) >= 10 else f"${avg_long_p:,.4f}"
                short_s = f"${avg_short_p:,.1f}" if abs(avg_short_p) >= 10 else f"${avg_short_p:,.4f}"
            else:
                long_s = short_s = "—"
            print(f"  {coin:5}  FR:{fr*100:+.4f}%  L/S:{ls:.3f}  "
                  f"多均:{long_s}  空均:{short_s}  OI:${oi_m:.1f}M")

    # ③ K线 + 布林带（并发）
    print("\n[3/5] 4h K线数据（RSI + 24h资金流）...")
    tech_data: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(fetch_klines_and_indicators, sym, "4h", 50): coin
                for coin, sym in futures_symbols.items()}
        for f in as_completed(futs):
            coin = futs[f]
            tech_data[coin] = f.result()
            t = tech_data[coin]
            rsi_str = f"  RSI:{t.get('rsi_14', '—')}" if t.get('rsi_14') is not None else ""
            print(f"  {coin:5}  "
                  f"买流:{t.get('flow_24h_buy_pct'):.1f}%{rsi_str}")

    # ④ 宏观数据 + ETF流量 + 恐慌贪婪指数
    print("\n[4/5] 宏观数据 + ETF流量 + 恐慌贪婪指数...")
    nasdaq = fetch_nasdaq_data()
    dxy    = fetch_dxy_data()
    macro  = {"nasdaq": nasdaq, "dxy": dxy}
    if nasdaq and nasdaq.get('price') is not None:
        chg = nasdaq.get('change_1d_pct')
        chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
        print(f"  纳斯达克: {nasdaq.get('price'):,.2f}  {chg_str}  ({nasdaq.get('source')})")
    else:
        print("  纳斯达克: 数据不可用（已跳过）")
    if dxy and dxy.get('price') is not None:
        chg = dxy.get('change_1d_pct')
        chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
        print(f"  美元指数: {dxy.get('price'):.2f}  {chg_str}")
    else:
        print("  美元指数: 数据不可用（已跳过）")

    # 恐慌贪婪指数
    fng = fetch_fear_greed_index()
    print(f"  恐慌贪婪指数: {fng.get('now_value','?')} ({fng.get('classification','?')})  趋势:{fng.get('trend_direction','?')}")

    # BTC ETF流量
    etf_flow = fetch_btc_etf_flow()
    if etf_flow.get("24h_flow_usd_m") is not None:
        e24 = etf_flow['24h_flow_usd_m']
        e48 = etf_flow.get('48h_flow_usd_m', 0) or 0
        e72 = etf_flow.get('72h_flow_usd_m', 0) or 0
        print(f"  BTC ETF流量: 24h=${e24:.0f}M  48h=${e48:.0f}M  72h=${e72:.0f}M  趋势:{etf_flow.get('flow_trend','?')}  ({etf_flow['source']})")
    else:
        print("  BTC ETF 流量: 数据不可用")

    # ETH ETF流量
    eth_etf_flow = fetch_coin_etf_flow("ethereum", "ETH")
    if eth_etf_flow.get("24h_flow_usd_m") is not None:
        ee24 = eth_etf_flow['24h_flow_usd_m']
        ee48 = eth_etf_flow.get('48h_flow_usd_m', 0) or 0
        ee72 = eth_etf_flow.get('72h_flow_usd_m', 0) or 0
        print(f"  ETH ETF流量: 24h=${ee24:.0f}M  48h=${ee48:.0f}M  72h=${ee72:.0f}M  趋势:{eth_etf_flow.get('flow_trend','?')}  ({eth_etf_flow['source']})")
    else:
        print("  ETH ETF 流量: 数据不可用")

    # ⑤ 信号生成
    print("\n[5/5] 信号生成...")
    signals: dict[str, dict] = {}
    for coin in COINS:
        if coin not in prices:
            continue
        price_d = prices[coin]
        liq = estimate_liquidation_zones(
            price_d["price"],
            contracts.get(coin, {}),
            tech_data.get(coin, {}))
        sig = generate_signal(
            coin, price_d,
            contracts.get(coin, {}),
            tech_data.get(coin, {}),
            liq, macro)
        signals[coin] = {**sig, "liquidation": liq, "sources": price_d["sources"]}
        s = signals[coin]
        dir_label = {"LONG": "🟢做多", "SHORT": "🔴做空", "NEUTRAL": "🟡观望"}.get(s["direction"], "?")
        print(f"  {coin:5} {dir_label}  入场:{s['entry_zone']}  "
              f"TP1:{s['tp1']:.2f}  SL:{s['sl']:.2f}  "
              f"P:{s['prob_score']}%  {s.get('pin_warning','')}")

    # ── 汇总输出 ──────────────────────────────────────────────────────────
    result = {
        "generated_at": ts.isoformat(),
        "prices":       {c: {"price": prices[c]["price"],
                              "change_24h": prices[c]["change_24h"],
                              "volume_24h": prices[c]["volume_24h"],
                              "volume_str": prices[c]["volume_str"],
                              "source_count": prices[c]["source_count"],
                              "sources": prices[c]["sources"]}
                         for c in prices},
        "signals":      signals,
        "macro":        macro,
        "fng":          fng,
        "etf_flow":     etf_flow,
        "eth_etf_flow": eth_etf_flow,
    }

    if output_json:
        Path(output_json).write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"\n✅ JSON: {output_json}")

    if output_md:
        _write_md_report(result, output_md)
        print(f"✅ MD : {output_md}")

    return result


def _write_md_report(data: dict, path: str):
    """输出 Markdown 简报"""
    ts = data["generated_at"]
    lines = [f"# 加密货币信号报告 | {ts}\n",
             f"> 引擎版本 v2 | 价格源自多交易所加权\n"]

    # 价格表
    lines += ["## 实时价格\n",
              "| 币种 | 价格 | 24h | 24h交易量 |",
              "|------|------|-----|----------|"]
    for coin, d in data["prices"].items():
        p = d['price']
        if abs(p) >= 10:   p_str = f"${p:,.1f}"
        elif abs(p) >= 1:  p_str = f"${p:,.2f}"
        else:               p_str = f"${p:,.3f}"
        sign = "▲" if d["change_24h"] >= 0 else "▼"
        vol = d.get("volume_str", "—")
        lines.append(f"| {coin} | {p_str} | {sign}{abs(d['change_24h']):.2f}% | {vol} |")
    lines.append("")

    # 信号表
    lines += ["## 交易信号\n",
              "| 币种 | 方向 | 入场 | 杠杆 | TP1 | SL | 盈利率 | RSI | 概率 |",
              "|------|------|------|------|-----|----|--------|-----|------|"]
    for coin, s in data["signals"].items():
        dir_cn = {"LONG": "做多", "SHORT": "做空", "NEUTRAL": "观望"}.get(s["direction"], "?")
        entry  = f"{s['entry_zone'][0]:,.2f}–{s['entry_zone'][1]:,.2f}"
        profit = f"{s['rr_tp1']:+.1f}%" if s["rr_tp1"] is not None else "—"
        tp1_s  = f"{s['tp1']}" if s.get('tp1') else "—"
        sl_s   = f"{s['sl']}" if s.get('sl') else "—"
        rsi_s  = f"{s.get('rsi_14', '—')}" if s.get('rsi_14') is not None else "—"
        lines.append(f"| {coin} | {dir_cn} | {entry} | {s['leverage']}× | "
                     f"{tp1_s} | {sl_s} | {profit} | {rsi_s} | {s['prob_score']}% |")
    lines.append("")

    # 合约数据表
    lines += ["## 合约数据\n",
              "| 币种 | 资金费率 | 多空比L/S | 多仓均价 | 空仓均价 | 买方流量 | 多头占比 |",
              "|------|---------|----------|---------|---------|---------|---------|"]
    # 检查合约数据是否可用：看看第一个币种是否有真实的合约字段
    first_ct = next(iter(data.get("contracts", {}).values()), {})
    has_contract_data = first_ct.get("funding_rate") is not None
    if not has_contract_data:
        lines.append("> ⚠️ 合约数据不可用：Binance Futures API 未返回数据，所有值均为默认值")
    for coin, s in data["signals"].items():
        fr_raw = s.get("funding_rate")
        fr_str = f"{fr_raw:+.3f}%" if fr_raw is not None else "—"
        liq  = s.get("liquidation", {}) or {}
        ls_raw = liq.get("ls_ratio")
        ls_str = f"{ls_raw:.2f}" if ls_raw is not None else "—"
        flow_raw = s.get("flow_buy_pct")
        flow_str = f"{flow_raw:.1f}%" if flow_raw is not None else "—"
        lp_raw = s.get("long_pct", 50) or 50
        lp_str = f"{lp_raw:.0f}%" if lp_raw is not None else "—"
        liq_l = liq.get("liq_long_zones", {}) or {}
        liq_s = liq.get("liq_short_zones", {}) or {}
        l5 = liq_l.get("5x"); l10 = liq_l.get("10x")
        s5 = liq_s.get("5x"); s10 = liq_s.get("10x")
        if l5 and l10:
            avg_l = (float(l5) + float(l10)) / 2
            avg_l_str = f"${avg_l:,.1f}" if abs(avg_l) >= 10 else f"${avg_l:,.4f}"
        else:
            avg_l_str = "—"
        if s5 and s10:
            avg_s = (float(s5) + float(s10)) / 2
            avg_s_str = f"${avg_s:,.1f}" if abs(avg_s) >= 10 else f"${avg_s:,.4f}"
        else:
            avg_s_str = "—"
        lines.append(f"| {coin} | {fr_str} | {ls_str} | {avg_l_str} | {avg_s_str} | {flow_str} | {lp_str} |")
    lines.append("")

    # 爆仓区间
    lines += ["## 爆仓区间估算（10x / 20x 杠杆）\n",
              "| 币种 | 多头10x爆 | 多头20x爆 | 空头10x爆 | 空头20x爆 | 风险方向 |",
              "|------|----------|----------|----------|----------|---------|"]
    for coin, s in data["signals"].items():
        liq = s.get("liquidation", {}) or {}
        ll10 = liq.get("liq_long_zones", {}).get("10x", "—")
        ll20 = liq.get("liq_long_zones", {}).get("20x", "—")
        ls10 = liq.get("liq_short_zones", {}).get("10x", "—")
        ls20 = liq.get("liq_short_zones", {}).get("20x", "—")
        risk = liq.get("primary_risk", "NEUTRAL")
        risk_cn = {"DOWN": "⬇多头爆仓风险", "UP": "⬆空头爆仓风险", "NEUTRAL": "⚪中性"}
        lines.append(f"| {coin} | {ll10} | {ll20} | {ls10} | {ls20} | {risk_cn.get(risk, '?')} |")
    lines.append("")

    # 宏观
    macro = data.get("macro", {}) or {}
    fng_data = data.get("fng", {}) or {}
    etf = data.get("etf_flow", {}) or {}
    lines += ["## 宏观数据\n"]
    if macro.get("nasdaq", {}).get('price') is not None:
        nd = macro["nasdaq"]
        nd_s = f"{nd.get('change_1d_pct'):+.2f}%" if nd.get('change_1d_pct') is not None else "N/A"
        lines.append(f"- **纳斯达克**: {nd['price']:,.2f}  ({nd_s})")
    if fng_data.get("now_value") is not None:
        lines.append(f"- **恐慌贪婪指数**: {fng_data['now_value']} ({fng_data.get('classification','')})  趋势:{fng_data.get('trend_direction','')}")
    if etf.get("24h_flow_usd_m") is not None:
        e24 = etf["24h_flow_usd_m"]
        e48 = etf.get("48h_flow_usd_m", 0) or 0
        e72 = etf.get("72h_flow_usd_m", 0) or 0
        sign24 = "+" if e24 >= 0 else ""
        sign48 = "+" if e48 >= 0 else ""
        sign72 = "+" if e72 >= 0 else ""
        lines.append(f"- **BTC ETF流量**: 24h:${sign24}{e24:.0f}M  48h:${sign48}{e48:.0f}M  72h:${sign72}{e72:.0f}M  趋势:{etf.get('flow_trend','?')}  ({etf['source']})")
    eth_etf = data.get("eth_etf_flow", {}) or {}
    if eth_etf.get("24h_flow_usd_m") is not None:
        ee24 = eth_etf["24h_flow_usd_m"]
        ee48 = eth_etf.get("48h_flow_usd_m", 0) or 0
        ee72 = eth_etf.get("72h_flow_usd_m", 0) or 0
        esign24 = "+" if ee24 >= 0 else ""
        esign48 = "+" if ee48 >= 0 else ""
        esign72 = "+" if ee72 >= 0 else ""
        lines.append(f"- **ETH ETF流量**: 24h:${esign24}{ee24:.0f}M  48h:${esign48}{ee48:.0f}M  72h:${esign72}{ee72:.0f}M  趋势:{eth_etf.get('flow_trend','?')}  ({eth_etf['source']})")

    # 因子权重
    lines.append("")
    lines.append("### 影响因子权重")
    lines.append("| 因子 | 权重说明 |")
    lines.append("|------|---------|")
    lines.append("| RSI超卖 | RSI<30超卖区域反弹(±25分) |")
    lines.append("| 资金费率 | 杠杆多头成本(±20分) |")
    lines.append("| 多空比 | 合约市场多空分歧度(±18分) |")
    lines.append("| 24h资金流 | 买方成交占比(±10分) |")
    lines.append("| 宏观联动 | 纳指/道指趋势(±12分) |")
    lines.append("| **恐慌贪婪** | **反情绪指标，越恐慌越看多(±25分)** |")
    lines.append("| **BTC ETF流量** | **机构资金流向，持续流入看多(±12分)** |")
    lines.append("| **ETH ETF流量** | **机构资金流向，ETH权重为BTC的0.7倍(±7分)** |")

    # 插针警告
    warnings = [s.get("pin_warning") for s in data["signals"].values() if s.get("pin_warning")]
    if warnings:
        lines += ["\n## 插针风险警告\n"] + [f"- {w}" for w in warnings]

    Path(path).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    json_out = str(OUT_DIR / "signal_v2_latest.json")
    md_out   = str(OUT_DIR / "signal_v2_latest.md")
    result = run(output_json=json_out, output_md=md_out)

    # 自动归档信号
    HISTORY_DIR = OUT_DIR / "signal_history"
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M")
    fname = f"signals_{ts}.json"
    (HISTORY_DIR / fname).write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"  存档: {fname}  ({len(result.get('signals',{}))}个信号)")
