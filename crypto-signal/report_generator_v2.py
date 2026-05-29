#!/usr/bin/env python3
"""
加密货币信号报告生成器 v2
读取 signal_v2_latest.json + paper_positions.json → HTML 报告
优化版: 新价格格式、10x/20x爆仓、鲸鱼持仓、ETF流量、移除数据源链接
"""

import json
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import webbrowser  # 自动在浏览器中打开报告

# 尝试导入贝叶斯信号生成器
try:
    from bayesian_signal_generator import BayesianSignalGenerator
    HAS_BAYESIAN = True
except ImportError:
    HAS_BAYESIAN = False
    print("  [BayesianSignalGenerator] 模块未找到，跳过贝叶斯展示")

# 尝试导入 DeFi Llama 数据构建函数
try:
    from defillama_fetcher import build_defi_section
    HAS_DEFILAMA = True
except ImportError:
    HAS_DEFILAMA = False
    print("  [DeFiLlama] 模块未找到，跳过 DeFi TVL 展示")

# 尝试导入代币解锁追踪模块
try:
    from token_unlock_tracker import get_unlock_summary_html
    HAS_UNLOCK_TRACKER = True
except ImportError:
    HAS_UNLOCK_TRACKER = False
    print("  [TokenUnlock] 模块未找到，跳过解锁数据展示")

CRYPTO_WS = Path(os.environ.get("CRYPTO_ANALYST_WS", "/Users/alex/projects/crypto_analyst"))
BASE         = CRYPTO_WS / "crypto-signal"
SIGNAL_FILE  = BASE / "signal_v2_latest.json"
PAPER_FILE   = BASE / "paper_positions.json"
REPORTS_DIR  = CRYPTO_WS / "reports"

CST = timezone(timedelta(hours=8), "CST")  # UTC+8 北京时间

OPENCLUE_DIMS_FILE = BASE / "openclue_latest_dims.json"


# ── OpenClue 10维度分析集成 ─────────────────────────────────
def build_openclue_section() -> str:
    """OpenClue 核心数据指标 + 币种趋势判断 + 策略 — 作为报告第一部分"""
    if not OPENCLUE_DIMS_FILE.exists():
        return ""
    try:
        dims = json.loads(OPENCLUE_DIMS_FILE.read_text())
    except Exception:
        return ""

    mcap = dims.get("mcap", {}) or {}
    stance = dims.get("stance", {}) or {}
    stances = stance.get("stances", {}) or {}
    etf = dims.get("etf_flows", {}) or {}
    fg = dims.get("fear_greed", {}) or {}
    bd = dims.get("btc_dom", {}) or {}
    sc = dims.get("stablecoin", {}) or {}
    fd = dims.get("funding", {}) or {}
    vx = dims.get("vix", {}) or {}
    eq = dims.get("equity", {}) or {}
    pl = dims.get("_phase_label", "⚪ 未判定")

    # ── 1. 宏观核心指标仪表盘 ──────────────────────────
    fg_val = fg.get("value", "?")
    fg_cls = fg.get("classification", "?")
    fg_trend = fg.get("trend", "?")
    fg_color = "#22c55e" if (fg_val if isinstance(fg_val, int) else 50) <= 30 else "#eab308"

    macro_rows = ""
    items = [
        ("总市值", mcap.get("total_market_cap_str", "—"), ""),
        ("恐慌贪婪指数", f"{fg_val} ({fg_cls})", f"趋势:{fg_trend}"),
        ("BTC主导率", f"{bd.get('btc_dominance','?')}%", f"ETH:{bd.get('eth_dominance','?')}%  山寨:{bd.get('alt_dominance','?')}%"),
        ("稳定币储备", sc.get("total_supply_str", "—"), f"购买力:{sc.get('power_level','?')}"),
        ("均资金费率", f"{((fd.get('average_fr_pct',0) or 0)*100):+.4f}%", fd.get("market_temp","?")),
        ("VIX波动率", f"{vx.get('vix_price','?')}", vx.get("vix_analysis","?")),
        ("纳指/美股", f"{eq.get('nasdaq_price','?')} ({(eq.get('nasdaq_change',0) or 0):+.2f}%)", f"相关性:{eq.get('correlation','?')}"),
    ]
    for label, val, sub in items:
        macro_rows += f"<tr><td><b>{label}</b></td><td>{val}</td><td style='color:var(--muted);font-size:0.78rem'>{sub}</td></tr>\n"

    # ── 2. 币种趋势判断表 ──────────────────────────────
    coin_rows = ""
    dir_label = {"LONG": "🟢做多", "SHORT": "🔴做空", "NEUTRAL": "⚪观望"}
    stances = dims.get("stances", {}) if dims else {}
    macro = dims.get("macro", {}) if dims else {}
    # 用 volume_sorted_coins 获取币种排序
    sc = _volume_sorted_coins(data)
    for coin in sc:
        s = stances.get(coin, {})
        if not s:
            continue
        d = s.get("direction", "NEUTRAL")
        score = s.get("score", 0)
        lp = s.get("long_pct", 50)
        tb = s.get("taker_bsr", 1)
        score_color = "#22c55e" if score >= 10 else ("#ef4444" if score <= -10 else "#eab308")
        coin_rows += f"<tr><td><b>{coin}</b></td><td>{dir_label.get(d,d)}</td><td style='color:{score_color}'>{score:+.0f}</td><td>{lp:.0f}%</td><td>{tb:.2f}</td></tr>\n"

    # ── 3. ETF推断 ─────────────────────────────────────
    btc_inf = etf.get("btc_inference", "")
    eth_inf = etf.get("eth_inference", "")
    etf_text = ""
    if btc_inf:
        etf_text += f"<p><b>BTC:</b> {btc_inf}</p>"
    if eth_inf:
        etf_text += f"<p><b>ETH:</b> {eth_inf}</p>"

    # ── 4. 策略建议 ────────────────────────────────────
    advice = ""
    conclusion = stance.get("conclusion", "")
    alt_advice = bd.get("alt_advice", "")
    macro_outlook = eq.get("macro_outlook", "")
    if conclusion:
        advice += f"<li><b>多空立场</b>：{conclusion}</li>"
    if alt_advice:
        advice += f"<li><b>板块配置</b>：{alt_advice}</li>"
    if macro_outlook:
        advice += f"<li><b>宏观展望</b>：{macro_outlook[:100]}</li>"

    return f"""<section id="openclue">
  <h2>🌐 OpenClue 核心分析框架 <span style="font-size:0.7rem;color:var(--muted)">openclue.net</span></h2>
  <div class="gen-time" style="margin:-8px 0 12px;color:var(--muted)">市场阶段: {pl} · 10维度综合分析</div>

  <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-bottom:14px">
    <div class="ps-card" style="padding:10px"><div class="ps-label" style="font-size:0.70rem">总市值</div><div class="ps-value" style="font-size:0.95rem">{mcap.get('total_market_cap_str','—')}</div></div>
    <div class="ps-card" style="padding:10px"><div class="ps-label" style="font-size:0.70rem">恐慌贪婪</div><div class="ps-value" style="font-size:0.95rem;color:{fg_color}">{fg_val}</div></div>
    <div class="ps-card" style="padding:10px"><div class="ps-label" style="font-size:0.70rem">BTC主导率</div><div class="ps-value" style="font-size:0.95rem">{bd.get('btc_dominance','?')}%</div></div>
    <div class="ps-card" style="padding:10px"><div class="ps-label" style="font-size:0.70rem">稳定币储备</div><div class="ps-value" style="font-size:0.95rem">{sc.get('total_supply_str','—')}</div></div>
  </div>

  <h3 style="font-size:0.90rem;margin:10px 0 6px">📊 宏观核心指标</h3>
  <div class="table-wrap">
  <table>
    <thead><tr><th>指标</th><th>数值</th><th>说明</th></tr></thead>
    <tbody>{macro_rows}</tbody>
  </table>
  </div>

  <h3 style="font-size:0.90rem;margin:14px 0 6px">🪙 各币种趋势判断</h3>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>方向</th><th>综合得分</th><th>多头占比</th><th>吃单比</th></tr></thead>
    <tbody>{coin_rows}</tbody>
  </table>
  </div>

  <h3 style="font-size:0.90rem;margin:14px 0 6px">🏦 ETF资金流推断</h3>
  <div class="advice-box" style="padding:10px 14px;margin:0 0 10px">
    {etf_text if etf_text else '<p style="color:var(--muted);margin:0">数据计算中...</p>'}
  </div>

  <h3 style="font-size:0.90rem;margin:14px 0 6px">🎯 策略建议</h3>
  <div class="advice-box" style="padding:10px 14px;margin:0">
    <ul style="margin:0;padding-left:18px;font-size:0.83rem">{advice if advice else '<li style="color:var(--muted)">分析中...</li>'}</ul>
  </div>
  <hr style="border:none;border-top:1px solid var(--border);margin:14px 0">
</section>"""


