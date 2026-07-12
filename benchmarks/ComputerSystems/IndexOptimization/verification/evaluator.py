"""Evaluator for PostgreSQL index optimization benchmark (TPC-H SF1 workload)."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

INVALID_COMBINED_SCORE = -1e18

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _is_repo_root(path: Path) -> bool:
    return (path / "benchmarks").is_dir() and (path / "frontier_eval").is_dir()


def _find_repo_root() -> Path:
    env_root = (os.environ.get("FRONTIER_ENGINEERING_ROOT") or "").strip()
    if env_root:
        cand = Path(env_root).expanduser().resolve()
        if _is_repo_root(cand):
            return cand
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if _is_repo_root(parent):
            return parent
    return Path.cwd().resolve()


def _task_dir(repo_root: Path) -> Path:
    return repo_root / "benchmarks" / "ComputerSystems" / "IndexOptimization"


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open() as f:
        return json.load(f)


def dump_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# PostgreSQL helpers
# ---------------------------------------------------------------------------

def _pg_connect(port: int, dbname: str = "postgres") -> Any:
    import psycopg2
    return psycopg2.connect(
        host="localhost", port=port, dbname=dbname,
        user="postgres", password="postgres",
    )


def _run_sql(conn: Any, sql: str, timeout_s: int = 60) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(f"SET statement_timeout = '{timeout_s}s'")
        cur.execute(sql)
        if cur.description is not None:
            return cur.fetchall()
        return []


def _table_exists(conn: Any, table: str) -> bool:
    sql = "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)"
    with conn.cursor() as cur:
        cur.execute(sql, (table,))
        return cur.fetchone()[0]


def _column_exists(conn: Any, table: str, column: str) -> bool:
    sql = "SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = %s AND column_name = %s)"
    with conn.cursor() as cur:
        cur.execute(sql, (table, column))
        return cur.fetchone()[0]


def _get_index_size_mb(conn: Any) -> float:
    sql = """
    SELECT COALESCE(SUM(pg_relation_size(indexrelid)), 0) / 1048576.0
    FROM pg_stat_user_indexes
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return float(cur.fetchone()[0])


def _get_data_size_mb(conn: Any) -> float:
    sql = """
    SELECT COALESCE(SUM(pg_relation_size(relid)), 0) / 1048576.0
    FROM pg_stat_user_tables
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return float(cur.fetchone()[0])


def _quote_ident(name: str) -> str:
    return f'"{name}"'


def _create_single_index(conn: Any, table: str, columns: list[str], method: str) -> None:
    cols = ", ".join(_quote_ident(c) for c in columns)
    idx_name = f"idx_{table}_{'_'.join(columns)}"
    sql = f"CREATE INDEX IF NOT EXISTS {_quote_ident(idx_name)} ON {_quote_ident(table)} USING {method} ({cols})"
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def _get_actual_indexes(conn: Any) -> list[dict[str, Any]]:
    """Return list of actual indexes currently in the database."""
    sql = """
    SELECT schemaname, tablename, indexname, indexdef
    FROM pg_indexes
    WHERE schemaname = 'public'
    ORDER BY tablename, indexname
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    result = []
    for row in rows:
        result.append({
            "table": row[1],
            "index_name": row[2],
            "definition": row[3],
        })
    return result


# ---------------------------------------------------------------------------
# Container management
# ---------------------------------------------------------------------------

