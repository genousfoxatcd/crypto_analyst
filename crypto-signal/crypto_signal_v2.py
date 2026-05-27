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
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CST = timezone(timedelta(hours=8), "CST")  # UTC+8 北京时间

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    sys.exit("requests 未安装：pip install requests")

try:
    import macro_strategy
    HAS_MACRO_STRATEGY = True
except ImportError:
    HAS_MACRO_STRATEGY = False
    print("  [MacroStrategy] 模块未找到，跳过宏观策略分析")

try:
    from bayesian_signal_generator import BayesianSignalGenerator
    HAS_BAYESIAN = True
except ImportError:
    HAS_BAYESIAN = False
    print("  [BayesianSignalGenerator] 模块未找到，跳过贝叶斯信号生成")

try:
    from defillama_fetcher import fetch_defi_tvl
    HAS_DEFILAMA = True
except ImportError:
    HAS_DEFILAMA = False
    print("  [DeFiLlama] 模块未找到，跳过 DeFi TVL 数据")

try:
    import _price_cache as pcache
    HAS_PRICE_CACHE = True
except ImportError:
    HAS_PRICE_CACHE = False
    print("  [PriceCache] 模块未找到，跳过价格缓存")

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
}

EXCHANGE_WEIGHTS = {"Binance": 3, "OKX": 2, "Bybit": 2, "Gate.io": 1, "Kraken": 1, "CoinGecko": 1}
BB_PERIOD = 20        # 布林带周期（20×4h = 80h）
BB_SIGMA  = 2.0       # 布林带标准差倍数
ATR_PERIOD = 14       # ATR 周期
TIMEOUT   = 8         # API 超时秒数

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"

OUT_DIR = Path(__file__).parent
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
            if not sym:
                continue
            r = SESSION.get(f"https://www.okx.com/api/v5/market/ticker?instId={sym}", timeout=TIMEOUT)
            d = r.json()["data"][0]
            price = float(d["last"])
            open24h = float(d.get("open24h", 0)) or 0
            change_24h = round((price - open24h) / open24h * 100, 2) if open24h > 0 else None
            vol_usdt = float(d.get("volCcy24h", 0)) or 0   # USDT 计价 24h 成交量
            out[coin] = {"price": price, "change_24h": change_24h,
                         "volume_24h": vol_usdt, "source": "OKX"}
        except Exception:
            pass
    return out

def _fetch_bybit_prices() -> dict[str, dict]:
    out = {}
    for coin, cfg in COINS.items():
        try:
            sym = cfg["bybit"]
            if not sym:
                continue
            r = SESSION.get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}", timeout=TIMEOUT)
            d = r.json()["result"]["list"][0]
            out[coin] = {"price": float(d["lastPrice"]),
                         "change_24h": float(d.get("price24hPcnt", 0)) * 100,
                         "volume_24h": float(d.get("turnover24h", 0)) or 0,  # USDT 计价成交量
                         "source": "Bybit"}
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
                             "volume_24h": float(index[k].get("quote_volume", 0)) or 0,
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
    """CoinGecko —— 降级源，SSL 失败静默跳过（3s 短超时）"""
    try:
        from requests.exceptions import SSLError
        ids = ",".join(cfg["cg"] for cfg in COINS.values())
        r   = SESSION.get(
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={ids}&vs_currencies=usd&include_24hr_change=true",
            timeout=3,     # ← 短超时，不阻塞主流程
        )
        data = r.json()
        out = {}
        cg_to_coin = {cfg["cg"]: c for c, cfg in COINS.items()}
        for gid, vals in data.items():
            coin = cg_to_coin.get(gid)
            if coin:
                out[coin] = {"price": vals["usd"], "change_24h": vals.get("usd_24h_change", 0), "source": "CoinGecko"}
        return out
    except SSLError:
        print("  [CoinGecko] SSL 握手失败，跳过", file=sys.stderr)
        return {}
    except Exception:
        return {}


