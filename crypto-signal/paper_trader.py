#!/usr/bin/env python3
"""
虚拟合约模拟交易系统 — paper_trader.py  v4.2
交易模式: 限价挂单 + 币安U本位合约模拟
交易窗口: TTL=5天  |  目标盈利: +10%~+15%  |  最大回撤: 5%

v4.2 改进（2026-05-19）:
  [算法优化] 挂单价策略 v3：LONG最多低于市价2%，减少挂单取消和重下
  [智能调整] 价差>5%的挂单不再取消，而是调整限价至市价附近(-2%/+2%)
  [NEUTRAL保留] 信号变为观望时保留旧单，仅方向反转才取消
  [监控告警] 新增24h取消率监控，超50%时告警并显示在状态面板

数据源原则（严格强制）：
  - 所有价格数据必须从 Binance API 实时获取，禁止使用过期/模拟数据
  - 价格获取失败时自动降级至备用交易所（同信号引擎降级链条）
  - 全场数据带时间戳，每60分钟内的数据才被视为有效
  - 若全部交易所API不可用，放弃本次操作，绝不使用历史价格替代

状态流: PENDING → OPEN → CLOSED_TP1/TP2/SL | CANCELLED

仓位分配: 动态权重，高置信度(prob_score)→多资金，高波动→低杠杆
风险控制: 7日回撤≤5%，盈利10%-15%区间告警，超限触发风险模式
算法优化: cmd_optimize — 分析近7日表现，自动调整 leverage_multiplier 和信号门槛

命令:
  init      — 读取最新信号，动态计算仓位大小，创建限价挂单
  update    — 更新价格；检查成交；P&L；止盈止损；风险控制
  status    — 打印挂单（含下单日期/成交状态/到期）+ 持仓 + 风险面板
  reopen    — 取消 PENDING，按最新信号重挂（OPEN 不变）
  cancel    — 取消所有 PENDING
  optimize  — 7日算法优化分析，自动调整 risk_config
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 尝试导入贝叶斯信号生成器
try:
    from bayesian_signal_generator import BayesianSignalGenerator
    HAS_BAYESIAN = True
except ImportError:
    HAS_BAYESIAN = False
    print("  [BayesianSignalGenerator] 模块未找到，跳过贝叶斯模型更新")

CST = timezone(timedelta(hours=8))
ORDER_TTL_DAYS = 5

DEFAULT_RISK_CONFIG = {
    "max_drawdown_pct":       5.0,   # 最大允许回撤 5%（7日）
    "profit_target_min_pct": 10.0,   # 目标盈利下限 10%（7日）
    "profit_target_max_pct": 15.0,   # 目标盈利上限 15%（7日）
    "min_prob_score":        10,     # 最低信号强度门槛
    "leverage_multiplier":   1.0,    # 优化后调整的杠杆系数（0.7~1.3）
    "max_position_weight":   0.30,   # 单仓最大占比（30%）
    "min_position_weight":   0.10,   # 单仓最小占比（10%）
    # ── 下单前验证（P1 优化）─────────────────────
    "max_single_margin":    300.0,  # 单笔订单最大保证金（USDT）
    "max_total_notional":   800.0,  # 总仓位名义价值上限（USDT）
    "max_leverage":         10,     # 单笔最大杠杆倍数
    "max_total_margin":     500.0,  # 总保证金上限（USDT）
    # ── VaR/CVaR 风险指标（P1 优化）─────────────────────
    "var_confidence":       0.95,   # VaR 置信水平（95%）
    "cvar_enabled":        True,   # 是否计算 CVaR
    "var_lookback_days":   30,     # VaR 计算回溯天数
}


def fmt_cst(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(CST).strftime("%Y-%m-%dT%H:%M:%S CST")
    except Exception:
        return iso_str[:19] + " UTC+8 北京时间"

def fmt_date(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(CST).strftime("%m-%d %H:%M")
    except Exception:
        return iso_str[:10]


try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False
    print("pip install requests", file=sys.stderr)

try:
    import _price_cache as pcache
    HAS_PRICE_CACHE = True
except ImportError:
    HAS_PRICE_CACHE = False

CRYPTO_WS      = Path(os.environ.get("CRYPTO_ANALYST_WS", "/Users/alex/projects/crypto_analyst"))
BASE           = CRYPTO_WS / "crypto-signal"
POSITIONS_FILE    = BASE / "paper_positions.json"
HISTORY_FILE      = BASE / "paper_trade_history.json"
SIGNAL_FILE       = BASE / "signal_v2_latest.json"
SNAPSHOT_DIR      = BASE / "position_snapshots"
DIRECTION_HISTORY  = BASE / "direction_history.json"  # 方向切换冷却追踪
DIRECTION_COOLDOWN_H = 4  # 方向切换冷却时间（小时）

INITIAL_CAPITAL   = 1000.0
BINANCE_MAKER_FEE = 0.0002   # 限价单 maker 手续费 0.02%
BINANCE_TAKER_FEE = 0.0004   # 市价单 taker 手续费 0.04%（SL/TP触发时按taker费率）
FUNDING_INTERVAL_H = 8       # 币安U本位合约资金费率结算间隔（每8小时）
COINS = ["BTC", "ETH", "BNB", "SOL", "DOGE", "TAO", "ZEC", "CAKE", "PAXG", "HYPE", "TRX", "AAVE"]
BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT",
    "SOL": "SOLUSDT", "DOGE": "DOGEUSDT", "TAO": "TAOUSDT",
    "ZEC": "ZECUSDT", "CAKE": "CAKEUSDT", "PAXG": "PAXGUSDT",
    "HYPE": "HYPEUSDT", "TRX": "TRXUSDT", "AAVE": "AAVEUSDT",
}


# ── 归档工具 ──────────────────────────────────────────────
def archive_positions():
    """将当前持仓快照归档至 position_snapshots/"""
    if not POSITIONS_FILE.exists():
        return
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ts  = now.strftime("%Y%m%d_%H%M")
    fname = f"positions_{ts}.json"
    data = json.loads(POSITIONS_FILE.read_text())
    (SNAPSHOT_DIR / fname).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # 更新索引
    index_file = SNAPSHOT_DIR / "INDEX.json"
    if index_file.exists():
        try:
            index = json.loads(index_file.read_text())
        except Exception:
            index = {"files": []}
    else:
        index = {"files": []}
    positions = data.get("positions", [])
    active = [p for p in positions if p.get("status") in ("PENDING", "OPEN")]
    index["files"].append({
        "file": fname,
        "timestamp": now.isoformat(),
        "cst": now.strftime("%Y-%m-%d %H:%M CST"),
        "capital": data.get("current_capital"),
        "pnl": round(data.get("current_capital", 0) - data.get("initial_capital", 0), 2),
        "active_positions": len(active),
        "total_positions": len(positions),
    })
    if len(index["files"]) > 100:
        index["files"] = index["files"][-100:]
    index["last_updated"] = now.isoformat()
    index_file.write_text(json.dumps(index, indent=2, ensure_ascii=False))


# ── 实时价格（从交易所实时API获取，优先使用_price_cache缓存）───
def get_current_prices() -> dict:
    """从交易所获取实时价格，返回 coin->float 格式"""
    # 优先从共享缓存获取（TTL=5min，返回格式 {coin: {"price":..., "source":...}}）
    if HAS_PRICE_CACHE:
        cached = pcache.get_all(list(BINANCE_SYMBOLS.keys()))
        if cached and all(v is not None for v in cached.values()):
            # 转换为 coin->float 格式（与 Binance API 直接返回格式一致）
            result = {}
            for coin, d in cached.items():
                if d is not None and "price" in d:
                    result[coin] = float(d["price"])
            if result:
                return result

    prices = {}
    if not REQUESTS_OK:
        print("  ❌ requests库不可用，无法获取实时价格", file=sys.stderr)
        return prices
    try:
        resp = requests.get("https://api.binance.com/api/v3/ticker/price", timeout=10)
        resp.raise_for_status()
        data = {item["symbol"]: float(item["price"]) for item in resp.json()}
        for coin, sym in BINANCE_SYMBOLS.items():
            if sym in data:
                prices[coin] = data[sym]
    except Exception as e:
        print(f"[Binance] 主API获取失败: {e}", file=sys.stderr)

    # 主源失败时降级至备用交易所
    if not prices:
        print("  ⚠️ Binance不可用，降级至备用交易所...")
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,binancecoin,solana,dogecoin,bittensor,zcash,pancakeswap-token,pax-gold,hyperliquid,tron&vs_currencies=usd",
                timeout=10)
            if resp.status_code == 200:
                cg = resp.json()
                M = {"bitcoin":"BTC","ethereum":"ETH","binancecoin":"BNB","solana":"SOL",
                     "dogecoin":"DOGE","bittensor":"TAO","zcash":"ZEC","pancakeswap-token":"CAKE",
                     "pax-gold":"PAXG","hyperliquid":"HYPE","tron":"TRX","aave":"AAVE"}
                for cg_id, coin in M.items():
                    if cg_id in cg and "usd" in cg[cg_id]:
                        prices[coin] = cg[cg_id]["usd"]
                if prices:
                    print(f"  ✅ CoinGecko 备用源获取成功: {len(prices)}币")
        except Exception as e2:
            print(f"  ❌ CoinGecko备用源也失败: {e2}", file=sys.stderr)

    if not prices:
        print("  ❌ 所有交易所API均不可用，禁止使用过期/模拟数据替代")
    elif HAS_PRICE_CACHE:
        # 写入缓存供 crypto_signal_v2.py 和 grid_bot_sim.py 共享
        pcache.set_all({coin: {"price": p, "source": "paper_trader"} for coin, p in prices.items()})
    return prices


# ── P&L 计算 ──────────────────────────────────────────────
def calc_expected_return(pos: dict) -> tuple[float, float, float]:
    """
    返回 (预期盈利率%, 止损风险率%, 盈亏比)，均以保证金为基准。
    预期盈利率 = (TP1距挂单价 / 挂单价) × 杠杆 × 100
    止损风险率 = (SL距挂单价  / 挂单价) × 杠杆 × 100
    """
    lp  = pos.get("limit_price") or pos.get("entry_price", 0)
    tp1 = pos.get("tp1")
    sl  = pos.get("sl")
    lev = pos.get("leverage", 3)
    dir = pos.get("direction", "LONG")
    if not lp:
        return 0.0, 0.0, 0.0
    if dir == "LONG":
        exp_ret  = (tp1 - lp) / lp * lev * 100 if tp1 else 0.0
        sl_risk  = (lp - sl)  / lp * lev * 100 if sl  else 0.0
    else:
        exp_ret  = (lp - tp1) / lp * lev * 100 if tp1 else 0.0
        sl_risk  = (sl - lp)  / lp * lev * 100 if sl  else 0.0
    rr = round(exp_ret / sl_risk, 2) if sl_risk > 0 else 0.0
    return round(exp_ret, 1), round(sl_risk, 1), rr


def calc_pnl(pos: dict, current_price: float) -> tuple[float, float]:
    entry  = pos["entry_price"]
    qty    = pos["quantity"]
    margin = pos["margin"]
    pnl    = (current_price - entry) * qty if pos["direction"] == "LONG" else (entry - current_price) * qty
    return round(pnl, 4), round(pnl / margin * 100, 2)

# ── VaR/CVaR 风险指标（P1 优化）─────────────────────────
def calc_var_cvar(positions: list, prices: dict, confidence: float = 0.95, lookback_days: int = 30) -> tuple[float, float]:
    """
    计算投资组合的 VaR（风险价值）和 CVaR（条件风险价值）。
    使用历史模拟法：基于历史收益率数据计算。
    
    参数：
        positions: 持仓列表（包含 direction, entry_price, quantity, leverage, margin）
        prices: 当前价格字典 {coin: price}
        confidence: VaR 置信水平（默认 0.95 = 95%）
        lookback_days: 历史数据回溯天数（默认 30 天）
    
    返回：
        (var_usd, cvar_usd): VaR 和 CVaR 值（USD）
    """
    if not positions:
        return 0.0, 0.0
    
    # 1. 计算当前组合价值
    portfolio_value = 0.0
    for pos in positions:
        if pos.get("status") != "OPEN":
            continue
        coin = pos["coin"]
        direction = pos.get("direction", "LONG")
        entry_price = pos.get("entry_price", 0)
        quantity = pos.get("quantity", 0)
        current_price = prices.get(coin, entry_price)
        
        # 计算持仓盈亏
        if direction == "LONG":
            pnl = (current_price - entry_price) * quantity
        else:  # SHORT
            pnl = (entry_price - current_price) * quantity
        
        portfolio_value += pos.get("margin", 0) + pnl
    
    # 2. 模拟历史收益率（使用正态分布假设）
    #    简化版：假设收益率服从正态分布 N(0, sigma^2)
    #    实际应读取历史价格数据计算
    import math
    from random import gauss
    
    # 假设日波动率 = 3%（可根据币种调整）
    daily_vol = 0.03
    num_simulations = 10000
    
    # 生成模拟收益率
    simulated_returns = [gauss(0, daily_vol) for _ in range(num_simulations)]
    
    # 3. 计算 VaR（风险价值）
    #    VaR = 组合价值 × 负收益率分位数
    simulated_pnl = [portfolio_value * r for r in simulated_returns]
    simulated_pnl_sorted = sorted(simulated_pnl)
    
    var_index = int((1 - confidence) * len(simulated_pnl_sorted))
    var_usd = -simulated_pnl_sorted[var_index]  # VaR 为正值
    
    # 4. 计算 CVaR（条件风险价值）
    #    CVaR = VaR 之外的平均损失
    tail_losses = simulated_pnl_sorted[:var_index]
    cvar_usd = -sum(tail_losses) / len(tail_losses) if tail_losses else var_usd
    
    return round(var_usd, 2), round(cvar_usd, 2)




# ── 动态仓位计算 ──────────────────────────────────────────
def calc_position_sizes(signals: dict, available_capital: float, risk_config: dict) -> dict:
    """
    基于回撤风险和盈利预期的动态仓位分配。
    因子：
      - prob_score（基础置信度）
      - R/R ratio（风险回报比）
      - ATR%（波动率，高波动→低权重）
    目标：7日回撤≤5%，收益10-15%
    """
    active = {c: s for c, s in signals.items()
              if s.get("direction", "NEUTRAL") != "NEUTRAL"
              and abs(s.get("prob_score", 0)) >= risk_config.get("min_prob_score", 10)}
    if not active:
        return {}

    # 多因子综合权重：prob × rr_boost / atr_penalty
    raw_scores = {}
    for c, s in active.items():
        prob = max(abs(s.get("prob_score", 10)), 10)
        rr   = max(abs(s.get("rr_ratio", 0) or 0), 0.1)
        atr  = max(s.get("atr_pct", 2.0) or 2.0, 0.5)
        # 高R/R → 权重乘数×1.5封顶，高波动→权重除数
        rr_boost = min(rr / 1.5, 1.5)  # rr=1.5→1.0x, rr=3.0→1.5x
        atr_penalty = 2.0 / atr         # atr=1%→2.0x, atr=2%→1.0x, atr=4%→0.5x
        raw_scores[c] = max(prob * rr_boost * atr_penalty, 5)

    total_score = sum(raw_scores.values())
    raw_weights = {c: raw_scores[c] / total_score for c in active}

    # 限制单仓权重
    w_max = risk_config.get("max_position_weight", 0.30)
    w_min = risk_config.get("min_position_weight", 0.15)
    weights = {}
    for c, w in raw_weights.items():
        weights[c] = max(w_min, min(w_max, w))
    # 归一化
    wsum = sum(weights.values())
    weights = {c: w / wsum for c, w in weights.items()}

    lev_mult = risk_config.get("leverage_multiplier", 1.0)
    result = {}
    for coin, sig in active.items():
        margin   = round(available_capital * weights[coin], 2)
        leverage = sig.get("leverage", 3)

        # 波动率代理：entry_zone 宽度 / entry_mid
        zone = sig.get("entry_zone", [None, None])
        mid  = sig.get("entry_mid")
        if zone[0] and zone[1] and mid:
            spread_pct = (zone[1] - zone[0]) / mid * 100
            if spread_pct > 4.0:
                leverage = min(leverage, 2)
            elif spread_pct > 2.0:
                leverage = min(leverage, 3)

        # 恐慌贪婪指数 → 杠杆调整
        #   极端恐慌→机会，可适当加杠杆   极端贪婪→泡沫风险，降低杠杆
        fng_val = sig.get("fng_value", 50) or 50
        if fng_val < 20:
            leverage = min(5, leverage + 1)   # 极度恐慌→可以激进
        elif fng_val > 75:
            leverage = max(1, leverage - 1)   # 极度贪婪→保守
        elif fng_val > 85:
            leverage = 1                       # 极端贪婪→只做1x

        # ETF流量趋势 → 权重调整
        #   持续流入→支持做多方向   持续流出→支持做空
        etf_score = sig.get("etf_score", 0) or 0
        if etf_score < -5 and sig.get("direction") == "LONG":
            # ETF流出与做多方向冲突→减仓
            margin = round(margin * 0.7, 2)
        elif etf_score > 5 and sig.get("direction") == "LONG":
            # ETF流入与做多方向一致→可适当加仓
            margin = round(min(margin * 1.15, available_capital * 0.35), 2)

        # 应用优化系数，范围 [1, 5]
        leverage = max(1, min(5, round(leverage * lev_mult)))
        result[coin] = {
            "margin":      margin,
            "leverage":    leverage,
            "weight_pct":  round(weights[coin] * 100, 1),
            "prob_score":  sig.get("prob_score", 10),
        }
    return result


# ── 风险控制 ──────────────────────────────────────────────
def check_risk_controls(state: dict) -> list[str]:
    """检查回撤和盈利目标，返回告警列表，更新 state 风险字段"""
    alerts = []
    cap  = state["current_capital"]
    cap0 = state["initial_capital"]
    cfg  = state.get("risk_config", DEFAULT_RISK_CONFIG)

    # 维护峰值净值
    state["peak_capital"] = max(state.get("peak_capital", cap0), cap)
    peak = state["peak_capital"]

    # 回撤检查
    drawdown = (peak - cap) / peak * 100 if peak > 0 else 0
    state["current_drawdown_pct"] = round(drawdown, 2)
    max_dd = cfg.get("max_drawdown_pct", 5.0)

    if drawdown >= max_dd:
        alerts.append(f"🚨 回撤超限！当前回撤 {drawdown:.1f}% ≥ 阈值 {max_dd:.0f}%  → 建议暂停新挂单")
        state["risk_mode"] = True
    elif drawdown >= max_dd * 0.7:
        alerts.append(f"⚠️  回撤预警：{drawdown:.1f}%（接近阈值 {max_dd:.0f}%）")
        state["risk_mode"] = False
    else:
        state["risk_mode"] = False

    # 盈利目标检查
    pnl_pct = (cap - cap0) / cap0 * 100
    pmin = cfg.get("profit_target_min_pct", 10.0)
    pmax = cfg.get("profit_target_max_pct", 15.0)

    if pnl_pct >= pmax:
        alerts.append(f"🎯 盈利达上限！+{pnl_pct:.1f}% ≥ {pmax:.0f}%  → 建议锁定收益，取消多余挂单")
        state["profit_lock_mode"] = True
    elif pnl_pct >= pmin:
        alerts.append(f"📈 已进入目标盈利区间：+{pnl_pct:.1f}%（{pmin:.0f}%–{pmax:.0f}%）")
        state["profit_lock_mode"] = False
    else:
        state["profit_lock_mode"] = False

    return alerts


# ── 24h取消率统计 ────────────────────────────────────────
def _calc_cancel_rate_24h(history: list) -> dict:
    """计算最近24小时内的取消率，用于监控过度取消"""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).isoformat()
    recent = [h for h in history if (h.get("closed_at") or "") >= cutoff]
    if not recent:
        return {"total": 0, "cancelled": 0, "filled": 0, "rate": 0.0}
    cancelled = [h for h in recent if not h.get("filled_at")]
    filled = [h for h in recent if h.get("filled_at")]
    rate = len(cancelled) / len(recent) * 100
    return {
        "total": len(recent),
        "cancelled": len(cancelled),
        "filled": len(filled),
        "rate": round(rate, 1),
    }


# ── 方向切换冷却 ──────────────────────────────────────────
def _load_direction_history() -> dict:
    """加载方向切换历史记录"""
    if not DIRECTION_HISTORY.exists():
        return {}
    try:
        return json.loads(DIRECTION_HISTORY.read_text())
    except Exception:
        return {}

def _save_direction_history(dh: dict):
    """保存方向切换历史"""
    DIRECTION_HISTORY.write_text(json.dumps(dh, indent=2, ensure_ascii=False))

def _check_direction_cooldown(coin: str, new_direction: str) -> tuple[bool, str]:
    """
    检查方向切换是否在冷却期内。
    
    Returns:
        (is_cooling, reason) - is_cooling=True 表示在冷却期，不应切换方向
    """
    dh = _load_direction_history()
    record = dh.get(coin, {})
    old_dir = record.get("direction", "NEUTRAL")
    last_switch = record.get("last_switch_ts", "")
    
    # 方向没变 → 不触发冷却
    if old_dir == new_direction or new_direction == "NEUTRAL":
        return False, ""
    
    # 方向变了 → 检查冷却期
    if last_switch:
        try:
            last_dt = datetime.fromisoformat(last_switch.replace("Z", "+00:00"))
            elapsed_h = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if elapsed_h < DIRECTION_COOLDOWN_H:
                remaining_h = DIRECTION_COOLDOWN_H - elapsed_h
                return True, f"⏳ 方向冷却中 ({old_dir}→{new_direction}，距上次切换 {elapsed_h:.1f}h，还需 {remaining_h:.1f}h)"
        except Exception:
            pass
    
    return False, ""

def _update_direction_history(coin: str, direction: str):
    """更新方向切换历史（仅在方向真正改变时调用）"""
    dh = _load_direction_history()
    old = dh.get(coin, {}).get("direction", "")
    if old != direction:
        dh[coin] = {
            "direction": direction,
            "last_switch_ts": datetime.now(timezone.utc).isoformat(),
        }
        _save_direction_history(dh)
        if old:
            print(f"  [{coin}] 🔄 方向切换记录: {old} → {direction}")

# ── 初始化：动态挂单 ──────────────────────────────────────
def cmd_init(prices: dict | None = None):
    if not SIGNAL_FILE.exists():
        print("❌ signal_v2_latest.json 不存在，请先运行 crypto_signal_v2.py")
        return

    sig_data  = json.loads(SIGNAL_FILE.read_text())
    signals   = sig_data.get("signals", {})
    now_str   = datetime.now(timezone.utc).isoformat()
    expires   = (datetime.now(timezone.utc) + timedelta(days=ORDER_TTL_DAYS)).isoformat()

    if prices is None:
        prices = get_current_prices()

    existing_state = {}
    if POSITIONS_FILE.exists():
        existing_state = json.loads(POSITIONS_FILE.read_text())

    risk_config   = existing_state.get("risk_config", DEFAULT_RISK_CONFIG.copy())
    realized      = existing_state.get("realized_pnl", 0.0)
    open_pos      = [p for p in existing_state.get("positions", []) if p["status"] == "OPEN"]
    pending_pos   = [p for p in existing_state.get("positions", []) if p["status"] == "PENDING"]

    # ── 贝叶斯因子权重注入 ──────────────────────────────────
    bayesian_adjust = {}
    if HAS_BAYESIAN:
        try:
            history_file = Path(__file__).parent / "paper_trade_history.json"
            if history_file.exists():
                bay_gen = BayesianSignalGenerator(history_file, lookback_days=30)
                fw = bay_gen.get_factor_weights()
                # 根据贝叶斯因子权重动态调整 prob_score 门槛
                # 权重最高的3个因子作为主要参考
                sorted_fw = sorted(fw["factors"].items(), key=lambda x: x[1], reverse=True)
                bayesian_adjust = {
                    "top_factors": sorted_fw[:3],
                    "win_rate": fw["prior_win_rate"],
                    "total_trades": fw["total_trades"],
                }
                # 胜率 > 55% → 降低门槛（可接受更多信号）
                # 胜率 < 35% → 提高门槛（过滤弱信号）
                if fw["prior_win_rate"] >= 55:
                    new_threshold = max(8, risk_config.get("min_prob_score", 10) - 2)
                    risk_config["min_prob_score"] = new_threshold
                    print(f"  🧠 贝叶斯: 历史胜率{fw['prior_win_rate']}%≥55% → 信号门槛降低至 p{new_threshold}")
                elif fw["prior_win_rate"] < 35 and fw["total_trades"] >= 3:
                    new_threshold = min(20, risk_config.get("min_prob_score", 10) + 5)
                    risk_config["min_prob_score"] = new_threshold
                    print(f"  🧠 贝叶斯: 历史胜率{fw['prior_win_rate']}%<35% → 信号门槛提高至 p{new_threshold}")
                else:
                    print(f"  🧠 贝叶斯: 历史胜率{fw['prior_win_rate']}% | 最佳因子={', '.join([f'{n}={w:.0f}%' for n,w in sorted_fw[:3]])}")
        except Exception as e:
            print(f"  ⚠️  贝叶斯因子权重加载失败: {e}")

    # ── PENDING 订单智能评估 v4 ────────────────────────────
    # 核心原则：宁可保留，不可滥砍。改善成交率的策略：
    # 1. 方向改变(LONG↔SHORT) + 冷却期已过 → 取消
    # 2. 方向改变(LONG↔SHORT) + 冷却期内 → 保留（防震荡）
    # 3. 信号观望 → 保留，仅标记（让市场证明多空）
    # 4. 价差>5% → 不平取消，而是调整限价至当前市价附近（-2%或+2%）
    # 5. 价差≤5% → 保留（给市场时间回到挂单价）
    kept_pending = []
    cancelled_pending = []
    history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []

    for p in pending_pos:
        coin = p["coin"]
        sig = signals.get(coin, {})
        new_dir = sig.get("direction", "NEUTRAL")
        cp_coin = prices.get(coin)

        keep = True
        adjust = False
        cancel_reason = None

        # 方向发生根本改变（做多↔做空）→ 检查冷却期
        if (p["direction"] == "LONG" and new_dir == "SHORT") or \
           (p["direction"] == "SHORT" and new_dir == "LONG"):
            cooling, cool_msg = _check_direction_cooldown(coin, new_dir)
            if cooling:
                # 冷却期内 → 保留旧单，等市场明朗
                keep = True
                print(f"  [{coin}] {cool_msg} → 保留旧挂单等待冷却结束")
            else:
                keep = False
                cancel_reason = f"🔄 信号方向反转({p['direction']}→{new_dir})，冷却已过，取消旧单"
                # 记录方向切换（冷却期外才记录）
                _update_direction_history(coin, new_dir)

        # 价差过大 → 调整限价至合理区间，避免无止境取消重下

        # 价差过大 → 调整限价至合理区间，避免无止境取消重下
        if cp_coin and keep:
            gap = abs(p["limit_price"] - cp_coin) / cp_coin
            if gap > 0.05:
                adjust = True
                # 多单：限价调整至市价-2%（仍低于市场，等待回调）
                if p["direction"] == "LONG":
                    new_price = round(cp_coin * 0.98, 6)
                else:
                    new_price = round(cp_coin * 1.02, 6)
                p["limit_price"] = new_price
                p["created_at"] = datetime.now(timezone.utc).isoformat()  # 刷新创建时间
                print(f"  [{coin}] 🔄 调整限价: ${p['limit_price']:,.4f}→${new_price:,.4f}（原价差{gap*100:.1f}%，已修正）")

        if keep:
            kept_pending.append(p)
            if adjust:
                gap_str = f"已调整"
            else:
                gap_str = f"价差{abs(p['limit_price'] - (cp_coin or 0)) / (cp_coin or 1) * 100:.1f}%"
            print(f"  [{coin}] ✅ 保留旧挂单 @ ${p['limit_price']:,.4f}（{gap_str}）")
        else:
            p["status"] = "CANCELLED"
            p["closed_at"] = now_str
            p["close_reason"] = cancel_reason
            cancelled_pending.append(p)
            history.append(_make_history_record(p, cp_coin or p["limit_price"], now_str))
            print(f"  [{coin}] {cancel_reason}")

    active_pos    = open_pos + kept_pending
    active_coins  = {p["coin"] for p in active_pos}
    used_margin   = sum(p["margin"] for p in open_pos)
    effective_cap = round(INITIAL_CAPITAL + realized, 2)
    available     = max(effective_cap - used_margin, 0)

    # 动态仓位计算（仅对无 OPEN/PENDING 仓的币，避免与智能评估保留的单重复）
    signals_for_sizing = {c: s for c, s in signals.items() if c not in active_coins}
    sizes = calc_position_sizes(signals_for_sizing, available, risk_config)

    new_orders = []
    for coin in COINS:
        if coin not in sizes:
            if coin in active_coins and any(p["coin"] == coin and p["status"] == "OPEN" for p in open_pos):
                print(f"  [{coin}] ⏭️ 已有持仓，跳过")
            continue

        sig       = signals.get(coin, {})
        direction = sig.get("direction", "NEUTRAL")
        if direction == "NEUTRAL":
            continue

        # ── 方向切换冷却检查 ──
        # 若方向刚切换且仍在冷却期，跳过创建新订单（防止震荡中追涨杀跌）
        cooling, cool_msg = _check_direction_cooldown(coin, direction)
        if cooling:
            print(f"  [{coin}] {cool_msg} → 跳过新订单创建")
            continue
        # 方向未冷却 → 记录当前方向
        _update_direction_history(coin, direction)

        sz         = sizes[coin]
        margin     = sz["margin"]
        leverage   = sz["leverage"]
        weight_pct = sz["weight_pct"]
        prob       = sz["prob_score"]

        zone     = sig.get("entry_zone", [None, None])
        zone_low = zone[0] if zone else None
        zone_hi  = zone[1] if zone else None
        entry_mid = sig.get("entry_mid")
        cp       = prices.get(coin)

        # ── 挂单价策略 v3：以成交概率优先 ──
        # 问题 v2：0.3% 最大偏移还是太窄，易产生大价差挂单被取消/调整
        # 改进 v3：允许回调到 entry_zone 范围但限制与市价的差距
        # 原则：宁可买入价略高/卖出价略低，也要成交
        if direction == "LONG":
            raw = zone_low or (entry_mid or cp or 0)
            # 多单限价：取 entry_zone 低端（等待回调），但最多低于市价2%
            limit_price = max(raw, cp * 0.98) if cp else raw
            # 不能高于当前价（否则直接市价买入）
            limit_price = min(limit_price, cp) if cp else limit_price
        else:
            raw = zone_hi or (entry_mid or cp or 0)
            # 空单限价：取 entry_zone 高端（等待反弹），但最多高于市价2%
            limit_price = min(raw, cp * 1.02) if cp else raw
            # 不能低于当前价
            limit_price = max(limit_price, cp) if cp else limit_price
        limit_price = round(limit_price, 6) if limit_price else 0

        if not limit_price:
            print(f"  [{coin}] ⚠️ 无法确定挂单价，跳过")
            continue

        notional = round(margin * leverage, 2)
        
        # ── 下单前验证（P1 优化）─────────────────────────────
        warnings = []
        blocked = False
        
        # 验证1: 单笔保证金限制
        if margin > risk_config.get("max_single_margin", 300.0):
            warnings.append(f"⚠️  保证金 ${margin:,.2f} > 上限 ${risk_config['max_single_margin']:,.2f}")
        
        # 验证2: 总保证金限制
        total_margin_used = sum(p.get("margin", 0) for p in open_pos + kept_pending)
        if total_margin_used + margin > risk_config.get("max_total_margin", 500.0):
            warnings.append(f"⚠️  总保证金 ${total_margin_used + margin:,.2f} > 上限 ${risk_config['max_total_margin']:,.2f}")
            blocked = True
        
        # 验证3: 杠杆限制
        if leverage > risk_config.get("max_leverage", 10):
            warnings.append(f"⚠️  杠杆 {leverage}x > 上限 {risk_config['max_leverage']}x")
            leverage = risk_config.get("max_leverage", 10)
            notional = round(margin * leverage, 2)
        
        # 验证4: 总名义价值限制
        total_notional = sum(p.get("notional", 0) for p in open_pos + kept_pending)
        if total_notional + notional > risk_config.get("max_total_notional", 800.0):
            warnings.append(f"⚠️  总名义价值 ${total_notional + notional:,.2f} > 上限 ${risk_config['max_total_notional']:,.2f}")
            blocked = True
        
        # 验证5: 信号强度验证
        if prob < risk_config.get("min_prob_score", 10):
            warnings.append(f"⚠️  信号强度 {prob} < 门槛 {risk_config['min_prob_score']}")
        
        # 输出警告
        for w in warnings:
            print(f"  [{coin}] {w}")
        
        # 阻止下单
        if blocked:
            print(f"  [{coin}] ❌ 下单被阻止（超过风险限制）")
            continue
        
        quantity = round(notional / limit_price, 8)
        gap_pct  = (limit_price - cp) / cp * 100 if cp else 0

        if direction == "LONG":
            fill_hint = "✅即时" if cp and cp <= limit_price else f"↘{abs(gap_pct):.1f}%"
        else:
            fill_hint = "✅即时" if cp and cp >= limit_price else f"↗{abs(gap_pct):.1f}%"

        new_orders.append({
            "coin":            coin,
            "direction":       direction,
            "order_type":      "LIMIT_BUY" if direction == "LONG" else "LIMIT_SELL",
            "limit_price":     round(limit_price, 6),
            "entry_zone_low":  zone_low,
            "entry_zone_high": zone_hi,
            "entry_price":     None,
            "current_price":   round(cp or limit_price, 6),
            "quantity":        quantity,
            "leverage":        leverage,
            "margin":          margin,
            "notional":        notional,
            "weight_pct":      weight_pct,
            "prob_score":      prob,
            "tp1":             sig.get("tp1"),
            "tp2":             sig.get("tp2"),
            "sl":              sig.get("sl"),
            "funding_rate":    sig.get("funding_rate", 0) / 100,  # 转换为小数
            "last_funding_time": 0,
            "status":          "PENDING",
            "placed_at":       now_str,
            "expires_at":      expires,
            "filled_at":       None,
            "closed_at":       None,
            "pnl_usd":         0.0,
            "pnl_pct":         0.0,
            "max_profit_usd":  0.0,
            "max_loss_usd":    0.0,
            "close_reason":    None,
            "_fill_hint":      fill_hint,
        })

    open_pnl_ex = sum(p.get("pnl_usd", 0) for p in open_pos)
    current_cap = round(effective_cap + open_pnl_ex, 4)

    state = {
        "initial_capital":      INITIAL_CAPITAL,
        "effective_capital":    effective_cap,
        "current_capital":      current_cap,
        "peak_capital":         existing_state.get("peak_capital", INITIAL_CAPITAL),
        "positions":            open_pos + kept_pending + new_orders,
        "created_at":           now_str,
        "last_updated":         now_str,
        "signal_generated":     sig_data.get("generated_at", now_str),
        "order_ttl_days":       ORDER_TTL_DAYS,
        "risk_config":          risk_config,
        "risk_mode":            existing_state.get("risk_mode", False),
        "profit_lock_mode":     existing_state.get("profit_lock_mode", False),
        "current_drawdown_pct": existing_state.get("current_drawdown_pct", 0.0),
        "total_trades":         existing_state.get("total_trades", 0),
        "total_wins":           existing_state.get("total_wins", 0),
        "total_losses":         existing_state.get("total_losses", 0),
        "realized_pnl":         realized,
        "optimization_log":     existing_state.get("optimization_log", []),
    }

    POSITIONS_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    archive_positions()

    # 保存取消记录到 history
    if cancelled_pending:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        history = [h for h in history if (h.get("closed_at") or "") >= cutoff]
        HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False))

    # ── 取消率监控 ────────────────────────────────────────
    cancel_info = _calc_cancel_rate_24h(history)
    if cancel_info["total"] >= 3 and cancel_info["rate"] >= 50:
        print(f"\n  ⚠️ 24h取消率 {cancel_info['rate']:.0f}%（{cancel_info['cancelled']}/{cancel_info['total']}）≥ 50% 阈值！")
        print(f"     建议运行 optimize 分析后手动调整策略参数")

    print(f"\n✅ 挂单完成 — {len(new_orders)} 单  可用 ${available:.0f}  TTL={ORDER_TTL_DAYS}天")
    if kept_pending:
        print(f"  📌 保留旧挂单 {len(kept_pending)} 单：{', '.join(p['coin'] for p in kept_pending)}")
    print(f"  {'币':<5} {'类型':<12} {'挂单价':>12} {'当前价':>12} {'保证金':>8} {'杠杆':>5} "
          f"{'权重':>6} {'信号':>6}  {'状态'}")
    print(f"  {'-'*80}")
    for o in new_orders:
        print(f"  {o['coin']:<5} {o['order_type']:<12} "
              f"${o['limit_price']:>10,.4f} ${o['current_price']:>10,.4f} "
              f"${o['margin']:>6.0f} {o['leverage']:>4}x "
              f"{o['weight_pct']:>5.1f}% "
              f"p{o['prob_score']:>4}  {o['_fill_hint']}")
    if open_pos:
        kept_coins = [p["coin"] for p in kept_pending]
        open_coins = [p["coin"] for p in open_pos]
        print(f"\n  保留 {len(open_pos)} 个 OPEN: {', '.join(open_coins)}")
    return state


# ── 更新 ──────────────────────────────────────────────────
def cmd_update() -> dict:
    if not POSITIONS_FILE.exists():
        return cmd_init()

    state  = json.loads(POSITIONS_FILE.read_text())
    prices = get_current_prices()
    if not prices:
        print("⚠️ 无法获取价格，跳过")
        return state

    now_str = datetime.now(timezone.utc).isoformat()
    now_dt  = datetime.now(timezone.utc)
    history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []

    closed_pnl = 0.0
    open_pnl   = 0.0

    for pos in state["positions"]:
        coin = pos["coin"]
        cp   = prices.get(coin)
        if cp is None:
            continue
        pos["current_price"] = round(cp, 6)

        # ── PENDING ───────────────────────────────────────
        if pos["status"] == "PENDING":
            exp_dt = datetime.fromisoformat(pos["expires_at"].replace("Z", "+00:00"))
            if now_dt >= exp_dt:
                pos["status"]       = "CANCELLED"
                pos["closed_at"]    = now_str
                pos["close_reason"] = f"⏰ 超时取消 (TTL {ORDER_TTL_DAYS}天)"
                print(f"  [{coin}] ⏰ 挂单超时取消  {pos['order_type']} @ ${pos['limit_price']:,.4f}")
                history.append(_make_history_record(pos, cp, now_str))
                continue

            lp  = pos["limit_price"]
            dir = pos["direction"]
            # 使用容差比较，避免因浮点数精度问题导致无法成交
            # 当当前价 <= 挂单价 * 1.0001 (LONG) 或当前价 >= 挂单价 * 0.9999 (SHORT) 时成交
            fill_tolerance = 1.0001
            if dir == "LONG":
                should_fill = cp <= lp * fill_tolerance
            else:
                should_fill = cp >= lp / fill_tolerance
            
            if should_fill:
                open_fee = round(pos["notional"] * BINANCE_MAKER_FEE, 4)
                pos["status"]      = "OPEN"
                pos["entry_price"] = lp
                pos["filled_at"]   = now_str
                pos["quantity"]    = round(pos["notional"] / lp, 8)
                pos["open_fee"]    = open_fee
                pos["pnl_usd"]     = -open_fee   # 开仓费即时计入
                pos["pnl_pct"]     = round(-open_fee / pos["margin"] * 100, 4)
                closed_pnl        -= open_fee    # 费用计入已实现亏损
                print(f"  [{coin}] 🟢 限价单成交！{dir} @ ${lp:,.4f}  (市价 ${cp:,.4f})  开仓费: -${open_fee:.4f}")
            continue

        # ── OPEN ──────────────────────────────────────────
        if pos["status"] != "OPEN":
            continue

        pnl_usd, pnl_pct = calc_pnl(pos, cp)
        pos["pnl_usd"]        = pnl_usd
        pos["pnl_pct"]        = pnl_pct
        pos["max_profit_usd"] = max(pos.get("max_profit_usd", 0), pnl_usd)
        pos["max_loss_usd"]   = min(pos.get("max_loss_usd", 0), pnl_usd)

        # ── 资金费率模拟（精确对齐 Binance 真实结算时间） ──────────
        # Binance 真实结算: UTC 00:00 / 08:00 / 16:00
        FUNDING_HOURS_UTC = [0, 8, 16]
        
        last_funding = pos.get("last_funding_time", 0)
        now_utc = now_dt.astimezone(timezone.utc)
        now_utc_ts = now_utc.timestamp()
        
        if last_funding == 0:
            # 首次运行，初始化为当前时间，不结算
            pos["last_funding_time"] = int(now_utc_ts)
            continue
        
        # 将上次结算时间转为 UTC datetime
        try:
            last_dt_utc = datetime.fromtimestamp(last_funding, tz=timezone.utc)
        except Exception:
            pos["last_funding_time"] = int(now_utc_ts)
            continue
        
        # 检查 last_dt_utc ~ now_utc 之间是否跨越了某个结算点
        funding_applied = False
        for fh in FUNDING_HOURS_UTC:
            # 构造结算时间点
            fh_today = now_utc.replace(hour=fh, minute=0, second=0, microsecond=0)
            fh_yesterday = fh_today - timedelta(days=1)
            
            # 检查是否跨越了该结算点
            if last_dt_utc < fh_yesterday <= now_utc:
                # 跨越了昨日的结算点
                fr = pos.get("funding_rate", 0)
                if fr != 0:
                    funding_cost = pos["notional"] * fr
                    if pos["direction"] == "LONG":
                        funding_cost = -funding_cost  # 多头支付
                    pnl_usd += funding_cost
                    pos["pnl_usd"] = pnl_usd
                    pos["total_funding_paid"] = round(pos.get("total_funding_paid", 0) + funding_cost, 4)
                    state["total_funding_paid"] = round(state.get("total_funding_paid", 0) + funding_cost, 4)
                    print(f"  [{coin}] 💸 资金费率结算(UTC {fh:02d}:00): {funding_cost:+.4f} (FR={fr*100:.4f}%)")
                    funding_applied = True
            elif last_dt_utc < fh_today <= now_utc:
                # 跨越了今日的结算点
                fr = pos.get("funding_rate", 0)
                if fr != 0:
                    funding_cost = pos["notional"] * fr
                    if pos["direction"] == "LONG":
                        funding_cost = -funding_cost  # 多头支付
                    pnl_usd += funding_cost
                    pos["pnl_usd"] = pnl_usd
                    pos["total_funding_paid"] = round(pos.get("total_funding_paid", 0) + funding_cost, 4)
                    state["total_funding_paid"] = round(state.get("total_funding_paid", 0) + funding_cost, 4)
                    print(f"  [{coin}] 💸 资金费率结算(UTC {fh:02d}:00): {funding_cost:+.4f} (FR={fr*100:.4f}%)")
                    funding_applied = True
        
        # 更新 last_funding_time 为当前时间
        pos["last_funding_time"] = int(now_utc_ts)
        if funding_applied:
            print(f"  [{coin}] ✅ 资金费率已按真实结算时间更新")

        # ── 价格穿越检测（防止TP/SL在更新间隔中被跳过）──
        dir = pos["direction"]
        prev_price = pos.get("current_price", cp)  # 上次更新的价格
        # 检查 TP/SL 是否被跨越（min(prev, cp) <= level <= max(prev, cp)）
        crossed_tp2 = pos.get("tp2") and min(prev_price, cp) <= pos["tp2"] <= max(prev_price, cp)
        crossed_tp1 = pos.get("tp1") and min(prev_price, cp) <= pos["tp1"] <= max(prev_price, cp)
        crossed_sl  = pos.get("sl")  and min(prev_price, cp) <= pos["sl"]  <= max(prev_price, cp)

        closed, reason = False, ""
        if dir == "LONG":
            if crossed_sl and cp <= pos["sl"]:
                # SL被向下穿越（当前价在SL之下）= 真正的止损
                closed = True; reason = f"🛑 SL触发（穿越检测）@${pos['sl']:,.4f} 当前${cp:,.4f}"
            # TP检查：优先TP2（更高利润）
            if pos["tp2"] and cp >= pos["tp2"]:
                closed = True; reason = f"🎯 TP2触发 @${cp:,.4f}"
            elif pos["tp1"] and cp >= pos["tp1"]:
                closed = True; reason = f"✅ TP1触发 @${cp:,.4f}"
            elif pos["sl"] and cp <= pos["sl"]:
                closed = True; reason = f"🛑 SL触发 @${cp:,.4f}"
        else:
            if crossed_sl and cp >= pos["sl"]:
                closed = True; reason = f"🛑 SL触发（穿越检测）@${pos['sl']:,.4f} 当前${cp:,.4f}"
            if pos["tp2"] and cp <= pos["tp2"]:
                closed = True; reason = f"🎯 TP2触发 @${cp:,.4f}"
            elif pos["tp1"] and cp <= pos["tp1"]:
                closed = True; reason = f"✅ TP1触发 @${cp:,.4f}"
            elif pos["sl"] and cp >= pos["sl"]:
                closed = True; reason = f"🛑 SL触发 @${cp:,.4f}"

        if closed:
            close_fee   = round(cp * pos["quantity"] * BINANCE_TAKER_FEE, 4)  # TP/SL按taker费率
            open_fee    = pos.get("open_fee", round(pos["notional"] * BINANCE_MAKER_FEE, 4))
            total_fee   = round(open_fee + close_fee, 4)
            net_pnl     = round(pnl_usd - close_fee, 4)   # gross已含open_fee影响
            net_pnl_pct = round(net_pnl / pos["margin"] * 100, 2)

            pos["status"]       = ("CLOSED_TP2" if "TP2" in reason else
                                   "CLOSED_TP1" if "TP1" in reason else "CLOSED_SL")
            pos["closed_at"]    = now_str
            pos["close_reason"] = reason
            pos["close_fee"]    = close_fee
            pos["total_fee"]    = total_fee
            pos["pnl_usd"]      = net_pnl
            pos["pnl_pct"]      = net_pnl_pct
            closed_pnl         += net_pnl
            state["total_trades"] = state.get("total_trades", 0) + 1
            state["total_fees"]   = round(state.get("total_fees", 0) + total_fee, 4)
            (state.__setitem__("total_wins",   state.get("total_wins", 0) + 1) if net_pnl >= 0
             else state.__setitem__("total_losses", state.get("total_losses", 0) + 1))
            print(f"  [{coin}] {reason}  毛P&L: ${pnl_usd:+.2f}  费用: -${total_fee:.4f}  净P&L: ${net_pnl:+.2f} ({net_pnl_pct:+.1f}%)")
            history.append(_make_history_record(pos, cp, now_str, reason=reason, fee=total_fee))
            
            # 更新贝叶斯模型（如果可用）
            if HAS_BAYESIAN:
                try:
                    history_file = Path(__file__).parent / "paper_trade_history.json"
                    bay_gen = BayesianSignalGenerator(history_file, lookback_days=30)
                    trade_record = _make_history_record(pos, cp, now_str, reason=reason, fee=total_fee)
                    bay_gen.update_from_trade(trade_record)
                    
                    # 保存贝叶斯学习日志和因子权重快照
                    log_file = BASE / "bayesian_learning_log.json"
                    bay_gen.save_learning_log(log_file)
                    
                    # 输出因子权重建议
                    fw = bay_gen.get_factor_weights(coin)
                    print(f"  🧠 贝叶斯因子权重: {fw['summary']}")
                    
                    # 在动态风险配置中记录贝叶斯胜率
                    risk_config = state.get("risk_config", DEFAULT_RISK_CONFIG.copy())
                    risk_config["bayesian_win_rate"] = fw["prior_win_rate"]
                    risk_config["bayesian_factor_weights"] = fw["factors"]
                    state["risk_config"] = risk_config
                except Exception as e:
                    print(f"  ⚠️  贝叶斯模型更新失败: {e}")
        else:
            open_pnl += pnl_usd

    state["realized_pnl"]    = round(state.get("realized_pnl", 0.0) + closed_pnl, 4)
    state["total_fees"]      = round(state.get("total_fees", 0) + sum(
        pos.get("total_fee", 0) for pos in state["positions"]
        if pos.get("close_fee") is not None), 4)
    state["effective_capital"] = round(INITIAL_CAPITAL + state["realized_pnl"], 2)
    state["current_capital"] = round(state["effective_capital"] + open_pnl, 4)
    state["last_updated"]    = now_str

    alerts = check_risk_controls(state)
    for a in alerts:
        print(f"  {a}")

    # ── 24h取消率监控 ─────────────────────────────────────
    cancel_info = _calc_cancel_rate_24h(history)
    if cancel_info["total"] >= 3:
        if cancel_info["rate"] >= 50:
            print(f"  ⚠️ 24h取消率 {cancel_info['rate']:.0f}%（{cancel_info['cancelled']}/{cancel_info['total']}）≥ 50% 阈值！建议运行 paper_trader.py optimize")
        elif cancel_info["rate"] >= 30:
            print(f"  📊 24h取消率 {cancel_info['rate']:.0f}%（{cancel_info['cancelled']}/{cancel_info['total']}）")
    state["cancel_rate_24h"] = cancel_info["rate"]

    POSITIONS_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    archive_positions()
    cutoff  = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    history = [h for h in history if (h.get("closed_at") or "") >= cutoff]
    HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False))

    return state


def _make_history_record(pos: dict, cp: float, now_str: str, reason: str = "", fee: float = 0.0) -> dict:
    return {
        "coin":        pos["coin"],
        "direction":   pos["direction"],
        "limit_price": pos.get("limit_price"),
        "entry_price": pos.get("entry_price") or pos.get("limit_price"),
        "close_price": cp,
        "quantity":    pos.get("quantity", 0),
        "leverage":    pos.get("leverage", 3),
        "margin":      pos.get("margin", 0),
        "weight_pct":  pos.get("weight_pct"),
        "prob_score":  pos.get("prob_score"),
        "pnl_usd":     pos.get("pnl_usd", 0.0),
        "pnl_pct":     pos.get("pnl_pct", 0.0),
        "fee_usd":     fee,
        "placed_at":   pos.get("placed_at"),
        "filled_at":   pos.get("filled_at"),
        "closed_at":   now_str,
        "reason":      reason or pos.get("close_reason", ""),
    }


# ── 状态打印 ──────────────────────────────────────────────
def cmd_status():
    if not POSITIONS_FILE.exists():
        print("无持仓记录"); return
    print_state(json.loads(POSITIONS_FILE.read_text()))


def print_state(state: dict):
    cap0       = state["initial_capital"]
    cap        = state["current_capital"]
    total_pnl  = cap - cap0
    peak       = state.get("peak_capital", cap0)
    drawdown   = state.get("current_drawdown_pct", 0.0)
    risk_mode  = state.get("risk_mode", False)
    profit_lock = state.get("profit_lock_mode", False)
    now_dt     = datetime.now(timezone.utc)
    positions  = state.get("positions", [])
    pending    = [p for p in positions if p["status"] == "PENDING"]
    open_pos   = [p for p in positions if p["status"] == "OPEN"]
    cfg        = state.get("risk_config", DEFAULT_RISK_CONFIG)

    mode_str = ("🚨 风险模式" if risk_mode else "🎯 锁利模式" if profit_lock else "✅ 正常")

    print(f"\n{'='*75}")
    print(f"  📊 模拟账户   初始:${cap0:.0f}  当前:${cap:.2f}  总P&L:${total_pnl:+.2f}  "
          f"(胜:{state.get('total_wins',0)} 负:{state.get('total_losses',0)})  "
          f"累计费用:-${state.get('total_fees',0):.4f}")
    print(f"  更新: {fmt_cst(state['last_updated'])}")
    print(f"{'='*75}")

    # 风险面板
    pnl_pct   = (cap - cap0) / cap0 * 100
    dd_bar    = "█" * int(drawdown / cfg.get("max_drawdown_pct", 5) * 10) + "░" * (10 - int(drawdown / cfg.get("max_drawdown_pct", 5) * 10))
    pnl_bar   = "█" * min(10, int(pnl_pct / cfg.get("profit_target_max_pct", 15) * 10)) + "░" * max(0, 10 - int(pnl_pct / cfg.get("profit_target_max_pct", 15) * 10))
    last_opt  = state.get("optimization_log", [{}])[-1].get("date", "从未") if state.get("optimization_log") else "从未"
    lev_mult  = cfg.get("leverage_multiplier", 1.0)

    print(f"\n  ⚖️  风险控制  [{mode_str}]")
    print(f"     回撤: {drawdown:>4.1f}% / {cfg.get('max_drawdown_pct',5):.0f}%  [{dd_bar}]   "
          f"盈亏: {pnl_pct:>+5.1f}%  [{pnl_bar}]  目标: {cfg.get('profit_target_min_pct',10):.0f}%-{cfg.get('profit_target_max_pct',15):.0f}%")
    print(f"     峰值: ${peak:.2f}  |  杠杆系数: {lev_mult:.1f}x  |  最低信号强度: p{cfg.get('min_prob_score',10)}  |  上次优化: {last_opt}")
    # ── 24h取消率 ─────────────────────────────────────────
    cancel_r = state.get("cancel_rate_24h", -1)
    if cancel_r >= 0:
        cancel_color = "🟢" if cancel_r < 30 else "🟡" if cancel_r < 50 else "🔴"
        print(f"     24h取消率: {cancel_color} {cancel_r:.1f}%  (目标 <50%)")

    # 宏观摘要（从最新信号读取）
    if SIGNAL_FILE.exists():
        try:
            sig_data = json.loads(SIGNAL_FILE.read_text())
            fng = sig_data.get("fng", {}) or {}
            etf = sig_data.get("etf_flow", {}) or {}
            fng_val = fng.get("now_value", "?")
            fng_cls = fng.get("classification", "")
            etf_24h = etf.get("24h_flow_usd_m")
            etf_str = f"ETF:${etf_24h:.0f}M" if etf_24h is not None else ""
            print(f"     宏观: 恐慌贪婪={fng_val}({fng_cls})  {etf_str}")
        except Exception:
            pass

    # 挂单队列
    if pending:
        print(f"\n  📋 挂单队列 ({len(pending)} 单，TTL={state.get('order_ttl_days', ORDER_TTL_DAYS)}天)")
        print(f"  {'币':^5} {'方向':^10} {'挂单价':>11} {'当前价':>11} {'价差':>7}  "
              f"{'保证金':>7} {'杠杆':>5} {'预期盈利':>8} {'止损风险':>8} {'盈利概率':>8}  "
              f"{'下单日期':^11} {'成交状态':^6} {'到期':^8} {'剩余'}")
        print(f"  {'-'*108}")
        for p in pending:
            cp     = p.get("current_price", p["limit_price"])
            lp     = p["limit_price"]
            exp_dt = datetime.fromisoformat(p["expires_at"].replace("Z", "+00:00"))
            ttl_h  = max(0, (exp_dt - now_dt).total_seconds() / 3600)
            gap    = (lp - cp) / cp * 100 if cp else 0
            placed = fmt_date(p.get("placed_at", ""))
            expiry = exp_dt.astimezone(CST).strftime("%m-%d")
            exp_ret, sl_risk, rr = calc_expected_return(p)
            prob   = p.get("prob_score", 0)
            tp1    = p.get("tp1")
            sl     = p.get("sl")
            tp_str = f"${tp1:,.2f}" if tp1 else "N/A"
            sl_str = f"${sl:,.2f}"  if sl  else "N/A"
            dir_d  = "🟢 多" if p["direction"] == "LONG" else "🔴 空"
            print(f"  {p['coin']:^5} {dir_d:^10} "
                  f"${lp:>9,.4f} ${cp:>9,.4f} {gap:>+6.2f}%  "
                  f"${p['margin']:>5.0f} {p['leverage']:>4}x "
                  f"{exp_ret:>+7.1f}% {sl_risk:>+7.1f}% {prob:>7}%  "
                  f"{placed:^11} {'未成交':^6} {expiry:^8} {ttl_h:.0f}h")
            print(f"  {'':^5} {'':^10}   TP1:{tp_str:>12}  SL:{sl_str:>12}  "
                  f"盈亏比: {rr:.2f}x")

    # 持仓中
    if open_pos:
        print(f"\n  🔥 持仓中 ({len(open_pos)} 仓)")
        print(f"  {'币':^5} {'方向':^5} {'入场价':>11} {'当前价':>11} {'杠杆':>5} "
              f"{'保证金':>7} {'P&L($)':>9} {'P&L%':>7} {'TP1':>11} {'TP2':>11} {'SL':>11} {'成交日期':^12}")
        print(f"  {'-'*115}")
        for pos in open_pos:
            cp       = pos.get("current_price", pos["entry_price"])
            filled   = fmt_date(pos.get("filled_at", pos.get("opened_at", "")))
            tp1_s    = f"${pos['tp1']:>8,.2f}" if pos.get('tp1') else "   N/A   "
            tp2_s    = f"${pos['tp2']:>8,.2f}" if pos.get('tp2') else "   N/A   "
            sl_s     = f"${pos['sl']:>8,.2f}" if pos.get('sl') else "   N/A   "
            print(f"  {pos['coin']:^5} {pos['direction']:^5} "
                  f"${pos['entry_price']:>9,.4f} "
                  f"${cp:>9,.4f} "
                  f"{pos['leverage']:>4}x "
                  f"${pos['margin']:>6.0f} "
                  f"${pos['pnl_usd']:>+8.2f} "
                  f"{pos['pnl_pct']:>+6.1f}% "
                  f"{tp1_s:>11} {tp2_s:>11} {sl_s:>11} "
                  f"{filled:^12}")

    if not pending and not open_pos:
        print("\n  （无挂单和持仓）")

    # 历史成交
    if HISTORY_FILE.exists():
        history = json.loads(HISTORY_FILE.read_text())
        if history:
            has_filled = any(h.get("filled_at") for h in history[-20:])
            has_cancelled = any("取消" in h.get("reason","") for h in history[-20:])
            print(f"\n  {'─'*75}")
            print(f"  📜 历史成交 ({len(history)} 单)  "
                  f"{'✅已成交' if has_filled else ''} "
                  f"{'⏰已取消' if has_cancelled else ''}")
            print(f"  {'下单日期':^11} {'币':^5} {'方向':^5} {'挂单价':>10} {'入场价':>10} "
                  f"{'平仓价':>10} {'净P&L':>9} {'费用':>7} {'是否成交':^8} {'原因'}")
            print(f"  {'─'*80}")
            for h in history[-10:]:
                placed  = fmt_date(h.get("placed_at") or h.get("closed_at", ""))
                icon    = "✅" if h.get("pnl_usd", 0) > 0 else ("⏰" if "取消" in h.get("reason","") else "🔴" if h.get("pnl_usd", 0) < 0 else "—")
                lp_s    = f"${h['limit_price']:,.4f}" if h.get("limit_price") else "  N/A  "
                ep_s    = f"${h['entry_price']:,.4f}" if h.get("entry_price") else "  N/A  "
                filled  = "✅已成交" if h.get("filled_at") else "❌未成交"
                fee_s   = f"-${h.get('fee_usd',0):.4f}" if h.get("fee_usd") else "  —  "
                close_p = h.get("close_price", 0)
                print(f"  {placed:^11} {h['coin']:^5} {h['direction']:^5} "
                      f"{lp_s:>10} {ep_s:>10} ${close_p:>8,.4f} "
                      f"{icon}${h['pnl_usd']:>+7.2f} {fee_s:>8} {filled:^10} {h.get('reason','')}")

    # ── 资金费率统计 ─────────────────────────────────────────
    total_funding = state.get("total_funding_paid", 0.0)
    if total_funding != 0:
        print(f"  累计资金费用: ${total_funding:+.4f}")


# ── 重置交易系统 ──────────────────────────────────────────
def cmd_reset():
    """清空所有数据，重置至初始状态"""
    now_str = datetime.now(timezone.utc).isoformat()
    fresh = {
        "initial_capital":      INITIAL_CAPITAL,
        "effective_capital":    INITIAL_CAPITAL,
        "current_capital":      INITIAL_CAPITAL,
        "peak_capital":         INITIAL_CAPITAL,
        "current_drawdown_pct": 0.0,
        "risk_mode":            False,
        "profit_lock_mode":     False,
        "last_updated":         now_str,
        "created_at":           now_str,
        "total_wins":           0,
        "total_losses":         0,
        "total_trades":         0,
        "total_fees":           0.0,
        "total_funding_paid":   0.0,
        "realized_pnl":         0.0,
        "positions":            [],
        "risk_config":           DEFAULT_RISK_CONFIG.copy(),
        "optimization_log":     [],
    }
    POSITIONS_FILE.write_text(json.dumps(fresh, indent=2, ensure_ascii=False))
    HISTORY_FILE.write_text("[]")
    archive_positions()
    print(f"\n{'='*60}")
    print(f"  🔄 模拟交易系统已重置")
    print(f"  初始资金: ${INITIAL_CAPITAL:.0f}  |  状态: 空仓")
    print(f"  时间: {fmt_cst(now_str)}")
    print(f"{'='*60}")
    return fresh


# ── 7日算法优化 ───────────────────────────────────────────
def cmd_optimize():
    """分析近7日成交数据，自动调整 risk_config 参数"""
    if not POSITIONS_FILE.exists():
        print("无持仓记录"); return

    state   = json.loads(POSITIONS_FILE.read_text())
    history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
    cfg     = state.get("risk_config", DEFAULT_RISK_CONFIG.copy())

    closed    = [h for h in history if h["pnl_usd"] != 0 or any(k in h.get("reason","") for k in ["TP","SL"])]
    cancelled = [h for h in history if "取消" in h.get("reason","")]
    all_trades = closed + cancelled

    # 委托单成交率 = 成交数 / (成交数 + 取消数)
    fill_rate = len(closed) / len(all_trades) * 100 if all_trades else 0
    # 仓单止损率  = 止损平仓数 / 成交仓位数
    sl_hits   = [h for h in closed if "SL" in h.get("reason","")]
    sl_rate   = len(sl_hits) / len(closed) * 100 if closed else 0

    now_cst = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    print(f"\n{'='*60}")
    print(f"  📊 7日算法优化报告  {now_cst}")
    print(f"{'='*60}")
    print(f"  委托单总数: {len(all_trades)}  |  成交: {len(closed)}  |  取消: {len(cancelled)}")
    print(f"  成交率: {fill_rate:.1f}%  (目标 ≥60%)   止损率: {sl_rate:.1f}%  (目标 <40%)")

    changes = []
    stats = {
        "total":        len(all_trades),
        "closed":       len(closed),
        "cancelled":    len(cancelled),
        "fill_rate":    round(fill_rate, 1),
        "sl_rate":      round(sl_rate, 1),
        "cancel_rate":  round(len(cancelled) / len(all_trades) * 100, 1) if all_trades else 0,
        "win_rate":     0.0,
        "avg_win":      0.0,
        "avg_loss":     0.0,
        "total_pnl":    0.0,
    }

    if closed:
        wins    = [h for h in closed if h["pnl_usd"] > 0]
        losses  = [h for h in closed if h["pnl_usd"] <= 0]
        wr      = len(wins) / len(closed) * 100
        avg_w   = sum(h["pnl_usd"] for h in wins) / len(wins) if wins else 0
        avg_l   = sum(h["pnl_usd"] for h in losses) / len(losses) if losses else 0
        tot_pnl = sum(h["pnl_usd"] for h in closed)
        stats.update({"win_rate": round(wr, 1), "avg_win": round(avg_w, 2),
                       "avg_loss": round(avg_l, 2), "total_pnl": round(tot_pnl, 2)})

        print(f"\n  交易表现:")
        print(f"    胜率:     {wr:.1f}%  ({len(wins)}胜 / {len(losses)}负 / {len(sl_hits)}止损)")
        print(f"    平均盈利: ${avg_w:+.2f}   平均亏损: ${avg_l:+.2f}")
        print(f"    总P&L:    ${tot_pnl:+.2f}")

        # ── 委托单成交率 ───────────────────────────────────
        if fill_rate < 60:
            changes.append(f"📌 成交率{fill_rate:.0f}% < 60%：entry_mid 偏离市价，建议调整挂单价至 entry_zone 边界以提高成交率")
            # 自动放宽挂单策略：记录建议（不自动修改 entry_mid，由下次 init 决策）
            cfg["_fill_rate_warning"] = True
        else:
            cfg.pop("_fill_rate_warning", None)

        # ── 仓单止损率 ─────────────────────────────────────
        if sl_rate >= 40:
            old_lm = cfg.get("leverage_multiplier", 1.0)
            cfg["leverage_multiplier"] = round(max(0.7, old_lm - 0.15), 2)
            changes.append(f"🛑 止损率{sl_rate:.0f}% ≥ 40% → 杠杆系数 {old_lm:.1f}→{cfg['leverage_multiplier']:.1f}，建议收紧 SL")
            old_ps = cfg.get("min_prob_score", 10)
            cfg["min_prob_score"] = min(25, old_ps + 3)
            changes.append(f"🔼 止损率过高 → 信号强度门槛 p{old_ps}→p{cfg['min_prob_score']} (仅接受高置信度信号)")

        # ── 杠杆系数调整（基于胜率） ───────────────────────
        old_lm = cfg.get("leverage_multiplier", 1.0)
        if wr < 35 and sl_rate < 40 and old_lm > 0.7:
            cfg["leverage_multiplier"] = round(max(0.7, old_lm - 0.1), 2)
            changes.append(f"📉 胜率{wr:.0f}% < 35% → 杠杆系数 {old_lm:.1f}→{cfg['leverage_multiplier']:.1f}")
        elif wr > 65 and sl_rate < 20 and old_lm < 1.3:
            cfg["leverage_multiplier"] = round(min(1.3, old_lm + 0.1), 2)
            changes.append(f"📈 胜率{wr:.0f}% > 65%，止损率{sl_rate:.0f}% < 20% → 杠杆系数 {old_lm:.1f}→{cfg['leverage_multiplier']:.1f}")

        # ── 信号门槛调整 ──────────────────────────────────
        old_ps = cfg.get("min_prob_score", 10)
        if wr < 35 and sl_rate < 40 and old_ps < 20:
            cfg["min_prob_score"] = min(20, old_ps + 2)
            changes.append(f"🔼 信号强度门槛 p{old_ps}→p{cfg['min_prob_score']} (过滤弱信号)")
        elif wr > 65 and fill_rate >= 60 and old_ps > 10:
            cfg["min_prob_score"] = max(10, old_ps - 1)
            changes.append(f"🔽 信号强度门槛 p{old_ps}→p{cfg['min_prob_score']} (接受更多信号)")

        # ── 风险收益比检查 ─────────────────────────────────
        if avg_l != 0 and abs(avg_l) > abs(avg_w) * 1.5:
            changes.append(f"⚠️  平均亏损/盈利 = {abs(avg_l)/abs(avg_w):.1f}x → 建议收紧 SL 至 0.5×ATR")

        # ── 盈亏目标调整 ──────────────────────────────────
        if tot_pnl < 0:
            changes.append("📉 整体为负：检查 SHORT 信号方向准确性，考虑降低 SHORT 权重")

        # ── 单币分析 ──────────────────────────────────────
        coin_pnl = {}
        for h in closed:
            coin_pnl.setdefault(h["coin"], [])
            coin_pnl[h["coin"]].append(h["pnl_usd"])
        if coin_pnl:
            worst = min(coin_pnl.items(), key=lambda x: sum(x[1]))
            if sum(worst[1]) < -15:
                changes.append(f"💡 {worst[0]} 累计亏损最大 ${sum(worst[1]):.1f}：建议下次降低该币权重")

    # ── 成交率不足（无成交时也检查） ─────────────────────
    if fill_rate < 60 and "📌 成交率" not in " ".join(changes):
        changes.append(f"📌 成交率{fill_rate:.0f}% < 60%：建议调整挂单价至 entry_zone 边界以提高成交率")

    # ── 输出调整结果 ──────────────────────────────────────
    print(f"\n  调整结果:")
    if changes:
        for c in changes:
            print(f"    {c}")
    else:
        print("    ✅ 策略参数运行良好，本次无调整")

    # 保存
    state["risk_config"]    = cfg
    state["last_optimized"] = datetime.now(timezone.utc).isoformat()
    if "optimization_log" not in state:
        state["optimization_log"] = []
    state["optimization_log"].append({
        "date":    datetime.now(CST).strftime("%Y-%m-%d"),
        "changes": changes,
        "stats":   stats,
    })
    # 只保留最近10次
    state["optimization_log"] = state["optimization_log"][-10:]

    POSITIONS_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    archive_positions()
    print(f"\n  ✅ risk_config 已更新  (杠杆系数:{cfg['leverage_multiplier']:.1f}  门槛:p{cfg['min_prob_score']})")
    return state


# ── 取消所有挂单 ──────────────────────────────────────────
def cmd_cancel():
    if not POSITIONS_FILE.exists():
        print("无持仓记录"); return
    state   = json.loads(POSITIONS_FILE.read_text())
    now_str = datetime.now(timezone.utc).isoformat()
    prices  = get_current_prices()
    history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
    n = 0
    for pos in state["positions"]:
        if pos["status"] == "PENDING":
            pos["status"]       = "CANCELLED"
            pos["closed_at"]    = now_str
            pos["close_reason"] = "🚫 手动取消"
            history.append(_make_history_record(pos, prices.get(pos["coin"], pos["limit_price"]), now_str))
            n += 1
    state["last_updated"] = now_str
    POSITIONS_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    archive_positions()
    HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    print(f"✅ 已取消 {n} 个挂单")


# ── HTML 报告 section ─────────────────────────────────────
def generate_html_section(state: dict) -> str:
    history   = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
    cap0      = state["initial_capital"]
    cap       = state["current_capital"]
    total_pnl = cap - cap0
    total_pct = total_pnl / cap0 * 100
    peak      = state.get("peak_capital", cap0)
    drawdown  = state.get("current_drawdown_pct", 0.0)
    cfg       = state.get("risk_config", DEFAULT_RISK_CONFIG)
    risk_mode = state.get("risk_mode", False)
    profit_lock = state.get("profit_lock_mode", False)
    win_count  = state.get("total_wins", 0)
    loss_count = state.get("total_losses", 0)
    win_rate   = win_count / (win_count + loss_count) * 100 if (win_count + loss_count) > 0 else 0
    positions  = state.get("positions", [])
    pending    = [p for p in positions if p["status"] == "PENDING"]
    open_pos   = [p for p in positions if p["status"] == "OPEN"]
    now_dt     = datetime.now(timezone.utc)

    pnl_color  = "#4ade80" if total_pnl >= 0 else "#f87171"
    dd_pct     = min(100, drawdown / cfg.get("max_drawdown_pct", 5) * 100)
    pnl_p_pct  = min(100, total_pct / cfg.get("profit_target_max_pct", 15) * 100)
    mode_badge = ('<span style="color:#f87171">🚨 风险模式</span>' if risk_mode else
                  '<span style="color:#fbbf24">🎯 锁利模式</span>' if profit_lock else
                  '<span style="color:#4ade80">✅ 正常</span>')
    last_opt   = state.get("optimization_log", [{}])[-1].get("date", "从未") if state.get("optimization_log") else "从未"

    # Pending rows
    pending_rows = ""
    for p in pending:
        cp     = p.get("current_price", p["limit_price"])
        lp     = p["limit_price"]
        exp_dt = datetime.fromisoformat(p["expires_at"].replace("Z", "+00:00"))
        ttl_h  = max(0, (exp_dt - now_dt).total_seconds() / 3600)
        gap    = (lp - cp) / cp * 100 if cp else 0
        placed = fmt_date(p.get("placed_at", ""))
        expiry = exp_dt.astimezone(CST).strftime("%m-%d")
        gcol   = "#4ade80" if (p["direction"]=="LONG" and cp<=lp) or (p["direction"]=="SHORT" and cp>=lp) else "#fbbf24"
        dir_c  = "short-dir" if p["direction"] == "SHORT" else "long-dir"
        pending_rows += f"""
        <tr>
          <td><b>{p['coin']}</b></td>
          <td class="{dir_c}">{p['order_type']}</td>
          <td>${lp:,.4f}</td><td>${cp:,.4f}</td>
          <td style="color:{gcol}">{gap:+.2f}%</td>
          <td>${p['margin']:.0f}</td><td>{p['leverage']}x</td>
          <td>p{p.get('prob_score',0)}/{p.get('weight_pct',0):.0f}%</td>
          <td>{placed}</td>
          <td><span style="color:#fbbf24">未成交</span></td>
          <td>{expiry} ({ttl_h:.0f}h)</td>
        </tr>"""

    # Open rows
    pos_rows = ""
    for pos in open_pos:
        cp    = pos.get("current_price", pos["entry_price"])
        pnl   = pos["pnl_usd"]
        color = "#4ade80" if pnl >= 0 else "#f87171"
        lp_s  = f"${pos['limit_price']:,.4f}" if pos.get("limit_price") else "市价"
        dir_c = "short-dir" if pos["direction"] == "SHORT" else "long-dir"
        filled = fmt_date(pos.get("filled_at", ""))
        pos_rows += f"""
        <tr>
          <td><b>{pos['coin']}</b></td>
          <td class="{dir_c}">{pos['direction']}</td>
          <td>{lp_s}</td><td>${pos['entry_price']:,.4f}</td><td>${cp:,.4f}</td>
          <td>{pos['leverage']}x</td><td>${pos['margin']:.0f}</td>
          <td style="color:{color}">${pnl:+.2f}</td>
          <td style="color:{color}">{pos['pnl_pct']:+.1f}%</td>
          <td>TP1:${pos['tp1']:,.2f}<br>SL:${pos['sl']:,.2f}</td>
          <td>{filled}</td>
          <td><span class="badge badge-open">持仓中</span></td>
        </tr>"""

    # History rows
    hist_rows = ""
    for h in reversed(history[-10:]):
        color  = "#4ade80" if h["pnl_usd"] > 0 else ("#6b7280" if h["pnl_usd"] == 0 else "#f87171")
        placed = fmt_date(h.get("placed_at") or h.get("closed_at",""))
        filled = "已成交" if h.get("filled_at") else "未成交"
        dir_c  = "short-dir" if h["direction"] == "SHORT" else "long-dir"
        lp_s   = f"${h['limit_price']:,.4f}" if h.get("limit_price") else "N/A"
        ep_s   = f"${h['entry_price']:,.4f}" if h.get("entry_price") else "N/A"
        hist_rows += f"""
        <tr>
          <td>{placed}</td>
          <td><b>{h['coin']}</b></td>
          <td class="{dir_c}">{h['direction']}</td>
          <td>{lp_s}</td><td>{ep_s}</td><td>${h['close_price']:,.4f}</td>
          <td>{h.get('leverage',3)}x</td>
          <td style="color:{color}">${h['pnl_usd']:+.2f}</td>
          <td style="color:{color}">{h['pnl_pct']:+.1f}%</td>
          <td>{filled}</td>
          <td style="font-size:0.8em">{h.get('reason','')}</td>
        </tr>"""
    if not hist_rows:
        hist_rows = '<tr><td colspan="11" style="text-align:center;color:#6b7280">暂无历史记录</td></tr>'

    # Optimization log
    opt_rows = ""
    for entry in reversed(state.get("optimization_log", [])[-5:]):
        changes_html = "<br>".join(entry.get("changes", ["✅ 无调整"])) or "✅ 无调整"
        s = entry.get("stats", {})
        opt_rows += f"""
        <tr>
          <td>{entry.get('date','')}</td>
          <td>{s.get('closed',0)}笔 / 取消{s.get('cancel_rate',0)}%</td>
          <td>{s.get('win_rate',0):.1f}%</td>
          <td>${s.get('total_pnl',0):+.2f}</td>
          <td style="font-size:0.85em">{changes_html}</td>
        </tr>"""

    update_time = fmt_cst(state.get("last_updated", ""))
    ttl_days    = state.get("order_ttl_days", ORDER_TTL_DAYS)

    return f"""
