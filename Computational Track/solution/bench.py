"""Score the solver on every benchmark with the official starter-kit scorer.

Run from the `Computational Track` directory:
    python solution/bench.py                 # default budget per benchmark
    python solution/bench.py --budget 30     # longer search
    python solution/bench.py --json out.json # machine-readable results
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.solve import lower_bound, solve_with_info  # noqa: E402
from starter_kit.baseline_routing import solve as baseline_solve  # noqa: E402
from starter_kit.benchmarks import BENCHMARKS  # noqa: E402
from starter_kit.hardware import build_hardware_graph  # noqa: E402
from starter_kit.scorer import score_summary  # noqa: E402


def run_bench(
    budget: float = 10.0,
    seed: int = 0,
    names: list[str] | None = None,
    quiet: bool = False,
    lb_budget: float | None = None,
) -> dict:
    graph = build_hardware_graph()
    rows = []
    for name, program in BENCHMARKS.items():
        if names and name not in names:
            continue
        bl_placement, bl_routed = baseline_solve(program, graph)
        baseline = score_summary(program, graph, bl_placement, bl_routed)

        start = time.perf_counter()
        placement, routed, info = solve_with_info(program, graph, time_budget=budget, seed=seed)
        runtime = time.perf_counter() - start
        ours = score_summary(program, graph, placement, routed)
        lb = lower_bound(program, graph, time_limit=lb_budget if lb_budget is not None else budget)
        rows.append(
            {
                "name": name,
                "baseline": baseline["score"],
                "valid": ours["valid"],
                "message": ours["message"],
                "swaps": ours["swap_count"],
                "depth": ours["depth"],
                "score": ours["score"],
                "lower_bound": lb,
                "gap": ours["score"] - lb,
                "runtime": runtime,
                "method": info["method"],
                "placement": {str(k): v for k, v in placement.items()},
                "routed": [list(op) for op in routed],
            }
        )
        if not quiet:
            r = rows[-1]
            print(
                f"  {name:<15} score {r['score']:>6.1f}  (baseline {r['baseline']:>5.1f}, "
                f"LB {lb:>5.1f})  {r['runtime']:.1f}s  via {r['method'][:60]}",
                flush=True,
            )
    total = sum(r["score"] for r in rows)
    result = {
        "rows": rows,
        "total": total,
        "baseline_total": sum(r["baseline"] for r in rows),
        "lower_bound_total": sum(r["lower_bound"] for r in rows),
        "all_valid": all(r["valid"] for r in rows),
        "budget": budget,
        "seed": seed,
    }
    return result


def print_table(result: dict) -> None:
    header = f"{'benchmark':<15} {'valid':>5} {'swaps':>5} {'depth':>5} {'score':>7} {'baseline':>8} {'LB':>6} {'gap':>5} {'time':>6}"
    print()
    print(header)
    print("-" * len(header))
    for r in result["rows"]:
        print(
            f"{r['name']:<15} {str(r['valid']):>5} {r['swaps']:>5} {r['depth']:>5} {r['score']:>7.1f} "
            f"{r['baseline']:>8.1f} {r['lower_bound']:>6.1f} {r['gap']:>5.1f} {r['runtime']:>5.1f}s"
        )
    print("-" * len(header))
    print(
        f"{'TOTAL':<15} {str(result['all_valid']):>5} {'':>5} {'':>5} {result['total']:>7.1f} "
        f"{result['baseline_total']:>8.1f} {result['lower_bound_total']:>6.1f} "
        f"{result['total'] - result['lower_bound_total']:>5.1f}"
    )
    improvement = 100 * (1 - result["total"] / result["baseline_total"])
    print(f"\nImprovement over baseline: {improvement:.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--budget", type=float, default=10.0, help="seconds per benchmark")
    parser.add_argument("--lb-budget", type=float, default=None, help="seconds for the lower-bound search (default: same as --budget)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--names", nargs="*", help="only run these benchmarks")
    parser.add_argument("--json", type=Path, help="write results to this file")
    args = parser.parse_args()
    result = run_bench(args.budget, args.seed, args.names, lb_budget=args.lb_budget)
    print_table(result)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2))
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