# ── 价格格式化 ──────────────────────────────────────────────
#   ≥ $10 → 1位小数    $1~$10 → 2位小数    < $1 → 3位小数
def fmt_price(p, coin=""):
    if p is None: return "—"
    if abs(p) >= 10:   return f"${p:,.1f}"
    if abs(p) >= 1:    return f"${p:,.2f}"
    return f"${p:,.3f}"


def fmt_price_num(p):
    """纯数字格式化（不加$前缀）"""
    if p is None: return "—"
    if abs(p) >= 10:   return f"{p:,.1f}"
    if abs(p) >= 1:    return f"{p:,.2f}"
    return f"{p:,.3f}"


def change_span(v):
    if v is None: return "<span>—</span>"
    color = "#4ade80" if v >= 0 else "#f87171"
    sign  = "+" if v >= 0 else ""


def _volume_sorted_coins(data):
    """按 24h 交易量降序排列所有币种"""
    prices = data.get("prices", {})
    signals = data.get("signals", {})
    # 只保留 signals 中存在的币种（有信号的）
    coin_vols = {}
    for coin in signals:
        pd = prices.get(coin, {})
        vol = pd.get("volume_24h", 0) or 0
        coin_vols[coin] = vol
    # 降序排列
    return [c for c, _ in sorted(coin_vols.items(), key=lambda x: x[1], reverse=True)]
    return f'<span style="color:{color}">{sign}{v:.2f}%</span>'


# ── Table builders ──────────────────────────────────────────

def build_contract_table(data, coin_order=None):
    signals = data.get("signals", {})
    rows = ""
    for coin in (coin_order or [c for c in signals]):
        sd = signals.get(coin)
        if not sd:
            continue
        fr  = sd.get("funding_rate", 0) or 0
        liq = sd.get("liquidation", {}) or {}
        ls  = liq.get("ls_ratio", 1) or 1
        flow = sd.get("flow_buy_pct", 50) or 50
        fr_color  = "#f87171" if fr > 0.002 else ("#4ade80" if fr < -0.001 else "#e5e7eb")
        ls_color  = "#f87171" if ls > 1.5 else ("#4ade80" if ls < 0.8 else "#e5e7eb")
        flow_color= "#4ade80" if flow > 55 else ("#f87171" if flow < 45 else "#e5e7eb")
        # 多仓均价 / 空仓均价 = 5x-10x 清算价均值
        liq_l = liq.get("liq_long_zones", {}) or {}
        liq_s = liq.get("liq_short_zones", {}) or {}
        avg_l_str = "—"
        avg_s_str = "—"
        if liq_l.get("5x") and liq_l.get("10x"):
            avg_l = (float(liq_l["5x"]) + float(liq_l["10x"])) / 2
            avg_l_str = fmt_price(avg_l, coin)
        if liq_s.get("5x") and liq_s.get("10x"):
            avg_s = (float(liq_s["5x"]) + float(liq_s["10x"])) / 2
            avg_s_str = fmt_price(avg_s, coin)
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b></td>"
            f"<td style='color:{fr_color}'>{fr:+.3f}%</td>"
            f"<td style='color:{ls_color}'>{ls:.2f}</td>"
            f"<td style='color:#e5e7eb;font-weight:500'>{avg_l_str}</td>"
            f"<td style='color:#e5e7eb;font-weight:500'>{avg_s_str}</td>"
            f"<td style='color:{flow_color}'>{flow:.1f}%</td>"
            f"</tr>\n"
        )
    return rows


def build_tech_table(data):
    signals = data.get("signals", {})
    rows = ""
    for coin, sd in signals.items():
        bb = sd.get("bb", {}) or {}
        atr_abs = sd.get("atr", 0) or 0
        price   = sd.get("price", 1) or 1
        atr_pct = atr_abs / price * 100 if price else 0
        pos     = bb.get("pos_pct", 50) or 50
        flow    = sd.get("flow_buy_pct", 50) or 50
        pos_color  = "#f87171" if pos > 100 else ("#f59e0b" if pos > 85 else ("#4ade80" if pos < 20 else "#e5e7eb"))
        flow_color = "#4ade80" if flow > 55 else ("#f87171" if flow < 45 else "#e5e7eb")
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b></td>"
            f"<td style='color:#6b7280'>{fmt_price(bb.get('lower'), coin)}</td>"
            f"<td>{fmt_price(bb.get('mid'), coin)}</td>"
            f"<td style='color:#6b7280'>{fmt_price(bb.get('upper'), coin)}</td>"
            f"<td style='color:{pos_color}'>{pos:.0f}%</td>"
            f"<td>{atr_pct:.2f}%</td>"
            f"<td style='color:{flow_color}'>{flow:.1f}%</td>"
            f"</tr>\n"
        )
    return rows


def build_liq_table(data, coin_order=None):
    """爆仓区间：10x + 20x 杠杆 + 爆多仓/爆空仓概率 + 定向插针点位"""
    signals = data.get("signals", {})
    prices  = data.get("prices", {})
    rows = ""
    for coin in (coin_order or [c for c in signals]):
        sd = signals.get(coin)
        if not sd:
            continue
        liq = sd.get("liquidation", {}) or {}
        ll10 = liq.get("liq_long_zones", {}).get("10x")
        ll20 = liq.get("liq_long_zones", {}).get("20x")
        ls10 = liq.get("liq_short_zones", {}).get("10x")
        ls20 = liq.get("liq_short_zones", {}).get("20x")
        
        # 当前价格
        cur_price = prices.get(coin, {}).get("price", 0) or 0
        
        # 距离计算（%）
        dist_l10 = ((cur_price - ll10) / cur_price * 100) if (ll10 and cur_price) else None
        dist_l20 = ((cur_price - ll20) / cur_price * 100) if (ll20 and cur_price) else None
        dist_s10 = ((ls10 - cur_price) / cur_price * 100) if (ls10 and cur_price) else None
        dist_s20 = ((ls20 - cur_price) / cur_price * 100) if (ls20 and cur_price) else None
        
        def fmt_dist(v):
            if v is None: return "—"
            color = "#4ade80" if v > 15 else ("#f59e0b" if v > 8 else "#f87171")
            return f'<span style="color:{color}">{v:.1f}%</span>'
        
        # ── 新字段：爆多仓/爆空仓概率 ──
        long_prob = liq.get("liq_long_probability", liq.get("liq_probability", 50))
        short_prob = liq.get("liq_short_probability", liq.get("liq_probability", 50))
        
        def fmt_prob_color(p, is_high=False):
            if p >= 60:
                # 高风险（≥60%）→ 红色加粗，真正需要关注
                return "#ef4444;font-weight:700"
            elif p >= 45:
                # 中风险（45-60%）→ 橙色，值得留意
                return "#f59e0b;font-weight:600"
            else:
                # 低风险（<45%）→ 默认绿色，无需额外关注
                return "#4ade80"
        
        def fmt_prob_cell(p):
            if p >= 60:
                return f'<span style="color:#ef4444;font-weight:700">{p:.0f}%</span>'
            elif p >= 45:
                return f'<span style="color:#f59e0b;font-weight:600">{p:.0f}%</span>'
            else:
                return f'<span style="color:#4ade80">{p:.0f}%</span>'
        
        # ── 新字段：爆多仓/爆空仓插针点位（价格精度自适应）──
        pin_long = liq.get("pin_long_price")
        pin_short = liq.get("pin_short_price")
        pin_long_str = fmt_price(pin_long, coin) if pin_long is not None else "—"
        pin_short_str = fmt_price(pin_short, coin) if pin_short is not None else "—"
        
        price_str = fmt_price(cur_price, coin)
        
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b></td>"
            f"<td style='color:#e5e7eb'>{price_str}</td>"
            f"<td style='color:#4ade80'>{fmt_price(ll10, coin)}<br><small>{fmt_dist(dist_l10)}</small></td>"
            f"<td style='color:#4ade80'>{fmt_price(ll20, coin)}<br><small>{fmt_dist(dist_l20)}</small></td>"
            f"<td style='color:#f87171'>{fmt_price(ls10, coin)}<br><small>{fmt_dist(dist_s10)}</small></td>"
            f"<td style='color:#f87171'>{fmt_price(ls20, coin)}<br><small>{fmt_dist(dist_s20)}</small></td>"
            f"<td style='color:{fmt_prob_color(long_prob)}'>{long_prob:.0f}%</td>"
            f"<td style='color:{fmt_prob_color(short_prob)}'>{short_prob:.0f}%</td>"
            f"<td style='color:#f59e0b'>{pin_long_str}</td>"
            f"<td style='color:#f59e0b'>{pin_short_str}</td>"
            f"</tr>\n"
        )
    return rows


