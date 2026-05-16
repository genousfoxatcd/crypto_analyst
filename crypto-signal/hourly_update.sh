#!/bin/bash
# hourly_update.sh — 每小时模拟持仓更新
# 1. 运行 paper_trader cmd_update
# 2. 用 multi-agent mini 生成简洁 digest 摘要
# 3. 打印摘要（可重定向或推送到其他渠道）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MA_DISPATCH="/Users/alex/.claude/multi_agent/ma_dispatch.py"
POSITIONS_FILE="$SCRIPT_DIR/paper_positions.json"

# Step 1: 执行 P&L 更新
echo "── [$(date '+%H:%M CST')] paper_trader update ──"
python3 "$SCRIPT_DIR/paper_trader.py" update

# Step 2: 读取持仓快照，通过 mini 生成 digest
if [ -f "$POSITIONS_FILE" ]; then
    POSITIONS_JSON=$(python3 -c "
import json, sys
d = json.load(open('$POSITIONS_FILE'))
out = {
    'account': {
        'initial': d['initial_capital'],
        'current': d['current_capital'],
        'pnl': round(d['current_capital'] - d['initial_capital'], 2),
        'realized_pnl': d.get('realized_pnl', 0),
        'drawdown_pct': d.get('current_drawdown_pct', 0),
        'risk_mode': d.get('risk_mode', False),
    },
    'positions': [
        {k: v for k, v in p.items()
         if k in ['coin','direction','status','limit_price','entry_price','pnl_usd','pnl_pct','leverage']}
        for p in d.get('positions', [])
    ]
}
print(json.dumps(out, ensure_ascii=False))
")

    DIGEST=$(python3 "$MA_DISPATCH" --model mini --quiet \
      "格式化持仓摘要" \
      "根据以下模拟合约账户持仓数据，生成一段简洁中文摘要。格式：账户总览一行（初始/当前/总PnL/回撤），然后每个持仓一行（币种 方向 状态 入场价 浮盈亏）。不需要解释，只输出表格文本。数据：$POSITIONS_JSON" \
      2>/dev/null || echo "[mini unavailable — raw data above]")

    echo ""
    echo "── mini digest ──"
    echo "$DIGEST"
fi
