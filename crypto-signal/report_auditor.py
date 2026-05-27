#!/usr/bin/env python3
"""
报告数据审核脚本 — 对照 prices_latest.json 审核 HTML 报告中的价格数据一致性
输出: audit_result.json
"""

import json
import os
import re
import sys
from pathlib import Path

# ── 工作区根路径（自动检测）───────────────────────────────────
# 优先使用环境变量，否则基于脚本位置推断
_WORKSPACE = Path(os.environ.get(
    "CRYPTO_ANALYST_WS",
    "/Users/alex/projects/crypto_analyst"
))
CRYPTO_DIR = _WORKSPACE / "crypto-signal"
REPORTS_DIR = _WORKSPACE / "reports"

# 可配置输入（支持命令行参数覆盖）
PRICES_FILE = CRYPTO_DIR / "prices_latest.json"
REPORT_FILE = REPORTS_DIR / "crypto_signal_20260511.html"
OUTPUT_FILE = CRYPTO_DIR / "audit_result.json"

TOLERANCE_PCT = 5.0  # 允许偏差上限


def extract_prices_from_html(html: str) -> dict:
    """从 HTML 中提取价格数据（粗匹配）"""
    found = {}
    patterns = {
        "BTC":  r"\$8[0-9],[0-9]{3}",
        "ETH":  r"\$2,[0-9]{3}",
        "BNB":  r"\$6[0-9]{2}",
        "SOL":  r"\$[89][0-9]\.[0-9]+|\$9[0-9]\.[0-9]+",
        "DOGE": r"\$0\.[0-9]{3,4}",
        "TAO":  r"\$3[0-9]{2}",
    }
    for coin, pattern in patterns.items():
        matches = re.findall(pattern, html)
        if matches:
            # 取第一个（通常是仪表盘价格）
            raw = matches[0].replace("$", "").replace(",", "")
            try:
                found[coin] = float(raw)
            except ValueError:
                found[coin] = None
    return found


def run_audit():
    prices_data = json.loads(Path(PRICES_FILE).read_text())
    html = Path(REPORT_FILE).read_text()

    real = {coin: d["price"] for coin, d in prices_data["coins"].items()}
    reported = extract_prices_from_html(html)

    issues = []
    passed = []

    for coin in real:
        real_price = real[coin]
        rep_price = reported.get(coin)

        if rep_price is None:
            issues.append({
                "coin": coin,
                "severity": "WARNING",
                "message": f"HTML中未能提取到 {coin} 价格",
                "real_price": real_price,
                "reported_price": None,
            })
            continue

        dev = abs(rep_price - real_price) / real_price * 100
        if dev > TOLERANCE_PCT:
            issues.append({
                "coin": coin,
                "severity": "ERROR" if dev > 10 else "WARNING",
                "message": f"价格偏差 {dev:.1f}%（超出容差 {TOLERANCE_PCT}%）",
                "real_price": real_price,
                "reported_price": rep_price,
                "deviation_pct": round(dev, 2),
            })
        else:
            passed.append({
                "coin": coin,
                "real_price": real_price,
                "reported_price": rep_price,
                "deviation_pct": round(dev, 2),
            })

    result = {
        "audit_time": prices_data["fetched_at"],
        "tolerance_pct": TOLERANCE_PCT,
        "total_coins": len(real),
        "passed": len(passed),
        "issues": len(issues),
        "status": "PASS" if not issues else "FAIL",
        "passed_coins": passed,
        "issue_coins": issues,
    }

    Path(OUTPUT_FILE).write_text(json.dumps(result, indent=2, ensure_ascii=False))

    # 打印报告
    print(f"\n=== 报告审核结果 ===")
    print(f"状态: {'✅ PASS' if result['status'] == 'PASS' else '❌ FAIL'}")
    print(f"通过: {result['passed']}/{result['total_coins']} 个币种")
    if issues:
        print(f"\n⚠️ 发现 {len(issues)} 个问题:")
        for i in issues:
            print(f"  [{i['severity']}] {i['coin']}: 报告 ${i['reported_price']} → 实际 ${i['real_price']:.2f}  (偏差 {i.get('deviation_pct', '?')}%)")
    if passed:
        print(f"\n✅ 通过验证:")
        for p in passed:
            print(f"  {p['coin']}: 报告 ${p['reported_price']} ≈ 实际 ${p['real_price']:.2f} (偏差 {p['deviation_pct']}%)")
    print(f"\n详细结果: {OUTPUT_FILE}")
    return result


if __name__ == "__main__":
    run_audit()
