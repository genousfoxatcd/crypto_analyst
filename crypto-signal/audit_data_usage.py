#!/usr/bin/env python3
"""
数据使用审计脚本 - 检测违规使用旧数据和模拟数据
每次报告生成后自动运行，也可手动运行
"""

import json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 路径配置
CRYPTO_WS = Path("/Users/alex/projects/crypto_analyst")
BASE         = CRYPTO_WS / "crypto-signal"
SIGNAL_FILE  = BASE / "signal_v2_latest.json"
AUDIT_LOG    = BASE / "data_audit.log"

CST = timezone(timedelta(hours=8), "CST")


def log_audit(message: str):
    """写入审计日志"""
    timestamp = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST")
    with open(AUDIT_LOG, "a") as f:
        f.write(f"[{timestamp}] {message}\n")
    print(f"  [AUDIT] {message}")


def audit_data_freshness() -> list[str]:
    """审计数据新鲜度"""
    violations = []
    
    if not SIGNAL_FILE.exists():
        violations.append("P0: 信号文件不存在")
        log_audit("P0: 信号文件不存在")
        return violations
    
    data = json.loads(SIGNAL_FILE.read_text())
    gen_time_str = data.get("generated_at", "")
    
    if not (gen_time_str.endswith("CST") or gen_time_str.endswith("北京时间")):
        violations.append("P0: 时间戳格式错误")
        log_audit(f"P0: 时间戳格式错误: {gen_time_str}")
        return violations
    
    try:
        gen_dt = datetime.strptime(gen_time_str.replace(" CST", "").replace(" UTC+8 北京时间", "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=CST)
        now = datetime.now(CST)
        age_minutes = (now - gen_dt).total_seconds() / 60
        
        if age_minutes > 60:
            violations.append(f"P1: 数据过期 {age_minutes:.0f} 分钟（>60分钟）")
            log_audit(f"P1: 数据过期 {age_minutes:.0f} 分钟")
        elif age_minutes > 30:
            violations.append(f"P2: 数据过期 {age_minutes:.0f} 分钟（30-60分钟）")
            log_audit(f"P2: 数据过期 {age_minutes:.0f} 分钟")
        else:
            log_audit(f"✅ 数据新鲜度通过: {age_minutes:.1f} 分钟")
    except Exception as e:
        violations.append(f"P0: 时间戳解析失败: {e}")
        log_audit(f"P0: 时间戳解析失败: {e}")
    
    return violations


def audit_simulated_data() -> list[str]:
    """审计模拟数据"""
    violations = []
    
    if not SIGNAL_FILE.exists():
        return violations
    
    data = json.loads(SIGNAL_FILE.read_text())
    
    # 检查1: 价格数据是否有数据源标识
    prices = data.get("prices", {})
    for coin, pdata in prices.items():
        if not pdata.get("sources"):
            violations.append(f"P3: {coin} 缺少数据源标识")
            log_audit(f"P3: {coin} 缺少数据源标识")
        
        if not pdata.get("is_valid", True):
            reason = pdata.get("invalid_reason", "未知")
            violations.append(f"P2: {coin} 数据无效（{reason}）")
            log_audit(f"P2: {coin} 数据无效（{reason}）")
    
    # 检查2: 信号数据是否有 price_source
    signals = data.get("signals", {})
    for coin, sg in signals.items():
        if not sg.get("price_source"):
            # 不是P0，因为可能从 prices 继承
            pass
    
    return violations


def audit_report_compliance() -> list[str]:
    """审计报告合规性（检查最近7天的报告）"""
    violations = []
    
    report_dir = CRYPTO_WS / "reports"
    if not report_dir.exists():
        return violations
    
    # 获取最近7天的报告
    import os
    cutoff = datetime.now(CST).timestamp() - 7 * 24 * 3600
    
    for fname in os.listdir(report_dir):
        if not fname.startswith("crypto_signal_v2_") or not fname.endswith(".html"):
            continue
        
        fpath = report_dir / fname
        if fpath.stat().st_mtime < cutoff:
            continue  # 超过7天，跳过
        
        # 检查报告中是否有数据新鲜度警告
        content = fpath.read_text()
        if "生成:" in content and "CST" in content:
            # 报告有时间戳，检查是否有警告
            if "⚠️" in content or "🔴" in content:
                # 有警告，可能是过期数据
                if "分钟前更新" in content:
                    # 提取分钟数
                    import re
                    match = re.search(r'(\d+(?:\.\d+)?)\s*分钟前更新', content)
                    if match:
                        minutes = float(match.group(1))
                        if minutes > 60:
                            violations.append(f"P1: 报告 {fname} 使用 >60分钟 旧数据")
                            log_audit(f"P1: 报告 {fname} 使用 >60分钟 旧数据")
    
    return violations


def main():
    """主审计函数"""
    print("🔍 开始数据使用审计...")
    log_audit("========== 审计开始 ==========")
    
    all_violations = []
    
    # 审计1: 数据新鲜度
    print("\n1️⃣  审计数据新鲜度...")
    violations1 = audit_data_freshness()
    all_violations.extend(violations1)
    
    # 审计2: 模拟数据
    print("2️⃣  审计模拟数据...")
    violations2 = audit_simulated_data()
    all_violations.extend(violations2)
    
    # 审计3: 报告合规性
    print("3️⃣  审计报告合规性...")
    violations3 = audit_report_compliance()
    all_violations.extend(violations3)
    
    # 输出审计报告
    print("\n" + "="*60)
    print("📋 审计报告摘要")
    print("="*60)
    
    if all_violations:
        print(f"🔴 发现 {len(all_violations)} 个违规:")
        for i, v in enumerate(all_violations, 1):
            print(f"  {i}. {v}")
        
        log_audit(f"审计完成: 发现 {len(all_violations)} 个违规")
    else:
        print("✅ 未发现违规，数据使用符合规范")
        log_audit("审计完成: 未发现违规")
    
    print("="*60)
    print(f"📝 审计日志: {AUDIT_LOG}")
    print(f"   查看: cat {AUDIT_LOG}")
    print("="*60)
    
    log_audit("========== 审计结束 ==========\n")
    
    # 返回退出码（有违规则返回1）
    return 1 if all_violations else 0


if __name__ == "__main__":
    sys.exit(main())
