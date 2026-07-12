# IndexOptimization

## Overview

Find high-quality B-tree index configurations for a PostgreSQL database serving a mixed analytical SQL workload (TPC-H SF1). The goal is to improve query execution time under index count and storage constraints, compared to a heuristic baseline.

## Input

The candidate script (`scripts/init.py`) reads the task configuration from `--input` and writes output to `--output`. The input provides:

- **Workload queries**: SQL queries with structured metadata (tables, filters, JOIN conditions)
- **Database schema**: Table definitions with column types, row counts, and existing indexes (primary keys)
- **Constraints**: `max_indexes` (10), `max_storage_mb` (500)

## Output

```json
{
  "indexes": [
    {"table": "orders", "columns": ["o_orderdate", "o_custkey"], "method": "btree"}
  ]
}
```

## Scoring

| Metric | Description |
|--------|-------------|
| `no_index_time_s` | Total query time without indexes (reference) |
| `baseline_time_s` | Total query time with heuristic indexes |
| `candidate_time_s` | Total query time with candidate indexes |
| `speedup` | baseline_time / candidate_time |
| `combined_score` | log2(speedup) × (1 - storage_penalty - count_penalty) |

**Hard constraints**: All queries must return identical results; ≤10 indexes; ≤500 MB storage.

## Workload Queries

| ID | Pattern | Tables | Index optimization target |
|----|---------|--------|--------------------------|
| Q1 | Single-table aggregate | lineitem | Sequential scan test |
| Q3 | Join + filter + aggregate + order | customer, orders, lineitem | Multi-join, range filter |
| Q5 | Multi-way join + aggregate | 6 tables | Star schema join |
| Q6 | Single-table range filter + aggregate | lineitem | Selective filter |
| Q10 | Join + aggregate + order | customer, orders, lineitem, nation | Join + filter |
| Q12 | Join + dual-condition filter + aggregate | orders, lineitem | Filter selectivity |

## Constraints

1. Only modify `scripts/init.py` — the ONLY editable file.
2. Keep CLI contract: `--input` and `--output`.
3. Output must contain an `indexes` list.
4. Each index must specify table, columns (list), and method ("btree").
5. Maximum 10 indexes per submission.
6. Total index storage must not exceed 500 MB.
7. All queries must return identical results before and after indexing.
8. Do not recommend indexes already covered by primary keys (listed in schema existing_indexes).

## Economic Relevance

Database index optimization directly affects production system performance and operational cost:
- **Query performance**: A well-chosen index can reduce query time from minutes to milliseconds in analytical workloads.
- **Storage cost**: Each unnecessary index consumes disk space and memory for caching. At TPC-H SF1 scale, an unused index costs ~10-100 MB.
- **Write overhead**: Indexes slow down INSERT/UPDATE/DELETE operations. In read-write mixed workloads, excess indexes degrade overall throughput.
- **Operational complexity**: Production databases often have hundreds of tables. Manual index tuning does not scale — automated index selection is a recognized industry need.

This benchmark evaluates an agent's ability to make these engineering trade-offs: improving read performance while respecting storage and count constraints.

## Optimization Directions

Agents can explore the following strategies to find better index configurations:

1. **Identify high-selectivity filters**: Indexing columns used in WHERE clauses with high selectivity (e.g., `o_orderdate`, `l_shipdate`) can significantly reduce scan ranges.

2. **Covering indexes for JOIN columns**: Indexes on foreign key columns (e.g., `o_custkey`, `l_orderkey`) can accelerate hash join probe phases.

3. **Avoid over-indexing**: Unused indexes incur storage and maintenance costs. The scoring formula penalizes both storage ratio and index count — more indexes do not always yield better scores.

4. **Recognize sequential-scan scenarios**: Queries like Q1 (aggregating most of a large table) may not benefit from indexes. Over-indexing such queries adds cost without benefit.

5. **Multi-column indexes**: A composite index (e.g., `(o_orderdate, o_custkey)`) can serve multiple query clauses simultaneously, potentially replacing several single-column indexes.

6. **Analyze Q1's behavior**: Q1 performs a full table scan on `lineitem`. Indexes do not help this query but consume storage. The scoring formula accounts for storage cost — a good strategy should recognize when not to index.
