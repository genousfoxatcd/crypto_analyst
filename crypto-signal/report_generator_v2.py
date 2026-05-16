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
    rows = ""
    for coin, sd in signals.items():
        fr  = sd.get("funding_rate", 0) or 0
        liq = sd.get("liquidation", {}) or {}
        ls  = liq.get("ls_ratio", 1) or 1
        bsr = sd.get("taker_bsr", 1) or 1
        flow = sd.get("flow_buy_pct", 50) or 50
        fr_color  = "#f87171" if fr > 0.002 else ("#4ade80" if fr < -0.001 else "#e5e7eb")
        ls_color  = "#f87171" if ls > 1.5 else ("#4ade80" if ls < 0.8 else "#e5e7eb")
        bsr_color = "#4ade80" if bsr > 1.3 else ("#f87171" if bsr < 0.7 else "#e5e7eb")
        flow_color= "#4ade80" if flow > 55 else ("#f87171" if flow < 45 else "#e5e7eb")
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b></td>"
            f"<td style='color:{fr_color}'>{fr:+.4f}%</td>"
            f"<td style='color:{ls_color}'>{ls:.3f}</td>"
            f"<td style='color:{bsr_color}'>{bsr:.3f}</td>"
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
    rows = ""
    for coin, sd in signals.items():
        liq = sd.get("liquidation", {}) or {}
        ll5  = liq.get("liq_long_zones", {}).get("5x")
        ll10 = liq.get("liq_long_zones", {}).get("10x")
        ls5  = liq.get("liq_short_zones", {}).get("5x")
        ls10 = liq.get("liq_short_zones", {}).get("10x")
        risk = liq.get("primary_risk", "NEUTRAL")
        risk_cell = (
            '<span class="badge badge-sl">多头爆仓风险</span>' if risk == "DOWN" else
            '<span class="badge badge-tp">空头爆仓风险</span>' if risk == "UP" else
            '<span style="color:#6b7280">中性</span>'
        )
        rows += (
            f"<tr>"
            f"<td><b>{coin}</b></td>"
            f"<td style='color:#4ade80'>{fmt_price(ll5, coin)}</td>"
            f"<td style='color:#4ade80'>{fmt_price(ll10, coin)}</td>"
            f"<td style='color:#f87171'>{fmt_price(ls5, coin)}</td>"
            f"<td style='color:#f87171'>{fmt_price(ls10, coin)}</td>"
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
    <thead><tr><th>币种</th><th>资金费率</th><th>多空比 L/S</th><th>吃单比 BSR</th><th>24h买方流量</th></tr></thead>
    <tbody>{contract_rows}</tbody>
  </table>
  </div>
  <p class="sub-note">资金费率>0.002%=多头拥挤 · L/S>1.5=多头超载 · BSR>1.3=买方主导</p>
</section>

<section id="technicals">
  <h2>📊 4h 布林带（20,2σ）+ ATR + 资金流</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>BB下轨</th><th>BB中轨</th><th>BB上轨</th><th>价格位置%</th><th>ATR%</th><th>24h买流占比</th></tr></thead>
    <tbody>{tech_rows}</tbody>
  </table>
  </div>
  <p class="sub-note">价格位置: 0%=下轨 / 100%=上轨 / &gt;100%=突破上轨（超买警告）</p>
</section>

<section id="liquidation">
  <h2>⚡ 爆仓区间估算（插针风险）</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>多头5x爆</th><th>多头10x爆</th><th>空头5x爆</th><th>空头10x爆</th><th>主要风险方向</th></tr></thead>
    <tbody>{liq_rows}</tbody>
  </table>
  </div>
</section>

<section id="macro">
  <h2>🌐 宏观数据</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>指标</th><th>数值</th><th>日涨跌</th><th>来源</th></tr></thead>
    <tbody>
      <tr><td><b>纳斯达克</b></td><td>{nd_val}</td><td>{nd_span}</td><td>{nd_src}</td></tr>
      <tr><td><b>美元指数 DXY</b></td><td colspan="3" style="color:var(--muted)">数据不可用</td></tr>
    </tbody>
  </table>
  </div>
</section>

<section id="signals">
  <h2>🎯 交易信号建议</h2>
  <div class="table-wrap">
  <table>
    <thead><tr><th>币种</th><th>方向</th><th>入场区间</th><th>杠杆</th><th>止盈1</th><th>止盈2</th><th>止损</th><th>R/R</th><th>胜率估算</th></tr></thead>
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
