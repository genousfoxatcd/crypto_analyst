#!/usr/bin/env python3
"""
币安合约网格机器人模拟交易系统 — grid_bot_sim.py
==================================================
模拟中性（多空双向）等差网格策略。

数据源：
  - 价格归档文件：grid_bot_price_archive.json（每小时更新）
  - 信号数据：signal_v2_latest.json（策略参考）

命令:
  init    — 根据分析结果启动网格机器人
  archive — 更新价格归档（从 Binance API 拉取实时价）
  update  — 更新所有活跃机器人状态（P&L、配对、风控）
  status  — 打印所有机器人摘要
  report  — 生成每日模拟报告 HTML
  close   — 手动平仓关闭指定机器人

盈利算法（2026-05-22 修正）：
  每格利润% = spacing_pct × 5 - 0.04%  (币安 Maker 双边费率)
  转总本金：× 0.18 经验系数

使用示例:
  python3 grid_bot_sim.py init --coin SOL --lower 82 --upper 92 --grids 30
  python3 grid_bot_sim.py archive
  python3 grid_bot_sim.py update
  python3 grid_bot_sim.py status
"""

import json
import os
import sys
import argparse
import uuid
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────
CST = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "grid_bot_state.json"
ARCHIVE_FILE = SCRIPT_DIR / "grid_bot_price_archive.json"
SIGNAL_FILE = SCRIPT_DIR / "signal_v2_latest.json"
DEFAULT_LEVERAGE = 5
DEFAULT_CAPITAL = 100.0
MAKER_FEE = 0.04  # 双边 0.02% × 2
CAPITAL_COEFFICIENT = 0.18  # 每格利润转总本金经验系数

BINANCE_COINS = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT",
    "SOL": "SOLUSDT", "DOGE": "DOGEUSDT", "TAO": "TAOUSDT",
    "ZEC": "ZECUSDT", "CAKE": "CAKEUSDT", "PAXG": "PAXGUSDT",
    "HYPE": "HYPEUSDT", "TRX": "TRXUSDT", "AAVE": "AAVEUSDT",
}

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ── 价格缓存（共用 _price_cache，减少 API 调用） ──
try:
    import _price_cache as pcache
    HAS_CACHE = True
except ImportError:
    HAS_CACHE = False


# ── 工具函数 ──────────────────────────────────────────
def now_cst():
    return datetime.now(CST)


def fmt_price(p, decimals=None):
    """智能格式化价格：低价币种自动增加小数位"""
    if decimals is not None:
        return f"{p:,.{decimals}f}"
    p_abs = abs(p)
    if p_abs < 1:
        return f"{p:.4f}"  # DOGE 类：4位小数
    elif p_abs < 10:
        return f"{p:.2f}"  # SOL 类：2位小数
    elif p_abs < 1000:
        return f"{p:.1f}"  # BNB 类：1位小数
    else:
        return f"{p:,.0f}"  # BTC 类：整数


def fmt_ts(dt=None):
    dt = dt or now_cst()
    return dt.strftime("%Y-%m-%d %H:%M CST")


def fmt_short(dt=None):
    dt = dt or now_cst()
    return dt.strftime("%m-%d %H:%M")


def fmt_duration(created_str):
    """将 created_at 字符串转为运行时长（如 2d 5h、12h 30m、8m）"""
    try:
        created = datetime.strptime(created_str, "%Y-%m-%d %H:%M CST")
        created = created.replace(tzinfo=CST)
        delta = now_cst() - created
        total_minutes = int(delta.total_seconds() / 60)
        if total_minutes < 60:
            return f"{total_minutes}m"
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours < 24:
            return f"{hours}h {minutes:02d}m"
        days = hours // 24
        hours_remain = hours % 24
        return f"{days}d {hours_remain}h"
    except Exception:
        return "-"


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


# ── 价格采集 ──────────────────────────────────────────
def fetch_prices():
    """从 Binance 拉取所有币种实时价格（优先使用共享缓存）"""
    # 优先从共享缓存获取（TTL=5min，复用信号引擎结果）
    if HAS_CACHE:
        cached = pcache.get_all(list(BINANCE_COINS.keys()))
        if cached and all(v is not None for v in cached.values()):
            # 缓存存的是 {"price": f, "source": "..."} dict，提取 float
            result = {}
            for coin, val in cached.items():
                if isinstance(val, dict):
                    result[coin] = float(val["price"])
                else:
                    result[coin] = float(val)
            return result

    url = "https://api.binance.com/api/v3/ticker/price"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        all_prices = r.json()
        symbol_set = set(BINANCE_COINS.values())
        prices = {}
        for item in all_prices:
            if item["symbol"] in symbol_set:
                for coin, sym in BINANCE_COINS.items():
                    if item["symbol"] == sym:
                        prices[coin] = float(item["price"])
        # 写入共享缓存
        if prices and HAS_CACHE:
            pcache.set_all({c: {"price": p, "source": "grid_bot"} for c, p in prices.items()})
        return prices
    except Exception as e:
        print(f"  [WARN] 价格获取失败: {e}")
        return None




