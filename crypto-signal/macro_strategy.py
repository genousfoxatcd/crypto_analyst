#!/usr/bin/env python3
"""
macro_strategy.py — 宏观策略分析模块
═══════════════════════════════
基于 OpenClue + BullBear Dashboard 分析框架的集成实现

数据源：
  · Fear & Greed Index  → alternative.me (free)
  · BTC Dominance        → CoinGecko /api/v3/global (free)
  · 总市值                → CoinGecko /api/v3/global (free)
  · 稳定币总市值          → CoinGecko 逐币种查询 (free)
  · 纳斯达克 / DXY        → 来自 crypto_signal_v2.py 输出
  · BTC/ETH ETF 流量      → 来自 crypto_signal_v2.py 输出
  · 资金费率趋势          → 来自 crypto_signal_v2.py 信号输出

输出：
  macro_assessment = {
    "market_phase": "accumulation|bull|euphoria|bear|capitulation",
    "phase_label": "底部积累期|牛市活跃期|狂喜期|熊市阴跌期|投降期",
    "phase_score": -100~+100,
    "fear_greed": {...},
    "btc_dominance": {...},
    "market_cap": {...},
    "stablecoin": {...},
    "funding_trend": {...},
    "etf_trend": {...},
    "risk_level": "low|medium|high",
    "strategy": "进攻型|平衡型|防御型|现金为王"
  }
"""

import json, sys, math
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    sys.exit("requests 未安装：pip install requests")

TIMEOUT = 10
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124"


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    retry = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

SESSION = _make_session()

# ── 宏观历史缓存（用于计算环比增减） ──
BASE_DIR = Path(__file__).parent
MACRO_HISTORY_FILE = BASE_DIR / ".macro_history.json"


def _load_macro_history() -> dict:
    """加载上次运行的宏观数据，用于计算环比"""
    if MACRO_HISTORY_FILE.exists():
        try:
            return json.loads(MACRO_HISTORY_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_macro_history(data: dict):
    """保存本次运行的关键宏观数据"""
    history = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fear_greed_value": data.get("fear_greed", {}).get("value"),
        "btc_dominance": data.get("market_overview", {}).get("btc_dominance"),
        "total_market_cap": data.get("market_overview", {}).get("total_market_cap"),
        "stablecoin_total": data.get("stablecoin", {}).get("total_supply_usd"),
    }
    MACRO_HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False))


def _calc_macro_deltas(current: dict, history: dict) -> dict:
    """计算当前值与历史值的环比变化"""
    deltas = {}

    # Fear & Greed：优先使用 API 提供的 7 日趋势中的 1 日差
    fng = current.get("fear_greed", {})
    values_7d = fng.get("values_7d", [])
    if len(values_7d) >= 2:
        deltas["fng_delta_1d"] = values_7d[0] - values_7d[1]
    elif history.get("fear_greed_value") is not None:
        deltas["fng_delta_1d"] = (fng.get("value") or 0) - history["fear_greed_value"]

    # BTC 主导率
    mkt = current.get("market_overview", {})
    dom = mkt.get("btc_dominance")
    prev_dom = history.get("btc_dominance")
    if dom is not None and prev_dom is not None and prev_dom > 0:
        deltas["dom_delta"] = round(dom - prev_dom, 2)

    # 总市值
    mc = mkt.get("total_market_cap")
    prev_mc = history.get("total_market_cap")
    if mc is not None and prev_mc is not None and prev_mc > 0:
        deltas["mcap_delta_pct"] = round((mc - prev_mc) / prev_mc * 100, 2)

    # 稳定币总量
    sc = current.get("stablecoin", {})
    sc_total = sc.get("total_supply_usd")
    prev_sc = history.get("stablecoin_total")
    if sc_total is not None and prev_sc is not None and prev_sc > 0:
        deltas["sc_delta_pct"] = round((sc_total - prev_sc) / prev_sc * 100, 2)

    return deltas
# ══════════════════════════════════════════════════════════════
def fetch_fear_greed_index() -> dict:
    """
    从 alternative.me 获取恐惧贪婪指数
    返回: {"value": 0-100, "classification": "Extreme Fear"|...,
           "timestamp": ..., "trend": "up|down|stable"}
    """
    try:
        r = SESSION.get("https://api.alternative.me/fng/?limit=7", timeout=TIMEOUT).json()
        data = r.get("data", [])
        if not data:
            return {}

        now = int(data[0]["timestamp"]) if data[0].get("timestamp") else 0
        value = int(data[0]["value"])
        classification = _fng_classify(value)

        # 计算7日趋势
        if len(data) >= 2:
            values_7d = [int(d["value"]) for d in data if d.get("value")]
            trend = _calc_trend(values_7d)
            delta = values_7d[0] - values_7d[-1] if len(values_7d) >= 2 else 0
        else:
            trend = "stable"
            delta = 0

        return {
            "value": value,
            "classification": classification,
            "classification_cn": _fng_classify_cn(value),
            "trend": trend,
            "delta_7d": delta,
            "values_7d": [int(d["value"]) for d in data if d.get("value")],
            "timestamp": now,
            "source": "alternative.me"
        }
    except Exception as e:
        print(f"  [FearGreed] {e}", file=sys.stderr)
        return {}


