#!/bin/bash
# run_weekly_review.sh —— 信号机周度复盘
# 用法: ./run_weekly_review.sh
# 自动化：周一 22:30 调用，读取本周数据生成复盘报告

set -e
WS="$(cd "$(dirname "$0")" && pwd)"
CS="$WS/crypto-signal"
REPORTS="$WS/reports"
PYTHON="$WS/venv/bin/python3"
[ ! -f "$PYTHON" ] && PYTHON=/opt/homebrew/bin/python3
DATE=$(date +%Y%m%d)

cd "$CS"
echo "📊 信号机周度复盘 [$(date '+%Y-%m-%d %H:%M')]"

# Step 1: 检查数据文件
HISTORY="$CS/paper_trade_history.json"
SIGNAL_DIR="$CS/signal_history"
if [ ! -f "$HISTORY" ]; then
    echo "❌ 交易历史文件不存在: $HISTORY"
    exit 1
fi
if [ ! -d "$SIGNAL_DIR" ]; then
    echo "❌ 信号历史目录不存在: $SIGNAL_DIR"
    exit 1
fi
echo "  ✅ 数据文件就绪 (信号快照: $(ls "$SIGNAL_DIR"/signals_*.json 2>/dev/null | wc -l) | 交易记录: $(python3 -c "import json;print(len(json.load(open('$HISTORY'))))"))"

# Step 2: 运行 Python 复盘分析脚本（自动生成 MD + HTML）
WEEKLY_PY="$CS/weekly_review_runner.py"
if [ -f "$WEEKLY_PY" ]; then
    echo "  [2/2] 运行复盘生成器..."
    $PYTHON "$WEEKLY_PY"
else
    echo "  [2/2] weekly_review_runner.py 不存在，跳过自动生成"
    echo "  ⚠️ 请确认复盘分析脚本已部署"
    exit 1
fi

# Step 3: 打开报告
HTML="$REPORTS/weekly_review_${DATE}.html"
MD="$REPORTS/weekly_review_${DATE}.md"
if [ -f "$HTML" ]; then
    open "$HTML"
    echo "  ✅ 报告: weekly_review_${DATE}.html"
elif [ -f "$MD" ]; then
    open "$MD"
    echo "  ✅ 报告: weekly_review_${DATE}.md"
fi

echo "✅ 周度复盘完成"
