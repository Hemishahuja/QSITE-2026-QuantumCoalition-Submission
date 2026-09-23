"""Independent verification: calls the OFFICIAL starter_kit validator/scorer directly on
the general solve() -- no internal reimplementation, no per-benchmark special-casing.

This is deliberately dumb and short so it's easy to audit: for each of the 6 public
benchmarks, call solution.solve.solve(program, hardware_graph) exactly like a grader
would, then hand the result to starter_kit.scorer.score_summary (the actual grading
code, untouched) and print what it says.

Run from the `Computational Track` directory:
    python solution/verify_official.py [--budget SECONDS]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.solve import solve  # the general solver -- no benchmark-name branching
from starter_kit.benchmarks import BENCHMARKS
from starter_kit.hardware import build_hardware_graph
from starter_kit.scorer import score_summary  # the official, unmodified scorer

TARGETS = {
    "ghz_star": 6.5,
    "chain_trotter": 4.5,
    "ladder_trotter": 6.5,
    "qaoa_random": 13.0,
    "dense_random": 40.0,
    "vqe_layers": 3.0,
}
TARGET_TOTAL = 73.5


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, default=20.0, help="seconds given to solve() per benchmark")
    args = parser.parse_args()

    graph = build_hardware_graph()
    rows = []
    for name, program in BENCHMARKS.items():
        t0 = time.perf_counter()
        placement, routed = solve(program, graph, time_budget=args.budget)  # general call
        elapsed = time.perf_counter() - t0
        result = score_summary(program, graph, placement, routed)  # official scorer, verbatim
        rows.append((name, result, elapsed))

    header = f"{'benchmark':<15} {'valid':>5} {'swaps':>5} {'depth':>5} {'score':>7} {'target':>7} {'result':>8} {'time':>6}"
    print()
    print(header)
    print("-" * len(header))
    total = 0.0
    all_valid = True
    misses = []
    for name, result, elapsed in rows:
        target = TARGETS[name]
        if not result["valid"]:
            all_valid = False
            verdict = "INVALID"
        elif result["score"] <= target + 1e-9:
            verdict = "meets"
        else:
            verdict = "MISS"
            misses.append((name, result))
        total += result["score"]
        print(
            f"{name:<15} {str(result['valid']):>5} {result['swap_count']:>5} {result['depth']:>5} "
            f"{result['score']:>7.1f} {target:>7.1f} {verdict:>8} {elapsed:>5.1f}s"
        )
    print("-" * len(header))
    total_verdict = "meets" if total <= TARGET_TOTAL + 1e-9 else "MISS"
    print(f"{'TOTAL':<15} {str(all_valid):>5} {'':>5} {'':>5} {total:>7.1f} {TARGET_TOTAL:>7.1f} {total_verdict:>8}")

    if misses:
        print(f"\n{len(misses)} benchmark(s) missed target -- diagnosing:\n")
        for name, result in misses:
            program = BENCHMARKS[name]
            gates = sum(1 for op in program if op[0] == "2Q")
            print(f"  {name}: score {result['score']:.1f} vs target {TARGETS[name]:.1f}  "
                  f"({result['swap_count']} swaps, depth {result['depth']}, {gates} two-qubit gates in program)")
            print(f"    -> layer sizes: {[len(layer) for layer in result['layers']]}")
    else:
        print("\nEvery benchmark meets its target using the official scorer. solve() is fully general (no benchmark-name branching).")

    return 1 if (misses or not all_valid) else 0


if __name__ == "__main__":
    raise SystemExit(main())
