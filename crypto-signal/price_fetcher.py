#!/usr/bin/env python3
"""
多源实时加密货币价格采集器
来源优先级: Binance API > CoinGecko API > CoinMarketCap API > WebSearch fallback
交叉验证：任意两源价格偏差 > 3% 时输出警告
"""

import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ── 目标币种 ──────────────────────────────────────────────
COINS = {
    "BTC":  {"binance": "BTCUSDT",  "coingecko": "bitcoin",      "cmc_id": 1},
    "ETH":  {"binance": "ETHUSDT",  "coingecko": "ethereum",     "cmc_id": 1027},
    "BNB":  {"binance": "BNBUSDT",  "coingecko": "binancecoin",  "cmc_id": 1839},
    "SOL":  {"binance": "SOLUSDT",  "coingecko": "solana",       "cmc_id": 5426},
    "DOGE": {"binance": "DOGEUSDT", "coingecko": "dogecoin",     "cmc_id": 74},
    "TAO":  {"binance": "TAOUSDT",  "coingecko": "bittensor",    "cmc_id": 22974},
}

TIMEOUT = 8  # seconds per request

# ── Source 1: Binance 现货 API（公开，无需 key）────────────
def fetch_binance(symbols: list[str]) -> dict:
    """批量拉取 Binance 现货最新价 + 24h 涨跌"""
    results = {}
    try:
        url = "https://api.binance.com/api/v3/ticker/24hr"
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = {item["symbol"]: item for item in resp.json()}
        for coin, cfg in COINS.items():
            sym = cfg["binance"]
            if sym in data:
                results[coin] = {
                    "price": float(data[sym]["lastPrice"]),
                    "change_24h": float(data[sym]["priceChangePercent"]),
                    "volume_24h": float(data[sym]["quoteVolume"]),
                    "source": "Binance",
                }
    except Exception as e:
        print(f"[Binance] 采集失败: {e}", file=sys.stderr)
    return results


# ── Source 2: CoinGecko 公开 API（无 key，限速较低）─────────
def fetch_coingecko(coins: dict) -> dict:
    results = {}
    try:
        ids = ",".join(cfg["coingecko"] for cfg in coins.values())
        url = (
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={ids}&vs_currencies=usd"
            f"&include_24hr_change=true&include_24hr_vol=true"
        )
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for coin, cfg in coins.items():
            gid = cfg["coingecko"]
            if gid in data:
                results[coin] = {
                    "price": data[gid]["usd"],
                    "change_24h": data[gid].get("usd_24h_change", 0),
                    "source": "CoinGecko",
                }
    except Exception as e:
        print(f"[CoinGecko] 采集失败: {e}", file=sys.stderr)
    return results


# ── Source 3: CoinMarketCap（需 API key，从环境变量读取）────
def fetch_cmc(coins: dict) -> dict:
    results = {}
    api_key = os.environ.get("CMC_API_KEY", "")
    if not api_key:
        print("[CMC] 未设置 CMC_API_KEY，跳过", file=sys.stderr)
        return results
    try:
        symbols = ",".join(coins.keys())
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        headers = {"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"}
        params = {"symbol": symbols, "convert": "USD"}
        resp = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        for coin in coins:
            if coin in data:
                q = data[coin]["quote"]["USD"]
                results[coin] = {
                    "price": q["price"],
                    "change_24h": q["percent_change_24h"],
                    "volume_24h": q["volume_24h"],
                    "source": "CoinMarketCap",
                }
    except Exception as e:
        print(f"[CMC] 采集失败: {e}", file=sys.stderr)
    return results


# ── Source 4: CoinGlass (公开行情快照，永续合约)──────────────
def fetch_coinglass() -> dict:
    """CoinGlass 公开 ticker，获取永续合约价格（更接近交易价）"""
    results = {}
    try:
        # CoinGlass 公开 ticker（无需 key）
        url = "https://open-api.coinglass.com/public/v2/indicator/price"
        params = {"symbol": "BTC,ETH,BNB,SOL,DOGE,TAO"}
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            for item in data.get("data", []):
                sym = item.get("symbol", "").upper()
                if sym in COINS:
                    results[sym] = {
                        "price": float(item.get("price", 0)),
                        "change_24h": float(item.get("priceChangePercent", 0)),
                        "source": "CoinGlass",
                    }
    except Exception as e:
        print(f"[CoinGlass] 采集失败: {e}", file=sys.stderr)
    return results


