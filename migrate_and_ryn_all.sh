#!/bin/bash

# -----------------------------
# 一键迁移 & 重建 & 分析 & 按依赖顺序执行 Python 脚本
# -----------------------------

SRC_DIR="/Users/alex/hermes_claud/super_mario"
DEST_DIR="$HOME/projects/crypto_analyst"
VENV_DIR="$DEST_DIR/venv"

echo "🚀 开始迁移项目..."

# 1️⃣ 创建目标目录
mkdir -p "$DEST_DIR"

# 2️⃣ 复制文件
echo "📂 复制文件中..."
cp -r "$SRC_DIR/"* "$DEST_DIR/"

# 3️⃣ 进入目标目录
cd "$DEST_DIR" || { echo "❌ 无法进入目录 $DEST_DIR"; exit 1; }

# 4️⃣ 初始化虚拟环境
echo "🐍 初始化虚拟环境..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# 5️⃣ 安装依赖
if [ -f requirements.txt ]; then
    echo "📦 安装依赖..."
    pip install --upgrade pip
    pip install -r requirements.txt
else
    echo "⚠️ 未找到 requirements.txt，跳过依赖安装"
fi

# 6️⃣ 分析项目内容
echo "🔍 分析项目数据和脚本..."
echo "🗂️ 数据文件统计（csv/json/txt）:"
find . -type f \( -name "*.csv" -o -name "*.json" -o -name "*.txt" \) -exec wc -l {} +

echo "📝 脚本文件列表 (py/js/sh):"
find . -type f \( -name "*.py" -o -name "*.js" -o -name "*.sh" \)

echo "📄 文档/知识库文件 (md/pdf):"
find . -type f \( -name "*.md" -o -name "*.pdf" \)

# 7️⃣ 初始化 Git
if [ ! -d ".git" ]; then
    echo "🔧 初始化 Git 仓库..."
    git init
    git add .
    git commit -m "Initial commit for crypto_analyst project"
fi

# 8️⃣ 按依赖顺序执行 Python 脚本
echo "🏃‍♂️ 分析 Python 脚本依赖并按顺序执行..."

# 收集所有 Python 文件
PY_FILES=()
while IFS= read -r -d $'\0' file; do
    PY_FILES+=("$file")
done < <(find . -type f -name "*.py" -print0)

# 构建依赖关系图（简单解析 import）
declare -A DEPENDENCIES

for file in "${PY_FILES[@]}"; do
    base=$(basename "$file" .py)
    deps=()
    while IFS= read -r line; do
        # 匹配 import module 或 from module import ...
        if [[ $line =~ ^[[:space:]]*import[[:space:]]+([a-zA-Z_][a-zA-Z0-9_]*) ]]; then
            deps+=("${BASH_REMATCH[1]}")
        elif [[ $line =~ ^[[:space:]]*from[[:space:]]+([a-zA-Z_][a-zA-Z0-9_]*)[[:space:]]+import ]]; then
            deps+=("${BASH_REMATCH[1]}")
        fi
    done < "$file"
    DEPENDENCIES["$base"]="${deps[*]}"
done

# 简单拓扑排序
VISITED=()
SORTED=()
declare -A TEMP_MARK

function visit() {
    local node=$1
    if [[ "${TEMP_MARK[$node]}" == "1" ]]; then
        echo "❌ 检测到循环依赖: $node"
        return
    fi
    if [[ ! " ${VISITED[*]} " =~ " $node " ]]; then
        TEMP_MARK["$node"]=1
        for dep in ${DEPENDENCIES[$node]}; do
            # 只考虑项目内的 Python 文件
            if [[ -f "${dep}.py" ]]; then
                visit "$dep"
            fi
        done
        VISITED+=("$node")
        SORTED+=("$node")
        TEMP_MARK["$node"]=0
    fi
}

# 执行拓扑排序
for file in "${PY_FILES[@]}"; do
    base=$(basename "$file" .py)
    visit "$base"
done

# 运行排序后的 Python 脚本
for script_base in "${SORTED[@]}"; do
    script_file="${script_base}.py"
    if [ -f "$script_file" ]; then
        echo "▶️ 正在运行: $script_file"
        python "$script_file"
        if [ $? -ne 0 ]; then
            echo "❌ 脚本 $script_file 执行出错，继续执行下一脚本"
        fi
    fi
done

echo "✅ 项目迁移、分析与依赖顺序执行完成"