def build_whale_table(data):
    """鲸鱼持仓多空占比"""
    whales = data.get("whales", {}) or {}
    available = {c: w for c, w in whales.items() if w.get("whale_long_ratio") is not None}
    if not available:
        return ""
    rows = ""
    for coin, w in available.items():
        lr = w.get("whale_long_ratio", 50)
        sr = w.get("whale_short_ratio", 50)
        if lr > 55:
            bias = '<span style="color:#4ade80">🟢多头优势</span>'
            bias_color = "#4ade80"
        elif lr < 45:
            bias = '<span style="color:#f87171">🔴空头优势</span>'
            bias_color = "#f87171"
        else:
            bias = '<span style="color:#e5e7eb">⚪均衡</span>'
            bias_color = "#e5e7eb"
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b></td>"
            f"<td>{lr:.0f}%</td>"
            f"<td>{sr:.0f}%</td>"
            f"<td style='color:{bias_color}'>{bias}</td>"
            f"</tr>\n"
        )
    return rows


def build_etf_row(data):
    """BTC ETF 24h/48h/72h 净流量"""
    etf = data.get("etf_flow", {}) or {}
    if etf.get("24h_flow_usd_m") is not None:
        flow = etf["24h_flow_usd_m"]
        flow_48 = etf.get("48h_flow_usd_m")
        flow_72 = etf.get("72h_flow_usd_m")
        src  = etf.get("source", "")
        color = "#4ade80" if flow > 0 else ("#f87171" if flow < 0 else "#e5e7eb")
        sign  = "+" if flow >= 0 else ""
        cells  = f'<td style="color:{color};font-weight:700">${sign}{flow:.1f}M</td>'
        if flow_48 is not None:
            s48 = "+" if flow_48 >= 0 else ""
            cells += f'<td style="color:{color}">${s48}{flow_48:.1f}M</td>'
        else:
            cells += '<td style="color:var(--muted)">—</td>'
        if flow_72 is not None:
            s72 = "+" if flow_72 >= 0 else ""
            cells += f'<td style="color:{color}">${s72}{flow_72:.1f}M</td>'
        else:
            cells += '<td style="color:var(--muted)">—</td>'
        return f'<tr><td><b>BTC ETF流量</b></td>{cells}<td>{src}</td></tr>'
    return '<tr><td><b>BTC ETF流量</b></td><td style="color:var(--muted)">数据不可用</td><td>—</td><td>—</td><td>—</td></tr>'


def build_eth_etf_row(data):
    """ETH ETF 24h/48h/72h 净流量"""
    eth_etf = data.get("eth_etf_flow", {}) or {}
    if eth_etf.get("24h_flow_usd_m") is not None:
        flow = eth_etf["24h_flow_usd_m"]
        flow_48 = eth_etf.get("48h_flow_usd_m")
        flow_72 = eth_etf.get("72h_flow_usd_m")
        src  = eth_etf.get("source", "")
        color = "#4ade80" if flow > 0 else ("#f87171" if flow < 0 else "#e5e7eb")
        sign  = "+" if flow >= 0 else ""
        cells  = f'<td style="color:{color};font-weight:700">${sign}{flow:.1f}M</td>'
        if flow_48 is not None:
            s48 = "+" if flow_48 >= 0 else ""
            cells += f'<td style="color:{color}">${s48}{flow_48:.1f}M</td>'
        else:
            cells += '<td style="color:var(--muted)">—</td>'
        if flow_72 is not None:
            s72 = "+" if flow_72 >= 0 else ""
            cells += f'<td style="color:{color}">${s72}{flow_72:.1f}M</td>'
        else:
            cells += '<td style="color:var(--muted)">—</td>'
        return f'<tr><td><b>ETH ETF流量</b></td>{cells}<td>{src}</td></tr>'
    return '<tr><td><b>ETH ETF流量</b></td><td style="color:var(--muted)">数据不可用</td><td>—</td><td>—</td><td>—</td></tr>'


def build_signal_table(data, coin_order=None):
    signals = data.get("signals", {})
    prices = data.get("prices", {})
    tech   = data.get("tech_data", {})
    contracts = data.get("contracts", {})
    DIR_LABEL = {"LONG": "🟢 做多", "SHORT": "🔴 做空", "NEUTRAL": "⚪ 观望"}
    DIR_CLASS = {"LONG": "long-dir", "SHORT": "short-dir", "NEUTRAL": ""}
    rows = ""
    for coin in (coin_order or [c for c in signals]):
        sd = signals.get(coin)
        if not sd:
            continue
        direction = sd.get("direction", "NEUTRAL")
        entry = sd.get("entry_zone", [None, None])
        entry_ready = sd.get("entry_ready", "unknown") or [None, None]
        entry_str = f"{fmt_price(entry[0], coin)} – {fmt_price(entry[1], coin)}" if entry[0] else "—"
        tp1  = fmt_price(sd.get("tp1"), coin)
        tp2  = fmt_price(sd.get("tp2"), coin)
        sl   = fmt_price(sd.get("sl"), coin)
        lev  = sd.get("leverage", 3)
        prob = sd.get("prob_score", 0)
        rr   = sd.get("rr_ratio", 0) or 0
        rsi  = sd.get("rsi_14")
        rsi_str = f"{rsi:.1f}" if rsi is not None else "—"
        rsi_color = "#4ade80" if rsi is not None and rsi <= 30 else ("#f87171" if rsi is not None and rsi >= 70 else "#e5e7eb")
        rr_color   = "#4ade80" if rr >= 2 else "#f59e0b"
        prob_color = "#4ade80" if prob >= 55 else ("#f59e0b" if prob >= 45 else "#f87171")
        pin_w  = sd.get("pin_warning", "") or ""
        pin_td = f"<br><small style='color:#f59e0b'>⚠️ {pin_w}</small>" if pin_w else ""

        # 合约数据
        ct = contracts.get(coin, {})
        funding_rate = ct.get("funding_rate", 0)
        long_pct = ct.get("long_pct", 50)
        taker_bsr = ct.get("taker_bsr", 1.0)
        long_liq = ct.get("long_liq_price")
        short_liq = ct.get("short_liq_price")

        # 价格数据
        pr = prices.get(coin, {})
        cur_price = pr.get("price", 0)
        chg_24h = pr.get("change_24h", 0)
        vol_str = pr.get("volume_str", "—")

        # 技术数据
        td_tech = tech.get(coin, {})
        flow_buy = td_tech.get("flow_buy_pct")

        # 当前价离入场区间上沿(entry_high)的距离%
        entry_high_val = entry[1] if (entry[1] and entry[1] > 0) else cur_price
        dist_from_entry = 0.0
        if cur_price > 0 and entry_high_val > 0:
            dist_from_entry = (cur_price - entry_high_val) / entry_high_val * 100

        # 构建 data 属性（JSON 编码）
        import json as _json
        detail = _json.dumps({
            "coin": coin,
            "direction": direction,
            "direction_cn": DIR_LABEL.get(direction, direction),
            "prob": prob,
            "leverage": lev,
            "rr": f"{rr:.1f}",
            "entry_low": fmt_price(entry[0], coin) if entry[0] else "—",
            "entry_high": fmt_price(entry[1], coin) if entry[1] else "—",
            "entry_distance": f"{dist_from_entry:+.1f}%" if dist_from_entry else "—",
            "tp1": tp1,
            "tp2": tp2,
            "sl": sl,
            "rsi": rsi_str,
            "funding_rate": f"{funding_rate:.4f}%" if isinstance(funding_rate, (int, float)) else str(funding_rate),
            "long_pct": f"{long_pct:.0f}%" if isinstance(long_pct, (int, float)) else "—",
            "taker_bsr": f"{taker_bsr:.2f}" if isinstance(taker_bsr, (int, float)) else "—",
            "flow_buy": f"{flow_buy:.1f}%" if flow_buy is not None else "—",
            "price": fmt_price(cur_price, coin),
            "change_24h": f"{chg_24h:+.2f}%" if isinstance(chg_24h, (int, float)) else "—",
            "volume": vol_str,
            "pin_warning": pin_w,
            "entry_ready": entry_ready,
            "long_liq": f"${long_liq:,.2f}" if long_liq else "—",
            "short_liq": f"${short_liq:,.2f}" if short_liq else "—",
        }, ensure_ascii=False).replace("'", "\\u0027").replace('"', '&quot;')

        # 入场状态：当前价 vs 入场区间上沿(entry_high)的距离%
        entry_badge = '<span style="color:#6b7280">—</span>'
        if cur_price > 0 and entry_high_val > 0:
            if direction == "NEUTRAL":
                # 观望策略：不判断入场，只显示偏离
                if abs(dist_from_entry) < 1.0:
                    entry_badge = '<span style="color:#6b7280">⚪ 震荡</span>'
                else:
                    sign = "↑" if dist_from_entry > 0 else "↓"
                    entry_badge = f'<span style="color:#6b7280">{sign}偏离 {abs(dist_from_entry):.1f}%</span>'
            elif direction == "LONG":
                # 做多：价格需低于入场上沿(entry_high)才能入场
                if dist_from_entry <= 0:
                    entry_badge = f'<span style="color:#4ade80;font-weight:600">✅ 低于上沿 {abs(dist_from_entry):.1f}%</span>'
                else:
                    entry_badge = f'<span style="color:#f59e0b;font-weight:600">🔺 偏高上沿 {dist_from_entry:.1f}%</span>'
            elif direction == "SHORT":
                # 做空：价格需高于入场下沿(entry_low)才能入场，用上沿距离做参考
                if dist_from_entry >= 0:
                    entry_badge = f'<span style="color:#4ade80;font-weight:600">✅ 高于上沿 {dist_from_entry:.1f}%</span>'
                else:
                    entry_badge = f'<span style="color:#f59e0b;font-weight:600">🔻 低于上沿 {abs(dist_from_entry):.1f}%</span>'

        rows += (
            f"<tr class='signal-clickable' data-detail='{detail}' style='cursor:pointer' title='点击查看{coin}完整信号分析'>"
            f"<td><b>{coin}</b>{pin_td}</td>"
            f"<td style='color:#e5e7eb'>{fmt_price(cur_price, coin)}</td>"
            f"<td class='{DIR_CLASS.get(direction)}'>{DIR_LABEL.get(direction, direction)}</td>"
            f"<td>{entry_str}</td>"
            f"<td>{entry_badge}</td>"
            f"<td>{lev}x</td>"
            f"<td>{tp1}</td>"
            f"<td>{tp2}</td>"
            f"<td>{sl}</td>"
            f"<td style='color:{rr_color}'>{rr:.1f}:1</td>"
            f"<td style='color:{rsi_color}'>{rsi_str}</td>"
            f"<td style='color:{prob_color}'>{prob}%</td>"
            f"</tr>\n"
        )
    return rows


