# EVOLVE-BLOCK-START
"""Index Optimization candidate program — recommend PostgreSQL indexes for TPC-H workload."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


# DO NOT MODIFY: CLI contract
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index Optimization candidate — recommend PostgreSQL indexes"
    )
    parser.add_argument("--input", required=True, help="Path to raw_task.json")
    parser.add_argument("--output", required=True, help="Output JSON path")
    return parser.parse_args()


# DO NOT MODIFY: input loading
def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open() as f:
        return json.load(f)


# DO NOT MODIFY: recursive resolve of query references
def _resolve_queries(task_dir: Path, raw: dict[str, Any]) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    for ref in raw.get("queries", []):
        qpath = (task_dir / ref).resolve()
        q = load_json(qpath)
        # Overlay benchmark_id context
        q["__query_file__"] = str(qpath)
        queries.append(q)
    return queries


# MODIFIABLE: index recommendation strategy (core optimization target)
def recommend_indexes(
    queries: list[dict[str, Any]],
    schema: dict[str, Any],
    constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Recommend a set of B-tree indexes to optimize query performance.

    Args:
        queries: List of query dicts, each with {"id", "sql", "metadata"}.
        schema: Dict mapping table name to {"columns", "row_count", "existing_indexes"}.
        constraints: {"max_indexes": int, "max_storage_mb": int, ...}

    Returns:
        List of index specs: [{"table": str, "columns": list[str], "method": "btree"}, ...]
    """
    # Baseline: no additional indexes beyond existing primary keys
    return []


# DO NOT MODIFY: output format
def solve(raw: dict[str, Any], task_dir: Path) -> dict[str, Any]:
    queries = _resolve_queries(task_dir, raw)
    schema = raw.get("schema", {})
    constraints = raw.get("constraints", {})
    indexes = recommend_indexes(queries, schema, constraints)
    return {
        "indexes": indexes,
        "benchmark_id": raw.get("benchmark_id", "index_optimization"),
    }


# DO NOT MODIFY: entry point
def main() -> None:
    args = _parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    task_dir = input_path.parent.parent  # data/ -> task root
    raw = load_json(input_path)
    submission = solve(raw, task_dir)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(submission, indent=2), encoding="utf-8")
    print(f"indexes: {len(submission.get('indexes', []))}")
    print(f"submission: {output_path}")


if __name__ == "__main__":
    main()
# EVOLVE-BLOCK-END
