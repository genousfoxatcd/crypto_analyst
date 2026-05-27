"""
调度器 (Scheduler)
每小时自动更新数据、生成报告、推送重要更新
"""

import schedule
import time
import sys
import json
from datetime import datetime
from pathlib import Path
import subprocess
import importlib.util

# 添加 crypto-signal 目录到 Python 路径
CRYPTO_SIGNAL_DIR = Path(__file__).parent
if str(CRYPTO_SIGNAL_DIR) not in sys.path:
    sys.path.insert(0, str(CRYPTO_SIGNAL_DIR))

# 配置文件路径
CONFIG_FILE = CRYPTO_SIGNAL_DIR / "config.json"

# 日志目录
LOGS_DIR = CRYPTO_SIGNAL_DIR.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def load_config() -> dict:
    """加载配置文件"""
    if not CONFIG_FILE.exists():
        print(f"  ⚠️  配置文件不存在: {CONFIG_FILE}")
        return {}
    
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        return config
    except Exception as e:
        print(f"  ❌ 加载配置文件失败: {e}")
        return {}


def hourly_update():
    """每小时更新数据"""
    print(f"\n{'=' * 60}")
    print(f"  🔄 每小时更新 - {datetime.now().strftime('%Y-%m-%d %H:%M CST')}")
    print(f"{'=' * 60}")
    
    # 1. 运行信号引擎（更新价格、合约数据等）
    try:
        signal_script = CRYPTO_SIGNAL_DIR / "crypto_signal_v2.py"
        
        result = subprocess.run(
            [sys.executable, str(signal_script)],
            cwd=CRYPTO_SIGNAL_DIR,
            capture_output=True,
            text=True,
            timeout=180
        )
        
        if result.returncode != 0:
            print(f"  ❌ 信号引擎运行失败: {result.stderr[:200]}")
            return
        
        print(f"  ✅ 信号引擎更新完成")
        
        # 打印输出（用于调试）
        if result.stdout:
            for line in result.stdout.split('\n'):
                if line.strip():
                    print(f"    {line}")
    
    except Exception as e:
        print(f"  ❌ 信号引擎运行异常: {e}")
        return
    
    # 2. 检查是否有重要变化（需要推送）
    try:
        check_and_notify()
    except Exception as e:
        print(f"  ⚠️ 检查推送失败: {e}")
    
    # 3. 更新模拟交易状态
    try:
        # 动态导入 paper_trader 模块
        spec = importlib.util.spec_from_file_location("paper_trader", CRYPTO_SIGNAL_DIR / "paper_trader.py")
        paper_trader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(paper_trader)
        
        # 调用 cmd_update 函数
        paper_trader.cmd_update()
        
        print(f"  ✅ 模拟交易状态更新完成")
    
    except Exception as e:
        print(f"  ⚠️ 模拟交易更新失败: {e}")


def check_and_notify():
    """检查是否有重要变化，如果有则推送"""
    config = load_config()
    if not config:
        return
    
    notification_config = config.get("notification", {})
    
    # 读取最新信号和上次信号（如果有存档）
    signal_file = CRYPTO_SIGNAL_DIR / config.get("system", {}).get("signal_file", "signal_v2_latest.json")
    
    if not signal_file.exists():
        print(f"  ⚠️ 信号文件不存在: {signal_file}")
        return
    
    try:
        current_signal = json.loads(signal_file.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"  ❌ 读取信号文件失败: {e}")
        return
    
    # 读取上次信号（存档）
    archive_dir = CRYPTO_SIGNAL_DIR / "signal_history"
    if archive_dir.exists():
        archives = sorted(archive_dir.glob("signal_v2_*.json"), reverse=True)
        if len(archives) >= 2:
            try:
                last_signal = json.loads(archives[1].read_text(encoding='utf-8'))
                compare_and_notify(current_signal, last_signal, notification_config)
            except Exception as e:
                print(f"  ⚠️ 比较信号失败: {e}")


