#!/usr/bin/env python3
"""
NCAAInsertion baseline solution — Insert TRF and redesign surrounding residues.

This file is the target of agent evolution. Code inside EVOLVE-BLOCK
can be modified by the agent; everything outside is read-only.

CLI contract:
    --prepared-input <path>   Path to prepared PDB (with design position metadata)
    --solution-output <path>  Path to write the designed PDB
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Read-only utilities (CLI contract, I/O)
# ---------------------------------------------------------------------------

def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open() as f:
        return json.load(f)


def dump_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def load_metadata(prepared_pdb: str | Path) -> dict[str, Any]:
    """Load design metadata from the .meta.json file accompanying the prepared PDB."""
    p = Path(prepared_pdb)
    for suffix in [".pdb.meta.json", ".meta.json"]:
        meta_path = p.with_suffix(suffix)
        if meta_path.exists():
            return load_json(meta_path)
    return {}


def resolve_trf_params() -> str | None:
    """Find TRF.params relative to this script's location."""
    candidates = [
        Path(__file__).resolve().parent.parent / "references" / "params" / "TRF.params",
    ]
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    return None


# EVOLVE-BLOCK-START
# ---------------------------------------------------------------------------
# Editable region — agent may modify the design algorithm below
# ---------------------------------------------------------------------------

import pyrosetta
from pyrosetta.rosetta.core.pack.task import TaskFactory
from pyrosetta.rosetta.core.pack.task.operation import PreventRepacking
from pyrosetta.rosetta.protocols.minimization_packing import PackRotamersMover


def design_sequence(
    prepared_pdb: str | Path,
    solution_output: str | Path,
    design_positions: list[int],
    trf_position: int,
    trf_params_path: str | None,
) -> dict[str, Any]:
    """
    Design optimal amino acids around TRF using PackRotamers.

    The prepared PDB already has TRF at trf_position (placed by evaluator).
    This function allows AA changes at design_positions to accommodate TRF.

    Strategy:
    1. Load PDB with TRF parameters
    2. Create design task: design at design_positions (change AA type + repack)
    3. Run 3 independent design rounds, keep the lowest-energy result
    4. Output designed PDB

    Agent improvements:
    - Simulated annealing with Metropolis acceptance
    - Flexible backbone around TRF
    - Custom rotamer libraries
    - Iterative design → minimization cycles
    """
    extra_res = f"-extra_res_fa {trf_params_path}" if trf_params_path else ""
    pyrosetta.init(silent=True, extra_options=extra_res)

    pose = pyrosetta.pose_from_file(str(prepared_pdb))
    scorefxn = pyrosetta.get_fa_scorefxn()

    # Run multiple design rounds, keep best
    best_energy = float("inf")
    best_pose = None

    for _ in range(3):
        # Fresh task per round (ensures no carryover from previous round)
        tf = TaskFactory()
        task = tf.create_task_and_apply_taskoperations(pose)

        for i in range(1, pose.total_residue() + 1):
            if i == trf_position:
                task.nonconst_residue_task(i).prevent_repacking()
            elif i in design_positions:
                # Full design mode: allow AA changes + extra rotamer sampling
                rt = task.nonconst_residue_task(i)
                rt.or_ex1(True)
                rt.or_ex2(True)
                rt.or_ex1aro(True)
                # Default behavior = design mode (not restrict_to_repacking)
            else:
                task.nonconst_residue_task(i).prevent_repacking()

        packer = PackRotamersMover()
        packer.score_function(scorefxn)
        packer.task(task)
        packer.apply(pose)

        energy = scorefxn(pose)
        if energy < best_energy:
            best_energy = energy
            best_pose = pose.clone()

    # Apply the best design
    pose.assign(best_pose)
    final_energy = scorefxn(pose)

    final_energy = scorefxn(pose)

    # Write output
    output_path = Path(solution_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pose.dump_pdb(str(output_path))

    # Check if TRF is present
    trf_found = False
    if 1 <= trf_position <= pose.total_residue():
        res = pose.residue(trf_position)
        trf_found = "TRF" in res.name() or res.name3() == "TRF"

    result = {
        "final_energy": round(float(final_energy), 6),
        "trf_position": trf_position,
        "trf_inserted": trf_found,
        "n_designed_positions": len(design_positions),
        "solver": "repack_around_trf",
    }
    return result

    final_energy = scorefxn(pose)

    # Write output
    output_path = Path(solution_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pose.dump_pdb(str(output_path))

    # Check if TRF is present
    trf_found = False
    if 1 <= trf_position <= pose.total_residue():
        res = pose.residue(trf_position)
        trf_found = res.name3() == "TRF" or "TRF" in res.name()

    result = {
        "final_energy": round(float(final_energy), 6),
        "trf_position": trf_position,
        "trf_inserted": trf_found,
        "n_designed_positions": len(design_positions) + (1 if trf_position > 0 else 0),
        "solver": "packrotamers_single_round_ncaa",
    }
    return result


# EVOLVE-BLOCK-END
# ---------------------------------------------------------------------------
# End of editable region
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Read-only main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NCAAInsertion baseline — insert TRF and design surrounding residues"
    )
    parser.add_argument("--prepared-input", required=True, help="Prepared PDB file")
    parser.add_argument("--solution-output", required=True, help="Output PDB path")
    args = parser.parse_args()

    # Load metadata
    meta = load_metadata(args.prepared_input)
    design_positions = meta.get("design_positions", [4, 6, 8])
    trf_position = meta.get("trf_position", 4)
    trf_params = resolve_trf_params()

    result = design_sequence(
        prepared_pdb=args.prepared_input,
        solution_output=args.solution_output,
        design_positions=design_positions,
        trf_position=trf_position,
        trf_params_path=trf_params,
    )

    trf_status = "✅" if result["trf_inserted"] else "❌"
    print(f"[init] TRF at position {result['trf_position']}: {trf_status}")
    print(f"[init] Designed {result['n_designed_positions']} positions, "
          f"final energy: {result['final_energy']:.4f}")


if __name__ == "__main__":
    main()