def _fng_classify(v: int) -> str:
    if v <= 25: return "Extreme Fear"
    if v <= 45: return "Fear"
    if v <= 55: return "Neutral"
    if v <= 75: return "Greed"
    return "Extreme Greed"


def _fng_classify_cn(v: int) -> str:
    if v <= 25: return "极度恐惧"
    if v <= 45: return "恐惧"
    if v <= 55: return "中性"
    if v <= 75: return "贪婪"
    return "极度贪婪"


# ══════════════════════════════════════════════════════════════
# 2. 市场概览（CoinGecko Global）
# ══════════════════════════════════════════════════════════════
def fetch_market_overview() -> dict:
    """
    从 CoinGecko 获取全局市场数据（含重试+缓存fallback）
    返回: {"btc_dominance": %, "total_market_cap": USD, 
            "total_volume_24h": USD, "eth_dominance": %}
    """
    import time as _time
    SCRIPT_DIR = Path(__file__).parent
    CACHE_FILE = SCRIPT_DIR / ".market_overview_cache.json"
    
    # 尝试API请求（最多3次，指数退避）
    for attempt in range(3):
        try:
            r = SESSION.get(
                "https://api.coingecko.com/api/v3/global",
                timeout=TIMEOUT
            )
            if not r.text or not r.text.strip():
                if attempt < 2:
                    _time.sleep(2 ** attempt)
                continue
            data = r.json()
            d = data.get("data", {})

            btc_d = d.get("market_cap_percentage", {}).get("btc")
            eth_d = d.get("market_cap_percentage", {}).get("eth")
            total_mc = d.get("total_market_cap", {}).get("usd")
            total_vol = d.get("total_volume", {}).get("usd")

            if btc_d is None:
                if attempt < 2:
                    _time.sleep(2 ** attempt)
                continue

            # BTC Dominance 趋势方向
            dom_trend = _calc_btc_dominance_trend(btc_d)

            result = {
                "btc_dominance": round(btc_d, 2),
                "eth_dominance": round(eth_d, 2) if eth_d else None,
                "total_market_cap": total_mc,
                "total_market_cap_str": _fmt_large(total_mc),
                "total_volume_24h": total_vol,
                "total_volume_24h_str": _fmt_large(total_vol),
                "dom_trend": dom_trend,
                "source": "CoinGecko",
                "cached": False,
            }
            # 成功后存缓存
            try:
                CACHE_FILE.write_text(json.dumps(result, ensure_ascii=False))
            except Exception:
                pass
            return result

        except Exception as e:
            if attempt < 2:
                _time.sleep(2 ** attempt)
            continue

    # 所有重试失败 → 读取缓存
    try:
        cached = json.loads(CACHE_FILE.read_text())
        if cached.get("btc_dominance") is not None and cached["btc_dominance"] > 0:
            cached["source"] = "CoinGecko(缓存)"
            cached["cached"] = True
            print(f"  [MarketOverview] API不可用，使用缓存数据 (BTC主导率={cached['btc_dominance']}%)", file=sys.stderr)
            return cached
    except Exception:
        pass

    print(f"  [MarketOverview] API不可用且无缓存，返回空", file=sys.stderr)
    return {}


def _calc_btc_dominance_trend(current_dom: float) -> str:
    """
    简单 BTC Dominance 趋势判断
    无法获取历史时基于绝对值
    """
    if current_dom > 60:
        return "weak_alt"       # 山寨弱势，BTC主导
    elif current_dom < 45:
        return "alt_season"     # 山寨季启动
    else:
        return "neutral"


# ══════════════════════════════════════════════════════════════
# 3. 稳定币数据（CoinGecko）
# ══════════════════════════════════════════════════════════════
STABLECOINS = {
    "USDT": "tether",
    "USDC": "usd-coin",
    "DAI":  "dai",
}