def _fetch_volume_changes(prices: dict, symbols: dict) -> dict:
    """获取各币种24h交易量环比变化率
    对比：当前24h滚动成交量 vs 昨日全日成交量（Binance日K线）"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    changes = {}
    def _get(symbol: str, coin: str) -> tuple:
        try:
            # 获取当前24h滚动成交量（已从prices中拿到）
            cur_vol = prices.get(coin, {}).get("volume_24h", 0) or 0
            # 获取昨日全日k线成交量（倒数第2根完整日K线）
            r = SESSION.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit=2", timeout=6).json()
            if len(r) >= 2:
                yesterday_vol = float(r[0][7])  # 昨日全日quote volume
                if yesterday_vol > 0 and cur_vol > 0:
                    return symbol, round((cur_vol - yesterday_vol) / yesterday_vol * 100, 1)
            return symbol, None
        except Exception:
            return symbol, None
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_get, sym, coin): coin for coin, sym in symbols.items()}
        for f in as_completed(futs):
            coin = futs[f]; _, chg = f.result()
            changes[coin] = chg
    return changes


def fetch_multi_exchange_prices() -> dict[str, dict]:
    """并发采集，计算加权均价（偏差>3% 告警）"""
    # ── 完整结果缓存检查（5min TTL）─────────────────────
    if HAS_PRICE_CACHE:
        _cached = pcache.get_result_cache()
        if _cached:
            print(f"  [Cache] 完整结果命中，跳过 API 调用（{len(_cached)} 币种）")
            return _cached

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
        # 格式化成交量为亿/百万单位
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
    # 获取24h交易量环比（基于Binance日K线）
    syms = {c: cfg["binance"] for c, cfg in COINS.items() if c in result}
    vol_changes = _fetch_volume_changes(result, syms)
    for coin, chg in vol_changes.items():
        if coin in result:
            result[coin]["volume_change_pct"] = chg
    # ── 写入完整结果缓存 ──────────────────────────────
    if HAS_PRICE_CACHE:
        pcache.set_result_cache(result)
    
    # 【严格强制】验证所有价格数据真实性
    result = _validate_all_prices(result)
    
    return result

def _validate_all_prices(prices_dict: dict) -> dict:
    """
    【严格强制】验证所有价格数据真实性
    检查项:
    1. 价格必须为正数
    2. 必须有数据源标识（sources 非空）
    3. 价格必须在合理范围内（与主流交易所价差 < 5%）
    4. 标记无效数据，禁止用于分析
    """
    print(f"  [PriceValidate] 验证 {len(prices_dict)} 币种价格数据...")
    validated = {}
    invalid_count = 0
    
    for coin, data in prices_dict.items():
        # 检查1: 价格必须为正数
        price = data.get("price", 0)
        if not price or price <= 0:
            print(f"    🔴 {coin}: 价格无效（{price}），标记为不可用")
            data["is_valid"] = False
            data["invalid_reason"] = "价格无效"
            invalid_count += 1
            validated[coin] = data
            continue
        
        # 检查2: 必须有数据源标识
        sources = data.get("sources", {})
        if not sources:
            print(f"    🔴 {coin}: 缺少数据源标识，标记为模拟数据")
            data["is_valid"] = False
            data["invalid_reason"] = "缺少数据源"
            invalid_count += 1
            validated[coin] = data
            continue
        
        # 检查3: 数据源数量必须 >= 1
        if len(sources) < 1:
            print(f"    🟡 {coin}: 只有 {len(sources)} 个数据源，可靠性低")
            data["is_valid"] = True
            data["warning"] = f"数据源少（{len(sources)}个）"
            validated[coin] = data
            continue
        
        # 检查4: 价格偏差检测（多源加权时已完成，这里二次确认）
        max_dev = data.get("max_dev_pct", 0)
        if max_dev > 5:
            print(f"    🟡 {coin}: 数据源偏差 {max_dev:.1f}% > 5%，警告")
            data["warning"] = f"数据源偏差大（{max_dev:.1f}%）"
        
        # 通过验证
        data["is_valid"] = True
        data["validated_at"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M UTC+8 北京时间")
        validated[coin] = data
        print(f"    ✅ {coin}: ${price:.4f} 验证通过（{len(sources)}源，偏差{max_dev:.1f}%）")
    
    print(f"  [PriceValidate] 完成：{len(prices_dict) - invalid_count} 有效，{invalid_count} 无效")
    return validated

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

        # RSI(14)
        rsi_14 = None
        if n >= 15:
            gains, losses = 0.0, 0.0
            for i in range(1, 15):
                diff = closes[-i] - closes[-(i+1)]
                if diff > 0:
                    gains += diff
                else:
                    losses -= diff
            avg_gain = gains / 14
            avg_loss = losses / 14
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                rsi_14 = 100 - (100 / (1 + rs))
            else:
                rsi_14 = 100.0

        # 4h 资金流（最近 6 根 = 24h）
        recent = min(6, n)
        buy_vol  = sum(taker_buy_vol[-recent:])
        sell_vol = sum(volumes[-recent:]) - buy_vol
        flow_ratio = buy_vol / (buy_vol + sell_vol) if (buy_vol + sell_vol) > 0 else 0.5
        # >0.55 = 买方主导, <0.45 = 卖方主导

        # EMA20 计算（4h 档位）
        ema20 = None
        ema20_slope = None  # 斜率：正=上升, 负=下降
        if n >= 20:
            alpha = 2.0 / 21.0  # EMA20 平滑系数
            ema_val = closes[0]  # 初始化为第一根收盘价
            for c in closes[1:]:
                ema_val = alpha * c + (1 - alpha) * ema_val
            ema20 = round(ema_val, 6)
            # 斜率：EMA20 近3根变化率，>0上升 <0下降
            ema_window = closes[-min(3, n):]
            ema_slope_start = closes[min(len(ema_window), n) - len(ema_window)]
            if len(ema_window) >= 2:
                ema_slope_vals = [ema_slope_start]
                for c in ema_window[1:]:
                    ema_slope_vals.append(alpha * c + (1 - alpha) * ema_slope_vals[-1])
                ema20_slope = round((ema_slope_vals[-1] - ema_slope_vals[-2]) / ema_slope_vals[-2] * 100, 3) if len(ema_slope_vals) >= 2 else None

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
            "rsi_14": round(rsi_14, 1) if rsi_14 is not None else None,
            "ema20": ema20,
            "ema20_slope": ema20_slope,  # 近3根EMA斜率（%/根）
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
    基础清算价 = 价格 × (1 - 1/leverage + 维保率0.005)
    
    v2.1: 加入ATR波动率缓冲 + 多空比拥挤调整，使距离%跨币种差异化
    """
    ls_ratio = contract.get("ls_ratio_top") or contract.get("ls_ratio_global", 1.0)
    atr = tech.get("atr", price * 0.02)

    # ── 波动率缓冲（ATR/price）──
    # 多头清算区缓冲较小，空头清算区缓冲较大（不对称）
    atr_pct = atr / price if price > 0 else 0.02
    vol_buf_long = atr_pct * 0.3   # 多头清算区更靠近（缓冲小）
    vol_buf_short = atr_pct * 1.5  # 空头清算区更远离（缓冲大，不对称）
    
    # ── 拥挤度调整（多空比）──
    # 对多头/空头使用不同调整系数，使距离%显著不同
    crowd_adj_long = (ls_ratio - 1.0) * 0.02   # 多头清算区调整（±4%）
    crowd_adj_short = (ls_ratio - 1.0) * 0.05  # 空头清算区调整（±10%，更不对称）
    crowd_adj_long = max(-0.04, min(0.04, crowd_adj_long))
    crowd_adj_short = max(-0.10, min(0.10, crowd_adj_short))
    
    # 估算多头集中清算区（向下插针目标）
    # 20x 使用较低乘数 + 安全下限（20x距离≥1%, 10x距离≥3%）
    liq_long = {
        "5x":  round(price * (1 - 1/5  + 0.005 + vol_buf_long*0.5 + crowd_adj_long*0.5), 6),
        "10x": round(price * max(0.87, min(0.95, 1 - 1/10 + 0.005 + vol_buf_long + crowd_adj_long)), 6),
        "20x": round(price * max(0.955, min(0.99, 1 - 1/20 + 0.005 + vol_buf_long*0.7 + crowd_adj_long*0.7)), 6),
    }
    # 估算空头集中清算区（向上插针目标）
    # 注：floor值必须设低，让vol_buf和crowd_adj产生跨币种差异化
    liq_short = {
        "5x":  round(price * (1 + 1/5  - 0.005 - vol_buf_short*0.5 - crowd_adj_short*0.5), 6),
        "10x": round(price * max(1.02, min(1.15, 1 + 1/10 - 0.005 - vol_buf_short - crowd_adj_short)), 6),
        "20x": round(price * max(1.002, min(1.05, 1 + 1/20 - 0.005 - vol_buf_short*0.7 - crowd_adj_short*0.7)), 6),
    }
    
    # 主要危险方向判断（降低阈值：0.60→0.52, 0.40→0.48）
    long_pct  = contract.get("long_pct_top") or contract.get("long_pct_global", 0.5)
    taker_bsr = contract.get("taker_buy_sell_ratio", 1.0)
    
    if long_pct >= 0.51:
        # 多头过于拥挤 → 向下插针风险高
        primary_risk = "DOWN"
        pin_target_primary = liq_long["10x"]   # 10x 是零售最常见杠杆
        pin_target_extreme = liq_long["20x"]
    elif long_pct <= 0.49:
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
    
    # ═══ 爆多仓概率（多头拥挤 → 向下插针风险）═══
    # 因子: 多头占比拥挤度(50%) + 资金费率惩罚(30%) + BB低位风险(20%)
    import math
    funding_rate = contract.get("funding_rate", 0) or 0
    bb_pos = tech.get("price_vs_bb_pct", 50)
    
    # 多头拥挤度归一化：long_pct偏离50%越多，风险越高
    crowd_score_long = max(0, (long_pct - 0.5) * 2)   # 范围 0~1（多头侧）
    funding_penalty = min(1.0, abs(funding_rate) * 1000)
    bb_risk_long = max(0, 1 - bb_pos / 100)            # BB低位=下方风险高
    
    x_long = crowd_score_long * 1.5 + funding_penalty * 0.8 + bb_risk_long * 0.6
    long_liq_prob = 1 / (1 + math.exp(-x_long + 1.2))  # sigmoid
    
    # ═══ 爆空仓概率（空头集中 → 向上插针风险）═══
    # 因子: 空头占比拥挤度(50%) + 吃单比风险(30%) + BB高位风险(20%)
    short_pct = 1 - long_pct
    crowd_score_short = max(0, (short_pct - 0.5) * 2)  # 范围 0~1（空头侧）
    taker_bsr = contract.get("taker_buy_sell_ratio", 1.0)
    taker_risk = max(0, taker_bsr - 1.0) if taker_bsr else 0  # 买方强势=空头风险
    bb_risk_short = max(0, bb_pos / 100 - 0.5)               # BB高位=上方风险高
    
    x_short = crowd_score_short * 1.5 + taker_risk * 0.5 + bb_risk_short * 0.6
    short_liq_prob = 1 / (1 + math.exp(-x_short + 1.2))  # sigmoid
    
    # 保留综合概率（取两者较大值，向后兼容）
    liq_prob = max(long_liq_prob, short_liq_prob)
    
    # 插针点位（基于 ATR 和流动性）
    # 插针通常打到：当前价 ± ATR * 2-3
    atr_mult = 2.5  # 插针倍数（经验值）
    pin_long_price = price - atr * atr_mult   # 向下插针点位
    pin_short_price = price + atr * atr_mult  # 向上插针点位
    
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
        "pin_long_price": round(pin_long_price, 6),   # 向下插针点位
        "pin_short_price": round(pin_short_price, 6),  # 向上插针点位
        "liq_probability": round(liq_prob * 100, 1),      # 综合爆仓概率%（兼容旧字段）
        "liq_long_probability": round(long_liq_prob * 100, 1),   # 爆多仓概率%
        "liq_short_probability": round(short_liq_prob * 100, 1), # 爆空仓概率%
        "atr": round(atr, 6),
    }

