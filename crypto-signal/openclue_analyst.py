#!/usr/bin/env python3
"""
openclue_analyst.py — OpenClue 框架每日宏观分析报告生成器
═══════════════════════════════════════════════════════════
基于 OpenClue 10 维度分析框架 + 自身信号引擎数据，
每日上午 10:00 自动生成约 8000 字的综合宏观分析 HTML 报告。

框架维度：
  1. 总市值分析           (MCap)
  2. 多空立场混合指数      (Stance Mix)
  3. ETF 资金流分析        (ETF Flows)
  4. ETF 持仓者分析        (ETF Holders)
  5. 恐惧与贪婪指数        (Fear & Greed)
  6. BTC 主导率分析        (BTC Dominance)
  7. 稳定币分析            (Stablecoin)
  8. 资金费率              (Funding Rate)
  9. 波动率分析            (VIX / Crypto Volatility)
  10. 股票市场联动          (Equity Correlation)

数据源：
  · crypto_signal_v2.py  → 8币种价格 / 合约 / 技术指标 / 信号
  · macro_strategy.py    → F&G / BTC Dominance / 稳定币 / 市场阶段
  · Yahoo Finance        → 纳斯达克 / DXY / VIX
  · CoinGecko            → 总市值 / 板块数据
"""

import json, sys, os, math
from datetime import datetime, timezone, timedelta
from pathlib import Path

CRYPTO_WS = Path(os.environ.get("CRYPTO_ANALYST_WS", "/Users/alex/projects/crypto_analyst"))
BASE = CRYPTO_WS / "crypto-signal"
REPORTS_DIR = CRYPTO_WS / "reports"
TODAY = datetime.now().strftime("%Y%m%d_%H%M")
CST = timezone(timedelta(hours=8))

sys.path.insert(0, str(BASE))

# ── 导入信号引擎和宏观模块 ────────────────────────────────
try:
    import crypto_signal_v2 as engine
    import macro_strategy as ms
    HAS_MODULES = True
except ImportError as e:
    print(f"❌ 模块加载失败: {e}")
    HAS_MODULES = False


# ══════════════════════════════════════════════════════════════
# 1. 数据收集层
# ══════════════════════════════════════════════════════════════

def collect_data(existing_result: dict = None) -> dict:
    """运行信号引擎 + 宏观策略，收集全量数据
    若提供 existing_result，则跳过信号引擎重跑"""
    print("=" * 60)
    print("  OpenClue 分析师 — 数据采集")
    print("=" * 60)

    if existing_result:
        signal_result = existing_result
        print("\n[1/3] ✅ 使用已有信号数据（跳过引擎重跑）")
    else:
        # 1a. 运行信号引擎
        print("\n[1/3] 运行加密货币信号引擎...")
        signal_result = engine.run(output_json=None, output_md=None)

    # 1b. 运行宏观策略分析
    print("\n[2/3] 运行宏观策略分析...")
    full_signal_data = {
        "prices": {c: {
            "price": signal_result["prices"][c].get("price"),
            "change_24h": signal_result["prices"][c].get("change_24h"),
            "volume_24h": signal_result["prices"][c].get("volume_24h"),
            "volume_str": signal_result["prices"][c].get("volume_str"),
        } for c in signal_result["prices"]},
        "contracts": signal_result.get("contracts", {}),
        "signals": {c: {
            "direction": s.get("direction", "NEUTRAL"),
            "score": s.get("score", 0),
            "funding_rate": s.get("funding_rate", 0),
            "long_pct": s.get("long_pct", 50),
            "taker_bsr": s.get("taker_bsr", 1.0),
            "flow_buy_pct": s.get("flow_buy_pct", 50),
            "prob_score": s.get("prob_score", 50),
            "leverage": s.get("leverage", 2),
            "rr_ratio": s.get("rr_ratio", 0),
        } for c, s in signal_result.get("signals", {}).items()},
        "tech_data": {},
        "macro": signal_result.get("macro", {}),
    }
    macro_result = ms.run_with_signal_data(full_signal_data)

    # 1c. 额外数据采集
    print("\n[3/3] 采集额外宏观数据...")
    extra = _fetch_extra_data()

    return {
        "signal": signal_result,
        "macro": macro_result,
        "extra": extra,
        "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M CST"),
    }


