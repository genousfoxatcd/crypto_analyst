#!/usr/bin/env python3
"""
虚拟合约模拟交易系统 — paper_trader.py  v3.0
交易模式: 挂单制 (限价单，模拟币安合约)
交易窗口: TTL=5天  |  目标盈利: +10%~+15%  |  最大回撤: 5%

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
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))
ORDER_TTL_DAYS = 5

DEFAULT_RISK_CONFIG = {
    "max_drawdown_pct":      3.0,   # 最大允许回撤
    "profit_target_min_pct": 5.0,   # 目标盈利下限
    "profit_target_max_pct": 10.0,  # 目标盈利上限（超过后锁利告警）
    "min_prob_score":        10,    # 最低信号强度门槛
    "leverage_multiplier":   1.0,   # 优化后调整的杠杆系数（0.7~1.3）
    "max_position_weight":   0.30,  # 单仓最大占比
    "min_position_weight":   0.10,  # 单仓最小占比
}


def fmt_cst(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(CST).strftime("%Y-%m-%dT%H:%M:%S CST")
    except Exception:
        return iso_str[:19] + " CST"

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

BASE              = Path("/Users/alex/hermes_claud/super_mario/crypto-signal")
POSITIONS_FILE    = BASE / "paper_positions.json"
HISTORY_FILE      = BASE / "paper_trade_history.json"
SIGNAL_FILE       = BASE / "signal_v2_latest.json"

INITIAL_CAPITAL   = 1000.0
BINANCE_MAKER_FEE = 0.0002   # 限价单 maker 手续费 0.02%（开仓+平仓各收一次）
COINS = ["BTC", "ETH", "BNB", "SOL", "DOGE", "TAO", "ZEC", "CAKE"]
BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT",
    "SOL": "SOLUSDT", "DOGE": "DOGEUSDT", "TAO": "TAOUSDT",
}


# ── 实时价格 ──────────────────────────────────────────────
def get_current_prices() -> dict:
    prices = {}
    if not REQUESTS_OK:
        return prices
    try:
        resp = requests.get("https://api.binance.com/api/v3/ticker/price", timeout=10)
        resp.raise_for_status()
        data = {item["symbol"]: float(item["price"]) for item in resp.json()}
        for coin, sym in BINANCE_SYMBOLS.items():
            if sym in data:
                prices[coin] = data[sym]
    except Exception as e:
        print(f"[Binance] 价格获取失败: {e}", file=sys.stderr)
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


# ── 动态仓位计算 ──────────────────────────────────────────
def calc_position_sizes(signals: dict, available_capital: float, risk_config: dict) -> dict:
    """
    依据 prob_score 和波动率动态分配保证金和杠杆。
    高 prob_score → 高权重 → 多资金；高波动（宽 entry_zone）→ 低杠杆。
    返回 {coin: {"margin": float, "leverage": int, "weight_pct": float}}
    """
    active = {c: s for c, s in signals.items()
              if s.get("direction", "NEUTRAL") != "NEUTRAL"
              and abs(s.get("prob_score", 0)) >= risk_config.get("min_prob_score", 10)}
    if not active:
        return {}

    # 置信度权重（prob_score 越高 → 越多资金）
    raw_scores = {c: max(abs(s.get("prob_score", 10)), 10) for c, s in active.items()}
    total_score = sum(raw_scores.values())
    raw_weights = {c: raw_scores[c] / total_score for c in active}

    # 限制单仓权重
    w_max = risk_config.get("max_position_weight", 0.30)
    w_min = risk_config.get("min_position_weight", 0.10)
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

        # 应用优化系数，范围 [1, 5]
        leverage = max(1, min(5, round(leverage * lev_mult)))
        result[coin] = {
            "margin":      margin,
            "leverage":    leverage,
            "weight_pct":  round(weights[coin] * 100, 1),
            "prob_score":  raw_scores.get(coin, 10),
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
    open_coins    = {p["coin"] for p in open_pos}
    used_margin   = sum(p["margin"] for p in open_pos)
    effective_cap = round(INITIAL_CAPITAL + realized, 2)          # capital grows/shrinks with realized P&L
    available     = max(effective_cap - used_margin, 0)

    # 动态仓位计算（仅对无 OPEN 仓的币）
    signals_for_sizing = {c: s for c, s in signals.items() if c not in open_coins}
    sizes = calc_position_sizes(signals_for_sizing, available, risk_config)

    new_orders = []
    for coin in COINS:
        if coin not in sizes:
            if coin in open_coins:
                print(f"  [{coin}] ⏭️ 已有 OPEN 持仓，跳过")
            continue

        sig       = signals.get(coin, {})
        direction = sig.get("direction", "NEUTRAL")
        if direction == "NEUTRAL":
            continue

        sz         = sizes[coin]
        margin     = sz["margin"]
        leverage   = sz["leverage"]
        weight_pct = sz["weight_pct"]
        prob       = sz["prob_score"]

        zone     = sig.get("entry_zone", [None, None])
        zone_low = zone[0] if zone else None
        zone_hi  = zone[1] if zone else None
        entry_mid = sig.get("entry_mid")

        if entry_mid:
            limit_price = entry_mid
        elif zone_low and zone_hi:
            limit_price = round((zone_low + zone_hi) / 2, 6)
        else:
            limit_price = prices.get(coin)

        if not limit_price:
            print(f"  [{coin}] ⚠️ 无法确定挂单价，跳过")
            continue

        notional = round(margin * leverage, 2)
        quantity = round(notional / limit_price, 8)
        cp       = prices.get(coin, limit_price)
        gap_pct  = (limit_price - cp) / cp * 100 if cp else 0

        if direction == "LONG":
            fill_hint = "✅即时" if cp <= limit_price else f"↘{abs(gap_pct):.1f}%"
        else:
            fill_hint = "✅即时" if cp >= limit_price else f"↗{abs(gap_pct):.1f}%"

        new_orders.append({
            "coin":            coin,
            "direction":       direction,
            "order_type":      "LIMIT_BUY" if direction == "LONG" else "LIMIT_SELL",
            "limit_price":     round(limit_price, 6),
            "entry_zone_low":  zone_low,
            "entry_zone_high": zone_hi,
            "entry_price":     None,
            "current_price":   round(cp, 6),
            "quantity":        quantity,
            "leverage":        leverage,
            "margin":          margin,
            "notional":        notional,
            "weight_pct":      weight_pct,
            "prob_score":      prob,
            "tp1":             sig.get("tp1"),
            "tp2":             sig.get("tp2"),
            "sl":              sig.get("sl"),
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
        "positions":            open_pos + new_orders,
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

    print(f"\n✅ 挂单完成 — {len(new_orders)} 单  可用 ${available:.0f}  TTL={ORDER_TTL_DAYS}天")
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
            if (dir == "LONG" and cp <= lp) or (dir == "SHORT" and cp >= lp):
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

        dir = pos["direction"]
        closed, reason = False, ""
        if dir == "LONG":
            if pos["tp2"] and cp >= pos["tp2"]:   closed = True; reason = f"🎯 TP2触发 @${cp:,.4f}"
            elif pos["tp1"] and cp >= pos["tp1"]: closed = True; reason = f"✅ TP1触发 @${cp:,.4f}"
            elif pos["sl"]  and cp <= pos["sl"]:  closed = True; reason = f"🛑 SL触发 @${cp:,.4f}"
        else:
            if pos["tp2"] and cp <= pos["tp2"]:   closed = True; reason = f"🎯 TP2触发 @${cp:,.4f}"
            elif pos["tp1"] and cp <= pos["tp1"]: closed = True; reason = f"✅ TP1触发 @${cp:,.4f}"
            elif pos["sl"]  and cp >= pos["sl"]:  closed = True; reason = f"🛑 SL触发 @${cp:,.4f}"

        if closed:
            close_fee   = round(cp * pos["quantity"] * BINANCE_MAKER_FEE, 4)
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
        else:
            open_pnl += pnl_usd

    state["realized_pnl"]    = round(state.get("realized_pnl", 0.0) + closed_pnl, 4)
    state["effective_capital"] = round(INITIAL_CAPITAL + state["realized_pnl"], 2)
    state["current_capital"] = round(state["effective_capital"] + open_pnl, 4)
    state["last_updated"]    = now_str

    alerts = check_risk_controls(state)
    for a in alerts:
        print(f"  {a}")

    POSITIONS_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))

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

    # 挂单队列
    if pending:
        print(f"\n  📋 挂单队列 ({len(pending)} 单，TTL={state.get('order_ttl_days', ORDER_TTL_DAYS)}天)")
        print(f"  {'币':^5} {'类型':^12} {'挂单价':>11} {'当前价':>11} {'价差':>7}  "
              f"{'保证金':>7} {'杠杆':>5} {'预期盈利':>8} {'止损风险':>8} {'盈利概率':>8}  "
              f"{'下单日期':^11} {'成交状态':^6} {'到期':^8} {'剩余'}")
        print(f"  {'-'*110}")
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
            print(f"  {p['coin']:^5} {p['order_type']:^12} "
                  f"${lp:>9,.4f} ${cp:>9,.4f} {gap:>+6.2f}%  "
                  f"${p['margin']:>5.0f} {p['leverage']:>4}x "
                  f"{exp_ret:>+7.1f}% {sl_risk:>+7.1f}% {prob:>7}%  "
                  f"{placed:^11} {'未成交':^6} {expiry:^8} {ttl_h:.0f}h")
            print(f"  {'':^5} {'':^12}   TP1:{tp_str:>12}  SL:{sl_str:>12}  "
                  f"盈亏比: {rr:.2f}x")

    # 持仓中
    if open_pos:
        print(f"\n  🔥 持仓中 ({len(open_pos)} 仓)")
        print(f"  {'币':^5} {'方向':^5} {'挂单价':>11} {'入场价':>11} {'当前价':>11} "
              f"{'杠杆':>5} {'保证金':>7} {'P&L($)':>9} {'P&L%':>7} {'成交日期':^12} {'状态'}")
        print(f"  {'-'*95}")
        for pos in open_pos:
            cp       = pos.get("current_price", pos["entry_price"])
            lp_str   = f"${pos['limit_price']:,.4f}" if pos.get("limit_price") else "  市价  "
            filled   = fmt_date(pos.get("filled_at", pos.get("opened_at", "")))
            print(f"  {pos['coin']:^5} {pos['direction']:^5} "
                  f"{lp_str:>11} "
                  f"${pos['entry_price']:>9,.4f} "
                  f"${cp:>9,.4f} "
                  f"{pos['leverage']:>4}x "
                  f"${pos['margin']:>6.0f} "
                  f"${pos['pnl_usd']:>+8.2f} "
                  f"{pos['pnl_pct']:>+6.1f}% "
                  f"{filled:^12} 🟢 OPEN")

    if not pending and not open_pos:
        print("\n  （无挂单和持仓）")

    # 近7日明细
    if HISTORY_FILE.exists():
        history = json.loads(HISTORY_FILE.read_text())
        if history:
            print(f"\n  {'─'*75}")
            print("  近7日明细:")
            print(f"  {'下单日期':^11} {'币':^5} {'方向':^5} {'挂单价':>10} {'入场价':>10} "
                  f"{'平仓价':>10} {'净P&L':>9} {'费用':>7} {'是否成交':^8} {'到期/原因'}")
            print(f"  {'─'*80}")
            for h in history[-10:]:
                placed  = fmt_date(h.get("placed_at") or h.get("closed_at", ""))
                icon    = "✅" if h["pnl_usd"] > 0 else ("⏰" if "取消" in h.get("reason","") else "🔴" if h["pnl_usd"] < 0 else "—")
                lp_s    = f"${h['limit_price']:,.4f}" if h.get("limit_price") else "  N/A  "
                ep_s    = f"${h['entry_price']:,.4f}" if h.get("entry_price") else "  N/A  "
                filled  = "已成交" if h.get("filled_at") else "未成交"
                fee_s   = f"-${h.get('fee_usd',0):.4f}" if h.get("fee_usd") else "  —  "
                print(f"  {placed:^11} {h['coin']:^5} {h['direction']:^5} "
                      f"{lp_s:>10} {ep_s:>10} ${h['close_price']:>8,.4f} "
                      f"{icon}${h['pnl_usd']:>+7.2f} {fee_s:>7} {filled:^8} {h.get('reason','')}")


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
    else:
        print(f"未知命令: {cmd}  (init|update|status|reopen|cancel|optimize)")
