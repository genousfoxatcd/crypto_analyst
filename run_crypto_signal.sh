#!/bin/bash
# run_crypto_signal.sh — 自动检测代理并运行信号系统
# 使用方法：
#   ./run_crypto_signal.sh              # 自动检测代理
#   ./run_crypto_signal.sh --no-proxy  # 不用代理直接运行

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/crypto-signal"

# 默认使用 venv
PYTHON="$SCRIPT_DIR/venv/bin/python3"
if [ ! -f "$PYTHON" ]; then
    PYTHON=python3
    echo "⚠️  venv 未找到，使用系统 python3"
fi

NO_PROXY="$1"

# 自动检测常见代理端口
detect_proxy() {
    for port in 7890 7891 1087 8080; do
        if curl -s --max-time 3 --proxy "http://127.0.0.1:$port" "https://api.binance.com/api/v3/ping" &>/dev/null; then
            echo "✅ 检测到代理: 127.0.0.1:$port"
            export http_proxy="http://127.0.0.1:$port"
            export https_proxy="http://127.0.0.1:$port"
            export HTTP_PROXY="$http_proxy"
            export HTTPS_PROXY="$https_proxy"
            return 0
        fi
    done
    echo "⚠️  未检测到代理，尝试直连..."
    return 1
}

if [ "$NO_PROXY" != "--no-proxy" ]; then
    detect_proxy || true
fi

echo ""
echo "🚀 启动信号分析系统..."
echo "   Python: $PYTHON"
echo "   工作目录: $(pwd)"
echo ""

$PYTHON crypto_signal_v2.py

echo ""
echo "✅ 完成！报告已生成到 reports/ 目录"
ls -t ../reports/crypto_signal_v2_*.html 2>/dev/null | head -3