def fetch_stablecoin_data() -> dict:
    """
    查询主流稳定币的总市值（近似场外购买力）
    返回: {"total_supply_usd": USD, "supply_change_24h": %, 
            "details": {USDT:..., USDC:..., DAI:...}}
    """
    ids = ",".join(STABLECOINS.values())
    try:
        r = SESSION.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_market_cap=true&include_24hr_change=true",
            timeout=TIMEOUT
        ).json()

        total = 0
        details = {}
        for name, gid in STABLECOINS.items():
            if gid in r:
                mc = r[gid].get("usd_market_cap", 0)
                chg = r[gid].get("usd_24h_change")
                total += mc if mc else 0
                details[name] = {
                    "market_cap": mc,
                    "market_cap_str": _fmt_large(mc),
                    "change_24h": round(chg, 2) if chg else None
                }

        return {
            "total_supply_usd": total,
            "total_supply_str": _fmt_large(total),
            "details": details,
            "source": "CoinGecko"
        }
    except Exception as e:
        print(f"  [Stablecoin] {e}", file=sys.stderr)
        return {}


# ══════════════════════════════════════════════════════════════
# 4. 核心评分引擎
# ══════════════════════════════════════════════════════════════

# 四个维度的权重（按重要性）
WEIGHTS = {
    "fear_greed":  0.25,
    "market_structure": 0.25,   # BTC Dominance + 总市值
    "institutional": 0.25,      # ETF流量 + 稳定币
    "macro_risk":  0.25,        # 纳指 + DXY + 资金费率
}

