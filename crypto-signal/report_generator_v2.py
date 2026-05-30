#!/usr/bin/env python3
"""
加密货币信号报告生成器 v2
读取 signal_v2_latest.json + paper_positions.json → HTML 报告
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE         = Path("/Users/alex/hermes_claud/super_mario/crypto-signal")
SIGNAL_FILE  = BASE / "signal_v2_latest.json"
PAPER_FILE   = BASE / "paper_positions.json"
REPORTS_DIR  = Path("/Users/alex/hermes_claud/super_mario/reports")


def fmt_price(p, coin=""):
    if p is None: return "—"
    if coin == "DOGE": return f"${p:,.4f}"
    if p >= 1000: return f"${p:,.2f}"
    if p >= 1:    return f"${p:,.4f}"
    return f"${p:.6f}"


def change_span(v):
    if v is None: return "<span>—</span>"
    color = "#4ade80" if v >= 0 else "#f87171"
    sign  = "+" if v >= 0 else ""
    return f'<span style="color:{color}">{sign}{v:.2f}%</span>'


# ── Table builders ──────────────────────────────────────────

def build_price_table(data):
    prices = data.get("prices", {})
    rows = ""
    for coin, pd in prices.items():
        chg_span = change_span(pd.get("change_24h"))
        sources  = pd.get("sources", {})
        cnt      = pd.get("source_count", len(sources))
        src_str  = " / ".join(f"{k}:{v:,.2f}" for k, v in list(sources.items())[:4])
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b></td>"
            f"<td>{fmt_price(pd.get('price'), coin)}</td>"
            f"<td>{chg_span}</td>"
            f"<td>{cnt}源</td>"
            f"<td style='font-size:0.78em;color:#9ca3af'>{src_str}</td>"
            f"</tr>\n"
        )
    return rows


def build_contract_table(data):
    signals = data.get("signals", {})
    contracts = data.get("contracts", {})
    # 检查合约数据是否可用
    first_ct = next(iter(contracts.values()), {})
    has_ct_data = first_ct.get("funding_rate") is not None
    if not has_ct_data:
        return '<tr><td colspan="6" style="text-align:center;color:#f87171;padding:16px;">⚠️ Binance Futures API不可用，所有合约数据为空</td></tr>'
    rows = ""
    for coin, sd in signals.items():
        ct = contracts.get(coin, {})
        fr_raw = sd.get("funding_rate")
        fr_str = f"{fr_raw:+.4f}%" if fr_raw is not None else "—"
        liq = sd.get("liquidation", {}) or {}
        ls_raw = liq.get("ls_ratio")
        ls_str = f"{ls_raw:.3f}" if ls_raw is not None else "—"
        flow_raw = sd.get("flow_buy_pct")
        flow_str = f"{flow_raw:.1f}%" if flow_raw is not None else "—"
        # 多仓/空仓均价
        liq_l = liq.get("liq_long_zones", {}) or {}
        liq_s = liq.get("liq_short_zones", {}) or {}
        avg_l_str = avg_s_str = "—"
        if liq_l.get("5x") and liq_l.get("10x"):
            avg_l = (float(liq_l["5x"]) + float(liq_l["10x"])) / 2
            avg_l_str = f"${avg_l:,.1f}" if abs(avg_l) >= 10 else f"${avg_l:,.4f}"
        if liq_s.get("5x") and liq_s.get("10x"):
            avg_s = (float(liq_s["5x"]) + float(liq_s["10x"])) / 2
            avg_s_str = f"${avg_s:,.1f}" if abs(avg_s) >= 10 else f"${avg_s:,.4f}"
        fr_color  = "#f87171" if fr_raw is not None and fr_raw > 0.002 else ("#4ade80" if fr_raw is not None and fr_raw < -0.001 else "#e5e7eb")
        ls_color  = "#f87171" if ls_raw is not None and ls_raw > 1.5 else ("#4ade80" if ls_raw is not None and ls_raw < 0.8 else "#e5e7eb")
        flow_color= "#4ade80" if flow_raw is not None and flow_raw > 55 else ("#f87171" if flow_raw is not None and flow_raw < 45 else "#e5e7eb")
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b></td>"
            f"<td style='color:{fr_color}'>{fr_str}</td>"
            f"<td style='color:{ls_color}'>{ls_str}</td>"
            f"<td>{avg_l_str}</td>"
            f"<td>{avg_s_str}</td>"
            f"<td style='color:{flow_color}'>{flow_str}</td>"
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
        atr_pct = atr_abs / price * 100
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


def build_liq_table(data):
    signals = data.get("signals", {})
    prices = data.get("prices", {})
    rows = ""
    for coin, sd in signals.items():
        liq = sd.get("liquidation", {}) or {}
        price = prices.get(coin, {}).get("price", 0)
        ll5  = liq.get("liq_long_zones", {}).get("5x")
        ll10 = liq.get("liq_long_zones", {}).get("10x")
        ll20 = liq.get("liq_long_zones", {}).get("20x")
        ls10 = liq.get("liq_short_zones", {}).get("10x")
        ls20 = liq.get("liq_short_zones", {}).get("20x")
        risk = liq.get("primary_risk", "NEUTRAL")
        risk_cell = (
            '<span class="badge badge-sl">多头爆仓风险</span>' if risk == "DOWN" else
            '<span class="badge badge-tp">空头爆仓风险</span>' if risk == "UP" else
            '<span style="color:#6b7280">中性</span>'
        )
        def fmt_liq(val):
            if val is None or price == 0: return '<span style="color:#6b7280">—</span>'
            dist = abs(price - val) / price * 100
            p_s = f"${val:,.2f}" if abs(val) >= 1 else f"${val:,.4f}"
            return f'{p_s} <span style="font-size:11px;color:#9ca3af">({dist:.1f}%)</span>'
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b></td>"
            f"<td style='color:#4ade80'>{fmt_liq(ll10)}</td>"
            f"<td style='color:#4ade80'>{fmt_liq(ll20)}</td>"
            f"<td style='color:#f87171'>{fmt_liq(ls10)}</td>"
            f"<td style='color:#f87171'>{fmt_liq(ls20)}</td>"
            f"<td>{risk_cell}</td>"
            f"</tr>\n"
        )
    return rows


def build_signal_table(data):
    signals = data.get("signals", {})
    DIR_LABEL = {"LONG": "🟢 做多", "SHORT": "🔴 做空", "NEUTRAL": "⚪ 观望"}
    DIR_CLASS = {"LONG": "long-dir", "SHORT": "short-dir", "NEUTRAL": ""}
    rows = ""
    for coin, sd in signals.items():
        direction = sd.get("direction", "NEUTRAL")
        entry = sd.get("entry_zone", [None, None]) or [None, None]
        entry_str = f"{fmt_price(entry[0], coin)} – {fmt_price(entry[1], coin)}" if entry[0] else "—"
        tp1  = fmt_price(sd.get("tp1"), coin)
        tp2  = fmt_price(sd.get("tp2"), coin)
        sl   = fmt_price(sd.get("sl"), coin)
        lev  = sd.get("leverage", 3)
        prob = sd.get("prob_score", 0)
        rr   = sd.get("rr_ratio", 0) or 0
        rr_color   = "#4ade80" if rr >= 2 else "#f59e0b"
        prob_color = "#4ade80" if prob >= 55 else ("#f59e0b" if prob >= 45 else "#f87171")
        pin_w  = sd.get("pin_warning", "") or ""
        pin_td = f"<br><small style='color:#f59e0b'>⚠️ {pin_w}</small>" if pin_w else ""
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b>{pin_td}</td>"
            f"<td class='{DIR_CLASS.get(direction)}'>{DIR_LABEL.get(direction, direction)}</td>"
            f"<td>{entry_str}</td>"
            f"<td>{lev}x</td>"
            f"<td>{tp1}</td>"
            f"<td>{tp2}</td>"
            f"<td>{sl}</td>"
            f"<td style='color:{rr_color}'>{rr:.1f}:1</td>"
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


# ── Main HTML generator ──────────────────────────────────────

def generate_html(signal_file: Path, paper_file: Path, output_path: Path):
    data     = json.loads(signal_file.read_text())
    gen_time = data.get("generated_at", datetime.now(timezone.utc).isoformat())
    gen_dt   = gen_time[:19].replace("T", " ") + " UTC"
    date_str = gen_time[:10]

    price_rows = build_price_table(data)
    contract_rows = build_contract_table(data)
    tech_rows  = build_tech_table(data)
    liq_rows   = build_liq_table(data)
    sig_rows   = build_signal_table(data)
    paper_html = build_paper_section(paper_file)

    macro  = data.get("macro", {}) or {}
    nasdaq = macro.get("nasdaq", {}) or {}
    nd_val = f"{nasdaq.get('price', 0):,.2f}" if nasdaq.get("price") else "N/A"
    nd_chg = nasdaq.get("change_1d_pct")
    nd_span= change_span(nd_chg)
    nd_src = nasdaq.get("source", "—")

    # ETF 流量数据
    btc_etf = data.get("etf_flow", {}) or {}
    eth_etf = data.get("eth_etf_flow", {}) or {}
    etf_rows = ""
    if btc_etf.get("today_str") and btc_etf["today_str"] != "数据不可用":
        btc24 = btc_etf.get("today_str", "—")
        eth24 = eth_etf.get("today_str", "—")
        btc_wk = btc_etf.get("last_week_str", "—")
        eth_wk = eth_etf.get("last_week_str", "—")
        btc_mo = btc_etf.get("last_month_str", "—")
        eth_mo = eth_etf.get("last_month_str", "—")
        btc_aum = btc_etf.get("aum_str", "—")
        eth_aum = eth_etf.get("aum_str", "—")
        btc_color = "#4ade80" if btc_etf.get("today", 0) and btc_etf["today"] > 0 else "#f87171"
        eth_color = "#4ade80" if eth_etf.get("today", 0) and eth_etf["today"] > 0 else "#f87171"
        etf_rows = f"""
      <tr><td><b>BTC ETF 当日</b></td><td style='color:{btc_color};font-weight:600'>{btc24}</td>
          <td>上周: {btc_wk}</td><td>上月: {btc_mo}</td><td>CoinMarketCap</td></tr>
      <tr><td><b>ETH ETF 当日</b></td><td style='color:{eth_color};font-weight:600'>{eth24}</td>
          <td>上周: {eth_wk}</td><td>上月: {eth_mo}</td><td>CoinMarketCap</td></tr>"""
    else:
        etf_rows = """
      <tr><td><b>BTC ETF流量</b></td><td colspan="3" style="color:var(--muted)">数据不可用（需 agent-browser）</td></tr>
      <tr><td><b>ETH ETF流量</b></td><td colspan="3" style="color:var(--muted)">数据不可用（需 agent-browser）</td></tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Super Mario · 加密货币信号报告 v2 · {date_str}</title>
