#!/bin/bash
# ==============================================================
# git_auto_sync.sh — Super Mario 自动 Git 同步脚本
# 
# 检测核心脚本变更 → 自动提交 + 打tag + 推送到 GitHub
# 由自动化任务或手动触发
# ==============================================================

PROJECT_DIR="/Users/alex/projects/crypto_analyst"
VERSION_FILE="${PROJECT_DIR}/VERSION"
CORE_PATTERNS=(
  "crypto-signal/crypto_signal_v2.py"
  "crypto-signal/paper_trader.py"
  "crypto-signal/grid_bot_sim.py"
  "crypto-signal/report_generator_v2.py"
  "crypto-signal/macro_strategy.py"
  "crypto-signal/bayesian_signal_generator.py"
  "crypto-signal/price_fetcher.py"
  "crypto-signal/audit_data_usage.py"
  "crypto-signal/report_auditor.py"
  "crypto-signal/openclue_analyst.py"
  "crypto-signal/data_archiver.py"
  "crypto-signal/daemon.py"
  "crypto-signal/scheduler.py"
  "AGENTS.md"
  "VERSIONING.md"
  "scripts/*.sh"
  "run_signal_daily.sh"
  "run_crypto_signal.sh"
  "run_grid_bot_daily.sh"
  "run_grid_bot_weekly.sh"
  "run_weekly_review.sh"
)

cd "$PROJECT_DIR" || exit 1

# 确保在 main 分支
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
  echo "⚠️  当前不在 main 分支 ($CURRENT_BRANCH)，跳过自动推送"
  exit 0
fi

# 检查远程仓库是否已配置
REMOTE=$(git remote get-url origin 2>/dev/null)
if [ -z "$REMOTE" ]; then
  echo "❌ GitHub 远程仓库未配置，跳过自动推送"
  echo "   请先运行: git remote add origin git@github.com:<你的用户名>/crypto_analyst.git"
  exit 0
fi

# 检查是否有核心脚本变更
HAS_CHANGES=0
CHANGED_FILES=""
for pattern in "${CORE_PATTERNS[@]}"; do
  # 检测已跟踪文件的修改
  MODIFIED=$(git diff --name-only -- "$pattern" 2>/dev/null)
  if [ -n "$MODIFIED" ]; then
    HAS_CHANGES=1
    CHANGED_FILES="$CHANGED_FILES $MODIFIED"
  fi
  # 检测新文件
  UNTRACKED=$(git ls-files --others --exclude-standard -- "$pattern" 2>/dev/null)
  if [ -n "$UNTRACKED" ]; then
    HAS_CHANGES=1
    CHANGED_FILES="$CHANGED_FILES $UNTRACKED"
  fi
done

if [ "$HAS_CHANGES" -eq 0 ]; then
  echo "✅ 无核心脚本变更，跳过提交"
  exit 0
fi

echo "📦 检测到核心脚本变更:"
echo "$CHANGED_FILES" | tr ' ' '\n' | sed 's/^/  - /'

# 读取当前版本号
if [ -f "$VERSION_FILE" ]; then
  CURRENT_VERSION=$(cat "$VERSION_FILE")
else
  CURRENT_VERSION="v2.2.0"
fi

# 递增补丁版本
BASE_VERSION="${CURRENT_VERSION#v}"
IFS='.' read -r MAJOR MINOR PATCH <<< "$BASE_VERSION"
NEW_PATCH=$((PATCH + 1))
NEW_VERSION="v${MAJOR}.${MINOR}.${NEW_PATCH}"

echo "🔖 版本: $CURRENT_VERSION → $NEW_VERSION"

# 生成 commit message
COMMIT_MSG="chore(ops): 自动同步核心脚本更新 ${NEW_VERSION}"

# Stash 非核心变更以避免干扰
git add -A
git stash --include-untracked -m "non-core-auto-stash-$(date +%s)" 2>/dev/null

# 只添加核心文件
for pattern in "${CORE_PATTERNS[@]}"; do
  git add -- "$pattern" 2>/dev/null
done

# 版本文件
echo "$NEW_VERSION" > "$VERSION_FILE"
git add "$VERSION_FILE"

# 提交
if git commit -m "$COMMIT_MSG" -m "自动检测到脚本变更

变更文件:
$(echo "$CHANGED_FILES" | tr ' ' '\n' | sed 's/^/  - /')"; then
  echo "✅ 提交成功: $COMMIT_MSG"

  # 打tag
  if git tag "$NEW_VERSION" -m "Release $NEW_VERSION"; then
    echo "✅ Tag 创建成功: $NEW_VERSION"
  fi

  # 推送
  echo "🔄 推送到 GitHub..."
  if git push origin main --tags 2>&1; then
    echo "✅ GitHub 推送成功"
  else
    echo "⚠️  GitHub 推送失败，请检查网络或权限"
    echo "    SSH Key 是否已添加到 GitHub？"
    echo "    测试: ssh -T git@github.com"
  fi
else
  echo "ℹ️  无新增变更跳过提交"
fi

# 恢复 stash
git stash pop 2>/dev/null || true

echo "✅ Git 自动同步完成"
exit 0