def _start_postgres(docker_image: str, data_dir: Path | None = None) -> tuple[str, int]:
    """Start a PostgreSQL Docker container, return (container_id, port)."""
    cmd = [
        "docker", "run", "-d",
        "--rm",
        "-e", "POSTGRES_PASSWORD=postgres",
        "-e", "POSTGRES_DB=postgres",
        "-P",  # random port
    ]
    if data_dir is not None and data_dir.exists():
        cmd += ["-v", f"{data_dir.resolve()}:/var/lib/postgresql/data"]
    cmd.append(docker_image)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"Docker start failed: {result.stderr}")

    container_id = result.stdout.strip()

    # Get the mapped port
    port_result = subprocess.run(
        ["docker", "port", container_id, "5432"],
        capture_output=True, text=True, timeout=10,
    )
    if port_result.returncode != 0:
        _stop_postgres(container_id)
        raise RuntimeError(f"Failed to get port: {port_result.stderr}")

    port = int(port_result.stdout.strip().split(":")[-1])

    # Wait for PostgreSQL to be ready
    for _ in range(30):
        try:
            conn = _pg_connect(port)
            conn.close()
            return container_id, port
        except Exception:
            time.sleep(1)

    _stop_postgres(container_id)
    raise RuntimeError("PostgreSQL did not become ready within 30s")


def _stop_postgres(container_id: str) -> None:
    subprocess.run(["docker", "stop", container_id], capture_output=True, timeout=30)


# ---------------------------------------------------------------------------
# Data restore helper
# ---------------------------------------------------------------------------

def _restore_dump(container_id: str, dump_path: Path) -> None:
    """Copy and restore a pg_dump archive into a running PostgreSQL container."""
    subprocess.run(
        f"docker cp {dump_path} {container_id}:/tmp/tpch_sf1.dump".split(),
        capture_output=True, timeout=30,
    )
    subprocess.run(
        ["docker", "exec", "-i", container_id,
         "pg_restore", "-U", "postgres", "-d", "postgres",
         "-Fc", "--clean", "/tmp/tpch_sf1.dump"],
        capture_output=True, text=True, timeout=300,
    )


# ---------------------------------------------------------------------------
# SQL loading helpers
# ---------------------------------------------------------------------------

def _resolve_queries(task_dir: Path, raw: dict[str, Any]) -> list[dict[str, Any]]:
    queries = []
    for ref in raw.get("queries", []):
        qpath = (task_dir / ref).resolve()
        q = load_json(qpath)
        queries.append(q)
    return queries


# ---------------------------------------------------------------------------
# Result normalization & comparison
# ---------------------------------------------------------------------------

def _normalize_value(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 8)
    if isinstance(v, int):
        return float(v)
    if isinstance(v, (list, tuple)):
        return tuple(_normalize_value(x) for x in v)
    if isinstance(v, dict):
        return {k: _normalize_value(v) for k, v in v.items()}
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v)


def _results_match(baseline_rows: list[tuple], candidate_rows: list[tuple]) -> bool:
    """Compare two result sets. Handle ORDERED vs unordered comparison."""
    if len(baseline_rows) != len(candidate_rows):
        return False
    # Use normalized comparison
    bn = [_normalize_value(r) for r in baseline_rows]
    cn = [_normalize_value(r) for r in candidate_rows]
    # Try ordered comparison first, fall back to sorted
    if bn == cn:
        return True
    return sorted(bn) == sorted(cn)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

def _time_query(conn: Any, sql: str, repeats: int = 3, timeout_s: int = 60) -> float:
    """Execute a query `repeats` times and return the median time in seconds."""
    times = []
    for _ in range(repeats):
        start = time.time()
        try:
            _run_sql(conn, sql, timeout_s)
        except Exception as e:
            raise RuntimeError(f"Query failed: {e}") from e
        elapsed = time.time() - start
        times.append(elapsed)
    times.sort()
    return times[len(times) // 2]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_index_def(
    idx: dict[str, Any], conn: Any,
) -> tuple[bool, str]:
    table = idx.get("table", "")
    columns = idx.get("columns", [])
    method = idx.get("method", "")

    if not table:
        return False, "missing table name"
    if not columns or not isinstance(columns, list):
        return False, "columns must be a non-empty list"
    if method != "btree":
        return False, f"unsupported index method: {method}"

    if not _table_exists(conn, table):
        return False, f"table does not exist: {table}"

    for col in columns:
        if not _column_exists(conn, table, col):
            return False, f"column does not exist: {table}.{col}"

    return True, ""


