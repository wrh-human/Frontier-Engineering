# IndexOptimization — PostgreSQL 索引优化

对 TPC-H SF1 分析型 workload，在 PostgreSQL 16 上优化索引配置。

## 统一 benchmark ID

`ComputerSystems/IndexOptimization`

## 任务描述

给定一个 PostgreSQL 数据库（TPC-H schema）和一个分析型 SQL workload，在索引数量和存储空间约束下，找到比 heuristic 基线更好的 B-tree 索引配置。

## 环境准备

```bash
docker build -t frontier-pg-index:latest verification/docker/
```

## 快速运行

```bash
python scripts/init.py --input data/raw_task.json --output outputs/candidate.json
python verification/evaluator.py scripts/init.py
```

## Unified task 运行

```bash
python -m frontier_eval \
  task=unified \
  task.benchmark=ComputerSystems/IndexOptimization \
  task.runtime.isolation_mode=docker \
  task.runtime.docker_image=frontier-pg-index:latest \
  algorithm=openevolve \
  algorithm.iterations=0
```

本 benchmark 对算法无关，基于规则的、ML-based、LLM-based、搜索算法均可参与。