<!-- ═══════════════ 模拟交易持仓 v3 ═══════════════ -->
<section id="paper-trading">
  <h2>📊 模拟合约交易持仓</h2>
  <p class="sub-note">初始资金 $1,000 · 动态仓位 · 限价挂单 · TTL={ttl_days}天 · 更新: {update_time}</p>

  <div class="paper-summary">
    <div class="ps-card"><div class="ps-label">当前资产</div>
      <div class="ps-value" style="color:#60a5fa">${cap:,.2f}</div></div>
    <div class="ps-card"><div class="ps-label">总盈亏</div>
      <div class="ps-value" style="color:{pnl_color}">${total_pnl:+.2f} ({total_pct:+.1f}%)</div></div>
    <div class="ps-card"><div class="ps-label">胜率</div>
      <div class="ps-value">{win_rate:.0f}% ({win_count}胜/{loss_count}负)</div></div>
    <div class="ps-card"><div class="ps-label">已实现P&L</div>
      <div class="ps-value" style="color:{('#4ade80' if state.get('realized_pnl',0)>=0 else '#f87171')}">${state.get('realized_pnl',0):+.2f}</div></div>
  </div>

  <div class="risk-panel">
    <h4>⚖️ 风险控制面板 {mode_badge}</h4>
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:8px">
      <div>
        <div style="font-size:0.8em;color:#9ca3af">回撤进度 ({drawdown:.1f}% / {cfg.get('max_drawdown_pct',5):.0f}%)</div>
        <div style="background:#1e293b;border-radius:4px;height:8px;width:160px;margin-top:4px">
          <div style="background:{'#f87171' if dd_pct>80 else '#fbbf24' if dd_pct>50 else '#4ade80'};height:8px;border-radius:4px;width:{dd_pct:.0f}%"></div>
        </div>
      </div>
      <div>
        <div style="font-size:0.8em;color:#9ca3af">盈利进度 ({total_pct:+.1f}% / {cfg.get('profit_target_max_pct',15):.0f}%)</div>
        <div style="background:#1e293b;border-radius:4px;height:8px;width:160px;margin-top:4px">
          <div style="background:{'#fbbf24' if pnl_p_pct>100 else '#4ade80'};height:8px;border-radius:4px;width:{min(pnl_p_pct,100):.0f}%"></div>
        </div>
      </div>
      <div style="font-size:0.85em;color:#9ca3af">
        峰值: ${peak:.2f} &nbsp;|&nbsp; 杠杆系数: {cfg.get('leverage_multiplier',1):.1f}x &nbsp;|&nbsp; 信号门槛: p{cfg.get('min_prob_score',10)} &nbsp;|&nbsp; 上次优化: {last_opt}
      </div>
    </div>
  </div>

  <h3>📋 限价挂单 ({len(pending)} 单)</h3>
  <div class="table-wrap"><table class="paper-table">
    <thead><tr>
      <th>币种</th><th>挂单类型</th><th>挂单价</th><th>当前价</th><th>价差</th>
      <th>保证金</th><th>杠杆</th><th>信号/权重</th><th>下单日期</th><th>成交状态</th><th>到期</th>
    </tr></thead>
    <tbody>{pending_rows if pending_rows else '<tr><td colspan="11" style="text-align:center;color:#6b7280">无待成交挂单</td></tr>'}</tbody>
  </table></div>

  <h3>🔥 持仓中 ({len(open_pos)} 仓)</h3>
  <div class="table-wrap"><table class="paper-table">
    <thead><tr>
      <th>币种</th><th>方向</th><th>挂单价</th><th>入场价</th><th>当前价</th>
      <th>杠杆</th><th>保证金</th><th>浮动盈亏</th><th>盈亏率</th><th>止盈/止损</th><th>成交日期</th><th>状态</th>
    </tr></thead>
    <tbody>{pos_rows if pos_rows else '<tr><td colspan="12" style="text-align:center;color:#6b7280">无持仓</td></tr>'}</tbody>
  </table></div>

  <h3>近7日成交明细</h3>
  <div class="table-wrap"><table class="paper-table">
    <thead><tr>
      <th>下单日期</th><th>币种</th><th>方向</th><th>挂单价</th><th>入场价</th>
      <th>平仓价</th><th>杠杆</th><th>P&L($)</th><th>P&L%</th><th>是否成交</th><th>原因</th>
    </tr></thead>
    <tbody>{hist_rows}</tbody>
  </table></div>

  <h3>算法优化日志</h3>
  <div class="table-wrap"><table class="paper-table">
    <thead><tr><th>日期</th><th>样本</th><th>胜率</th><th>总P&L</th><th>调整内容</th></tr></thead>
    <tbody>{opt_rows if opt_rows else '<tr><td colspan="5" style="text-align:center;color:#6b7280">暂无优化记录</td></tr>'}</tbody>
  </table></div>
</section>
"""


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "init":
        cmd_init()
    elif cmd == "update":
        state = cmd_update()
        print_state(state)
    elif cmd == "status":
        cmd_status()
    elif cmd == "reopen":
        cmd_init(prices=get_current_prices())
    elif cmd == "cancel":
        cmd_cancel()
    elif cmd == "optimize":
        cmd_optimize()
    elif cmd == "reset":
        cmd_reset()
    else:
        print(f"未知命令: {cmd}  (init|update|status|reopen|cancel|optimize|reset)")