def build_paper_section(paper_file: Path) -> str:
    if not paper_file.exists():
        return ""
    sys.path.insert(0, str(BASE))
    try:
        import paper_trader
        state = json.loads(paper_file.read_text())
        return paper_trader.generate_html_section(state)
    except Exception as e:
        return f'<section id="paper-trading"><p style="color:#f87171">模拟交易加载失败: {e}</p></section>'


# ── 宏觀策略摘要（报告第一部分）────────────────────────────────
def build_macro_summary_section(data: dict) -> str:
    """构建宏观策略摘要 HTML 区块，放在报告最顶部"""

    # 删除旧函数体，完全重写为调用组建函数
    return _build_macro_summary(data)


def _build_macro_summary(data: dict) -> str:
    """宏观策略摘要主构建函数"""
    ma = data.get("macro_assessment", {}) or {}
    assessment = ma.get("assessment", {}) or {}
    fng = ma.get("fear_greed", {}) or {}
    mkt = ma.get("market_overview", {}) or {}
    sc  = ma.get("stablecoin", {}) or {}
    coin_trends = assessment.get("coin_trends", {}) or {}
    seg = assessment.get("segment_analysis", {}) or {}

    if not assessment:
        return _build_compact_macro(data)

    phase_label = assessment.get("phase_label", "⚪ 未判定")
    phase_score = assessment.get("phase_score", 0)
    risk_level  = assessment.get("risk_level", "?")
    strategy    = assessment.get("strategy", "?")
    signals_list = assessment.get("signals", [])
    deltas      = data.get("macro_deltas", {}) or {}       # ← 环比增减数据

    # 辅助函数：格式化 delta 显示
    def _fmt_delta(val, unit="", force_pct=False):
        """格式化环比变化：正=绿 ↑，负=红 ↓，0=灰 —"""
        if val is None:
            return ""
        if force_pct:
            display = f"{val:+.2f}{unit}"
        elif unit == "%":
            display = f"{val:+.2f}%"
        else:
            display = f"{val:+d}{unit}" if isinstance(val, int) else f"{val:+.1f}{unit}"
        if val > 0:
            return f'<span style="font-size:0.75rem;color:#4ade80">↑{display}</span>'
        elif val < 0:
            return f'<span style="font-size:0.75rem;color:#f87171">↓{abs(val)}{unit}</span>'
        return f'<span style="font-size:0.75rem;color:var(--muted)">— 持平</span>'

    phase_color_map = {"capitulation":"#a855f7","fear_bottom":"#22c55e","accumulation":"#22c55e",
                       "bull_early":"#eab308","bull_active":"#f97316","euphoria":"#ef4444","neutral":"#6b7280"}
    phase_color = phase_color_map.get(assessment.get("phase", "neutral"), "#6b7280")
    risk_color_map = {"low":"#22c55e","medium":"#eab308","high":"#ef4444"}
    risk_color = risk_color_map.get(risk_level, "#6b7280")

    # ── 宏观指标卡片 ─────────────────────────────────────
    card_rows = ""

    if fng.get("value") is not None:
        v = fng['value']
        fng_color = "#22c55e" if v <= 35 else ("#eab308" if v <= 55 else "#f97316" if v <= 75 else "#ef4444")
        fng_icon = "🟢" if v <= 35 else ("🟡" if v <= 55 else "🟠" if v <= 75 else "🔴")
        delta = fng.get("delta_7d", 0)
        delta_icon = "📈" if delta >= 0 else "📉"
        # 1日环比
        fng_d1 = deltas.get("fng_delta_1d")
        fng_d1_str = _fmt_delta(fng_d1) if fng_d1 is not None else ""
        card_rows += f"""
    <div class="macro-card"><span class="macro-label">😱 恐惧贪婪</span>
        <span class="macro-val" style="color:{fng_color}">{v} {fng_icon}</span>
        <span class="macro-sub">{fng.get('classification_cn','?')} · {delta_icon}{abs(delta)} (7d) {fng_d1_str}</span></div>"""

    if mkt.get("btc_dominance") is not None:
        dom = mkt['btc_dominance']
        dom_note = "🏦 BTC主导" if dom > 60 else "⚖️ 中性" if dom > 45 else "🚀 山寨活跃"
        dom_delta = deltas.get("dom_delta")
        dom_delta_str = _fmt_delta(dom_delta, "%") if dom_delta is not None else ""
        card_rows += f"""
    <div class="macro-card"><span class="macro-label">🏛️ BTC主导率</span>
        <span class="macro-val">{dom}%</span>
        <span class="macro-sub">{dom_note} {dom_delta_str}</span></div>"""

    if mkt.get("total_market_cap_str"):
        mc = mkt.get('total_market_cap', 0)
        mc_note = "高位" if mc > 3e12 else "正常" if mc > 1.5e12 else "低位"
        mc_delta = deltas.get("mcap_delta_pct")
        mc_delta_str = _fmt_delta(mc_delta, "%") if mc_delta is not None else ""
        card_rows += f"""
    <div class="macro-card"><span class="macro-label">📊 总市值</span>
        <span class="macro-val">{mkt['total_market_cap_str']}</span>
        <span class="macro-sub">{mc_note} {mc_delta_str}</span></div>"""

    if sc.get("total_supply_str"):
        sc_note = "🟢 充沛" if sc.get('total_supply_usd',0) > 150e9 else "🟡 正常" if sc.get('total_supply_usd',0) > 100e9 else "🔴 不足"
        sc_delta = deltas.get("sc_delta_pct")
        sc_delta_str = _fmt_delta(sc_delta, "%") if sc_delta is not None else ""
        card_rows += f"""
    <div class="macro-card"><span class="macro-label">💵 稳定币储备</span>
        <span class="macro-val">{sc['total_supply_str']}</span>
        <span class="macro-sub">{sc_note} {sc_delta_str}</span></div>"""

    # ── VIX 波动率 (from OpenClue) ──────────────────────────
    vix_info = ""
    try:
        dims = json.loads(OPENCLUE_DIMS_FILE.read_text())
        vx = dims.get("vix", {})
        if vx.get("vix_price") is not None:
            vix_info = f'<span style="font-size:0.78rem;color:var(--muted);margin-top:6px;display:block">VIX: {vx["vix_price"]} · {vx.get("vix_analysis", "")}</span>'
    except Exception:
        pass

    # ── 各币种趋势表（已合并实时价格数据）──
    trend_rows = ""
    prices = data.get("prices", {})
    # 动态获取所有币种（排除内部键 _segment）
    all_coins = [c for c in coin_trends if c != "_segment"]
    for coin in all_coins:
        t = coin_trends.get(coin, {})
        if not t:
            continue
        
        # 价格数据（来自 prices）
        pd = prices.get(coin, {})
        price_str = fmt_price(pd.get('price'), coin) if pd.get('price') else '—'
        
        change = t.get("change_24h")
        chg_str = f'{change:+.2f}%' if change is not None else '—'
        chg_clr = '#4ade80' if change is not None and change >= 0 else '#f87171'

        funding = t.get("funding_rate")
        fr_str = f'{funding:+.3f}%' if funding is not None else '—'
        fr_clr = '#f87171' if funding is not None and abs(funding) > 0.003 else '#e5e7eb'

        flow = t.get("flow_buy_pct") or 50
        flow_str = f'{flow:.1f}%'
        flow_clr = '#4ade80' if flow > 55 else '#f87171' if flow < 45 else '#e5e7eb'

        long_pct = t.get("long_pct") or 50
        long_clr = '#f87171' if long_pct > 60 else '#4ade80' if long_pct < 42 else '#e5e7eb'

        trend_label = t.get("trend_label", "⚪ 中性/震荡")
        
        # 量环比（来自 prices）
        vcp = pd.get("volume_change_pct")
        vcp_html = ''
        if vcp is not None:
            vcp_clr = '#4ade80' if vcp >= 0 else '#f87171'
            vcp_arrow = '▲' if vcp >= 0 else '▼'
            vcp_html = f'<span style="color:{vcp_clr}">{vcp_arrow}{abs(vcp):.1f}%</span>'

        trend_rows += f"""
    <tr>
        <td><b>{coin}</b></td>
        <td style="color:#e5e7eb">{price_str}</td>
        <td style="color:{chg_clr}">{chg_str}</td>
        <td>{trend_label}</td>
        <td style="color:{fr_clr}">{fr_str}</td>
        <td style="color:{flow_clr}">{flow_str}</td>
        <td style="color:{long_clr}">{long_pct:.0f}%</td>
        <td>{t.get('volume','—')}</td>
        <td>{vcp_html}</td>
    </tr>"""

    # ── 板块轮动分析 ─────────────────────────────────────
    seg_html = ""
    if seg:
        ms_avg = seg.get("mainstream_avg")
        ac_avg = seg.get("altcoin_avg")
        ms_avg_str = f'{ms_avg:+.2f}%' if ms_avg is not None else '—'
        ac_avg_str = f'{ac_avg:+.2f}%' if ac_avg is not None else '—'
        rotation = seg.get("rotation_signal", "—")
        ms_long = seg.get("mainstream_avg_long_pct", 50)
        ac_long = seg.get("altcoin_avg_long_pct", 50)
        ms_winner = seg.get("mainstream_max_winner") or "—"
        ms_loser = seg.get("mainstream_max_loser") or "—"
        ac_winner = seg.get("altcoin_max_winner") or "—"
        ac_loser = seg.get("altcoin_max_loser") or "—"

        # 动态板块标签
        ms_coins = seg.get("mainstream_coins", [])
        ac_coins = seg.get("altcoins", [])
        ms_label = "/".join(ms_coins) if ms_coins else "—"
        ac_label = "/".join(ac_coins) if ac_coins else "—"

        ms_clr = '#4ade80' if ms_avg is not None and ms_avg >= 0 else '#f87171'
        ac_clr = '#4ade80' if ac_avg is not None and ac_avg >= 0 else '#f87171'

        seg_html = f"""
    <h3 style="color:var(--accent);margin:20px 0 10px;font-size:0.95rem">🔄 板块轮动分析</h3>
    <div class="table-wrap"><table>
        <thead><tr><th>板块</th><th>24h平均涨跌</th><th>最强</th><th>最弱</th><th>平均多头占比</th><th>资金流向</th></tr></thead>
        <tbody>
            <tr>
                <td><b>🏦 主流币</b><br><small style="color:var(--muted)">{ms_label}</small></td>
                <td style="color:{ms_clr};font-weight:700">{ms_avg_str}</td>
                <td style="color:#4ade80">{ms_winner}</td>
                <td style="color:#f87171">{ms_loser}</td>
                <td style="color:{'#f87171' if ms_long > 58 else '#e5e7eb' if ms_long > 48 else '#4ade80'}">{ms_long:.0f}%</td>
                <td rowspan="2" style="vertical-align:middle;max-width:250px;font-size:0.82rem">{rotation}</td>
            </tr>
            <tr>
                <td><b>🚀 山寨币</b><br><small style="color:var(--muted)">{ac_label}</small></td>
                <td style="color:{ac_clr};font-weight:700">{ac_avg_str}</td>
                <td style="color:#4ade80">{ac_winner}</td>
                <td style="color:#f87171">{ac_loser}</td>
                <td style="color:{'#f87171' if ac_long > 58 else '#e5e7eb' if ac_long > 48 else '#4ade80'}">{ac_long:.0f}%</td>
            </tr>
        </tbody>
    </table></div>"""

    # ── 策略建议 ──────────────────────────────────────────
    advice_html = ""
    strategy_advice = assessment.get("strategy_advice", [])
    if strategy_advice:
        items = "".join(f"<li>{tip}</li>" for tip in strategy_advice)
        advice_html = f"""
    <div class="advice-box" style="margin:12px 0;background:var(--card);border:1px solid var(--border);border-left:3px solid var(--yellow);border-radius:6px;padding:10px 14px;">
        <h4 style="color:var(--yellow);margin:0 0 6px 0;font-size:0.9rem">📋 策略建议</h4>
        <ul style="margin:0;padding-left:18px;color:var(--text);font-size:0.83rem">{items}</ul>
    </div>"""

    # ── 核心信号汇总 ──────────────────────────────────────
    signals_html = ""
    if signals_list:
        items = ""
        for sig in signals_list:
            if isinstance(sig, list) and len(sig) >= 3:
                dim, direction, desc = sig[0], sig[1], sig[2]
                icon = "🟢" if "LONG" in direction else ("🔴" if "SHORT" in direction else "⚪")
                items += f"<li style='font-size:0.83rem;margin:3px 0'>{icon} <b>{dim}</b>: {desc}</li>"
        if items:
            signals_html = f"""
    <div style="background:var(--card);border:1px solid var(--border);border-radius:6px;padding:10px 14px;margin:12px 0">
        <h4 style="color:var(--accent);margin:0 0 6px 0;font-size:0.9rem">🔍 核心信号汇总</h4>
        <ul style="margin:0;padding-left:18px;list-style:none">{items}</ul>
    </div>"""

    return f"""
<!-- 宏观策略摘要 — 报告第一部分 -->
<section id="macro-summary" style="margin-bottom:24px">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:1.3rem">🎯</span>
      <h2 style="margin:0;font-size:1.1rem;color:var(--accent);border:none;padding:0">宏观策略摘要</h2>
    </div>
    <span style="display:inline-flex;align-items:center;gap:5px;
      background:{phase_color}22;color:{phase_color};
      border:1px solid {phase_color}44;border-radius:20px;
      padding:4px 12px;font-size:0.83rem;font-weight:600">{phase_label}</span>
    <span style="display:inline-flex;align-items:center;gap:5px;
      background:{risk_color}22;color:{risk_color};
      border:1px solid {risk_color}44;border-radius:20px;
      padding:4px 12px;font-size:0.83rem;font-weight:600">⚡ 风险: {risk_level.upper()}</span>
    <span style="color:var(--muted);font-size:0.78rem">综合评分: {phase_score} · 策略: {strategy}</span>
  </div>

  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px">{card_rows}</div>
  {vix_info}

  <!-- 各币种趋势表 -->
  <h3 style="color:var(--accent);margin:18px 0 10px;font-size:0.95rem">📈 全量币种趋势（多因子综合判断）</h3>
  <div class="table-wrap"><table>
    <thead><tr><th>币种</th><th>均价</th><th>24h涨跌</th><th>趋势方向</th><th>资金费率</th><th>买方流量</th><th>多头占比</th><th>24h量</th><th>量环比</th></tr></thead>
    <tbody>{trend_rows}</tbody>
  </table></div>
  <p class="sub-note">趋势方向基于多因子综合得分（价格+资金流+费率+多头拥挤度+Taker BSR），绿色=看涨，红色=看跌，灰色=中性</p>

  {seg_html}
  {advice_html}
  {signals_html}
</section>



"""


