# crypto-signal

加密货币信号分析与模拟合约交易系统。信号引擎多源采集价格和合约数据，生成交易建议；模拟交易器据此挂单并追踪盈亏。两套系统通过双向反馈回路互相驱动迭代。

## Relationships

- **Signal Engine → Paper Trader**：信号机生成 Signal，Paper Trader 据此挂单、追踪成交和盈亏
- **Paper Trader → Signal Engine**：交易结果（成交率、TP/SL 命中率、实际 PnL、Factor Breakdown 关联）反向输入信号机，每周驱动 Signal Weights 更新
- **Signal Engine（自迭代）**：根据 Signal Accuracy 和因子贡献分析，每周调整 Signal Weights
- **Paper Trader（自迭代）**：`cmd_optimize()` 根据历史胜率调整杠杆系数和最低 prob_score 门槛

## Language

**Signal（信号）**：
信号引擎在某一时刻对某个币种生成的交易建议快照，包含方向（direction）、入场区间（entry_zone）、止盈位（tp1/tp2）、止损位（sl）和概率得分（prob_score）。有效期到下一次信号生成为止。
_Avoid_: 预测、信号值、分析结果

**Signal Accuracy（信号准确率）**：
衡量信号预测质量的标准。正确 = 持仓触达 TP1；错误 = 触发 SL；无效 = 挂单 TTL 内未成交（不纳入统计）；未决 = 持仓超期未平，按浮盈方向判定（盈利算正确，亏损算错误）。
_Avoid_: 命中率；注意"胜率"专指已平仓交易中盈利比例，准确率额外过滤掉无效挂单

**Signal Archive（信号档案）**：
每次信号生成后保存的带时间戳快照，路径 `signal_history/signals_YYYYMMDD_HH.json`，包含完整信号数据和 Factor Breakdown。Paper Trader 开单时记录对应的 `signal_snapshot_id`，供周度反馈分析关联。
_Avoid_: 信号日志、历史信号

**Factor Breakdown（因子明细）**：
信号生成时各打分因子的得分贡献快照，格式如 `{"bb_score": +25, "funding_score": +20, "ls_score": +15, ...}`。必须实时存档，不可事后重算——权重调整后历史数据无法用新权重反推旧贡献。
_Avoid_: 因子分数、打分明细

**Signal Weights（信号权重）**：
各打分因子的得分参数，存于 `signal_weights.json`，信号机每次运行时读取。每次周度优化修改前自动备份为 `signal_weights_YYYYMMDD.json`，保留完整版本历史以支持回滚。
_Avoid_: 权重配置、打分参数

**Feedback Loop（反馈回路）**：
Paper Trader 向信号机提供两个维度的优化依据：（1）币种维度 — 每个币每个方向的历史胜率和平均 PnL；（2）因子维度 — 各因子在盈利交易 vs 亏损交易中的 Factor Breakdown 分布差异。两者共同驱动 Signal Weights 每周迭代。
_Avoid_: 数据回传、优化输入

**Optimization Guardrails（优化护栏）**：
周度优化的调整约束：单因子调整幅度 ≤±20%；每个因子保留最低绝对值防止被归零；某币/某方向已平仓交易 <5 笔时跳过该维度优化（样本不足）。
_Avoid_: 调整限制、安全边界

**Optimization Report（优化报告）**：
周度反馈优化后生成的审计报告，包含：本周 Signal Accuracy、各因子贡献分析、Signal Weights 变化明细及调整理由。任一因子调整幅度 >30% 或整体胜率下降时输出告警。路径：`reports/optimization_YYYYMMDD.md`。
_Avoid_: 优化日志、权重报告

## Example dialogue

> **Dev**: "这笔 TAO SHORT 的信号准确吗？"
> **Domain**: "它还是 PENDING，没成交，属于无效，不纳入 Signal Accuracy 统计。"

> **Dev**: "本周优化要不要把资金费率的权重砍掉一半？"
> **Domain**: "Optimization Guardrails 限制单次调整 ≤±20%，而且样本 <5 笔就跳过，先看 Optimization Report 的建议再决定。"

## Flagged ambiguities

- "胜率" 在不同上下文含义不同：Paper Trader 的 `cmd_optimize()` 里指已平仓盈利比例；Signal Accuracy 额外排除无效挂单 — 两者不可混用
- "优化" 同时指 Paper Trader 的 `cmd_optimize()`（调整杠杆/门槛）和信号机的 Signal Weights 更新 — 上下文不明时需指定是哪套系统的优化