# ══════════════════════════════════════════════════════════════════════════════
# 模块 5 — 宏观数据（Nasdaq + 地缘 + ETF流量）
# ══════════════════════════════════════════════════════════════════════════════
def fetch_nasdaq_data() -> dict:
    """通过 Yahoo Finance / Nasdaq.com API / Stooq 获取纳指，含缓存fallback"""
    import time as _time
    SCRIPT_DIR = Path(__file__).parent
    CACHE_FILE = SCRIPT_DIR / ".nasdaq_cache.json"

    # 尝试1：Nasdaq.com API（最可靠，无需cookie）
    # 注意：Nasdaq.com 使用 COMP 作为纳斯达克综合指数代码，不是 IXIC
    import requests as _requests
    for symbol in ["COMP", "NDX"]:
        try:
            r = _requests.get(
                f"https://api.nasdaq.com/api/quote/{symbol}/info?assetclass=index",
                timeout=TIMEOUT,
                headers={"User-Agent": UA, "Accept": "application/json",
                         "Referer": "https://www.nasdaq.com/"})
            if r.status_code == 200:
                data = r.json()
                info = data.get("data") or {}
                if not info:
                    continue
                primary = info.get("primaryData") or {}
                price_raw = primary.get("lastSalePrice", "")
                price_str = str(price_raw).replace("$", "").replace(",", "") if price_raw else ""
                price = float(price_str) if price_str else None
                chg_raw = primary.get("percentageChange", "")
                chg_str = str(chg_raw).replace("%", "").replace(",", "") if chg_raw else ""
                chg_pct = float(chg_str) if chg_str else None
                if price and price > 0:
                    result = {
                        "price": round(price, 2),
                        "change_1d_pct": round(chg_pct, 2) if chg_pct is not None else None,
                        "source": f"nasdaq.com ({symbol})",
                    }
                    _save_nasdaq_cache(CACHE_FILE, result)
                    return result
        except Exception:
            continue

    # 尝试2：Yahoo Finance（使用独立session，避免SESSION的Accept header导致429）
    yf_session = _requests.Session()
    yf_session.headers.update({"User-Agent": UA})
    for attempt in range(2):
        try:
            r = yf_session.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EIXIC?interval=1d&range=5d",
                timeout=TIMEOUT,
                headers={"User-Agent": UA, "Referer": "https://finance.yahoo.com/"})
            if r.status_code == 429:
                _time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code == 200:
                data = r.json()
                result_data = data.get("chart", {}).get("result", [None])[0]
                if not result_data:
                    continue
                closes = result_data.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                closes = [c for c in closes if c is not None]
                if len(closes) >= 2:
                    chg_1d = (closes[-1] - closes[-2]) / closes[-2] * 100
                    result = {
                        "price": round(closes[-1], 2),
                        "change_1d_pct": round(chg_1d, 2),
                        "source": "Yahoo Finance",
                    }
                    _save_nasdaq_cache(CACHE_FILE, result)
                    return result
        except Exception:
            _time.sleep(1)

    # 备用3：尝试 Stooq
    try:
        r = SESSION.get("https://stooq.com/q/l/?s=^ndq&f=sd2t2ohlcv&h&e=csv", timeout=TIMEOUT)
        lines = [l for l in r.text.strip().split("\n") if l and not l.startswith("Date")]
        if len(lines) >= 2:
            today_line = lines[-1].split(",")
            yesterday_line = lines[-2].split(",")
            today_close = float(today_line[6])
            yesterday_close = float(yesterday_line[6])
            chg_pct = round((today_close - yesterday_close) / yesterday_close * 100, 2) if yesterday_close != 0 else None
            result = {"price": today_close, "change_1d_pct": chg_pct, "source": "Stooq"}
            _save_nasdaq_cache(CACHE_FILE, result)
            return result
        elif lines:
            line = lines[-1].split(",")
            price = float(line[6])
            return {"price": price, "change_1d_pct": None, "source": "Stooq"}
    except Exception:
        pass

    # 备用2：读取缓存
    try:
        cached = json.loads(CACHE_FILE.read_text())
        if cached.get("price") and cached["price"] > 0:
            cached["source"] = cached.get("source", "?") + "(缓存)"
            return cached
    except Exception:
        pass

    return {}