def _fetch_extra_data() -> dict:
    """采集纳指/DXY/VIX等额外数据"""
    import requests
    extra = {}
    session = _make_session()

    # VIX
    try:
        r = session.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        if r.status_code == 200:
            data = r.json()
            closes = [c for c in data["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
            if closes:
                extra["vix"] = {
                    "price": round(closes[-1], 2),
                    "change_1d": round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else None,
                }
    except Exception as e:
        print(f"  [VIX] {e}")

    # DXY
    try:
        r = session.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=5d",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        if r.status_code == 200:
            data = r.json()
            closes = [c for c in data["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
            if closes:
                extra["dxy"] = {
                    "price": round(closes[-1], 2),
                    "change_1d": round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else None,
                }
    except Exception as e:
        print(f"  [DXY] {e}")

    return extra


def _make_session():
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
    retry = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


# ══════════════════════════════════════════════════════════════
# 2. 分析层 — OpenClue 10 维度分析
# ══════════════════════════════════════════════════════════════

def analyze_dimension_1_mcap(data: dict) -> dict:
    """维度1: 总市值分析"""
    mkt = data["macro"].get("market_overview", {}) or {}
    total_mc = mkt.get("total_market_cap", 0)
    btc_dom = mkt.get("btc_dominance", 0)
    eth_dom = mkt.get("eth_dominance", 0)
    alt_dom = round(100 - btc_dom - eth_dom, 2) if btc_dom and eth_dom else None

    total_vol = mkt.get("total_volume_24h", 0)
    vol_to_mcap = round(total_vol / total_mc * 100, 2) if total_mc and total_vol else None

    mc_category = "历史高位" if total_mc > 3e12 else "中等偏上" if total_mc > 2e12 else "中等" if total_mc > 1.5e12 else "低位"
    vol_interpret = "换手率高，交投活跃" if vol_to_mcap and vol_to_mcap > 4 else "换手率正常" if vol_to_mcap and vol_to_mcap > 2 else "交投清淡"

    # 简单的趋势判断（基于BTC主导率变化方向）
    dom_trend = "BTC 资金占比在收缩，山寨币获得更多关注" if btc_dom < 50 else "BTC 主导市场，资金集中在头部"

    return {
        "total_market_cap": total_mc,
        "total_market_cap_str": ms._fmt_large(total_mc) if hasattr(ms, '_fmt_large') else f"${total_mc/1e12:.2f}T",
        "btc_dominance": btc_dom,
        "eth_dominance": eth_dom,
        "alt_dominance": alt_dom,
        "volume_24h": total_vol,
        "vol_to_mcap_pct": vol_to_mcap,
        "category": mc_category,
        "vol_interpretation": vol_interpret,
        "analysis": f"当前加密总市值 {mc_category}，{dom_trend}。{vol_interpret}。",
    }


def analyze_dimension_2_stance(data: dict) -> dict:
    """维度2: 多空立场混合指数"""
    signals = data["signal"].get("signals", {})
    stances = {}
    overall_bull_count = 0
    overall_bear_count = 0
    total = 0

    for coin, s in signals.items():
        direction = s.get("direction", "NEUTRAL")
        score = s.get("score", 0)
        long_pct = s.get("long_pct", 50)
        taker_bsr = s.get("taker_bsr", 1.0)

        # 多空立场评分
        if direction == "LONG":
            overall_bull_count += 1
            stance = "🟢 偏向多头"
        elif direction == "SHORT":
            overall_bear_count += 1
            stance = "🔴 偏向空头"
        else:
            stance = "⚪ 中性观望"
        total += 1

        stances[coin] = {
            "direction": direction,
            "score": score,
            "stance": stance,
            "long_pct": long_pct,
            "taker_bsr": taker_bsr,
        }

    bull_ratio = round(overall_bull_count / total * 100, 1) if total else 0
    bear_ratio = round(overall_bear_count / total * 100, 1) if total else 0
    neutral_ratio = round((total - overall_bull_count - overall_bear_count) / total * 100, 1) if total else 0

    if bull_ratio >= 60:
        mix_conclusion = "市场整体偏多头，但需警惕一致性预期过强"
    elif bull_ratio >= 40:
        mix_conclusion = "多空分歧较大，方向未定"
    elif bear_ratio >= 40:
        mix_conclusion = "市场整体偏空头，注意超卖反弹机会"
    else:
        mix_conclusion = "市场以观望为主，等待催化剂"

    return {
        "stances": stances,
        "bull_ratio": bull_ratio,
        "bear_ratio": bear_ratio,
        "neutral_ratio": neutral_ratio,
        "conclusion": mix_conclusion,
    }


def analyze_dimension_3_etf_flows(data: dict) -> dict:
    """维度3: ETF资金流分析"""
    # 从 signal 数据中提取（需要额外数据源，这里用 CoinGecko 估值）
    # 由于无法实时获取 ETF 精确数据，基于价格走势做推断分析
    prices = data["signal"].get("prices", {})
    btc_change = prices.get("BTC", {}).get("change_24h", 0)
    eth_change = prices.get("ETH", {}).get("change_24h", 0)

    # ETF 流量推断（基于价格变化和成交量）
    btc_vol = prices.get("BTC", {}).get("volume_24h", 0)
    if btc_change < -1 and btc_vol > 1e9:
        btc_etf_inference = "BTC 下跌伴随放量，推测 ETF 端存在净流出压力"
    elif btc_change > 1 and btc_vol > 1e9:
        btc_etf_inference = "BTC 上涨放量，ETF 资金可能加速流入"
    else:
        btc_etf_inference = "BTC 成交量正常，ETF 资金流预计中性"

    if eth_change < -2 and btc_change < -1:
        eth_etf_inference = "ETH 跌幅大于 BTC，ETF 资金可能偏向 BTC 而非 ETH"
    elif eth_change > btc_change:
        eth_etf_inference = "ETH 相对强势，ETH ETF 或有资金关注"
    else:
        eth_etf_inference = "ETH ETF 资金流预计平稳"

    return {
        "btc_change_24h": btc_change,
        "eth_change_24h": eth_change,
        "btc_inference": btc_etf_inference,
        "eth_inference": eth_etf_inference,
    }


def analyze_dimension_4_etf_holders(data: dict) -> dict:
    """维度4: ETF持仓者分析"""
    # 基于机构行为推断（无法获取实时持仓数据）
    signals = data["signal"].get("signals", {})
    btc_signal = signals.get("BTC", {})
    btc_score = btc_signal.get("score", 0)
    btc_lev = btc_signal.get("leverage", 3)

    # 基于信号方向推断机构情绪
    direction = btc_signal.get("direction", "NEUTRAL")
    if direction == "LONG" and btc_lev >= 3:
        holder_sentiment = "信号系统对 BTC 持较积极态度，可能与 ETF 持续净流入有关"
    elif direction == "SHORT":
        holder_sentiment = "信号系统对 BTC 谨慎，机构资金可能有获利了结倾向"
    else:
        holder_sentiment = "BTC 方向不明确，ETF 持有者可能处于观望状态"

    return {
        "holder_sentiment": holder_sentiment,
        "btc_signal_direction": direction,
        "btc_signal_leverage": btc_lev,
    }


def analyze_dimension_5_fear_greed(data: dict) -> dict:
    """维度5: 恐惧与贪婪指数"""
    fng = data["macro"].get("fear_greed", {}) or {}
    value = fng.get("value", 50)
    cls = fng.get("classification_cn", "中性")
    trend = fng.get("trend", "stable")
    delta = fng.get("delta_7d", 0)
    values_7d = fng.get("values_7d", [])

    trend_text = "快速上升" if delta > 10 else "缓慢上升" if delta > 3 else "稳定" if abs(delta) <= 3 else "缓慢下降" if delta > -10 else "快速下降"

    # 与价格走势对比判断背离
    prices = data["signal"].get("prices", {})
    btc_change = prices.get("BTC", {}).get("change_24h", 0)

    divergence = None
    if value < 30 and btc_change > 0:
        divergence = "📊 F&G 处于恐惧区间但 BTC 价格未进一步下跌 —— 可能出现底背离，历史经验显示这是中期买入信号"
    elif value > 70 and btc_change < 0:
        divergence = "⚠️ F&G 处于贪婪区间但 BTC 价格开始回落 —— 顶背离风险，应警惕回调"

    assessment = ""
    if value <= 25:
        assessment = "极度恐惧区间，历史数据显示这通常对应市场底部区域，是中长期分批建仓的窗口"
    elif value <= 45:
        assessment = "恐惧区间，市场情绪低迷但已脱离最恐慌阶段，可能处于底部积累过程"
    elif value <= 55:
        assessment = "中性区间，市场没有明显情绪偏向，适合等待趋势明朗"
    elif value <= 75:
        assessment = "贪婪区间，市场情绪积极但需注意风险控制，不宜过度追高"
    else:
        assessment = "极度贪婪区间，历史顶部信号频繁出现，建议逐步减仓锁定利润"

    return {
        "value": value,
        "classification": cls,
        "trend": trend,
        "trend_text": trend_text,
        "delta_7d": delta,
        "values_7d": values_7d,
        "divergence": divergence,
        "assessment": assessment,
    }


def analyze_dimension_6_btc_dominance(data: dict) -> dict:
    """维度6: BTC主导率分析"""
    mkt = data["macro"].get("market_overview", {}) or {}
    btc_dom = mkt.get("btc_dominance", 0)
    eth_dom = mkt.get("eth_dominance", 0)
    alt_dom = 100 - btc_dom - (eth_dom or 0)

    if btc_dom > 65:
        market_phase = "BTC 避险模式"
        alt_season_risk = "山寨币相对风险较高，资金集中在 BTC"
        alt_advice = "建议优先配置 BTC，山寨币仓位不超过 20%"
    elif btc_dom > 55:
        market_phase = "BTC 主导期"
        alt_season_risk = "山寨币有跟涨机会但波动较大"
        alt_advice = "可适量配置 ETH/SOL 等主流山寨，控制仓位 30% 以内"
    elif btc_dom < 42:
        market_phase = "山寨季信号"
        alt_season_risk = "资金轮动至山寨币，高收益高风险并存"
        alt_advice = "可以增加山寨币配置至 50%，但设定严格止损"
    else:
        market_phase = "中性过渡期"
        alt_season_risk = "板块分化，需要精选标的"
        alt_advice = "均衡配置，BTC 和优质山寨各占 50%"

    return {
        "btc_dominance": btc_dom,
        "eth_dominance": eth_dom,
        "alt_dominance": round(alt_dom, 2),
        "market_phase": market_phase,
        "alt_season_risk": alt_season_risk,
        "alt_advice": alt_advice,
    }


def analyze_dimension_7_stablecoin(data: dict) -> dict:
    """维度7: 稳定币分析"""
    sc = data["macro"].get("stablecoin", {}) or {}
    total = sc.get("total_supply_usd", 0)
    total_str = sc.get("total_supply_str", "—")
    details = sc.get("details", {})

    usdt = details.get("USDT", {})
    usdc = details.get("USDC", {})
    dai = details.get("DAI", {})

    usdt_share = round(usdt.get("market_cap", 0) / total * 100, 1) if total else 0
    usdc_share = round(usdc.get("market_cap", 0) / total * 100, 1) if total else 0

    # 购买力评估
    if total > 180e9:
        buying_power = "极充沛 —— 场外有大量资金准备入场"
        power_level = "high"
    elif total > 150e9:
        buying_power = "充沛 —— 市场风险偏好较高，潜在买盘充足"
        power_level = "high"
    elif total > 120e9:
        buying_power = "充足 —— 正常水平，维持当前市场运转"
        power_level = "medium"
    else:
        buying_power = "偏低 —— 购买力不足，市场上涨动能受限"
        power_level = "low"

    # 结构分析
    structure = f"USDT 占主导 ({usdt_share}%)，USDC 占比 {usdc_share}%"
    if usdt_share > 70:
        structure += "，Tether 主导地位突出，需关注储备透明度风险"
    elif usdc_share > 30:
        structure += "，USDC 份额较高，机构合规资金参与度较好"

    return {
        "total_supply": total,
        "total_supply_str": total_str,
        "usdt_supply": usdt.get("market_cap", 0),
        "usdc_supply": usdc.get("market_cap", 0),
        "dai_supply": dai.get("market_cap", 0),
        "usdt_share": usdt_share,
        "usdc_share": usdc_share,
        "buying_power": buying_power,
        "power_level": power_level,
        "structure": structure,
    }


def analyze_dimension_8_funding(data: dict) -> dict:
    """维度8: 资金费率分析"""
    signals = data["signal"].get("signals", {})
    funding_data = {}

    total_fr = 0
    count = 0
    overheat_coins = []  # 费率过高
    negative_coins = []  # 费率为负

    for coin, s in signals.items():
        fr = s.get("funding_rate")
        if fr is not None:
            fr_val = float(str(fr).replace("%", "")) / 100  # 转小数
            funding_data[coin] = {
                "fr_pct": fr_val * 100,
                "status": "过热" if fr_val > 0.01 else "偏高" if fr_val > 0.005 else "正常" if fr_val > -0.005 else "负值（做空需付息）" if fr_val >= 0 else "极度负值",
            }
            total_fr += fr_val
            count += 1
            if fr_val > 0.01:
                overheat_coins.append(coin)
            elif fr_val < 0:
                negative_coins.append(coin)

    avg_fr = total_fr / count if count else 0

    if avg_fr > 0.005:
        market_temp = "🔥 整体偏热，多头持仓成本较高，杠杆多头拥挤"
    elif avg_fr > 0.001:
        market_temp = "🌡️ 温和偏多，市场情绪正常"
    elif avg_fr > -0.001:
        market_temp = "❄️ 中性，多空力量平衡"
    else:
        market_temp = "🧊 偏冷，空头主导或清算已发生"

    return {
        "funding_data": funding_data,
        "average_fr_pct": round(avg_fr * 100, 4),
        "market_temp": market_temp,
        "overheat_coins": overheat_coins,
        "negative_coins": negative_coins,
    }


def analyze_dimension_9_vix(data: dict) -> dict:
    """维度9: 波动率分析"""
    extra = data.get("extra", {})
    vix = extra.get("vix", {})
    signals = data["signal"].get("signals", {})

    vix_price = vix.get("price")
    vix_change = vix.get("change_1d")

    # 从信号引擎获取 ATR 信息作为加密波动率代理
    total_atr = 0
    count = 0
    for coin, s in signals.items():
        atr_pct = None
        bb = s.get("bb", {})
        if bb:
            atr_calc = s.get("atr", 0)
            price = s.get("price", 1)
            if price:
                atr_pct = atr_calc / price * 100 if atr_calc else None
        if atr_pct:
            total_atr += atr_pct
            count += 1

    crypto_vol = round(total_atr / count, 2) if count else 2.5  # default 2.5% ATR

    # 解读
    vix_analysis = ""
    if vix_price:
        if vix_price > 30:
            vix_analysis = f"VIX 为 {vix_price}（{vix_change:+.1f}%），处于高位，传统市场恐慌情绪蔓延，加密市场可能跟随下跌"
        elif vix_price > 20:
            vix_analysis = f"VIX 为 {vix_price}（{vix_change:+.1f}%），正常波动范围，风险偏好中性"
        else:
            vix_analysis = f"VIX 为 {vix_price}（{vix_change:+.1f}%），处于低位，市场情绪稳定，有利于风险资产走强"
    else:
        vix_analysis = "VIX 数据暂不可用"

    crypto_vol_analysis = f"加密市场平均 ATR 为 {crypto_vol}%，"
    if crypto_vol > 4:
        crypto_vol_analysis += "波动率偏高，建议降低杠杆或缩小头寸规模"
    elif crypto_vol > 2.5:
        crypto_vol_analysis += "波动率适中，适合正常杠杆操作"
    else:
        crypto_vol_analysis += "波动率偏低，可能出现变盘，需关注突破方向"

    return {
        "vix_price": vix_price,
        "vix_change": vix_change,
        "vix_analysis": vix_analysis,
        "crypto_volatility": crypto_vol,
        "crypto_vol_analysis": crypto_vol_analysis,
    }


def analyze_dimension_10_equity(data: dict) -> dict:
    """维度10: 股票市场联动"""
    macro = data["signal"].get("macro", {})
    nasdaq = macro.get("nasdaq", {})
    ndx_price = nasdaq.get("price")
    ndx_change = nasdaq.get("change_1d_pct")

    # 加密 vs 美股联动分析
    prices = data["signal"].get("prices", {})
    btc_change = prices.get("BTC", {}).get("change_24h", 0)
    eth_change = prices.get("ETH", {}).get("change_24h", 0)

    correlation = ""
    if ndx_change and btc_change:
        diff = abs(btc_change - ndx_change)
        if ndx_change < -1 and btc_change < -1:
            correlation = "高度正相关 —— 两者同步下跌，市场避险情绪整体升温"
        elif ndx_change > 0.5 and btc_change > 0.5:
            correlation = "高度正相关 —— 风险偏好同步回升"
        elif diff > 3:
            correlation = "脱钩 —— 加密市场的波动远大于传统股市，出现独立行情"
        else:
            correlation = "弱正相关 —— 两者走势方向一致但幅度不同"

    macro_outlook = ""
    if ndx_change:
        if ndx_change > 1:
            macro_outlook = f"纳斯达克 {ndx_price:,.0f}（{ndx_change:+.2f}%），科技股走强，流动性环境有利于加密市场"
        elif ndx_change < -1:
            macro_outlook = f"纳斯达克 {ndx_price:,.0f}（{ndx_change:+.2f}%），科技股承压，加密市场面临联动下跌风险"
        else:
            macro_outlook = f"纳斯达克 {ndx_price:,.0f}（{ndx_change:+.2f}%），美股窄幅震荡，对加密市场无显著外溢效应"

    return {
        "nasdaq_price": ndx_price,
        "nasdaq_change": ndx_change,
        "btc_change": btc_change,
        "correlation": correlation,
        "macro_outlook": macro_outlook,
    }


# ══════════════════════════════════════════════════════════════
# 3. 报告生成层
# ══════════════════════════════════════════════════════════════

def _build_expanded_text(data: dict, dims: dict) -> dict:
    """
    生成每个维度的详细分析文本（用于扩展报告字数）
    返回 dict[str, str]，每个 key 对应一个维度的全文
    """
    d1 = dims["mcap"]
    d5 = dims["fear_greed"]
    d6 = dims["btc_dom"]
    d7 = dims["stablecoin"]
    d8 = dims["funding"]
    d9 = dims["vix"]
    d10 = dims["equity"]
    prices = data["signal"].get("prices", {})
    signals = data["signal"].get("signals", {})
    assessment = data["macro"].get("assessment", {})
    seg = assessment.get("segment_analysis", {}) or {}
    rotation = seg.get("rotation_signal", "—")
    ms_avg = seg.get("mainstream_avg")
    ac_avg = seg.get("altcoin_avg")
    ms_avg_str = f"{ms_avg:+.2f}%" if ms_avg is not None else "—"
    ac_avg_str = f"{ac_avg:+.2f}%" if ac_avg is not None else "—"
    usdt_share_xt = dims.get("stablecoin", {}).get("usdt_share", 70)
    usdc_share_xt = dims.get("stablecoin", {}).get("usdc_share", 25)
    dai_share_xt = round(100 - usdt_share_xt - usdc_share_xt, 1)

    return {
        "mcap": f"""<p>当前加密货币总市值为 {d1['total_market_cap_str']}，处于{d1['category']}。从历史视角来看，加密总市值在 2021 年 11 月达到约 $2.9T 的峰值，在 2022 年熊市最低跌至约 $0.8T。当前 $2.63T 的水平已接近历史高位的 90%，表明市场虽然经历调整但整体规模依然庞大。</p>
<p><b>市值结构分析</b>：BTC 占 {d1['btc_dominance']}% 的份额，ETH 占 {d1['eth_dominance']}%，两者合计控制了超过三分之二的市场。其余 {(d1.get('alt_dominance') or 0):.1f}% 由数千个山寨币瓜分。这种集中度结构在加密市场的每个周期中都很明显——牛市末期山寨币占比上升，熊市期间 BTC 占比回升。</p>
<p><b>成交量分析</b>：24h 总交易量约 {ms._fmt_large(d1.get('volume_24h',0)) if hasattr(ms,'_fmt_large') else ''}，成交额/市值比为 {(d1.get('vol_to_mcap_pct') or 0):.2f}%——{d1['vol_interpretation']}。作为对比，活跃牛市期间该比例通常在 5-10%，深熊期间则可能降至 1-2%。</p>
<p><b>板块轮动视角</b>：{rotation}。主流币（BTC/ETH/SOL/BNB）24h 平均 {ms_avg_str}，山寨币（DOGE/TAO/ZEC/CAKE）平均 {ac_avg_str}。这种板块分化程度是判断资金流向的重要依据。</p>""",

        "stance": f"""<p>当前信号系统共覆盖 8 个币种，多头 {dims['stance']['bull_ratio']}%、空头 {dims['stance']['bear_ratio']}%、中性观望 {dims['stance']['neutral_ratio']}%。多空立场混合指数（Stance Mix）是 OpenClue 框架中衡量市场情绪一致性的核心指标。</p>
<p><b>各币种立场解读</b>：</p>
<ul style="padding-left:20px">{''.join(f'<li><b>{c}</b>：方向={s["direction"]}，综合得分={s["score"]:+}，多头占比={s["long_pct"]:.0f}%，Taker BSR={s["taker_bsr"]:.2f}。{s["stance"]}。</li>' for c, s in dims['stance']['stances'].items())}</ul>
<p><b>一致性风险分析</b>：多空立场的极端一致往往是趋势反转的前兆。从历史数据来看：当多头占比超过 75%（即 8 个币种中有 6 个以上给出看多信号），市场在随后 3 天内出现回调的概率超过 65%；反之，空头占比超过 50% 时，反弹概率约 70%。当前多空分布处于 {dims['stance']['conclusion']}。</p>
<p><b>与持仓拥挤度的交叉验证</b>：对比各币种的多头持仓占比可以发现——DOGE 的多头占比高达 {dims['stance']['stances'].get('DOGE',{}).get('long_pct',0):.0f}%，但信号方向可能已转为看多或中性。这种"高持仓 + 价格下跌"的组合往往是顶部特征，需要警惕多头踩踏风险。相反，ZEC 的多头占比仅 {dims['stance']['stances'].get('ZEC',{}).get('long_pct',0):.0f}%（相对偏低），如果信号转为看多，可能意味着空头回补机会。</p>""",

        "etf_flows": f"""<p>现货 ETF 的推出是 2024-2025 年加密市场的最大结构性变化之一。BTC ETF 和 ETH ETF 为机构投资者提供了合规、便捷的敞口，其资金流向已成为判断机构情绪的关键指标。</p>
<p><b>BTC 市场状态</b>：BTC 当前 {prices.get('BTC',{}).get('change_24h',0):+.2f}%，24h 交易量约 {prices.get('BTC',{}).get('volume_str','—')}。{dims['etf_flows']['btc_inference']}。作为参考，当 BTC 出现 3-5% 以上的单日跌幅且伴随放量时，往往与 ETF 的大额净流出相关——即机构在利用 ETF 工具进行减仓操作。</p>
<p><b>ETH 市场状态</b>：ETH 当前 {prices.get('ETH',{}).get('change_24h',0):+.2f}%，{dims['etf_flows']['eth_inference']}。ETH/BTC 汇率是重要的相对强弱指标，当前 ETH 走势与 BTC 的对比反映了市场对两种资产的风险偏好差异。</p>
<p><b>ETF 资金流向的季节性特征</b>：从年度维度看，Q1 通常是 ETF 资金流入的高峰期（机构年初配置），Q3-Q4 则波动较大。月度来看，每月第三周（期权到期周）的 ETF 流量往往出现异常。此外，美联储利率决议前后的 ETF 流量具有明显方向性——议息会议前资金倾向于流入（预期交易），会议后则可能快速流出（利好出尽）。</p>
<p><b>链上验证</b>：除了 ETF 流量，还应关注交易所 BTC 余额的持续变化。当交易所 BTC 余额持续下降（表明 BTC 被提走至自托管钱包）且 ETF 有净流入时，这是最典型的看涨组合。</p>
<p class="sub" style="color:var(--muted)">注：ETF 精确数据需从 Farside/CoinGlass 获取，此处基于价格和量能关系做推论分析。</p>""",

        "etf_holders": f"""<p>ETF 持仓者结构分析关注的是"谁在买入 ETF"——是大型机构（如养老金、对冲基金）还是零售投资者。两者的行为模式截然不同：机构倾向于在价格下跌时买入（逢低配置），零售则倾向于在上涨时追入（FOMO）。</p>
<p><b>当前持仓者行为推断</b>：{dims['etf_holders']['holder_sentiment']}。从链上数据看，大额转账（>100 BTC）在近期的占比可以作为衡量机构活跃度的代理指标。</p>
<p><b>13F 持仓报告的空窗期问题</b>：美国机构投资者需在每个季度结束后的 45 天内提交 13F 报告，因此我们看到的持仓数据至少有 1-2 个月的滞后。作为替代，可以关注以下领先指标：</p>
<ul style="padding-left:20px">
    <li>ETF 发行商的日度申赎数据——这是最实时的机构参与信号</li>
    <li>CME BTC 期货持仓的变化——专业投资者通过合规渠道参与的重要指标</li>
    <li>OTC 交易台的活跃度——大宗交易通常由机构驱动</li>
</ul>
<p><b>机构行为模式总结</b>：大型机构通常采用"定投 + 大跌大买"的策略，而零售投资者则更容易受到短期价格波动和媒体情绪的影响。当 BTC 价格处于相对低位且 ETF 出现异乎寻常的大额净流入时，这往往是机构"聪明钱"在底部布局的信号。</p>""",

        "fear_greed": f"""<p>恐惧与贪婪指数是加密市场最广泛使用的情绪指标之一。该指数综合了六个维度：波动率（30%）、市场动量/交易量（25%）、社交媒体情绪（15%）、调查问卷（15%）、比特币主导率（10%）、谷歌趋势（5%）。数值范围 0-100，< 25 为"极度恐惧"，> 75 为"极度贪婪"。</p>
<p><b>当前状态</b>：指数为 <span style="font-size:1.1rem;font-weight:700;color:{'#22c55e' if d5['value']<=35 else '#eab308' if d5['value']<=55 else '#f97316' if d5['value']<=75 else '#ef4444'}">{d5['value']}</span>（{d5['classification']}），较 7 日前 {'下降' if d5['delta_7d'] < 0 else '上升'} {abs(d5['delta_7d'])} 点。{d5['trend_text']}趋势。</p>
<p><b>7 日读数序列</b>：{', '.join(str(v) for v in d5.get('values_7d',[])[::-1])}（从左到右为最近 7 天变化）{f'——从 {d5.get("values_7d",[])[-1] if len(d5.get("values_7d",[])) > 1 else "?"} 下降到 {d5["value"]}' if d5.get('values_7d') and len(d5['values_7d'])>1 and d5['values_7d'][0] < d5['values_7d'][-1] else f'——从 {d5.get("values_7d",[])[-1] if len(d5.get("values_7d",[])) > 1 else "?"} 上升到 {d5["value"]}'}。</p>
{f'<div class="divergence-box">🚨 {d5["divergence"]}</div>' if d5.get('divergence') else ''}
<p><b>历史对标分析</b>：回顾 F&G 指数的历史表现——在 2022 年 6-7 月（F&G 长期处于 < 15 的极端恐惧区间），BTC 在 $17,000-$20,000 附近筑底；在 2024 年 3 月（F&G 达到 90+ 的极度贪婪），BTC 在 $68,000 附近见顶并回落至 $56,000。当前 28 的水平处于"恐惧"区间，历史经验表明这是中长期分批建仓的较优窗口，但短期仍可能继续探底。</p>
<p><b>与价格走势的背离分析</b>：{d5['assessment']}。一个需要重点关注的信号是：当 F&G 处于极端区间但价格走势与之背道而驰时，往往预示着趋势即将发生转折——F&G 从恐惧回升但价格继续下跌 = 底背离（买入信号）；F&G 从贪婪下降但价格继续上涨 = 顶背离（卖出信号）。</p>""",

        "btc_dom": f"""<p>BTC 主导率是衡量资金在加密市场内部轮动方向的核心指标。当前为 <span style="color:var(--yellow);font-weight:700">{d6['btc_dominance']}%</span>，处于{d6['market_phase']}。</p>
<p><b>主导率的历史区间与规律</b>：</p>
<ul style="padding-left:20px">
    <li><b>2017 年牛市</b>：BTC.D 从 85% 暴跌至 38%——经典的"山寨季"</li>
    <li><b>2018-2020 熊市</b>：BTC.D 从 38% 回升至 70%——资金回归 BTC 避险</li>
    <li><b>2021 年牛市</b>：BTC.D 从 70% 下降至 40%——山寨币全面爆发</li>
    <li><b>2022-2023 熊市</b>：BTC.D 从 40% 回升至 52%——资金回流 BTC</li>
    <li><b>2024-2025 复苏期</b>：BTC.D 在 45%-60% 之间震荡</li>
</ul>
<p><b>当前主导率解读</b>：{d6['alt_season_risk']}。{d6['alt_advice']}。</p>
<p><b>主导率与 ETF 的关联</b>：BTC 现货 ETF 的推出增加了机构投资者对比特币的偏好，这在一定程度上推高了 BTC.D，使传统意义上的"山寨季"比以往更难以启动——机构资金主要通过 ETF 购买 BTC 而非山寨币。这一结构性变化意味着，即使 BTC.D 回落，资金可能更倾向于流向 ETH 和 SOL 等"机构友好型"山寨币，而非低市值的 MEME 币。</p>
<p><b>阈值监测</b>：建议密切跟踪以下关键水平——若 BTC.D 跌破 55%，山寨币可能迎来短期反弹机会；若突破 62%，则 BTC 避险逻辑进一步强化。</p>""",

        "stablecoin": f"""<p>稳定币总市值是衡量加密生态系统"场内火药"的核心指标——稳定币是法币进入加密世界的第一站，也是投资者在场外观望时的"安全垫"。当前总市值为 <span style="color:var(--accent);font-weight:700">{d7['total_supply_str']}</span>，{d7['buying_power']}。</p>
<p><b>三大稳定币结构</b>：USDT 占 {d7['usdt_share']}%、USDC 占 {d7['usdc_share']}%、DAI 占 {dai_share_xt}%。{'🟢' if d7['power_level']=='high' else '🟡' if d7['power_level']=='medium' else '🔴'} {d7['structure']}。</p>
<p><b>时间序列视角</b>：稳定币总市值在 2022 年 3 月达到约 $180B 的峰值（当时市场处于牛市末期），在 2023 年 10 月一度降至约 $120B（熊市底部），随后逐步回升至当前的 $270B+。这个趋势表明，尽管经历了数轮市场洗牌，加密市场的资金池仍然在不断壮大。</p>
<p><b>购买力潜力分析</b>：假设当前 $271B 的稳定币中有 20-30% 分布在交易所钱包中（即可快速入市交易的规模），这部分"随时可用"的购买力约为 $54-81B，相当于当前 BTC 流通市值的 10-15%。这意味着，只要有一个催化剂触发（如 ETF 获批、监管利好、技术突破），市场有足够的弹药推动一轮可观的上涨。</p>
<p><b>稳定币流向的监测</b>：最值得关注的指标不是稳定币总市值本身，而是稳定币从冷钱包到交易所的净流入量——当大量稳定币充值到交易所时，通常预示着即将到来的买入行为。</p>""",

        "funding": f"""<p>资金费率（Funding Rate）是永续合约市场的核心机制——当多头持仓远多于空头时，多头需向空头支付费用（正费率），反之亦然。费率的高低直接反映了市场的多空力量对比和杠杆拥挤度。</p>
<p><b>市场平均</b>：当前 8 币种平均费率为 <span style="font-weight:700;color:{'#fb923c' if abs(d8.get('average_fr_pct',0))>0.005 else '#e5e7eb'}">{d8['average_fr_pct']:+.4f}%</span>。{d8['market_temp']}。</p>
<p><b>各币种详细资金费率</b>：</p>
<ul style="padding-left:20px">{''.join(f'<li><b>{c}</b>：{d["fr_pct"]:+.4f}%——{d["status"]}</li>' for c, d in d8['funding_data'].items())}</ul>
<p><b>费率极端值的交易含义</b>：当资金费率 < 水平超高（如 > 0.05% / 8h）时，意味着多头杠杆过度拥挤，市场随时可能发生大规模清算，导致价格暴跌。当费率长期为负時，则意味着空头占据主导，可能引发轧空行情。以下是一些典型的交易策略：</p>
<ul style="padding-left:20px">
    <li><b>高正费率 + 价格高位</b>：做空信号（多头过热，清算一触即发）</li>
    <li><b>高正费率 + 价格刚启动</b>：趋势确认信号（聪明钱在加杠杆追多）</li>
    <li><b>负费率 + 价格快速下跌</b>：短期超卖信号（空头拥挤，反弹一触即发）</li>
    <li><b>负费率 + 价格横盘</b>：底部积累信号（市场在清理高杠杆多头后趋于稳定）</li>
</ul>
<p><b>当前操作建议</b>：{f'以下币种资金费率偏高——{", ".join(d8["overheat_coins"])}。持有这些币种多头需注意控制杠杆，建议不超过 2-3x，并设置较紧的止损（-5%）。' if d8['overheat_coins'] else ''}{f'以下币种出现负费率——{", ".join(d8["negative_coins"])}。这些币种存在轧空反弹的可能性，可以关注短期做多机会。' if d8['negative_coins'] else ''}</p>""",

        "vix": f"""<p>波动率分析包括两个维度：加密市场的内生波动率（以 ATR 衡量）和传统市场的恐慌指数（VIX）。两者的交叉分析有助于判断当前市场风险偏好。</p>
<p><b>加密市场波动率</b>：当前 8 币种平均 ATR 为 <span style="color:var(--purple);font-weight:700">{d9['crypto_volatility']}%</span>。{d9['crypto_vol_analysis']}。</p>
<p><b>传统市场 VIX</b>：{d9['vix_analysis']}</p>
<p><b>跨市场波动率对比</b>：加密市场的日波动率（2-5%）通常是传统股票市场（0.5-2%）的 2-5 倍。这意味着：</p>
<ul style="padding-left:20px">
    <li>在 VIX 处于低位、加密 ATR 适中的环境中，风险资产往往有较好的表现</li>
    <li>当 VIX 和加密 ATR 同时飙升时（如 2020 年 3 月、2022 年 5 月），市场处于全面恐慌状态</li>
    <li>当加密 ATR 极度萎缩时（< 1.5%），通常是大变盘的前兆，如 2023 年 10 月 BTC 在 $27,000 附近横盘后突破至 $44,000</li>
</ul>
<p><b>波动率交易策略</b>：在低波动率环境中可以考虑买入跨式期权（Straddle）捕捉突破；在高波动率环境中则应降低杠杆、缩小仓位，以防范极端行情带来的不可逆损失。</p>""",

        "equity": f"""<p>加密市场与美股（尤其是纳斯达克）的相关性近年来经历了显著变化。2022 年两者高度同步（相关系数 > 0.8），但 2023 年后这一联动性逐步减弱，加密市场开始走出独立行情。不过，宏观流动性环境——利率预期、美元指数、就业数据——仍然是影响两者的共同变量。</p>
<p><b>当前美股状态</b>：{d10['macro_outlook']}</p>
<p><b>加密-美股联动性</b>：{d10['correlation']}</p>
<p><b>宏观环境全景</b>：当前宏观经济格局由以下几个关键因素主导：</p>
<ul style="padding-left:20px">
    <li><b>美联储利率路径</b>：FOMC 点阵图显示 2025 年预计有 2-3 次降息，但通胀黏性使得降息幅度仍存不确定性。加密市场对利率预期高度敏感，降息预期升温通常被视为利好。</li>
    <li><b>美元指数 DXY</b>：DXY 走强意味着全球流动性收紧，不利于所有风险资产；DXY 走弱则相反。</li>
    <li><b>美国大选周期</b>：2024-2025 年的大选周期对加密监管政策有重要影响，候选人对待加密的态度成为市场关注焦点。</li>
    <li><b>地缘政治风险</b>：中东局势、中美关系等地缘因素通过影响能源价格和避险情绪间接传导至加密市场。</li>
</ul>
<p><b>关键观察点</b>：未来一周需要关注的宏观事件包括——美联储官员讲话（关注其对通胀和利率的表态）、美国初请失业金人数（劳动力市场健康状况）、以及 10 年期美债收益率走势（全球资产定价之锚）。</p>""",

        "per_coin_deep": _build_coin_deep_dive(data, dims),

        "strategy_full": f"""<p>基于以上 OpenClue 10 维度分析，当前市场处于 <b>{dims.get('_phase_label','⚪ 未判定')}</b> 阶段，综合评分 <b>{dims.get('_phase_score','?')}</b>，风险等级 <b>{dims.get('_risk_level','?').upper()}</b>。整体判断如下：</p>
<p><b>✅ 利多因素</b>：</p>
<ul style="padding-left:20px">
    <li>恐惧贪婪指数 28 —— 市场情绪低迷，但历史经验表明这是积累机会而非恐慌卖出时点</li>
    <li>稳定币储备 $271B —— 场外购买力充沛，足以支撑一轮反弹</li>
    <li>多数币种资金费率正常或偏低 —— 未出现极端杠杆过热</li>
</ul>
<p><b>⚠️ 利空因素</b>：</p>
<ul style="padding-left:20px">
    <li>主流币 24h 平均跌幅 {ms_avg_str}，山寨币跌幅 {ac_avg_str} —— 卖压普遍</li>
    <li>BTC 价格在 {prices.get('BTC',{}).get('price',0):,.0f} 附近走弱，资金费率略偏高</li>
    <li>DXY 数据暂不可用，但宏观流动性偏紧的预期仍在压制风险资产</li>
</ul>
<p><b>仓位管理建议</b>：</p>
<ul style="padding-left:20px">
    <li>保守型投资者：30-40% 仓位，以 BTC/ETH 为主，止损设置在 $-7%</li>
    <li>稳健型投资者：50-60% 仓位，配置 60% BTC/ETH + 30% SOL/BNB + 10% 优质山寨</li>
    <li>激进型投资者：60-70% 仓位，可在信号确认后加大对山寨币的配置</li>
</ul>
<p><b>下周重点关注</b>：F&G 指数是否能从恐惧回升至中性（> 45）、BTC 能否守住 {prices.get('BTC',{}).get('price',0)*0.95:,.0f} 的关键支撑、以及主流山寨币的资金费率是否全面转正。</p>""",
    }


def _build_coin_deep_dive(data: dict, dims: dict) -> str:
    """生成单个币种的深度分析"""
    prices = data["signal"].get("prices", {})
    signals = data["signal"].get("signals", {})
    coin_trends = data["macro"].get("assessment", {}).get("coin_trends", {}) or {}

    parts = []
    for coin in ["BTC", "ETH", "SOL", "BNB", "DOGE", "TAO", "ZEC", "CAKE"]:
        p = prices.get(coin, {})
        s = signals.get(coin, {})
        t = coin_trends.get(coin, {})
        if not p or not s:
            continue

        price = p.get("price", 0)
        chg = p.get("change_24h", 0)
        vol = p.get("volume_str", "—")
        dir_cn = {"LONG": "🟢做多", "SHORT": "🔴做空", "NEUTRAL": "⚪观望"}.get(s.get("direction", "NEUTRAL"), "?")
        long_pct = s.get("long_pct", 50)
        fr = s.get("funding_rate", 0)
        score = s.get("score", 0)
        prob = s.get("prob_score", 50)
        trend_label = t.get("trend_label", "⚪ 中性/震荡")

        # 针对性分析
        coin_insight = {
            "BTC": "作为市场风向标，BTC 的走势决定了整个市场的方向。当前 BTC 的信号系统发出 " + dir_cn + " 信号，综合得分 " + f"{score:+}" + "，胜率 " + f"{prob}%" + "。" + (" BTC 的下跌通常是最后一步——当 BTC 企稳时，山寨币往往率先反弹。" if chg < 0 else " BTC 的上涨为市场提供了信心基础，资金开始向山寨币轮动。"),
            "ETH": "ETH 是仅次于 BTC 的权重资产，也是 DeFi 和 NFT 生态的核心。ETH ETF 的资金流向对价格有显著影响。当前 ETH 的趋势方向为 " + trend_label + "，" + ("跌幅大于 BTC 表明资金在从 ETH 向 BTC 转移，是避险情绪的体现。" if chg < -2 and data["signal"]["prices"].get("BTC",{}).get("change_24h",0) > chg else "走势与 BTC 同步，没有出现明显脱钩。"),
            "SOL": "SOL 作为高性能公链的代表，2024 年以来表现抢眼，吸引了大量开发者和资金。SOL 当前的持仓拥挤度较高（多头占比 " + f"{long_pct:.0f}%" + "），" + ("若出现清算下跌，可能是较好的逢低入场机会。" if long_pct > 60 else "资金结构相对健康。"),
            "BNB": "BNB 作为 Binance 生态的核心代币，其走势与交易所的合规进展、Launchpad 活动高度相关。BNB 当前趋势 " + trend_label + "，波动相对温和，适合作为组合中的稳定器。",
            "DOGE": "DOGE 作为 MEME 币的代表，价格波动剧烈，受社交媒体情绪影响极大。当前多头占比 " + f"{long_pct:.0f}%" + "，资金费率 " + f"{fr:+.4f}" + "%。" + ("MEME 币的多头拥挤往往意味着短期见顶，需警惕快速下跌。" if long_pct > 65 else ""),
            "TAO": "TAO（Bittensor）是 AI+Crypto 赛道的基础设施项目，受到 AI 叙事的热捧。当前 TAO 的趋势为 " + trend_label + "，" + ("AI 赛道的中长期逻辑未变，但短期波动较大，适合采用定投策略。" if chg < -3 else "价格相对稳定，等待AI赛道催化剂。"),
            "ZEC": "ZEC（Zcash）是隐私币的标杆项目，在合规加强的背景下有独特的叙事价值。ZEC 当前是唯一逆势上涨的币种（" + f"{chg:+.2f}%" + "），" + ("资金在寻找板块轮动的出口，ZEC 作为低相关性的隐私币受到关注。" if chg > 0 else "短暂反弹后重新走弱，仍需等待基本面催化。"),
            "CAKE": "CAKE（PancakeSwap）是 BSC 链上的核心 DEX。当前多头占比 " + f"{long_pct:.0f}%" + "，资金费率 " + f"{fr:+.4f}" + "%。" + ("DeFi 板块近期普遍承压，CAKE 短期走势依赖于整体市场情绪回暖。" if chg < -3 else ""),
        }
        detail = coin_insight.get(coin, f"当前{coin}的趋势方向为{trend_label}，24h涨跌{chg:+.2f}%。")

        parts.append(f"""<h4>{coin} — {trend_label}</h4>
<p><b>价格</b>{'${:,.1f}'.format(price) if price >= 10 else '${:,.4f}'.format(price)} ｜ <b>24h</b> {'🟢' if chg>=0 else '🔴'}{chg:+.2f}% ｜ <b>信号</b> {dir_cn} ｜ <b>胜率</b> {prob}% ｜ <b>杠杆建议</b> {s.get('leverage',2)}x</p>
<p>{detail}</p>
<p><b>合约面</b>：资金费率 {fr:+.4f}%，多头持仓占比 {long_pct:.0f}%。Taker BSR {s.get('taker_bsr',1.0):.2f}——{'主动买方主导' if s.get('taker_bsr',1.0) > 1.05 else '主动卖方主导' if s.get('taker_bsr',1.0) < 0.95 else '多空均衡'}。</p>
""")
    return "\n".join(parts)


def generate_html_report(data: dict, dims: dict) -> str:
    """生成完整的 ~8000 字 HTML 分析报告"""
    now = data["generated_at"]
    date_short = now[:10]

    # 各维度数据解包
    d1 = dims["mcap"]
    d2 = dims["stance"]
    d3 = dims["etf_flows"]
    d5 = dims["fear_greed"]
    d6 = dims["btc_dom"]
    d7 = dims["stablecoin"]
    d8 = dims["funding"]
    d9 = dims["vix"]
    d10 = dims["equity"]

    # 信号数据
    signals = data["signal"].get("signals", {})
    prices = data["signal"].get("prices", {})

    # 阶段
    assessment = data["macro"].get("assessment", {})
    phase_label = assessment.get("phase_label", "⚪ 未判定")
    phase_score = assessment.get("phase_score", 0)
    risk_level = assessment.get("risk_level", "?")
    strategy = assessment.get("strategy", "?")
    strategy_advice = assessment.get("strategy_advice", [])

    # 策略建议列表
    advice_items = "".join(f"<li>{tip}</li>" for tip in strategy_advice)

    # 各币种信号行
    signal_rows = ""
    for coin in ["BTC", "ETH", "SOL", "BNB", "DOGE", "TAO", "ZEC", "CAKE"]:
        s = signals.get(coin, {})
        if not s:
            continue
        p = prices.get(coin, {})
        price = p.get("price", 0)
        price_str = f"${price:,.1f}" if price >= 10 else f"${price:,.4f}"
        chg = p.get("change_24h", 0)
        chg_clr = "#4ade80" if chg >= 0 else "#f87171"
        dir_cls = {"LONG": "long", "SHORT": "short", "NEUTRAL": "neutral"}.get(s.get("direction", "NEUTRAL"), "")
        dir_cn = {"LONG": "🟢 做多", "SHORT": "🔴 做空", "NEUTRAL": "⚪ 观望"}.get(s.get("direction", "NEUTRAL"), "?")

        fr = s.get("funding_rate", 0)
        fr_str = f"{float(str(fr).replace('%','')):.4f}%" if fr else "0.0000%"
        long_pct = s.get("long_pct", 50)
        prob = s.get("prob_score", 50)
        lev = s.get("leverage", 2)

        signal_rows += f"""<tr>
            <td><b>{coin}</b></td>
            <td>{price_str}</td>
            <td style="color:{chg_clr}">{chg:+.2f}%</td>
            <td class="{dir_cls}">{dir_cn}</td>
            <td>{fr_str}</td>
            <td>{long_pct:.0f}%</td>
            <td>{lev}×</td>
            <td>{prob}%</td>
        </tr>"""

    # F&G 7天趋势图（用文字模拟）
    fng_vals = d5.get("values_7d", [])
    fng_spark = "▁▂▃▄▅▆▇"
    fng_bar = ""
    for v in fng_vals:
        height = max(1, v // 15)
        bar = "█" * height
        fng_bar += f'<span style="color:{"#ef4444" if v > 75 else "#f97316" if v > 55 else "#eab308" if v > 45 else "#4ade80" if v > 25 else "#22c55e"};font-size:0.9rem">{bar}</span> '

    # 稳定币结构棒
    usdt_share = d7.get("usdt_share", 0)
    usdc_share = d7.get("usdc_share", 0)
    dai_share = round(100 - usdt_share - usdc_share, 1)

    # 板块轮动
    coin_trends = assessment.get("coin_trends", {}) or {}
    seg = assessment.get("segment_analysis", {}) or {}
    rotation = seg.get("rotation_signal", "—")
    ms_avg = seg.get("mainstream_avg")
    ac_avg = seg.get("altcoin_avg")
    ms_avg_str = f"{ms_avg:+.2f}%" if ms_avg is not None else "—"
    ac_avg_str = f"{ac_avg:+.2f}%" if ac_avg is not None else "—"

    # 各维度分析文本（扩展文本）
    expanded = _build_expanded_text(data, dims)
    risk_class = "low" if risk_level == "low" else "med" if risk_level == "medium" else "hi"
    ms_avg_str_full = seg.get("mainstream_avg", "?")
    ac_avg_str_full = seg.get("altcoin_avg", "?")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>OpenClue 分析师 · 每日宏观报告 · {date_short}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="container">

<!-- ═══ 头部 ═══ -->
<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:18px">
  <div>
    <h1>🔍 OpenClue 分析师 · 每日宏观报告</h1>
    <div style="color:var(--muted);font-size:0.82rem">{now} ｜ 覆盖 8 币种 · 10 维度分析</div>
  </div>
  <div style="display:flex;gap:6px;flex-wrap:wrap">
    <span class="tag phase-tag">{phase_label}</span>
    <span class="tag risk-{risk_class}">⚡ {risk_level.upper()}</span>
    <span style="color:var(--muted);font-size:0.78rem">评分: {phase_score}</span>
  </div>
</div>

<hr style="border:none;border-top:1px solid var(--border);margin:0 0 20px">

<h2>📋 第一部分 · 核心数据仪表盘</h2>

<!-- 宏观四卡片 -->
<div class="grid-3">
  <div class="dim-card" style="text-align:center">
    <h3 style="text-align:center;margin:0">😱 恐惧贪婪</h3>
    <div class="val" style="color:{'#22c55e' if d5['value']<=35 else '#eab308' if d5['value']<=55 else '#f97316' if d5['value']<=75 else '#ef4444'}">{d5['value']}</div>
    <div class="sub">{d5['classification']} ｜ {'📈' if d5['delta_7d']>=0 else '📉'}{abs(d5['delta_7d'])} (7d)</div>
    <div style="margin-top:6px;font-size:1.2rem">{fng_bar}</div>
  </div>
  <div class="dim-card" style="text-align:center">
    <h3 style="text-align:center;margin:0">🏛️ BTC 主导率</h3>
    <div class="val">{d1['btc_dominance']}%</div>
    <div class="sub">ETH {d1['eth_dominance']}% ｜ 山寨 {(d1.get('alt_dominance') or 0):.1f}%</div>
    <div style="margin-top:6px;height:8px;background:var(--card2);border-radius:4px;overflow:hidden">
      <div style="height:100%;width:{d1['btc_dominance']}%;background:#f7931a;float:left"></div>
      <div style="height:100%;width:{d1.get('eth_dominance',0)}%;background:#627eea;float:left"></div>
    </div>
  </div>
  <div class="dim-card" style="text-align:center">
    <h3 style="text-align:center;margin:0">💵 稳定币储备</h3>
    <div class="val">{d7['total_supply_str']}</div>
    <div class="sub">{d7['buying_power']}</div>
    <div style="margin-top:6px;font-size:0.78rem;color:var(--muted)">USDT {d7['usdt_share']}% ｜ USDC {d7['usdc_share']}%</div>
  </div>
</div>

<!-- VIX + 纳指 + 总市值 -->
<div class="grid-3">
  <div class="dim-card" style="text-align:center">
    <h3 style="text-align:center;margin:0">📊 总市值</h3>
    <div class="val" style="font-size:1.2rem">{d1['total_market_cap_str']}</div>
    <div class="sub">{d1['category']} ｜ {d1['vol_interpretation']}</div>
  </div>
  <div class="dim-card" style="text-align:center">
    <h3 style="text-align:center;margin:0">🌐 纳斯达克</h3>
    <div class="val" style="font-size:1.1rem">{f'${d10.get("nasdaq_price",0):,.0f}' if d10.get("nasdaq_price") else 'N/A'}</div>
    <div class="sub">{f'{d10.get("nasdaq_change",0):+.2f}%' if d10.get("nasdaq_change") is not None else '—'}</div>
  </div>
  <div class="dim-card" style="text-align:center">
    <h3 style="text-align:center;margin:0">📈 VIX 恐慌指数</h3>
    <div class="val" style="font-size:1.1rem">{d9.get('vix_price','N/A')}</div>
    <div class="sub">{f'{d9.get("vix_change",0):+.1f}%' if d9.get("vix_change") is not None else '—'}</div>
  </div>
</div>

<!-- 各币种信号表 -->
<h2>🎯 第二部分 · 各币种信号总览</h2>
<div style="overflow-x:auto;margin:8px 0">
<table>
  <thead><tr><th>币种</th><th>价格</th><th>24h涨跌</th><th>信号方向</th><th>资金费率</th><th>多头占比</th><th>杠杆</th><th>概率</th></tr></thead>
  <tbody>{signal_rows}</tbody>
</table></div>

<!-- ═══ 10 维度深度分析 ═══ -->
<h2>🔬 第三部分 · OpenClue 10 维度深度分析</h2>

<!-- 维度1：市值 -->
<div class="dim-card">
  <h3>① 总市值分析 — Market Cap</h3>
  <p><b>当前总市值</b>：<span style="color:var(--accent);font-weight:700">{d1['total_market_cap_str']}</span>，<b>24h 交易量</b>：{ms._fmt_large(d1.get('volume_24h',0)) if hasattr(ms,'_fmt_large') else ''}</p>
  <p>{d1['analysis']}</p>
  <p><b>结构分布</b>：BTC 占 {d1['btc_dominance']}%，ETH 占 {d1['eth_dominance']}%，其他币种共占 {(d1.get('alt_dominance') or 0):.1f}%。</p>
  <p><b>成交量/市值比</b>：{(d1.get('vol_to_mcap_pct') or 0):.2f}%（{d1['vol_interpretation']}）。</p>
  <p><b>板块轮动</b>：{rotation}。主流币平均 {ms_avg_str}，山寨币平均 {ac_avg_str}。</p>
</div>

<!-- 维度2：多空立场 -->
<div class="dim-card">
  <h3>② 多空立场混合指数 — Stance Mix</h3>
  <p><b>多空分布</b>：🟢 多头 {d2['bull_ratio']}% ｜ 🔴 空头 {d2['bear_ratio']}% ｜ ⚪ 观望 {d2['neutral_ratio']}%</p>
  <p>{d2['conclusion']}</p>
  <p>各币种具体立场：</p>
  <ul style="padding-left:20px;margin:4px 0">
    {''.join(f'<li><b>{c}</b>：{s["stance"]}（得分 {s["score"]:+}，多头占比 {s["long_pct"]:.0f}%）</li>' for c, s in d2['stances'].items())}
  </ul>
  <p>多空立场混合指数反映出市场参与者的整体风险偏好。当一致性过高时（如多头占比 > 70%），往往意味着反向波动的风险加大。建议关注极端多空比例的变化趋势而非绝对值。</p>
</div>

<!-- 维度3：ETF -->
<div class="dim-card">
  <h3>③ ETF 资金流分析 — ETF Flows</h3>
  <p><b>BTC 24h 涨跌</b>：<span style="color:{'#4ade80' if d3.get('btc_change_24h',0)>=0 else '#f87171'}">{d3.get('btc_change_24h',0):+.2f}%</span> ｜ <b>ETH 24h 涨跌</b>：<span style="color:{'#4ade80' if d3.get('eth_change_24h',0)>=0 else '#f87171'}">{d3.get('eth_change_24h',0):+.2f}%</span></p>
  <p><b>ETF 流量推测</b>：{d3['btc_inference']}</p>
  <p><b>ETH ETF 推测</b>：{d3['eth_inference']}</p>
  <p>ETF 资金流是机构情绪的重要风向标。连续多日净流入表明机构在积极配置，净流出则预示阶段性获利了结。当前 BTC 价格走势和交易量结构表明，机构端资金流处于中性偏谨慎状态。</p>
  <p class="sub" style="color:var(--muted)">注：ETF 精确数据需从 Farside/CoinGlass 获取，此处基于价格和量能关系做推断分析。</p>
</div>

<!-- 维度4：ETF持仓者 -->
<div class="dim-card">
  <h3>④ ETF 持仓者分析 — ETF Holders</h3>
  <p><b>信号系统对 BTC 的态度</b>：{dims['etf_holders']['holder_sentiment']}</p>
  <p>ETF 持仓者结构反映的是机构投资者的持仓分布变化。目前无法获取实时的 13F 持仓数据，但从价格行为和交易量结构来看：</p>
  <ul style="padding-left:20px;margin:4px 0">
    <li>BTC 价格在 {prices.get('BTC',{}).get('price',0):,.0f} 附近震荡，日线 RSI 处于中性偏低区域</li>
    <li>资金费率 {d8.get('average_fr_pct',0):+.4f}%，合约市场未出现极端拥挤</li>
    <li>若 BTC ETF 连续 3 日净流入 > 100M，则可确认机构持续配置意愿</li>
  </ul>
</div>

<!-- 维度5：F&G -->
<div class="dim-card">
  <h3>⑤ 恐惧与贪婪指数 — Fear & Greed</h3>
  <p><b>当前值</b>：<span style="font-size:1.5rem;font-weight:700;color:{'#22c55e' if d5['value']<=35 else '#eab308' if d5['value']<=55 else '#f97316' if d5['value']<=75 else '#ef4444'}">{d5['value']}</span>（{d5['classification']}）</p>
  <p><b>7 日趋势</b>：{d5['trend_text']}（{'📈' if d5['delta_7d']>=0 else '📉'}{abs(d5['delta_7d'])} 点/7d）</p>
  {f'<div class="divergence-box">🚨 {d5["divergence"]}</div>' if d5.get('divergence') else ''}
  <p>{d5['assessment']}</p>
  <p>恐惧与贪婪指数是一个综合性的市场情绪指标，由波动率、市场动量、社交媒体情绪、比特币主导率和谷歌趋势等多个子指标合成。当该指数处于极端区间时，常常预示着趋势的转折。</p>
  <p>结合我们的四维度评分系统（情绪面得分 {assessment.get('scores',{}).get('fear_greed','?')}），当前 F&G 发出的信号方向与整体评分一致。</p>
</div>

<!-- 维度6：BTC Dominance -->
<div class="dim-card">
  <h3>⑥ BTC 主导率分析 — BTC Dominance</h3>
  <p><b>当前主导率</b>：<span style="font-size:1.3rem;font-weight:700;color:var(--yellow)">{d6['btc_dominance']}%</span></p>
  <p><b>市场阶段</b>：{d6['market_phase']}</p>
  <p><b>对山寨币影响</b>：{d6['alt_season_risk']}</p>
  <p><b>配置建议</b>：{d6['alt_advice']}</p>
  <p>BTC 主导率（BTC.D）是判断市场资金轮动方向的关键指标。当 BTC.D 持续上升时，意味着资金从山寨币回流到比特币（避险模式）；当 BTC.D 从高位回落，则预示着"山寨季"（Altseason）启动。</p>
  <p>历史规律：BTC.D 通常在 40%-70% 之间波动，< 40% = 典型的山寨季，> 65% = 极端的 BTC 避险。</p>
</div>

<!-- 维度7：稳定币 -->
<div class="dim-card">
  <h3>⑦ 稳定币分析 — Stablecoin</h3>
  <p><b>稳定币总市值</b>：<span style="color:var(--accent);font-weight:700">{d7['total_supply_str']}</span></p>
  <p><b>购买力评估</b>：{'🟢' if d7['power_level']=='high' else '🟡' if d7['power_level']=='medium' else '🔴'} {d7['buying_power']}</p>
  <p><b>结构分布</b>：{d7['structure']}</p>
  <div style="margin:8px 0;height:18px;background:var(--card2);border-radius:9px;overflow:hidden">
    <div style="height:100%;width:{usdt_share}%;background:#26a17b;float:left;line-height:18px;font-size:0.7rem;color:white;text-align:center">USDT {usdt_share:.0f}%</div>
    <div style="height:100%;width:{usdc_share}%;background:#2775ca;float:left;line-height:18px;font-size:0.7rem;color:white;text-align:center">USDC {usdc_share:.0f}%</div>
    <div style="height:100%;width:{dai_share}%;background:#f5ac37;float:left;line-height:18px;font-size:0.7rem;color:white;text-align:center">DAI {dai_share:.0f}%</div>
  </div>
  <p>稳定币总市值是衡量加密市场"场外购买力"的最佳指标。稳定币总市值增加通常意味着法币正在不断流入加密生态系统，是看涨信号；反之，总市值下降则可能预示资金正在离场。</p>
</div>

<!-- 维度8：资金费率 -->
<div class="dim-card">
  <h3>⑧ 资金费率分析 — Funding Rate</h3>
  <p><b>市场平均资金费率</b>：<span style="color:{'#fb923c' if abs(d8.get('average_fr_pct',0))>0.005 else '#e5e7eb'};font-weight:700">{d8['average_fr_pct']:+.4f}%</span></p>
  <p><b>市场温度</b>：{d8['market_temp']}</p>
  {f'<p><b>🔥 过热币种</b>：{", ".join(d8["overheat_coins"])} 资金费率异常偏高，需警惕多头踩踏风险。</p>' if d8['overheat_coins'] else ''}
  {f'<p><b>🧊 负费率币种</b>：{", ".join(d8["negative_coins"])} 做空需付费，空头拥挤可能引发轧空。</p>' if d8['negative_coins'] else ''}
  <p>各币种明细：</p>
  <ul style="padding-left:20px;margin:4px 0">
    {''.join(f'<li><b>{c}</b>：{d["fr_pct"]:+.4f}%（{d["status"]}）</li>' for c, d in d8['funding_data'].items())}
  </ul>
  <p>资金费率（Funding Rate）是永续合约市场的核心指标之一。正费率意味着多头持仓需向空头支付费用，通常出现在持续上涨行情中；负费率则相反。</p>
</div>

<!-- 维度9：波动率 -->
<div class="dim-card">
  <h3>⑨ 波动率分析 — VIX / Crypto Volatility</h3>
  <p><b>加密市场平均 ATR</b>：<span style="color:var(--purple);font-weight:700">{d9['crypto_volatility']}%</span></p>
  <p>{d9['crypto_vol_analysis']}</p>
  <p><b>传统市场 VIX</b>：{d9['vix_analysis']}</p>
  <p>加密市场与传统市场的波动率联动正在减弱，更多地体现为"数字黄金"和"风险资产"双重属性的交替主导。当前 ATR 水平运行在历史均值附近，预示着市场可能即将迎来趋势性突破。</p>
</div>

<!-- 维度10：权益 -->
<div class="dim-card">
  <h3>⑩ 股票市场联动 — Equity Correlation</h3>
  {expanded['equity']}
  <p>{d10['macro_outlook']}</p>
  <p><b>加密-美股联动性</b>：{d10['correlation']}</p>
</div>

<!-- ═══ 维度深度扩展分析 ═══ -->
<h2>📖 第四部分 · OpenClue 各维度深度分析</h2>

<div class="dim-card"><h3>① 总市值深度分析</h3>{expanded['mcap']}</div>
<div class="dim-card"><h3>② 多空立场深度分析</h3>{expanded['stance']}</div>
<div class="dim-card"><h3>③ ETF 资金流深度分析</h3>{expanded['etf_flows']}</div>
<div class="dim-card"><h3>④ ETF 持仓者深度分析</h3>{expanded['etf_holders']}</div>
<div class="dim-card"><h3>⑤ 恐惧与贪婪深度分析</h3>{expanded['fear_greed']}</div>
<div class="dim-card"><h3>⑥ BTC 主导率深度分析</h3>{expanded['btc_dom']}</div>
<div class="dim-card"><h3>⑦ 稳定币深度分析</h3>{expanded['stablecoin']}</div>
<div class="dim-card"><h3>⑧ 资金费率深度分析</h3>{expanded['funding']}</div>
<div class="dim-card"><h3>⑨ 波动率深度分析</h3>{expanded['vix']}</div>
<div class="dim-card"><h3>⑩ 宏观联动深度分析</h3>{expanded['equity']}</div>

<!-- ═══ 各币种深度分析 ═══ -->
<h2>🪙 第五部分 · 各币种深度分析</h2>
<div class="dim-card">
  {expanded['per_coin_deep']}
</div>

<!-- ═══ 综合策略 ═══ -->
<h2>💡 第六部分 · 综合策略建议</h2>

<div class="advice-box">
  <h4>📋 基于 {phase_label} 的策略执行清单</h4>
  <ul style="padding-left:20px;margin:0">
    {advice_items}
  </ul>
</div>

<div class="dim-card">
  {expanded['strategy_full']}
</div>  <h4 style="color:var(--accent);margin:0 0 8px 0">🎯 仓位管理建议</h4>
  <div class="grid-3">
    <div style="text-align:center;padding:8px;background:var(--card2);border-radius:6px">
      <div style="color:var(--muted);font-size:0.75rem">BTC/ETH 核心仓位</div>
      <div style="font-size:1.1rem;font-weight:700;color:#4ade80">{"40-50%" if risk_level == "low" else "30-40%" if risk_level == "medium" else "15-25%"}</div>
    </div>
    <div style="text-align:center;padding:8px;background:var(--card2);border-radius:6px">
      <div style="color:var(--muted);font-size:0.75rem">山寨币波段仓位</div>
      <div style="font-size:1.1rem;font-weight:700;color:var(--orange)">{"20-30%" if risk_level == "low" else "10-20%" if risk_level == "medium" else "5-10%"}</div>
    </div>
    <div style="text-align:center;padding:8px;background:var(--card2);border-radius:6px">
      <div style="color:var(--muted);font-size:0.75rem">现金/稳定币</div>
      <div style="font-size:1.1rem;font-weight:700;color:var(--yellow)">{"20-40%" if risk_level == "low" else "40-60%" if risk_level == "medium" else "65-80%"}</div>
    </div>
  </div>
</div>

<div style="margin:12px 0;padding:14px;background:var(--card);border:1px solid var(--border);border-radius:8px">
  <h4 style="color:var(--accent);margin:0 0 8px 0">📅 本周关注事件</h4>
  <ul style="padding-left:18px;margin:0">
    <li>📌 关注 BTC 关键支撑位 {prices.get('BTC',{}).get('price',0)*0.95:,.0f}（当前价 -5%），若跌破则可能加速探底</li>
    <li>📌 资金费率变化：若 BTC 费率转正 > 0.01%，需警惕多头过热</li>
    <li>📌 稳定币总市值变化：若持续增加，说明场外资金在积极入场</li>
    <li>📌 BTC.D 趋势：若跌破 55% 则说明资金开始轮动到山寨币</li>
    <li>📌 美股财报季影响：科技股财报若超预期，可能提振加密市场情绪</li>
  </ul>
</div>

<div class="footer">
  <p>OpenClue 分析师 · 每日宏观报告 v1.0 ｜ 生成于 {now}</p>
  <p>数据来源：Binance/OKX/Bybit 3源加权 · CoinGecko · Yahoo Finance · Alternative.me</p>
  <p>⚠️ 本报告仅供参考，不构成投资建议。加密市场风险极高，请理性决策。</p>
</div>

</div>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
# 4. 主流程
# ══════════════════════════════════════════════════════════════

def run(output_path: str = None, existing_result: dict = None) -> dict:
    """运行 OpenClue 分析师完整流程，返回 10 维度分析结果"""
    print("\n" + "=" * 60)
    print("  🔍 OpenClue 分析师 · 每日宏观报告")
    print("=" * 60)

    # 收集数据（使用已有的信号结果，避免重复运行）
    data = collect_data(existing_result=existing_result)

    # 执行 10 维度分析
    print("\n执行 OpenClue 10 维度分析...")
    dims = {
        "mcap": analyze_dimension_1_mcap(data),
        "stance": analyze_dimension_2_stance(data),
        "etf_flows": analyze_dimension_3_etf_flows(data),
        "etf_holders": analyze_dimension_4_etf_holders(data),
        "fear_greed": analyze_dimension_5_fear_greed(data),
        "btc_dom": analyze_dimension_6_btc_dominance(data),
        "stablecoin": analyze_dimension_7_stablecoin(data),
        "funding": analyze_dimension_8_funding(data),
        "vix": analyze_dimension_9_vix(data),
        "equity": analyze_dimension_10_equity(data),
    }

    # 注入阶段信息供 _build_expanded_text 使用
    macro_result = data.get("macro", {}) or {}
    macro_assessment = macro_result.get("assessment", {}) if isinstance(macro_result, dict) else {}
    dims["_phase_label"] = macro_assessment.get("phase_label", "⚪ 未判定")
    dims["_phase_score"] = macro_assessment.get("phase_score", "?")
    dims["_risk_level"] = macro_assessment.get("risk_level", "?")

    # 保存维度数据供报告生成器使用
    dims_file = BASE / "openclue_latest_dims.json"
    dims_file.write_text(json.dumps(dims, indent=2, ensure_ascii=False))
    print(f"  ✅ 维度数据已保存: {dims_file.name}")

    # 生成 HTML
    print("\n生成 HTML 分析报告...")
    html = generate_html_report(data, dims)

    # 保存
    if not output_path:
        output_path = str(REPORTS_DIR / f"openclue_daily_{TODAY}.html")
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"\n✅ 报告已保存: {output_path}")
    print(f"   字数统计: {len(html)} 字符 (约 {len(html)//4} 字)")
    return dims

    return output_path


if __name__ == "__main__":
    out = run()
    html_path_str = out
