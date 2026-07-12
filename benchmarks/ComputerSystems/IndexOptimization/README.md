# IndexOptimization — PostgreSQL Index Selection

Optimize index configuration for a TPC-H SF1 analytical workload on PostgreSQL 16.

## Benchmark ID

`ComputerSystems/IndexOptimization`

## Task

Given a PostgreSQL database with TPC-H schema and a mixed analytical SQL workload, find a high-quality B-tree index configuration that improves query performance compared to a heuristic baseline, subject to index count and storage constraints.

## Economic Relevance

Database index optimization is a core database administration task with direct operational impact:
- PostgreSQL is one of the most widely deployed open-source databases in production, used by enterprises across finance, e-commerce, logistics, and analytics.
- Unoptimized indexes cause unnecessary storage costs, slow down write operations, and degrade query performance — a poorly indexed database can be 10-100x slower than a well-tuned one.
- Automated index selection reduces the need for manual DBA tuning, which is both expensive and error-prone at scale.
- This benchmark uses TPC-H, the industry-standard decision-support workload, making results transferable to real-world analytical processing environments.

## Workload

6 TPC-H SF1 queries covering multi-table JOINs, aggregation, range filters, sorting, and sequential scans.

## Environment

Requires Docker:

```bash
docker build -t frontier-pg-index:latest verification/docker/
```

## Quick Run

```bash
python scripts/init.py --input data/raw_task.json --output outputs/candidate.json
python verification/evaluator.py scripts/init.py
```

## First-Time Setup

Generate TPC-H SF1 data (required once before running the evaluator):

```bash
bash data/tpch_sf1/gen_data.sh
```

## Unified Task

```bash
python -m frontier_eval \
  task=unified \
  task.benchmark=ComputerSystems/IndexOptimization \
  task.runtime.isolation_mode=docker \
  task.runtime.docker_image=frontier-pg-index:latest \
  algorithm=openevolve \
  algorithm.iterations=0
```

## Algorithm Agnostic

This benchmark is algorithm-agnostic. Rule-based, ML-based, LLM-based, and search-based approaches are all welcome.

Inspired by research on automated index recommendation including LLMIA (Zhao et al., arXiv:2503.07884, 2025).