def read_signal_trend(coin: str) -> str:
    """
    从主信号系统读取网格趋势判断。
    返回: "bullish" | "bearish" | "neutral"
    """
    if not SIGNAL_FILE.exists():
        return "neutral"
    try:
        data = json.loads(SIGNAL_FILE.read_text())
        signals = data.get("signals", {})
        if coin in signals:
            return signals[coin].get("grid_signal", "neutral")
        return "neutral"
    except Exception:
        return "neutral"
def archive_prices():
    """将当前价格追加到归档文件"""
    prices = fetch_prices()
    if not prices:
        return False

    archive = load_json(ARCHIVE_FILE)
    if "records" not in archive:
        archive["records"] = []

    ts = now_cst().isoformat()
    record = {"ts": ts, "timestamp": ts, "prices": prices}
    archive["records"].append(record)

    # 保留最近 30 天
    cutoff = (now_cst() - timedelta(days=30)).isoformat()
    archive["records"] = [r for r in archive["records"]
                          if r.get("timestamp", "") >= cutoff]

    save_json(ARCHIVE_FILE, archive)
    print(f"  [ARCHIVE] {fmt_ts()} | {len(prices)} 币种已归档 | "
          f"总计 {len(archive['records'])} 条记录")
    return True


# ── 网格利润计算 ──────────────────────────────────────
def calc_grid_profit(lower, upper, grids, leverage=DEFAULT_LEVERAGE):
    """计算等差网格每格利润（修正算法）"""
    spacing = (upper - lower) / grids
    mid_price = (upper + lower) / 2
    spacing_pct = (spacing / mid_price) * 100
    per_grid_profit = spacing_pct * leverage - MAKER_FEE
    per_fill_vs_capital = per_grid_profit * CAPITAL_COEFFICIENT
    return {
        "spacing": round(spacing, 4),
        "mid_price": round(mid_price, 4),
        "spacing_pct": round(spacing_pct, 4),
        "per_grid_profit_pct": round(per_grid_profit, 4),
        "per_fill_vs_capital_pct": round(per_fill_vs_capital, 4),
        "per_fill_usd": round(per_fill_vs_capital * DEFAULT_CAPITAL / 100, 4),
    }