def analyze_macro_phase(
    fng: dict,
    market: dict,
    stablecoin: dict,
    etf_data: dict,
    macro_data: dict
) -> dict:
    """
    综合多因子判断市场阶段
    返回: phase 信息 + 各维度得分 + 综合得分
    """
    scores = {}
    signals = []
    warnings = []

    # ── 维度 A: 情绪面（Fear & Greed）────────────────────
    fng_score = 0
    if fng.get("value") is not None:
        v = fng["value"]
        if v <= 20:
            fng_score = -80     # 极度恐惧 → 看多信号
            signals.append(("fear_greed", "LONG", "极度恐惧，历史底部信号"))
        elif v <= 35:
            fng_score = -40
            signals.append(("fear_greed", "LONG", "恐惧区间，积累机会"))
        elif v <= 55:
            fng_score = 0       # 中性
            signals.append(("fear_greed", "NEUTRAL", "情绪中性"))
        elif v <= 75:
            fng_score = 30
            signals.append(("fear_greed", "SHORT", "贪婪区间，注意风险"))
        else:
            fng_score = 60
            signals.append(("fear_greed", "SHORT", "极度贪婪，警惕顶部"))

        # 背离检测：F&G 趋势 vs 价格
        if fng.get("trend") == "down" and fng.get("delta_7d", 0) < -10:
            signals.append(("fear_greed_div", "SHORT", "F&G快速下降，散户恐慌出走"))
        elif fng.get("trend") == "up" and fng.get("delta_7d", 0) > 10:
            signals.append(("fear_greed_div", "LONG", "F&G快速回升，情绪修复"))
    scores["fear_greed"] = fng_score

    # ── 维度 B: 市场结构（BTC Dominance + 总市值）─────────
    mkt_score = 0
    dom = market.get("btc_dominance")
    if dom is not None:
        if dom > 65:
            mkt_score += 20     # BTC高度主导 → 防御/熊市特征
            signals.append(("btc_dom", "SHORT_ALT", "BTC主导>65%，山寨风险高"))
        elif dom > 55:
            mkt_score += 5
        elif dom < 40:
            mkt_score -= 20     # 低主导 → 山寨季，风险偏好高
            signals.append(("btc_dom", "LONG_ALT", "BTC主导<40%，资金轮动到山寨"))
        elif dom < 48:
            mkt_score -= 5

        # 趋势判断
        trend = market.get("dom_trend", "neutral")
        if trend == "alt_season":
            signals.append(("dom_trend", "LONG_ALT", "主导率低位，山寨活跃期"))
        elif trend == "weak_alt":
            signals.append(("dom_trend", "DEFENSIVE", "主导率高位，资金集中BTC"))

    # 总市值趋势判断（基于BS >3T 或 <1T 等粗略区间）
    # 注意：这里只做当前值判断，无法获取历史
    total_mc = market.get("total_market_cap")
    if total_mc:
        if total_mc > 3e12:
            mkt_score += 15     # 市场成熟/过热
            signals.append(("market_cap", "CAUTION", "总市值>3T，历史高位区间"))
        elif total_mc > 2e12:
            mkt_score += 5
        elif total_mc < 1e12:
            mkt_score -= 20     # 市场低迷
            signals.append(("market_cap", "OVERSOLD", "总市值<1T，市场低估"))

    scores["market_structure"] = mkt_score

    # ── 维度 C: 机构资金（ETF + 稳定币）───────────────────
    inst_score = 0

    # ETF 流量综合（BTC + ETH）
    btc_etf = etf_data.get("btc_etf", {}) or {}
    eth_etf = etf_data.get("eth_etf", {}) or {}
    etf_24h = (btc_etf.get("24h_flow_usd_m") or 0) + (eth_etf.get("24h_flow_usd_m") or 0) * 0.7
    etf_72h = (btc_etf.get("72h_flow_usd_m") or 0) + (eth_etf.get("72h_flow_usd_m") or 0) * 0.7

    if etf_24h > 100:
        inst_score += 25
        signals.append(("etf_flow", "LONG", f"24h ETF净流入${etf_24h:.0f}M，机构积极"))
    elif etf_24h > 30:
        inst_score += 10
        signals.append(("etf_flow", "LONG", f"ETF小幅流入${etf_24h:.0f}M"))
    elif etf_24h < -100:
        inst_score -= 25
        signals.append(("etf_flow", "SHORT", f"24h ETF净流出${abs(etf_24h):.0f}M，机构撤退"))
    elif etf_24h < -30:
        inst_score -= 10
    else:
        signals.append(("etf_flow", "NEUTRAL", "ETF流量中性"))

    # 72h 趋势判断
    if etf_72h and etf_72h > 200:
        signals.append(("etf_trend_72h", "STRONG_LONG", "3日累计净流入强劲"))
    elif etf_72h and etf_72h < -200:
        signals.append(("etf_trend_72h", "STRONG_SHORT", "3日累计净流出，趋势明确"))

    # 稳定币储备
    sc_total = stablecoin.get("total_supply_usd")
    if sc_total:
        if sc_total > 150e9:
            inst_score += 15     # 大量稳定币 → 场外购买力充沛
            signals.append(("stablecoin", "LONG", "稳定币储备充足(${:.0f}B)，买盘潜力大".format(sc_total/1e9)))
        elif sc_total < 100e9:
            inst_score -= 10     # 稳定币减少 → 购买力不足
            signals.append(("stablecoin", "CAUTION", "稳定币储备偏低(${:.0f}B)".format(sc_total/1e9)))

    scores["institutional"] = inst_score

    # ── 维度 D: 宏观风险（纳指 + DXY + 资金费率趋势）───────
    macro_score = 0

    # 纳斯达克
    ndx = macro_data.get("nasdaq", {}) or {}
    ndx_1d = ndx.get("change_1d_pct")
    if ndx_1d is not None:
        if ndx_1d > 2:
            macro_score += 15
            signals.append(("macro_ndx", "BULLISH", "纳指大涨>2%，风险偏好高"))
        elif ndx_1d < -2:
            macro_score -= 20
            signals.append(("macro_ndx", "BEARISH", "纳指大跌<-2%，加密跟跌风险"))
        elif ndx_1d < -1:
            macro_score -= 8

    # DXY
    dxy = macro_data.get("dxy", {}) or {}
    dxy_1d = dxy.get("change_1d_pct")
    if dxy_1d is not None:
        if dxy_1d > 0.5:
            macro_score -= 10      # DXY走强 → 风险资产承压
            signals.append(("macro_dxy", "BEARISH", "DXY走强，流动性收紧"))
        elif dxy_1d < -0.5:
            macro_score += 8
            signals.append(("macro_dxy", "BULLISH", "DXY走弱，流动性宽松"))

    scores["macro_risk"] = macro_score

    # ── 综合评分（-100 ~ +100）────────────────────────────
    total = 0
    for dim, score in scores.items():
        total += score * WEIGHTS.get(dim, 0.25)

    total = max(-100, min(100, round(total)))

    # ── 市场阶段判定 ──────────────────────────────────────
    phase, phase_label, risk_level, strategy = _classify_phase(total, fng)

    # ── 综合策略建议 ──────────────────────────────────────
    advice = _generate_strategy_advice(phase, signals, fng)

    return {
        "phase": phase,
        "phase_label": phase_label,
        "phase_score": total,
        "risk_level": risk_level,
        "strategy": strategy,
        "strategy_advice": advice,
        "scores": scores,
        "signals": signals,
        "warnings": warnings,
    }


