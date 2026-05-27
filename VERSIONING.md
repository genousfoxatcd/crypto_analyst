# VERSIONING.md — Super Mario 版本管理计划

> 定义项目版本规则、Git工作流、自动提交机制

---

## 一、版本号规则

采用 **SemVer 2.0**（语义化版本）：

```
v主版本.次版本.补丁版本
  ↑      ↑       ↑
 重大   功能     Bugfix/
 重构   新增     微调

示例: v2.2.0 → 补丁修复 → v2.2.1
      v2.2.0 → 新增功能 → v2.3.0
      v2.2.0 → 重大重构 → v3.0.0
```

### 版本对应关系

| 层级 | Git操作 | 触发条件 |
|------|---------|---------|
| **主版本** | `git tag vN.0.0` | 架构重构、数据体系变更、API不兼容 |
| **次版本** | `git tag vX.N.0` | 新币种接入、新策略模块、新报告类型 |
| **补丁** | `git tag vX.X.N` | Bugfix、参数调优、文档更新、自动化配置变更 |

---

## 二、Git分支策略

```
main ───●────────────●──────────●──── (稳定版)
         \          / \        /
feature1  ●──●──●──    ●──●──●
                          feature2
```

| 分支 | 用途 | 保护 |
|------|------|------|
| `main` | 生产稳定版，自动化任务从此分支运行 | ✅ 保护 |
| `dev` | 开发集成，日常修改合并到此 | 可选 |
| `feature/*` | 新功能开发，如 `feature/bnb-grid` | 临时 |
| `fix/*` | Bug修复，如 `fix/price-stale-check` | 临时 |
| `release/*` | 发布准备，版本号确认 | 临时（可选） |

---

## 三、自动提交规则

### 触发场景

核心脚本变更 → 自动检测 → 提交 + 打tag + 推送

### 核心脚本列表（变更即触发自动提交）

| 路径 | 说明 | 重要度 |
|------|------|--------|
| `crypto-signal/crypto_signal_v2.py` | 信号引擎 | 🔴 P0 |
| `crypto-signal/paper_trader.py` | 模拟交易 | 🔴 P0 |
| `crypto-signal/grid_bot_sim.py` | 网格合约机器人 | 🔴 P0 |
| `crypto-signal/report_generator_v2.py` | HTML报告生成 | 🟡 P1 |
| `crypto-signal/macro_strategy.py` | 宏观策略 | 🟡 P1 |
| `crypto-signal/bayesian_signal_generator.py` | 贝叶斯优化 | 🟡 P1 |
| `crypto-signal/price_fetcher.py` | 价格采集 | 🟡 P1 |
| `crypto-signal/audit_data_usage.py` | 数据审计 | 🟡 P1 |
| `crypto-signal/report_auditor.py` | 报告审核 | 🟡 P1 |
| `crypto-signal/openclue_analyst.py` | OpenClue分析 | 🟢 P2 |
| `crypto-signal/data_archiver.py` | 数据归档 | 🟢 P2 |
| `crypto-signal/daemon.py` | 守护进程 | 🟢 P2 |
| `crypto-signal/scheduler.py` | 调度器 | 🟢 P2 |
| `AGENTS.md` | 项目规范 | 🟢 P2 |
| `VERSIONING.md` | 版本管理 | 🟢 P2 |
| `run_signal_daily.sh` | 信号运行脚本 | 🟡 P1 |
| `run_grid_bot_daily.sh` | 网格日更脚本 | 🟡 P1 |
| `run_grid_bot_weekly.sh` | 网格周报脚本 | 🟢 P2 |
| `run_weekly_review.sh` | 周度复盘脚本 | 🟢 P2 |

### 不触发提交的内容

- `reports/*` — 自动生成的报告
- `signal_history/*` — 信号归档JSON
- `*.json` 动态数据文件（持仓、价格缓存等）
- `__pycache__/*` / `*.pyc`
- `logs/*` / `*.log`
- `memory/*` — 工作笔记
- `.workbuddy/*` — AI配置

### 自动git同步脚本

参见 `scripts/git_auto_sync.sh`，检测到核心脚本变更时：
1. `git add` 变更文件
2. `git commit -m "类型(模块): 简短描述 vX.X.X"`
3. `git tag vX.X.X`（补丁+1）
4. `git push origin main --tags`

---

## 四、Commit Message规范

```
<类型>(<模块>): <描述>

类型: feat | fix | refactor | chore | docs | config | perf
模块: signal | trader | grid | report | macro | bayes | ops | infra
```

**示例**:
```
feat(signal): 新增HYPE币种信号支持 v2.3.0
fix(trader): 修复资金费率结算四舍五入错误 v2.2.1
refactor(grid): 重构网格配对率算法 v2.4.0
config(infra): 添加22:00信号自动化任务 v2.2.1
docs(ops): 更新VERSIONING.md分支策略 v2.2.1
```

---

## 五、版本标签策略

| Tag格式 | 用例 | 示例 |
|---------|------|------|
| `vX.X.X` | 标准版本 | `v2.2.0` |
| `vX.X.X-rc.N` | 发布候选 | `v2.3.0-rc.1` |

每次自动提交自动递增 **补丁版本**。
功能修改或架构变更手动调整 **次/主版本** 后重置补丁为0。

---

## 六、快速命令

```bash
# 手动提交并打tag
bash scripts/git_auto_sync.sh

# 查看当前版本
git tag --sort=-version:refname | head -1

# 查看版本历史
git log --oneline --decorate -10

# 创建发布版本
git tag v2.3.0 && git push origin v2.3.0
```

---

*VERSIONING.md v1.0 · 2026-05-27*