def _build_compact_macro(data: dict) -> str:
    """当 macro_assessment 不可用时，展示ETF和纳指的简版"""
    macro  = data.get("macro", {}) or {}
    nasdaq = macro.get("nasdaq", {}) or {}
    nd_val = f"{nasdaq.get('price', 0):,.2f}" if nasdaq.get("price") else "N/A"
    nd_chg = nasdaq.get("change_1d_pct")

    # ETF
    etf = data.get("etf_flow", {}) or {}
    eth_etf = data.get("eth_etf_flow", {}) or {}
    e24 = etf.get("24h_flow_usd_m")
    e72 = etf.get("72h_flow_usd_m")

    return f"""<section id="macro-summary" style="margin-bottom:24px">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
    <span style="font-size:1.2rem">🌐</span>
    <h2 style="margin:0;font-size:1.05rem;color:var(--accent);border:none;padding:0">宏观数据</h2>
  </div>
  <div class="table-wrap"><table>
    <thead><tr><th>指标</th><th>数值</th><th>24h变化</th><th>72h流量</th></tr></thead>
    <tbody>
      <tr><td><b>纳斯达克</b></td><td>{nd_val}</td><td>{change_span(nd_chg)}</td><td>—</td></tr>
      <tr><td><b>BTC ETF</b></td><td>{f'${e24:+.1f}M' if e24 is not None else '—'}</td>
          <td>{f'${e72:+.1f}M' if e72 is not None else '—'}</td><td>{etf.get('source','')}</td></tr>
      <tr><td><b>ETH ETF</b></td><td>{f'${eth_etf.get("24h_flow_usd_m",0):+.1f}M' if eth_etf.get("24h_flow_usd_m") is not None else '—'}</td>
          <td>{f'${eth_etf.get("72h_flow_usd_m",0):+.1f}M' if eth_etf.get("72h_flow_usd_m") is not None else '—'}</td>
          <td>{eth_etf.get('source','')}</td></tr>
    </tbody>
  </table></div>
</section>"""