<style>
:root{{--bg:#0f172a;--card:#1e293b;--card2:#263347;--border:#334155;--text:#e2e8f0;
  --muted:#94a3b8;--accent:#60a5fa;--green:#4ade80;--red:#f87171;--yellow:#fbbf24}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.6}}
.container{{max-width:1300px;margin:0 auto;padding:20px 16px}}
header{{border-bottom:1px solid var(--border);padding-bottom:16px;margin-bottom:24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
h1{{font-size:1.4rem;font-weight:700;color:var(--accent)}}
.gen-time{{color:var(--muted);font-size:0.78rem}}
h2{{font-size:1.05rem;font-weight:600;color:var(--accent);margin:26px 0 10px;padding-bottom:5px;border-bottom:1px solid var(--border)}}
h3{{font-size:0.9rem;font-weight:600;color:var(--text);margin:14px 0 7px}}
.table-wrap{{overflow-x:auto;margin-bottom:8px}}
table{{width:100%;border-collapse:collapse;font-size:0.83rem}}
th{{background:var(--card2);color:var(--muted);font-weight:600;text-transform:uppercase;font-size:0.72rem;padding:8px 10px;text-align:center;border:1px solid var(--border)}}
td{{padding:7px 10px;text-align:center;border:1px solid var(--border);background:var(--card)}}
tr:hover td{{background:var(--card2)}}
.long-dir{{color:var(--green);font-weight:700}}
.short-dir{{color:var(--red);font-weight:700}}
.badge{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:0.72rem;font-weight:600}}
.badge-open{{background:#1d4ed8;color:#bfdbfe}}
.badge-tp{{background:#14532d;color:#86efac}}
.badge-sl{{background:#7f1d1d;color:#fca5a5}}
.sub-note{{color:var(--muted);font-size:0.78rem;margin-top:6px}}
.paper-summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:10px 0 18px}}
.ps-card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center}}
.ps-label{{color:var(--muted);font-size:0.72rem;text-transform:uppercase;margin-bottom:4px}}
.ps-value{{font-size:1.05rem;font-weight:700}}
.advice-box{{background:var(--card);border:1px solid #374151;border-left:3px solid var(--yellow);border-radius:6px;padding:14px 18px;margin:14px 0}}
.advice-box h4{{color:var(--yellow);margin-bottom:6px}}
.audit-badge{{display:inline-flex;align-items:center;gap:5px;background:#14532d;color:#86efac;border:1px solid #16a34a;border-radius:6px;padding:4px 10px;font-size:0.78rem;font-weight:600}}
@media(max-width:700px){{.paper-summary{{grid-template-columns:1fr 1fr}}}}
</style>
</head>
<body>
<div class="container">

<header>
  <div>
    <h1>🔍 Super Mario · 加密货币信号报告 <small style="font-size:0.8rem;color:var(--muted)">v2</small></h1>
    <div class="gen-time">生成: {gen_dt} &nbsp;|&nbsp; Binance · OKX · Bybit · Gate.io · Kraken · CoinGecko</div>
  </div>
  <span class="audit-badge">✅ 6源交叉验证</span>
</header>

<section id="prices">
  <h2>💰 实时价格（加权均价）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>均价</th><th>24h涨跌</th><th>数据源数</th><th>各源价格</th></tr></thead>
    <tbody>{price_rows}</tbody>
  </table>
  </div>
</section>

<section id="contracts">
  <h2>📈 Futures 合约数据</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>资金费率</th><th>多空比 L/S</th><th>多仓均价</th><th>空仓均价</th><th>24h买方流量</th></tr></thead>
    <tbody>{contract_rows}</tbody>
  </table>
  </div>
  <p class="sub-note">资金费率>0.002%=多头拥挤 · L/S>1.5=多头超载 · 多空均价=5x-10x清算均值</p>
</section>

<section id="liquidation">
  <h2>⚡ 爆仓区间估算（10x / 20x 杠杆）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>多头10x爆(距%)</th><th>多头20x爆(距%)</th><th>空头10x爆(距%)</th><th>空头20x爆(距%)</th><th>主要风险方向</th></tr></thead>
    <tbody>{liq_rows}</tbody>
  </table>
  </div>
  <p class="sub-note">差异化公式：ATR波动率缓冲 + 多空比拥挤调整 → 各币种清算距离不同 · 距%=价格距当前价百分比</p>
</section>

<section id="macro">
  <h2>🌐 宏观数据 & ETF 资金流向</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>指标</th><th>24h/当日</th><th>48h/上周</th><th>72h/上月</th><th>来源</th></tr></thead>
    <tbody>
      <tr><td><b>纳斯达克</b></td><td>{nd_val}</td><td>{nd_span}</td><td>{nd_src}</td></tr>{etf_rows}
    </tbody>
  </table>
  </div>
</section>

<section id="signals">
  <h2>🎯 交易信号建议</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>方向</th><th>入场区间</th><th>杠杆</th><th>止盈1</th><th>止盈2</th><th>止损</th><th>R/R</th><th>RSI(14)</th><th>24h买流</th><th>胜率估算</th></tr></thead>
    <tbody>{sig_rows}</tbody>
  </table>
  </div>
  <p class="sub-note">⚠️ 仅供参考，不构成投资建议 · 用户约束：只买ETF基金，不买个股</p>
</section>

{paper_html}

<footer style="margin-top:36px;padding-top:14px;border-top:1px solid var(--border);color:var(--muted);font-size:0.73rem;text-align:center">
  Super Mario 🔍 · 加密货币信号报告 v2 · {date_str} · 数据: Binance / OKX / Bybit / CoinGecko / Stooq
</footer>
</div>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    print(f"✅ HTML: {output_path}")
    return output_path


if __name__ == "__main__":
    today = datetime.now().strftime("%Y%m%d")
    out = REPORTS_DIR / f"crypto_signal_v2_{today}.html"
    generate_html(SIGNAL_FILE, PAPER_FILE, out)