def _classify_phase(total_score: int, fng: dict) -> tuple:
    """
    根据综合得分 + 恐惧贪婪判定市场阶段
    返回: (phase, phase_label, risk_level, strategy)
    """
    fng_val = fng.get("value", 50)
    fng_cls = fng.get("classification", "Neutral")

    if total_score <= -60 or (fng_val <= 15 and total_score <= -30):
        phase = "capitulation"
        phase_label = "🟣 恐慌投降期"
        risk_level = "high"
        strategy = "分批建仓"
    elif total_score <= -30 or (fng_val <= 25 and total_score <= -10):
        phase = "fear_bottom"
        phase_label = "🟢 恐惧筑底期"
        risk_level = "medium"
        strategy = "择优积累"
    elif total_score <= -10:
        phase = "accumulation"
        phase_label = "🟢 底部积累期"
        risk_level = "low"
        strategy = "进攻型 · 逢低加仓"
    elif total_score <= 15:
        phase = "bull_early"
        phase_label = "🟡 温和上涨期"
        risk_level = "low"
        strategy = "平衡型 · 持有为主"
    elif total_score <= 45:
        phase = "bull_active"
        phase_label = "🟠 牛市活跃期"
        risk_level = "medium"
        strategy = "持有+滚动止盈"
    elif total_score >= 70 or (fng_val >= 85 and total_score >= 50):
        phase = "euphoria"
        phase_label = "🔴 狂热顶部区"
        risk_level = "high"
        strategy = "防御型 · 逐步减仓"
    else:
        phase = "neutral"
        phase_label = "⚪ 中性震荡期"
        risk_level = "low"
        strategy = "观望或轻仓波段"

    return phase, phase_label, risk_level, strategy


def _generate_strategy_advice(phase: str, signals: list, fng: dict) -> list:
    """根据市场阶段生成具体策略建议"""
    advices = {
        "capitulation": [
            "🔴 市场极度恐慌，但历史证明这是最佳买入窗口",
            "建议：分批定投BTC/ETH，每下跌5%加仓一次",
            "杠杆：不超过2x，或以现货为主",
            "关注：稳定币流入交易所 → 聪明钱抄底信号",
        ],
        "fear_bottom": [
            "🟢 恐惧情绪弥漫，但机构可能在偷偷吸筹",
            "建议：开始建立中期仓位，优先BTC/ETH",
            "杠杆：2-3x，严格止损(-5%)",
            "关注：ETF连续净流入 → 确认底部",
        ],
        "accumulation": [
            "🟢 市场从恐惧中恢复，进入积累阶段",
            "建议：逐步增加仓位，可以配置优质山寨",
            "杠杆：3x 为主，控制单笔风险<2%",
            "关注：BTC Dominance是否从高位回落 → 山寨轮动信号",
        ],
        "bull_early": [
            "🟡 温和上涨，趋势向好但尚未过热",
            "建议：持有核心仓位，小仓位做波段",
            "杠杆：3x 正常操作",
            "关注：F&G是否进入贪婪区间 → 加仓最后窗口",
        ],
        "bull_active": [
            "🟠 牛市活跃，市场情绪偏暖",
            "建议：持有为主，逐步将杠杆降至2x",
            "止盈：每涨15%减仓10%",
            "关注：资金费率是否>0.05% → 过热预警",
        ],
        "euphoria": [
            "🔴 极度贪婪，历史顶部信号密集",
            "建议：大幅减仓至30%以下，或完全离场",
            "杠杆：1x或只做现货/空仓",
            "关注：MVRV>3.5、ETF连续净流出 → 确认顶部",
        ],
        "neutral": [
            "⚪ 方向不明，适合观望或小仓位波段",
            "建议：不超过50%仓位，等待方向确认",
            "关注：BTC是否突破关键位 + ETF流量变化",
        ],
    }
    return advices.get(phase, [])


# ══════════════════════════════════════════════════════════════
# 5. 工具函数
# ══════════════════════════════════════════════════════════════

def _calc_trend(values: list) -> str:
    """简单线性趋势判断"""
    if len(values) < 2:
        return "stable"
    recent = sum(values[:3]) / min(3, len(values))
    old = sum(values[-3:]) / min(3, len(values))
    if len(values) <= 3:
        old = values[-1]
    diff = recent - old
    if diff > 5:
        return "up"
    elif diff < -5:
        return "down"
    return "stable"


def _fmt_large(v):
    """格式化大数字为易读字符串"""
    if v is None:
        return "—"
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.2f}M"
    return f"${v:.0f}"


# ══════════════════════════════════════════════════════════════
# 6. 各币种趋势分析与板块分析（新增）
# ══════════════════════════════════════════════════════════════

# 板块划分
MAINSTREAM_COINS = ["BTC", "ETH", "SOL", "BNB"]
ALTCOIN_COINS = ["DOGE", "TAO", "ZEC", "CAKE", "HYPE", "PAXG"]

