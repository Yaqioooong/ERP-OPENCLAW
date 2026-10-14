---
name: procurement-analysis
description: >
  采购分析主技能包。串联完整分析流程：需求拆解 → 数据收集 → 执行分析 → 生成图表 → 输出报告。
  当需要进行供应商对比、价格分析、物料筛选、采购策略评估时加载此技能。
---

# 采购分析技能（主流程）

## 适用场景
- 供应商比价分析（多供应商、同物料价格对比）
- 物料行情评估（价格趋势、供需分析）
- 供应商综合评分（按信用评级、价格、交期加权打分）
- 采购策略建议（分阶段采购、集中采购、备选供应商推荐）
- 生成含图表的采购分析报告

## 分析流程

### 阶段 1：数据收集
1. 调用 MCP 业务工具获取 ERP 数据：
   - `supplier_query(name="供应商名")` → 获取供应商列表
   - `part_query(name="物料名")` 或 `part_search(name="物料名")` → 获取零部件
   - `part_by_supplier(supplier_id=N)` → 按供应商查零部件
   - `order_search_details(partName="xxx")` → 查历史订单价格
   - `inventory_warning()` → 查库存预警
2. **获取外部数据**（不可跳过）：
   a. 激活 `/skills/procurement/supplier-price-urls/` 读取 `data/url_mapping.yaml`
   b. 按 MCP 返回的零部件名 + 供应商名查找对应报价 URL
   c. **找到 URL** → 激活爬虫技能（如 `web-content-fetcher`）获取最新报价
   d. **未找到 URL** → 使用 `web_search` 搜索同类型零部件规格参数和市场价格
   e. 无论哪种路径，最终都要有外部参考数据用于对比分析

### 阶段 2：执行分析
1. 编写 Python 分析脚本，使用 `execute` 工具运行
2. 如需安装依赖：`pip install pandas matplotlib numpy`
3. 分析维度示例：
   - 价格对比：最低价、均价、最高价、价差百分比
   - 供应商评估：信用评级 + 价格 + 交期加权
   - 物料质量：规格参数对比
   - 综合排名：多维度加权评分
4. 分析结果写入 `/data/analysis_result.json`（Python 脚本输出，供后续阶段引用）

### 阶段 3：生成图表
1. 用 `read_file` 读取 `/data/analysis_result.json`
2. 根据分析类型选择 chart_type（参考下方速查表）
3. 调用 `generate_visualization(chart_type="xxx", config={...})`
4. 聚焦 2-4 个最有洞察力的图表，不必每种都生成

### 阶段 4：生成报告
1. 汇总分析结论和图表 URL
2. 报告写入 `/analysis/report_{timestamp}.md`
3. 只向主 Agent 返回：报告路径 + 摘要 + 结论 + 建议

## 图表类型速查

| 分析目的 | chart_type | 说明 |
|----------|------------|------|
| 供应商价格横向对比 | bar | 各供应商对同一物料报价 |
| 物料采购量/金额对比 | column | 按物料/供应商的柱状对比 |
| 价格/订单量趋势 | line | 时间序列趋势 |
| 采购金额占比 | pie | 按分类或供应商的饼图 |
| 供应商多维度评分 | radar | 价格/质量/交期/服务多轴 |
| 价格分布 | histogram | 价格区间分布直方图 |
| 采购金额层级 | treemap | 分类→物料层级占比 |
| 供应商-物料关系 | network_graph | 供应关系拓扑图 |

## 分析资源

### 脚本模板
- `scripts/price_compare.py` — 价格对比分析
- `scripts/supplier_scoring.py` — 供应商综合评分

### 报告模板
```markdown
# 采购分析报告

## 1. 分析概述
（目的、数据范围、方法说明）

## 2. 数据概览
（查询到的供应商数量、物料数量、数据时间范围）

## 3. 对比分析
（核心对比结果，附图表引用）

## 4. 结论与建议
（3-5 条可操作的采购建议）
```

## 关键原则
- **技能发现**：每次任务开始先用 `ls /skills/procurement/` 扫描可用技能，不依赖静态列表
- **数据真实性**：所有结论基于真实数据，不编造
- **信任框架**：所有工具返回结果由 DeepAgents 自动 offload（>20k tokens）和 Summarization（85% 阈值），无需手动管理
- **只写最终产物**：只有 Python 分析结果（`/data/analysis_result.json`）和最终报告（`/analysis/report_*.md`）需要显式 `write_file`
- **精简输出**：报告不超过 500 字摘要 + 3-5 条结论

## 环境依赖
- Python 3.11+
- pandas, matplotlib, numpy（如未安装：`pip install pandas matplotlib numpy`）
