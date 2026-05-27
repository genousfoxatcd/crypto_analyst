#!/bin/bash
# run_signal_daily.sh —— 加密货币每日信号全流程
# 用法: ./run_signal_daily.sh
# 自动化调用此脚本即可，无需LLM介入分析

set -e
WS="$(cd "$(dirname "$0")" && pwd)"
CS="$WS/crypto-signal"
PYTHON="$WS/venv/bin/python3"
[ ! -f "$PYTHON" ] && PYTHON=/opt/homebrew/bin/python3

cd "$CS"
echo "🚀 加密货币每日信号 [$(date '+%Y-%m-%d %H:%M')]"

# Step 1: 运行信号引擎
echo "  [1/3] 运行信号引擎..."
$PYTHON crypto_signal_v2.py 2>&1 | tail -5

# Step 2: 更新模拟持仓
echo "  [2/3] 更新模拟持仓..."
$PYTHON paper_trader.py update 2>&1 | tail -5

# Step 3: 生成HTML报告并打开
echo "  [3/3] 生成报告..."
HTML=$(ls -t "$WS/reports/crypto_signal_v2_"*.html 2>/dev/null | head -1)
if [ -n "$HTML" ]; then
    open "$HTML"
    echo "  ✅ 报告: $(basename "$HTML")"
else
    echo "  ⚠️ 未找到HTML报告"
fi

echo "✅ 每日信号流程完成"
