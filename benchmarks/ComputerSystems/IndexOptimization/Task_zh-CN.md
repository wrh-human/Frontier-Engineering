# IndexOptimization - 数据库索引优化

## 概述

在索引数量和存储空间约束下，为 PostgreSQL 数据库上的分析型 SQL workload 找到高质量的 B-tree 索引配置，以优化查询执行时间。

## 输入

candidate 脚本通过 `--input` 读取任务配置，将输出写入 `--output`。输入包含：

- **查询 workload**：SQL 查询及结构化 metadata（涉及表、过滤条件、JOIN 条件）
- **数据库 schema**：表定义、列类型、行数、已有索引（主键）
- **约束**：`max_indexes` (10)、`max_storage_mb` (500)

## 输出

```json
{
  "indexes": [
    {"table": "orders", "columns": ["o_orderdate", "o_custkey"], "method": "btree"}
  ]
}
```

## 评分

| 指标 | 说明 |
|------|------|
| `baseline_time_s` | heuristic 索引下的总查询时间 |
| `candidate_time_s` | candidate 索引下的总查询时间 |
| `combined_score` | log2(speedup) × (1 - 存储惩罚 - 数量惩罚) |

## 约束

1. 只修改 `scripts/init.py`
2. 保持 CLI 接口不变（`--input` 和 `--output`）
3. 输出必须包含 `indexes` 列表
4. 每个索引需指定 table、columns 和 method
5. 最多 10 个索引
6. 索引空间不超过 500 MB
7. 索引前后所有查询结果必须一致
8. 不要推荐主键中已存在的索引

## 优化方向建议

Agent 可以尝试以下策略来找到更好的索引配置：

1. **高选择性过滤列**：对 WHERE 子句中选择性高的列（如 `o_orderdate`、`l_shipdate`）建索引，可显著缩小扫描范围
2. **JOIN 列覆盖索引**：对外键列（如 `o_custkey`、`l_orderkey`）建索引，可加速哈希连接的探测阶段
3. **避免过度索引**：未被使用的索引会产生存储和维护开销。评分公式中存储惩罚和数量惩罚共同作用——更多的索引不一定带来更高的分数
4. **识别全表扫描场景**：Q1 对大表大部分行做聚合查询，索引无法帮助此类查询。过度建索引只会增加成本
5. **多列索引**：组合索引（如 `(o_orderdate, o_custkey)`）可同时服务于多个查询子句，可能替代多个单列索引
6. **分析 Q1 的特性**：Q1 对 `lineitem` 做全表扫描，索引对此查询无帮助但消耗存储。评分公式考虑了存储成本，好的策略应能识别何时不应建索引