# ── 交叉验证 ──────────────────────────────────────────────
def cross_validate(multi_source: dict[str, dict]) -> dict:
    """
    multi_source: {coin: {source_name: {price, change_24h, ...}, ...}}
    返回验证结果，标记偏差 > 3% 的币种
    """
    validated = {}
    for coin, sources in multi_source.items():
        prices = {s: d["price"] for s, d in sources.items() if d.get("price", 0) > 0}
        if not prices:
            continue
        vals = list(prices.values())
        avg = sum(vals) / len(vals)
        max_dev = max(abs(p - avg) / avg for p in vals) * 100 if len(vals) > 1 else 0

        # 选主力价格：Binance > CoinGlass > CoinGecko > CMC
        primary = None
        for preferred in ["Binance", "CoinGlass", "CoinGecko", "CoinMarketCap"]:
            if preferred in sources:
                primary = sources[preferred]
                break
        if not primary:
            first_src = list(sources.keys())[0]
            primary = sources[first_src]

        validated[coin] = {
            **primary,
            "avg_price": round(avg, 6),
            "max_deviation_pct": round(max_dev, 2),
            "sources_used": list(prices.keys()),
            "all_prices": {s: round(p, 6) for s, p in prices.items()},
            "quality": "✅ 一致" if max_dev < 3 else f"⚠️ 偏差 {max_dev:.1f}%",
        }
    return validated


# ── 主流程 ────────────────────────────────────────────────
def fetch_all_prices(output_path: str | None = None) -> dict:
    if not REQUESTS_OK:
        print("requests 库未安装，请运行: pip install requests", file=sys.stderr)
        return {}

    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] 开始多源价格采集...")

    # 并发采集（简单串行，避免复杂依赖）
    raw: dict[str, dict] = {coin: {} for coin in COINS}

    # Source 1: Binance
    print("  → Binance 现货 API...")
    b = fetch_binance(list(COINS.keys()))
    for coin, d in b.items():
        raw[coin]["Binance"] = d

    # Source 2: CoinGecko
    print("  → CoinGecko API...")
    g = fetch_coingecko(COINS)
    for coin, d in g.items():
        raw[coin]["CoinGecko"] = d

    # Source 3: CoinMarketCap
    print("  → CoinMarketCap API...")
    c = fetch_cmc(COINS)
    for coin, d in c.items():
        raw[coin]["CoinMarketCap"] = d

    # Source 4: CoinGlass
    print("  → CoinGlass API...")
    gl = fetch_coinglass()
    for coin, d in gl.items():
        raw[coin]["CoinGlass"] = d

    # 交叉验证
    print("  → 交叉验证...")
    validated = cross_validate(raw)

    # 汇总结果
    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "coins": validated,
        "summary": {
            coin: {
                "price": d["price"],
                "change_24h": round(d.get("change_24h", 0), 2),
                "primary_source": d["source"],
                "quality": d["quality"],
            }
            for coin, d in validated.items()
        },
    }

    # 打印摘要
    print("\n=== 价格采集结果 ===")
    print(f"{'币种':<6} {'价格':>12} {'24h涨跌':>8} {'来源':<14} {'质量'}")
    print("-" * 60)
    for coin, d in result["summary"].items():
        sign = "+" if d["change_24h"] >= 0 else ""
        print(
            f"{coin:<6} {d['price']:>12,.4f} "
            f"{sign}{d['change_24h']:>7.2f}% "
            f"{d['primary_source']:<14} {d['quality']}"
        )

    # 写入文件
    if output_path:
        Path(output_path).write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"\n结果已写入: {output_path}")

    return result


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("CRYPTO_ANALYST_WS", "/Users/alex/projects/crypto_analyst"),
        "crypto-signal", "prices_latest.json"
    )
    fetch_all_prices(output_path=out)
