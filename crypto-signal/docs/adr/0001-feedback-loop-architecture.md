# ADR 0001: 双向反馈回路架构（File-based, Weekly Cycle）

**日期**: 2026-05-11  
**状态**: 已确认

## 决策

信号机与模拟交易系统之间采用**文件驱动、每周触发**的双向反馈回路，而非实时数据库联动。

## 背景

需要让两套系统互相提供优化数据：信号机生成 Signal → Paper Trader 交易 → 交易结果反哺信号机调整 Signal Weights。

## 选择与权衡

| 方案 | 优点 | 缺点 |
|------|------|------|
| 文件 + 每周批处理（选定） | 零依赖、可审计、防过拟合 | 延迟一周才能生效 |
| 实时数据库联动 | 响应快 | 引入 SQLite 依赖，单笔数据噪音大，易过拟合 |
| 每日触发 | 更快收敛 | 样本量不足时调整不稳定 |

## 具体约定

- Signal Weights 存于 `signal_weights.json`，修改前备份为 `signal_weights_YYYYMMDD.json`
- 信号历史存于 `signal_history/signals_YYYYMMDD_HH.json`，包含 Factor Breakdown
- 每周日触发 `feedback_optimizer.py`，输出 Optimization Report + 更新 Signal Weights
- Optimization Guardrails：单因子 ≤±20%，样本 <5 笔跳过，调整幅度 >30% 告警
