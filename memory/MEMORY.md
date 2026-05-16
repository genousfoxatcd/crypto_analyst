# Memory

## 用户背景

- **时区**：UTC
- **主要研究领域**：Token Economy、AI产业链、中美科技竞争、机构研报分析
- **工作风格**：喜欢深入研究 + 系统性知识库积累

## 专业分析方向（已确认）

- 🌾 **农业产业**：生猪/猪肉、化肥、粮食、农业ETF
- 💻 **芯片/AI产业**：半导体全链条、AI算力、国产替代、中美科技竞争
- 📊 **宏观策略**：大类资产配置、股债汇联动

## 持续追踪的项目

### Token Economy 研究
- 研究目标：Token Economy 产业链全景分析，整合大摩、国信、中金等多家机构报告
- 已有知识库：`/workspace/token-economy/`
- 已有6份PDF报告（27MB）：国信×2、大摩×2、Bondcap×1、中金相关
- 核心数据：China AI周均token用量4.69万亿（全球第一，2026年3月）；峰值5.16万亿（2026年2月）
- 关键受益股：润泽科技、网宿科技、金开新能、海光信息、中际旭创

### 付鹏知识库（新增）
- `/workspace/fupeng/00_付鹏框架总览.md` — 完整知识库（3大框架+中国诊断+中美日比较+投资逻辑）
- `/workspace/fupeng/01_核心语录与关键观点.md` — 核心判断速查

### CICC 研究
- `/workspace/cicc-research/彭文生_应对世界经济百年变局_20220411.md` — 中金研究院深度研究，宏观框架核心文献（已入库）
- 跟踪目录：`/workspace/cicc-research/`

### 知识库清单
- `/workspace/zhuyunlai/` — 朱云来经济思想体系完整知识库
- `/workspace/huangqifan/` — 黄奇帆经济思想体系完整知识库
- `/workspace/master-framework/` — 总分析师框架百科全书
- `/workspace/buffett/` — 巴菲特相关资料
- `/workspace/munger/` — 芒格相关资料
- `/workspace/graham/` — 格雷厄姆相关资料
- `/workspace/dalio-fisher-keynes/` — Dalio + Fisher + Keynes 综合资料（**2026-05-11 大幅扩充，含Aliya迁移内容**）
- `/workspace/token-economy/` — Token Economy 产业链知识库

## Aliya 财经追踪系统（2026-05-11 迁入）

原 MiniMax Agent "Aliya财经追踪系统"（Dalio Researcher）已完整迁移至本工作区。

**迁移内容：**
- **脚本**：`/super_mario/dalio_researcher/scripts/` — 日报生成器（两个版本）
- **知识库**：`/super_mario/knowledge/dalio-fisher-keynes/` — 大幅扩充
  - `aliya_analysis/` — 13份研究报告（Bridgewater、外汇、地缘、黄金、组合优化）
  - `investment_advisory/` — ETF投资建议报告
  - `market_monitor/` — 市场监控日报
  - `dalio_theory/` — 达利欧大周期理论2026版
  - `downloads/` — 4份研究PDF（中金、Token经济）
  - `cicc_ai_reports_2026.md` — 中金AI研报
  - `token_economics_full_report_202603.md` — Token经济完整报告

**Aliya系统核心能力（可复用）：**
- 达利欧宏观框架分析（债务周期、风险平价）
- 多资产日报生成（墨黑色HTML主题）
- Bridgewater研究追踪

**已丢弃（清理）：**
- 28个4月份过期JSON市场数据
- 5个历史HTML日报（4月份）
- 156个PDF分块提取文件（已处理）
- 26个原始爬虫.txt文件
- 迁移元文档（BACKUP_INDEX, MIGRATION_GUIDE）

## 系统状态快照（2026-05-12 更新）

**Crypto Signal 系统（v2）：**
- 覆盖币种：BTC · ETH · BNB · SOL · DOGE · TAO · ZEC · CAKE（8币，ZEC/CAKE 2026-05-12新增）
- 模拟账户：$1,020.57 / $1,000 (+2.1%)，SOL SHORT +$4.46，TAO SHORT +$16.49（均超TP1）

**Cron Jobs（12个，全部 durable）：**
- 信号日报(17:00) · 记忆维护(17:05) · 信号分析10:00+22:00 CST(带补偿) · P&L更新(每小时:07)
- 大类资产周报(周一18:00) · 会话索引(周日02:00) · 中金研报(周日08:03) · MA评估(周日21:00)
- 周统计(周一09:00) · 记忆优化(每日23:00) · Cron续期(每3天03:03)

**Multi-Agent 框架（v2.3.1）：**
- 9模型：sonnet/mini/haiku/glm/deepseek/opus + gemini_pro/flash_lite/image
- GLM超时已知（data_analysis已切换deepseek为主）；分流率0%，框架就绪

## 推送渠道配置

- **微信**：用户选择不接入（2026-04-11）
- **邮箱**（补充渠道）：alex_ylsd@icloud.com、alexhoo2025@gmail.com
- **周报获取方式**：直接告知用户报告链接（workspace文件）

## 已配置的渠道