def _classify_coin_trend(change_24h, flow_buy_pct, funding_rate, long_pct, taker_bsr, prob_score):
    """
    基于多因子判断单个币种的短期趋势。
    v2: 以信号评分(prob_score)为基础，叠加趋势特征（24h涨跌），确保趋势方向与信号方向一致。

    返回: ("STRONG_LONG"|"LONG"|"NEUTRAL"|"SHORT"|"STRONG_SHORT", 得分)

    prob_score 范围约 0-100，映射到 -50 ~ +50:
      prob >= 65 → +30 (高置信做多)
      prob >= 55 → +15 (偏多)
      prob <= 35 → -30 (高置信做空)
      prob <= 45 → -15 (偏空)

    趋势调整（24h涨跌幅）:
      change_24h > 5% → +10
      change_24h > 1% → +5
      change_24h < -5% → -10
      change_24h < -1% → -5
    """
    score = 0

    # ── 信号评分作为基础方向 ──
    if prob_score is not None:
        if prob_score >= 65:
            score += 30   # 高置信做多
        elif prob_score >= 55:
            score += 15   # 偏多
        elif prob_score <= 35:
            score -= 30   # 高置信做空
        elif prob_score <= 45:
            score -= 15   # 偏空
        # 45-55 区间为中性，不加分

    # ── 24h涨跌幅趋势调整 ──
    if change_24h is not None:
        if change_24h > 5:
            score += 10
        elif change_24h > 1:
            score += 5
        elif change_24h < -5:
            score -= 10
        elif change_24h < -1:
            score -= 5

    # ── 买方流量辅助 ──
    if flow_buy_pct is not None:
        if flow_buy_pct > 55:
            score += 8
        elif flow_buy_pct > 52:
            score += 3
        elif flow_buy_pct < 45:
            score -= 8
        elif flow_buy_pct < 48:
            score -= 3

    # ── 资金费率辅助（方向修正）──
    # funding_rate 已是百分比格式（如 0.0045 表示 0.0045%）
    if funding_rate is not None:
        if funding_rate < -0.001:
            score += 5    # 负费率 = 多头清洗完毕，看涨
        elif funding_rate > 0.005:
            score -= 5    # 高正费率 = 多头过热，看跌
        elif funding_rate > 0.001:
            score -= 2

    # ── 多头占比拥挤度 ──
    if long_pct is not None:
        if long_pct > 65:
            score -= 8    # 多头拥挤 → 下杀风险
        elif long_pct > 58:
            score -= 3
        elif long_pct < 40:
            score += 6    # 空头拥挤 → 反弹机会
        elif long_pct < 48:
            score += 3

    # ── Taker BSR ──
    if taker_bsr is not None:
        if taker_bsr > 1.2:
            score += 6
        elif taker_bsr > 1.05:
            score += 3
        elif taker_bsr < 0.8:
            score -= 6
        elif taker_bsr < 0.95:
            score -= 3

    # ── 方向判定（统一阈值，与信号方向对齐）──
    if score >= 25:
        return "STRONG_LONG", score
    elif score >= 10:
        return "LONG", score
    elif score <= -25:
        return "STRONG_SHORT", score
    elif score <= -10:
        return "SHORT", score
    return "NEUTRAL", score


def analyze_coin_trends(prices: dict, contracts: dict, signals: dict, tech_data: dict) -> dict:
    """
    分析各币种趋势 + 主流/山寨板块动态
    返回完整币种趋势数据和板块分析
    """
    coin_trends = {}

    for coin in list(MAINSTREAM_COINS) + list(ALTCOIN_COINS):
        pr = prices.get(coin, {})
        ct = contracts.get(coin, {})
        sg = signals.get(coin, {})
        td = tech_data.get(coin, {})

        change_24h = pr.get("change_24h")
        volume_str = pr.get("volume_str", "—")
        flow_buy = sg.get("flow_buy_pct") or td.get("flow_24h_buy_pct")
        funding_rate = sg.get("funding_rate")
        if funding_rate is not None and isinstance(funding_rate, str):
            funding_rate = float(funding_rate.rstrip("%"))
        long_pct = sg.get("long_pct", 50)
        taker_bsr = sg.get("taker_bsr", 1.0)
        prob_score = sg.get("prob_score", 50)

        # 【修复】直接使用信号方向（确保与交易信号一致）
        signal_direction = sg.get("direction", "NEUTRAL")
        
        # 直接使用信号方向，不再独立评分
        trend = signal_direction
        trend_score = prob_score  # 使用 prob_score 作为趋势得分
        
        # 方向颜色和标签（与信号方向一致）
        dir_colors = {
            "LONG": "#4ade80",
            "NEUTRAL": "#9ca3af",
            "SHORT": "#f87171",
        }
        dir_labels = {
            "LONG": "🟢 看涨",
            "NEUTRAL": "⚪ 中性/震荡",
            "SHORT": "🔴 看跌",
        }
        
        coin_trends[coin] = {
            "change_24h": change_24h,
            "volume": volume_str,
            "trend": trend,
            "trend_label": dir_labels.get(trend, "?"),
            "trend_score": trend_score,
            "color": dir_colors.get(trend, "#9ca3af"),
            "flow_buy_pct": flow_buy,
            "funding_rate": funding_rate,
            "long_pct": long_pct,
            "taker_bsr": taker_bsr,
            "prob_score": prob_score,
        }

    # ── 板块分析 ────────────────────────────────────────────
    seg = _segment_analysis(coin_trends, prices)
    coin_trends["_segment"] = seg

    return coin_trends


