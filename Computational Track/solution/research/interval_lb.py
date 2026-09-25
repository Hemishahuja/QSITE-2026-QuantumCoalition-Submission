"""Interval-decomposition lower bound on total SWAP count (general move model).

For any valid routing, the SWAPs executed strictly between gate i-1 and gate j-1 (i.e. those
needed for gates i..j-1 after gate i-1 has run) form a valid routing of the sub-program
gates[i:j] with a FREE initial mapping. Hence for any partition of the program into
consecutive intervals, total_swaps >= sum of free-start optima of the intervals.

We compute free-start exact optima (or proven lower bounds on timeout) for intervals of
increasing length in parallel, strengthening each search with the DP bound from shorter
intervals as a suffix heuristic, then take the best partition by DP.

    python solution/research/interval_lb.py <benchmark> <per_interval_seconds> [max_len] [workers]
"""

from __future__ import annotations

import json
import sys
import time
from multiprocessing import Pool

from common import BENCHMARKS, HERE, gates_of
from general_exact import general_min_swaps


def dp_table(G, val):
    """B[i][j] = best lower bound for gates[i:j] from any partition into known intervals."""
    B = [[0] * (G + 1) for _ in range(G + 1)]
    for length in range(1, G + 1):
        for i in range(0, G - length + 1):
            j = i + length
            best = val.get((i, j), 0)
            for m in range(i + 1, j):
                s = B[i][m] + B[m][j]
                if s > best:
                    best = s
            B[i][j] = best
    return B


def work(args):
    gates, i, j, start, suffix, tl = args
    t0 = time.perf_counter()
    res, wit, nodes = general_min_swaps(gates[i:j], time_limit=tl, start_budget=start, suffix_lb=suffix,
                                        memo_cap=300_000)
    exact = wit is not None
    lb = res if exact else res + 1
    return i, j, max(lb, start), exact, round(time.perf_counter() - t0, 2), nodes


def main():
    name = sys.argv[1]
    tl = float(sys.argv[2])
    gates = gates_of(BENCHMARKS[name])
    G = len(gates)
    max_len = int(sys.argv[3]) if len(sys.argv) > 3 else G
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 14
    val: dict = {}
    exact_set = set()
    out = HERE / f"interval_lb_{name}.json"
    first_len = 2
    if out.exists():  # resume
        data = json.loads(out.read_text())
        for k, (v, ex) in data["intervals"].items():
            i, j = map(int, k.split("-"))
            val[(i, j)] = v
            if ex:
                exact_set.add((i, j))
        first_len = data["max_len_done"] + 1
    t_start = time.perf_counter()
    with Pool(workers, maxtasksperchild=4) as pool:
        for length in range(first_len, max_len + 1):
            B = dp_table(G, val)
            tasks = []
            for i in range(0, G - length + 1):
                j = i + length
                suffix = [B[i + t][j] for t in range(length + 1)]
                tasks.append((gates, i, j, B[i][j], suffix, tl))
            n_exact = 0
            improved = 0
            for i, j, lb, exact, el, nodes in pool.imap_unordered(work, tasks):
                if lb > B[i][j]:
                    improved += 1
                val[(i, j)] = lb
                if exact:
                    exact_set.add((i, j))
                    n_exact += 1
            B = dp_table(G, val)
            print(f"len={length}: intervals={len(tasks)} exact={n_exact} improved={improved} "
                  f"LB(whole)={B[0][G]} t={time.perf_counter()-t_start:.0f}s", flush=True)
            out.write_text(json.dumps({
                "benchmark": name, "G": G, "per_interval_seconds": tl, "max_len_done": length,
                "whole_program_swap_lb": B[0][G],
                "prefix_lb": [B[0][j] for j in range(G + 1)],
                "intervals": {f"{i}-{j}": [v, (i, j) in exact_set] for (i, j), v in sorted(val.items())},
            }, indent=0))
            if n_exact == 0 and improved == 0:
                break


if __name__ == "__main__":
    main()
