#!/usr/bin/env python3
"""
PyRosetta-based evaluator for FixedBackboneDesign task.

Usage (CONTRIBUTING.md test command format):
    python verification/evaluator.py scripts/init.py

Usage (step-by-step for local development):
    python verification/evaluator.py prepare --raw-task data/raw_task.json --prepared-output outputs/prepared.pdb
    python verification/evaluator.py evaluate --native outputs/prepared.pdb --candidate outputs/solution.pdb --result-output outputs/result.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Prepare: load raw_task.json, load PDB, create a prepared reference PDB
# ---------------------------------------------------------------------------

def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open() as f:
        return json.load(f)


def dump_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def prepare(raw_task_path: str | Path, prepared_output: str | Path) -> None:
    """
    Read raw_task.json, load the PDB, and output a prepared PDB file.
    The prepared PDB serves as the reference (native) structure.
    """
    import pyrosetta
    pyrosetta.init(silent=True)

    task = load_json(raw_task_path)
    # raw_task.json is at <task_root>/data/raw_task.json
    # PDB paths in raw_task.json are relative to <task_root>/
    task_root = Path(raw_task_path).resolve().parent.parent
    pdb_path = Path(task["pdb_path"])
    if not pdb_path.is_absolute():
        pdb_path = task_root / pdb_path

    design_positions = task["design_positions"]
    task_config = task.get("task_config", {})

    # Load the native structure
    pose = pyrosetta.pose_from_file(str(pdb_path))

    # Set up a PackerTask to repack the native side chains
    scorefxn = pyrosetta.get_fa_scorefxn()

    # Repack the native side chains to get a baseline energy
    # Use a simple TaskFactory that repacks all positions
    tf = pyrosetta.rosetta.core.pack.task.TaskFactory()
    tf.push_back(pyrosetta.rosetta.core.pack.task.operation.RestrictToRepacking())
    packer = pyrosetta.rosetta.protocols.minimization_packing.PackRotamersMover()
    packer.score_function(scorefxn)
    packer.task_factory(tf)
    packer.apply(pose)

    # Score the repacked native pose (this is the baseline energy)
    baseline_energy = scorefxn(pose)

    # Dump the repacked native pose as the prepared reference PDB
    output_path = Path(prepared_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pose.dump_pdb(str(output_path))

    # Also save metadata alongside the prepared PDB
    meta = {
        "baseline_energy": round(baseline_energy, 6),
        "design_positions": design_positions,
        "n_design_positions": len(design_positions),
        "pdb_source": task.get("pdb_id", str(pdb_path.name)),
        "native_sequence": task.get("native_sequence", ""),
        "energy_function": task.get("energy_function", "ref2015"),
        "task_config": task_config,
    }
    meta_path = Path(prepared_output).with_suffix(".meta.json")
    dump_json(meta_path, meta)
    print(f"[evaluator] Prepared reference: {output_path}")
    print(f"[evaluator] Baseline energy: {baseline_energy:.4f}")


# ---------------------------------------------------------------------------
# Evaluate: score the candidate PDB against the native reference
# ---------------------------------------------------------------------------

def evaluate_candidate(
    native_pdb: str | Path,
    candidate_pdb: str | Path,
) -> dict[str, Any]:
    """
    Score a designed PDB using PyRosetta ref2015 and return metrics.

    Returns:
        dict with keys: valid, combined_score, total_energy, baseline_energy,
                        improvement, and individual energy terms (fa_atr, fa_rep, ...)
    """
    import pyrosetta
    pyrosetta.init(silent=True)

    scorefxn = pyrosetta.get_fa_scorefxn()

    # Score native
    native_pose = pyrosetta.pose_from_file(str(native_pdb))
    native_energy = scorefxn(native_pose)

    # Score candidate
    candidate_pose = pyrosetta.pose_from_file(str(candidate_pdb))
    candidate_energy = scorefxn(candidate_pose)

    # Extract individual energy terms
    native_energies = native_pose.energies()
    candidate_energies = candidate_pose.energies()

    # Get the score types used by ref2015
    score_types = [
        pyrosetta.rosetta.core.scoring.ScoreType.fa_atr,
        pyrosetta.rosetta.core.scoring.ScoreType.fa_rep,
        pyrosetta.rosetta.core.scoring.ScoreType.fa_sol,
        pyrosetta.rosetta.core.scoring.ScoreType.fa_elec,
        pyrosetta.rosetta.core.scoring.ScoreType.hbond_bb_sc,
        pyrosetta.rosetta.core.scoring.ScoreType.hbond_sc,
        pyrosetta.rosetta.core.scoring.ScoreType.p_aa_pp,
        pyrosetta.rosetta.core.scoring.ScoreType.ref,
    ]
    term_names = {
        pyrosetta.rosetta.core.scoring.ScoreType.fa_atr: "fa_atr",
        pyrosetta.rosetta.core.scoring.ScoreType.fa_rep: "fa_rep",
        pyrosetta.rosetta.core.scoring.ScoreType.fa_sol: "fa_sol",
        pyrosetta.rosetta.core.scoring.ScoreType.fa_elec: "fa_elec",
        pyrosetta.rosetta.core.scoring.ScoreType.hbond_bb_sc: "hbond_bb_sc",
        pyrosetta.rosetta.core.scoring.ScoreType.hbond_sc: "hbond_sc",
        pyrosetta.rosetta.core.scoring.ScoreType.p_aa_pp: "p_aa_pp",
        pyrosetta.rosetta.core.scoring.ScoreType.ref: "ref",
    }

    metrics: dict[str, Any] = {"valid": True}

    # Total energies
    metrics["native_energy"] = round(native_energy, 6)
    metrics["total_energy"] = round(candidate_energy, 6)
    metrics["improvement"] = round(native_energy - candidate_energy, 6)

    # Combined score: normalized improvement
    if abs(native_energy) > 1e-6:
        metrics["combined_score"] = round((native_energy - candidate_energy) / abs(native_energy), 6)
    else:
        metrics["combined_score"] = 0.0

    # Individual energy terms
    for st in score_types:
        name = term_names.get(st, str(st))
        native_term = native_energies.total_energies()[st] if hasattr(native_energies, "total_energies") else 0.0
        candidate_term = candidate_energies.total_energies()[st] if hasattr(candidate_energies, "total_energies") else 0.0
        metrics[f"native_{name}"] = round(float(native_term), 6)
        metrics[name] = round(float(candidate_term), 6)

    # Detect invalid: NaN or Inf energies
    import math
    for key in ("total_energy", "native_energy"):
        val = metrics.get(key, 0.0)
        if math.isnan(val) or math.isinf(val):
            metrics["valid"] = False
            metrics["combined_score"] = -1e18

    return metrics


def evaluate(
    native_pdb: str | Path,
    candidate_pdb: str | Path,
    result_output: str | Path,
) -> None:
    """Evaluate designed sequence and write result JSON."""
    metrics = evaluate_candidate(native_pdb, candidate_pdb)
    dump_json(result_output, metrics)
    print(f"[evaluator] total_energy={metrics['total_energy']:.4f}  "
          f"improvement={metrics['improvement']:.4f}  "
          f"valid={metrics['valid']}")


# ---------------------------------------------------------------------------
# Full pipeline: run_candidate_and_evaluate
#   Matches the CONTRIBUTING.md test command pattern:
#   python verification/evaluator.py scripts/init.py
# ---------------------------------------------------------------------------

def run_candidate_and_evaluate(script_path: str | Path) -> int:
    """
    Full automatic pipeline:
    1. Read raw_task.json and prepare the PDB
    2. Run the candidate script (design)
    3. Evaluate the designed structure
    4. Write metrics.json to the task root
    """
    evaluator_dir = Path(__file__).resolve().parent
    task_dir = evaluator_dir.parent  # Task root directory
    raw_task = task_dir / "data" / "raw_task.json"
    outputs_dir = task_dir / "outputs"
    outputs_dir.mkdir(exist_ok=True)

    prepared_pdb = outputs_dir / "prepared.pdb"
    solution_pdb = outputs_dir / "solution.pdb"
    metrics_path = task_dir / "metrics.json"

    # Step 1: Prepare
    print(f"[evaluator] Preparing from {raw_task}")
    prepare(raw_task, prepared_pdb)

    # Step 2: Run candidate
    print(f"[evaluator] Running candidate: {script_path}")
    try:
        result = subprocess.run(
            [sys.executable, str(script_path),
             "--prepared-input", str(prepared_pdb),
             "--solution-output", str(solution_pdb)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(task_dir),
        )
        print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
        if result.stderr:
            print(f"[evaluator] candidate stderr:\n{result.stderr[-2000:]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("[evaluator] ERROR: candidate timed out (300s)")
        metrics = {
            "valid": False,
            "combined_score": -1e18,
            "total_energy": 0.0,
            "error_message": "candidate timed out",
        }
        dump_json(metrics_path, metrics)
        print(json.dumps(metrics))
        return 1
    except FileNotFoundError:
        print(f"[evaluator] ERROR: candidate script not found: {script_path}")
        return 1

    # Step 3: Evaluate
    print(f"[evaluator] Evaluating designed structure")
    metrics = evaluate_candidate(prepared_pdb, solution_pdb)
    metrics["returncode"] = result.returncode
    if result.returncode != 0:
        metrics["valid"] = False
        metrics["combined_score"] = -1e18
        metrics["error_message"] = f"candidate returned non-zero exit code: {result.returncode}"

    # Step 4: Write metrics.json
    dump_json(metrics_path, metrics)
    # Also print JSON to stdout for the unified framework
    print(json.dumps(metrics))
    return 0 if metrics.get("valid", False) else 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) == 2 and not sys.argv[1].startswith("--"):
        # CONTRIBUTING.md test command format: python evaluator.py scripts/init.py
        return run_candidate_and_evaluate(sys.argv[1])

    parser = argparse.ArgumentParser(
        description="PyRosetta evaluator for FixedBackboneDesign"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # prepare
    p_prep = subparsers.add_parser("prepare", help="Prepare reference PDB from raw task")
    p_prep.add_argument("--raw-task", required=True)
    p_prep.add_argument("--prepared-output", required=True)

    # evaluate
    p_eval = subparsers.add_parser("evaluate", help="Evaluate designed PDB")
    p_eval.add_argument("--native", required=True)
    p_eval.add_argument("--candidate", required=True)
    p_eval.add_argument("--result-output", required=True)

    args = parser.parse_args()

    if args.command == "prepare":
        prepare(args.raw_task, args.prepared_output)
    elif args.command == "evaluate":
        evaluate(args.native, args.candidate, args.result_output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
