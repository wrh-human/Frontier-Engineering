"""
Heuristic index recommendation baseline.

Strategy:
1. Collect all WHERE filter columns and JOIN columns from workload queries.
2. Remove columns already covered by existing indexes (primary keys).
3. Count column frequency across queries.
4. Create single-column B-tree indexes on the most frequent columns.
5. Stop before exceeding max_indexes.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open() as f:
        return json.load(f)


def _resolve_queries(task_dir: Path, raw: dict[str, Any]) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    for ref in raw.get("queries", []):
        qpath = (task_dir / ref).resolve()
        queries.append(load_json(qpath))
    return queries


def _flatten_filter_columns(metadata: dict) -> list[tuple[str, str]]:
    """Extract (table, column) pairs from filter conditions."""
    result: list[tuple[str, str]] = []
    for f in metadata.get("filters", []):
        tbl = f.get("table", "")
        col = f.get("column", "")
        if tbl and col:
            result.append((tbl, col))
    return result


def _flatten_join_columns(metadata: dict) -> list[tuple[str, str]]:
    """Extract (table, column) pairs from JOIN conditions (both sides)."""
    result: list[tuple[str, str]] = []
    for jc in metadata.get("join_conditions", []):
        for side in [jc.get("left", []), jc.get("right", [])]:
            if len(side) >= 2:
                result.append((side[0], side[1]))
    return result


def _columns_in_existing_index(table: str, schema: dict[str, Any]) -> set[str]:
    """Return set of column names already covered by existing indexes on this table."""
    covered: set[str] = set()
    table_info = schema.get(table, {})
    for idx in table_info.get("existing_indexes", []):
        for col in idx.get("columns", []):
            covered.add(col)
    return covered


def recommend_indexes(
    queries: list[dict[str, Any]],
    schema: dict[str, Any],
    constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    max_indexes = constraints.get("max_indexes", 10)

    # Collect candidate columns from filters and joins
    candidates: list[tuple[str, str]] = []
    for q in queries:
        meta = q.get("metadata", {})
        candidates.extend(_flatten_filter_columns(meta))
        candidates.extend(_flatten_join_columns(meta))

    # Count frequency and deduplicate
    freq = Counter(candidates)

    # Sort by frequency (descending), then by table name for determinism
    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0][0], x[0][1]))

    # Build index list, skipping columns already covered by existing indexes
    result: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for (table, column), _count in ranked:
        if len(result) >= max_indexes:
            break
        if (table, column) in seen_pairs:
            continue
        # Skip if column already has an index
        existing = _columns_in_existing_index(table, schema)
        if column in existing:
            continue
        seen_pairs.add((table, column))
        result.append({"table": table, "columns": [column], "method": "btree"})

    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    task_dir = input_path.parent.parent
    raw = load_json(input_path)
    queries = _resolve_queries(task_dir, raw)
    schema = raw.get("schema", {})
    constraints = raw.get("constraints", {})

    indexes = recommend_indexes(queries, schema, constraints)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"indexes": indexes}, indent=2), encoding="utf-8"
    )
    print(f"heuristic indexes: {len(indexes)}")
    for idx in indexes:
        print(f"  {idx['table']}({', '.join(idx['columns'])})")


if __name__ == "__main__":
    main()
