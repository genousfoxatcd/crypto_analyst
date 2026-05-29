#!/bin/bash
# run_grid_bot_weekly.sh —— 网格合约机器人周度分析
# 用法: ./run_grid_bot_weekly.sh
# 自动化: 周二 10:15 调用

set -e
WS="$(cd "$(dirname "$0")" && pwd)"
CS="$WS/crypto-signal"
PYTHON="$WS/venv/bin/python3"
[ ! -f "$PYTHON" ] && PYTHON=/opt/homebrew/bin/python3
DATE=$(date +%Y%m%d)

cd "$CS"
echo "🤖 网格机器人周度分析 [$(date '+%Y-%m-%d %H:%M')]"

# Step 1: 先做每日更新（确保数据最新）
echo "  [1/3] 每日更新..."
$PYTHON grid_bot_sim.py archive 2>&1 | tail -1
$PYTHON grid_bot_sim.py update 2>&1 | tail -1

# Step 2: 周度分析
echo "  [2/3] 周度分析..."
$PYTHON grid_bot_weekly.py 2>&1 | tail -10

# Step 3: 打开报告
HTML="$WS/reports/grid_bot_weekly_${DATE}.html"
if [ -f "$HTML" ]; then
    open "$HTML"
    echo "  ✅ 周报: grid_bot_weekly_${DATE}.html"
else
    echo "  ⚠️ 未找到周报HTML"
fi

echo "✅ 网格机器人周度分析完成"