# ── 数据新鲜度检查 ─────────────────────────────────────
def check_data_freshness(signal_file: Path) -> tuple[bool, str, float]:
    """
    检查signal JSON的数据新鲜度
    返回: (is_fresh, warning_msg, age_minutes)
    """
    try:
        data = json.loads(signal_file.read_text())
        gen_time_str = data.get("generated_at", "")
        
        if not gen_time_str.endswith("CST"):
            return False, "⚠️ 数据时间戳格式错误", 999
        
        gen_dt = datetime.strptime(gen_time_str.replace(" CST", "").replace(" UTC+8 北京时间", ""), "%Y-%m-%d %H:%M").replace(tzinfo=CST)
        now = datetime.now(CST)
        age_minutes = (now - gen_dt).total_seconds() / 60
        
        if age_minutes > 60:
            return False, f"⚠️ 数据已过期 {age_minutes:.0f} 分钟，请重新运行信号引擎！", age_minutes
        elif age_minutes > 30:
            return True, f"⚠️ 数据 {age_minutes:.0f} 分钟前更新，建议重新运行信号引擎", age_minutes
        else:
            return True, f"✅ 数据 {age_minutes:.0f} 分钟前更新", age_minutes
    except Exception as e:
        return False, f"⚠️ 数据时间戳解析失败: {e}", 999


def force_refresh_prices(data: dict) -> dict:
    """
    强制重新拉取实时价格，覆盖JSON中的旧价格
    """
    try:
        import _price_cache as pcache
        coins = list(data.get("prices", {}).keys())
        if not coins:
            return data
        
        # 清除缓存，强制重新拉取
        if hasattr(pcache, 'clear_cache'):
            pcache.clear_cache()
        
        cached = pcache.get_all(coins)
        if cached and all(v is not None for v in cached.values()):
            print(f"  [ReportGen] ✅ 实时价格已更新（{len(coins)}币种）")
            for coin in coins:
                if coin in cached and cached[coin] is not None:
                    if "price" in data["prices"][coin]:
                        old_price = data["prices"][coin]["price"]
                        new_price = cached[coin]["price"]
                        data["prices"][coin]["price"] = new_price
                        data["prices"][coin]["source"] = cached[coin].get("source", "Cache-Refresh")
                        print(f"    {coin}: ${old_price:.4f} → ${new_price:.4f}")
    except Exception as e:
        print(f"  [ReportGen] ⚠️ 价格刷新失败: {e}", file=sys.stderr)
    
    return data


# ── Main HTML generator ──────────────────────────────────────



# ── 爆仓区间重计算（Phase B2）──────────────────────────────
def recalc_liq(data: dict) -> dict:
    """
    使用最新价格重新计算爆仓区间。
    解决 P1-3：报告生成时爆仓价未重新计算的问题。
    """
    print("  [ReportGen] 🔄 重新计算爆仓区间...")

    # 延迟导入，避免循环依赖
    try:
        import importlib.util, sys
        sig_path = Path(__file__).parent / "crypto_signal_v2.py"
        spec = importlib.util.spec_from_file_location("crypto_signal_v2", sig_path)
        if "crypto_signal_v2" not in sys.modules:
            mod = importlib.util.module_from_spec(spec)
            sys.modules["crypto_signal_v2"] = mod
            spec.loader.exec_module(mod)
        else:
            mod = sys.modules["crypto_signal_v2"]

        if not hasattr(mod, "estimate_liquidation_zones"):
            print("  [ReportGen] ⚠️  estimate_liquidation_zones 函数不存在，跳过爆仓重计算")
            return data

        for coin, s in data.get("signals", {}).items():
            price    = data.get("prices", {}).get(coin, {}).get("price", 0)
            if not price or price <= 0:
                continue

            contract = data.get("contracts", {}).get(coin, {})
            tech     = data.get("tech_data", {}).get(coin, {})

            try:
                liq = mod.estimate_liquidation_zones(price, contract, tech)
                # 贝叶斯修正
                try:
                    from bayesian_liquidation import correct_liquidation
                    liq = correct_liquidation(coin, liq)
                except ImportError:
                    pass
                s["liquidation"] = liq
            except Exception as e:
                print(f"  [ReportGen] ⚠️  {coin} 爆仓重计算失败: {e}")

        print("  [ReportGen] ✅ 爆仓区间已重新计算")
    except Exception as e:
        print(f"  [ReportGen] ⚠️  爆仓重计算异常: {e}")

    return data