def _segment_analysis(coin_trends: dict, prices: dict) -> dict:
    """
    板块对比分析
    """
    seg = {"mainstream_coins": MAINSTREAM_COINS, "altcoins": ALTCOIN_COINS}

    # 主流板块
    ms_trends = []
    ms_changes = []
    ms_scores = []
    for c in MAINSTREAM_COINS:
        t = coin_trends.get(c, {})
        ms_trends.append(t.get("trend", "NEUTRAL"))
        if t.get("change_24h") is not None:
            ms_changes.append(t["change_24h"])
        ms_scores.append(t.get("trend_score", 0))

    seg["mainstream_avg"] = round(sum(ms_changes) / len(ms_changes), 2) if ms_changes else None
    seg["mainstream_max_winner"] = MAINSTREAM_COINS[ms_changes.index(max(ms_changes))] if ms_changes else None
    seg["mainstream_max_loser"] = MAINSTREAM_COINS[ms_changes.index(min(ms_changes))] if ms_changes else None

    # 山寨板块
    ac_trends = []
    ac_changes = []
    ac_scores = []
    for c in ALTCOIN_COINS:
        t = coin_trends.get(c, {})
        ac_trends.append(t.get("trend", "NEUTRAL"))
        if t.get("change_24h") is not None:
            ac_changes.append(t["change_24h"])
        ac_scores.append(t.get("trend_score", 0))

    seg["altcoin_avg"] = round(sum(ac_changes) / len(ac_changes), 2) if ac_changes else None
    seg["altcoin_max_winner"] = ALTCOIN_COINS[ac_changes.index(max(ac_changes))] if ac_changes else None
    seg["altcoin_max_loser"] = ALTCOIN_COINS[ac_changes.index(min(ac_changes))] if ac_changes else None

    # 钱流方向判断
    # 主流强+山寨弱 = 避险模式（BTC主导）
    # 主流弱+山寨强 = 资金轮动到山寨
    # 都强 = 全面牛市
    # 都弱 = 全面熊市
    ms_avg_change = seg["mainstream_avg"] or 0
    ac_avg_change = seg["altcoin_avg"] or 0
    ms_score_avg = sum(ms_scores) / len(ms_scores) if ms_scores else 0
    ac_score_avg = sum(ac_scores) / len(ac_scores) if ac_scores else 0

    diff = ms_avg_change - ac_avg_change

    if ms_avg_change > 0 and ac_avg_change < -1:
        seg["rotation_signal"] = "资金撤离山寨，回流BTC/ETH（避险）"
        seg["rotation_direction"] = "TO_MAINSTREAM"
    elif ms_avg_change < -1 and ac_avg_change > 0:
        seg["rotation_signal"] = "资金从主流溢出至山寨（轮动）"
        seg["rotation_direction"] = "TO_ALTCOIN"
    elif ms_avg_change > 1 and ac_avg_change > 1:
        seg["rotation_signal"] = "全面上涨（牛市形态）"
        seg["rotation_direction"] = "ALL_BULL"
    elif ms_avg_change < -1 and ac_avg_change < -1:
        seg["rotation_signal"] = "全面下跌（熊市形态）"
        seg["rotation_direction"] = "ALL_BEAR"
    elif diff > 1:
        seg["rotation_signal"] = f"主流强于山寨{diff:.1f}%，资金偏好大市值"
        seg["rotation_direction"] = "LEAN_MAINSTREAM"
    elif diff < -1:
        seg["rotation_signal"] = f"山寨强于主流{abs(diff):.1f}%，风险偏好回升"
        seg["rotation_direction"] = "LEAN_ALTCOIN"
    else:
        seg["rotation_signal"] = "板块分化不显著，结构均衡"
        seg["rotation_direction"] = "NEUTRAL"

    # 多空情绪对比
    ms_long_pcts = [coin_trends.get(c, {}).get("long_pct", 50) for c in MAINSTREAM_COINS]
    ac_long_pcts = [coin_trends.get(c, {}).get("long_pct", 50) for c in ALTCOIN_COINS]
    ms_long_avg = sum(ms_long_pcts) / len(ms_long_pcts) if ms_long_pcts else 50
    ac_long_avg = sum(ac_long_pcts) / len(ac_long_pcts) if ac_long_pcts else 50
    seg["mainstream_avg_long_pct"] = round(ms_long_avg, 1)
    seg["altcoin_avg_long_pct"] = round(ac_long_avg, 1)

    return seg