# ---------------------------------------------------------------------------
# Submission runner
# ---------------------------------------------------------------------------

def _run_submission(script_path: Path, input_path: Path, output_path: Path, cwd: Path) -> tuple[dict[str, Any], str, str]:
    """Run a submission script and return parsed output."""
    result = subprocess.run(
        [sys.executable, str(script_path), "--input", str(input_path), "--output", str(output_path)],
        capture_output=True, text=True, timeout=60, cwd=str(cwd),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Script failed (exit {result.returncode}): {result.stderr[:500]}")

    if not output_path.exists():
        raise RuntimeError(f"Output file not created: {output_path}")

    try:
        submission = load_json(output_path)
    except Exception as e:
        raise RuntimeError(f"Invalid output JSON: {e}") from e

    return submission, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def _compute_metrics(
    no_index_time: float,
    baseline_time: float,
    candidate_time: float,
    n_indexes: int,
    storage_mb: float,
    data_size_mb: float,
    correct: bool,
    warmup_ok: bool,
) -> dict[str, Any]:
    """Compute all metrics and validation flags."""

    metrics: dict[str, Any] = {
        "valid": 0.0,
        "combined_score": INVALID_COMBINED_SCORE,
        "no_index_time_s": round(no_index_time, 4),
        "baseline_time_s": round(baseline_time, 4),
        "candidate_time_s": round(candidate_time, 4),
        "n_indexes": n_indexes,
        "storage_mb": round(storage_mb, 2),
        "data_size_mb": round(data_size_mb, 2),
        "correct": 1.0 if correct else 0.0,
    }

    # Hard constraints
    valid = (
        correct
        and warmup_ok
        and n_indexes <= 10
        and storage_mb <= 500.0
    )

    if not valid:
        metrics["valid"] = 0.0
        metrics["combined_score"] = INVALID_COMBINED_SCORE
        return metrics

    speedup = baseline_time / max(candidate_time, 1e-9)
    metrics["speedup"] = round(speedup, 4)

    log_speedup = math.log2(max(speedup, 1e-6))
    storage_penalty = 0.3 * (storage_mb / 500.0)
    count_penalty = 0.1 * (n_indexes / 10.0)
    combined = log_speedup * (1.0 - storage_penalty - count_penalty)

    metrics["valid"] = 1.0
    metrics["combined_score"] = round(combined, 6)
    metrics["log_speedup"] = round(log_speedup, 6)
    metrics["storage_penalty"] = round(storage_penalty, 6)
    metrics["count_penalty"] = round(count_penalty, 6)

    return metrics


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate(
    program_path: str,
    *,
    timeout_s: float = 300.0,
    repo_root: Path | None = None,
) -> Any:
    """
    UnifiedTask evaluator interface.

    1. Load config and generate submissions
    2. Start PostgreSQL, restore data
    3. Measure no-index, heuristic, and candidate performance
    4. Verify correctness
    5. Compute score
    """
    artifacts: dict[str, Any] = {}
    start_time = time.time()

    # Resolve paths
    if repo_root is None:
        repo_root = _find_repo_root()
    task_dir = _task_dir(repo_root)
    docker_image = "frontier-pg-index:latest"
    raw_task_path = task_dir / "data" / "raw_task.json"
    temp_dir = Path(tempfile.mkdtemp(prefix="fe_idxopt_"))
    outputs_dir = task_dir / "outputs"
    outputs_dir.mkdir(exist_ok=True)

    try:
        # ------------------------------------------------------------------
        # Phase A: Generate submissions
        # ------------------------------------------------------------------
        raw = load_json(raw_task_path)
        queries = _resolve_queries(task_dir, raw)
        schema = raw.get("schema", {})
        constraints = raw.get("constraints", {})
        artifacts["n_queries"] = len(queries)
        artifacts["query_ids"] = [q.get("id", f"q{i}") for i, q in enumerate(queries)]

        # Run baseline (heuristic)
        baseline_script = task_dir / "baseline" / "heuristic.py"
        baseline_output = outputs_dir / "baseline_submission.json"
        try:
            baseline_sub, baseline_stdout, baseline_stderr = _run_submission(
                baseline_script, raw_task_path, baseline_output, task_dir,
            )
            artifacts["baseline_stdout"] = baseline_stdout[:2000]
        except Exception as e:
            artifacts["error_message"] = f"baseline failed: {e}"
            return _wrap(_compute_metrics(0, 0, 0, 0, 0, 0, False, False), artifacts)

        # Run candidate
        candidate_script = Path(program_path).resolve()
        candidate_output = outputs_dir / "candidate_submission.json"
        try:
            candidate_sub, candidate_stdout, _ = _run_submission(
                candidate_script, raw_task_path, candidate_output, task_dir,
            )
            artifacts["candidate_stdout"] = candidate_stdout[:2000]
        except Exception as e:
            artifacts["error_message"] = f"candidate failed: {e}"
            return _wrap(_compute_metrics(0, 0, 0, 0, 0, 0, False, False), artifacts)

        candidate_indexes = candidate_sub.get("indexes", [])
        artifacts["candidate_n_indexes"] = len(candidate_indexes)
        artifacts["candidate_indexes"] = candidate_indexes

        # ------------------------------------------------------------------
        # Phase B: Database evaluation
        # ------------------------------------------------------------------
        container_id = None
        port = None

        try:
            container_id, port = _start_postgres(docker_image)
            artifacts["postgres_port"] = port
            conn = _pg_connect(port)

            # Restore data from pre-generated dump
            dump_path = task_dir / "data" / "tpch_sf1" / "tpch_sf1.dump"
            if not dump_path.exists():
                raise RuntimeError("no tpch_sf1.dump found — run data/tpch_sf1/gen_data.sh first")
            _restore_dump(container_id, dump_path)
            conn = _pg_connect(port)

            # Verify existing indexes in database match metadata
            actual_idxs = _get_actual_indexes(conn)
            artifacts["actual_indexes"] = actual_idxs
            if len(actual_idxs) == 0:
                raise RuntimeError("No primary key indexes created - schema may not be loaded correctly")

            # Prepare query list for timing
            query_sqls = [q.get("sql", "") for q in queries]
            query_ids = [q.get("id", f"q{i}") for i, q in enumerate(queries)]

            # --------------------------------------------------------------
            # Measure no-index time (reference only)
            # --------------------------------------------------------------
            no_index_times = []
            for sql in query_sqls:
                t = _time_query(conn, sql, repeats=3)
                no_index_times.append(t)
            no_index_total = sum(no_index_times)
            artifacts["no_index_query_times"] = no_index_times

            # --------------------------------------------------------------
            # Measure heuristic time (baseline)
            # --------------------------------------------------------------
            baseline_indexes = baseline_sub.get("indexes", [])
            for idx in baseline_indexes:
                ok, msg = _validate_index_def(idx, conn)
                if not ok:
                    raise RuntimeError(f"Invalid baseline index: {msg}")
                _create_single_index(conn, idx["table"], idx["columns"], idx["method"])
            conn.commit()

            # Warmup
            for sql in query_sqls:
                try:
                    _run_sql(conn, sql, timeout_s=30)
                except Exception:
                    pass

            baseline_times = []
            for sql in query_sqls:
                t = _time_query(conn, sql, repeats=3)
                baseline_times.append(t)
            baseline_total = sum(baseline_times)
            artifacts["baseline_query_times"] = baseline_times
            artifacts["baseline_indexes"] = baseline_indexes

            # Store baseline results for correctness comparison
            baseline_results = []
            for sql in query_sqls:
                baseline_results.append(_run_sql(conn, sql))
            conn.close()

            # --------------------------------------------------------------
            # Measure candidate time
            # --------------------------------------------------------------
            _stop_postgres(container_id)
            container_id = None

            # Fresh instance for candidate
            container_id2, port2 = _start_postgres(docker_image)
            conn2 = _pg_connect(port2)
            _restore_dump(container_id2, dump_path)
            conn2 = _pg_connect(port2)

            # Validate and create candidate indexes
            for idx in candidate_indexes:
                ok, msg = _validate_index_def(idx, conn2)
                if not ok:
                    raise RuntimeError(f"Invalid candidate index: {msg}")
                _create_single_index(conn2, idx["table"], idx["columns"], idx["method"])
            conn2.commit()

            # Measure storage
            storage_mb = _get_index_size_mb(conn2)
            data_size_mb = _get_data_size_mb(conn2)

            # Warmup
            for sql in query_sqls:
                try:
                    _run_sql(conn2, sql, timeout_s=30)
                except Exception:
                    pass

            candidate_times = []
            for sql in query_sqls:
                t = _time_query(conn2, sql, repeats=3)
                candidate_times.append(t)
            candidate_total = sum(candidate_times)
            artifacts["candidate_query_times"] = candidate_times

            # Correctness verification
            candidate_results = []
            for sql in query_sqls:
                candidate_results.append(_run_sql(conn2, sql))

            all_correct = True
            mismatches = []
            for i, (br, cr) in enumerate(zip(baseline_results, candidate_results)):
                if not _results_match(br, cr):
                    all_correct = False
                    mismatches.append(query_ids[i])
            artifacts["mismatch_queries"] = mismatches

            conn2.close()
            _stop_postgres(container_id2)

            # Compute final score
            metrics = _compute_metrics(
                no_index_total,
                baseline_total,
                candidate_total,
                len(candidate_indexes),
                storage_mb,
                data_size_mb,
                all_correct,
                True,
            )
            metrics["runtime_s"] = round(time.time() - start_time, 4)
            return _wrap(metrics, artifacts)

        except Exception as e:
            traceback.print_exc()
            artifacts["error_message"] = str(e)[:500]
            err_metrics = {
                "valid": 0.0,
                "combined_score": INVALID_COMBINED_SCORE,
                "no_index_time_s": 0.0,
                "baseline_time_s": 0.0,
                "candidate_time_s": 0.0,
                "n_indexes": 0,
                "storage_mb": 0.0,
                "data_size_mb": 0.0,
                "correct": 0.0,
                "timeout": 1.0 if "timeout" in str(e).lower() else 0.0,
                "runtime_s": round(time.time() - start_time, 4),
            }
            return _wrap(err_metrics, artifacts)
        finally:
            if container_id is not None:
                try:
                    _stop_postgres(container_id)
                except Exception:
                    pass

    except Exception as e:
        traceback.print_exc()
        artifacts["error_message"] = str(e)[:500]
        return _wrap(
            {"valid": 0.0, "combined_score": INVALID_COMBINED_SCORE, "runtime_s": round(time.time() - start_time, 4)},
            artifacts,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _wrap(metrics: dict[str, Any], artifacts: dict[str, Any]) -> Any:
    try:
        from openevolve.evaluation_result import EvaluationResult
        return EvaluationResult(metrics=metrics, artifacts=artifacts)
    except ImportError:
        return metrics


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) == 2 and not sys.argv[1].startswith("--"):
        result = evaluate(sys.argv[1])
        metrics = result.metrics if hasattr(result, "metrics") else result
        print(json.dumps(metrics))
        return 0 if metrics.get("valid", 0) > 0 else 1

    parser = argparse.ArgumentParser(description="IndexOptimization evaluator")
    parser.add_argument("program", help="Path to candidate program")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else None
    result = evaluate(args.program, timeout_s=args.timeout, repo_root=repo_root)
    metrics = result.metrics if hasattr(result, "metrics") else result
    print(json.dumps(metrics))
    return 0 if metrics.get("valid", 0) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