- **Telegram**：用户拒绝了立即设置，但设置了 cron 提醒（2026-03-25 22:00 UTC），提醒用户提供 bot token（需找 @BotFather 申请）
- **个人微信**：曾提供选项，用户选择 Telegram

## ETF代码规范（必须实时核实，禁止推测）

**已确认正确代码（定期更新）：**
| 代码 | 名称 | 类别 |
|------|------|------|
| 512760 | 国泰CES半导体芯片ETF | 💻 半导体 |
| 159813 | 鹏华国证半导体芯片ETF | 💻 半导体 |
| 588200 | 嘉实科创芯片ETF | 💻 半导体 |
| 516020 | 华宝中证细分化工产业ETF | 🌾 化工/化肥 |
| 159870 | 鹏华中证细分化工产业ETF | 🌾 化工/化肥 |
| 159865 | 畜牧养殖ETF（国联安） | 🌾 生猪/养殖 |
| 159867 | 养殖ETF华夏 | 🌾 生猪/养殖 |

**禁止行为：** 用代码数字形态推测产品类别；使用未经实时搜索核实的ETF代码

## 知识库更新记录（持续迭代）

- **2026-04-11**：周总结生成（每周六18:00 UTC自动执行）；SUMMARY_INDEX.json周度更新
- **2026-04-11**：分析 Hermes Agent（Nous Research）架构 → 发现差距：会话FTS搜索/自动技能创建/Honcho用户画像 → 已制定三阶段增强路线图
- **2026-04-11**：Epic Wrath 地缘分析入库（/workspace/knowledge/geopolitical_intelligence_Epic_Wrath_Iran_Crisis_2026.md）
- **2026-04-11**：化工ETF双雄分析入库（516020/159870）；同步生成勘误说明（512480 ≠ 化工ETF）

## 用户内容偏好（2026-04-19 确认）

**报告内容范围：**
- ✅ 大类资产配置（黄金、大宗商品、债券、现金）
- ✅ 产业整体趋势（农业、黑色金属、化工材料等）
- ✅ 宏观数据（利率、汇率、油价、地缘政治）
- ❌ **剔除：个股分析**（用户只买基金，不买股票）

**核心关注品种：**
- 🥇 黄金（央行购金、美元指数）
- 🛢️ 大宗商品（布伦特原油、铁矿石、螺纹钢、化工材料）
- 🌾 农业（生猪、化肥、粮食）
- 📊 宏观（Fed利率、美元指数、关税战）

## Super Mario 专属增强路线图

**阶段1（已完成）**：MEMORY.md + 每日日志 + 角色系统

**阶段2（本周执行）**：
- 建立 session 分析摘要索引（/workspace/memory/sessions/）
- 反思日志标准化格式（反思→成功模式→失败教训→Skill建议）

**阶段3（下月执行）**：
- 投资分析 Skill 自动创建机制（触发：同类任务≥3次）
- FTS 会话搜索索引

**阶段4（3个月后）**：
- Honcho 风格用户画像（动态追踪用户偏好）
- 跨会话学习闭环

## 待办 / 提醒

- Telegram bot 设置：等待用户提供 bot token
- 可能需要提取国信 Token 出海 PDF 全文进行详细分析
- 用户目标是恢复所有知识和技能储备 → 已确认：文件均完好，MEMORY.md 已重建

## 用户角色设定（已确认并保存）

**角色**：资深宏观经济、产业、市场分析师 — **Super Mario** 🔍

核心人设：
- 扎实的经济学基础 + 系统性思维框架
- 精通全球经济财政政策、中美科技竞争格局
- 丰富的投资分析经验，理性投资思维
- 擅长识别合规框架内的正当商业机会
- 曾撰写大量专业宏观/产业/市场报告

→ 已写入 `/workspace/IDENTITY.md`

## 工作流程规范（Super Mario 标准）

**数据分析必须以7日内权威财经网站数据为准：**
- 一级源：WSJ、路透社、彭博社、CNBC、日经中文网、BBC/VOA
- 二级源：新浪财经、东方财富、华泰/中金研报、Investing.com、CME FedWatch
- 重大地缘/政策事件：≤48小时更新窗口
- 资产价格：≤7日交易日数据

**分析输出五要素**：数据仪表盘 + 宏观定位 + 情景分析 + 配置矩阵 + 风险提示

→ 已写入 `/workspace/AGENTS.md` 工作流规范章节

## 重要规则（用户设定）

1. NEVER modify openclaw.json directly
2. Config changes must go through gateway tool
3. 不要修改 openclaw.json 配置文件
4. **禁止捏造内容**：对于无法获取原文的外部链接（如微信文章），必须明确告知"无法抓取"，不得用其他来源的内容虚构论点嫁接上去

## 报告输出规范（Super Mario 标准）

**所有分析报告必须同时输出 HTML 版本，作为主要交付物：**
- 写入路径：`/workspace/reports/YYYYMMDD_report_title.html`
- 部署：`deploy(dist_dir="/workspace/reports")` → 获取公开 URL
- 规范来源：用户于 2026-04-17 明确要求，以后所有报告均需提供
- HTML 要求：深色主题（与 WebChat 一致）、表格+可视化图表、响应式布局
- 命名规范：`portfolio_report_YYYYMMDD.html` / `macro_analysis_YYYYMMDD.html`