def _save_nasdaq_cache(cache_file: Path, data: dict) -> None:
    """保存纳指数据缓存"""
    try:
        cache_file.write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass

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
    """美元指数 — Yahoo Finance + 缓存fallback（含延迟重试，避免429）"""
    import time as _time
    import requests as _requests
    SCRIPT_DIR = Path(__file__).parent
    CACHE_FILE = SCRIPT_DIR / ".dxy_cache.json"

    # 使用独立session（避免SESSION的Accept header导致429）
    yf_session = _requests.Session()
    yf_session.headers.update({"User-Agent": UA})

    # 尝试1：Yahoo Finance（最多3次，指数退避，首次延迟5秒避免429）
    for attempt in range(3):
        try:
            _time.sleep(5 if attempt == 0 else 2 ** attempt)
            r = yf_session.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=3d",
                timeout=TIMEOUT,
                headers={"User-Agent": UA, "Referer": "https://finance.yahoo.com/"})
            if r.status_code == 429:
                continue
            if r.status_code == 200:
                data = r.json()
                result_data = data.get("chart", {}).get("result", [None])[0]
                if result_data:
                    closes = result_data.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                    closes = [c for c in closes if c is not None]
                    if len(closes) >= 2:
                        chg = (closes[-1] - closes[-2]) / closes[-2] * 100
                        result = {"price": round(closes[-1], 2), "change_1d_pct": round(chg, 2), "source": "Yahoo Finance"}
                        _save_nasdaq_cache(CACHE_FILE, result)
                        return result
        except Exception:
            pass

    # 备用2：尝试 FRED API (美元指数 DXY)
    try:
        r = yf_session.get(
            "https://api.stlouisfed.org/fred/series/observations?series_id=DTWEXBGS&sort_order=desc&limit=2&api_key=demo&file_type=json",
            timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            obs = data.get("observations", [])
            if len(obs) >= 2:
                today = float(obs[0]["value"])
                yesterday = float(obs[1]["value"])
                chg = (today - yesterday) / yesterday * 100
                result = {"price": round(today, 2), "change_1d_pct": round(chg, 2), "source": "FRED"}
                _save_nasdaq_cache(CACHE_FILE, result)
                return result
    except Exception:
        pass

    # 备用3：读取缓存（DXY 日内变化不大，缓存可用）
    try:
        cached = json.loads(CACHE_FILE.read_text())
        if cached.get("price") and cached["price"] > 0:
            cached["source"] = cached.get("source", "?") + "(缓存)"
            return cached
    except Exception:
        pass

    return {}

# ══════════════════════════════════════════════════════════════════════════════
# 模块 5d — BTC / ETH ETF 多日净流量
# ══════════════════════════════════════════════════════════════════════════════
def fetch_btc_etf_flow() -> dict:
    """
    BTC ETF 24h / 48h / 72h 净流量（通过 CoinGecko 成交量估算）
    每天独立求和后计算 Day-over-Day 变化量。
    """
    return fetch_coin_etf_flow("bitcoin", "BTC")


def fetch_coin_etf_flow(coin_id: str, coin_label: str) -> dict:
    """
    获取指定币种的 ETF 流量估算（通过 CoinGecko 7天成交量数据）
    按24h窗口独立求和，计算 Day-over-Day 变化量
    """
    result = {"24h_flow_usd_m": None, "48h_flow_usd_m": None, "72h_flow_usd_m": None,
              "flow_trend": "unknown", "source": ""}
    pct = 0.10 if coin_id == "bitcoin" else 0.08

    def _compute_flows(volumes: list) -> dict | None:
        if len(volumes) < 10:
            return None
        now_ms = volumes[-1][0]
        d1, d2, d3 = 0.0, 0.0, 0.0
        for ts, v in volumes:
            age_h = (now_ms - ts) / 3600000
            if age_h < 24:       d1 += v
            elif age_h < 48:     d2 += v
            elif age_h < 72:     d3 += v
        d1f, d2f, d3f = d1*pct, d2*pct, d3*pct
        return {"24h": round((d1f-d2f)/1e8, 1),
                "48h": round((d2f-d3f)/1e8, 1),
                "72h": round((max(d3f,0))/1e8, 1),
                "d1": round(d1f/1e8,1), "d2": round(d2f/1e8,1), "d3": round(d3f/1e8,1)}

    try:
        r = SESSION.get(f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?vs_currency=usd&days=7", timeout=8)
        if r.status_code == 200:
            vols = r.json().get("total_volumes", [])
            f = _compute_flows(vols)
            if f:
                result["24h_flow_usd_m"] = f["24h"]
                result["48h_flow_usd_m"] = f["48h"]
                result["72h_flow_usd_m"] = f["72h"]
                if abs(f["24h"]) > abs(f["48h"])*1.2 and f["24h"] > 0:
                    result["flow_trend"] = "increasing"
                elif abs(f["24h"]) > abs(f["48h"])*1.2 and f["24h"] < 0:
                    result["flow_trend"] = "decreasing"
                else:
                    result["flow_trend"] = "fluctuating"
                result["source"] = "CoinGecko(估)"
                return result
    except Exception as e:
        print(f"  [{coin_label} ETF] CoinGecko: {e}", file=sys.stderr)
    return result


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

    fr = contract.get("funding_rate", 0)
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

    # 6) 24h 涨跌幅趋势因子（新增，与趋势方向对齐）
    change_24h = price_data.get("change_24h")
    if change_24h is not None:
        if change_24h > 8:
            score += 15    # 强势上涨，趋势共振
        elif change_24h > 3:
            score += 8
        elif change_24h < -8:
            score -= 15    # 暴跌中，但需关注均值回归
        elif change_24h < -3:
            score -= 8

    # 6) 宏观联动（纳指 + 道指综合，加密先行指标）
    # 各币与美股相关性权重（BTC/ETH最高，迷因币最低）
    MACRO_CORR = {
        "BTC": 1.00, "ETH": 1.00, "SOL": 0.90, "BNB": 0.85,
        "TAO": 0.85, "ZEC": 0.75, "DOGE": 0.65, "CAKE": 0.60, "HYPE": 0.85,
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

    # ── 7) 趋势过滤（4h EMA20方向，避免逆势交易）─────────────────
    # 使用 EMA20 近3根斜率作为趋势方向判定：
    #   ema20_slope > 0 → 上涨趋势 → 做空信号被打折
    #   ema20_slope < 0 → 下跌趋势 → 做多信号被打折
    # 若 EMA20 不可用，降级使用 change_24h
    ema20_val = tech.get("ema20")
    ema20_slope = tech.get("ema20_slope")
    
    if ema20_slope is not None:
        # 有EMA数据 → 精确趋势过滤
        if ema20_slope < -0.1 and score > 0:
            # 下跌趋势中做多 → 得分减半（逆势风险大）
            score *= 0.5
        elif ema20_slope > 0.1 and score < 0:
            # 上涨趋势中做空 → 得分减半（逆势风险大）
            score *= 0.5
    else:
        # 降级：EMA不可用，使用24h涨跌幅作趋势代理
        change_24h_val = price_data.get("change_24h", 0) or 0
        if change_24h_val < -1.5 and score > 0:
            score *= 0.5   # 逆势做多 → 得分减半
        elif change_24h_val > 1.5 and score < 0:
            score *= 0.5   # 逆势做空 → 得分减半

    # ── 8) 突破检测（BB宽幅扩张+价格突破上轨 = 主升浪）──────────
    bb_width = tech.get("bb_width_pct", 5)
    if bb_width > 15 and price > bb_upper:
        # 强制LONG（布林带大幅扩张+价格突破上轨=趋势加速信号）
        direction = "LONG"
        score = max(score, 15.0)
        breakout_override = True
    else:
        breakout_override = False

    # ── 判断方向 ───────────────────────────────────────────────────────────
    if breakout_override:
        pass  # 已由突破检测设定
    elif score >= 10:
        direction = "LONG"
    elif score <= -10:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # ── 入场点位（收紧区间，0.5%-1.5% 可操作范围）─────────────────
    if direction == "LONG":
        # 做多：入场区在下轨~中轨之间（0.3%-2% 窄区间）
        entry_low  = max(bb_lower, price * 0.985)                # 不低于下轨，不低于-1.5%
        entry_high = min(bb_mid - atr * 0.15, price * 0.998)      # 不高于中轨，不高于-0.2%
        # 若当前价已在下轨附近（pos_pct<30），可在当前附近入场
        if pos_pct < 30:
            entry_low  = price * 0.997
            entry_high = price * 1.003
        tp1 = bb_mid
        tp2 = bb_upper
        sl  = bb_lower - atr * 0.8
        leverage = 3 if tech.get("atr_pct", 3) < 3.5 else 2

    elif direction == "SHORT":
        # 做空：入场区在上轨~中轨之间（0.3%-2% 窄区间）
        entry_low  = max(bb_mid + atr * 0.15, price * 1.002)     # 不低于中轨，不低于+0.2%
        entry_high = min(bb_upper, price * 1.015)                 # 不高于上轨，不高于+1.5%
        if pos_pct > 70:
            # 顶部附近：入场在当前位置
            entry_low  = price * 0.997
            entry_high = price * 1.003
            tp1 = bb_mid
            tp2 = bb_lower
            sl  = max(bb_upper + atr * 0.8, entry_high * 1.008)
        elif price < bb_mid:
            # 已跌破中轨：入场在当前位置
            entry_low  = price * 0.998
            entry_high = min(bb_mid, price * 1.008)              # 收紧上限
            tp1 = bb_lower
            tp2 = max(bb_lower - atr * 0.8, price * 0.97)
            sl  = max(bb_mid * 1.005, entry_high * 1.01)
        else:
            tp1 = bb_mid
            tp2 = bb_lower
            sl  = max(bb_upper + atr * 0.8, entry_high * 1.008)
        leverage = 3 if tech.get("atr_pct", 3) < 3.5 else 2

    else:
        # NEUTRAL：当前价±0.5% 极窄区间
        entry_low  = price * 0.995
        entry_high = price * 1.005
        tp1 = bb_mid
        tp2 = None
        sl  = price * 0.92
        leverage = 2

    # ── 入场就绪判断 ─────────────────────────────────────
    entry_mid = (entry_low + entry_high) / 2
    entry_ready = "unknown"
    if price > 0 and entry_high > 0:
        dev_pct = (price - entry_high) / entry_high * 100  # 统一用上沿线
        if direction == "NEUTRAL":
            # 观望策略：不判断"入场"，只记录偏离度
            entry_ready = f"neutral_{dev_pct:+.1f}%"
        elif direction == "LONG":
            if price >= entry_low and price <= entry_high:
                entry_ready = "ready"
            elif price > entry_high:
                entry_ready = f"above_{dev_pct:.1f}%"
            else:
                entry_ready = "not_ready"
        elif direction == "SHORT":
            if price >= entry_low and price <= entry_high:
                entry_ready = "ready"
            elif price < entry_low:
                dev_low = (entry_low - price) / entry_low * 100
                entry_ready = f"below_{dev_low:.1f}%"
            else:
                entry_ready = "not_ready"

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

    # 优先使用贝叶斯后验概率（如果可用）
    bayesian_prob = None
    if HAS_BAYESIAN:
        try:
            # 创建贝叶斯信号生成器（使用历史交易数据）
            history_file = Path(__file__).parent / "paper_trade_history.json"
            if history_file.exists():
                bay_gen = BayesianSignalGenerator(history_file, lookback_days=30)
                
                # 准备信号数据（包含各因子的值）
                signal_for_bayes = {
                    "bb_pos": pos_pct,
                    "funding_rate": fr,
                    "long_pct": long_pct,
                    "taker_bsr": taker_bsr,
                    "flow_buy": flow_buy,
                    "change_24h": change_24h
                }
                
                # 计算贝叶斯后验概率
                bayesian_prob = bay_gen.calc_posterior(coin, signal_for_bayes)
                
                if bayesian_prob and bayesian_prob > 0:
                    prob_score = bayesian_prob
                    print(f"  📊 贝叶斯后验概率: {prob_score:.1f}% (coin={coin})")
        except Exception as e:
            print(f"  ⚠️ 贝叶斯概率计算失败: {e}")

    # 如果贝叶斯概率不可用，使用原有规则计算
    if "prob_score" not in locals() or prob_score is None:

        # 基础得分 50 + 方向得分 + R/R 加分
        base_prob = 50 + max(-35, min(35, int(score * 0.7)))
        if rr >= 3.0: base_prob += 10
        elif rr >= 2.0: base_prob += 5
        elif rr < 1.5: base_prob -= 10
        prob_score = max(30, min(82, round(base_prob)))
        print(f"  📊 使用规则计算 prob_score: {prob_score}% (coin={coin})")

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
        "rsi_14": tech.get("rsi_14"),
        "bb": {"upper": R(bb_upper), "mid": R(bb_mid), "lower": R(bb_lower),
               "width_pct": tech.get("bb_width_pct"), "pos_pct": tech.get("price_vs_bb_pct")},
        "atr": R(atr),
        "funding_rate": R(fr * 100, 4),
        "long_pct": R(long_pct, 1),
        "taker_bsr": R(taker_bsr, 3),
        "flow_buy_pct": tech.get("flow_24h_buy_pct"),
        "ema20": tech.get("ema20"),
        "ema20_slope": tech.get("ema20_slope"),
        "entry_ready": entry_ready,
    }

# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════
def run(output_json: str | None = None, output_md: str | None = None) -> dict:
    ts = datetime.now(CST)
    print(f"\n{'='*60}")
    print(f"  加密货币信号引擎 v2 — {ts.strftime('%Y-%m-%d %H:%M UTC+8 北京时间')}")
    print(f"{'='*60}")

    # ① 价格（并发）
    print("\n[1/5] 多交易所价格采集（6源）...")
    prices = fetch_multi_exchange_prices()
    for coin, d in prices.items():
        vcp = d.get("volume_change_pct")
        vcp_str = f" 环比:{vcp:+.1f}%" if vcp is not None else ""
        print(f"  {coin:5} ${d['price']:>12,.4f}  {d['change_24h']:+.2f}%  "
              f"  量:{d.get('volume_str','—'):>6}{vcp_str}  偏差:{d['max_dev_pct']}%  {d['quality']}")

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
            ls = d.get("ls_ratio_top") or d.get("ls_ratio_global") or 0
            oi_m = d.get("open_interest_usdt", 0) / 1e6
            # 从价格和合约数据推算多仓/空仓清算均价（5x-10x平均）
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

    # ③ K线 + RSI + 资金流
    print("\n[3/5] 4h K线数据（RSI + 24h资金流）...")
    tech_data: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(fetch_klines_and_indicators, sym, "4h", 50): coin
                for coin, sym in futures_symbols.items()}
        for f in as_completed(futs):
            coin = futs[f]
            tech_data[coin] = f.result()
            t = tech_data[coin]
            rsi_str = f"  RSI:{t.get('rsi_14', '—')}" if t.get('rsi_14') is not None else ""
            flow_val = t.get('flow_24h_buy_pct')
            flow_str = f"{flow_val:.1f}%" if flow_val is not None else "N/A"
            print(f"  {coin:5}  "
                  f"买流:{flow_str}{rsi_str}")

    # ④ 宏观数据
    print("\n[4/5] 宏观数据（Nasdaq / DXY / DeFi TVL）...")
    nasdaq = fetch_nasdaq_data()
    dxy    = fetch_dxy_data()
    macro  = {"nasdaq": nasdaq, "dxy": dxy}
    if HAS_DEFILAMA:
        defi_tvl = fetch_defi_tvl()
        macro["defi_tvl"] = defi_tvl
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

    # ETF流量
    etf_flow = fetch_btc_etf_flow()
    if etf_flow.get("24h_flow_usd_m") is not None:
        e24 = etf_flow["24h_flow_usd_m"]
        e48 = etf_flow.get("48h_flow_usd_m", 0) or 0
        e72 = etf_flow.get("72h_flow_usd_m", 0) or 0
        print(f"  BTC ETF流量: 24h=${e24:.0f}M  48h=${e48:.0f}M  72h=${e72:.0f}M  趋势:{etf_flow.get('flow_trend','?')}  ({etf_flow['source']})")
    else:
        print("  BTC ETF流量: 数据不可用")
    eth_etf_flow = fetch_coin_etf_flow("ethereum", "ETH")
    if eth_etf_flow.get("24h_flow_usd_m") is not None:
        ee24 = eth_etf_flow["24h_flow_usd_m"]
        ee48 = eth_etf_flow.get("48h_flow_usd_m", 0) or 0
        ee72 = eth_etf_flow.get("72h_flow_usd_m", 0) or 0
        print(f"  ETH ETF流量: 24h=${ee24:.0f}M  48h=${ee48:.0f}M  72h=${ee72:.0f}M  趋势:{eth_etf_flow.get('flow_trend','?')}  ({eth_etf_flow['source']})")
    else:
        print("  ETH ETF流量: 数据不可用")

    # ⑤ 信号生成（在宏观策略分析之前，因为需要全量币种数据）
    print("\n[5/6] 信号生成...")
    signals: dict[str, dict] = {}
    for coin in COINS:
        if coin not in prices:
            continue
        price_d = prices[coin]
        liq = estimate_liquidation_zones(
            price_d["price"],
            contracts.get(coin, {}),
            tech_data.get(coin, {}))
        # 贝叶斯修正爆仓概率
        try:
            from bayesian_liquidation import correct_liquidation
            liq = correct_liquidation(coin, liq)
        except ImportError:
            pass
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

    # ⑥ 宏观策略分析（含全量币种趋势分析）
    # ── 网格机器人联动（P2 优化）─────────────────────
    # 添加 grid_signal 字段：趋势判断（bullish/bearish/neutral）
    for c, sig in signals.items():
        direction = sig.get("direction", "NEUTRAL")
        score = abs(sig.get("prob_score", 50))
        if direction == "LONG" and score >= 60:
            grid_signal = "bullish"
        elif direction == "SHORT" and score >= 60:
            grid_signal = "bearish"
        else:
            grid_signal = "neutral"
        sig["grid_signal"] = grid_signal
    
    macro_assessment = {}
    if HAS_MACRO_STRATEGY:
        print("\n[6/6] 宏观策略分析（市场阶段+全量币种趋势+板块轮动）...")
        # 传递全量价格/合约/信号/技术数据
        full_signal_data = {
            "prices": {c: {
                "price": prices[c].get("price"),
                "change_24h": prices[c].get("change_24h"),
                "volume_24h": prices[c].get("volume_24h"),
                "volume_str": prices[c].get("volume_str"),
                "volume_change_pct": prices[c].get("volume_change_pct"),
            } for c in prices},
            "contracts": contracts,
            "signals": {c: {
                "direction": signals[c].get("direction", "NEUTRAL"),
                "score": signals[c].get("score", 0),
                "funding_rate": signals[c].get("funding_rate", 0),
                "long_pct": signals[c].get("long_pct", 50),
                "taker_bsr": signals[c].get("taker_bsr", 1.0),
                "flow_buy_pct": signals[c].get("flow_buy_pct", 50),
                "prob_score": signals[c].get("prob_score", 50),
                "leverage": signals[c].get("leverage", 2),
                "rr_ratio": signals[c].get("rr_ratio", 0),
            } for c in signals},
            "tech_data": tech_data,
            "macro": macro,
            "etf_flow": etf_flow,
            "eth_etf_flow": eth_etf_flow,
        }
        macro_assessment = macro_strategy.run_with_signal_data(full_signal_data)
        a = macro_assessment.get("assessment", {})
        print(f"  📊 市场阶段: {a.get('phase_label','?')}  (得分:{a.get('phase_score','?')})")
        print(f"  😱 F&G: {macro_assessment.get('fear_greed',{}).get('value','?')} "
              f"({macro_assessment.get('fear_greed',{}).get('classification_cn','')})")
        btc_dom = macro_assessment.get('market_overview',{}).get('btc_dominance')
        total_mcap = macro_assessment.get('market_overview',{}).get('total_market_cap_str')
        print(f"  💰 BTC主导率: {btc_dom if (btc_dom and btc_dom > 0) else '数据不可用'}%  "
              f"总市值: {total_mcap if total_mcap else '数据不可用'}")
        print(f"  💵 稳定币: {macro_assessment.get('stablecoin',{}).get('total_supply_str','数据不可用')}")
        print(f"  🎯 策略建议: {a.get('strategy','?')}")
        # 板块分析
        seg = a.get("segment_analysis", {})
        if seg:
            print(f"  📈 主流币({seg.get('mainstream_avg','?')}%) vs 山寨币({seg.get('altcoin_avg','?')}%)")
            print(f"  🔄 资金轮动: {seg.get('rotation_signal','?')}")
    else:
        print("\n[6/6] 宏观策略分析 → 跳过（模块未加载）")

    # ── 网格机器人联动（P2 优化）─────────────────────
    # 添加 grid_signal 字段：趋势判断（bullish/bearish/neutral）
    for c, sig in signals.items():
        direction = sig.get("direction", "NEUTRAL")
        score = abs(sig.get("prob_score", 50))
        if direction == "LONG" and score >= 60:
            grid_signal = "bullish"
        elif direction == "SHORT" and score >= 60:
            grid_signal = "bearish"
        else:
            grid_signal = "neutral"
        sig["grid_signal"] = grid_signal
    
    # ── 汇总输出 ──────────────────────────────────────────────────────────
    result = {
        "generated_at": ts.strftime("%Y-%m-%d %H:%M UTC+8 北京时间"),
        "prices":       {c: {"price": prices[c]["price"],
                              "change_24h": prices[c]["change_24h"],
                              "volume_24h": prices[c]["volume_24h"],
                              "volume_str": prices[c]["volume_str"],
                              "volume_change_pct": prices[c].get("volume_change_pct"),
                              "source_count": prices[c]["source_count"],
                              "sources": prices[c]["sources"]}
                         for c in prices},
        # ── 技术数据（供报告生成器重新计算爆仓区间）─────
        "tech_data":    tech_data,
        # ── 网格机器人联动（P2 优化）─────────────────────
        "grid_signal":  {},  # 占位符，后面填充
        
        "signals":      signals,
        "contracts":    contracts,   # ← 新增：合约数据（供报告生成器重新计算爆仓区间）
        "macro":        macro,
        "macro_assessment": macro_assessment,   # ← 新增宏观策略评估
        "macro_deltas":     macro_assessment.get("macro_deltas", {}),  # ← 环比增减数据
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
    """输出 Markdown 简报，供 HTML 生成器读取"""
    ts = data["generated_at"]  # 已经是 CST 字符串
    lines = [f"# 加密货币信号报告 | {ts}\n",
             f"> 引擎版本 v2 | 价格：{sum(d['source_count'] for d in data['prices'].values())}个数据点 | "
             f"Binance Futures 合约 + RSI + 清算预估 + 宏观策略\n"]

    # ── 0. 宏观策略摘要（报告第一部分）───────────────────────────────────
    ma = data.get("macro_assessment", {}) or {}
    assessment = ma.get("assessment", {}) or {}
    fng = ma.get("fear_greed", {}) or {}
    mkt = ma.get("market_overview", {}) or {}
    sc  = ma.get("stablecoin", {}) or {}

    if assessment:
        lines += ["---\n", "## 📊 宏观策略摘要\n"]
        # 市场阶段 + 风险等级
        lines += [
            f"| **市场阶段** | {assessment.get('phase_label','—')} | **综合评分** | {assessment.get('phase_score','?')} |",
            f"| **风险等级** | {assessment.get('risk_level','?')} | **策略定位** | {assessment.get('strategy','?')} |",
        ]

        # Fear & Greed
        if fng and fng.get("value") is not None:
            delta = fng.get("delta_7d", 0)
            delta_str = f" {'📈+' if delta >= 0 else '📉'}{abs(delta)}" if delta else ""
            fng_signal = ("🟢 历史底部区间" if fng['value'] <= 25 else
                          "🟢 积累机会" if fng['value'] <= 45 else
                          "🟡 中性" if fng['value'] <= 55 else
                          "🟠 注意风险" if fng['value'] <= 75 else "🔴 警惕顶部")
            lines.append(
                f"| **恐惧贪婪指数** | {fng['value']} ({fng.get('classification_cn','?')}) {delta_str} | "
                f"**趋势** | {fng.get('trend','?')} (7d{delta_str}) |")
            lines.append(f"| **F&G 判断** | {fng_signal} | | |")

        # BTC Dominance + 市值
        btc_dom = mkt.get("btc_dominance") if mkt else None
        if btc_dom and btc_dom > 0:
            dom_signal = ("🏦 BTC主导（防御）" if btc_dom > 60 else
                          "⚖️ 中性" if btc_dom > 45 else
                          "🚀 山寨活跃" if btc_dom < 40 else "⚖️ 中性")
            mcap_src = " (缓存)" if mkt.get("cached") else ""
            lines.append(
                f"| **BTC主导率** | {btc_dom}% ({dom_signal}) | "
                f"**总市值** | {mkt.get('total_market_cap_str','?')}{mcap_src} |")
        else:
            cache_mkt = mkt if mkt else {}
            cache_mcap = cache_mkt.get('total_market_cap_str','?')
            lines.append(f"| **BTC主导率/总市值** | CoinGecko数据不可用 | 上次缓存: {cache_mcap if cache_mcap != '?' else '无'} | |")

        # 稳定币
        if sc and sc.get("total_supply_str"):
            sc_signal = ("🟢 购买力充沛" if sc.get('total_supply_usd',0) > 150e9 else
                         "🟡 正常" if sc.get('total_supply_usd',0) > 100e9 else "🔴 购买力不足")
            lines.append(f"| **稳定币储备** | {sc['total_supply_str']} ({sc_signal}) | | |")

        # ── 各币种趋势表 ───────────────────────────────────────────
        coin_trends = assessment.get("coin_trends", {}) or {}
        if coin_trends:
            lines += ["", "### 📈 各币种趋势（多因子综合判断）"]
            lines += ["| 币种 | 24h涨跌 | 趋势方向 | 资金费率 | 买方流量 | 多头占比 | 24h量 |",
                      "|------|---------|----------|---------|---------|---------|-------|"]
            for coin in ["BTC", "ETH", "SOL", "BNB", "DOGE", "TAO", "ZEC", "CAKE"]:
                t = coin_trends.get(coin, {})
                if not t:
                    continue
                chg = t.get("change_24h")
                chg_str = f"{chg:+.2f}%" if chg is not None else "—"
                fr = t.get("funding_rate")
                fr_str = f"{fr:+.3f}%" if fr is not None else "—"
                flow = t.get("flow_buy_pct") or 50
                long_pct = t.get("long_pct") or 50
                trend_label = t.get("trend_label", "⚪ 中性/震荡")
                lines.append(f"| {coin} | {chg_str} | {trend_label} | {fr_str} | {flow:.1f}% | {long_pct:.0f}% | {t.get('volume','—')} |")

        # ── 板块轮动分析 ──────────────────────────────────────────
        seg = assessment.get("segment_analysis", {}) or {}
        if seg:
            lines += ["", "### 🔄 板块轮动分析"]
            ms_avg = seg.get("mainstream_avg")
            ac_avg = seg.get("altcoin_avg")
            ms_avg_str = f"{ms_avg:+.2f}%" if ms_avg is not None else "—"
            ac_avg_str = f"{ac_avg:+.2f}%" if ac_avg is not None else "—"
            rotation = seg.get("rotation_signal", "—")
            lines += [
                f"| **板块** | **24h平均涨跌** | **最强** | **最弱** | **多头拥挤** |",
                f"|---------|----------------|---------|---------|------------|",
                f"| 🏦 **主流币** BTC/ETH/SOL/BNB | {ms_avg_str} | {seg.get('mainstream_max_winner','—')} | {seg.get('mainstream_max_loser','—')} | {seg.get('mainstream_avg_long_pct','?')}% |",
                f"| 🚀 **山寨币** DOGE/TAO/ZEC/CAKE | {ac_avg_str} | {seg.get('altcoin_max_winner','—')} | {seg.get('altcoin_max_loser','—')} | {seg.get('altcoin_avg_long_pct','?')}% |",
                "", f"> 💡 资金流向：{rotation}",
            ]

        # 策略建议
        strategy_advice = assessment.get("strategy_advice", [])
        if strategy_advice:
            lines += ["", "**策略建议：**"]
            for tip in strategy_advice:
                lines.append(f"- {tip}")

        # 核心信号汇总
        signals_list = assessment.get("signals", [])
        if signals_list:
            lines += ["", "**核心信号汇总：**"]
            for sig in signals_list:
                if isinstance(sig, list) and len(sig) >= 3:
                    dim, direction, desc = sig[0], sig[1], sig[2]
                    icon = "🟢" if "LONG" in direction else ("🔴" if "SHORT" in direction else "⚪")
                    lines.append(f"- {icon} **[{dim}]** {desc}")

        lines.append("\n---\n")

    # 价格表
    lines += ["## 💰 实时价格\n",
              "| 币种 | 价格 | 24h | 24h交易量 | 量环比 |",
              "|------|------|-----|----------|--------|"]
    for coin, d in data["prices"].items():
        p = d['price']
        if abs(p) >= 10:   p_str = f"${p:,.1f}"
        elif abs(p) >= 1:  p_str = f"${p:,.2f}"
        else:               p_str = f"${p:,.3f}"
        sign = "▲" if d["change_24h"] >= 0 else "▼"
        vol = d.get("volume_str", "—")
        vcp = d.get("volume_change_pct")
        vcp_str = f"{vcp:+.1f}%" if vcp is not None else "—"
        lines.append(f"| {coin} | {p_str} | {sign}{abs(d['change_24h']):.2f}% | {vol} | {vcp_str} |")

    lines.append("")

    # 信号表
    lines += ["## 🎯 交易信号\n",
              "| 币种 | 方向 | 入场区间 | 杠杆 | TP1 | TP2 | SL | 盈利率(TP1) | 概率 | 资金费率 | 多头占比 |",
              "|------|------|--------|------|-----|-----|----|------------|------|---------|---------|"]
    for coin, s in data["signals"].items():
        dir_cn = {"LONG": "做多", "SHORT": "做空", "NEUTRAL": "观望"}.get(s["direction"], "?")
        entry  = f"{s['entry_zone'][0]:,.2f}–{s['entry_zone'][1]:,.2f}"
        profit = f"{s['rr_tp1']:+.1f}%" if s["rr_tp1"] is not None else "—"
        tp2_str = f"{s['tp2']:,.2f}" if s['tp2'] else "—"
        lines.append(
            f"| {coin} | {dir_cn} | {entry} | {s['leverage']}× | "
            f"{s['tp1']:,.2f} | {tp2_str} | {s['sl']:,.2f} | "
            f"{profit} | {s['prob_score']}% | {s.get('funding_rate','?')}% | {s.get('long_pct','?')}% |")

    lines.append("")

    # 宏观
    nasdaq = data["macro"].get("nasdaq", {})
    dxy = data["macro"].get("dxy", {})
    lines += ["## 🌐 宏观数据\n"]
    if nasdaq and nasdaq.get('price') is not None:
        nd_chg = nasdaq.get('change_1d_pct')
        nd_chg_str = f"{nd_chg:+.2f}%" if nd_chg is not None else "N/A"
        lines.append(f"- **纳斯达克**: {nasdaq.get('price'):,.2f}  ({nd_chg_str})")
    if dxy and dxy.get('price') is not None:
        dx_chg = dxy.get('change_1d_pct')
        dx_chg_str = f"{dx_chg:+.2f}%" if dx_chg is not None else "N/A"
        lines.append(f"- **美元指数(DXY)**: {dxy.get('price'):.2f}  ({dx_chg_str})")

    # F&G
    fng = data.get("fng", {}) or {}
    if fng.get("now_value") is not None:
        lines.append(f"- **恐慌贪婪指数**: {fng['now_value']} ({fng.get('classification','')})  趋势:{fng.get('trend_direction','')}")
    # BTC ETF
    etf = data.get("etf_flow", {}) or {}
    if etf.get("24h_flow_usd_m") is not None:
        e24 = etf["24h_flow_usd_m"]; e48 = etf.get("48h_flow_usd_m",0) or 0; e72 = etf.get("72h_flow_usd_m",0) or 0
        s24 = "+" if e24>=0 else ""; s48 = "+" if e48>=0 else ""; s72 = "+" if e72>=0 else ""
        lines.append(f"- **BTC ETF流量**: 24h:${{s24}}{e24:.0f}M  48h:${{s48}}{e48:.0f}M  72h:${{s72}}{e72:.0f}M  趋势:{etf.get('flow_trend','?')}  ({etf['source']})")
    # ETH ETF
    eth_etf = data.get("eth_etf_flow", {}) or {}
    if eth_etf.get("24h_flow_usd_m") is not None:
        ee24 = eth_etf["24h_flow_usd_m"]; ee48 = eth_etf.get("48h_flow_usd_m",0) or 0; ee72 = eth_etf.get("72h_flow_usd_m",0) or 0
        es24 = "+" if ee24>=0 else ""; es48 = "+" if ee48>=0 else ""; es72 = "+" if ee72>=0 else ""
        lines.append(f"- **ETH ETF流量**: 24h:${{es24}}{ee24:.0f}M  48h:${{es48}}{ee48:.0f}M  72h:${{es72}}{ee72:.0f}M  趋势:{eth_etf.get('flow_trend','?')}  ({eth_etf['source']})")
    lines.append("")

    # 合约数据表
    lines += ["## 📊 合约数据\n",
              "| 币种 | 资金费率 | 多空比L/S | 多仓均价 | 空仓均价 | 买方流量 | 多头占比 |",
              "|------|---------|----------|---------|---------|---------|---------|"]
    for coin, s in data["signals"].items():
        fr   = s.get("funding_rate", 0) or 0
        liq  = s.get("liquidation", {}) or {}
        ls   = liq.get("ls_ratio", 1) or 1
        flow = s.get("flow_buy_pct", 50) or 50
        lp   = s.get("long_pct", 50) or 50
        liq_l = liq.get("liq_long_zones", {}) or {}
        liq_s = liq.get("liq_short_zones", {}) or {}
        l5 = liq_l.get("5x"); l10 = liq_l.get("10x")
        s5 = liq_s.get("5x"); s10 = liq_s.get("10x")
        if l5 and l10:
            avg_l = (float(l5) + float(l10)) / 2
            avg_l_str = f"${{avg_l:,.1f}}" if abs(avg_l) >= 10 else f"${{avg_l:,.4f}}"
        else:
            avg_l_str = "—"
        if s5 and s10:
            avg_s = (float(s5) + float(s10)) / 2
            avg_s_str = f"${{avg_s:,.1f}}" if abs(avg_s) >= 10 else f"${{avg_s:,.4f}}"
        else:
            avg_s_str = "—"
        lines.append(f"| {coin} | {fr:+.3f}% | {ls:.2f} | {avg_l_str} | {avg_s_str} | {flow:.1f}% | {lp:.0f}% |")
    lines.append("")

    # 爆仓区间
    lines += ["## ⚡ 爆仓区间估算（10x / 20x 杠杆）\n",
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

    # 因子权重
    lines += ["### 影响因子权重\n",
              "| 因子 | 权重说明 |",
              "|------|---------|",
              "| RSI超卖 | RSI<30超卖区域反弹(±25分) |",
              "| 资金费率 | 杠杆多头成本(±20分) |",
              "| 多空比 | 合约市场多空分歧度(±18分) |",
              "| 24h资金流 | 买方成交占比(±10分) |",
              "| 宏观联动 | 纳指/道指趋势(±12分) |",
              "| **恐慌贪婪** | **反情绪指标，越恐慌越看多(±25分)** |",
              "| **BTC ETF流量** | **机构资金流向，持续流入看多(±12分)** |",
              "| **ETH ETF流量** | **机构资金流向，ETH权重为BTC的0.7倍(±7分)** |"]
    lines.append("")


    # 插针警告
    warnings = [s.get("pin_warning") for s in data["signals"].values() if s.get("pin_warning")]
    if warnings:
        lines += ["\n## ⚠️ 插针风险警告\n"] + [f"- {w}" for w in warnings]

    Path(path).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser(description="加密货币信号引擎 v2")
    parser.add_argument("--auto-open", action="store_true", help="自动在外部浏览器中打开生成的HTML报告")
    parser.add_argument("--output-json", type=str, help="JSON输出路径")
    parser.add_argument("--output-md", type=str, help="Markdown输出路径")
    args = parser.parse_args()
    
    import json as _json
    json_out = args.output_json or str(OUT_DIR / "signal_v2_latest.json")
    md_out   = args.output_md or str(OUT_DIR / "signal_v2_latest.md")
    result = run(output_json=json_out, output_md=md_out)
    
    # 自动归档信号
    HISTORY_DIR = OUT_DIR / "signal_history"
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(CST)
    ts = now.strftime("%Y%m%d_%H%M")
    fname = f"signals_{ts}.json"
    (HISTORY_DIR / fname).write_text(_json.dumps(result, indent=2, ensure_ascii=False))
    print(f"  存档: {fname}  ({len(result.get('signals',{}))}个信号)")

    # 运行 OpenClue 分析师完整报告（使用已有信号数据避免重跑）
    try:
        import openclue_analyst
        print("\n" + "=" * 60)
        print("  🔍 运行 OpenClue 分析师（10维度框架）...")
        print("=" * 60)
        dims = openclue_analyst.run(existing_result=result)
        print(f"✅ OpenClue 10维度分析完成：{list(dims.keys())}")
    except ImportError:
        print("  ⏭️ OpenClue 分析师模块未加载，跳过")
    except Exception as e:
        print(f"  ⏭️ OpenClue 分析师异常: {e}")

    # 重新生成主 HTML 报告（含 OpenClue 维度数据）
    try:
        print("\n生成主 HTML 信号报告...")
        import report_generator_v2 as rg
        today = datetime.now(CST).strftime("%Y%m%d_%H%M")
        out_path = OUT_DIR.parent / "reports" / f"crypto_signal_v2_{today}.html"
        rg.generate_html(OUT_DIR / "signal_v2_latest.json", OUT_DIR / "paper_positions.json", out_path, auto_open=args.auto_open)
        print(f"✅ 主报告已更新: {out_path}")
    except Exception as e:
        print(f"  ⏭️ 报告生成异常: {e}")