def compare_and_notify(current: dict, last: dict, config: dict):
    """
    比较当前信号和上次信号，如果有重要变化则推送
    
    Args:
        current: 当前信号数据
        last: 上次信号数据
        config: 通知配置
    """
    # 动态导入 notifier 模块
    try:
        spec = importlib.util.spec_from_file_location("notifier", CRYPTO_SIGNAL_DIR / "notifier.py")
        notifier_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(notifier_module)
        
        notifier = notifier_module.WeChatNotifier(CONFIG_FILE)
    
    except Exception as e:
        print(f"  ⚠️ 创建推送器失败: {e}")
        return
    
    messages = []
    
    # 1. 检查 prob_score 变化
    prob_score_threshold = config.get("prob_score_change_threshold", 0.15)
    
    current_signals = current.get("signals", {})
    last_signals = last.get("signals", {})
    
    for coin in current_signals:
        if coin not in last_signals:
            continue
        
        current_prob = current_signals[coin].get("prob_score", 0)
        last_prob = last_signals[coin].get("prob_score", 0)
        
        if current_prob == 0 or last_prob == 0:
            continue
        
        change_pct = abs(current_prob - last_prob) / last_prob
        
        if change_pct > prob_score_threshold:
            messages.append(f"  📊 {coin} prob_score 变化: {last_prob:.1f}% → {current_prob:.1f}% ({change_pct:+.1%})")
    
    # 2. 检查方向改变
    for coin in current_signals:
        if coin not in last_signals:
            continue
        
        current_dir = current_signals[coin].get("direction", "")
        last_dir = last_signals[coin].get("direction", "")
        
        if current_dir != last_dir and current_dir and last_dir:
            messages.append(f"  🔄 {coin} 方向改变: {last_dir} → {current_dir}")
    
    # 3. 检查胜率下降
    win_rate_threshold = config.get("win_rate_drop_threshold", 0.2)
    
    # 这里需要读取历史交易数据来计算胜率
    # 暂时跳过，等待实现
    
    # 如果有变化，则推送
    if messages:
        message = "📊 **信号变化通知**\n\n"
        message += f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M CST')}\n\n"
        message += "\n".join(messages)
        
        notifier.send_message(message, title="信号变化通知")
        print(f"  ✅ 信号变化通知已推送")


def daily_report():
    """每日报告（10:00/22:00）"""
    print(f"\n{'=' * 60}")
    print(f"  📊 每日报告 - {datetime.now().strftime('%Y-%m-%d %H:%M CST')}")
    print(f"{'=' * 60}")
    
    # 生成完整 HTML 报告
    try:
        report_script = CRYPTO_SIGNAL_DIR / "report_generator_v2.py"
        
        result = subprocess.run(
            [sys.executable, str(report_script)],
            cwd=CRYPTO_SIGNAL_DIR,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            print(f"  ❌ 报告生成失败: {result.stderr[:200]}")
            return
        
        print(f"  ✅ 报告生成完成")
        
        # 推送报告
        send_notification(f"📊 每日报告已更新 - {datetime.now().strftime('%m-%d %H:%M')}")
    
    except Exception as e:
        print(f"  ❌ 报告生成异常: {e}")
        return


def send_notification(message: str):
    """
    发送推送（调用 notifier.py）
    
    Args:
        message: 推送消息内容
    """
    try:
        # 动态导入 notifier 模块
        spec = importlib.util.spec_from_file_location("notifier", CRYPTO_SIGNAL_DIR / "notifier.py")
        notifier_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(notifier_module)
        
        notifier = notifier_module.WeChatNotifier(CONFIG_FILE)
        notifier.send_message(message, title="加密货币分析系统通知")
    
    except Exception as e:
        print(f"  ⚠️ 发送推送失败: {e}")


def weekly_optimization():
    """每周优化（每周一 10:00 运行）"""
    print(f"\n{'=' * 60}")
    print(f"  📊 每周优化 - {datetime.now().strftime('%Y-%m-%d %H:%M CST')}")
    print(f"{'=' * 60}")
    
    # 1. 评估当前表现
    # TODO: 实现评估逻辑
    
    # 2. 优化因子权重
    try:
        # 动态导入 factor_weight_optimizer 模块
        spec = importlib.util.spec_from_file_location("factor_weight_optimizer", CRYPTO_SIGNAL_DIR / "factor_weight_optimizer.py")
        optimizer_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(optimizer_module)
        
        history_file = CRYPTO_SIGNAL_DIR / load_config().get("system", {}).get("history_file", "paper_trade_history.json")
        optimizer = optimizer_module.FactorWeightOptimizer(history_file)
        
        # 优化权重
        new_weights = optimizer.optimize_weights(num_samples=1000)
        print(f"  ✅ 新权重: {new_weights}")
        
        # 保存权重到配置文件
        config = load_config()
        if "optimizer" not in config:
            config["optimizer"] = {}
        
        config["optimizer"]["factor_weights"] = new_weights
        config["optimizer"]["updated_at"] = datetime.now().isoformat()
        
        CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f"  ✅ 权重已保存到配置文件")
        
        # 推送通知
        send_notification(f"📊 每周优化完成\n\n新权重: {new_weights}")
    
    except Exception as e:
        print(f"  ❌ 每周优化失败: {e}")


def main():
    """主函数"""
    print("🚀 调度器启动...")
    print("  - 每小时更新数据")
    print("  - 每日报告: 10:00 / 22:00 CST")
    print("  - 每周优化: 每周一 10:00 CST")
    
    # 调度器设置
    schedule.every().hour.do(hourly_update)
    schedule.every().day.at("10:00").do(daily_report)
    schedule.every().day.at("22:00").do(daily_report)
    schedule.every().monday.at("10:00").do(weekly_optimization)
    
    print(f"\n  ✅ 调度器已启动，等待任务执行...")
    
    # 主循环
    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分钟检查一次


if __name__ == "__main__":
    main()
