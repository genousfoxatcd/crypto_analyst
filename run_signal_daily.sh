#!/bin/bash
# run_signal_daily.sh —— 加密货币每日信号全流程
# 用法: ./run_signal_daily.sh
# 自动化调用此脚本即可，无需LLM介入分析
# 注意: 不使用 set -e，每个步骤独立容错 + 自动重试

WS="$(cd "$(dirname "$0")" && pwd)"
CS="$WS/crypto-signal"
PYTHON="$WS/venv/bin/python3"
[ ! -f "$PYTHON" ] && PYTHON=/opt/homebrew/bin/python3

cd "$CS"
echo "🚀 加密货币每日信号 [$(date '+%Y-%m-%d %H:%M')]"
EXIT_CODE=0

# ── 辅助函数: 带重试的执行 ──
retry_cmd() {
    local label="$1" max_retries="$2"; shift 2
    local attempt=0 rc=1
    while [ $attempt -lt $max_retries ]; do
        attempt=$((attempt+1))
        echo "  [$label] 尝试 #$attempt..."
        if "$@" 2>&1; then
            echo "  [$label] ✅ 成功 (尝试 #$attempt)"
            return 0
        fi
        rc=$?
        [ $attempt -lt $max_retries ] && echo "  [$label] ⚠️ 失败(rc=$rc)，5秒后重试..." && sleep 5
    done
    echo "  [$label] 🔴 失败(rc=$rc)，已耗尽 $max_retries 次重试"
    return $rc
}

# Step 1: 运行信号引擎
echo "  [1/3] 运行信号引擎..."
retry_cmd "信号引擎" 2 $PYTHON crypto_signal_v2.py || EXIT_CODE=1

# Step 2: 更新模拟持仓（即使信号引擎失败也尝试）
echo "  [2/3] 更新模拟持仓..."
retry_cmd "更新持仓" 1 $PYTHON paper_trader.py update || EXIT_CODE=1

# Step 3: 确认HTML报告
echo "  [3/3] 确认报告..."
HTML=$(ls -t "$WS/reports/crypto_signal_v2_"*.html 2>/dev/null | head -1)
if [ -n "$HTML" ]; then
    echo "  ✅ 报告已生成: $(basename "$HTML")"
else
    echo "  ⚠️ 未找到HTML报告"
    EXIT_CODE=1
fi

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ 每日信号流程全部完成"
else
    echo "⚠️ 每日信号流程部分步骤失败(exit=$EXIT_CODE)"
fi
exit $EXIT_CODE