def enforce_data_freshness(signal_file: Path):
    """
    【严格强制】检查数据新鲜度，>60分钟拒绝生成报告
    不通过则 sys.exit(1) 终止程序
    """
    if not signal_file.exists():
        print("🔴 [数据审计] 信号文件不存在，终止报告生成")
        sys.exit(1)
    
    data = json.loads(signal_file.read_text())
    gen_time_str = data.get("generated_at", "")
    
    # 解析时间戳
    try:
        if gen_time_str.endswith("CST") or gen_time_str.endswith("北京时间"):
            gen_dt = datetime.strptime(gen_time_str.replace(" CST", "").replace(" UTC+8 北京时间", "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=CST)
        else:
            print(f"🔴 [数据审计] 时间戳格式错误: {gen_time_str}")
            sys.exit(1)
    except Exception as e:
        print(f"🔴 [数据审计] 时间戳解析失败: {e}")
        sys.exit(1)
    
    # 计算数据年龄
    now = datetime.now(CST)
    age_seconds = (now - gen_dt).total_seconds()
    age_minutes = age_seconds / 60
    
    # 严格规则：>60分钟拒绝生成
    if age_minutes > 60:
        print(f"🔴 [数据审计] 数据已过期 {age_minutes:.1f} 分钟（>60分钟），拒绝生成报告")
        print(f"   请先运行: cd {BASE} && python3 crypto_signal_v2.py")
        sys.exit(1)
    
    if age_minutes > 30:
        print(f"🟡 [数据审计] 数据已过期 {age_minutes:.1f} 分钟（30-60分钟），生成报告但显示警告")
    elif age_minutes > 10:
        print(f"🟢 [数据审计] 数据 {age_minutes:.1f} 分钟前更新，正常生成报告")
    else:
        print(f"🟢 [数据审计] 数据新鲜（{age_minutes:.1f} 分钟），正常生成报告")
    
    return age_minutes


def generate_html(signal_file: Path, paper_file: Path, output_path: Path, auto_open: bool = False):
    # 【严格强制】第一步：检查数据新鲜度，不通过则终止
    age_min = enforce_data_freshness(signal_file)
    
    # 【Phase B1】如果信号文件 > 30 分钟，强制重新运行信号引擎
    if age_min > 30:
        print(f"  [ReportGen] ⚠️ 信号数据已 {age_min:.1f} 分钟，重新生成...")
        try:
            result = subprocess.run(
                [sys.executable, "crypto_signal_v2.py"],
                cwd=BASE, capture_output=True, timeout=180
            )
            if result.returncode != 0:
                print(f"  [ReportGen] ⚠️ 信号引擎运行失败，使用旧数据")
                print(f"    stderr: {result.stderr.decode()[:200]}")
            else:
                print(f"  [ReportGen] ✅ 信号已重新生成")
        except Exception as e:
            print(f"  [ReportGen] ⚠️ 信号引擎运行异常: {e}，使用旧数据")
    
    data = json.loads(signal_file.read_text())
    
    # 【严格强制】第二步：强制重新拉取所有价格（即使数据"新鲜"）
    print(f"  [ReportGen] 强制重新拉取所有币种实时价格...")
    data = force_refresh_prices(data)
    
    # 【Phase B2】重新计算爆仓区间（使用最新价格）
    data = recalc_liq(data)
    
    # 使用当前北京时间（修复：避免使用JSON中的旧时间戳）
    now = datetime.now(CST)
    gen_dt   = now.strftime("%Y-%m-%d %H:%M") + " UTC+8 北京时间"
    date_str = now.strftime("%Y-%m-%d")
    
    # 构建数据新鲜度消息（age_min 由 enforce_data_freshness() 返回）
    if age_min <= 10:
        freshness_msg = f"✅ 数据 {age_min:.0f} 分钟前更新"
    elif age_min <= 30:
        freshness_msg = f"⚠️ 数据 {age_min:.0f} 分钟前更新"
    else:
        freshness_msg = f"⚠️ 数据 {age_min:.0f} 分钟前更新，建议重新运行信号引擎"
    
    # 构建新鲜度警告HTML（>60分钟已在 enforce_data_freshness() 中被 sys.exit 拦截）
    if age_min > 30:
        freshness_html = f'<div style="background:#f59e0b;color:white;padding:6px 10px;border-radius:6px;margin:6px 0;font-size:0.85rem">{freshness_msg}</div>'
    elif age_min > 10:
        freshness_html = f'<div style="color:var(--muted);font-size:0.75rem;margin-top:4px">{freshness_msg}<br><span style="color:#f59e0b">建议重新拉取新鲜数据</span></div>'
    else:
        freshness_html = f'<div style="color:var(--muted);font-size:0.75rem;margin-top:4px">{freshness_msg}</div>'

    # 按 24h 交易量排序币种
    sorted_coins = _volume_sorted_coins(data)
    if sorted_coins:
        print(f"  [ReportGen] 报告按24h量排列: {' > '.join(sorted_coins)}")
    
    contract_rows     = build_contract_table(data, sorted_coins)
    liq_rows          = build_liq_table(data, sorted_coins)
    whale_rows        = build_whale_table(data)
    sig_rows          = build_signal_table(data, sorted_coins)
    # 生成报告前先更新模拟交易状态（获取最新价格和SL/TP触发）
    if paper_file.exists():
        try:
            import paper_trader
            paper_trader.cmd_update()
        except Exception as e:
            print(f"  [ReportGen] 模拟交易更新失败: {e}", file=sys.stderr)
    paper_html        = build_paper_section(paper_file)
    macro_summary_html = build_macro_summary_section(data)  # 统一宏观策略摘要（已合并OpenClue指标）
    
    # 构建 DeFi Llama TVL 部分
    defi_html = ""
    if HAS_DEFILAMA:
        macro = data.get("macro", {}) or {}
        defi_tvl = macro.get("defi_tvl", {}) or {}
        if defi_tvl.get("total_tvl"):
            defi_html = build_defi_section(defi_tvl)
    
    macro  = data.get("macro", {}) or {}
    nasdaq = macro.get("nasdaq", {}) or {}
    nd_val = f"{nasdaq.get('price', 0):,.2f}" if nasdaq.get("price") else "N/A"
    nd_chg = nasdaq.get("change_1d_pct")
    nd_span= change_span(nd_chg)

    etf_row = build_etf_row(data)
    eth_etf_row = build_eth_etf_row(data)

    # 代币解锁数据
    unlock_html = ""
    if HAS_UNLOCK_TRACKER:
        try:
            unlock_html = get_unlock_summary_html()
        except Exception as e:
            print(f"  [TokenUnlock] 生成失败: {e}")

    # 鲸鱼表格（可选）
    whale_section = ""
    if whale_rows:
        whale_section = f"""
<section id="whales">
  <h2>🐋 鲸鱼持仓（多空占比）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>鲸鱼多头%</th><th>鲸鱼空头%</th><th>倾向</th></tr></thead>
    <tbody>{whale_rows}</tbody>
  </table>
  </div>
</section>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Super Mario · 加密货币信号报告 v2 · {date_str}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="container">

<header>
  <div>
    <h1>🔍 Super Mario · 加密货币信号报告 <small style="font-size:0.8rem;color:var(--muted)">v2</small></h1>
    <div class="gen-time">生成: {gen_dt} &nbsp;|&nbsp; 价格: Binance·OKX·Bybit 3源加权</div>
    {freshness_html}
  </div>
  <span class="audit-badge">✅ 3源加权·备选降级</span>
</header>

{macro_summary_html}

<section id="contracts">
  <h2>📈 Futures 合约数据</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>资金费率</th><th>多空比 L/S</th><th>多仓均价</th><th>空仓均价</th><th>24h买方流量</th></tr></thead>
    <tbody>{contract_rows}</tbody>
  </table>
  </div>
  <p class="sub-note">爆仓概率 = 距离+拥挤度+资金费率+BB位置 四因子模型 · 距离越近权重越高 · 绿&lt;45% 黄45-60% 红≥60%</p>
</section>

<section id="liquidation">
  <h2>⚡ 爆仓区间（10x / 20x 杠杆）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>当前价</th><th>多头10x爆(距离%)</th><th>多头20x爆(距离%)</th><th>空头10x爆(距离%)</th><th>空头20x爆(距离%)</th><th>爆多仓概率</th><th>爆空仓概率</th><th>爆多仓插针</th><th>爆空仓插针</th></tr></thead>
    <tbody>{liq_rows}</tbody>
  </table>
  </div>
</section>

{whale_section}
{unlock_html}

<section id="macro">
  <h2>🌐 宏观数据</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>指标</th><th>24h净流量</th><th>48h净流量</th><th>72h净流量</th><th>来源</th></tr></thead>
    <tbody>
      <tr><td><b>纳斯达克</b></td><td>{nd_val}</td><td>{nd_span}</td><td style="color:var(--muted)">—</td><td>Yahoo Finance</td></tr>
      {etf_row}
      {eth_etf_row}
    </tbody>
  </table>
  </div>
</section>

{defi_html}

<section id="signals">
  <h2>🎯 交易信号建议</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>当前价</th><th>方向</th><th>入场区间</th><th>入场距离</th><th>杠杆</th><th>止盈1</th><th>止盈2</th><th>止损</th><th>R/R</th><th>RSI(14)</th><th>贝叶斯概率</th></tr></thead>
    <tbody>{sig_rows}</tbody>
  </table>
  </div>
  <p class="sub-note">⚠️ 仅供参考，不构成投资建议 · 用户约束：只买ETF基金，不买个股</p>
</section>


{paper_html}

<footer style="margin-top:36px;padding-top:14px;border-top:1px solid var(--border);color:var(--muted);font-size:0.73rem;text-align:center">
  Super Mario 🔍 · 加密货币信号报告 v2 · {date_str} · 数据: Binance/OKX/Bybit/Gate/Kraken/CoinGecko
</footer>
</div>

<!-- 信号详情弹窗 -->
<div class="modal-overlay" id="signalModal">
  <div class="modal-box" id="modalBox"></div>
</div>

<script>
// ===== 1. 信号弹窗 =====
(function() {{
  var overlay = document.getElementById('signalModal');
  var modalBox = document.getElementById('modalBox');

  // 点击信号行 → 弹窗
  document.querySelectorAll('.signal-clickable').forEach(function(row) {{
    row.addEventListener('click', function() {{
      var raw = this.getAttribute('data-detail');
      if (!raw) return;
      var d = JSON.parse(raw.replace(/&quot;/g, '"').replace(/\\u0027/g, "'"));

      var dirColor = d.direction === 'LONG' ? 'var(--green)' : (d.direction === 'SHORT' ? 'var(--red)' : 'var(--muted)');
      var signalBg  = d.direction === 'LONG' ? 'rgba(74,222,128,.12)' : (d.direction === 'SHORT' ? 'rgba(248,113,113,.12)' : 'rgba(148,163,184,.08)');
      var pinHtml = d.pin_warning ? '<div style="margin-top:8px;color:#f59e0b;font-size:0.82rem">⚠️ ' + d.pin_warning + '</div>' : '';

      modalBox.innerHTML = 
        '<div class="modal-header">' +
          '<h3>🔍 ' + d.coin + ' 信号详情</h3>' +
          '<button class="modal-close" onclick="document.getElementById(\'signalModal\').classList.remove(\'active\')">&times;</button>' +
        '</div>' +
        '<div class="modal-body">' +
          '<div class="modal-signal" style="background:' + signalBg + ';color:' + dirColor + '">' +
            d.direction_cn + ' · 评分 ' + d.prob + '% · ' + d.leverage + 'x杠杆' +
          '</div>' +
          pinHtml +
          '<div class="modal-grid" style="margin-top:12px">' +
            '<div class="modal-item"><div class="lbl">🎯 入场区间</div><div class="val">' + d.entry_low + ' – ' + d.entry_high + '</div></div>' +
            '<div class="modal-item"><div class="lbl">✅ 入场状态</div><div class="val">' + 
              (d.entry_ready === "ready" ? '<span style="color:var(--green)">✅ 当前价可入场</span>' : 
               (d.entry_ready && d.entry_ready.startsWith("above_") ? '<span style="color:var(--red)">⚠️ 已突破上沿' + d.entry_ready.replace("above_", "") + '</span>' : 
                (d.entry_ready && d.entry_ready.startsWith("below_") ? '<span style="color:var(--red)">⚠️ 已跌破下沿' + d.entry_ready.replace("below_", "") + '</span>' : 
                  '<span style="color:var(--muted)">⚠️ 等待入场时机</span>')) + 
            '</div></div>' +
            '<div class="modal-item"><div class="lbl">💰 当前价格</div><div class="val">' + d.price + '</div></div>' +
            '<div class="modal-item"><div class="lbl">✅ 止盈 TP1</div><div class="val" style="color:var(--green)">' + d.tp1 + '</div></div>' +
            '<div class="modal-item"><div class="lbl">✅ 止盈 TP2</div><div class="val" style="color:var(--green)">' + d.tp2 + '</div></div>' +
            '<div class="modal-item"><div class="lbl">🛑 止损 SL</div><div class="val" style="color:var(--red)">' + d.sl + '</div></div>' +
            '<div class="modal-item"><div class="lbl">⚖️ 盈亏比 R/R</div><div class="val">' + d.rr + ':1</div></div>' +
            '<div class="modal-item"><div class="lbl">📊 RSI(14)</div><div class="val">' + d.rsi + '</div></div>' +
            '<div class="modal-item"><div class="lbl">📈 24h涨跌</div><div class="val" style="color:' + (parseFloat(d.change_24h) >= 0 ? 'var(--green)' : 'var(--red)') + '">' + d.change_24h + '</div></div>' +
            '<div class="modal-item"><div class="lbl">💵 资金费率</div><div class="val">' + d.funding_rate + '</div></div>' +
            '<div class="modal-item"><div class="lbl">👥 多头占比</div><div class="val">' + d.long_pct + '</div></div>' +
            '<div class="modal-item"><div class="lbl">📉 Taker BSR</div><div class="val">' + d.taker_bsr + '</div></div>' +
            '<div class="modal-item"><div class="lbl">💧 4h买流占比</div><div class="val">' + d.flow_buy + '</div></div>' +
          '</div>' +
          '<div style="margin-top:14px;display:grid;grid-template-columns:1fr 1fr;gap:10px">' +
            '<div class="modal-item"><div class="lbl">⚡ 做多爆仓价(10x)</div><div class="val" style="color:var(--red)">' + d.long_liq + '</div></div>' +
            '<div class="modal-item"><div class="lbl">⚡ 做空爆仓价(10x)</div><div class="val" style="color:var(--red)">' + d.short_liq + '</div></div>' +
          '</div>' +
        '</div>';

      overlay.classList.add('active');
    }});
  }});

  // 点击遮罩关闭
  overlay.addEventListener('click', function(e) {{
    if (e.target === overlay) overlay.classList.remove('active');
  }});

  // ESC 关闭
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') overlay.classList.remove('active');
  }});
}})();

// ===== 2. Section 折叠 =====
(function() {{
  document.querySelectorAll('section h2').forEach(function(h2) {{
    h2.classList.add('section-toggle');
    h2.addEventListener('click', function() {{
      var body = this.nextElementSibling;
      // 跳过 comment 节点
      while (body && body.nodeType !== 1) body = body.nextElementSibling;
      if (!body) return;
      if (!body.classList.contains('section-body')) {{
        // 把该 section 里 h2 后面的兄弟包起来
        var wrapper = document.createElement('div');
        wrapper.className = 'section-body';
        var next = this.nextElementSibling;
        while (next && next.tagName !== 'H2') {{
          var toMove = next;
          next = next.nextElementSibling;
          wrapper.appendChild(toMove);
        }}
        this.parentNode.insertBefore(wrapper, this.nextElementSibling);
        body = wrapper;
      }}
      this.classList.toggle('collapsed');
      body.classList.toggle('collapsed');
      if (body.classList.contains('collapsed')) {{
        body.style.maxHeight = '0';
      }} else {{
        body.style.maxHeight = body.scrollHeight + 'px';
      }}
    }});
  }});
}})();

// ===== 3. 宏观卡片点击提示 =====
(function() {{
  document.querySelectorAll('.macro-card').forEach(function(card) {{
    card.addEventListener('click', function() {{
      var label = (this.querySelector('.macro-label') || {{}}).textContent || '';
      alert('📊 ' + label.trim() + '\\n\\n点击数据可查看详细趋势图（需连接数据源）');
    }});
  }});
}})();
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    print(f"✅ HTML: {output_path}")
    
    # 自动触发审核（仅限信号报告）
    try:
        import subprocess as _sp
        verifier = str(BASE / "report_verifier.py")
        result = _sp.run([sys.executable, verifier, str(output_path)], 
                        capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"  ✅ 报告审核通过: {output_path.name}")
        else:
            print(f"  ⚠️ 报告审核发现问题，请手动检查:")
            print(f"    {result.stdout.split(chr(10))[0] if result.stdout else '未知错误'}")
    except Exception as _e:
        pass  # 审核失败不影响报告生成
    
    # 自动在外部浏览器中打开报告
    if auto_open:
        try:
            abs_path = output_path.absolute()
            url = f"file://{abs_path}"
            print(f"  🌐 在外部浏览器中打开: {url}")
            webbrowser.open(url)
        except Exception as e:
            print(f"  ⚠️  浏览器打开失败: {e}")
    
    return output_path


if __name__ == "__main__":
    now_ts = datetime.now().strftime("%Y%m%d_%H%M")
    out = REPORTS_DIR / f"crypto_signal_v2_{now_ts}.html"
    generate_html(SIGNAL_FILE, PAPER_FILE, out)