# ── 网格机器人核心 ────────────────────────────────────
class GridBot:
    """单个网格机器人实例"""

    def __init__(self, coin, lower, upper, grids, leverage=DEFAULT_LEVERAGE,
                 capital=DEFAULT_CAPITAL, bot_id=None):
        self.bot_id = bot_id or str(uuid.uuid4())[:8]
        self.coin = coin
        self.lower = float(lower)
        self.upper = float(upper)
        self.grids = int(grids)
        self.leverage = int(leverage)
        self.capital = float(capital)
        self.created_at = fmt_ts()
        self.status = "active"  # active | stopped_out | take_profit | closed
        self.closed_at = None
        self.close_reason = None

        # 网格计算
        profit = calc_grid_profit(lower, upper, grids, leverage)
        self.spacing = profit["spacing"]
        self.spacing_pct = profit["spacing_pct"]
        self.per_grid_profit_pct = profit["per_grid_profit_pct"]
        self.per_fill_usd = profit["per_fill_usd"]

        # 状态追踪
        self.last_price = None
        self.last_grid_level = None
        self.completed_pairs = 0
        self.total_pnl = 0.0
        self.peak_pnl = 0.0
        self.max_drawdown = 0.0

        # 网格档位追踪
        self.grid_fills = {}  # {level: {"buy": bool, "sell": bool}}

        # 目标
        self.tp_target = capital * 0.10  # +10U
        self.sl_target = -capital * 0.10  # -10U

        # 更新记录
        self.update_count = 0
        self.last_update = None

    def _get_grid_level(self, price):
        """计算价格对应的网格档位"""
        if price < self.lower:
            return -1
        if price > self.upper:
            return self.grids
        level = int((price - self.lower) / self.spacing)
        return max(0, min(self.grids, level))

    def _get_grid_price(self, level):
        """获取档位对应的价格"""
        return self.lower + level * self.spacing

    def update(self, current_price):
        """用最新价格更新机器人状态"""
        if self.status != "active":
            return

        self.update_count += 1
        self.last_update = fmt_ts()

        # 初始化
        if self.last_price is None:
            self.last_price = current_price
            self.last_grid_level = self._get_grid_level(current_price)
            # 初始化网格档位
            for i in range(self.grids + 1):
                self.grid_fills[str(i)] = {"buy": False, "sell": False}
            return

        price_prev = self.last_price
        level_prev = max(0, min(self.grids, self.last_grid_level))
        level_curr = self._get_grid_level(current_price)

        # 检测网格线穿越
        if level_curr != level_prev:
            # 价格上涨：穿越的档位视为卖出
            if level_curr > level_prev:
                for lvl in range(level_prev + 1, level_curr + 1):
                    if 0 <= lvl <= self.grids:
                        self.grid_fills[str(lvl)]["sell"] = True
            # 价格下跌：穿越的档位视为买入
            else:
                for lvl in range(level_curr + 1, level_prev + 1):
                    if 0 <= lvl <= self.grids:
                        self.grid_fills[str(lvl)]["buy"] = True

            # 检查完成配对（同一档位买和卖都触发 → 完成一次）
            new_pairs = 0
            for lvl in range(self.grids + 1):
                gf = self.grid_fills[str(lvl)]
                if gf["buy"] and gf["sell"] and not gf.get("counted", False):
                    new_pairs += 1
                    gf["counted"] = True

            if new_pairs > 0:
                self.completed_pairs += new_pairs

        # 计算累计 P&L
        self.total_pnl = self.completed_pairs * self.per_fill_usd

        # 更新追踪
        self.last_price = current_price
        self.last_grid_level = level_curr
        if self.total_pnl > self.peak_pnl:
            self.peak_pnl = self.total_pnl
        self.max_drawdown = self.peak_pnl - self.total_pnl

        # 止盈止损检查
        if self.total_pnl >= self.tp_target:
            self.status = "take_profit"
            self.closed_at = fmt_ts()
            self.close_reason = f"TP触发: +${self.total_pnl:.2f} ≥ +${self.tp_target:.2f}"
        elif self.total_pnl <= self.sl_target:
            self.status = "stopped_out"
            self.closed_at = fmt_ts()
            self.close_reason = f"SL触发: -${abs(self.total_pnl):.2f} ≤ -${abs(self.sl_target):.2f}"

    def to_dict(self):
        return {
            "bot_id": self.bot_id,
            "coin": self.coin,
            "lower": self.lower,
            "upper": self.upper,
            "grids": self.grids,
            "leverage": self.leverage,
            "capital": self.capital,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "close_reason": self.close_reason,
            "spacing": self.spacing,
            "spacing_pct": self.spacing_pct,
            "per_grid_profit_pct": self.per_grid_profit_pct,
            "per_fill_usd": self.per_fill_usd,
            "completed_pairs": self.completed_pairs,
            "total_pnl": round(self.total_pnl, 4),
            "peak_pnl": round(self.peak_pnl, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "tp_target": self.tp_target,
            "sl_target": self.sl_target,
            "last_price": self.last_price,
            "last_grid_level": self.last_grid_level,
            "update_count": self.update_count,
            "last_update": self.last_update,
            "current_pos_pct": (
                round((self.last_price - self.lower) / (self.upper - self.lower) * 100, 1)
                if self.last_price else None
            ),
            "grid_fills": self.grid_fills,
        }

    @classmethod
    def from_dict(cls, d):
        bot = cls(
            coin=d["coin"], lower=d["lower"], upper=d["upper"],
            grids=d["grids"], leverage=d["leverage"],
            capital=d["capital"], bot_id=d["bot_id"]
        )
        bot.status = d["status"]
        bot.created_at = d["created_at"]
        bot.closed_at = d.get("closed_at")
        bot.close_reason = d.get("close_reason")
        bot.completed_pairs = d.get("completed_pairs", 0)
        bot.total_pnl = d.get("total_pnl", 0.0)
        bot.peak_pnl = d.get("peak_pnl", 0.0)
        bot.max_drawdown = d.get("max_drawdown", 0.0)
        bot.last_price = d.get("last_price")
        bot.last_grid_level = d.get("last_grid_level")
        bot.update_count = d.get("update_count", 0)
        bot.last_update = d.get("last_update")
        # 恢复网格档位
        for i in range(bot.grids + 1):
            bot.grid_fills[str(i)] = d.get("grid_fills", {}).get(
                str(i), {"buy": False, "sell": False}
            )
        return bot


# ── 机器人管理器 ──────────────────────────────────────
class GridBotManager:
    """管理所有网格机器人"""

    def __init__(self):
        self.bots = {}  # {bot_id: GridBot}
        self._load()

    def _load(self):
        state = load_json(STATE_FILE)
        for bid, bd in state.get("bots", {}).items():
            self.bots[bid] = GridBot.from_dict(bd)

    def _save(self):
        data = {"bots": {bid: bot.to_dict() for bid, bot in self.bots.items()},
                "updated_at": fmt_ts()}
        save_json(STATE_FILE, data)

    def init_bot(self, coin, lower, upper, grids, leverage=DEFAULT_LEVERAGE,
                 capital=DEFAULT_CAPITAL, signal_trend=None):
        """
        创建网格机器人，可选根据主信号趋势调整参数。
        signal_trend: "bullish" | "bearish" | "neutral" | None
        """
        # 根据趋势调整网格参数
        if signal_trend == "bullish":
            # 牛市：偏多网格（lower 下调 2%，让更多买单成交）
            lower = lower * 0.98
            print(f"  📈 牛市模式：lower 下调 2% (${lower:.2f})")
        elif signal_trend == "bearish":
            # 熊市：偏空网格（upper 上调 2%，让更多卖单成交）
            upper = upper * 1.02
            print(f"  📉 熊市模式：upper 上调 2% (${upper:.2f})")
        else:
            print(f"  ⚪ 中性模式：标准网格")
        
        bot = GridBot(coin, lower, upper, grids, leverage, capital)
        self.bots[bot.bot_id] = bot
        self._save()
        return bot

    def close_bot(self, bot_id, reason="手动关闭"):
        if bot_id in self.bots:
            self.bots[bot_id].status = "closed"
            self.bots[bot_id].closed_at = fmt_ts()
            self.bots[bot_id].close_reason = reason
            self._save()
            return True
        return False

    def update_all(self):
        """更新所有活跃机器人"""
        prices = fetch_prices()
        if not prices:
            print("  [ERROR] 无法获取价格，跳过更新")
            return

        updated = 0
        for bid, bot in self.bots.items():
            if bot.status != "active":
                continue
            if bot.coin not in prices:
                print(f"  [WARN] {bot.coin} 价格缺失")
                continue
            bot.update(prices[bot.coin])
            updated += 1

        self._save()
        print(f"  [UPDATE] {fmt_ts()} | {updated} 个机器人已更新")
        for bid, bot in self.bots.items():
            if bot.status == "active" and bot.last_price:
                pos_pct = (bot.last_price - bot.lower) / (bot.upper - bot.lower) * 100
                print(f"    {bot.coin:5} P&L: ${bot.total_pnl:+.2f} | "
                      f"配对:{bot.completed_pairs} | "
                      f"价格:${fmt_price(bot.last_price)} | "
                      f"进度:{pos_pct:.0f}%")


        # 检查触发
        for bid, bot in self.bots.items():
            if bot.status == "take_profit":
                print(f"\n  ✅ {bot.coin} 止盈触发! {bot.close_reason}")
            elif bot.status == "stopped_out":
                print(f"\n  🔴 {bot.coin} 止损触发! {bot.close_reason}")
    def get_active(self):
        return {bid: bot for bid, bot in self.bots.items()
                if bot.status == "active"}

    def get_all(self):
        return self.bots

    def print_status(self):
        active = self.get_active()
        closed = {bid: bot for bid, bot in self.bots.items()
                  if bot.status != "active"}

        print(f"\n{'='*80}")
        print(f"  🤖 币安合约网格机器人 · 模拟交易系统")
        print(f"  更新: {fmt_ts()}")
        print(f"{'='*80}")

        # ── 最新价格速览 ──
        prices = fetch_prices()
        if prices:
            print(f"\n  📊 虚拟币最新价格")
            print(f"  {'币种':5} {'价格':>12} {'24h参考':>12}")
            print(f"  {'─'*32}")
            for coin in BINANCE_COINS:
                if coin in prices:
                    print(f"  {coin:5} ${fmt_price(prices[coin]):>10}  —")
            print()

        if not self.bots:
            print("  暂无机器人")
            return

        if active:
            print(f"\n  🔥 运行中 ({len(active)} 个)")
            header = f"  {'币种':5} {'区间':>16} {'网格':>5} {'配对':>5} "
            header += f"{'P&L':>9} {'P&L%':>6} {'价格':>10} {'进度':>5} {'运行时长':>10} {'状态'}"
            print(header)
            print(f"  {'─' * (len(header) - 2)}")
            for bid, b in sorted(active.items()):
                range_str = f"${fmt_price(b.lower)}-${fmt_price(b.upper)}"
                pnl_pct = b.total_pnl / b.capital * 100
                status_icon = "🟢" if b.total_pnl > 0 else ("🔴" if b.total_pnl < 0 else "⚪")
                pos_pct = (b.last_price - b.lower) / (b.upper - b.lower) * 100 if b.last_price else 0
                duration = fmt_duration(b.created_at)
                print(f"  {b.coin:5} {range_str:>16} {b.grids:>5} "
                      f"{b.completed_pairs:>5} "
                      f"${b.total_pnl:>+8.2f} {pnl_pct:>+5.1f}% "
                      f"${fmt_price(b.last_price or 0):>9} "
                      f"{pos_pct:>4.0f}% "
                      f"{duration:>10} "
                      f"{status_icon}")

        if closed:
            print(f"\n  📜 已关闭 ({len(closed)} 个)")
            for bid, b in sorted(closed.items()):
                icon = "✅" if b.status == "take_profit" else "🔴"
                print(f"  {icon} {b.coin:5} {b.status:14} P&L: ${b.total_pnl:+.2f} "
                      f"配对:{b.completed_pairs} | {b.close_reason}")

    def generate_html_report(self, output_path=None):
        """生成每日模拟报告 HTML"""
        from datetime import datetime

        active = self.get_active()
        closed = {bid: bot for bid, bot in self.bots.items()
                  if bot.status != "active"}

        if not output_path:
            date_str = now_cst().strftime("%Y%m%d")
            output_path = (Path(__file__).resolve().parents[1] /
                           "reports" / f"grid_bot_daily_{date_str}.html")

        rows_active = ""
        for bid, b in sorted(active.items()):
            pnl_pct = b.total_pnl / b.capital * 100 if b.capital else 0
            pnl_color = "#22c55e" if b.total_pnl >= 0 else "#ef4444"
            pos_pct = (b.last_price - b.lower) / (b.upper - b.lower) * 100 if b.last_price else 0
            rows_active += f"""
            <tr>
              <td><strong>{b.coin}</strong></td>
              <td>${fmt_price(b.lower)}–${fmt_price(b.upper)}</td>
              <td>{b.grids}</td>
              <td>{b.completed_pairs}</td>
              <td style="color:{pnl_color}">${b.total_pnl:+.2f}</td>
              <td style="color:{pnl_color}">{pnl_pct:+.2f}%</td>
              <td>${fmt_price(b.last_price or 0)}</td>
              <td>{pos_pct:.0f}%</td>
              <td>{b.leverage}x</td>
            </tr>"""

        rows_closed = ""
        for bid, b in sorted(closed.items()):
            icon = "✅" if b.status == "take_profit" else "🔴"
            rows_closed += f"""
            <tr>
              <td>{icon} {b.coin}</td>
              <td>{b.status}</td>
              <td>{b.completed_pairs}</td>
              <td style="color:{'#22c55e' if b.total_pnl>=0 else '#ef4444'}">${b.total_pnl:+.2f}</td>
              <td>{b.close_reason or '-'}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>网格机器人模拟交易日报 | {now_cst().strftime('%Y-%m-%d')}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="container">
<div class="header">
  <h1>🤖 币安合约网格机器人 · 模拟交易日报</h1>
  <div style="color:var(--dim);font-size:13px;margin-top:4px;">
    {now_cst().strftime('%Y-%m-%d %H:%M UTC+8 北京时间')} | 引擎: grid_bot_sim v1.0</div>
</div>

<div class="section">
  <h2>📊 概览</h2>
  <div class="summary">
    <div class="sum-item">
      <div class="sum-val" style="color:var(--gold)">{len(self.bots)}</div>
      <div class="sum-label">总机器人</div>
    </div>
    <div class="sum-item">
      <div class="sum-val" style="color:var(--green)">{len(active)}</div>
      <div class="sum-label">运行中</div>
    </div>
    <div class="sum-item">
      <div class="sum-val" style="color:var(--dim)">{len(closed)}</div>
      <div class="sum-label">已关闭</div>
    </div>
    <div class="sum-item">
      <div class="sum-val" style="color:{'#22c55e' if sum(b.total_pnl for b in self.bots.values())>=0 else '#ef4444'}">
        ${sum(b.total_pnl for b in self.bots.values()):+.2f}</div>
      <div class="sum-label">总P&L</div>
    </div>
  </div>
</div>

<div class="section">
  <h2>🔥 运行中机器人</h2>
  <table>
    <thead><tr>
      <th>币种</th><th>区间</th><th>网格</th><th>配对</th>
      <th>P&L(USD)</th><th>P&L%</th><th>价格</th><th>进度</th><th>杠杆</th>
    </tr></thead>
    <tbody>{rows_active}</tbody>
  </table>
</div>

<div class="section">
  <h2>📜 已关闭</h2>
  <table>
    <thead><tr><th>币种</th><th>结果</th><th>配对</th><th>P&L</th><th>原因</th></tr></thead>
    <tbody>{rows_closed or '<tr><td colspan="5" style="color:var(--dim)">暂无</td></tr>'}</tbody>
  </table>
</div>

<div class="disclaimer">
  ⚠️ 模拟交易数据，基于 Binance 实时价格 + 网格穿越配对算法。不构成投资建议。
  止盈/止损 = ±10U（本金10%）。实际币安网格手续费和滑点可能不同。
</div>
</div>
</body>
</html>"""

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            f.write(html)
        print(f"  [REPORT] 已保存: {output_path}")
        return output_path


# ── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="网格机器人模拟交易系统")
    sub = parser.add_subparsers(dest="cmd")

    # init
    p_init = sub.add_parser("init", help="启动新网格机器人")
    p_init.add_argument("--coin", required=True)
    p_init.add_argument("--lower", type=float, required=True)
    p_init.add_argument("--upper", type=float, required=True)
    p_init.add_argument("--grids", type=int, required=True)
    p_init.add_argument("--leverage", type=int, default=DEFAULT_LEVERAGE)
    p_init.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)

    # archive
    sub.add_parser("archive", help="归档当前价格")

    # update
    sub.add_parser("update", help="更新所有活跃机器人")

    # status
    sub.add_parser("status", help="打印机器人状态")

    # report
    p_rep = sub.add_parser("report", help="生成每日报告")

    # close
    p_close = sub.add_parser("close", help="关闭指定机器人")
    p_close.add_argument("--id", required=True)

    args = parser.parse_args()

    if args.cmd == "init":
        mgr = GridBotManager()
        # 硬约束检查（修正系数 0.06，基于币安实际每格最低要求）
        min_capital = args.upper * args.grids * 0.06 / args.leverage
        if min_capital > 150:
            print(f"  ❌ {args.coin} 最低所需本金 ≈ ${min_capital:.0f}U，超过预算 (100U)")
            print(f"    建议：选择低价币种或减少网格数")
            return
        bot = mgr.init_bot(args.coin, args.lower, args.upper, args.grids,
                           args.leverage, args.capital)
        print(f"  ✅ 机器人已启动: {bot.coin} | ID: {bot.bot_id}")
        print(f"    区间: ${bot.lower:,.2f} – ${bot.upper:,.2f} | "
              f"{bot.grids}格 | {bot.leverage}x | ${bot.capital}U")
        print(f"    每格利润: {bot.per_grid_profit_pct:.2f}% | "
              f"每次配对 ≈ ${bot.per_fill_usd:.4f}")
        profit_info = calc_grid_profit(args.lower, args.upper, args.grids, args.leverage)
        print(f"    [调试] 间距={profit_info['spacing']:.4f} | "
              f"间距%={profit_info['spacing_pct']:.4f}% | "
              f"预估本金={min_capital:.0f}U")

    elif args.cmd == "archive":
        archive_prices()

    elif args.cmd == "update":
        mgr = GridBotManager()
        mgr.update_all()

    elif args.cmd == "status":
        mgr = GridBotManager()
        mgr.print_status()

    elif args.cmd == "report":
        mgr = GridBotManager()
        path = mgr.generate_html_report()
        print(f"  报告路径: {path}")

    elif args.cmd == "close":
        mgr = GridBotManager()
        if mgr.close_bot(args.id):
            print(f"  ✅ 机器人 {args.id} 已关闭")
        else:
            print(f"  ❌ 未找到机器人 {args.id}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