# ══════════════════════════════════════════════════════════════
# 7. 主入口 — 从信号数据中提取已有+新增数据
# ══════════════════════════════════════════════════════════════

def run_with_signal_data(signal_data: dict) -> dict:
    """
    从 signal_data (crypto_signal_v2 的输出) 中提取全量数据进行分析
    signal_data 包含: prices, contracts, signals, tech_data, macro
    """
    fng = fetch_fear_greed_index()
    market = fetch_market_overview()
    stablecoin = fetch_stablecoin_data()

    # 从信号数据中提取 ETF 流量
    etf_data = {
        "btc_etf": signal_data.get("etf_flow", {}),
        "eth_etf": signal_data.get("eth_etf_flow", {}),
    }

    # 从信号数据中提取宏观 (Nasdaq, DXY)
    macro_data = signal_data.get("macro", {})

    # 生成综合评估
    assessment = analyze_macro_phase(
        fng=fng,
        market=market,
        stablecoin=stablecoin,
        etf_data=etf_data,
        macro_data=macro_data,
    )

    # ── 各币种趋势分析（新增）────────────────────────────────
    prices = signal_data.get("prices", {})
    contracts = signal_data.get("contracts", {})
    signals_coin = signal_data.get("signals", {})
    tech_data = signal_data.get("tech_data", {})

    coin_trends = analyze_coin_trends(prices, contracts, signals_coin, tech_data)
    seg = coin_trends.get("_segment", {})
    assessment["coin_trends"] = coin_trends
    assessment["segment_analysis"] = seg

    # 将板块分析信号添加到汇总
    if seg.get("rotation_signal"):
        assessment["signals"].append(("segment_rotation", seg.get("rotation_direction", "NEUTRAL"), seg["rotation_signal"]))

    # 计算环比增减
    macro_deltas = _calc_macro_deltas(
        {"fear_greed": fng, "market_overview": market, "stablecoin": stablecoin},
        _load_macro_history()
    )

    # 保存本次数据供下次计算环比
    _save_macro_history({"fear_greed": fng, "market_overview": market, "stablecoin": stablecoin})

    return {
        "assessment": assessment,
        "fear_greed": fng,
        "market_overview": market,
        "stablecoin": stablecoin,
        "macro_deltas": macro_deltas,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def fetch_all() -> dict:
    """
    独立运行（无信号数据），仅采集宏观数据并分析
    """
    fng = fetch_fear_greed_index()
    market = fetch_market_overview()
    stablecoin = fetch_stablecoin_data()

    assessment = analyze_macro_phase(
        fng=fng,
        market=market,
        stablecoin=stablecoin,
        etf_data={},
        macro_data={},
    )

    return {
        "assessment": assessment,
        "fear_greed": fng,
        "market_overview": market,
        "stablecoin": stablecoin,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    result = fetch_all()
    print(json.dumps(result, indent=2, ensure_ascii=False))


"""
## 踩坑经验

# 以下由 AI 在实际调用中自动积累，请勿手动删除

- `_fetch_okx_prices()` / OKX ticker: 返回字段 `volCcy24h` 是 USDT 计价 24h 成交量, 需映射到 `volume_24h`; `sps24h` 才是价格涨跌幅百分比, `open24h` 需自行计算 `change_24h`
- `_fetch_bybit_prices()` / Bybit spot ticker: `turnover24h` 是 USDT 计价 24h 成交量; `price24hPcnt` 是小数的百分比(如 `0.0161` = +1.61%), 需 `*100`
- `analyze_coin_trends()` 硬编码币种列表: 只遍历 `MAINSTREAM_COINS + ALTCOIN_COINS`, 新增币种需同时加到这两个列表之一(PAXG 需加到 `ALTCOIN_COINS`)
- `report_generator_v2.py` 全量币种趋势表: 硬编码了8个币种, 需改为 `for coin in coin_trends.keys()` 动态生成
- **网络隔离问题(2026-05-26)**: 沙盒/Bash 环境无法访问境外 API(Binance/OKX/Bybit/CoinGecko 全部不通), 需在本机 Mac 终端设置 `export https_proxy=http://127.0.0.1:7890` 后运行; 已创建 `run_crypto_signal.sh` 自动检测代理
"""
