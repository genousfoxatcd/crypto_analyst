#!/bin/bash
# run_grid_bot_daily.sh —— 网格合约机器人每日更新
# 用法: ./run_grid_bot_daily.sh
# 自动化: 每日 12:00 调用

set -e
WS="$(cd "$(dirname "$0")" && pwd)"
CS="$WS/crypto-signal"
PYTHON="$WS/venv/bin/python3"
[ ! -f "$PYTHON" ] && PYTHON=/opt/homebrew/bin/python3
DATE=$(date +%Y%m%d)

cd "$CS"
echo "🤖 网格机器人每日更新 [$(date '+%Y-%m-%d %H:%M')]"

# Step 1: 价格归档
echo "  [1/4] 归档当前价格..."
$PYTHON grid_bot_sim.py archive 2>&1 | tail -3

# Step 2: 更新机器人状态
echo "  [2/4] 更新机器人状态..."
$PYTHON grid_bot_sim.py update 2>&1 | tail -5

# Step 3: 状态快照
echo "  [3/4] 状态快照..."
$PYTHON grid_bot_sim.py status 2>&1 | tail -8

# Step 4: 生成日报
echo "  [4/4] 生成日报..."
$PYTHON grid_bot_sim.py report 2>&1 | tail -3

HTML="$WS/reports/grid_bot_daily_${DATE}.html"
if [ -f "$HTML" ]; then
    echo "  ✅ 日报: grid_bot_daily_${DATE}.html"
fi

echo "✅ 网格机器人每日更新完成"
